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
from html import unescape
from html.parser import HTMLParser
from json import dumps, loads
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from codex_fleet.codex.app_server import AppServerClient, AppServerError
from codex_fleet.models import (
    NeedsInput,
    ProposedTask,
    RunMessage,
    RunResult,
    TokenUsage,
    WorkItem,
    WorkItemState,
)
from codex_fleet.planner import PlannerContractError, parse_planner_output
from codex_fleet.test_proof import run_test_proof


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
        model: str | None = None,
        reasoning_effort: str | None = None,
        agent_role: str | None = None,
        human_answers: list[dict[str, object]] | None = None,
        timeout_seconds: int = 3600,
    ) -> None:
        self.command = command
        self.approval_policy = approval_policy
        self.sandbox_mode = sandbox_mode
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.agent_role = agent_role
        self.human_answers = human_answers or []
        self.timeout_seconds = timeout_seconds
        self.run_id: str | None = None

    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        prompt = _prompt_for_item(item, role_override=self.agent_role, human_answers=self.human_answers)
        role = self.agent_role or _role_for_item(item)
        expected_role_line = f"Agent role: {_normalize_role(role)}"
        if expected_role_line not in prompt:
            return RunResult(
                success=False,
                summary="Codex App Server prompt role mismatch.",
                error=f"Expected prompt to contain `{expected_role_line}`.",
            )
        output_path = workspace / ".codex-fleet-app-server-transcript.txt"
        install_artifact, install_command, install_error = _ensure_dependencies(workspace)
        if install_error:
            install_needs_input = NeedsInput(
                question=f"Dependency installation failed before Codex could run: {install_error}",
                needed_to_continue=True,
                suggested_state=WorkItemState.NEEDS_INPUT.value,
            )
            return RunResult(
                success=False,
                summary=install_needs_input.question,
                test_commands=(install_command or "dependency install failed",),
                artifacts=tuple(path for path in (install_artifact,) if path is not None),
                needs_input=install_needs_input,
                error=install_needs_input.question,
            )
        client = AppServerClient(
            self.command,
            workspace,
            approval_policy=self.approval_policy,
            sandbox_mode=self.sandbox_mode,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            timeout_seconds=self.timeout_seconds,
        )
        try:
            outcome = client.run_turn(prompt=prompt, title=f"{item.identifier}: {item.title}")
        except (AppServerError, OSError) as exc:
            return RunResult(success=False, summary="Codex App Server failed.", error=str(exc))

        messages = _app_server_messages(
            item=item,
            role=role,
            prompt=prompt,
            notifications=outcome.messages,
            output_path=output_path,
        )
        transcript_text = "\n".join(
            message.content
            for message in messages
            if message.kind not in {"chat_user", "tool_result", "raw_event"}
        )
        output_path.write_text("\n\n".join(_format_run_message(message) for message in messages))
        changed_files = tuple(_changed_files(workspace))
        role = _normalize_role(role)
        planner_failure = parse_planner_contract_failure(transcript_text, require_output=role == "planner")
        needs_input: NeedsInput | None = parse_needs_input(transcript_text) or planner_failure
        proposed_tasks = parse_planner_tasks(transcript_text) or parse_proposed_tasks(transcript_text)
        if role == "planner" and needs_input is None and not proposed_tasks:
            needs_input = NeedsInput(
                question=(
                    "Planner output was invalid: no durable child tasks were created. "
                    "Return a codex-fleet-planner-output JSON block with at least one task."
                ),
                needed_to_continue=True,
                suggested_state=WorkItemState.NEEDS_INPUT.value,
            )
        token_usage = parse_token_usage(transcript_text)
        if needs_input is not None:
            return RunResult(
                success=False,
                summary=needs_input.question,
                changed_files=changed_files,
                test_commands=tuple(command for command in (install_command, "reported by Codex App Server") if command),
                artifacts=tuple(path for path in (install_artifact, output_path) if path is not None),
                proposed_tasks=proposed_tasks,
                needs_input=needs_input,
                token_usage=token_usage,
                messages=messages,
                codex_thread_id=outcome.thread_id,
                codex_turn_id=outcome.turn_id,
                error=needs_input.question,
            )
        proof_result = _verify_test_agent_output(workspace, role, run_id=self.run_id or item.safe_identifier)
        verification_artifacts = proof_result.artifacts
        verification_commands = proof_result.commands
        verification_error = proof_result.error
        if role == "test_reviewer" and verification_error:
            needs_input = NeedsInput(
                question=f"Test Agent could not verify the app: {verification_error}",
                needed_to_continue=True,
                suggested_state=WorkItemState.NEEDS_INPUT.value,
            )
            return RunResult(
                success=False,
                summary=needs_input.question,
                changed_files=changed_files,
                test_commands=tuple(command for command in (install_command, *verification_commands, "reported by Codex App Server") if command),
                artifacts=tuple(path for path in (install_artifact, output_path, *verification_artifacts) if path is not None),
                proposed_tasks=proposed_tasks,
                needs_input=needs_input,
                token_usage=token_usage,
                messages=messages,
                codex_thread_id=outcome.thread_id,
                codex_turn_id=outcome.turn_id,
                preview_url=proof_result.preview_url,
                test_video_path=proof_result.video_path,
                test_video_url=proof_result.video_url,
                screenshot_paths=proof_result.screenshot_paths,
                test_proof_status=proof_result.status,
                error=needs_input.question,
            )
        summary = _tail(transcript_text) or f"Codex {outcome.summary} for {item.identifier}."
        if item.identifier and item.identifier not in summary:
            summary = f"{item.identifier}: {summary}"
        return RunResult(
            success=outcome.completed,
            summary=summary,
            changed_files=changed_files,
            test_commands=tuple(command for command in (install_command, *verification_commands, "reported by Codex App Server") if command),
            artifacts=tuple(path for path in (install_artifact, output_path, *verification_artifacts) if path is not None),
            proposed_tasks=proposed_tasks,
            token_usage=token_usage,
            messages=messages,
            codex_thread_id=outcome.thread_id,
            codex_turn_id=outcome.turn_id,
            preview_url=proof_result.preview_url,
            test_video_path=proof_result.video_path,
            test_video_url=proof_result.video_url,
            screenshot_paths=proof_result.screenshot_paths,
            test_proof_status=proof_result.status,
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
        planner_needs_input = parse_planner_contract_failure(output)
        proposed_tasks = parse_planner_tasks(output) or parse_proposed_tasks(output)
        token_usage = parse_token_usage(output)
        if planner_needs_input is not None:
            needs_input = planner_needs_input
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


def _prompt_for_item(
    item: WorkItem,
    *,
    role_override: str | None = None,
    human_answers: list[dict[str, object]] | None = None,
) -> str:
    description = _plain_text(item.description) or "No description provided."
    role = _normalize_role(role_override) if role_override else _role_for_item(item)
    role_contract = _role_prompt_contract(role)
    answer_section = _human_answers_prompt_section(human_answers or [])
    return (
        f"Work item {item.identifier}: {item.title}\n\n"
        f"Agent role: {role}\n\n"
        f"Description:\n{description}\n\n"
        f"{answer_section}"
        "You are running inside codex-fleet. Do not call Plane directly; the daemon parses your final answer "
        "and updates Plane. Keep Plane-facing summaries concise and do not dump raw logs, secrets, or large diffs.\n\n"
        "For full-auto or agent follow-up work, keep moving with reasonable product assumptions. Do not ask the "
        "human for subjective copy, audience, style, feature, or section details; state your assumptions and proceed. "
        "Use `codex-fleet-needs-input` only for real blockers such as missing credentials, inaccessible files, "
        "destructive approval, impossible local setup, or dependency installation failures.\n\n"
        f"{role_contract}\n\n"
        "If you are blocked on user input, include exactly one fenced block named `codex-fleet-needs-input` "
        "containing JSON with `question`, optional `needed_to_continue`, and optional `suggested_state`.\n\n"
        "Only planner tasks may create durable child assignments. Planner output must be one fenced block named "
        "`codex-fleet-planner-output` containing the required JSON object: `summary`, `tasks`, and `reviewers`. "
        "Do not include secrets."
    )


class _HtmlToTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "p", "div", "li", "section", "article", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div", "li", "section", "article", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        joined = unescape("".join(self.parts))
        lines = [" ".join(line.split()) for line in joined.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line)).strip()


