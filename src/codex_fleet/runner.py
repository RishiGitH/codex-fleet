from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from json import dumps, loads
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from codex_fleet.codex.app_server import AppServerClient, AppServerError
from codex_fleet.models import (
    NeedsInput,
    ProposedTask,
    RunResult,
    TokenUsage,
    WorkItem,
    WorkItemState,
)
from codex_fleet.prompt_protocol import build_runner_prompt


class Runner(ABC):
    @abstractmethod
    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        raise NotImplementedError


@dataclass(frozen=True)
class RunnerPreflight:
    ok: bool
    message: str


class FakeRunner(Runner):
    """Deterministic runner used for tests and local smoke checks."""

    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed

    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        marker = workspace / ".codex-fleet-fake-run.txt"
        marker.write_text(f"Fake run for {item.identifier}: {item.title}\n")
        if self.succeed:
            return RunResult(
                success=True,
                summary=f"Fake runner completed {item.identifier}.",
                changed_files=(str(marker),),
                test_commands=("fake-tests: passed",),
                artifacts=(marker,),
            )
        return RunResult(success=False, summary="Fake runner failed.", error="configured failure")


class CodexAppServerRunner(Runner):
    """Runs one work item through Codex App Server."""

    def __init__(
        self,
        command: str = "codex app-server",
        approval_policy: str = "on-request",
        sandbox_mode: str = "workspace-write",
        timeout_seconds: int = 3600,
    ) -> None:
        self.command = command
        self.approval_policy = approval_policy
        self.sandbox_mode = sandbox_mode
        self.timeout_seconds = timeout_seconds

    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        prompt = _prompt_for_item(item)
        client = AppServerClient(
            self.command,
            workspace,
            approval_policy=self.approval_policy,
            sandbox_mode=self.sandbox_mode,
            timeout_seconds=self.timeout_seconds,
        )
        try:
            outcome = client.run_turn(prompt=prompt, title=f"{item.identifier}: {item.title}")
        except (AppServerError, OSError) as exc:
            return RunResult(success=False, summary="Codex App Server failed.", error=str(exc))

        return RunResult(
            success=outcome.completed,
            summary=f"Codex {outcome.summary} for {item.identifier}.",
            test_commands=("reported by Codex",),
            error=None if outcome.completed else outcome.summary,
        )


