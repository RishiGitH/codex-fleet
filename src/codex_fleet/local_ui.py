from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from codex_fleet.config import FleetConfig
from codex_fleet.models import WorkItem, WorkItemState
from codex_fleet.orchestrator import Orchestrator
from codex_fleet.runner import FakeRunner
from codex_fleet.store import RunStore
from codex_fleet.tracker import MemoryTracker

MAX_JSON_BODY_BYTES = 16_384
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class LocalUiServer:
    server: ThreadingHTTPServer
    url: str

    def serve_forever(self) -> None:
        self.server.serve_forever()

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class DemoBoard:
    def __init__(self, config: FleetConfig, store: RunStore) -> None:
        self.config = config
        self.store = store
        self._lock = threading.Lock()
        self._next_sequence = 2
        self._runs_by_item: dict[str, dict[str, str | None]] = {}
        self.tracker = MemoryTracker(
            [
                WorkItem(
                    id="local-1",
                    identifier="LOCAL-1",
                    title="Demo Codex work item",
                    description="Run the fake Codex worker in an isolated git worktree.",
                    state=WorkItemState.BACKLOG.value,
                    priority=2,
                )
            ],
            active_states=[WorkItemState.READY.value],
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            items = sorted(self.tracker.fetch_all_items(), key=lambda item: item.identifier)
            return {
                "items": [
                    {
                        "id": item.id,
                        "identifier": item.identifier,
                        "title": item.title,
                        "description": item.description,
                        "state": item.state,
                        "priority": item.priority,
                        "comments": self.tracker.comments.get(item.id, []),
                        "run": self._display_run(self._runs_by_item.get(item.id, {})),
                    }
                    for item in items
                ]
            }

    def create_item(self, title: str, description: str | None = None) -> dict[str, Any]:
        with self._lock:
            item_id = f"local-{self._next_sequence}"
            identifier = f"LOCAL-{self._next_sequence}"
            self._next_sequence += 1
            item = WorkItem(
                id=item_id,
                identifier=identifier,
                title=title.strip() or "Untitled work item",
                description=description,
                state=WorkItemState.BACKLOG.value,
                priority=3,
            )
            self.tracker.add_item(item)
            self.tracker.create_comment(item.id, "created")
        return self.snapshot()

    def move_to_ready(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            self.tracker.update_item_state(item_id, WorkItemState.READY.value)
            self.tracker.create_comment(item_id, "ready")
        return self.snapshot()

    def run_ready_item(self, *, succeed: bool = True) -> dict[str, Any]:
        with self._lock:
            ready_items = self.tracker.fetch_candidate_items()
            if not ready_items:
                message = "No Ready work items found."
                snapshot = {
                    "items": [
                        {
                            "id": item.id,
                            "identifier": item.identifier,
                            "title": item.title,
                            "description": item.description,
                            "state": item.state,
                            "priority": item.priority,
                            "comments": self.tracker.comments.get(item.id, []),
                            "run": self._runs_by_item.get(item.id, {}),
                        }
                        for item in sorted(self.tracker.fetch_all_items(), key=lambda candidate: candidate.identifier)
                    ]
                }
                return {"message": message, **snapshot}
            item = sorted(ready_items, key=lambda candidate: candidate.identifier)[0]
            self.tracker.update_item_state(item.id, WorkItemState.RUNNING.value)
            self.tracker.create_comment(item.id, "queued")
            self._runs_by_item[item.id] = {"status": "running"}

        worker = threading.Thread(target=self._complete_run, args=(item.id, succeed), daemon=True)
        worker.start()
        return {"message": f"Started fake run for {item.identifier}.", **self.snapshot()}

    def _complete_run(self, item_id: str, succeed: bool) -> None:
        with self._lock:
            item = self.tracker.fetch_items_by_ids([item_id])[0]
            run_tracker = MemoryTracker([item], active_states=[WorkItemState.RUNNING.value])

        result = Orchestrator(
            config=self.config,
            tracker=run_tracker,
            runner=FakeRunner(succeed=succeed),
            store=self.store,
        ).run_once()

        with self._lock:
            if result.run is None:
                self.tracker.create_comment(item_id, result.message)
                self._runs_by_item[item_id] = {"status": "idle"}
                return
            updated = run_tracker.fetch_items_by_ids([item_id])[0]
            self.tracker.update_item_state(item_id, updated.state)
            for comment in run_tracker.comments.get(item_id, []):
                self.tracker.create_comment(item_id, comment)
            self.tracker.create_comment(item_id, result.message)
            self._runs_by_item[item_id] = {
                "id": result.run.id,
                "status": result.run.status.value,
                "branch_name": result.run.branch_name,
                "worktree_path": self._display_path(result.run.worktree_path),
                "error": "run failed" if result.run.error else None,
            }

    def _display_run(self, run: dict[str, str | None]) -> dict[str, str | None]:
        if not run:
            return {}
        return {
            "id": run.get("id"),
            "status": run.get("status"),
            "branch_name": run.get("branch_name"),
            "worktree_path": run.get("worktree_path"),
            "error": run.get("error"),
        }

    def _display_path(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(path.resolve().relative_to(self.config.workspace.root))
        except ValueError:
            return path.name


def create_local_ui_server(
    config: FleetConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    store_path: Path | None = None,
    unsafe_allow_remote: bool = False,
) -> LocalUiServer:
    if not unsafe_allow_remote and host not in LOOPBACK_HOSTS:
        raise ValueError("Local UI only binds loopback hosts unless unsafe_allow_remote is enabled")

    store = RunStore(store_path or config.repo / ".codex-fleet" / "local-ui-runs.sqlite3")
    board = DemoBoard(config=config, store=store)
    csrf_token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
            if not self._valid_host_origin():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if self.path == "/" or self.path.startswith("/?"):
                self._send_html(_INDEX_HTML.replace("__CSRF_TOKEN__", csrf_token))
                return
            if self.path == "/api/state":
                self._send_json(board.snapshot())
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
            if not self._valid_host_origin() or self.headers.get("X-Codex-Fleet-CSRF") != csrf_token:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/items":
                    payload = self._read_json()
                    self._send_json(board.create_item(str(payload.get("title", "")), payload.get("description")))
                    return
                if parsed.path.startswith("/api/items/") and parsed.path.endswith("/ready"):
                    item_id = parsed.path.removeprefix("/api/items/").removesuffix("/ready").strip("/")
                    self._send_json(board.move_to_ready(item_id))
                    return
                if parsed.path == "/api/run":
                    query = parse_qs(parsed.query)
                    self._send_json(board.run_ready_item(succeed=query.get("fail") != ["1"]))
                    return
            except Exception as exc:  # noqa: BLE001 - HTTP boundary returns readable demo errors.
                status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if str(exc) == "JSON body is too large" else HTTPStatus.BAD_REQUEST
                self._send_json({"error": str(exc), **board.snapshot()}, status=status)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                raise ValueError("Content-Type must be application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_JSON_BODY_BYTES:
                raise ValueError("JSON body is too large")
            if length <= 0:
                return {}
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _valid_host_origin(self) -> bool:
            host_header = self.headers.get("Host", "").split(":", 1)[0]
            if host_header and host_header not in LOOPBACK_HOSTS:
                return False
            origin = self.headers.get("Origin")
            if origin:
                origin_host = urlparse(origin).hostname
                if origin_host not in LOOPBACK_HOSTS:
                    return False
            return True

    server = ThreadingHTTPServer((host, port), Handler)
    actual_host, actual_port = cast(tuple[str, int], server.server_address[:2])
    return LocalUiServer(server=server, url=f"http://{actual_host}:{actual_port}")


_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>codex-fleet internal smoke harness</title>
  <style>
    :root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f7f8fa; color: #1f2328; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 24px; border-bottom: 1px solid #d8dee4; background: #ffffff; }
    h1 { margin: 0; font-size: 20px; font-weight: 700; }
    main { padding: 20px; }
    form { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 16px; }
    input { height: 34px; border: 1px solid #d0d7de; border-radius: 6px; padding: 0 10px; min-width: 260px; }
    button { height: 34px; border: 1px solid #8c959f; border-radius: 6px; background: #ffffff; color: #1f2328; padding: 0 12px; font-weight: 600; cursor: pointer; }
    button.primary { background: #0969da; border-color: #0969da; color: #ffffff; }
    button.danger { border-color: #cf222e; color: #cf222e; }
    .board { display: grid; grid-template-columns: repeat(4, minmax(210px, 1fr)); gap: 12px; align-items: start; }
    .column { min-height: 360px; border: 1px solid #d8dee4; border-radius: 8px; background: #ffffff; padding: 10px; }
    .column h2 { margin: 0 0 10px; font-size: 14px; color: #57606a; }
    .card { border: 1px solid #d8dee4; border-radius: 8px; padding: 10px; margin-bottom: 10px; background: #f6f8fa; }
    .card strong { display: block; font-size: 13px; margin-bottom: 3px; }
    .card p { margin: 0 0 8px; font-size: 13px; line-height: 1.35; }
    .run { margin-top: 8px; font-size: 12px; color: #1f2328; word-break: break-word; }
    .run div { margin-top: 4px; }
    .comments { margin-top: 8px; padding-top: 8px; border-top: 1px solid #d8dee4; font-size: 12px; color: #57606a; white-space: pre-wrap; }
    .toolbar { display: flex; gap: 8px; flex-wrap: wrap; }
    #message { min-height: 18px; color: #57606a; font-size: 13px; }
    @media (max-width: 900px) { .board { grid-template-columns: 1fr; } header { align-items: flex-start; flex-direction: column; } }
  </style>
</head>
<body>
  <header>
    <h1>codex-fleet internal smoke harness</h1>
    <div class="toolbar">
      <button class="primary" onclick="run(false)">Run Ready</button>
      <button class="danger" onclick="run(true)">Run Ready as Failure</button>
    </div>
  </header>
  <main>
    <form onsubmit="createItem(event)">
      <input id="title" name="title" value="Make a fake Codex change" aria-label="Work item title">
      <button type="submit">Create Item</button>
      <span id="message"></span>
    </form>
    <section class="board" id="board"></section>
  </main>
  <script>
    const csrfToken = "__CSRF_TOKEN__";
    const states = ["Backlog", "Ready", "Planning", "Running", "Needs Input", "Human Review", "Done"];

    async function api(path, options = {}) {
      const response = await fetch(path, options);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      return data;
    }

    async function refresh(data) {
      data = data || await api("/api/state");
      document.getElementById("message").textContent = data.message || data.error || "";
      const board = document.getElementById("board");
      board.innerHTML = "";
      for (const state of states) {
        const column = document.createElement("section");
        column.className = "column";
        column.innerHTML = `<h2>${state}</h2>`;
        for (const item of data.items.filter((candidate) => candidate.state === state)) {
          const card = document.createElement("article");
          card.className = "card";
          card.innerHTML = `<strong>${item.identifier}</strong><p>${escapeHtml(item.title)}</p>`;
          if (state === "Backlog") {
            const ready = document.createElement("button");
            ready.textContent = "Move to Ready";
            ready.onclick = async () => refresh(await api(`/api/items/${item.id}/ready`, { method: "POST", headers: { "X-Codex-Fleet-CSRF": csrfToken } }));
            card.appendChild(ready);
          }
          if (item.run && Object.keys(item.run).length > 0) {
            const run = document.createElement("div");
            run.className = "run";
            const fields = [
              ["Status", item.run.status],
              ["Run", item.run.id],
              ["Branch", item.run.branch_name],
              ["Worktree", item.run.worktree_path],
              ["Error", item.run.error]
            ].filter((entry) => entry[1]);
            run.innerHTML = fields.map((entry) => `<div><b>${entry[0]}:</b> ${escapeHtml(entry[1])}</div>`).join("");
            card.appendChild(run);
          }
          const comments = document.createElement("div");
          comments.className = "comments";
          comments.textContent = item.comments.join("\\n\\n");
          card.appendChild(comments);
          column.appendChild(card);
        }
        board.appendChild(column);
      }
    }

    async function createItem(event) {
      event.preventDefault();
      const title = document.getElementById("title").value;
      refresh(await api("/api/items", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Codex-Fleet-CSRF": csrfToken },
        body: JSON.stringify({ title })
      }));
    }

    async function run(fail) {
      await refresh(await api(`/api/run${fail ? "?fail=1" : ""}`, { method: "POST", headers: { "X-Codex-Fleet-CSRF": csrfToken } }));
      pollUntilTerminal();
    }

    function pollUntilTerminal() {
      const timer = setInterval(async () => {
        const data = await api("/api/state");
        await refresh(data);
        const running = data.items.some((item) => item.state === "Running");
        if (!running) clearInterval(timer);
      }, 500);
    }

    function escapeHtml(value) {
      return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
    }

    refresh();
  </script>
</body>
</html>
"""
