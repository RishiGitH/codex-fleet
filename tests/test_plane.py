import subprocess
from pathlib import Path

import httpx
import pytest

import codex_fleet.plane as plane_module
from codex_fleet.config import FleetConfig, WorkspaceConfig
from codex_fleet.models import ProposedTask, RunResult, WorkItem
from codex_fleet.orchestrator import Orchestrator
from codex_fleet.plane import (
    PlaneClient,
    PlaneSettings,
    PlaneTracker,
    normalize_plane_item,
    plane_project_external_id,
    plane_project_identifier_candidates,
    plane_project_name,
    state_id_by_name,
)
from codex_fleet.runner import FakeRunner, Runner
from codex_fleet.store import RunStore
from codex_fleet.tracker import TrackerError


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_normalize_plane_item_maps_core_fields() -> None:
    item = normalize_plane_item(
        {
            "id": "abc",
            "sequence_id": 42,
            "name": "Add runner",
            "description_stripped": "Build it",
            "priority": "high",
            "state_detail": {"name": "Ready"},
            "project_detail": {"identifier": "CF"},
            "label_details": [{"name": "Backend"}],
        }
    )

    assert item.id == "abc"
    assert item.identifier == "CF-42"
    assert item.title == "Add runner"
    assert item.description == "Build it"
    assert item.priority == 2
    assert item.state == "Ready"
    assert item.labels == ("backend",)


def test_state_id_by_name_is_case_insensitive() -> None:
    states = [{"id": "ready-id", "name": "Ready"}]

    assert state_id_by_name(states, "ready") == "ready-id"


def test_state_id_by_name_raises_for_missing_state() -> None:
    with pytest.raises(TrackerError):
        state_id_by_name([], "Ready")


def test_plane_project_identifier_candidates_are_plane_safe() -> None:
    candidates = plane_project_identifier_candidates("My cool app!!")

    assert candidates[0] == "MYCOOLAPP"
    assert all(candidate.isalnum() for candidate in candidates[:5])
    assert all(len(candidate) <= 12 for candidate in candidates[:5])


def test_plane_project_external_id_uses_resolved_path(tmp_path: Path) -> None:
    project = tmp_path / "app"
    project.mkdir()

    assert plane_project_external_id(project) == str(project.resolve())


def test_plane_project_name_removes_forbidden_characters() -> None:
    assert plane_project_name("codex-fleet-plane-map-demo") == "codex fleet plane map demo"
    assert plane_project_name("!!!") == "Codex Fleet Project"


def test_plane_client_retries_transient_api_gateway_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(502, request=request, json={"detail": "starting"})
        return httpx.Response(200, request=request, json={"ok": True})

    monkeypatch.setattr(plane_module.time, "sleep", lambda _delay: None)
    client = PlaneClient(
        PlaneSettings(
            base_url="http://plane.local",
            api_key="test-key",
            workspace_slug="codex-fleet",
            project_id="project-id",
        )
    )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        response = client._request_with_backoff(http_client, "GET", "http://plane.local/api/test")

    assert response.status_code == 200
    assert calls == 2