class CodexCliRunner(Runner):
    """Runs one work item through the installed Codex CLI."""

    def __init__(
        self,
        command: str = "codex exec",
        approval_policy: str = "on-request",
        sandbox_mode: str = "workspace-write",
        timeout_seconds: int = 3600,
        stream_logs: bool = True,
    ) -> None:
        self.command = command
        self.approval_policy = approval_policy
        self.sandbox_mode = sandbox_mode
        self.timeout_seconds = timeout_seconds
        self.stream_logs = stream_logs

    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        command_parts = _split_command(self.command)
        if command_parts is None:
            return RunResult(
                success=False,
                summary="Codex CLI preflight failed.",
                error="Configured Codex command could not be parsed.",
            )
        preflight = check_codex_cli_preflight(command_parts)
        if not preflight.ok:
            return RunResult(success=False, summary="Codex CLI preflight failed.", error=preflight.message)

        prompt = _prompt_for_item(item)
        output_path = workspace / ".codex-fleet-codex-cli-output.txt"
        command = [
            *command_parts,
            "--cd",
            str(workspace),
            "--sandbox",
            self.sandbox_mode,
            "-c",
            f"approval_policy={dumps(self.approval_policy)}",
            "-",
        ]
        try:
            completed = _run_command_streaming(
                command,
                cwd=workspace,
                input_text=prompt,
                output_path=output_path,
                timeout_seconds=self.timeout_seconds,
                stream_logs=self.stream_logs,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return RunResult(success=False, summary="Codex CLI failed.", error=str(exc))

        output = completed.stdout or ""
        changed_files = tuple(_changed_files(workspace))
        if completed.returncode != 0:
            return RunResult(
                success=False,
                summary="Codex CLI failed.",
                changed_files=changed_files,
                artifacts=(output_path,),
                error=_tail(output) or f"Codex CLI exited with {completed.returncode}",
            )
        needs_input = parse_needs_input(output)
        proposed_tasks = parse_proposed_tasks(output)
        token_usage = parse_token_usage(output)
        if needs_input is not None:
            return RunResult(
                success=False,
                summary=needs_input.question,
                changed_files=changed_files,
                test_commands=("reported by Codex CLI",),
                artifacts=(output_path,),
                proposed_tasks=proposed_tasks,
                needs_input=needs_input,
                token_usage=token_usage,
                error=needs_input.question,
            )
        return RunResult(
            success=True,
            summary=_tail(output) or f"Codex CLI completed {item.identifier}.",
            changed_files=changed_files,
            test_commands=("reported by Codex CLI",),
            artifacts=(output_path,),
            proposed_tasks=proposed_tasks,
            token_usage=token_usage,
        )


def check_codex_cli_preflight(command_parts: list[str]) -> RunnerPreflight:
    if not _is_codex_exec_command(command_parts):
        return RunnerPreflight(True, "Preflight skipped for custom Codex command.")

    binary = command_parts[0]
    if shutil.which(binary) is None:
        return RunnerPreflight(
            False,
            f"Configured Codex command binary was not found: {binary}. Install and authenticate Codex CLI.",
        )

    try:
        exec_help = subprocess.run(
            [*command_parts, "--help"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return RunnerPreflight(False, f"Codex exec help could not be inspected: {exc}")
    if exec_help.returncode != 0:
        return RunnerPreflight(False, "Codex exec help could not be inspected.")
    help_text = f"{exec_help.stdout}\n{exec_help.stderr}"
    missing = [flag for flag in ("--cd", "--sandbox", "--config") if flag not in help_text]
    if "stdin" not in help_text.lower():
        missing.append("stdin prompt")
    if missing:
        return RunnerPreflight(
            False,
            "Codex exec CLI contract appears different from codex-fleet's runner expectations. "
            "Missing support: "
            + ", ".join(missing),
        )

    try:
        login_status = subprocess.run(
            [binary, "login", "status"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return RunnerPreflight(False, f"Codex CLI authentication was not confirmed: {exc}")
    if login_status.returncode != 0:
        return RunnerPreflight(
            False,
            "Codex CLI authentication was not confirmed. Run `codex login status` and `codex login`.",
        )
    return RunnerPreflight(True, "Codex CLI preflight passed.")


def _prompt_for_item(item: WorkItem) -> str:
    return build_runner_prompt(item)


def parse_proposed_tasks(output: str) -> tuple[ProposedTask, ...]:
    tasks: list[ProposedTask] = []
    for block in _fenced_blocks(output, "codex-fleet-proposed-tasks"):
        try:
            raw = loads(block)
        except ValueError:
            continue
        if not isinstance(raw, list):
            continue
        for entry in raw[:10]:
            task = _proposed_task_from_raw(entry)
            if task is not None:
                tasks.append(task)
    return tuple(tasks)


def parse_needs_input(output: str) -> NeedsInput | None:
    for block in _fenced_blocks(output, "codex-fleet-needs-input"):
        try:
            raw = loads(block)
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        needed = raw.get("needed_to_continue")
        suggested_state = raw.get("suggested_state")
        return NeedsInput(
            question=question.strip()[:4000],
            needed_to_continue=needed if isinstance(needed, bool) else True,
            suggested_state=(
                suggested_state.strip()
                if isinstance(suggested_state, str) and suggested_state.strip()
                else WorkItemState.NEEDS_INPUT.value
            ),
        )
    return None


def parse_token_usage(output: str) -> TokenUsage | None:
    """Extract token usage from current and older Codex CLI text summaries."""
    data = _parse_token_usage_json(output) or _parse_token_usage_text(output)
    if not data:
        return None
    input_tokens = _positive_int(data.get("input_tokens") or data.get("prompt_tokens"))
    output_tokens = _positive_int(data.get("output_tokens") or data.get("completion_tokens"))
    total_tokens = _positive_int(data.get("total_tokens"))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens)


def _parse_token_usage_json(output: str) -> dict[str, int] | None:
    for block in _fenced_blocks(output, "codex-fleet-token-usage"):
        try:
            raw = loads(block)
        except ValueError:
            continue
        if isinstance(raw, dict):
            return raw
    for line in output.splitlines():
        if "token" not in line.lower():
            continue
        try:
            raw = loads(line)
        except ValueError:
            continue
        if isinstance(raw, dict):
            usage = raw.get("token_usage") or raw.get("usage")
            if isinstance(usage, dict):
                return usage
    return None


def _parse_token_usage_text(output: str) -> dict[str, int] | None:
    usage_lines = [line for line in output.splitlines() if "token" in line.lower()]
    text = "\n".join(usage_lines[-8:])
    if not text:
        return None
    aliases = {
        "input_tokens": ("input", "prompt"),
        "output_tokens": ("output", "completion"),
        "total_tokens": ("total",),
    }
    result: dict[str, int] = {}
    for key, names in aliases.items():
        for name in names:
            match = re.search(rf"\b{name}(?:[_\s-]?tokens?)?\b\s*[:=]\s*([0-9][0-9,]*)", text, flags=re.IGNORECASE)
            if match:
                result[key] = int(match.group(1).replace(",", ""))
                break
    if not result:
        match = re.search(
            r"([0-9][0-9,]*)\s+input\b.*?([0-9][0-9,]*)\s+output\b.*?([0-9][0-9,]*)\s+total\b",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            result = {
                "input_tokens": int(match.group(1).replace(",", "")),
                "output_tokens": int(match.group(2).replace(",", "")),
                "total_tokens": int(match.group(3).replace(",", "")),
            }
    return result or None


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value.replace(",", ""))
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _run_command_streaming(
    command: list[str],
    *,
    cwd: Path,
    input_text: str,
    output_path: Path,
    timeout_seconds: int,
    stream_logs: bool,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    stdout = process.stdout

    output_chunks: list[str] = []
    queue: Queue[str | None] = Queue()

    def read_output() -> None:
        try:
            for chunk in iter(stdout.readline, ""):
                queue.put(chunk)
        finally:
            queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        process.stdin.write(input_text)
        process.stdin.close()
    except BrokenPipeError:
        pass

    deadline = time.monotonic() + max(1, timeout_seconds)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stream_closed = False
    with output_path.open("w") as artifact:
        while not stream_closed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                reader.join(timeout=1)
                raise subprocess.TimeoutExpired(command, timeout_seconds, output="".join(output_chunks))
            try:
                chunk = queue.get(timeout=min(0.2, remaining))
            except Empty:
                if process.poll() is not None and not reader.is_alive():
                    stream_closed = True
                continue
            if chunk is None:
                stream_closed = True
                continue
            output_chunks.append(chunk)
            artifact.write(chunk)
            artifact.flush()
            if stream_logs:
                sys.stdout.write(chunk)
                sys.stdout.flush()

    returncode = process.wait(timeout=1)
    return subprocess.CompletedProcess(command, returncode, stdout="".join(output_chunks), stderr="")


def _fenced_blocks(output: str, name: str) -> list[str]:
    blocks: list[str] = []
    fence = f"```{name}"
    index = 0
    while True:
        start = output.find(fence, index)
        if start == -1:
            return blocks
        content_start = output.find("\n", start)
        if content_start == -1:
            return blocks
        end = output.find("```", content_start + 1)
        if end == -1:
            return blocks
        blocks.append(output[content_start + 1 : end].strip())
        index = end + 3


def _proposed_task_from_raw(raw: Any) -> ProposedTask | None:
    if not isinstance(raw, dict):
        return None
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    description = raw.get("description")
    labels = raw.get("labels")
    role = raw.get("role")
    depends_on = raw.get("depends_on")
    suggested_state = raw.get("suggested_state")
    clean_labels = ["agent-proposed"]
    if isinstance(labels, list):
        clean_labels.extend(str(label).strip() for label in labels if str(label).strip())
    clean_depends_on: list[str] = []
    if isinstance(depends_on, list):
        clean_depends_on.extend(str(value).strip()[:120] for value in depends_on if str(value).strip())
    return ProposedTask(
        title=title.strip()[:240],
        description=description.strip()[:4000] if isinstance(description, str) and description.strip() else None,
        role=role.strip()[:80] if isinstance(role, str) and role.strip() else None,
        depends_on=tuple(dict.fromkeys(clean_depends_on)),
        suggested_state=suggested_state.strip()[:80] if isinstance(suggested_state, str) and suggested_state.strip() else None,
        labels=tuple(dict.fromkeys(clean_labels)),
    )


def _split_command(command: str) -> list[str] | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    return parts or None


def _is_codex_exec_command(command_parts: list[str]) -> bool:
    return Path(command_parts[0]).name == "codex" and len(command_parts) > 1 and command_parts[1] == "exec"


def _changed_files(workspace: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) > 3:
            paths.append(line[3:])
    return paths


def _tail(value: str, *, limit: int = 1200) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[-limit:]
