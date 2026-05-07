from __future__ import annotations

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
from codex_fleet.models import ProposedTask, RunResult, WorkItem


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
        proposed_tasks = parse_proposed_tasks(output)
        return RunResult(
            success=True,
            summary=_tail(output) or f"Codex CLI completed {item.identifier}.",
            changed_files=changed_files,
            test_commands=("reported by Codex CLI",),
            artifacts=(output_path,),
            proposed_tasks=proposed_tasks,
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
    description = item.description or "No description provided."
    return (
        f"Work item {item.identifier}: {item.title}\n\n"
        f"Description:\n{description}\n\n"
        "Make the smallest correct change, run relevant tests, and summarize the result.\n\n"
        "If you discover follow-up work that should become separate tasks, include one fenced block named "
        "`codex-fleet-proposed-tasks` containing a JSON array of objects with `title` and optional "
        "`description`. Do not include secrets."
    )


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

    output_chunks: list[str] = []
    queue: Queue[str | None] = Queue()

    def read_output() -> None:
        try:
            for chunk in iter(process.stdout.readline, ""):
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
    clean_labels = ["agent-proposed"]
    if isinstance(labels, list):
        clean_labels.extend(str(label).strip() for label in labels if str(label).strip())
    return ProposedTask(
        title=title.strip()[:240],
        description=description.strip()[:4000] if isinstance(description, str) and description.strip() else None,
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
