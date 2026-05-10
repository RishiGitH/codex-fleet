from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import time
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from codex_fleet.config import load_config, write_plane_tracker_config
from codex_fleet.execution_settings import (
    config_with_codex_settings,
    merged_work_item_settings,
    settings_value,
)
from codex_fleet.factory import build_plane_client, build_runner, build_tracker, default_store_path
from codex_fleet.folder_picker import FolderPickerError, pick_folder
from codex_fleet.harness import HarnessPlan, apply_harness, plan_harness
from codex_fleet.local_work_items import (
    LocalWorkItemStore,
    LocalWorkItemTracker,
    default_local_work_item_store_path,
)
from codex_fleet.models import RunStatus, WorkItem, WorkItemState
from codex_fleet.orchestrator import Orchestrator
from codex_fleet.plane import PlaneClient, PlaneSettings, plane_project_external_id
from codex_fleet.plane_bootstrap import ensure_plane_labels, ensure_plane_states
from codex_fleet.plane_local_bootstrap import (
    PlaneLocalBootstrapError,
    bootstrap_local_plane,
    create_local_plane_session,
)
from codex_fleet.plane_manager import DEFAULT_PLANE_URL
from codex_fleet.project_registry import (
    DEFAULT_CODEX_SETTINGS,
    LocalProject,
    ProjectRegistry,
    ProjectRegistryError,
    default_project_registry_path,
    discover_git_root,
    normalize_codex_settings,
)
from codex_fleet.store import RunStore, StoredArtifact, StoredEvent, StoredRun, StoredRunMessage
from codex_fleet.tracker import Tracker

DEFAULT_LOCAL_API_HOST = "127.0.0.1"
DEFAULT_LOCAL_API_PORT = 18790
STARTER_PROJECT_TYPES = {"blank", "simple-web", "node-next", "python"}
LOGIN_NONCE_TTL_SECONDS = 120
SESSION_CODE_TTL_SECONDS = 120
CODEX_FLEET_API_BUILD = "codex-fleet-api-board-simplified-v1"
CODEX_FLEET_APP_SERVER_PROTOCOL = "app-server-v2-effort-workspaceWrite"


class LocalApiError(RuntimeError):
    pass


class CodexNotConfiguredError(ProjectRegistryError):
    pass


class LocalApiServer(ThreadingHTTPServer):
    repo: Path
    registry: ProjectRegistry
    run_store: RunStore
    token: str
    unsafe_allow_remote: bool


def create_local_api_server(
    repo: Path,
    *,
    host: str = DEFAULT_LOCAL_API_HOST,
    port: int = DEFAULT_LOCAL_API_PORT,
    unsafe_allow_remote: bool = False,
) -> LocalApiServer:
    if not unsafe_allow_remote and host not in {"127.0.0.1", "localhost", "::1"}:
        raise LocalApiError("codex-fleet local API must bind to loopback unless explicitly unsafe.")
    repo = repo.expanduser().absolute()
    server = LocalApiServer((host, port), _Handler)
    server.repo = repo
    server.registry = ProjectRegistry(default_project_registry_path(repo))
    server.run_store = RunStore(default_store_path(repo))
    server.token = load_or_create_local_api_token(repo)
    server.unsafe_allow_remote = unsafe_allow_remote
    return server


def load_or_create_local_api_token(repo: Path) -> str:
    secrets_dir = repo.expanduser().absolute() / ".codex-fleet" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    token_path = secrets_dir / "local_api_token"
    if token_path.exists():
        return token_path.read_text().strip()
    token = secrets.token_urlsafe(32)
    token_path.write_text(token + "\n")
    token_path.chmod(0o600)
    return token


def build_onboarding_url(
    repo: Path,
    *,
    plane_url: str,
    project_path: Path | None = None,
    api_url: str | None = None,
    include_token: bool = True,
) -> str:
    """Build the local Plane fork onboarding URL.

    The token is placed in the URL fragment so it is not sent to the static Plane
    web server. It still belongs to the local browser profile, so callers should
    only use this with a loopback Plane URL on a trusted machine.
    """
    repo = repo.expanduser().absolute()
    fragment: dict[str, str] = {
        "apiUrl": api_url or f"http://{DEFAULT_LOCAL_API_HOST}:{DEFAULT_LOCAL_API_PORT}",
        "path": str((project_path or repo).expanduser().absolute()),
    }
    if include_token:
        fragment["token"] = load_or_create_local_api_token(repo)
    return f"{plane_url.rstrip('/')}/codex-fleet/onboarding#{urlencode(fragment)}"


def build_plane_login_url(
    repo: Path,
    *,
    api_url: str,
    plane_url: str,
    redirect_path: str,
) -> str:
    repo = repo.expanduser().absolute()
    nonce = create_one_time_local_api_code(repo, kind="login", ttl_seconds=LOGIN_NONCE_TTL_SECONDS)
    redirect = _with_codex_fleet_fragment(
        f"{plane_url.rstrip('/')}/{redirect_path.lstrip('/')}",
        api_url=api_url,
    )
    query = urlencode(
        {
            "nonce": nonce,
            "redirect": redirect,
            "planeOrigin": plane_url.rstrip("/"),
        }
    )
    return f"{api_url.rstrip('/')}/api/plane/login?{query}"


def _safe_relative_plane_path(path: str) -> str | None:
    candidate = path.strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return None
    candidate = candidate.lstrip("/")
    if not candidate or candidate.startswith("../") or "/../" in f"/{candidate}":
        return None
    return candidate


def _with_codex_fleet_fragment(url: str, *, api_url: str, token: str | None = None, code: str | None = None) -> str:
    parsed = urlparse(url)
    fragment = parse_qs(parsed.fragment)
    fragment["apiUrl"] = [api_url.rstrip("/")]
    if token is not None:
        fragment["token"] = [token]
    if code is not None:
        fragment["code"] = [code]
    return urlunparse(parsed._replace(fragment=urlencode(fragment, doseq=True)))


def create_one_time_local_api_code(repo: Path, *, kind: str, ttl_seconds: int) -> str:
    code = secrets.token_urlsafe(24)
    path = _one_time_code_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    codes = _read_one_time_codes(path)
    now = time.time()
    codes = {
        key: value
        for key, value in codes.items()
        if isinstance(value, dict) and float(value.get("expires_at", 0)) > now
    }
    codes[code] = {"kind": kind, "expires_at": now + ttl_seconds}
    path.write_text(json.dumps(codes, sort_keys=True))
    path.chmod(0o600)
    return code


def consume_one_time_local_api_code(repo: Path, code: str, *, kind: str) -> bool:
    path = _one_time_code_path(repo)
    codes = _read_one_time_codes(path)
    value = codes.pop(code, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(codes, sort_keys=True))
    path.chmod(0o600)
    if not isinstance(value, dict):
        return False
    if value.get("kind") != kind:
        return False
    return float(value.get("expires_at", 0)) > time.time()


def _one_time_code_path(repo: Path) -> Path:
    return repo.expanduser().absolute() / ".codex-fleet" / "secrets" / "local_api_one_time_codes.json"


