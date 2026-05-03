from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from codex_fleet.codex.protocol import (
    IdSequence,
    extract_thread_id,
    extract_turn_id,
    initialized_notification,
    initialize_message,
    is_turn_completed,
    is_turn_failed,
    parse_json_line,
    response_result,
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


class AppServerClient:
    def __init__(
        self,
        command: str,
        cwd: Path,
        *,
        approval_policy: str = "on-request",
        sandbox_mode: str = "workspace-write",
        timeout_seconds: int = 3600,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.approval_policy = approval_policy
        self.sandbox_mode = sandbox_mode
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
            return self._run_protocol(process.stdin, process.stdout, prompt, title)
        finally:
            _terminate_process(process)

    def _run_protocol(
        self,
        stdin: TextIO,
        stdout: TextIO,
        prompt: str,
        title: str,
    ) -> TurnOutcome:
        init_id = self.ids.next()
        _send(stdin, initialize_message(init_id).to_line())
        response = _read_response(stdout, init_id, self.timeout_seconds)
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
            _read_response(stdout, thread_id_request, self.timeout_seconds),
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
                sandbox_policy={"mode": self.sandbox_mode},
            ).to_line(),
        )
        turn_result = response_result(
            _read_response(stdout, turn_id_request, self.timeout_seconds),
            turn_id_request,
        )
        turn_id = extract_turn_id(turn_result)

        completed = _wait_for_turn(stdout, self.timeout_seconds)
        return TurnOutcome(
            thread_id=thread_id,
            turn_id=turn_id,
            completed=completed,
            summary="turn completed" if completed else "turn failed",
        )


def _send(stdin: TextIO, line: str) -> None:
    stdin.write(line)
    stdin.flush()


def _read_response(stdout: TextIO, request_id: int, timeout_seconds: int) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        line = stdout.readline()
        if not line:
            continue
        payload = parse_json_line(line)
        if payload.get("id") == request_id:
            return payload
    raise AppServerError(f"Timed out waiting for response id {request_id}")


def _wait_for_turn(stdout: TextIO, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        line = stdout.readline()
        if not line:
            continue
        payload = parse_json_line(line)
        if is_turn_completed(payload):
            return True
        if is_turn_failed(payload):
            return False
    raise AppServerError("Timed out waiting for turn completion")


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
