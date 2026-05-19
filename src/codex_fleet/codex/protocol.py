from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast


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
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> JsonRpcMessage:
    params: dict[str, Any] = {
        "threadId": thread_id,
        "input": [{"type": "text", "text": prompt}],
        "cwd": cwd,
        "title": title,
        "approvalPolicy": approval_policy,
        "sandboxPolicy": sandbox_policy,
    }
    if model:
        params["model"] = model
    if reasoning_effort:
        params["effort"] = reasoning_effort
    return JsonRpcMessage(
        {
            "method": "turn/start",
            "id": request_id,
            "params": params,
        }
    )


def sandbox_policy_for_mode(mode: str, *, writable_roots: list[str] | None = None) -> dict[str, Any]:
    if mode == "danger-full-access":
        return {"type": "dangerFullAccess"}
    if mode == "read-only":
        return {"type": "readOnly", "networkAccess": False}
    if mode == "workspace-write":
        return {
            "type": "workspaceWrite",
            "networkAccess": False,
            "writableRoots": writable_roots or [],
            "excludeSlashTmp": False,
            "excludeTmpdirEnvVar": False,
        }
    raise ProtocolError(f"Unsupported App Server sandbox mode: {mode}")


def validate_app_server_turn_start_shape() -> None:
    """Guard the request shape that the Codex App Server currently requires."""
    sandbox_policy = sandbox_policy_for_mode("workspace-write", writable_roots=["/tmp/codex-fleet-protocol-check"])
    message = turn_start_message(
        1,
        thread_id="thread-check",
        prompt="protocol check",
        cwd="/tmp/codex-fleet-protocol-check",
        title="codex-fleet protocol check",
        approval_policy="never",
        sandbox_policy=sandbox_policy,
        model="gpt-5.4-mini",
        reasoning_effort="low",
    )
    params = message.payload.get("params")
    if not isinstance(params, dict):
        raise ProtocolError("App Server turn/start params must be an object")
    actual_policy = params.get("sandboxPolicy")
    if not isinstance(actual_policy, dict) or actual_policy.get("type") != "workspaceWrite":
        raise ProtocolError("App Server sandboxPolicy must use type=workspaceWrite")
    if "mode" in actual_policy:
        raise ProtocolError("App Server sandboxPolicy must not use legacy mode")
    if params.get("effort") != "low":
        raise ProtocolError("App Server reasoning must use effort")
    if "reasoningEffort" in params:
        raise ProtocolError("App Server request must not use legacy reasoningEffort")
    input_items = params.get("input")
    if not isinstance(input_items, list) or not input_items or input_items[0].get("type") != "text":
        raise ProtocolError("App Server input items must include type=text")


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
    return cast(str, thread["id"])


def extract_turn_id(result: dict[str, Any]) -> str:
    turn = result.get("turn")
    if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
        raise ProtocolError("turn/start response missing turn.id")
    return cast(str, turn["id"])


def is_turn_completed(payload: dict[str, Any]) -> bool:
    return payload.get("method") == "turn/completed"


def is_turn_failed(payload: dict[str, Any]) -> bool:
    return payload.get("method") in {"turn/failed", "turn/cancelled"}