def _read_one_time_codes(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


class _Handler(BaseHTTPRequestHandler):
    server: LocalApiServer

    def _log(self, message: str) -> None:
        print(f"codex-fleet API: {message}", file=sys.stderr, flush=True)

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API.
        self._send_json({"ok": True})

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/health", "/api/health", "/api/status"}:
            self._send_json(
                {
                    "ok": True,
                    "service": "codex-fleet-api",
                    "build": CODEX_FLEET_API_BUILD,
                    "app_server_protocol": CODEX_FLEET_APP_SERVER_PROTOCOL,
                }
            )
            return
        if path == "/api/session":
            if not self._authorized():
                self._send_auth_missing()
                return
            self._send_json(
                {
                    "ok": True,
                    "connected": True,
                    "service": "codex-fleet-api",
                    "build": CODEX_FLEET_API_BUILD,
                    "app_server_protocol": CODEX_FLEET_APP_SERVER_PROTOCOL,
                    "repo": str(self.server.repo),
                    "projects": len(self.server.registry.list_projects()),
                }
            )
            return
        if path == "/api/folders/check":
            if not self._authorized():
                self._send_auth_missing()
                return
            self._send_json(
                {
                    "ok": True,
                    "available": True,
                    "picker": "native",
                }
            )
            return
        if path == "/api/session/exchange":
            query = parse_qs(parsed.query)
            code = query.get("code", [""])[0]
            if not code or not consume_one_time_local_api_code(self.server.repo, code, kind="session"):
                self._send_error(HTTPStatus.UNAUTHORIZED, "Local launcher connection expired.", code="auth_missing")
                return
            self._send_json(
                {
                    "ok": True,
                    "apiUrl": _server_api_url(self.server),
                    "token": self.server.token,
                }
            )
            return
        if path == "/api/plane/connect":
            query = parse_qs(parsed.query)
            redirect_path = query.get("redirect_path", ["codex-fleet/projects/"])[0]
            safe_redirect_path = _safe_relative_plane_path(redirect_path)
            if safe_redirect_path is None:
                self._send_error(HTTPStatus.BAD_REQUEST, "Redirect path must be a relative local Plane path.", code="bad_request")
                return
            config_path = self.server.repo / ".codex-fleet.yml"
            plane_url = DEFAULT_PLANE_URL
            if config_path.exists():
                config = load_config(self.server.repo)
                plane_url = config.tracker.plane_base_url or DEFAULT_PLANE_URL
            session_code = create_one_time_local_api_code(self.server.repo, kind="session", ttl_seconds=SESSION_CODE_TTL_SECONDS)
            redirect = _with_codex_fleet_fragment(
                f"{plane_url.rstrip('/')}/{safe_redirect_path.lstrip('/')}",
                api_url=_server_api_url(self.server),
                code=session_code,
            )
            self.send_response(HTTPStatus.FOUND.value)
            self.send_header("Location", redirect)
            self.end_headers()
            return
        if path == "/api/plane/login":
            query = parse_qs(parsed.query)
            nonce = query.get("nonce", [""])[0]
            if not nonce or not consume_one_time_local_api_code(self.server.repo, nonce, kind="login"):
                self._send_auth_missing()
                return
            redirect = query.get("redirect", [""])[0]
            plane_origin = query.get("planeOrigin", [""])[0]
            if not _safe_loopback_redirect(redirect, expected_origin=plane_origin):
                self._send_error(HTTPStatus.BAD_REQUEST, "Redirect must be a loopback http URL.")
                return
            try:
                session = create_local_plane_session(self.server.repo)
            except PlaneLocalBootstrapError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            session_code = create_one_time_local_api_code(self.server.repo, kind="session", ttl_seconds=SESSION_CODE_TTL_SECONDS)
            redirect = _with_codex_fleet_fragment(
                redirect,
                api_url=_server_api_url(self.server),
                code=session_code,
            )
            self._send_plane_login_redirect(redirect, session.session_key)
            return
        if path == "/api/projects":
            if not self._authorized():
                self._send_auth_missing()
                return
            try:
                projects = self.server.registry.list_projects()
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc), code="stale_runtime_state")
                return
            self._send_json({"projects": [_project_payload(project) for project in projects]})
            return
        if path.startswith("/api/projects/") and (path.endswith("/settings") or path.endswith("/fleet-settings")):
            if not self._authorized():
                self._send_auth_missing()
                return
            project_id = path.removeprefix("/api/projects/")
            project_id = project_id.removesuffix("/settings").removesuffix("/fleet-settings").strip("/")
            try:
                project = _project_from_path_ref(self.server, project_id)
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=project_id)
                return
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc), code="stale_runtime_state")
                return
            if project is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Project not found.")
                return
            self._send_json({"settings": project.codex_settings, "project": _project_payload(project)})
            return
        if path.startswith("/api/projects/") and path.endswith("/agent-analytics"):
            if not self._authorized():
                self._send_auth_missing()
                return
            project_id = path.removeprefix("/api/projects/").removesuffix("/agent-analytics").strip("/")
            try:
                project = _project_from_path_ref(self.server, project_id)
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=project_id)
                return
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc), code="stale_runtime_state")
                return
            if project is None and project_id not in {"current", "local"}:
                self._send_error(HTTPStatus.NOT_FOUND, "Project not found.")
                return
            repo = project.repo_path if project is not None else self.server.repo
            store = RunStore(default_store_path(repo))
            self._send_json({"analytics": _agent_analytics_payload(store)})
            return
        if path.startswith("/api/projects/") and path.endswith("/fleet-logs"):
            if not self._authorized():
                self._send_auth_missing()
                return
            project_id = path.removeprefix("/api/projects/").removesuffix("/fleet-logs").strip("/")
            try:
                project = _project_from_path_ref(self.server, project_id)
            except CodexNotConfiguredError:
                self._send_json({"fleet_logs": _unlinked_fleet_logs_payload(self.server.repo, plane_project_id=project_id)})
                return
            if project is None and project_id not in {"current", "local"}:
                self._send_json(
                    {
                        "fleet_logs": _unlinked_fleet_logs_payload(
                            self.server.repo,
                            plane_project_id=project_id,
                        )
                    }
                )
                return
            repo = project.repo_path if project is not None else self.server.repo
            self._send_json({"fleet_logs": _fleet_logs_payload(repo, project)})
            return
        if path.startswith("/api/projects/") and path.endswith("/fleet-dashboard"):
            if not self._authorized():
                self._send_auth_missing()
                return
            project_id = path.removeprefix("/api/projects/").removesuffix("/fleet-dashboard").strip("/")
            try:
                project = _project_from_path_ref(self.server, project_id)
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=project_id)
                return
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc), code="stale_runtime_state")
                return
            if project is None and project_id not in {"current", "local"}:
                self._send_error(HTTPStatus.NOT_FOUND, "Project not found.")
                return
            repo = project.repo_path if project is not None else self.server.repo
            self._send_json({"dashboard": _fleet_dashboard_payload(repo, project)})
            return
        if path.startswith("/api/projects/") and path.endswith("/revision"):
            if not self._authorized():
                self._send_auth_missing()
                return
            project_id = path.removeprefix("/api/projects/").removesuffix("/revision").strip("/")
            try:
                project = _project_from_path_ref(self.server, project_id)
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=project_id)
                return
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc), code="stale_runtime_state")
                return
            if project is None and project_id not in {"current", "local"}:
                self._send_error(HTTPStatus.NOT_FOUND, "Project not found.")
                return
            repo = project.repo_path if project is not None else self.server.repo
            self._send_json({"revision": RunStore(default_store_path(repo)).revision()})
            return
        if path.startswith("/api/projects/") and path.endswith("/control-plane-status"):
            if not self._authorized():
                self._send_auth_missing()
                return
            project_id = path.removeprefix("/api/projects/").removesuffix("/control-plane-status").strip("/")
            try:
                project = _project_from_path_ref(self.server, project_id)
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=project_id)
                return
            if project is None and project_id not in {"current", "local"}:
                self._send_error(HTTPStatus.NOT_FOUND, "Project not found.")
                return
            repo = project.repo_path if project is not None else self.server.repo
            self._send_json({"status": _control_plane_status_payload(self.server, repo)})
            return
        if path.startswith("/api/projects/"):
            if not self._authorized():
                self._send_auth_missing()
                return
            project_id = path.removeprefix("/api/projects/").strip("/")
            try:
                project = self.server.registry.get_project(int(project_id))
            except ValueError:
                self._send_error(HTTPStatus.BAD_REQUEST, "Project id must be an integer.")
                return
            if project is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Project not found.")
                return
            self._send_json({"project": _project_payload(project)})
            return
        if path == "/api/runs":
            if not self._authorized():
                self._send_auth_missing()
                return
            try:
                repo = _repo_from_query(self.server, parse_qs(parsed.query))
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=parse_qs(parsed.query).get("plane_project_id", [""])[0])
                return
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"runs": [_run_payload(run) for run in RunStore(default_store_path(repo)).list_runs()]})
            return
        if path == "/api/events":
            if not self._authorized():
                self._send_auth_missing()
                return
            query = parse_qs(parsed.query)
            try:
                repo = _repo_from_query(self.server, query)
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=query.get("plane_project_id", [""])[0])
                return
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            limit = _int_query(query.get("limit", ["100"])[0], default=100, minimum=1, maximum=500)
            self._send_json({"events": [_event_payload(event) for event in RunStore(default_store_path(repo)).list_recent_events(limit=limit)]})
            return
        if path == "/api/work-items/ready":
            if not self._authorized():
                self._send_auth_missing()
                return
            try:
                repo = _repo_from_query(self.server, parse_qs(parsed.query))
                items = _candidate_work_items(repo)
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=parse_qs(parsed.query).get("plane_project_id", [""])[0])
                return
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"items": [_work_item_payload(item) for item in items]})
            return
        if path.startswith("/api/runs/") and path.endswith("/events"):
            if not self._authorized():
                self._send_auth_missing()
                return
            try:
                repo = _repo_from_query(self.server, parse_qs(parsed.query))
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            store = RunStore(default_store_path(repo))
            run_id = path.removeprefix("/api/runs/").removesuffix("/events").strip("/")
            if store.get_run(run_id) is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Run not found.")
                return
            self._send_json({"events": [_event_payload(event) for event in store.list_events(run_id)]})
            return
        if path.startswith("/api/runs/") and path.endswith("/transcript"):
            if not self._authorized():
                self._send_auth_missing()
                return
            try:
                repo = _repo_from_query(self.server, parse_qs(parsed.query))
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            query = parse_qs(parsed.query)
            store = RunStore(default_store_path(repo))
            run_id = path.removeprefix("/api/runs/").removesuffix("/transcript").strip("/")
            if store.get_run(run_id) is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Run not found.")
                return
            view = query.get("view", ["chat"])[0]
            messages = _filter_run_messages_for_view(store.list_run_messages(run_id), view=view)
            self._send_json({"messages": [_run_message_payload(message) for message in messages]})
            return
        if path.startswith("/api/projects/") and path.endswith("/worktrees"):
            if not self._authorized():
                self._send_auth_missing()
                return
            project_ref = path.removeprefix("/api/projects/").removesuffix("/worktrees").strip("/")
            try:
                project = _project_from_path_ref(self.server, project_ref)
                repo = project.repo_path if project is not None else _repo_from_query(self.server, parse_qs(parsed.query))
            except (CodexNotConfiguredError, ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"worktrees": _worktrees_payload(repo)})
            return
        if path.startswith("/api/runs/") and "/artifacts/" in path:
            if not self._authorized():
                self._send_auth_missing()
                return
            try:
                repo = _repo_from_query(self.server, parse_qs(parsed.query))
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            parts = [part for part in path.strip("/").split("/") if part]
            if len(parts) != 5 or parts[:2] != ["api", "runs"] or parts[3] != "artifacts":
                self._send_error(HTTPStatus.NOT_FOUND, "Unknown artifact endpoint.")
                return
            try:
                self._send_artifact(repo, run_id=parts[2], artifact_id=int(parts[4]))
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if path.startswith("/api/runs/"):
            if not self._authorized():
                self._send_auth_missing()
                return
            try:
                repo = _repo_from_query(self.server, parse_qs(parsed.query))
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            store = RunStore(default_store_path(repo))
            run_id = path.removeprefix("/api/runs/").strip("/")
            run = store.get_run(run_id)
            if run is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Run not found.")
                return
            self._send_json({"run": _run_detail_payload(store, run)})
            return
        if path.startswith("/api/work-items/") and path.endswith("/run-status"):
            if not self._authorized():
                self._send_auth_missing()
                return
            try:
                repo = _repo_from_query(self.server, parse_qs(parsed.query))
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            store = RunStore(default_store_path(repo))
            item_id = path.removeprefix("/api/work-items/").removesuffix("/run-status").strip("/")
            run = store.latest_run_for_item(item_id)
            self._send_json({"run": _run_detail_payload(store, run) if run is not None else None})
            return
        if path.startswith("/api/work-items/") and path.endswith("/children"):
            if not self._authorized():
                self._send_auth_missing()
                return
            try:
                repo = _repo_from_query(self.server, parse_qs(parsed.query))
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            store = RunStore(default_store_path(repo))
            item_id = path.removeprefix("/api/work-items/").removesuffix("/children").strip("/")
            self._send_json({"children": [_task_metadata_payload(child) for child in store.list_child_task_metadata(item_id)]})
            return
        if path.startswith("/api/work-items/") and path.endswith("/graph"):
            if not self._authorized():
                self._send_auth_missing()
                return
            try:
                repo = _repo_from_query(self.server, parse_qs(parsed.query))
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            item_id = path.removeprefix("/api/work-items/").removesuffix("/graph").strip("/")
            self._send_json({"graph": _work_item_graph_payload(repo, item_id)})
            return
        if path.startswith("/api/work-items/") and path.endswith("/parent"):
            if not self._authorized():
                self._send_auth_missing()
                return
            try:
                repo = _repo_from_query(self.server, parse_qs(parsed.query))
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            store = RunStore(default_store_path(repo))
            item_id = path.removeprefix("/api/work-items/").removesuffix("/parent").strip("/")
            metadata = store.get_task_metadata(item_id)
            self._send_json({"parent": _task_metadata_payload(metadata) if metadata is not None else None})
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
        path = urlparse(self.path).path
        if not self._authorized():
            self._send_auth_missing()
            return
        if path == "/api/plane/login-url":
            payload = self._read_json()
            redirect_path = payload.get("redirect_path") if isinstance(payload, dict) else None
            if not isinstance(redirect_path, str):
                self._send_error(HTTPStatus.BAD_REQUEST, "Expected JSON body with 'redirect_path'.", code="bad_request")
                return
            safe_redirect_path = _safe_relative_plane_path(redirect_path)
            if safe_redirect_path is None:
                self._send_error(HTTPStatus.BAD_REQUEST, "Redirect path must be a relative local Plane path.", code="bad_request")
                return
            config_path = self.server.repo / ".codex-fleet.yml"
            plane_url = DEFAULT_PLANE_URL
            if config_path.exists():
                config = load_config(self.server.repo)
                plane_url = config.tracker.plane_base_url or DEFAULT_PLANE_URL
            url = build_plane_login_url(
                self.server.repo,
                api_url=_server_api_url(self.server),
                plane_url=plane_url,
                redirect_path=safe_redirect_path,
            )
            self._send_json({"url": url})
            return
        if path == "/api/onboarding/local-bootstrap":
            payload = self._read_json()
            path_value = payload.get("path") if isinstance(payload, dict) else None
            project_path = Path(path_value) if isinstance(path_value, str) and path_value.strip() else self.server.repo
            name_value = payload.get("name")
            name = name_value if isinstance(name_value, str) and name_value.strip() else None
            try:
                project = self.server.registry.add_project(project_path, name=name)
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            project, plane_mapping = _ensure_plane_project_mapping(self.server, project)
            plan = plan_harness(project.repo_path)
            status = plan.status
            self.server.registry.update_harness_status(project.id, status)
            self._send_json(
                {
                    "ok": True,
                    "project": _project_payload(project),
                    "harness": _harness_plan_payload(plan, status=status),
                    "plane": plane_mapping,
                },
                status=HTTPStatus.CREATED,
            )
            return
        if path == "/api/folders/pick":
            try:
                folder = pick_folder()
            except FolderPickerError as exc:
                error_code = "picker_cancelled" if "cancel" in str(exc).lower() else "picker_unavailable"
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc), code=error_code)
                return
            self._send_json({"path": str(folder.path), "name": folder.name})
            return
        if path == "/api/projects":
            payload = self._read_json()
            setup_log: list[str] = []
            project_name = payload.get("name") if isinstance(payload.get("name"), str) else "(unnamed)"
            self._log(f"project setup requested: name={project_name!r}")
            setup_log.append("Project setup requested.")
            if _bool_payload(payload, "create_new", default=False):
                try:
                    project_path = _create_starter_project(payload)
                except ProjectRegistryError as exc:
                    self._log(f"project setup failed while creating starter folder: {exc}")
                    self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                payload = {**payload, "path": str(project_path)}
                self._log(f"starter folder ready: {project_path}")
                setup_log.append(f"Starter folder created: {project_path}")
            path_value = payload.get("path") if isinstance(payload, dict) else None
            if not isinstance(path_value, str) or not path_value.strip():
                self._log("project setup rejected: missing path")
                self._send_error(HTTPStatus.BAD_REQUEST, "Expected JSON body with non-empty 'path'.")
                return
            name_value = payload.get("name")
            name = name_value if isinstance(name_value, str) and name_value.strip() else None
            apply_project_harness = _bool_payload(payload, "apply_harness", default=False)
            codex_settings = _codex_settings_payload(payload)
            require_plane_mapping = _bool_payload(payload, "require_plane_mapping", default=False)
            if not _bool_payload(payload, "create_new", default=False):
                git_root = discover_git_root(Path(path_value))
                if git_root is None:
                    self._log(f"project setup rejected: folder is not a git repository path={path_value!r}")
                    self._send_error(
                        HTTPStatus.BAD_REQUEST,
                        "Choose a git repository folder, or switch to Create new project. "
                        f"codex-fleet creates isolated git worktrees and cannot run from a non-git folder: {Path(path_value).expanduser().resolve()}",
                    )
                    return
            try:
                project = self.server.registry.add_project(Path(path_value), name=name, codex_settings=codex_settings)
            except ProjectRegistryError as exc:
                self._log(f"project setup failed while registering folder: {exc}")
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._log(f"local project registered: id={project.id} path={project.repo_path}")
            setup_log.append(f"Local project registered: {project.repo_path}")
            project, plane_mapping = _ensure_plane_project_mapping(self.server, project)
            self._log(f"Plane project mapping: status={plane_mapping.get('status')} project_id={plane_mapping.get('project_id')}")
            if require_plane_mapping and plane_mapping.get("status") != "linked":
                reason = str(plane_mapping.get("reason") or "Plane project mapping failed.")
                self._send_error(HTTPStatus.BAD_REQUEST, reason, code="plane_mapping_failed")
                return
            if plane_mapping.get("status") == "linked":
                setup_log.append(f"Plane project linked: {plane_mapping.get('project_id')}")
            else:
                setup_log.append(f"Plane project mapping {plane_mapping.get('status')}: {plane_mapping.get('reason', 'no detail')}")
            written: list[Path] = []
            if apply_project_harness:
                written = apply_harness(project.repo_path)
                self._log(f"harness applied: files={len(written)}")
                setup_log.append(f"Harness applied: {len(written)} files written.")
            plan = plan_harness(project.repo_path)
            status = plan.status
            self.server.registry.update_harness_status(project.id, status)
            project = self.server.registry.get_project(project.id) or project
            setup_log.append(f"Harness status: {status}.")
            initial_item = None
            initial_goal = payload.get("initial_goal")
            workflow_mode = str(project.codex_settings.get("workflow_mode") or "plan_execute")
            start_initial_goal = _bool_payload(payload, "start_initial_goal", default=True) and workflow_mode != "plan_only"
            if isinstance(initial_goal, str) and initial_goal.strip():
                try:
                    initial_item = _create_goal_work_item(
                        project.repo_path,
                        initial_goal,
                        ready=start_initial_goal,
                        settings=project.codex_settings,
                    )
                except (ProjectRegistryError, ValueError) as exc:
                    self._log(f"initial goal creation failed: {exc}")
                    self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                state_value = initial_item.state.value if hasattr(initial_item.state, "value") else str(initial_item.state)
                self._log(f"initial work item created: id={initial_item.id} state={state_value}")
                setup_log.append(f"Initial work item created in {state_value}: {initial_item.identifier}")
            self._send_json(
                {
                    "project": _project_payload(project),
                    "plane": plane_mapping,
                    "harness": _harness_plan_payload(plan, status=status),
                    "written": [str(path) for path in written],
                    "initial_item": _work_item_payload(initial_item) if initial_item is not None else None,
                    "setup_log": setup_log,
                },
                status=HTTPStatus.CREATED,
            )
            return
        if path == "/api/projects/configure-codex":
            payload = self._read_json()
            try:
                result = _configure_codex_for_plane_project(self.server, payload)
            except ProjectRegistryError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc), code="configure_codex_failed")
                return
            self._send_json(result, status=HTTPStatus.CREATED)
            return
        if path.startswith("/api/projects/") and (path.endswith("/settings") or path.endswith("/fleet-settings")):
            payload = self._read_json()
            project_id = path.removeprefix("/api/projects/")
            project_id = project_id.removesuffix("/settings").removesuffix("/fleet-settings").strip("/")
            try:
                current = _project_from_path_ref(self.server, project_id)
                if current is None:
                    raise ProjectRegistryError("Project not found.")
                project = self.server.registry.update_project_settings(current.id, _codex_settings_payload(payload))
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=project_id)
                return
            except (ValueError, ProjectRegistryError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"settings": project.codex_settings, "project": _project_payload(project)})
            return
        if path.startswith("/api/projects/") and "/harness/" in path:
            self._handle_harness_request()
            return
        if path == "/api/work-items":
            payload = self._read_json()
            try:
                repo = _repo_from_payload(self.server, payload)
                settings = _settings_for_payload(self.server, payload)
                item = _create_work_item(repo, payload, settings=settings)
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=str(payload.get("plane_project_id") or ""))
                return
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"item": _work_item_payload(item)}, status=HTTPStatus.CREATED)
            return
        if path in {"/api/runs", "/api/runs/next-ready", "/api/work-items/next/run"}:
            payload = self._read_json()
            fake = _bool_payload(payload, "fake", default=False)
            fake_succeed = _bool_payload(payload, "fake_succeed", default=True)
            try:
                repo = _repo_from_payload(self.server, payload)
                settings = _settings_for_payload(self.server, payload)
                item_id = payload.get("plane_work_item_id") or payload.get("work_item_id") or payload.get("item_id")
                if path == "/api/runs" and isinstance(item_id, str) and item_id.strip():
                    result = _run_work_item(
                        repo,
                        item_id.strip(),
                        fake=fake,
                        fake_succeed=fake_succeed,
                        settings=settings,
                    )
                else:
                    result = _run_next_work_item(repo, fake=fake, fake_succeed=fake_succeed, settings=settings)
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=str(payload.get("plane_project_id") or ""))
                return
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/work-items/") and path.endswith("/run"):
            item_id = path.removeprefix("/api/work-items/").removesuffix("/run").strip("/")
            if not item_id:
                self._send_error(HTTPStatus.BAD_REQUEST, "Missing work item id.")
                return
            payload = self._read_json()
            fake = _bool_payload(payload, "fake", default=False)
            fake_succeed = _bool_payload(payload, "fake_succeed", default=True)
            try:
                repo = _repo_from_payload(self.server, payload)
                settings = _settings_for_payload(self.server, payload)
                result = _run_work_item(repo, item_id, fake=fake, fake_succeed=fake_succeed, settings=settings)
            except CodexNotConfiguredError as exc:
                self._send_not_configured(str(exc), plane_project_id=str(payload.get("plane_project_id") or ""))
                return
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/work-items/") and path.endswith("/answer-input"):
            item_id = path.removeprefix("/api/work-items/").removesuffix("/answer-input").strip("/")
            if not item_id:
                self._send_error(HTTPStatus.BAD_REQUEST, "Missing work item id.")
                return
            payload = self._read_json()
            answer = payload.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                self._send_error(HTTPStatus.BAD_REQUEST, "Expected non-empty answer.")
                return
            try:
                repo = _repo_from_payload(self.server, payload)
                result = _answer_work_item_input(repo, item_id, answer.strip())
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/work-items/") and path.endswith("/settings"):
            item_id = path.removeprefix("/api/work-items/").removesuffix("/settings").strip("/")
            payload = self._read_json()
            try:
                repo = _repo_from_payload(self.server, payload)
                result = _update_work_item_settings(repo, item_id, payload)
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/work-items/") and path.endswith("/plan"):
            item_id = path.removeprefix("/api/work-items/").removesuffix("/plan").strip("/")
            payload = self._read_json()
            try:
                repo = _repo_from_payload(self.server, payload)
                tracker = _build_local_api_tracker(load_config(repo))
                items = tracker.fetch_items_by_ids([item_id])
                if not items:
                    raise ValueError(f"Work item not found: {item_id}")
                tracker.update_item_state(item_id, WorkItemState.READY.value)
                _update_work_item_settings(
                    repo,
                    item_id,
                    {
                        **payload,
                        "workflow_mode": "plan_execute",
                        "agent_role": "planner",
                    },
                )
                result = _run_work_item(repo, item_id, fake=_bool_payload(payload, "fake", default=False), fake_succeed=True, settings=payload)
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/work-items/") and path.endswith("/delivery-task"):
            item_id = path.removeprefix("/api/work-items/").removesuffix("/delivery-task").strip("/")
            payload = self._read_json()
            try:
                repo = _repo_from_payload(self.server, payload)
                result = _create_delivery_task(repo, item_id, payload)
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.CREATED)
            return
        if path.startswith("/api/work-items/") and path.endswith("/retry"):
            item_id = path.removeprefix("/api/work-items/").removesuffix("/retry").strip("/")
            if not item_id:
                self._send_error(HTTPStatus.BAD_REQUEST, "Missing work item id.")
                return
            payload = self._read_json()
            try:
                repo = _repo_from_payload(self.server, payload)
                result = _retry_work_item(repo, item_id)
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/work-items/") and path.endswith("/cancel"):
            item_id = path.removeprefix("/api/work-items/").removesuffix("/cancel").strip("/")
            if not item_id:
                self._send_error(HTTPStatus.BAD_REQUEST, "Missing work item id.")
                return
            payload = self._read_json()
            try:
                repo = _repo_from_payload(self.server, payload)
                result = _cancel_work_item(repo, item_id)
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/runs/") and path.endswith("/retry"):
            run_id = path.removeprefix("/api/runs/").removesuffix("/retry").strip("/")
            payload = self._read_json()
            try:
                repo = _repo_from_payload(self.server, payload)
                run = RunStore(default_store_path(repo)).get_run(run_id)
                if run is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "Run not found.")
                    return
                result = _retry_work_item(repo, run.item_id)
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/runs/") and path.endswith("/cancel"):
            run_id = path.removeprefix("/api/runs/").removesuffix("/cancel").strip("/")
            payload = self._read_json()
            try:
                repo = _repo_from_payload(self.server, payload)
                result = _cancel_run(repo, run_id)
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/delivery/") and path.endswith("/complete"):
            delivery_item_id = path.removeprefix("/api/delivery/").removesuffix("/complete").strip("/")
            payload = self._read_json()
            try:
                repo = _repo_from_payload(self.server, payload)
                result = _complete_delivery_task(repo, delivery_item_id)
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler API.
        path = urlparse(self.path).path
        if not self._authorized():
            self._send_auth_missing()
            return
        if path.startswith("/api/projects/") and path.endswith("/fleet-settings"):
            payload = self._read_json()
            project_id = path.removeprefix("/api/projects/").removesuffix("/fleet-settings").strip("/")
            try:
                current = _project_from_path_ref(self.server, project_id)
                if current is None:
                    raise ProjectRegistryError("Project not found.")
                settings = _codex_settings_payload(payload)
                updated = self.server.registry.update_project_settings(current.id, settings)
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"project": _project_payload(updated), "settings": updated.codex_settings})
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_harness_request(self) -> None:
        parts = [part for part in self.path.strip("/").split("/") if part]
        if len(parts) != 5 or parts[0] != "api" or parts[1] != "projects" or parts[3] != "harness":
            self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")
            return
        try:
            project_id = int(parts[2])
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "Project id must be an integer.")
            return
        project = self.server.registry.get_project(project_id)
        if project is None:
            self._send_error(HTTPStatus.NOT_FOUND, "Project not found.")
            return

        action = parts[4]
        if action == "plan":
            plan = plan_harness(project.repo_path)
            status = plan.status
            self.server.registry.update_harness_status(project.id, status)
            self._send_json({"harness": _harness_plan_payload(plan, status=status)})
            return
        if action == "apply":
            payload = self._read_json()
            overwrite = _bool_payload(payload, "overwrite", default=False)
            written = apply_harness(project.repo_path, overwrite=overwrite)
            plan = plan_harness(project.repo_path)
            status = plan.status
            self.server.registry.update_harness_status(project.id, status)
            self._send_json(
                {
                    "written": [str(path) for path in written],
                    "harness": _harness_plan_payload(plan, status=status),
                }
            )
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Unknown harness endpoint.")

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        token = self.headers.get("X-Codex-Fleet-Token", "")
        return auth == f"Bearer {self.server.token}" or token == self.server.token

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = min(int(raw_length), 1024 * 1024)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin", "")
        self.send_header("Access-Control-Allow-Origin", origin if _safe_loopback_origin(origin) else "null")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Codex-Fleet-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(body)

    def _send_auth_missing(self) -> None:
        self._send_error(HTTPStatus.UNAUTHORIZED, "Local launcher connection required.", code="auth_missing")

    def _send_not_configured(self, message: str | None = None, *, plane_project_id: str | None = None) -> None:
        payload: dict[str, Any] = {
            "ok": False,
            "code": "codex_not_configured",
            "error": message or "Codex is not configured for this project.",
            "message": "Configure Codex for this project to choose where agents should work.",
        }
        if plane_project_id:
            payload["plane_project_id"] = plane_project_id
        self._send_json(payload, status=HTTPStatus.CONFLICT)

    def _send_error(self, status: HTTPStatus, message: str, *, code: str | None = None) -> None:
        payload: dict[str, Any] = {"ok": False, "error": message}
        if code:
            payload["code"] = code
        self._send_json(payload, status=status)

    def _send_artifact(self, repo: Path, *, run_id: str, artifact_id: int) -> None:
        store = RunStore(default_store_path(repo))
        run = store.get_run(run_id)
        if run is None:
            self._send_error(HTTPStatus.NOT_FOUND, "Run not found.")
            return
        artifact = next((item for item in store.list_artifacts(run_id) if item.id == artifact_id), None)
        if artifact is None:
            self._send_error(HTTPStatus.NOT_FOUND, "Artifact not found.")
            return
        artifact_path = Path(artifact.path).expanduser().resolve()
        allowed_roots = [repo.expanduser().resolve()]
        if run.worktree_path:
            allowed_roots.append(Path(run.worktree_path).expanduser().resolve())
        if not any(_is_relative_to(artifact_path, root) for root in allowed_roots):
            self._send_error(HTTPStatus.FORBIDDEN, "Artifact path is outside the project/worktree.")
            return
        if not artifact_path.exists() or not artifact_path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "Artifact file not found.")
            return
        body = artifact_path.read_bytes()
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Codex-Fleet-Artifact-Kind", artifact.kind)
        self.send_header("X-Codex-Fleet-Artifact-Redaction", artifact.redaction)
        if artifact.sha256:
            self.send_header("X-Codex-Fleet-Artifact-Sha256", artifact.sha256)
        self.end_headers()
        self.wfile.write(body)

    def _send_plane_login_redirect(self, redirect: str, session_key: str) -> None:
        self.send_response(HTTPStatus.FOUND.value)
        self.send_header("Location", redirect)
        for cookie_name in ("session-id", "sessionid"):
            self.send_header(
                "Set-Cookie",
                f"{cookie_name}={session_key}; Max-Age=604800; Path=/; HttpOnly; SameSite=Lax",
            )
        self.end_headers()