class FakePlaneClient:
    def __init__(self) -> None:
        self.updated: list[tuple[str, dict[str, str]]] = []

    def list_work_items(self) -> list[dict[str, object]]:
        return []

    def list_states(self) -> list[dict[str, str]]:
        return [{"id": "running-id", "name": "Running"}]

    def list_labels(self) -> list[dict[str, str]]:
        return [
            {"id": "agent-proposed-id", "name": "agent-proposed"},
            {"id": "agent-followup-id", "name": "agent-followup"},
        ]

    def label_ids_by_names(self, names: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(label["id"] for label in self.list_labels() if label["name"] in names)

    def update_work_item(self, item_id: str, payload: dict[str, str]) -> None:
        self.updated.append((item_id, payload))

    def create_work_item_comment(self, item_id: str, body: str) -> None:
        return None

    def create_work_item(
        self,
        name: str,
        description_html: str,
        state_id: str | None = None,
        label_ids: tuple[str, ...] = (),
    ) -> dict[str, object]:
        label_lookup = {label["id"]: label["name"] for label in self.list_labels()}
        return {
            "id": "created-item",
            "sequence_id": 2,
            "name": name,
            "description_html": description_html,
            "state": state_id,
            "label_details": [{"name": label_lookup[label_id]} for label_id in label_ids],
            "project_detail": {"identifier": "CF"},
        }


def test_plane_tracker_updates_state_by_resolved_id() -> None:
    client = FakePlaneClient()
    tracker = PlaneTracker(client=client, active_states=["Ready"])  # type: ignore[arg-type]

    tracker.update_item_state("item-1", "Running")

    assert client.updated == [("item-1", {"state": "running-id"})]


def test_plane_tracker_resolves_state_ids_before_filtering() -> None:
    class StateOnlyClient(FakePlaneClient):
        def list_work_items(self) -> list[dict[str, object]]:
            return [{"id": "item-1", "sequence_id": 1, "name": "Task", "state": "ready-id"}]

        def list_states(self) -> list[dict[str, str]]:
            return [{"id": "ready-id", "name": "Ready"}]

    tracker = PlaneTracker(client=StateOnlyClient(), active_states=["Ready"])  # type: ignore[arg-type]

    items = tracker.fetch_candidate_items()

    assert [item.id for item in items] == ["item-1"]
    assert items[0].state == "Ready"


class FakePlaneApi:
    def __init__(self) -> None:
        self.states = [
            {"id": "backlog-id", "name": "Backlog"},
            {"id": "ready-id", "name": "Ready"},
            {"id": "running-id", "name": "Running"},
            {"id": "human-review-id", "name": "Human Review"},
            {"id": "rework-id", "name": "Rework"},
        ]
        self.items: list[dict[str, object]] = [
            {
                "id": "item-1",
                "sequence_id": 1,
                "name": "Plane smoke",
                "description_stripped": "Exercise the Plane tracker.",
                "state_detail": {"name": "Ready"},
                "project_detail": {"identifier": "CF"},
            }
        ]
        self.comments: list[tuple[str, str]] = []
        self.transitions: list[str] = []

    def list_work_items(self) -> list[dict[str, object]]:
        return self.items

    def list_states(self) -> list[dict[str, str]]:
        return self.states

    def list_labels(self) -> list[dict[str, str]]:
        return [
            {"id": "agent-proposed-id", "name": "agent-proposed"},
            {"id": "agent-followup-id", "name": "agent-followup"},
        ]

    def label_ids_by_names(self, names: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(label["id"] for label in self.list_labels() if label["name"] in names)

    def update_work_item(self, item_id: str, payload: dict[str, str]) -> None:
        state_id = payload["state"]
        state_name = next(state["name"] for state in self.states if state["id"] == state_id)
        item = next(item for item in self.items if item["id"] == item_id)
        item["state_detail"] = {"name": state_name}
        self.transitions.append(str(state_name))

    def create_work_item_comment(self, item_id: str, body: str) -> None:
        self.comments.append((item_id, body))

    def create_work_item(
        self,
        name: str,
        description_html: str,
        state_id: str | None = None,
        label_ids: tuple[str, ...] = (),
    ) -> dict[str, object]:
        state_name = next((state["name"] for state in self.states if state["id"] == state_id), "Backlog")
        label_lookup = {label["id"]: label["name"] for label in self.list_labels()}
        item = {
            "id": "item-2",
            "sequence_id": 2,
            "name": name,
            "description_html": description_html,
            "state": state_id,
            "state_detail": {"name": state_name},
            "label_details": [{"name": label_lookup[label_id]} for label_id in label_ids],
            "project_detail": {"identifier": "CF"},
        }
        self.items.append(item)
        return item


def test_plane_tracker_orchestrator_success_posts_status_and_human_review(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    api = FakePlaneApi()
    tracker = PlaneTracker(api, active_states=["Ready"])  # type: ignore[arg-type]
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()

    result = Orchestrator(
        config=config,
        tracker=tracker,
        runner=FakeRunner(),
        store=RunStore(tmp_path / "runs.sqlite3"),
    ).run_once()

    assert result.dispatched is True
    assert api.transitions == ["Running", "Human Review"]
    assert api.items[0]["state_detail"] == {"name": "Human Review"}
    comments = "\n".join(comment for _, comment in api.comments)
    assert "codex-fleet started run" in comments
    assert "codex-fleet completed run" in comments
    assert "Workspace:" in comments
    assert result.run is not None
    assert result.run.worktree_path is not None
    assert (result.run.worktree_path / ".codex-fleet-fake-run.txt").exists()


def test_plane_tracker_orchestrator_failure_posts_status_and_rework(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    api = FakePlaneApi()
    tracker = PlaneTracker(api, active_states=["Ready"])  # type: ignore[arg-type]
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()

    result = Orchestrator(
        config=config,
        tracker=tracker,
        runner=FakeRunner(succeed=False),
        store=RunStore(tmp_path / "runs.sqlite3"),
    ).run_once()

    assert result.dispatched is True
    assert api.transitions == ["Running", "Rework"]
    assert api.items[0]["state_detail"] == {"name": "Rework"}
    assert "codex-fleet run failed" in "\n".join(comment for _, comment in api.comments)


def test_plane_tracker_creates_agent_proposed_follow_up_item(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    api = FakePlaneApi()
    tracker = PlaneTracker(api, active_states=["Ready"])  # type: ignore[arg-type]
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()

    result = Orchestrator(
        config=config,
        tracker=tracker,
        runner=ProposingRunner(),
        store=RunStore(tmp_path / "runs.sqlite3"),
    ).run_once()

    assert result.dispatched is True
    assert api.items[1]["name"] == "Follow up in Plane"
    assert api.items[1]["state_detail"] == {"name": "Ready"}
    assert api.items[1]["label_details"] == [{"name": "agent-followup"}]
    comments = "\n".join(comment for _, comment in api.comments)
    assert "Agent-proposed follow-up tasks" in comments
    assert "proposed this follow-up" in comments


class ProposingRunner(Runner):
    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        return RunResult(
            success=True,
            summary="Done",
            proposed_tasks=(ProposedTask(title="Follow up in Plane", description="Visible follow-up."),),
        )