def _plain_text(value: str | None) -> str:
    if not value:
        return ""
    raw = str(value)
    if "<" not in raw or ">" not in raw:
        return unescape(raw).strip()
    parser = _HtmlToTextParser()
    try:
        parser.feed(raw)
        parser.close()
        text = parser.text()
    except Exception:
        text = re.sub(r"<[^>]+>", "", unescape(raw)).strip()
    return text or re.sub(r"<[^>]+>", "", unescape(raw)).strip()


def _human_answers_prompt_section(human_answers: list[dict[str, object]]) -> str:
    clean_answers: list[str] = []
    for item in human_answers[-5:]:
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not answer:
            continue
        if question:
            clean_answers.append(f"- Question: {question}\n  Answer: {answer}")
        else:
            clean_answers.append(f"- Answer: {answer}")
    if not clean_answers:
        return ""
    return "Human answers since last run:\n" + "\n".join(clean_answers) + "\n\n"


def _role_for_item(item: WorkItem) -> str:
    labels = {label.lower().replace("-", "_") for label in item.labels}
    for role in (
        "planner",
        "code_scout",
        "implementer",
        "quality_reviewer",
        "security_reviewer",
        "test_reviewer",
        "delivery_manager",
    ):
        if f"agent_{role}" in labels:
            return role
    if "agent_harness_reviewer" in labels or "agent_token_reviewer" in labels:
        return "quality_reviewer"
    if "agent_test_agent" in labels or "agent_qa_reviewer" in labels:
        return "test_reviewer"
    if "agent_reviewer" in labels:
        return "reviewer"
    return "implementer"