def _project_payload(project: LocalProject) -> dict[str, Any]:
    payload: dict[str, Any] = asdict(project)
    payload["repo_path"] = str(payload["repo_path"])
    if payload["git_root"] is not None:
        payload["git_root"] = str(payload["git_root"])
    return payload


def _safe_loopback_redirect(url: str, *, expected_origin: str = "") -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    if not expected_origin:
        return True
    expected = urlparse(expected_origin)
    return (
        expected.scheme == parsed.scheme
        and expected.hostname == parsed.hostname
        and (expected.port or 80) == (parsed.port or 80)
    )


def _server_api_url(server: LocalApiServer) -> str:
    host, port = server.server_address[:2]
    host_text = host.decode("utf-8") if isinstance(host, bytes) else str(host)
    return f"http://{host_text}:{port}"


def _safe_loopback_origin(origin: str) -> bool:
    if not origin:
        return False
    parsed = urlparse(origin)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _ensure_plane_project_mapping(server: LocalApiServer, project: LocalProject) -> tuple[LocalProject, dict[str, Any]]:
    try:
        control_config = _load_or_bootstrap_plane_config(server.repo)
    except Exception as exc:
        return project, {"status": "skipped", "reason": f"control config unavailable: {exc}"}
    if control_config.tracker.kind != "plane":
        return project, {"status": "skipped", "reason": "control repo is not Plane-backed"}

    try:
        control_client = build_plane_client(control_config)
    except ValueError as exc:
        return project, {"status": "skipped", "reason": str(exc)}

    workspace_slug = control_client.settings.workspace_slug
    base_url = control_client.settings.base_url
    api_key = control_client.settings.api_key

    try:
        plane_project_id = project.plane_project_id
        if project.repo_path == control_config.repo and control_client.settings.project_id:
            plane_project_id = control_client.settings.project_id
        elif not plane_project_id:
            plane_project = control_client.ensure_project(
                name=project.name,
                identifier_seed=project.slug,
                external_id=plane_project_external_id(project.repo_path),
            )
            plane_project_id = str(plane_project["id"])

        mapped = server.registry.update_plane_mapping(
            project.id,
            workspace_slug=workspace_slug,
            project_id_in_plane=plane_project_id,
        )
        project_client = PlaneClient(
            PlaneSettings(
                base_url=base_url,
                api_key=api_key,
                workspace_slug=workspace_slug,
                project_id=plane_project_id,
            )
        )
        state_result = ensure_plane_states(project_client, control_config.tracker.active_states)
        created_labels = ensure_plane_labels(project_client)
        write_plane_tracker_config(
            mapped.repo_path,
            base_url=base_url,
            workspace_slug=workspace_slug,
            project_id=plane_project_id,
            api_key_value=api_key,
        )
        return mapped, {
            "status": "linked",
            "workspace_slug": workspace_slug,
            "project_id": plane_project_id,
            "created_states": list(state_result.created_states),
            "created_labels": list(created_labels),
            "config_path": str(mapped.repo_path / ".codex-fleet.yml"),
        }
    except Exception as exc:
        return project, {"status": "error", "reason": str(exc)}


