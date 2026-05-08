import subprocess
from pathlib import Path

from codex_fleet.config import FleetConfig, WorkspaceConfig
from codex_fleet.models import NeedsInput, ProposedTask, RunResult, RunStatus, WorkItem
from codex_fleet.orchestrator import Orchestrator
from codex_fleet.runner import FakeRunner, Runner
from codex_fleet.store import RunStore
from codex_fleet.tracker import MemoryTracker


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_orchestrator_moves_successful_item_to_human_review(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()

    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner()).run_once()

    assert result.dispatched is True
    assert result.run is not None
    assert result.run.status == RunStatus.HUMAN_REVIEW
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Human Review"
    assert tracker.comments["1"]


def test_orchestrator_persists_run_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")

    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()

    assert result.run is not None
    stored = store.get_run(result.run.id)
    assert stored is not None
    assert stored.status == "human_review"
    assert stored.worktree_path is not None


def test_orchestrator_records_events_and_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")

    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()

    assert result.run is not None
    events = store.list_events(result.run.id)
    artifacts = store.list_artifacts(result.run.id)
    assert [event.kind for event in events] == [
        "claimed",
        "started",
        "workspace_preparing",
        "workspace_prepared",
        "runner_started",
        "runner_finished",
        "completed",
        "state_update_confirmed",
    ]
    assert events[-2].payload["state"] == "Human Review"
    assert artifacts
    assert artifacts[0].path.endswith(".codex-fleet-fake-run.txt")


def test_orchestrator_creates_agent_follow_up_tasks_for_review_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")

    result = Orchestrator(
        config=config,
        tracker=tracker,
        runner=ProposingRunner(),
        store=store,
    ).run_once()

    assert result.run is not None
    created = [item for item in tracker.fetch_all_items() if item.identifier == "CF-2"]
    assert len(created) == 1
    assert created[0].state == "Backlog"
    assert "agent-proposed" in created[0].labels
    assert "CF-2" in tracker.comments["1"][-1]
    assert "proposed this follow-up" in tracker.comments[created[0].id][-1]
    metadata = store.get_task_metadata(created[0].id)
    assert metadata is not None
    assert metadata.source == "agent-proposed"
    assert metadata.depth == 1
    assert metadata.parent_item_id == "1"
    events = store.list_events(result.run.id)
    assert "proposed_task_created" in [event.kind for event in events]
    completed = [event for event in events if event.kind == "completed"]
    assert completed[-1].payload["proposed_task_count"] == 1


def test_orchestrator_auto_runs_agent_follow_up_tasks_in_full_agent_mode(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")

    result = Orchestrator(
        config=config,
        tracker=tracker,
        runner=ProposingRunner(),
        store=store,
        agent_task_mode="agent_task_planner",
    ).run_once()

    assert result.run is not None
    created = [item for item in tracker.fetch_all_items() if item.identifier == "CF-2"]
    assert len(created) == 1
    assert created[0].state == "Ready"
    assert "agent-followup" in created[0].labels
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Planning"


def test_orchestrator_can_create_follow_up_tasks_for_review(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")

    result = Orchestrator(
        config=config,
        tracker=tracker,
        runner=ProposingRunner(),
        store=store,
        agent_task_mode="review_and_approve",
    ).run_once()

    assert result.run is not None
    created = [item for item in tracker.fetch_all_items() if item.identifier == "CF-2"]
    assert len(created) == 1
    assert created[0].state == "Backlog"
    assert "agent-proposed" in created[0].labels


def test_orchestrator_stops_auto_followups_at_depth_limit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="agent-followup", depth=2)

    result = Orchestrator(
        config=config,
        tracker=tracker,
        runner=ProposingRunner(),
        store=store,
        agent_task_mode="agent_task_planner",
        max_task_depth=2,
    ).run_once()

    assert result.run is not None
    created = [item for item in tracker.fetch_all_items() if item.identifier == "CF-2"]
    assert len(created) == 1
    assert created[0].state == "Backlog"
    assert "agent-proposed" in created[0].labels
    metadata = store.get_task_metadata(created[0].id)
    assert metadata is not None
    assert metadata.depth == 3


def test_orchestrator_records_failure_event(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")

    result = Orchestrator(
        config=config,
        tracker=tracker,
        runner=FakeRunner(succeed=False),
        store=store,
    ).run_once()

    assert result.run is not None
    events = store.list_events(result.run.id)
    failed = [event for event in events if event.kind == "failed"]
    assert failed[-1].payload["state"] == "Rework"
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Rework"


def test_orchestrator_skips_duplicate_claim(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    assert store.try_claim_item("1", "existing-run") is True

    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()

    assert result.dispatched is False
    assert result.run is None
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Ready"


def test_orchestrator_holds_claim_when_start_state_update_is_not_observed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = NonUpdatingTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")

    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()
    second = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()

    assert result.dispatched is True
    assert result.run is not None
    assert result.run.status == RunStatus.STALLED
    assert second.dispatched is False
    stored = store.get_run(result.run.id)
    assert stored is not None
    assert stored.status == "stalled"
    events = store.list_events(result.run.id)
    assert events[-1].kind == "state_update_pending"
    assert "held" in tracker.comments["1"][-1]


def test_orchestrator_holds_claim_when_final_state_update_is_not_observed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = FinalStateStuckTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")

    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()
    second = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()

    assert result.dispatched is True
    assert result.run is not None
    assert result.run.status == RunStatus.HUMAN_REVIEW
    assert second.dispatched is False
    events = store.list_events(result.run.id)
    assert events[-1].kind == "state_update_pending"
    assert "not dispatched again" in tracker.comments["1"][-1]


def test_orchestrator_moves_blocked_runner_to_needs_input(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")

    result = Orchestrator(config=config, tracker=tracker, runner=NeedsInputRunner(), store=store).run_once()

    assert result.dispatched is True
    assert result.run is not None
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Needs Input"
    assert "Which target should I use?" in tracker.comments["1"][-1]
    assert "needs_input" in [event.kind for event in store.list_events(result.run.id)]


def test_orchestrator_does_not_dispatch_dependency_blocked_child(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    tracker = MemoryTracker(
        [
            WorkItem(id="child", identifier="CF-2", title="Child", description=None, state="Ready"),
            WorkItem(id="dep", identifier="CF-1", title="Dependency", description=None, state="Running"),
        ],
        active_states=["Ready"],
    )
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="child", source="agent-followup", depth=1, depends_on=("dep",))

    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()

    assert result.dispatched is False
    assert tracker.fetch_items_by_ids(["child"])[0].state == "Ready"


class ProposingRunner(Runner):
    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        return RunResult(
            success=True,
            summary="Implemented initial work.",
            proposed_tasks=(ProposedTask(title="Add regression coverage", description="Cover the follow-up path."),),
        )


class NeedsInputRunner(Runner):
    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        return RunResult(
            success=False,
            summary="Blocked.",
            needs_input=NeedsInput(question="Which target should I use?"),
            error="Which target should I use?",
        )


class NonUpdatingTracker(MemoryTracker):
    def update_item_state(self, item_id: str, state: str) -> None:
        if item_id not in {item.id for item in self.fetch_all_items()}:
            super().update_item_state(item_id, state)


class FinalStateStuckTracker(MemoryTracker):
    def update_item_state(self, item_id: str, state: str) -> None:
        if state == "Running":
            super().update_item_state(item_id, state)
