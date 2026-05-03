import json
import sys


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
        print(json.dumps({"id": request_id, "result": {"turn": {"id": "turn-1"}}}), flush=True)
        print(json.dumps({"method": "turn/completed", "params": {"status": "ok"}}), flush=True)
    else:
        print(json.dumps({"id": request_id, "error": {"message": "unknown method"}}), flush=True)