def _configure_codex_for_plane_project(server: LocalApiServer, payload: dict[str, Any]) -> dict[str, Any]:
    plane_project_id = payload.get("plane_project_id")
    if not isinstance(plane_project_id, str) or not plane_project_id.strip():
        raise ProjectRegistryError("Plane project id is required.")
    plane_project_id = plane_project_id.strip()

    try:
        control_config = _load_or_bootstrap_plane_config(server.repo)
    except Exception as exc:
        raise ProjectRegistryError(f"Control repo config unavailable: {exc}") from exc
    if control_config.tracker.kind != "plane":
        raise ProjectRegistryError("Codex Fleet needs Plane mode before configuring Plane projects.")
    workspace_slug = control_config.tracker.plane_workspace_slug
    if not workspace_slug:
        raise ProjectRegistryError("Control repo is missing Plane workspace slug.")

    setup_log: list[str] = ["Codex project configuration requested from Plane."]
    mode = payload.get("mode")
    create_new = mode == "create_new" or _bool_payload(payload, "create_new", default=False)
    add_existing = mode == "add_existing" or (not create_new)
    if create_new:
        project_path = _create_starter_project(payload)
        setup_log.append(f"Repo folder ready: {project_path}")
    elif add_existing:
        path_value = payload.get("repo_path") or payload.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise ProjectRegistryError("Choose an existing git repo folder.")
        project_path = Path(path_value).expanduser().resolve()
        if discover_git_root(project_path) is None:
            raise ProjectRegistryError(f"Existing repo folder is not a git repository: {project_path}")
        setup_log.append(f"Existing repo selected: {project_path}")
    else:
        raise ProjectRegistryError("Configuration mode must be create_new or add_existing.")

    name_value = payload.get("name")
    name = name_value if isinstance(name_value, str) and name_value.strip() else None
    codex_settings = _codex_settings_payload(payload)
    project = server.registry.add_project(
        project_path,
        name=name,
        codex_settings=codex_settings,
        plane_workspace_slug=workspace_slug,
        plane_project_id=plane_project_id,
    )
    project, plane_mapping = _ensure_plane_project_mapping(server, project)
    if plane_mapping.get("status") != "linked":
        reason = str(plane_mapping.get("reason") or "Plane project mapping failed.")
        raise ProjectRegistryError(reason)
    setup_log.append(f"Plane project linked: {plane_project_id}")

    written: list[Path] = []
    if _bool_payload(payload, "apply_harness", default=True):
        written = apply_harness(project.repo_path)
        setup_log.append(f"Harness applied: {len(written)} files written.")
    plan = plan_harness(project.repo_path)
    server.registry.update_harness_status(project.id, plan.status)
    project = server.registry.get_project(project.id) or project
    setup_log.append(f"Harness status: {plan.status}.")
    _comment_plane_project_configured(server, project, setup_log)
    return {
        "ok": True,
        "project": _project_payload(project),
        "plane": plane_mapping,
        "harness": _harness_plan_payload(plan, status=plan.status),
        "written": [str(path) for path in written],
        "setup_log": setup_log,
    }


def _comment_plane_project_configured(server: LocalApiServer, project: LocalProject, setup_log: list[str]) -> None:
    try:
        config = load_config(project.repo_path)
        tracker = build_tracker(config)
        for item in tracker.fetch_candidate_items():
            if item.state == WorkItemState.READY.value:
                tracker.create_comment(
                    item.id,
                    "Codex is configured for this project.\n\n"
                    + "\n".join(f"- {line}" for line in setup_log)
                    + "\n\nReady work items will be picked up by the next codex-fleet daemon tick.",
                )
                return
    except Exception as exc:  # noqa: BLE001 - setup should succeed even if a comment cannot be added.
        print(f"codex-fleet API: project configured but comment skipped: {exc}", file=sys.stderr, flush=True)


def _load_or_bootstrap_plane_config(repo: Path) -> Any:
    config = load_config(repo)
    if config.tracker.kind == "plane":
        return config
    result = bootstrap_local_plane(repo)
    write_plane_tracker_config(
        repo,
        base_url=DEFAULT_PLANE_URL,
        workspace_slug=result.workspace_slug,
        project_id=result.project_id,
        api_key_value=result.api_key,
    )
    return load_config(repo)