def _normalize_role(role: str | None) -> str:
    normalized = (role or "").strip().lower().replace("-", "_")
    normalized = {
        "harness_reviewer": "quality_reviewer",
        "token_reviewer": "quality_reviewer",
        "qa_reviewer": "test_reviewer",
        "tester": "test_reviewer",
        "test_agent": "test_reviewer",
    }.get(normalized, normalized)
    return (
        normalized
        if normalized
        in {
            "planner",
            "code_scout",
            "implementer",
            "reviewer",
            "quality_reviewer",
            "security_reviewer",
            "test_reviewer",
            "delivery_manager",
        }
        else "implementer"
    )


def _app_server_messages(
    *,
    item: WorkItem,
    role: str,
    prompt: str,
    notifications: tuple[dict[str, Any], ...],
    output_path: Path,
) -> tuple[RunMessage, ...]:
    role = _normalize_role(role)
    messages: list[RunMessage] = [
        RunMessage(
            sequence=0,
            kind="chat_user",
            content=prompt,
            agent_role=role,
            agent_name=_agent_display_name(role),
        )
    ]
    sequence = 1
    assistant_buffer: list[str] = []

    def flush_assistant() -> None:
        nonlocal sequence
        content = "".join(assistant_buffer).strip()
        assistant_buffer.clear()
        if not content:
            return
        artifact_path = output_path if len(content) > 4000 else None
        messages.append(
            RunMessage(
                sequence=sequence,
                kind="chat_assistant",
                content=content,
                agent_role=role,
                agent_name=_agent_display_name(role),
                artifact_path=artifact_path,
                payload={"source": "app-server-delta"},
            )
        )
        sequence += 1

    for payload in notifications:
        kind, content = _message_from_app_server_payload(payload)
        if kind == "assistant_delta":
            assistant_buffer.append(content)
            continue
        if kind == "ignore":
            continue
        flush_assistant()
        if not content:
            continue
        artifact_path = output_path if len(content) > 4000 else None
        messages.append(
            RunMessage(
                sequence=sequence,
                kind=kind,
                content=_tail(content, limit=4000),
                agent_role=role,
                agent_name=_agent_display_name(role),
                artifact_path=artifact_path,
                payload={"method": payload.get("method")},
            )
        )
        sequence += 1
    flush_assistant()
    if len(messages) == 1:
        messages.append(
            RunMessage(
                sequence=1,
                kind="system_event",
                content=f"Codex App Server completed turn for {item.identifier}.",
                agent_role=role,
                agent_name=_agent_display_name(role),
            )
        )
    return tuple(messages)


def _message_from_app_server_payload(payload: dict[str, Any]) -> tuple[str, str]:
    method = str(payload.get("method") or "")
    params = payload.get("params")
    content = _payload_text(params)
    if method == "item/agentMessage/delta":
        return "assistant_delta", content
    if _is_protocol_noise(method, content):
        return "ignore", ""
    if not content:
        if method in {
            "thread/status/changed",
            "turn/started",
            "skills/changed",
            "mcpServer/startupStatus/updated",
            "account/rateLimits/updated",
            "item/started",
            "item/completed",
            "thread/tokenUsage/updated",
            "turn/completed",
        }:
            return "ignore", ""
        content = method
    if method in {"turn/completed"}:
        return "chat_assistant", content
    if method in {"turn/failed", "turn/cancelled"}:
        return "error", content
    if "tool" in method and ("start" in method or "call" in method):
        return "tool_call", content
    if "tool" in method or "exec" in method or "command" in method:
        return "tool_result", content
    return "system_event", content


