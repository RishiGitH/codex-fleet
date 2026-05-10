from __future__ import annotations

import shlex
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any, TextIO, cast

from codex_fleet.codex.protocol import (
    IdSequence,
    extract_thread_id,
    extract_turn_id,
    initialize_message,
    initialized_notification,
    is_turn_completed,
    is_turn_failed,
    parse_json_line,
    response_result,
    sandbox_policy_for_mode,
    thread_start_message,
    turn_start_message,
)


class AppServerError(RuntimeError):
    pass


@dataclass(frozen=True)
class TurnOutcome:
    thread_id: str
    turn_id: str
    completed: bool
    summary: str
    messages: tuple[dict[str, Any], ...] = ()


class AppServerClient:
    def __init__(
        self,
        command: str,
        cwd: Path,
        *,
        approval_policy: str = "on-request",
        sandbox_mode: str = "workspace-write",
        model: str | None = None,
        reasoning_effort: str | None = None,
        timeout_seconds: int = 3600,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.approval_policy = approval_policy
        self.sandbox_mode = sandbox_mode
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.ids = IdSequence()

    def run_turn(self, prompt: str, title: str) -> TurnOutcome:
        process = subprocess.Popen(
            shlex.split(self.command),
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        try:
            if process.stdin is None or process.stdout is None:
                raise AppServerError("Failed to open Codex App Server pipes")
            return self._run_protocol(
                cast(TextIO, process.stdin),
                cast(TextIO, process.stdout),
                prompt,
                title,
            )
        finally:
            _terminate_process(process)

    def _run_protocol(
        self,
        stdin: TextIO,
        stdout: TextIO,
        prompt: str,
        title: str,
    ) -> TurnOutcome:
        reader = _LineReader(stdout)
        init_id = self.ids.next()
        _send(stdin, initialize_message(init_id).to_line())
        response = _read_response(reader, init_id, self.timeout_seconds)
        response_result(response, init_id)
        _send(stdin, initialized_notification().to_line())

        thread_id_request = self.ids.next()
        _send(
            stdin,
            thread_start_message(
                thread_id_request,
                cwd=str(self.cwd),
                sandbox=self.sandbox_mode,
            ).to_line(),
        )
        thread_result = response_result(
            _read_response(reader, thread_id_request, self.timeout_seconds),
            thread_id_request,
        )
        thread_id = extract_thread_id(thread_result)

        turn_id_request = self.ids.next()
        _send(
            stdin,
            turn_start_message(
                turn_id_request,
                thread_id=thread_id,
                prompt=prompt,
                cwd=str(self.cwd),
                title=title,
                approval_policy=self.approval_policy,
                sandbox_policy=sandbox_policy_for_mode(self.sandbox_mode, writable_roots=[str(self.cwd)]),
                model=self.model,
                reasoning_effort=self.reasoning_effort,
            ).to_line(),
        )
        turn_result = response_result(
            _read_response(reader, turn_id_request, self.timeout_seconds),
            turn_id_request,
        )
        turn_id = extract_turn_id(turn_result)

        completed, messages = _wait_for_turn(reader, self.timeout_seconds)
        return TurnOutcome(
            thread_id=thread_id,
            turn_id=turn_id,
            completed=completed,
            summary=_outcome_summary(messages) or ("turn completed" if completed else "turn failed"),
            messages=tuple(messages),
        )


def _send(stdin: TextIO, line: str) -> None:
    stdin.write(line)
    stdin.flush()


def _read_response(reader: _LineReader, request_id: int, timeout_seconds: int) -> dict[str, Any]:
    for line in reader.read_json_lines(timeout_seconds):
        payload = parse_json_line(line)
        if payload.get("id") == request_id:
            return payload
    raise AppServerError(f"Timed out waiting for response id {request_id}")


def _wait_for_turn(reader: _LineReader, timeout_seconds: int) -> tuple[bool, list[dict[str, Any]]]:
    messages: list[dict[str, Any]] = []
    for line in reader.read_json_lines(timeout_seconds):
        payload = parse_json_line(line)
        if payload.get("method"):
            messages.append(payload)
        if is_turn_completed(payload):
            return True, messages
        if is_turn_failed(payload):
            return False, messages
    raise AppServerError("Timed out waiting for turn completion")


def _outcome_summary(messages: list[dict[str, Any]]) -> str | None:
    for payload in reversed(messages):
        params = payload.get("params")
        if isinstance(params, dict):
            for key in ("summary", "message", "text"):
                value = params.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


class _LineReader:
    def __init__(self, stdout: TextIO) -> None:
        self.stdout = stdout
        self.lines: Queue[str | None] = Queue()
        self.thread = threading.Thread(target=self._read_lines, daemon=True)
        self.thread.start()

    def read_json_lines(self, timeout_seconds: int) -> Iterator[str]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                line = self.lines.get(timeout=min(remaining, 1.0))
            except Empty:
                continue
            if line is None:
                return
            yield line

    def _read_lines(self) -> None:
        try:
            for line in self.stdout:
                self.lines.put(line)
        finally:
            self.lines.put(None)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