def _create_starter_project(payload: dict[str, Any]) -> Path:
    name_value = payload.get("name")
    if not isinstance(name_value, str) or not name_value.strip():
        raise ProjectRegistryError("New projects need a non-empty name.")
    project_type = payload.get("project_type")
    project_type = project_type if isinstance(project_type, str) and project_type in STARTER_PROJECT_TYPES else "blank"
    location_value = payload.get("location") or payload.get("parent_path")
    if not isinstance(location_value, str) or not location_value.strip():
        raise ProjectRegistryError("New projects need a parent folder.")
    parent = Path(location_value).expanduser().resolve()
    if not parent.exists() or not parent.is_dir():
        raise ProjectRegistryError(f"Parent folder does not exist: {parent}")
    if parent == Path(parent.anchor):
        raise ProjectRegistryError("Refusing to create a project directly at the filesystem root.")
    folder_slug_value = payload.get("folder_slug") or payload.get("project_folder_name") or payload.get("slug")
    slug = _starter_slug(folder_slug_value) if isinstance(folder_slug_value, str) and folder_slug_value.strip() else _starter_slug(name_value)
    target = (parent / slug).resolve()
    if target.exists() and any(target.iterdir()):
        raise ProjectRegistryError(f"Project folder is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    _write_starter_files(target, name=name_value.strip(), project_type=project_type)
    if not (target / ".git").exists():
        subprocess.run(["git", "init"], cwd=target, text=True, capture_output=True, check=False)
    _ensure_initial_commit(target)
    return target


def _starter_slug(value: str) -> str:
    import re

    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
    return slug or "codex-fleet-project"


def _write_starter_files(target: Path, *, name: str, project_type: str) -> None:
    _write_if_missing(target / "README.md", f"# {name}\n\nCreated by codex-fleet.\n")
    if project_type == "simple-web":
        _write_if_missing(
            target / "index.html",
            "<!doctype html>\n<html lang=\"en\">\n<head><meta charset=\"utf-8\"><title>"
            + name
            + "</title></head>\n<body><main><h1>"
            + name
            + "</h1></main></body>\n</html>\n",
        )
    elif project_type == "node-next":
        _write_if_missing(
            target / "package.json",
            json.dumps(
                {
                    "name": _starter_slug(name).lower(),
                    "private": True,
                    "scripts": {
                        "dev": "next dev",
                        "build": "next build",
                        "lint": "next lint",
                    },
                    "dependencies": {"next": "latest", "react": "latest", "react-dom": "latest"},
                    "devDependencies": {},
                },
                indent=2,
            )
            + "\n",
        )
        _write_if_missing(target / "app/page.tsx", f"export default function Page() {{\n  return <main>{name}</main>;\n}}\n")
    elif project_type == "python":
        package = _starter_slug(name).lower().replace("-", "_").replace(".", "_")
        _write_if_missing(
            target / "pyproject.toml",
            "[project]\n"
            f"name = \"{_starter_slug(name).lower()}\"\n"
            "version = \"0.1.0\"\n"
            "requires-python = \">=3.11\"\n\n"
            "[tool.pytest.ini_options]\n"
            "testpaths = [\"tests\"]\n",
        )
        _write_if_missing(target / package / "__init__.py", "")
        _write_if_missing(target / "tests" / "test_smoke.py", "def test_smoke():\n    assert True\n")


def _write_if_missing(path: Path, content: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _ensure_initial_commit(target: Path) -> None:
    has_head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=target,
        text=True,
        capture_output=True,
        check=False,
    )
    if has_head.returncode == 0:
        return
    subprocess.run(["git", "add", "."], cwd=target, text=True, capture_output=True, check=False)
    subprocess.run(["git", "config", "user.email", "codex-fleet-local@example.invalid"], cwd=target, text=True, capture_output=True, check=False)
    subprocess.run(["git", "config", "user.name", "codex-fleet local"], cwd=target, text=True, capture_output=True, check=False)
    result = subprocess.run(
        ["git", "commit", "-m", "Initial codex-fleet starter project"],
        cwd=target,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ProjectRegistryError(f"Could not create initial git commit for starter project: {detail}")


def _run_payload(run: StoredRun) -> dict[str, Any]:
    settings = run.settings if isinstance(run.settings, dict) else {}
    return {
        "id": run.id,
        "item_id": run.item_id,
        "identifier": run.identifier,
        "status": run.status,
        "branch_name": run.branch_name,
        "worktree_path": run.worktree_path,
        "runner_name": run.runner_name,
        "agent_role": run.agent_role,
        "agent_name": run.agent_name,
        "agent_avatar": run.agent_avatar,
        "model": run.model,
        "reasoning_effort": run.reasoning_effort,
        "codex_thread_id": run.codex_thread_id,
        "codex_turn_id": run.codex_turn_id,
        "settings": settings,
        "effective_settings": settings,
        "settings_source": settings.get("settings_source"),
        "token_usage": run.token_usage,
        "error": run.error,
        "started_at": None,
        "completed_at": None,
        "duration_seconds": None,
        "workflow_mode": settings.get("workflow_mode"),
        "parent_workflow_mode": settings.get("parent_workflow_mode"),
        "prompt_role": run.agent_role,
        "is_legacy_runner": run.runner_name == "CodexCliRunner",
        "artifact_count": None,
        "changed_files": [],
        "latest_event_text": None,
        "blocker_text": run.error,
    }


def _run_detail_payload(store: RunStore, run: StoredRun) -> dict[str, Any]:
    payload = _run_payload(run)
    events = store.list_events(run.id)
    artifacts = store.list_artifacts(run.id)
    payload["events"] = [_event_payload(event) for event in events]
    payload["artifacts"] = [_artifact_payload(artifact) for artifact in artifacts]
    payload["transcript_preview"] = [
        _run_message_payload(message)
        for message in _filter_run_messages_for_view(store.list_run_messages(run.id), view="chat")[:8]
    ]
    payload["artifact_count"] = len(artifacts)
    payload["started_at"] = _first_event_time(events)
    payload["completed_at"] = _last_terminal_event_time(events)
    payload["duration_seconds"] = _duration_seconds(payload["started_at"], payload["completed_at"])
    payload["changed_files"] = _changed_files_from_events(events)
    payload["latest_event_text"] = _latest_event_text(events)
    payload["blocker_text"] = _blocker_text(run, events)
    return payload


_ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.CLAIM_ACQUIRED.value,
    RunStatus.PREPARING_WORKSPACE.value,
    RunStatus.WORKSPACE_READY.value,
    RunStatus.RUNNER_STARTED.value,
    RunStatus.RUNNER_STREAMING.value,
    RunStatus.RUNNING_CODEX.value,
    RunStatus.CANCEL_REQUESTED.value,
}


def _agent_analytics_payload(store: RunStore) -> dict[str, Any]:
    runs = store.list_runs(limit=500)
    by_role: dict[str, dict[str, Any]] = {}
    total_tokens = 0
    for run in runs:
        role = run.agent_role or str(run.settings.get("agent_role") or "unknown")
        bucket = by_role.setdefault(
            role,
            {
                "role": role,
                "runs": 0,
                "success": 0,
                "failed": 0,
                "active": 0,
                "cancelled": 0,
                "total_tokens": 0,
            },
        )
        bucket["runs"] += 1
        if run.status in {RunStatus.HUMAN_REVIEW.value, RunStatus.DONE.value}:
            bucket["success"] += 1
        elif run.status == RunStatus.CANCELLED.value:
            bucket["cancelled"] += 1
        elif run.status in _ACTIVE_RUN_STATUSES:
            bucket["active"] += 1
        elif run.status in {
            RunStatus.FAILED.value,
            RunStatus.REWORK.value,
            RunStatus.BLOCKED.value,
            RunStatus.NEEDS_INPUT.value,
            RunStatus.STALLED.value,
        }:
            bucket["failed"] += 1
        tokens = run.token_usage.get("total_tokens")
        if isinstance(tokens, int):
            bucket["total_tokens"] += tokens
            total_tokens += tokens
    return {
        "runs_total": len(runs),
        "active_runs": sum(1 for run in runs if run.status in _ACTIVE_RUN_STATUSES),
        "total_tokens": total_tokens,
        "by_role": sorted(by_role.values(), key=lambda item: str(item["role"])),
        "recent_events": [_event_payload(event) for event in store.list_recent_events(limit=25)],
    }


def _fleet_logs_payload(repo: Path, project: LocalProject | None) -> dict[str, Any]:
    store = RunStore(default_store_path(repo))
    runs = store.list_runs(limit=200)
    task_metadata = [metadata for parent_id in store.list_parent_item_ids_with_children() for metadata in store.list_child_task_metadata(parent_id)]
    latest_by_item = {run.item_id: run for run in runs}
    return {
        "project": _project_payload(project) if project is not None else None,
        "repo": str(repo.expanduser().resolve()),
        "analytics": _agent_analytics_payload(store),
        "runs": [_run_detail_payload(store, run) for run in runs],
        "recent_events": [_event_payload(event) for event in store.list_recent_events(limit=100)],
        "tasks": [
            {
                **_task_metadata_payload(metadata),
                "latest_run": _run_payload(latest_by_item[metadata.item_id]) if metadata.item_id in latest_by_item else None,
            }
            for metadata in task_metadata
        ],
    }


def _fleet_dashboard_payload(repo: Path, project: LocalProject | None) -> dict[str, Any]:
    store = RunStore(default_store_path(repo))
    metadata = store.list_task_metadata()
    root_tasks = [item for item in metadata if not item.parent_item_id]
    child_tasks = [item for item in metadata if item.parent_item_id]
    runs = store.list_runs(limit=200)
    latest_by_item: dict[str, StoredRun] = {}
    for run in runs:
        latest_by_item.setdefault(run.item_id, run)
    work_items_by_id = _dashboard_work_items_by_id(repo, [task.item_id for task in metadata])
    events = store.list_recent_events(limit=200)
    artifacts = [artifact for run in runs for artifact in store.list_artifacts(run.id)]

    def task_payload(task: Any) -> dict[str, Any]:
        latest = latest_by_item.get(task.item_id)
        return {
            **_task_metadata_payload(task, work_item=work_items_by_id.get(task.item_id)),
            "latest_run": _run_payload(latest) if latest is not None else None,
        }

    return {
        "project": _project_payload(project) if project is not None else None,
        "repo": str(repo.expanduser().resolve()),
        "revision": store.revision(),
        "root_tasks": [
            {
                **task_payload(task),
                "children": [task_payload(child) for child in metadata if child.parent_item_id == task.item_id],
            }
            for task in root_tasks
        ],
        "child_tasks": [task_payload(task) for task in child_tasks],
        "dependencies": [
            {"item_id": task.item_id, "depends_on": list(task.depends_on)}
            for task in metadata
            if task.depends_on
        ],
        "runs": [_run_detail_payload(store, run) for run in runs],
        "claims": [_claim_payload(claim) for claim in store.list_active_claims()],
        "events": [_event_payload(event) for event in events],
        "artifacts": [_artifact_payload(artifact) for artifact in artifacts],
        "active_agents": _active_agents_payload(store),
        "recent_agents": _recent_agents_payload(store),
        "transcript_previews": _transcript_previews_payload(store, runs),
        "token_usage": _dashboard_token_usage(runs),
        "needs_input": [_needs_input_payload(item) for item in store.list_open_needs_input()],
        "worktrees": _worktrees_payload(repo),
    }


def _worktrees_payload(repo: Path) -> list[dict[str, Any]]:
    store = RunStore(default_store_path(repo))
    runs = store.list_runs(limit=500)
    metadata_by_item = {metadata.item_id: metadata for metadata in store.list_task_metadata()}
    by_path: dict[str, dict[str, Any]] = {}
    for run in runs:
        if not run.worktree_path:
            continue
        metadata = metadata_by_item.get(run.item_id)
        settings = metadata.settings if metadata is not None else {}
        path = str(run.worktree_path)
        existing = by_path.get(path)
        payload = {
            "task_id": run.item_id,
            "task_key": run.identifier,
            "role": run.agent_role or (metadata.role if metadata is not None else None) or "orchestrator",
            "branch": run.branch_name,
            "path": path,
            "status": run.status,
            "last_run_id": run.id,
            "pr_url": settings.get("pr_url") if isinstance(settings, dict) else None,
            "delivery_status": settings.get("delivery_status") if isinstance(settings, dict) else None,
            "exists": Path(path).exists(),
        }
        if existing is None or _run_rank(str(payload["status"])) >= _run_rank(str(existing.get("status") or "")):
            by_path[path] = payload
    return sorted(by_path.values(), key=lambda item: (str(item.get("task_key") or ""), str(item.get("path") or "")))


def _run_rank(status: str) -> int:
    order = {
        RunStatus.RUNNING_CODEX.value: 5,
        RunStatus.RUNNER_STREAMING.value: 5,
        RunStatus.RUNNER_STARTED.value: 5,
        RunStatus.NEEDS_INPUT.value: 4,
        RunStatus.HUMAN_REVIEW.value: 3,
        RunStatus.DONE.value: 2,
        RunStatus.RUNNER_COMPLETED.value: 2,
        RunStatus.FAILED.value: 1,
    }
    return order.get(status, 0)


def _dashboard_work_items_by_id(repo: Path, item_ids: list[str]) -> dict[str, WorkItem]:
    ids = sorted({item_id for item_id in item_ids if item_id})
    if not ids:
        return {}
    try:
        tracker = _build_local_api_tracker(load_config(repo))
        return {item.id: item for item in tracker.fetch_items_by_ids(ids)}
    except Exception:
        return {}


def _work_item_graph_payload(repo: Path, item_id: str) -> dict[str, Any]:
    store = RunStore(default_store_path(repo))
    root = store.get_task_metadata(item_id)
    children = store.list_child_task_metadata(item_id)
    runs = [run for run in store.list_runs(limit=200) if run.item_id == item_id or run.item_id in {child.item_id for child in children}]
    return {
        "root": _task_metadata_payload(root) if root is not None else None,
        "children": [_task_metadata_payload(child) for child in children],
        "dependencies": [
            {"item_id": child.item_id, "depends_on": list(child.depends_on)}
            for child in children
            if child.depends_on
        ],
        "runs": [_run_detail_payload(store, run) for run in runs],
        "needs_input": [_needs_input_payload(item) for item in store.list_needs_input_for_item(item_id)],
    }


def _dashboard_token_usage(runs: list[StoredRun]) -> dict[str, Any]:
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    seen = False
    for run in runs:
        usage = run.token_usage if isinstance(run.token_usage, dict) else {}
        if not usage:
            continue
        seen = True
        for key in totals:
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] += value
    return totals if seen else {"status": "Unavailable"}


def _first_event_time(events: list[StoredEvent]) -> str | None:
    return events[0].created_at if events else None


def _last_terminal_event_time(events: list[StoredEvent]) -> str | None:
    terminal_kinds = {
        "runner_finished",
        "agent_session_finished",
        "completed",
        "needs_input",
        "failed",
        "cancelled",
        "parent_completed",
        "parent_blocked",
    }
    for event in reversed(events):
        if event.kind in terminal_kinds:
            return event.created_at
    return None


def _duration_seconds(started_at: str | None, completed_at: str | None) -> int | None:
    if not started_at or not completed_at:
        return None
    try:
        from datetime import datetime

        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((end - start).total_seconds()))


def _changed_files_from_events(events: list[StoredEvent]) -> list[str]:
    for event in reversed(events):
        files = event.payload.get("changed_files")
        if isinstance(files, list):
            return [str(item) for item in files if item is not None]
    return []


def _latest_event_text(events: list[StoredEvent]) -> str | None:
    if not events:
        return None
    return _event_text(events[-1])


def _blocker_text(run: StoredRun, events: list[StoredEvent]) -> str | None:
    if run.error:
        return run.error
    for event in reversed(events):
        if event.kind in {"needs_input", "parent_blocked", "failed"}:
            question = event.payload.get("question")
            if question:
                return str(question)
            message = event.payload.get("message") or event.payload.get("error")
            if message:
                return str(message)
            return _event_text(event)
    return None


def _event_text(event: StoredEvent) -> str:
    payload = event.payload
    identifier = payload.get("identifier")
    if event.kind == "claimed":
        return f"Claimed {identifier or payload.get('item_id') or 'work item'}."
    if event.kind in {"started", "runner_started", "agent_session_started"}:
        return "Agent session started."
    if event.kind in {"runner_finished", "agent_session_finished"}:
        return "Agent session finished."
    if event.kind == "needs_input":
        return "Agent needs input."
    if event.kind == "parent_completed":
        return "Parent task completed."
    if event.kind == "delivery_task_created":
        return f"Delivery task {payload.get('identifier') or ''} created.".strip()
    if event.kind == "workspace_prepared":
        return "Workspace prepared."
    return event.kind.replace("_", " ").capitalize() + "."


def _active_agents_payload(store: RunStore) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    for run in store.list_runs(limit=200):
        if run.status not in _ACTIVE_RUN_STATUSES:
            continue
        agents.append(
            {
                "run_id": run.id,
                "item_id": run.item_id,
                "role": run.agent_role,
                "name": run.agent_name,
                "status": run.status,
                "model": run.model,
                "reasoning_effort": run.reasoning_effort,
                "settings": run.settings,
            }
        )
    return agents


def _recent_agents_payload(store: RunStore) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    for run in store.list_runs(limit=50):
        agents.append(
            {
                "run_id": run.id,
                "item_id": run.item_id,
                "identifier": run.identifier,
                "role": run.agent_role,
                "name": run.agent_name,
                "status": run.status,
                "model": run.model,
                "reasoning_effort": run.reasoning_effort,
                "last_event": _run_message_payload(store.list_run_messages(run.id)[-1]) if store.list_run_messages(run.id) else None,
            }
        )
    return agents


def _transcript_previews_payload(store: RunStore, runs: list[StoredRun]) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for run in runs[:25]:
        messages = store.list_run_messages(run.id)[:3]
        if not messages:
            continue
        previews.append({"run_id": run.id, "messages": [_run_message_payload(message) for message in messages]})
    return previews


def _unlinked_fleet_logs_payload(_repo: Path, *, plane_project_id: str) -> dict[str, Any]:
    analytics = {
        "runs_total": 0,
        "active_runs": 0,
        "total_tokens": 0,
        "by_role": [],
        "recent_events": [],
    }
    return {
        "linked": False,
        "message": (
            "Codex is not configured for this project yet. "
            "Configure Codex for this project to create a new repo or add an existing repo before agents run."
        ),
        "plane_project_id": plane_project_id,
        "project": None,
        "repo": "",
        "analytics": analytics,
        "runs": [],
        "recent_events": [],
        "tasks": [],
    }


def _event_payload(event: StoredEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "kind": event.kind,
        "payload": event.payload,
        "created_at": event.created_at,
        "text": _event_text(event),
    }


def _artifact_payload(artifact: StoredArtifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "path": artifact.path,
        "kind": artifact.kind,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "redaction": artifact.redaction,
        "created_at": artifact.created_at,
    }


def _run_message_payload(message: StoredRunMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "run_id": message.run_id,
        "sequence": message.sequence,
        "kind": message.kind,
        "agent_role": message.agent_role,
        "agent_name": message.agent_name,
        "content": message.content,
        "artifact_path": message.artifact_path,
        "payload": message.payload,
        "created_at": message.created_at,
    }