def _is_protocol_noise(method: str, content: str) -> bool:
    stripped = content.strip()
    if stripped in {
        "userMessage",
        "assistantMessage",
        "agentMessage",
        "reasoning",
        "codex",
        "codex_apps",
        "computer-use",
        "turn/completed",
        "item/completed",
    }:
        return True
    if stripped.startswith(("item/", "turn/", "thread/")):
        return True
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27,}", stripped):
        return True
    return method in {
        "item/started",
        "item/completed",
        "thread/tokenUsage/updated",
        "thread/status/changed",
        "turn/completed",
    }


def _payload_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "message", "summary", "output", "content", "delta"):
            nested = value.get(key)
            if isinstance(nested, str) and nested:
                return nested
        for nested in value.values():
            text = _payload_text(nested)
            if text:
                return text
    if isinstance(value, list):
        parts = [_payload_text(item) for item in value]
        return "".join(part for part in parts if part)
    return ""


def _agent_display_name(role: str) -> str:
    return {
        "planner": "Planner",
        "code_scout": "Code Scout",
        "implementer": "Implementer",
        "reviewer": "Reviewer",
        "quality_reviewer": "Quality Reviewer",
        "security_reviewer": "Security Reviewer",
        "test_reviewer": "Test Agent",
        "delivery_manager": "Delivery Manager",
    }.get(role, "Implementer")


def _format_run_message(message: RunMessage) -> str:
    return f"[{message.sequence}] {message.kind} {message.agent_name or ''}\n{message.content}"


def _role_prompt_contract(role: str) -> str:
    if role == "planner":
        return (
            "Plan only. Do not edit files. Return structured planner JSON for child Plane tasks with specific "
            "instructions, dependencies, acceptance criteria, roles, and reviewers. The JSON must use task roles "
            "only from code_scout, implementer, quality_reviewer, security_reviewer, and test_reviewer; use "
            "reviewers only from quality_reviewer, security_reviewer, and test_reviewer. For code-changing tasks, "
            "include quality_reviewer and test_reviewer unless the task is clearly non-runnable documentation."
        )
    if role in {"quality_reviewer", "security_reviewer", "reviewer"}:
        return "Review only. Do not edit files. Report findings, residual risk, and verification gaps."
    if role == "test_reviewer":
        return "Test only. Do not edit product source. Run build/test/preview checks, capture screenshots or video when available, and report proof artifacts."
    if role == "delivery_manager":
        return "Prepare delivery instructions only. Do not push, merge, deploy, or open a pull request."
    if role == "code_scout":
        return "Explore only. Do not edit files. Report the relevant files, tests, risks, and recommended next tasks."
    return "Implement only the assigned task, run relevant tests, and summarize changed files plus verification."


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


def parse_planner_tasks(output: str) -> tuple[ProposedTask, ...]:
    tasks: list[ProposedTask] = []
    for block in _planner_output_blocks(output):
        try:
            planner = parse_planner_output(block)
        except PlannerContractError:
            return ()
        for task in planner.tasks:
            tasks.append(
                ProposedTask(
                    title=task.title,
                    description=task.description,
                    role=task.role,
                    planner_id=task.planner_id,
                    depends_on=task.depends_on,
                    labels=(f"priority-{task.priority}",),
                )
            )
        implementer_titles = tuple(task.title for task in tasks if task.role == "implementer")
        roles = {task.role for task in tasks}
        for reviewer in planner.reviewers:
            if reviewer in roles:
                continue
            tasks.append(
                ProposedTask(
                    title=f"Review implementation: {reviewer.replace('_', ' ')}",
                    description="Review the completed implementation and report findings only.",
                    role=reviewer,
                    depends_on=implementer_titles,
                )
            )
            roles.add(reviewer)
        if "implementer" in roles:
            if "quality_reviewer" not in roles:
                tasks.append(
                    ProposedTask(
                        title="Quality review implementation",
                        description="Review the implemented change for build correctness, harness fit, token/context efficiency, and residual risks. Do not edit product source.",
                        role="quality_reviewer",
                        depends_on=implementer_titles,
                    )
                )
            if "test_reviewer" not in roles:
                tasks.append(
                    ProposedTask(
                        title="Test implementation and record proof",
                        description="Run the app or available tests, capture proof artifacts when possible, and report preview/test results. Do not edit product source.",
                        role="test_reviewer",
                        depends_on=implementer_titles,
                    )
                )
        return tuple(tasks)
    return ()


