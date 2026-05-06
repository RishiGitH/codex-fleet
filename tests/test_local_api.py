from __future__ import annotations

import http.client
import json
import subprocess
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import pytest

import codex_fleet.local_api as local_api
from codex_fleet.config import FleetConfig, TrackerConfig
from codex_fleet.factory import default_store_path
from codex_fleet.local_api import (
    build_onboarding_url,
    build_plane_login_url,
    create_local_api_server,
)
from codex_fleet.local_work_items import LocalWorkItemStore, default_local_work_item_store_path
from codex_fleet.models import ProposedTask, RunResult, WorkItem
from codex_fleet.plane import PlaneSettings
from codex_fleet.plane_local_bootstrap import PlaneLocalSessionResult
from codex_fleet.runner import Runner
from codex_fleet.store import RunStore


def test_local_api_rejects_non_loopback_by_default(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="loopback"):
        create_local_api_server(tmp_path, host="0.0.0.0")


def test_local_api_status_and_project_registration(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    project_dir.mkdir()
    _init_git_repo(project_dir)
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        status = _json_request(f"{base_url}/api/status")
        health = _json_request(f"{base_url}/health")
        assert status["service"] == "codex-fleet-api"
        assert health["ok"] is True
        assert server.token not in json.dumps(status)

        with pytest.raises(HTTPError) as unauthorized:
            _json_request(f"{base_url}/api/projects")
        assert unauthorized.value.code == 401
        body = json.loads(unauthorized.value.read().decode("utf-8"))
        assert body["code"] == "auth_missing"
        assert "token" not in body["error"].lower()

        created = _json_request(
            f"{base_url}/api/projects",
            method="POST",
            token=server.token,
            payload={"path": str(project_dir)},
        )

        assert created["project"]["name"] == "sample"
        projects = _json_request(f"{base_url}/api/projects", token=server.token)
        project = _json_request(f"{base_url}/api/projects/{created['project']['id']}", token=server.token)
        assert projects["projects"][0]["repo_path"] == str(project_dir.resolve())
        assert project["project"]["repo_path"] == str(project_dir.resolve())
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_session_requires_auth_without_leaking_token(tmp_path: Path) -> None:
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as unauthorized:
            _json_request(f"{base_url}/api/session")
        body = json.loads(unauthorized.value.read().decode("utf-8"))

        session = _json_request(f"{base_url}/api/session", token=server.token)

        assert unauthorized.value.code == 401
        assert body["code"] == "auth_missing"
        assert session["connected"] is True
        assert session["service"] == "codex-fleet-api"
        assert server.token not in json.dumps(session)
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_session_requires_auth_and_reports_connection(tmp_path: Path) -> None:
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as unauthorized:
            _json_request(f"{base_url}/api/session")
        assert unauthorized.value.code == 401
        body = json.loads(unauthorized.value.read().decode("utf-8"))
        assert body["code"] == "auth_missing"

        session = _json_request(f"{base_url}/api/session", token=server.token)

        assert session["ok"] is True
        assert session["connected"] is True
        assert session["service"] == "codex-fleet-api"
        assert session["repo"] == str(tmp_path.resolve())
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_options_allows_private_network_preflight(tmp_path: Path) -> None:
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(
            f"{base_url}/api/folders/pick",
            method="OPTIONS",
            headers={
                "Origin": "http://127.0.0.1:8080",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        with urlopen(request) as response:
            assert response.headers["Access-Control-Allow-Private-Network"] == "true"
            assert "X-Codex-Fleet-Token" in response.headers["Access-Control-Allow-Headers"]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_project_registration_can_apply_harness(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    project_dir.mkdir()
    _init_git_repo(project_dir)
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        created = _json_request(
            f"{base_url}/api/projects",
            method="POST",
            token=server.token,
            payload={"path": str(project_dir), "apply_harness": True},
        )

        assert created["project"]["harness_status"] == "warnings"
        assert created["harness"]["status"] == "warnings"
        assert "AGENTS.md" in (project_dir / "AGENTS.md").read_text()
        assert "agent-proposed" in (project_dir / "WORKFLOW.md").read_text()
        assert any(path.endswith("AGENTS.md") for path in created["written"])
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_rejects_linked_folder_that_is_not_git_repo(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    project_dir.mkdir()
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as error:
            _json_request(
                f"{base_url}/api/projects",
                method="POST",
                token=server.token,
                payload={"path": str(project_dir), "initial_goal": "Build something"},
            )

        assert error.value.code == 400
        body = error.value.read().decode("utf-8")
        assert "Choose a git repository folder" in body
        assert "cannot run from a non-git folder" in body
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_project_registration_can_save_settings_and_create_initial_goal(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    project_dir.mkdir()
    _init_git_repo(project_dir)
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        created = _json_request(
            f"{base_url}/api/projects",
            method="POST",
            token=server.token,
            payload={
                "path": str(project_dir),
                "initial_goal": "Build a polished landing page. Include pricing.",
                "start_initial_goal": True,
                "codex_settings": {
                    "default_model": "gpt-5.4-mini",
                    "reasoning_effort": "high",
                    "agent_task_mode": "agent_task_planner",
                },
            },
        )

        settings = _json_request(f"{base_url}/api/projects/{created['project']['id']}/settings", token=server.token)

        assert created["project"]["codex_settings"]["default_model"] == "gpt-5.4-mini"
        assert created["initial_item"]["title"] == "Build a polished landing page"
        assert created["initial_item"]["state"] == "Ready"
        assert any("Initial work item created in Ready" in entry for entry in created["setup_log"])
        assert settings["settings"]["agent_task_mode"] == "agent_task_planner"
        metadata = RunStore(default_store_path(project_dir)).get_task_metadata(created["initial_item"]["id"])
        assert metadata is not None
        assert metadata.source == "human-requested"
        assert metadata.settings["default_model"] == "gpt-5.4-mini"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_extracts_codex_task_settings_from_work_item_description() -> None:
    item = WorkItem(
        id="item-1",
        identifier="CF-1",
        title="Task",
        description=(
            '<p>Build it.</p><details data-codex-fleet-task-settings="true">'
            "<summary>codex-fleet task settings</summary><pre>{"
            "&quot;default_model&quot;: &quot;gpt-5.4-mini&quot;, "
            "&quot;reasoning_effort&quot;: &quot;high&quot;, "
            "&quot;agent_task_mode&quot;: &quot;manual&quot;"
            "}</pre></details>"
        ),
        state="Ready",
    )

    settings = local_api._codex_settings_from_work_item(item)

    assert settings == {
        "default_model": "gpt-5.4-mini",
        "reasoning_effort": "high",
        "agent_task_mode": "manual",
    }


def test_local_api_folder_picker_requires_auth_and_returns_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()

    class Picked:
        path = selected
        name = "selected"

    monkeypatch.setattr(local_api, "pick_folder", lambda: Picked())
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as unauthorized:
            _json_request(f"{base_url}/api/folders/pick", method="POST")
        assert unauthorized.value.code == 401
        body = json.loads(unauthorized.value.read().decode("utf-8"))
        assert body["code"] == "auth_missing"

        picked = _json_request(f"{base_url}/api/folders/pick", method="POST", token=server.token, payload={})

        assert picked == {"name": "selected", "path": str(selected)}
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_folder_picker_reports_picker_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_api, "pick_folder", lambda: (_ for _ in ()).throw(local_api.FolderPickerError("cancelled")))
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as failure:
            _json_request(f"{base_url}/api/folders/pick", method="POST", token=server.token, payload={})

        assert failure.value.code == 400
        body = json.loads(failure.value.read().decode("utf-8"))
        assert body["code"] == "picker_cancelled"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_project_registration_links_plane_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_dir = tmp_path / "sample"
    project_dir.mkdir()
    _init_git_repo(project_dir)
    writes: list[dict[str, object]] = []

    class FakePlaneClient:
        settings = PlaneSettings(
            base_url="http://127.0.0.1:8080",
            api_key="local-plane-token",
            workspace_slug="codex-local",
            project_id="control-project-id",
        )

        def ensure_project(self, *, name: str, identifier_seed: str, external_id: str) -> dict[str, str]:
            assert name == "sample"
            assert identifier_seed == "sample"
            assert external_id == str(project_dir.resolve())
            return {"id": "plane-project-id"}

        def list_labels(self) -> list[dict[str, str]]:
            return []

        def create_label(self, name: str, color: str) -> dict[str, str]:
            return {"name": name, "color": color}

    class FakeStateResult:
        created_states = ("Ready", "Running")

    monkeypatch.setattr(
        local_api,
        "load_config",
        lambda repo: FleetConfig(
            repo=tmp_path,
            tracker=TrackerConfig(
                kind="plane",
                plane_base_url="http://127.0.0.1:8080",
                plane_api_key="local-plane-token",
                plane_workspace_slug="codex-local",
                plane_project_id="control-project-id",
            ),
        ).resolved(),
    )
    monkeypatch.setattr(local_api, "build_plane_client", lambda config: FakePlaneClient())
    monkeypatch.setattr(local_api, "ensure_plane_states", lambda client, active_states: FakeStateResult())
    monkeypatch.setattr(local_api, "ensure_plane_labels", lambda client: ("human-requested", "agent-proposed", "agent-followup"))
    monkeypatch.setattr(local_api, "write_plane_tracker_config", lambda repo, **kwargs: writes.append({"repo": repo, **kwargs}))

    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        created = _json_request(
            f"{base_url}/api/projects",
            method="POST",
            token=server.token,
            payload={"path": str(project_dir)},
        )

        assert created["plane"]["status"] == "linked"
        assert created["plane"]["project_id"] == "plane-project-id"
        assert set(created["plane"]["created_labels"]) == {"human-requested", "agent-proposed", "agent-followup"}
        assert created["project"]["plane_workspace_slug"] == "codex-local"
        assert created["project"]["plane_project_id"] == "plane-project-id"
        assert writes == [
            {
                "repo": project_dir.resolve(),
                "base_url": "http://127.0.0.1:8080",
                "workspace_slug": "codex-local",
                "project_id": "plane-project-id",
                "api_key_value": "local-plane-token",
            }
        ]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_exposes_stored_runs(tmp_path: Path) -> None:
    store = RunStore(default_store_path(tmp_path))
    store.upsert_run(
        run_id="run-1",
        item_id="item-1",
        identifier="CF-1",
        status="human_review",
        branch_name="codex-fleet/CF-1",
        worktree_path="/tmp/worktree",
    )
    store.add_event("run-1", "completed", {"state": "Human Review"})
    store.add_artifact("run-1", "/tmp/worktree/.codex-fleet-fake-run.txt")
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        runs = _json_request(f"{base_url}/api/runs", token=server.token)
        run = _json_request(f"{base_url}/api/runs/run-1", token=server.token)
        status = _json_request(f"{base_url}/api/work-items/item-1/run-status", token=server.token)
        events = _json_request(f"{base_url}/api/events?limit=1", token=server.token)

        assert runs["runs"][0]["id"] == "run-1"
        assert run["run"]["status"] == "human_review"
        assert run["run"]["events"][0]["kind"] == "completed"
        assert run["run"]["events"][0]["run_id"] == "run-1"
        assert run["run"]["artifacts"][0]["path"].endswith(".codex-fleet-fake-run.txt")
        assert status["run"]["id"] == "run-1"
        assert status["run"]["events"][0]["payload"] == {"state": "Human Review"}
        assert events["events"][0]["run_id"] == "run-1"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_runs_specific_memory_work_item(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = _json_request(
            f"{base_url}/api/work-items/memory-1/run",
            method="POST",
            token=server.token,
            payload={"fake": True},
        )
        run = _json_request(f"{base_url}/api/runs/{result['run']['id']}", token=server.token)

        assert result["dispatched"] is True
        assert result["run"]["status"] == "human_review"
        assert any(event["kind"] == "completed" for event in result["run"]["events"])
        assert result["run"]["artifacts"]
        assert run["run"]["item_id"] == "memory-1"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_lists_and_runs_next_ready_item(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        ready = _json_request(f"{base_url}/api/work-items/ready", token=server.token)
        result = _json_request(
            f"{base_url}/api/work-items/next/run",
            method="POST",
            token=server.token,
            payload={"fake": True},
        )

        assert ready["items"][0]["identifier"] == "CF-1"
        assert result["dispatched"] is True
        assert result["run"]["identifier"] == "CF-1"
        assert result["run"]["status"] == "human_review"
        assert result["run"]["worktree_path"]

        ready_after_run = _json_request(f"{base_url}/api/work-items/ready", token=server.token)
        assert ready_after_run["items"] == []
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_next_ready_uses_work_item_codex_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_git_repo(tmp_path)
    local_store = LocalWorkItemStore(default_local_work_item_store_path(tmp_path))
    local_store.update_item_state("memory-1", "Done")
    monkeypatch.setattr(local_api, "build_runner", lambda *args, **kwargs: _ProposingRunner())
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    description = (
        '<p>Do the first step.</p><details data-codex-fleet-task-settings="true">'
        "<summary>codex-fleet task settings</summary><pre>"
        "{&quot;default_model&quot;: &quot;gpt-5.5&quot;, &quot;reasoning_effort&quot;: &quot;low&quot;, "
        "&quot;agent_task_mode&quot;: &quot;manual&quot;}"
        "</pre></details>"
    )
    try:
        _json_request(
            f"{base_url}/api/work-items",
            method="POST",
            token=server.token,
            payload={"title": "Task with manual follow-ups", "description": description},
        )
        result = _json_request(
            f"{base_url}/api/runs/next-ready",
            method="POST",
            token=server.token,
            payload={},
        )
        ready_after_run = _json_request(f"{base_url}/api/work-items/ready", token=server.token)

        assert result["dispatched"] is True
        assert result["run"]["status"] == "human_review"
        assert ready_after_run["items"] == []
        events = RunStore(default_store_path(tmp_path)).list_events(result["run"]["id"])
        assert "proposed_tasks_skipped" in [event.kind for event in events]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_product_run_aliases_use_orchestrator_path(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        first = _json_request(
            f"{base_url}/api/runs",
            method="POST",
            token=server.token,
            payload={"plane_work_item_id": "memory-1", "fake": True},
        )
        second = _json_request(
            f"{base_url}/api/runs/next-ready",
            method="POST",
            token=server.token,
            payload={"fake": True},
        )

        assert first["dispatched"] is True
        assert first["run"]["item_id"] == "memory-1"
        assert first["run"]["status"] == "human_review"
        assert second["dispatched"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_can_target_registered_project_for_ready_items_and_runs(tmp_path: Path) -> None:
    project_dir = tmp_path / "app"
    project_dir.mkdir()
    _init_git_repo(project_dir)
    server = create_local_api_server(tmp_path, port=0)
    project = server.registry.add_project(project_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        ready = _json_request(f"{base_url}/api/work-items/ready?project_id={project.id}", token=server.token)
        result = _json_request(
            f"{base_url}/api/runs/next-ready",
            method="POST",
            token=server.token,
            payload={"project_id": project.id, "fake": True},
        )
        project_runs = _json_request(f"{base_url}/api/runs?project_id={project.id}", token=server.token)
        root_runs = _json_request(f"{base_url}/api/runs", token=server.token)

        assert ready["items"][0]["identifier"] == "CF-1"
        assert result["dispatched"] is True
        assert result["run"]["status"] == "human_review"
        assert result["run"]["worktree_path"].startswith(str(project_dir / ".codex-fleet" / "workspaces"))
        assert project_runs["runs"][0]["id"] == result["run"]["id"]
        assert root_runs["runs"] == []
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_can_target_registered_project_by_plane_project_id(tmp_path: Path) -> None:
    project_dir = tmp_path / "app"
    project_dir.mkdir()
    _init_git_repo(project_dir)
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  plane_base_url: http://127.0.0.1:8080\n"
        "  plane_api_key: local-key\n"
        "  plane_workspace_slug: codex-local\n"
        "  plane_project_id: control-plane-project\n"
    )
    server = create_local_api_server(tmp_path, port=0)
    project = server.registry.add_project(project_dir)
    server.registry.update_plane_mapping(
        project.id,
        workspace_slug="codex-local",
        project_id_in_plane="mapped-plane-project",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        ready = _json_request(
            f"{base_url}/api/work-items/ready?plane_project_id=mapped-plane-project",
            token=server.token,
        )
        result = _json_request(
            f"{base_url}/api/runs/next-ready",
            method="POST",
            token=server.token,
            payload={"plane_project_id": "mapped-plane-project", "fake": True},
        )

        assert ready["items"][0]["identifier"] == "CF-1"
        assert result["dispatched"] is True
        assert result["run"]["worktree_path"].startswith(str(project_dir / ".codex-fleet" / "workspaces"))
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_can_run_next_ready_item_to_rework(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = _json_request(
            f"{base_url}/api/work-items/next/run",
            method="POST",
            token=server.token,
            payload={"fake": True, "fake_succeed": False},
        )
        run = _json_request(f"{base_url}/api/runs/{result['run']['id']}", token=server.token)

        assert result["dispatched"] is True
        assert result["run"]["status"] == "failed"
        assert result["run"]["error"] == "configured failure"
        failed = [event for event in run["run"]["events"] if event["kind"] == "failed"]
        assert failed[-1]["payload"]["state"] == "Rework"

        ready_after_run = _json_request(f"{base_url}/api/work-items/ready", token=server.token)
        assert ready_after_run["items"] == []
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_creates_persistent_local_work_item(tmp_path: Path) -> None:
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        created = _json_request(
            f"{base_url}/api/work-items",
            method="POST",
            token=server.token,
            payload={"title": "Build pricing page", "description": "Use existing app shell."},
        )
        ready = _json_request(f"{base_url}/api/work-items/ready", token=server.token)

        assert created["item"]["identifier"] == "CF-2"
        assert created["item"]["title"] == "Build pricing page"
        assert [item["identifier"] for item in ready["items"]] == ["CF-1", "CF-2"]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_creates_plane_work_item_with_human_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path.joinpath(".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  plane_base_url: http://127.0.0.1:8080\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: codex-fleet\n"
        "  plane_project_id: project-id\n"
    )
    created: list[dict[str, object]] = []

    class FakeTracker:
        def create_work_item(self, *, title, description, state, labels):  # type: ignore[no-untyped-def]
            created.append({"title": title, "description": description, "state": state, "labels": labels})
            return WorkItem(
                id="plane-1",
                identifier="CF-1",
                title=title,
                description=description,
                state=state,
                labels=labels,
            )

    monkeypatch.setattr(local_api, "build_tracker", lambda _config: FakeTracker())
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        response = _json_request(
            f"{base_url}/api/work-items",
            method="POST",
            token=server.token,
            payload={"title": "Build onboarding", "description": "Make it clear."},
        )

        assert response["item"]["labels"] == ["human-requested"]
        assert created == [
            {
                "title": "Build onboarding",
                "description": "Make it clear.",
                "state": "Ready",
                "labels": ("human-requested",),
            }
        ]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_creates_new_starter_project(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        created = _json_request(
            f"{base_url}/api/projects",
            method="POST",
            token=server.token,
            payload={
                "create_new": True,
                "name": "Demo Web",
                "location": str(parent),
                "project_type": "simple-web",
                "apply_harness": True,
            },
        )
        project_path = Path(created["project"]["repo_path"])

        assert project_path == parent / "Demo-Web"
        assert (project_path / "index.html").exists()
        assert (project_path / ".git").exists()
        subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=project_path, check=True, capture_output=True)
        assert (project_path / "AGENTS.md").exists()
        assert created["harness"]["status"] in {"ready", "warnings"}
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_plans_and_applies_project_harness(tmp_path: Path) -> None:
    project_dir = tmp_path / "app"
    project_dir.mkdir()
    server = create_local_api_server(tmp_path, port=0)
    project = server.registry.add_project(project_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        planned = _json_request(
            f"{base_url}/api/projects/{project.id}/harness/plan",
            method="POST",
            token=server.token,
            payload={},
        )
        applied = _json_request(
            f"{base_url}/api/projects/{project.id}/harness/apply",
            method="POST",
            token=server.token,
            payload={},
        )

        assert "AGENTS.md" in planned["harness"]["missing"]
        assert planned["harness"]["status"] == "blocked"
        assert planned["harness"]["scan"]["git_root"] is None
        assert "not a git repository" in planned["harness"]["scan"]["warnings"]
        assert str(project_dir / "AGENTS.md") in applied["written"]
        assert applied["harness"]["status"] == "blocked"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_api_bootstraps_local_onboarding_project(tmp_path: Path) -> None:
    project_dir = tmp_path / "bootstrap-app"
    project_dir.mkdir()
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = _json_request(
            f"{base_url}/api/onboarding/local-bootstrap",
            method="POST",
            token=server.token,
            payload={"path": str(project_dir)},
        )

        assert result["ok"] is True
        assert result["project"]["name"] == "bootstrap-app"
        assert result["harness"]["status"] == "blocked"
        assert "not a git repository" in result["harness"]["scan"]["warnings"]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_onboarding_url_places_local_token_in_fragment(tmp_path: Path) -> None:
    project_dir = tmp_path / "app"
    project_dir.mkdir()

    url = build_onboarding_url(
        tmp_path,
        plane_url="http://127.0.0.1:3000/",
        project_path=project_dir,
        api_url="http://127.0.0.1:8790",
    )
    parsed = urlparse(url)
    fragment = parse_qs(parsed.fragment)

    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:3000"
    assert parsed.path == "/codex-fleet/onboarding"
    assert "token" not in parse_qs(parsed.query)
    assert fragment["apiUrl"] == ["http://127.0.0.1:8790"]
    assert fragment["path"] == [str(project_dir.resolve())]
    assert fragment["token"][0]


def test_plane_login_url_uses_nonce_without_long_lived_token(tmp_path: Path) -> None:
    token = local_api.load_or_create_local_api_token(tmp_path)
    url = build_plane_login_url(
        tmp_path,
        api_url="http://127.0.0.1:8790",
        plane_url="http://127.0.0.1:8080",
        redirect_path="codex-fleet/projects/project-id/issues/",
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    redirect = urlparse(query["redirect"][0])
    redirect_fragment = parse_qs(redirect.fragment)

    assert parsed.path == "/api/plane/login"
    assert query["nonce"][0]
    assert "token" not in query
    assert token not in url
    assert query["planeOrigin"] == ["http://127.0.0.1:8080"]
    assert redirect.scheme == "http"
    assert redirect.netloc == "127.0.0.1:8080"
    assert redirect.path == "/codex-fleet/projects/project-id/issues/"
    assert redirect_fragment["apiUrl"] == ["http://127.0.0.1:8790"]
    assert "token" not in redirect_fragment


def test_plane_login_endpoint_sets_plane_session_cookie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_api,
        "create_local_plane_session",
        lambda _repo: PlaneLocalSessionResult(session_key="session-secret", user_email="codex-fleet-local@example.local"),
    )
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        nonce = local_api.create_one_time_local_api_code(tmp_path, kind="login", ttl_seconds=60)
        query = urlencode(
            {
                "nonce": nonce,
                "redirect": "http://127.0.0.1:8080/codex-fleet/",
                "planeOrigin": "http://127.0.0.1:8080",
            }
        )
        path = (
            f"/api/plane/login?{query}"
        )
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        conn.request("GET", path)
        response = conn.getresponse()

        assert response.status == 302
        location = response.getheader("Location") or ""
        parsed_location = urlparse(location)
        fragment = parse_qs(parsed_location.fragment)
        assert parsed_location.scheme == "http"
        assert parsed_location.netloc == "127.0.0.1:8080"
        assert parsed_location.path == "/codex-fleet/"
        assert fragment["apiUrl"] == [f"http://127.0.0.1:{server.server_port}"]
        assert fragment["code"][0]
        cookies = response.headers.get_all("Set-Cookie")
        assert any(cookie.startswith("session-id=session-secret;") for cookie in cookies)
        assert any(cookie.startswith("sessionid=session-secret;") for cookie in cookies)

        exchanged = _json_request(f"http://127.0.0.1:{server.server_port}/api/session/exchange?code={fragment['code'][0]}")
        assert exchanged["token"] == server.token
        with pytest.raises(HTTPError) as reused:
            _json_request(f"http://127.0.0.1:{server.server_port}/api/session/exchange?code={fragment['code'][0]}")
        assert reused.value.code == 401
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_plane_login_rejects_reused_nonce_and_wrong_redirect_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_api,
        "create_local_plane_session",
        lambda _repo: PlaneLocalSessionResult(session_key="session-secret", user_email="codex-fleet-local@example.local"),
    )
    server = create_local_api_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        nonce = local_api.create_one_time_local_api_code(tmp_path, kind="login", ttl_seconds=60)
        query = urlencode(
            {
                "nonce": nonce,
                "redirect": "http://127.0.0.1:8080/codex-fleet/",
                "planeOrigin": "http://127.0.0.1:8080",
            }
        )
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        conn.request("GET", f"/api/plane/login?{query}")
        assert conn.getresponse().status == 302

        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        conn.request("GET", f"/api/plane/login?{query}")
        assert conn.getresponse().status == 401

        bad_nonce = local_api.create_one_time_local_api_code(tmp_path, kind="login", ttl_seconds=60)
        bad_query = urlencode(
            {
                "nonce": bad_nonce,
                "redirect": "http://127.0.0.1:9999/codex-fleet/",
                "planeOrigin": "http://127.0.0.1:8080",
            }
        )
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        conn.request("GET", f"/api/plane/login?{bad_query}")
        assert conn.getresponse().status == 400
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _json_request(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, method=method)
    if token:
        request.add_header("X-Codex-Fleet-Token", token)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=5) as response:  # noqa: S310 - loopback test server.
        return json.loads(response.read().decode("utf-8"))


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


class _ProposingRunner(Runner):
    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        return RunResult(
            success=True,
            summary=f"Completed {item.identifier}.",
            proposed_tasks=(ProposedTask(title="Agent follow-up", description="Continue the work."),),
        )