def _filter_run_messages_for_view(messages: list[StoredRunMessage], *, view: str) -> list[StoredRunMessage]:
    normalized = view.strip().lower()
    if normalized == "raw":
        return messages
    if normalized == "timeline":
        return [message for message in messages if message.kind in {"system_event", "error", "tool_call", "tool_result"}]
    chat_kinds = {"chat_user", "chat_assistant", "tool_call", "tool_result", "needs_input", "final_answer", "error", "user", "assistant"}
    return [message for message in messages if message.kind in chat_kinds and not _looks_like_protocol_noise(message.content)]


def _looks_like_protocol_noise(content: str) -> bool:
    stripped = content.strip()
    if stripped in {"userMessage", "assistantMessage", "agentMessage", "reasoning", "codex", "codex_apps", "computer-use"}:
        return True
    if stripped.startswith(("item/", "turn/", "thread/")):
        return True
    return bool(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27,}", stripped))


def _needs_input_payload(item: Any) -> dict[str, Any]:
    return {
        "run_id": item.run_id,
        "item_id": item.item_id,
        "question": item.question,
        "asked_at": item.asked_at,
        "resolved_at": item.resolved_at,
        "answer": item.answer,
        "answer_comment_id": item.answer_comment_id,
    }


def _claim_payload(claim: Any) -> dict[str, Any]:
    return {
        "item_id": claim.item_id,
        "run_id": claim.run_id,
        "status": claim.status,
        "created_at": claim.created_at,
        "updated_at": claim.updated_at,
    }


def _token_usage_payload(token_usage: object) -> dict[str, int]:
    if token_usage is None:
        return {}
    return {
        key: value
        for key, value in {
            "input_tokens": getattr(token_usage, "input_tokens", None),
            "output_tokens": getattr(token_usage, "output_tokens", None),
            "total_tokens": getattr(token_usage, "total_tokens", None),
        }.items()
        if isinstance(value, int)
    }


def _task_metadata_payload(metadata: Any, *, work_item: WorkItem | None = None) -> dict[str, Any]:
    payload = {
        "item_id": metadata.item_id,
        "source": metadata.source,
        "depth": metadata.depth,
        "parent_item_id": metadata.parent_item_id,
        "parent_identifier": metadata.parent_identifier,
        "parent_run_id": metadata.parent_run_id,
        "created_by_run_id": metadata.created_by_run_id,
        "root_item_id": metadata.root_item_id,
        "role": metadata.role,
        "depends_on": list(metadata.depends_on),
        "generation": metadata.generation,
        "approval_mode": metadata.approval_mode,
        "terminal_outcome": metadata.terminal_outcome,
        "settings": metadata.settings,
        "created_at": metadata.created_at,
        "updated_at": metadata.updated_at,
    }
    if work_item is not None:
        payload.update(
            {
                "identifier": work_item.identifier,
                "title": work_item.title,
                "state": work_item.state,
                "priority": work_item.priority,
                "labels": list(work_item.labels),
            }
        )
    return payload


def _work_item_payload(item: WorkItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "identifier": item.identifier,
        "title": item.title,
        "description": item.description,
        "state": item.state,
        "priority": item.priority,
        "url": item.url,
        "labels": list(item.labels),
    }


def _harness_plan_payload(plan: HarnessPlan, *, status: str) -> dict[str, Any]:
    return {
        "repo": str(plan.repo),
        "status": status,
        "scan": {
            "git_root": str(plan.scan.git_root) if plan.scan.git_root else None,
            "dirty": plan.scan.dirty,
            "stack": plan.scan.stack,
            "package_manager": plan.scan.package_manager,
            "commands": {
                "install": plan.scan.install_command,
                "test": plan.scan.test_command,
                "lint": plan.scan.lint_command,
                "typecheck": plan.scan.typecheck_command,
                "build": plan.scan.build_command,
                "dev": plan.scan.dev_command,
            },
            "warnings": list(plan.scan.warnings),
        },
        "files": [
            {
                "path": str(file.path),
                "exists": file.exists,
            }
            for file in plan.files
        ],
        "missing": [str(file.path) for file in plan.missing],
    }


