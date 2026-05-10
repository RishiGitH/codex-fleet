import json
import sys

turn_params = {}

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        print(json.dumps({"id": request_id, "result": {}}), flush=True)
    elif method == "initialized":
        continue
    elif method == "thread/start":
        print(json.dumps({"id": request_id, "result": {"thread": {"id": "thread-1"}}}), flush=True)
    elif method == "turn/start":
        turn_params = message.get("params") or {}
        sandbox_policy = turn_params.get("sandboxPolicy") or {}
        if sandbox_policy.get("type") != "workspaceWrite":
            print(
                json.dumps(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32600,
                            "message": "Invalid request: missing field `type`",
                        },
                    }
                ),
                flush=True,
            )
            continue
        if "reasoningEffort" in turn_params or turn_params.get("effort") != "low":
            print(
                json.dumps(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32600,
                            "message": "Invalid request: use `effort` for reasoning",
                        },
                    }
                ),
                flush=True,
            )
            continue
        print(json.dumps({"id": request_id, "result": {"turn": {"id": "turn-1"}}}), flush=True)
        print(
            json.dumps(
                {
                    "method": "agent/message",
                    "params": {
                        "text": (
                            f"Fake App Server ran model={turn_params.get('model')} "
                            f"reasoning={turn_params.get('effort')}"
                        )
                    },
                }
            ),
            flush=True,
        )
        print(json.dumps({"method": "turn/completed", "params": {"summary": "fake app server completed"}}), flush=True)
    else:
        print(json.dumps({"id": request_id, "error": {"message": "unknown method"}}), flush=True)
