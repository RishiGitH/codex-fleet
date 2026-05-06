from __future__ import annotations

import json
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
from codex_fleet.models import WorkItem, WorkItemState
from codex_fleet.orchestrator import Orchestrator
from codex_fleet.plane import PlaneClient, PlaneSettings, plane_project_external_id
from codex_fleet.plane_bootstrap import ensure_plane_labels, ensure_plane_states
from codex_fleet.plane_local_bootstrap import PlaneLocalBootstrapError, create_local_plane_session
from codex_fleet.project_registry import (
    DEFAULT_CODEX_SETTINGS,
    LocalProject,
    ProjectRegistry,
    ProjectRegistryError,
    default_project_registry_path,
    discover_git_root,
    normalize_codex_settings,
)
from codex_fleet.store import RunStore, StoredArtifact, StoredEvent, StoredRun
from codex_fleet.tracker import Tracker

DEFAULT_LOCAL_API_HOST = "127.0.0.1"
DEFAULT_LOCAL_API_PORT = 8790
STARTER_PROJECT_TYPES = {"blank", "simple-web", "node-next", "python"}
LOGIN_NONCE_TTL_SECONDS = 120
SESSION_CODE_TTL_SECONDS = 120


class LocalApiError(RuntimeError):
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
                    "repo": str(self.server.repo),
                    "projects": len(self.server.registry.list_projects()),
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
                    "apiUrl": f"http://{self.server.server_address[0]}:{self.server.server_address[1]}",
                    "token": self.server.token,
                }
            )
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
                api_url=f"http://{self.server.server_address[0]}:{self.server.server_address[1]}",
                code=session_code,
            )
            self._send_plane_login_redirect(redirect, session.session_key)
            return
        if path == "/api/projects":
            if not self._authorized():
                self._send_auth_missing()
                return
            self._send_json({"projects": [_project_payload(project) for project in self.server.registry.list_projects()]})
            return
        if path.startswith("/api/projects/") and path.endswith("/settings"):
            if not self._authorized():
                self._send_auth_missing()
                return
            project_id = path.removeprefix("/api/projects/").removesuffix("/settings").strip("/")
            project = _project_from_path_ref(self.server, project_id)
            if project is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Project not found.")
                return
            self._send_json({"settings": project.codex_settings, "project": _project_payload(project)})
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
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"items": [_work_item_payload(item) for item in items]})
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
        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
        path = urlparse(self.path).path
        if not self._authorized():
            self._send_auth_missing()
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
            start_initial_goal = _bool_payload(payload, "start_initial_goal", default=True)
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
        if path.startswith("/api/projects/") and path.endswith("/settings"):
            payload = self._read_json()
            project_id = path.removeprefix("/api/projects/").removesuffix("/settings").strip("/")
            try:
                current = _project_from_path_ref(self.server, project_id)
                if current is None:
                    raise ProjectRegistryError("Project not found.")
                project = self.server.registry.update_project_settings(current.id, _codex_settings_payload(payload))
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
            except (ProjectRegistryError, ValueError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(body)

    def _send_auth_missing(self) -> None:
        self._send_error(HTTPStatus.UNAUTHORIZED, "Local launcher connection required.", code="auth_missing")

    def _send_error(self, status: HTTPStatus, message: str, *, code: str | None = None) -> None:
        payload: dict[str, Any] = {"ok": False, "error": message}
        if code:
            payload["code"] = code
        self._send_json(payload, status=status)

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


def _safe_loopback_origin(origin: str) -> bool:
    if not origin:
        return False
    parsed = urlparse(origin)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _ensure_plane_project_mapping(server: LocalApiServer, project: LocalProject) -> tuple[LocalProject, dict[str, Any]]:
    try:
        control_config = load_config(server.repo)
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
    slug = _starter_slug(name_value)
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
    return {
        "id": run.id,
        "item_id": run.item_id,
        "identifier": run.identifier,
        "status": run.status,
        "branch_name": run.branch_name,
        "worktree_path": run.worktree_path,
        "error": run.error,
    }


def _run_detail_payload(store: RunStore, run: StoredRun) -> dict[str, Any]:
    payload = _run_payload(run)
    payload["events"] = [_event_payload(event) for event in store.list_events(run.id)]
    payload["artifacts"] = [_artifact_payload(artifact) for artifact in store.list_artifacts(run.id)]
    return payload


def _event_payload(event: StoredEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "kind": event.kind,
        "payload": event.payload,
        "created_at": event.created_at,
    }


def _artifact_payload(artifact: StoredArtifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "path": artifact.path,
        "kind": artifact.kind,
        "created_at": artifact.created_at,
    }


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
            raise ProjectRegistryError("Plane project is not registered with codex-fleet.")
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
        raise ProjectRegistryError("Plane project is not registered with codex-fleet.")
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
    return server.registry.get_project_by_plane_id(workspace_slug=workspace_slug, plane_project_id=value)


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
        ),
        agent_task_settings_resolver=lambda item: (
            str(settings_value(settings_for_item(item), "agent_task_mode")),
            int(settings_value(settings_for_item(item), "max_task_depth")),
        ),
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
    merged_settings = normalize_codex_settings({**(settings or {}), **_codex_settings_from_work_item(items[0])})
    config = _config_with_codex_settings(base_config, merged_settings)
    scoped_tracker = _SingleItemTracker(
        tracker,
        item_id=item_id,
        active_states=config.tracker.active_states,
    )
    store = RunStore(default_store_path(config.repo))
    result = Orchestrator(
        config=config,
        tracker=scoped_tracker,
        runner=build_runner(config, fake=fake, fake_succeed=fake_succeed),
        store=store,
        agent_task_mode=str(_settings_value(merged_settings, "agent_task_mode")),
        max_task_depth=int(_settings_value(merged_settings, "max_task_depth")),
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
                error=result.run.error,
            ),
        )
    return payload


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
        created = tracker.create_work_item(
            title=title.strip(),
            description=description,
            state=WorkItemState.READY.value,
            labels=("human-requested",),
        )
        if created is None:
            raise ValueError("codex-fleet could not create the work item.")
    RunStore(default_store_path(config.repo)).upsert_task_metadata(
        item_id=created.id,
        source="human-requested",
        depth=0,
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
        created = tracker.create_work_item(
            title=title,
            description=description,
            state=state,
            labels=("human-requested",),
        )
        if created is None:
            raise ValueError("codex-fleet could not create the initial goal work item.")
    RunStore(default_store_path(config.repo)).upsert_task_metadata(
        item_id=created.id,
        source="human-requested",
        depth=0,
        settings=normalize_codex_settings(settings),
    )
    return created


def _config_with_codex_settings(config: Any, settings: dict[str, Any] | None) -> Any:
    normalized = normalize_codex_settings(settings)
    updated = config.model_copy(deep=True)
    runner_mode = str(normalized.get("runner_mode") or "codex")
    if runner_mode in {"codex", "cli"}:
        updated.codex.runner = "cli"
    elif runner_mode == "app-server":
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
    if updated.codex.runner == "cli" and isinstance(model, str) and model.strip():
        updated.codex.command = _codex_command_with_model(updated.codex.command, model.strip())
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
    if settings.get("agent_task_mode") == "project-default":
        settings.pop("agent_task_mode", None)
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
        "agent_task_mode",
    ):
        value = payload.get(key)
        if isinstance(value, str):
            settings[key] = value
    for key in ("max_parallel_agents", "max_task_depth", "job_timeout_seconds"):
        value = payload.get(key)
        if isinstance(value, int):
            settings[key] = value
    subagents = payload.get("subagents")
    if isinstance(subagents, dict):
        settings["subagents"] = subagents
    return settings


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