def _control_plane_status_payload(server: LocalApiServer, repo: Path) -> dict[str, Any]:
    config = load_config(repo)
    store = RunStore(default_store_path(config.repo))
    plane_ready = False
    plane_detail = "tracker is not Plane"
    if config.tracker.kind == "plane":
        try:
            client = build_plane_client(config)
            client.list_states()
            plane_ready = True
            plane_detail = "connected"
        except Exception as exc:  # noqa: BLE001 - status endpoint reports readiness, not failure.
            plane_detail = str(exc)
    runner_ready = True
    runner_detail: str = config.codex.runner
    if config.codex.runner == "cli":
        try:
            result = subprocess.run(
                config.codex.command.split()[:2] + ["--help"],
                cwd=config.repo,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            runner_ready = result.returncode == 0
            runner_detail = result.stderr.strip() or (result.stdout.splitlines()[0] if result.stdout else config.codex.command)
        except Exception as exc:  # noqa: BLE001
            runner_ready = False
            runner_detail = str(exc)
    return {
        "api": {"ready": True, "repo": str(server.repo), "project_repo": str(config.repo)},
        "daemon": {"ready": True, "store": str(store.path)},
        "plane": {"ready": plane_ready, "detail": plane_detail},
        "runner": {"ready": runner_ready, "detail": runner_detail},
        "auth": {"ready": bool(server.token), "mode": "local-token"},
    }


def _repo_from_query(server: LocalApiServer, query: dict[str, list[str]]) -> Path:
    return _repo_from_project_ref(
        server,
        project_id=query.get("project_id", [""])[0],
        plane_project_id=query.get("plane_project_id", [""])[0],
    )


def _repo_from_payload(server: LocalApiServer, payload: dict[str, Any]) -> Path:
    return _repo_from_project_ref(
        server,
        project_id=payload.get("project_id"),
        plane_project_id=payload.get("plane_project_id"),
    )


def _settings_for_payload(server: LocalApiServer, payload: dict[str, Any]) -> dict[str, Any]:
    project = _project_from_payload_ref(server, payload)
    base = project.codex_settings if project is not None else {}
    return normalize_codex_settings({**base, **_codex_settings_payload(payload)})


def _project_from_payload_ref(server: LocalApiServer, payload: dict[str, Any]) -> LocalProject | None:
    project_id = payload.get("project_id")
    if project_id is not None and project_id != "":
        if not isinstance(project_id, int | str):
            raise ProjectRegistryError("Project id must be an integer.")
        try:
            project = server.registry.get_project(int(project_id))
        except ValueError as exc:
            raise ProjectRegistryError("Project id must be an integer.") from exc
        if project is None:
            raise ProjectRegistryError("Project not found.")
        return project
    plane_project_id = payload.get("plane_project_id")
    if isinstance(plane_project_id, str) and plane_project_id.strip():
        try:
            config = load_config(server.repo)
        except Exception as exc:
            raise ProjectRegistryError(f"Control repo config unavailable: {exc}") from exc
        workspace_slug = config.tracker.plane_workspace_slug
        if not workspace_slug:
            raise ProjectRegistryError("Control repo is missing Plane workspace slug.")
        if config.tracker.plane_project_id == plane_project_id:
            return _project_for_repo(server, config.repo)
        project = server.registry.get_project_by_plane_id(
            workspace_slug=workspace_slug,
            plane_project_id=plane_project_id.strip(),
        )
        if project is None:
            raise CodexNotConfiguredError("Codex is not configured for this project.")
        return project
    return _project_for_repo(server, server.repo)


def _project_for_repo(server: LocalApiServer, repo: Path) -> LocalProject | None:
    resolved = repo.expanduser().resolve()
    for project in server.registry.list_projects():
        if project.repo_path.expanduser().resolve() == resolved:
            return project
    return None


def _repo_from_project_ref(server: LocalApiServer, *, project_id: object, plane_project_id: object = None) -> Path:
    if project_id is not None and project_id != "":
        return _repo_from_project_id(server, project_id)
    if plane_project_id is not None and plane_project_id != "":
        return _repo_from_plane_project_id(server, plane_project_id)
    return server.repo


def _repo_from_project_id(server: LocalApiServer, value: object) -> Path:
    if value is None or value == "":
        return server.repo
    if not isinstance(value, int | str):
        raise ProjectRegistryError("Project id must be an integer.")
    try:
        project_id = int(value)
    except ValueError as exc:
        raise ProjectRegistryError("Project id must be an integer.") from exc
    project = server.registry.get_project(project_id)
    if project is None:
        raise ProjectRegistryError("Project not found.")
    return project.repo_path


def _repo_from_plane_project_id(server: LocalApiServer, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ProjectRegistryError("Plane project id must be a string.")
    plane_project_id = value.strip()
    try:
        config = load_config(server.repo)
    except Exception as exc:
        raise ProjectRegistryError(f"Control repo config unavailable: {exc}") from exc
    workspace_slug = config.tracker.plane_workspace_slug
    if not workspace_slug:
        raise ProjectRegistryError("Control repo is missing Plane workspace slug.")
    if config.tracker.plane_project_id == plane_project_id:
        return server.repo
    project = server.registry.get_project_by_plane_id(
        workspace_slug=workspace_slug,
        plane_project_id=plane_project_id,
    )
    if project is None:
        raise CodexNotConfiguredError("Codex is not configured for this project.")
    return project.repo_path


def _project_from_path_ref(server: LocalApiServer, value: str) -> LocalProject | None:
    if value.isdigit():
        return server.registry.get_project(int(value))
    try:
        config = load_config(server.repo)
    except Exception:
        return None
    workspace_slug = config.tracker.plane_workspace_slug
    if not workspace_slug:
        return None
    if config.tracker.plane_project_id == value:
        return server.registry.add_project(
            config.repo,
            name=config.repo.name,
            plane_workspace_slug=workspace_slug,
            plane_project_id=value,
        )
    project = server.registry.get_project_by_plane_id(workspace_slug=workspace_slug, plane_project_id=value)
    if project is None and value not in {"current", "local"}:
        raise CodexNotConfiguredError("Codex is not configured for this project.")
    return project


def _candidate_work_items(repo: Path) -> list[WorkItem]:
    config = load_config(repo)
    try:
        tracker = _build_local_api_tracker(config)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return tracker.fetch_candidate_items()


def _run_next_work_item(
    repo: Path,
    *,
    fake: bool,
    fake_succeed: bool,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_config = load_config(repo)
    config = config_with_codex_settings(base_config, settings)
    try:
        tracker = _build_local_api_tracker(config)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    store = RunStore(default_store_path(config.repo))

    def settings_for_item(item: WorkItem) -> dict[str, Any]:
        return merged_work_item_settings(settings, item, store)

    result = Orchestrator(
        config=config,
        tracker=tracker,
        runner=build_runner(config, fake=fake, fake_succeed=fake_succeed),
        store=store,
        runner_factory=lambda item: build_runner(
            config_with_codex_settings(base_config, settings_for_item(item)),
            fake=fake,
            fake_succeed=fake_succeed,
            agent_role=str(settings_for_item(item).get("agent_role") or "implementer"),
            human_answers=_human_answers_from_settings(settings_for_item(item)),
        ),
        agent_task_settings_resolver=lambda item: (
            str(settings_value(settings_for_item(item), "workflow_mode")),
            _int_setting(settings_for_item(item), "max_depth"),
        ),
        max_child_tasks_per_run=_int_setting(settings, "max_child_tasks_per_run"),
    ).run_once()
    payload: dict[str, Any] = {
        "dispatched": result.dispatched,
        "message": result.message,
        "run": None,
    }
    if result.run is not None:
        payload["run"] = _run_detail_payload(
            store,
            StoredRun(
                id=result.run.id,
                item_id=result.run.item.id,
                identifier=result.run.item.identifier,
                status=result.run.status.value,
                branch_name=result.run.branch_name,
                worktree_path=str(result.run.worktree_path) if result.run.worktree_path else None,
                runner_name=result.run.runner_name,
                agent_role=result.run.agent_role,
                agent_name=result.run.agent_name,
                agent_avatar=result.run.agent_avatar,
                model=result.run.model,
                reasoning_effort=result.run.reasoning_effort,
                codex_thread_id=result.run.codex_thread_id,
                codex_turn_id=result.run.codex_turn_id,
                settings=result.run.settings,
                token_usage=_token_usage_payload(result.run.token_usage),
                error=result.run.error,
            )
        )
    return payload


def _run_work_item(
    repo: Path,
    item_id: str,
    *,
    fake: bool,
    fake_succeed: bool,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_config = load_config(repo)
    try:
        tracker = _build_local_api_tracker(base_config)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    items = tracker.fetch_items_by_ids([item_id])
    if not items:
        raise ValueError(f"Work item not found: {item_id}")
    store = RunStore(default_store_path(base_config.repo))
    merged_settings = merged_work_item_settings(settings or {}, items[0], store)
    merged_settings = normalize_codex_settings({**merged_settings, **_codex_settings_from_work_item(items[0])})
    config = _config_with_codex_settings(base_config, merged_settings)
    scoped_tracker = _SingleItemTracker(
        tracker,
        item_id=item_id,
        active_states=config.tracker.active_states,
    )
    result = Orchestrator(
        config=config,
        tracker=scoped_tracker,
        runner=build_runner(
            config,
            fake=fake,
            fake_succeed=fake_succeed,
            agent_role=str(merged_settings.get("agent_role") or "implementer"),
            human_answers=_human_answers_from_settings(merged_settings),
        ),
        store=store,
        workflow_mode=str(_settings_value(merged_settings, "workflow_mode")),
        max_depth=_int_setting(merged_settings, "max_depth"),
        max_child_tasks_per_run=_int_setting(merged_settings, "max_child_tasks_per_run"),
    ).run_once()
    payload: dict[str, Any] = {
        "dispatched": result.dispatched,
        "message": result.message,
        "run": None,
    }
    if result.run is not None:
        payload["run"] = _run_detail_payload(
            store,
            StoredRun(
                id=result.run.id,
                item_id=result.run.item.id,
                identifier=result.run.item.identifier,
                status=result.run.status.value,
                branch_name=result.run.branch_name,
                worktree_path=str(result.run.worktree_path) if result.run.worktree_path else None,
                runner_name=result.run.runner_name,
                agent_role=result.run.agent_role,
                agent_name=result.run.agent_name,
                agent_avatar=result.run.agent_avatar,
                model=result.run.model,
                reasoning_effort=result.run.reasoning_effort,
                codex_thread_id=result.run.codex_thread_id,
                codex_turn_id=result.run.codex_turn_id,
                settings=result.run.settings,
                token_usage=_token_usage_payload(result.run.token_usage),
                error=result.run.error,
            ),
        )
    return payload


def _answer_work_item_input(repo: Path, item_id: str, answer: str) -> dict[str, Any]:
    config = load_config(repo)
    tracker = _build_local_api_tracker(config)
    items = tracker.fetch_items_by_ids([item_id])
    if not items:
        raise ValueError(f"Work item not found: {item_id}")
    store = RunStore(default_store_path(config.repo))
    pending = store.latest_open_needs_input(item_id)
    latest = store.latest_run_for_item(item_id)
    if pending is None:
        question = latest.error if latest is not None and latest.status == RunStatus.NEEDS_INPUT.value else "Human provided follow-up input."
        run_id = latest.id if latest is not None else f"manual-{int(time.time())}"
        store.record_needs_input(run_id, item_id, question or "Human provided follow-up input.")
        pending = store.latest_open_needs_input(item_id)
    assert pending is not None
    tracker.create_comment(item_id, f"Human answer for codex-fleet:\n\n{answer}")
    _append_human_answer_to_task(store, item_id, question=pending.question, answer=answer, run_id=pending.run_id, comment_id=None)
    store.resolve_needs_input(pending.run_id, answer=answer, answer_comment_id=None)
    store.add_event(pending.run_id, "needs_input_resolved", {"item_id": item_id, "source": "codex_panel", "state": WorkItemState.READY.value})
    tracker.create_comment(item_id, "codex-fleet captured your answer and moved this task back to Ready.")
    tracker.update_item_state(item_id, WorkItemState.READY.value)
    updated = tracker.fetch_items_by_ids([item_id])
    return {
        "ok": True,
        "state": WorkItemState.READY.value,
        "resolved_run_id": pending.run_id,
        "revision": store.revision(),
        "item": _work_item_payload(updated[0]) if updated else None,
        "run": _run_detail_payload(store, latest) if latest is not None else None,
    }


def _append_human_answer_to_task(
    store: RunStore,
    item_id: str,
    *,
    question: str,
    answer: str,
    run_id: str,
    comment_id: str | None,
) -> None:
    metadata = store.get_task_metadata(item_id)
    entry = {"question": question, "answer": answer, "run_id": run_id, "comment_id": comment_id}
    if metadata is None:
        store.upsert_task_metadata(item_id=item_id, source="human-answer", settings={"human_answers": [entry]})
        return
    settings = dict(metadata.settings)
    existing = settings.get("human_answers")
    answers = [value for value in existing if isinstance(value, dict)] if isinstance(existing, list) else []
    answers.append(entry)
    settings["human_answers"] = answers[-10:]
    store.update_task_settings(item_id, settings)


def _human_answers_from_settings(settings: dict[str, Any]) -> list[dict[str, object]]:
    answers = settings.get("human_answers")
    if not isinstance(answers, list):
        return []
    return [answer for answer in answers if isinstance(answer, dict)]


def _retry_work_item(repo: Path, item_id: str) -> dict[str, Any]:
    config = load_config(repo)
    tracker = _build_local_api_tracker(config)
    items = tracker.fetch_items_by_ids([item_id])
    if not items:
        raise ValueError(f"Work item not found: {item_id}")
    store = RunStore(default_store_path(config.repo))
    latest = store.latest_run_for_item(item_id)
    if latest is not None:
        store.finish_claim(item_id, latest.id, "retry_requested")
        if latest.status in _ACTIVE_RUN_STATUSES:
            store.update_run_status(latest.id, RunStatus.CANCEL_REQUESTED.value, error="Retry requested from local UI.")
        store.add_event(latest.id, "retry_requested", {"state": WorkItemState.READY.value})
    tracker.create_comment(item_id, "codex-fleet retry requested. This item was moved back to Ready.")
    tracker.update_item_state(item_id, WorkItemState.READY.value)
    return {
        "ok": True,
        "item": _work_item_payload(tracker.fetch_items_by_ids([item_id])[0]),
        "previous_run": _run_payload(latest) if latest is not None else None,
        "state": WorkItemState.READY.value,
    }


def _cancel_work_item(repo: Path, item_id: str) -> dict[str, Any]:
    config = load_config(repo)
    tracker = _build_local_api_tracker(config)
    items = tracker.fetch_items_by_ids([item_id])
    if not items:
        raise ValueError(f"Work item not found: {item_id}")
    store = RunStore(default_store_path(config.repo))
    latest = store.latest_run_for_item(item_id)
    if latest is not None:
        store.update_run_status(latest.id, RunStatus.CANCEL_REQUESTED.value, error="Cancelled by local API.")
        store.finish_claim(item_id, latest.id, "cancel_requested")
        store.add_event(latest.id, "cancel_requested", {"state": WorkItemState.NEEDS_INPUT.value, "run_status": RunStatus.CANCELLED.value})
        store.update_run_status(latest.id, RunStatus.CANCELLED.value, error="Cancelled by local API.")
        store.add_event(latest.id, "cancelled", {"state": WorkItemState.NEEDS_INPUT.value, "run_status": RunStatus.CANCELLED.value})
    tracker.create_comment(item_id, "codex-fleet cancelled this run. Review the history, then retry or leave it in Needs Input.")
    tracker.update_item_state(item_id, WorkItemState.NEEDS_INPUT.value)
    latest = store.latest_run_for_item(item_id)
    return {
        "ok": True,
        "item": _work_item_payload(tracker.fetch_items_by_ids([item_id])[0]),
        "run": _run_detail_payload(store, latest) if latest is not None else None,
        "state": WorkItemState.NEEDS_INPUT.value,
    }


def _create_delivery_task(repo: Path, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = load_config(repo)
    tracker = _build_local_api_tracker(config)
    items = tracker.fetch_items_by_ids([item_id])
    if not items:
        raise ValueError(f"Work item not found: {item_id}")
    parent = items[0]
    store = RunStore(default_store_path(config.repo))
    latest = store.latest_run_for_item(item_id)
    changed_files = payload.get("changed_files")
    changed_file_lines = "\n".join(f"- `{path}`" for path in changed_files if isinstance(path, str)) if isinstance(changed_files, list) else "- Not reported"
    artifact_lines = "- None reported"
    if latest is not None:
        artifacts = store.list_artifacts(latest.id)
        if artifacts:
            artifact_lines = "\n".join(f"- `{artifact.path}`" for artifact in artifacts)
    branch = str(payload.get("branch") or (latest.branch_name if latest is not None else "") or "Not reported")
    worktree = str(payload.get("worktree") or (latest.worktree_path if latest is not None else "") or "Not reported")
    instructions = _delivery_instructions(config.repo, branch=branch, worktree=worktree)
    title = f"Publish or merge result for {parent.identifier}"
    description = (
        f"Prepare delivery for `{parent.identifier}`.\n\n"
        f"Parent id: `{parent.id}`\n\n"
        f"Branch: `{branch}`\n\n"
        f"Worktree: `{worktree}`\n\n"
        f"Changed files:\n{changed_file_lines}\n\n"
        f"Artifacts:\n{artifact_lines}\n\n"
        f"Test results: {payload.get('test_results') or 'Not reported'}\n\n"
        f"Preview URL: {payload.get('preview_url') or 'Not available'}\n\n"
        f"{instructions}"
    )
    created = tracker.create_work_item(
        title=title,
        description=description,
        state=WorkItemState.BACKLOG.value,
        labels=("agent-delivery-manager", "delivery"),
    )
    if created is None:
        raise ValueError("codex-fleet could not create the delivery work item.")
    parent_metadata = store.get_task_metadata(item_id)
    store.upsert_task_metadata(
        item_id=created.id,
        source="delivery",
        depth=(parent_metadata.depth + 1) if parent_metadata is not None else 1,
        parent_item_id=item_id,
        parent_identifier=parent.identifier,
        root_item_id=(parent_metadata.root_item_id if parent_metadata is not None and parent_metadata.root_item_id else item_id),
        role="delivery_manager",
        depends_on=(item_id,),
        approval_mode="full_auto",
        settings={
            "workflow_mode": "execute_only",
            "phase": "delivery",
            "agent_role": "delivery_manager",
            "delivery_status": "task_created",
        },
    )
    store.add_event(latest.id if latest is not None else created.id, "delivery_task_created", {"item_id": created.id, "parent_item_id": item_id})
    tracker.create_comment(item_id, f"codex-fleet created delivery task `{created.identifier}`.")
    return {"ok": True, "item": _work_item_payload(created)}


def _complete_delivery_task(repo: Path, delivery_item_id: str) -> dict[str, Any]:
    config = load_config(repo)
    tracker = _build_local_api_tracker(config)
    items = tracker.fetch_items_by_ids([delivery_item_id])
    if not items:
        raise ValueError(f"Delivery work item not found: {delivery_item_id}")
    item = items[0]
    store = RunStore(default_store_path(config.repo))
    metadata = store.get_task_metadata(delivery_item_id)
    if metadata is None or metadata.role != "delivery_manager":
        raise ValueError("Only delivery-manager work items can be completed through delivery completion.")
    settings = dict(metadata.settings or {})
    branch = str(settings.get("branch") or "").strip()
    worktree = str(settings.get("worktree") or "").strip()
    if not branch or branch == "Not reported":
        _delivery_needs_input(tracker, store, item, metadata, "Delivery cannot complete because no branch was recorded.")
        return {"ok": False, "state": WorkItemState.NEEDS_INPUT.value, "error": "missing branch"}
    if not worktree or worktree == "Not reported":
        _delivery_needs_input(tracker, store, item, metadata, "Delivery cannot complete because no worktree was recorded.")
        return {"ok": False, "state": WorkItemState.NEEDS_INPUT.value, "error": "missing worktree"}

    worktree_path = Path(worktree).expanduser().resolve()
    workspace_root = config.workspace.root.expanduser()
    if not workspace_root.is_absolute():
        workspace_root = (config.repo / workspace_root).resolve()
    else:
        workspace_root = workspace_root.resolve()
    if not _is_relative_to(worktree_path, workspace_root):
        _delivery_needs_input(tracker, store, item, metadata, "Delivery cleanup refused because the worktree path is outside the registered workspace root.")
        return {"ok": False, "state": WorkItemState.NEEDS_INPUT.value, "error": "unsafe worktree"}

    clean = subprocess.run(["git", "-C", str(config.repo), "status", "--porcelain"], text=True, capture_output=True, check=False)
    if clean.returncode != 0 or clean.stdout.strip():
        _delivery_needs_input(tracker, store, item, metadata, "Delivery cannot merge because the main project worktree has uncommitted changes.")
        return {"ok": False, "state": WorkItemState.NEEDS_INPUT.value, "error": "main worktree dirty"}

    merge = subprocess.run(["git", "-C", str(config.repo), "merge", "--no-ff", branch], text=True, capture_output=True, check=False)
    if merge.returncode != 0:
        _delivery_needs_input(
            tracker,
            store,
            item,
            metadata,
            "Delivery merge failed. Resolve conflicts manually, then retry delivery completion.\n\n"
            f"Details:\n```\n{_tail((merge.stdout or '') + (merge.stderr or ''), limit=1500)}\n```",
        )
        return {"ok": False, "state": WorkItemState.NEEDS_INPUT.value, "error": "merge failed"}

    remove = subprocess.run(["git", "-C", str(config.repo), "worktree", "remove", str(worktree_path)], text=True, capture_output=True, check=False)
    prune = subprocess.run(["git", "-C", str(config.repo), "worktree", "prune"], text=True, capture_output=True, check=False)
    cleanup_error = ""
    if remove.returncode != 0:
        cleanup_error = _tail((remove.stdout or "") + (remove.stderr or ""), limit=1000)
    settings["delivery_status"] = "complete" if not cleanup_error else "merged_cleanup_needs_input"
    settings["merged_at"] = int(time.time())
    store.update_task_settings(delivery_item_id, settings)
    latest = store.latest_run_for_item(delivery_item_id)
    event_run_id = latest.id if latest is not None else delivery_item_id
    store.add_event(
        event_run_id,
        "delivery_completed",
        {"branch": branch, "worktree": str(worktree_path), "cleanup_error": cleanup_error or None, "prune": prune.returncode},
    )
    if cleanup_error:
        tracker.create_comment(item.id, f"codex-fleet merged `{branch}`, but worktree cleanup needs input.\n\nDetails:\n```\n{cleanup_error}\n```")
        tracker.update_item_state(item.id, WorkItemState.NEEDS_INPUT.value)
        return {"ok": False, "state": WorkItemState.NEEDS_INPUT.value, "error": cleanup_error}
    tracker.create_comment(item.id, f"codex-fleet merged `{branch}` and removed worktree `{worktree_path}`.")
    tracker.update_item_state(item.id, WorkItemState.DONE.value)
    return {"ok": True, "state": WorkItemState.DONE.value, "item": _work_item_payload(tracker.fetch_items_by_ids([item.id])[0])}


def _delivery_needs_input(
    tracker: Tracker,
    store: RunStore,
    item: WorkItem,
    metadata: Any,
    message: str,
) -> None:
    settings = dict(metadata.settings or {})
    settings["delivery_status"] = "needs_input"
    store.update_task_settings(item.id, settings)
    latest = store.latest_run_for_item(item.id)
    store.add_event(latest.id if latest is not None else item.id, "delivery_needs_input", {"error": message})
    tracker.create_comment(item.id, message)
    tracker.update_item_state(item.id, WorkItemState.NEEDS_INPUT.value)


def _tail(value: str, *, limit: int = 1200) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def _delivery_instructions(repo: Path, *, branch: str, worktree: str) -> str:
    remote = subprocess.run(["git", "-C", str(repo), "remote"], text=True, capture_output=True, check=False)
    has_remote = remote.returncode == 0 and bool(remote.stdout.strip())
    if has_remote:
        return (
            "GitHub/remote delivery instructions:\n"
            f"- Review the worktree: `{worktree}`\n"
            f"- Push the branch when ready: `git -C {repo} push -u origin {branch}`\n"
            "- Open a pull request from that branch after human review."
        )
    return (
        "Local delivery instructions:\n"
        f"- Review the worktree: `{worktree}`\n"
        f"- Merge locally when ready: `git -C {repo} merge {branch}`\n"
        "- No remote was detected, so codex-fleet will not suggest pushing or opening a pull request."
    )


def _cancel_run(repo: Path, run_id: str) -> dict[str, Any]:
    config = load_config(repo)
    store = RunStore(default_store_path(config.repo))
    run = store.get_run(run_id)
    if run is None:
        raise ValueError(f"Run not found: {run_id}")
    store.update_run_status(run.id, RunStatus.CANCEL_REQUESTED.value, error="Cancelled by local API.")
    store.finish_claim(run.item_id, run.id, "cancel_requested")
    store.add_event(run.id, "cancel_requested", {"state": WorkItemState.NEEDS_INPUT.value, "run_status": RunStatus.CANCELLED.value})
    store.update_run_status(run.id, RunStatus.CANCELLED.value, error="Cancelled by local API.")
    store.add_event(run.id, "cancelled", {"state": WorkItemState.NEEDS_INPUT.value, "run_status": RunStatus.CANCELLED.value})
    tracker = _build_local_api_tracker(config)
    tracker.create_comment(run.item_id, f"codex-fleet run `{run.id}` was cancelled from the local UI.")
    tracker.update_item_state(run.item_id, WorkItemState.NEEDS_INPUT.value)
    cancelled = store.get_run(run.id)
    assert cancelled is not None
    return {"ok": True, "run": _run_detail_payload(store, cancelled), "state": WorkItemState.NEEDS_INPUT.value}


def _create_work_item(repo: Path, payload: dict[str, Any], *, settings: dict[str, Any] | None = None) -> WorkItem:
    config = load_config(repo)
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Expected JSON body with non-empty 'title'.")
    description_value = payload.get("description")
    description = description_value if isinstance(description_value, str) and description_value.strip() else None
    if config.tracker.kind == "memory":
        created = _create_local_work_item(config.repo, title=title, description=description)
    else:
        tracker = build_tracker(config)
        maybe_created = tracker.create_work_item(
            title=title.strip(),
            description=description,
            state=WorkItemState.READY.value,
            labels=("human-requested",),
        )
        if maybe_created is None:
            raise ValueError("codex-fleet could not create the work item.")
        created = maybe_created
    RunStore(default_store_path(config.repo)).upsert_task_metadata(
        item_id=created.id,
        source="human-requested",
        depth=0,
        root_item_id=created.id,
        role="orchestrator",
        settings=normalize_codex_settings(settings),
    )
    return created


def _create_goal_work_item(
    repo: Path,
    goal: str,
    *,
    ready: bool,
    settings: dict[str, Any] | None = None,
) -> WorkItem:
    title, description = _goal_title_and_description(goal)
    state = WorkItemState.READY.value if ready else WorkItemState.BACKLOG.value
    config = load_config(repo)
    if config.tracker.kind == "memory":
        created = LocalWorkItemStore(default_local_work_item_store_path(repo)).create_item(
            title=title,
            description=description,
            state=state,
        )
    else:
        tracker = build_tracker(config)
        maybe_created = tracker.create_work_item(
            title=title,
            description=description,
            state=state,
            labels=("human-requested",),
        )
        if maybe_created is None:
            raise ValueError("codex-fleet could not create the initial goal work item.")
        created = maybe_created
    RunStore(default_store_path(config.repo)).upsert_task_metadata(
        item_id=created.id,
        source="human-requested",
        depth=0,
        root_item_id=created.id,
        role="orchestrator",
        settings=normalize_codex_settings(settings),
    )
    return created


def _config_with_codex_settings(config: Any, settings: dict[str, Any] | None) -> Any:
    normalized = normalize_codex_settings(settings)
    updated = config.model_copy(deep=True)
    updated.codex.runner = "app-server"
    if updated.codex.command == "codex exec":
        updated.codex.command = "codex app-server"
    approval = normalized.get("approval_policy")
    if isinstance(approval, str) and approval.strip():
        updated.codex.approval_policy = approval.strip()
    sandbox = normalized.get("sandbox_mode")
    if isinstance(sandbox, str) and sandbox.strip():
        updated.codex.sandbox_mode = sandbox.strip()
    timeout_seconds = normalized.get("job_timeout_seconds")
    if isinstance(timeout_seconds, int) and timeout_seconds > 0:
        updated.codex.turn_timeout_ms = timeout_seconds * 1000
    max_agents = normalized.get("max_parallel_agents")
    if isinstance(max_agents, int) and max_agents > 0:
        updated.agent.max_concurrent_agents = max_agents
    model = normalized.get("default_model")
    if isinstance(model, str) and model.strip():
        updated.codex.model = model.strip()
    reasoning = normalized.get("reasoning_effort")
    if isinstance(reasoning, str) and reasoning.strip():
        updated.codex.reasoning_effort = reasoning.strip()
    return updated


def _codex_command_with_model(command: str, model: str) -> str:
    parts = command.split()
    if any(part in {"--model", "-m"} or part.startswith("--model=") for part in parts):
        return command
    if parts[:2] == ["codex", "exec"]:
        return f"{command} --model {model}"
    return command


def _settings_value(settings: dict[str, Any] | None, key: str) -> object:
    return normalize_codex_settings(settings).get(key, DEFAULT_CODEX_SETTINGS[key])


def _int_setting(settings: dict[str, Any] | None, key: str) -> int:
    value = _settings_value(settings, key)
    return value if isinstance(value, int) else int(DEFAULT_CODEX_SETTINGS[key])


def _goal_title_and_description(goal: str) -> tuple[str, str]:
    clean = " ".join(goal.strip().split())
    first_sentence = clean.split(".", 1)[0].strip()
    title = first_sentence or clean
    if len(title) > 80:
        title = title[:77].rstrip() + "..."
    return title, goal.strip()


def _codex_settings_from_work_item(item: WorkItem) -> dict[str, Any]:
    description = item.description or ""
    marker = "codex-fleet task settings"
    if marker not in description:
        return {}
    import html
    import re

    match = re.search(r"\{\s*&quot;default_model&quot;.*?\}", description, flags=re.DOTALL)
    raw = match.group(0) if match else ""
    if not raw:
        match = re.search(r"\{\s*\"default_model\".*?\}", description, flags=re.DOTALL)
        raw = match.group(0) if match else ""
    if not raw:
        return {}
    try:
        parsed = json.loads(html.unescape(raw))
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    settings = {key: value for key, value in parsed.items() if key != "skills"}
    if settings.get("workflow_mode") == "project-default":
        settings.pop("workflow_mode", None)
    return settings


def _codex_settings_payload(payload: dict[str, Any]) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    raw = payload.get("codex_settings")
    if isinstance(raw, dict):
        settings.update(raw)
    for key in (
        "runner_mode",
        "default_model",
        "reasoning_effort",
        "approval_policy",
        "sandbox_mode",
        "workflow_mode",
        "agent_role",
        "skill_policy",
    ):
        value = payload.get(key)
        if isinstance(value, str):
            settings[key] = value
    for key in (
        "max_parallel_agents",
        "max_depth",
        "max_child_tasks_per_run",
        "max_total_agent_created_tasks_per_parent",
        "job_timeout_seconds",
        "max_prompt_protocol_tokens",
        "max_plane_comment_chars",
    ):
        value = payload.get(key)
        if isinstance(value, int):
            settings[key] = value
    subagents = payload.get("subagents")
    if isinstance(subagents, dict):
        settings["subagents"] = subagents
    return settings


def _update_work_item_settings(repo: Path, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = load_config(repo)
    tracker = _build_local_api_tracker(config)
    items = tracker.fetch_items_by_ids([item_id])
    if not items:
        raise ValueError(f"Work item not found: {item_id}")
    store = RunStore(default_store_path(config.repo))
    existing = store.get_task_metadata(item_id)
    settings = normalize_codex_settings({**(existing.settings if existing is not None else {}), **_codex_settings_payload(payload)})
    role = str(settings.get("agent_role") or payload.get("role") or (existing.role if existing is not None else "implementer"))
    depends_on = payload.get("depends_on")
    dependency_ids = tuple(str(item) for item in depends_on if isinstance(item, str)) if isinstance(depends_on, list) else (
        existing.depends_on if existing is not None else ()
    )
    store.upsert_task_metadata(
        item_id=item_id,
        source=existing.source if existing is not None else "human-settings",
        depth=existing.depth if existing is not None else 0,
        parent_item_id=existing.parent_item_id if existing is not None else None,
        parent_identifier=existing.parent_identifier if existing is not None else None,
        parent_run_id=existing.parent_run_id if existing is not None else None,
        created_by_run_id=existing.created_by_run_id if existing is not None else None,
        root_item_id=existing.root_item_id if existing is not None else item_id,
        role=role,
        depends_on=dependency_ids,
        generation=existing.generation if existing is not None else 0,
        approval_mode=str(settings.get("workflow_mode") or "plan_execute"),
        terminal_outcome=existing.terminal_outcome if existing is not None else None,
        settings=settings,
    )
    metadata = store.get_task_metadata(item_id)
    if metadata is None:
        raise ValueError("Could not persist work item settings.")
    tracker.create_comment(item_id, "codex-fleet task settings were updated.")
    return {"ok": True, "settings": settings, "metadata": _task_metadata_payload(metadata)}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _create_local_work_item(repo: Path, *, title: str, description: str | None) -> WorkItem:
    return LocalWorkItemStore(default_local_work_item_store_path(repo)).create_item(
        title=title,
        description=description,
    )


def _build_local_api_tracker(config: Any) -> Tracker:
    if config.tracker.kind == "memory":
        return LocalWorkItemTracker(
            LocalWorkItemStore(default_local_work_item_store_path(config.repo)),
            active_states=config.tracker.active_states,
        )
    return build_tracker(config)


def _bool_payload(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key, default)
    return value if isinstance(value, bool) else default


def _int_query(value: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return min(max(parsed, minimum), maximum)


class _SingleItemTracker(Tracker):
    def __init__(self, tracker: Tracker, *, item_id: str, active_states: list[str]) -> None:
        self.tracker = tracker
        self.item_id = item_id
        self.active_states = {state.lower() for state in active_states}

    def fetch_candidate_items(self) -> list[WorkItem]:
        items = self.tracker.fetch_items_by_ids([self.item_id])
        return [item for item in items if item.state.lower() in self.active_states]

    def fetch_items_by_ids(self, ids: list[str]) -> list[WorkItem]:
        return self.tracker.fetch_items_by_ids(ids)

    def update_item_state(self, item_id: str, state: str) -> None:
        self.tracker.update_item_state(item_id, state)

    def create_comment(self, item_id: str, body: str) -> None:
        self.tracker.create_comment(item_id, body)

    def create_work_item(
        self,
        *,
        title: str,
        description: str | None,
        state: str,
        labels: tuple[str, ...] = (),
    ) -> WorkItem | None:
        return self.tracker.create_work_item(title=title, description=description, state=state, labels=labels)