def parse_planner_contract_failure(output: str, *, require_output: bool = False) -> NeedsInput | None:
    blocks = _planner_output_blocks(output)
    if require_output and not blocks:
        return NeedsInput(
            question="Planner output was invalid: missing codex-fleet-planner-output JSON block.",
            needed_to_continue=True,
            suggested_state=WorkItemState.NEEDS_INPUT.value,
        )
    for block in blocks:
        try:
            parse_planner_output(block)
        except PlannerContractError as exc:
            return NeedsInput(
                question=f"Planner output was invalid: {exc}",
                needed_to_continue=True,
                suggested_state=WorkItemState.NEEDS_INPUT.value,
            )
    return None


def _planner_output_blocks(output: str) -> list[str]:
    blocks = _fenced_blocks(output, "codex-fleet-planner-output")
    if blocks:
        return blocks
    recovered = _recover_planner_json_object(output)
    return [recovered] if recovered else []


def _recover_planner_json_object(output: str) -> str | None:
    """Recover plain planner JSON when the model omitted the required fence."""
    summary_index = output.find('"summary"')
    tasks_index = output.find('"tasks"')
    if summary_index == -1 or tasks_index == -1:
        return None
    start = output.rfind("{", 0, min(summary_index, tasks_index))
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(output)):
        char = output[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = output[start : index + 1].strip()
                try:
                    loads(candidate)
                except ValueError:
                    return None
                return candidate
    return None


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
        planner_id=str(raw.get("id")).strip()[:120] if str(raw.get("id") or "").strip() else None,
        suggested_state=suggested_state.strip()[:80] if isinstance(suggested_state, str) and suggested_state.strip() else None,
        labels=tuple(dict.fromkeys(clean_labels)),
    )


def _ensure_dependencies(workspace: Path) -> tuple[Path | None, str | None, str | None]:
    package_json = workspace / "package.json"
    if package_json.exists() and not (workspace / "node_modules").exists():
        if (workspace / "pnpm-lock.yaml").exists() and shutil.which("pnpm"):
            command = "pnpm install"
        elif (workspace / "yarn.lock").exists() and shutil.which("yarn"):
            command = "yarn install"
        else:
            command = "npm install"
        return _run_artifact_command(workspace, command, artifact_name=".codex-fleet-install.log", timeout_seconds=900)
    pyproject = workspace / "pyproject.toml"
    if pyproject.exists() and not (workspace / ".venv").exists() and shutil.which("uv"):
        return _run_artifact_command(workspace, "uv sync", artifact_name=".codex-fleet-install.log", timeout_seconds=900)
    return None, None, None


@dataclass(frozen=True)
class _ProofAdapterResult:
    artifacts: tuple[Path, ...] = ()
    commands: tuple[str, ...] = ()
    error: str | None = None
    preview_url: str | None = None
    video_path: Path | None = None
    video_url: str | None = None
    screenshot_paths: tuple[Path, ...] = ()
    status: str | None = None


def _verify_test_agent_output(workspace: Path, role: str, *, run_id: str) -> _ProofAdapterResult:
    if role != "test_reviewer":
        return _ProofAdapterResult()
    result = run_test_proof(workspace, run_id=run_id)
    return _ProofAdapterResult(
        artifacts=result.artifacts,
        commands=result.commands,
        error=result.error,
        preview_url=result.preview_url,
        video_path=result.video_path,
        video_url=result.video_url,
        screenshot_paths=result.screenshot_paths,
        status=result.status,
    )


def _run_artifact_command(
    workspace: Path,
    command: str,
    *,
    artifact_name: str,
    timeout_seconds: int,
) -> tuple[Path, str, str | None]:
    artifact = workspace / artifact_name
    try:
        completed = subprocess.run(
            shlex.split(command),
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        artifact.write_text(str(exc))
        return artifact, command, str(exc)
    artifact.write_text(completed.stdout or "")
    if completed.returncode != 0:
        return artifact, command, _tail(completed.stdout or f"{command} exited with {completed.returncode}", limit=1000)
    return artifact, command, None


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
