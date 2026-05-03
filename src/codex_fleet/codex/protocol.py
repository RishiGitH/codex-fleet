from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class ProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class JsonRpcMessage:
    payload: dict[str, Any]

    def to_line(self) -> str:
        return json.dumps(self.payload, separators=(",", ":")) + "\n"


class IdSequence:
    def __init__(self) -> None:
        self._next_id = 1

    def next(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value


def initialize_message(request_id: int) -> JsonRpcMessage:
    return JsonRpcMessage(
        {
            "method": "initialize",
            "id": request_id,
            "params": {
                "capabilities": {"experimentalApi": True},
                "clientInfo": {
                    "name": "codex-fleet",
                    "title": "codex-fleet",
                    "version": "0.1.0",
                },
            },
        }
    )


def initialized_notification() -> JsonRpcMessage:
    return JsonRpcMessage({"method": "initialized", "params": {}})


def thread_start_message(request_id: int, cwd: str, sandbox: str) -> JsonRpcMessage:
    return JsonRpcMessage(
        {
            "method": "thread/start",
            "id": request_id,
            "params": {"cwd": cwd, "sandbox": sandbox},
        }
    )


def turn_start_message(
    request_id: int,
    *,
    thread_id: str,
    prompt: str,
    cwd: str,
    title: str,
    approval_policy: str,
    sandbox_policy: dict[str, Any],
) -> JsonRpcMessage:
    return JsonRpcMessage(
        {
            "method": "turn/start",
            "id": request_id,
            "params": {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "cwd": cwd,
                "title": title,
                "approvalPolicy": approval_policy,
                "sandboxPolicy": sandbox_policy,
            },
        }
    )


def parse_json_line(line: str) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"Invalid JSON-RPC line: {line[:200]}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("JSON-RPC payload must be an object")
    return payload


def response_result(payload: dict[str, Any], request_id: int) -> dict[str, Any]:
    if payload.get("id") != request_id:
        raise ProtocolError(f"Expected response id {request_id}, got {payload.get('id')}")
    if "error" in payload:
        raise ProtocolError(f"JSON-RPC error: {payload['error']}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ProtocolError("JSON-RPC response result must be an object")
    return result


def extract_thread_id(result: dict[str, Any]) -> str:
    thread = result.get("thread")
    if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
        raise ProtocolError("thread/start response missing thread.id")
    return thread["id"]


def extract_turn_id(result: dict[str, Any]) -> str:
    turn = result.get("turn")
    if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
        raise ProtocolError("turn/start response missing turn.id")
    return turn["id"]


def is_turn_completed(payload: dict[str, Any]) -> bool:
    return payload.get("method") == "turn/completed"


def is_turn_failed(payload: dict[str, Any]) -> bool:
    return payload.get("method") in {"turn/failed", "turn/cancelled"}
