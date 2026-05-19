import subprocess
from pathlib import Path

from codex_fleet.config import FleetConfig, WorkspaceConfig
from codex_fleet.daemon import FleetDaemon
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
    assert created[0].state == "Ready"
    assert "agent-followup" in created[0].labels
    assert "CF-2" in tracker.comments["1"][-1]
    assert "proposed this follow-up" in tracker.comments[created[0].id][-1]
    metadata = store.get_task_metadata(created[0].id)
    assert metadata is not None
    assert metadata.source == "agent-followup"
    assert metadata.depth == 1
    assert metadata.parent_item_id == "1"
    events = store.list_events(result.run.id)
    assert "proposed_task_created" in [event.kind for event in events]
    waiting = [event for event in events if event.kind == "parent_waiting"]
    assert waiting[-1].payload["child_count"] == 1


def test_orchestrator_auto_runs_agent_follow_up_tasks_in_full_auto_mode(tmp_path: Path) -> None:
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
        workflow_mode="full_auto",
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
        workflow_mode="plan_only",
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
        workflow_mode="plan_execute",
        max_depth=2,
    ).run_once()

    assert result.run is not None
    created = [item for item in tracker.fetch_all_items() if item.identifier == "CF-2"]
    assert len(created) == 1
    assert created[0].state == "Backlog"
    assert "agent-proposed" in created[0].labels
    metadata = store.get_task_metadata(created[0].id)
    assert metadata is not None
    assert metadata.depth == 3


def test_execute_only_creates_one_implementer_child_task(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    item = WorkItem(id="1", identifier="CF-1", title="Build it", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")

    result = Orchestrator(
        config=config,
        tracker=tracker,
        runner=ProposingRunner(),
        store=store,
        workflow_mode="execute_only",
    ).run_once()

    created = [item for item in tracker.fetch_all_items() if item.identifier == "CF-2"]
    assert result.run is None
    assert len(created) == 1
    assert created[0].state == "Ready"
    metadata = store.get_task_metadata(created[0].id)
    assert metadata is not None
    assert metadata.role == "implementer"
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Planning"


def test_plan_execute_creates_planner_child_before_implementation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    item = WorkItem(id="1", identifier="CF-1", title="Build it", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="human-requested", depth=0, settings={"workflow_mode": "plan_execute"})

    result = Orchestrator(config=config, tracker=tracker, runner=ProposingRunner(), store=store).run_once()

    created = [item for item in tracker.fetch_all_items() if item.identifier == "CF-2"]
    assert result.run is None
    assert len(created) == 1
    assert created[0].state == "Ready"
    assert "agent-planner" in created[0].labels
    metadata = store.get_task_metadata(created[0].id)
    assert metadata is not None
    assert metadata.role == "planner"
    assert metadata.parent_item_id == "1"
    assert metadata.settings["workflow_mode"] == "execute_only"
    assert metadata.settings["parent_workflow_mode"] == "plan_execute"
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Planning"


def test_planner_output_creates_sibling_child_tasks_under_parent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    parent = WorkItem(id="1", identifier="CF-1", title="Parent", description=None, state="Planning")
    planner = WorkItem(id="2", identifier="CF-2", title="Plan CF-1", description=None, state="Ready", labels=("agent-planner",))
    tracker = MemoryTracker([parent, planner], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="human-requested", depth=0, settings={"workflow_mode": "plan_execute"})
    store.upsert_task_metadata(
        item_id="2",
        source="agent-followup",
        depth=1,
        parent_item_id="1",
        parent_identifier="CF-1",
        root_item_id="1",
        role="planner",
        settings={"workflow_mode": "plan_execute", "agent_role": "planner"},
    )

    result = Orchestrator(config=config, tracker=tracker, runner=ProposingRunner(), store=store).run_once()

    assert result.run is not None
    created = [item for item in tracker.fetch_all_items() if item.identifier == "CF-3"]
    assert len(created) == 1
    child_metadata = store.get_task_metadata(created[0].id)
    assert child_metadata is not None
    assert child_metadata.parent_item_id == "1"
    assert child_metadata.role == "implementer"
    assert tracker.fetch_items_by_ids(["2"])[0].state == "Human Review"
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Planning"


def test_planner_title_dependencies_are_resolved_to_child_ids(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    parent = WorkItem(id="1", identifier="CF-1", title="Parent", description=None, state="Planning")
    planner = WorkItem(id="2", identifier="CF-2", title="Plan CF-1", description=None, state="Ready", labels=("agent-planner",))
    tracker = MemoryTracker([parent, planner], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="human-requested", depth=0, settings={"workflow_mode": "full_auto"})
    store.upsert_task_metadata(
        item_id="2",
        source="agent-followup",
        depth=1,
        parent_item_id="1",
        parent_identifier="CF-1",
        root_item_id="1",
        role="planner",
        settings={"workflow_mode": "execute_only", "parent_workflow_mode": "full_auto", "agent_role": "planner"},
    )

    result = Orchestrator(config=config, tracker=tracker, runner=DependentPlanningRunner(), store=store).run_once()

    assert result.run is not None
    first = [item for item in tracker.fetch_all_items() if item.title == "Scout files"][0]
    second = [item for item in tracker.fetch_all_items() if item.title == "Implement page"][0]
    second_metadata = store.get_task_metadata(second.id)
    assert second_metadata is not None
    assert second_metadata.depends_on == (first.id,)


def test_planner_id_dependencies_are_resolved_to_child_ids(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    parent = WorkItem(id="1", identifier="CF-1", title="Parent", description=None, state="Planning")
    planner = WorkItem(id="2", identifier="CF-2", title="Plan CF-1", description=None, state="Ready", labels=("agent-planner",))
    tracker = MemoryTracker([parent, planner], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="human-requested", depth=0, settings={"workflow_mode": "full_auto"})
    store.upsert_task_metadata(
        item_id="2",
        source="agent-followup",
        depth=1,
        parent_item_id="1",
        parent_identifier="CF-1",
        root_item_id="1",
        role="planner",
        settings={"workflow_mode": "execute_only", "parent_workflow_mode": "full_auto", "agent_role": "planner"},
    )

    result = Orchestrator(config=config, tracker=tracker, runner=PlannerIdDependencyRunner(), store=store).run_once()

    assert result.run is not None
    first = [item for item in tracker.fetch_all_items() if item.title == "Scaffold app"][0]
    second = [item for item in tracker.fetch_all_items() if item.title == "Test app"][0]
    second_metadata = store.get_task_metadata(second.id)
    assert second_metadata is not None
    assert second_metadata.depends_on == (first.id,)


def test_full_auto_parent_completes_after_children_pass_and_creates_delivery_task(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    parent = WorkItem(id="1", identifier="CF-1", title="Parent", description=None, state="Planning")
    child = WorkItem(id="2", identifier="CF-2", title="Child", description=None, state="Human Review")
    tracker = MemoryTracker([parent, child], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="human-requested", depth=0, settings={"workflow_mode": "full_auto"})
    store.upsert_task_metadata(item_id="2", source="agent-followup", depth=1, parent_item_id="1", role="implementer")
    store.upsert_run(
        run_id="child-run",
        item_id="2",
        identifier="CF-2",
        status="done",
        branch_name="codex-fleet/CF-2",
        worktree_path=str(repo / ".codex-fleet" / "workspaces" / "CF-2"),
        agent_role="implementer",
        settings={
            "proof_kind": "cli_logs",
            "proof_status": "cli_passed",
            "proof_log_paths": [str(repo / ".codex-fleet" / "artifacts" / "child-run" / "pytest.log")],
        },
    )

    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()

    assert result.message == "Full auto parent completed."
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Done"
    delivery = [item for item in tracker.fetch_all_items() if item.title.startswith("Publish or merge result")]
    assert len(delivery) == 1
    delivery_metadata = store.get_task_metadata(delivery[0].id)
    assert delivery_metadata.role == "delivery_manager"
    assert delivery_metadata.settings["proof_kind"] == "cli_logs"
    assert delivery_metadata.settings["proof_status"] == "cli_passed"
    assert "Proof logs:" in (delivery[0].description or "")


def test_full_auto_parent_accepts_child_human_review_without_manual_review(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    parent = WorkItem(id="1", identifier="CF-1", title="Parent", description=None, state="Planning")
    child = WorkItem(id="2", identifier="CF-2", title="Child", description=None, state="Human Review")
    tracker = MemoryTracker([parent, child], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="human-requested", depth=0, settings={"workflow_mode": "full_auto"})
    store.upsert_task_metadata(
        item_id="2",
        source="agent-followup",
        depth=1,
        parent_item_id="1",
        parent_identifier="CF-1",
        role="implementer",
        settings={"workflow_mode": "execute_only", "parent_workflow_mode": "full_auto", "agent_role": "implementer"},
    )
    store.upsert_run(
        run_id="run-child",
        item_id="2",
        identifier="CF-2",
        status="human_review",
        branch_name="codex-fleet/CF-2",
        worktree_path=str(tmp_path / "workspaces" / "CF-2"),
        agent_role="implementer",
        model="gpt-5.5",
        reasoning_effort="low",
        settings={"parent_workflow_mode": "full_auto"},
    )

    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()

    assert result.message == "Full auto parent completed."
    assert tracker.fetch_items_by_ids(["2"])[0].state == "Done"
    assert store.get_run("run-child").status == "done"
    assert "accepted this agent task automatically" in tracker.comments["2"][-1]


def test_full_auto_parent_completes_even_if_parent_column_drifted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    parent = WorkItem(id="1", identifier="CF-1", title="Parent", description=None, state="Human Review")
    child = WorkItem(id="2", identifier="CF-2", title="Child", description=None, state="Human Review")
    tracker = MemoryTracker([parent, child], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="human-requested", depth=0, settings={"workflow_mode": "full_auto"})
    store.upsert_task_metadata(item_id="2", source="agent-followup", depth=1, parent_item_id="1", role="implementer")

    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()

    assert result.message == "Full auto parent completed."
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Done"


def test_full_auto_parent_waiting_on_child_comment_is_deduplicated(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    parent = WorkItem(id="1", identifier="CF-1", title="Parent", description=None, state="Planning")
    child = WorkItem(id="2", identifier="CF-2", title="Child", description=None, state="Needs Input")
    sibling = WorkItem(id="3", identifier="CF-3", title="Sibling", description=None, state="Ready")
    tracker = MemoryTracker([parent, child, sibling], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="human-requested", depth=0, settings={"workflow_mode": "full_auto"})
    store.upsert_task_metadata(item_id="2", source="agent-followup", depth=1, parent_item_id="1", role="planner")
    store.upsert_task_metadata(item_id="3", source="agent-followup", depth=1, parent_item_id="1", role="implementer")

    orchestrator = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store)
    first = orchestrator.run_once()
    second = orchestrator.run_once()

    assert first.message == "Run completed and moved to Human Review."
    assert first.run is not None
    assert first.run.item.identifier == "CF-3"
    assert second.message == "No candidate work items found."
    assert len(tracker.comments["1"]) == 1
    assert "waiting on child `CF-2`" in tracker.comments["1"][0]
    assert [event.kind for event in store.list_events("parent:1")] == ["parent_blocked"]


def test_full_auto_child_success_moves_child_to_done(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    parent = WorkItem(id="1", identifier="CF-1", title="Parent", description=None, state="Planning")
    child = WorkItem(id="2", identifier="CF-2", title="Implement", description=None, state="Ready", labels=("agent-implementer",))
    tracker = MemoryTracker([parent, child], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="human-requested", depth=0, settings={"workflow_mode": "full_auto"})
    store.upsert_task_metadata(
        item_id="2",
        source="agent-followup",
        depth=1,
        parent_item_id="1",
        parent_identifier="CF-1",
        role="implementer",
        settings={"workflow_mode": "execute_only", "parent_workflow_mode": "full_auto", "agent_role": "implementer"},
    )

    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner(), store=store).run_once()

    assert result.run is not None
    assert result.run.status == RunStatus.DONE
    assert tracker.fetch_items_by_ids(["2"])[0].state == "Done"


def test_full_auto_planner_question_creates_fallback_tasks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    parent = WorkItem(id="1", identifier="CF-1", title="Landing page", description=None, state="Planning")
    planner = WorkItem(id="2", identifier="CF-2", title="Plan CF-1", description=None, state="Ready", labels=("agent-planner",))
    tracker = MemoryTracker([parent, planner], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="human-requested", depth=0, settings={"workflow_mode": "full_auto"})
    store.upsert_task_metadata(
        item_id="2",
        source="agent-followup",
        depth=1,
        parent_item_id="1",
        parent_identifier="CF-1",
        root_item_id="1",
        role="planner",
        settings={"workflow_mode": "execute_only", "parent_workflow_mode": "full_auto", "agent_role": "planner"},
    )

    result = Orchestrator(config=config, tracker=tracker, runner=NeedsInputRunner(), store=store).run_once()

    assert result.message == "Full auto planner blocker converted into child tasks."
    assert tracker.fetch_items_by_ids(["2"])[0].state == "Done"
    created_roles = {
        store.get_task_metadata(item.id).role
        for item in tracker.fetch_all_items()
        if item.id not in {"1", "2"} and store.get_task_metadata(item.id) is not None
    }
    assert {"implementer", "quality_reviewer", "test_reviewer"} <= created_roles
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Planning"


def test_full_auto_child_question_creates_bounded_repair_task(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    parent = WorkItem(id="1", identifier="CF-1", title="Parent", description=None, state="Planning")
    child = WorkItem(id="2", identifier="CF-2", title="Test app", description=None, state="Ready", labels=("agent-test-reviewer",))
    tracker = MemoryTracker([parent, child], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="1", source="human-requested", depth=0, settings={"workflow_mode": "full_auto"})
    store.upsert_task_metadata(
        item_id="2",
        source="agent-followup",
        depth=1,
        parent_item_id="1",
        parent_identifier="CF-1",
        root_item_id="1",
        role="test_reviewer",
        settings={"workflow_mode": "execute_only", "parent_workflow_mode": "full_auto", "agent_role": "test_reviewer"},
    )

    result = Orchestrator(config=config, tracker=tracker, runner=NeedsInputRunner(), store=store).run_once()

    assert result.run is not None
    assert result.run.status == RunStatus.DONE
    assert tracker.fetch_items_by_ids(["2"])[0].state == "Done"
    repairs = [item for item in tracker.fetch_all_items() if "agent-repair" in item.labels]
    assert len(repairs) == 1
    assert repairs[0].state == "Ready"
    assert store.get_task_metadata(repairs[0].id).role == "implementer"


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
    assert failed[-1].payload["state"] == "Needs Input"
    assert failed[-1].payload["run_status"] == "rework"
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Needs Input"


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
    assert "resume automatically" in tracker.comments["1"][-1]
    assert "needs_input" in [event.kind for event in store.list_events(result.run.id)]
    pending = store.latest_open_needs_input("1")
    assert pending is not None
    assert pending.question == "Which target should I use?"


def test_daemon_resolves_needs_input_from_human_comment(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    item = WorkItem(id="1", identifier="CF-1", title="Smoke", description=None, state="Needs Input")
    tracker = MemoryTracker([item], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_run(
        run_id="run-1",
        item_id="1",
        identifier="CF-1",
        status=RunStatus.NEEDS_INPUT.value,
        error="Which target should I use?",
    )
    store.record_needs_input("run-1", "1", "Which target should I use?")
    store.upsert_task_metadata(item_id="1", source="agent-followup", depth=1, role="planner", settings={"workflow_mode": "execute_only"})
    tracker.create_comment("1", "Use the public Codex Fleet landing page target.")
    daemon = FleetDaemon(config)
    daemon.tracker = tracker
    daemon.store = store

    assert daemon.reconcile_needs_input_answers() == 1

    assert tracker.fetch_items_by_ids(["1"])[0].state == "Ready"
    assert store.latest_open_needs_input("1") is None
    metadata = store.get_task_metadata("1")
    assert metadata is not None
    assert metadata.settings["human_answers"][0]["answer"] == "Use the public Codex Fleet landing page target."


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


def test_dependent_agent_runs_from_committed_dependency_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    implementer = WorkItem(id="impl", identifier="CF-1", title="Implement page", description=None, state="Ready")
    reviewer = WorkItem(id="review", identifier="CF-2", title="Review page", description=None, state="Ready")
    tracker = MemoryTracker([implementer, reviewer], active_states=["Ready"])
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_task_metadata(item_id="impl", source="agent-followup", depth=1, role="implementer")
    store.upsert_task_metadata(
        item_id="review",
        source="agent-followup",
        depth=1,
        role="quality_reviewer",
        depends_on=("impl",),
    )

    first = Orchestrator(config=config, tracker=tracker, runner=HandoffRunner(), store=store).run_once()
    second = Orchestrator(config=config, tracker=tracker, runner=HandoffRunner(), store=store).run_once()

    assert first.run is not None
    assert second.run is not None
    assert tracker.fetch_items_by_ids(["review"])[0].state == "Human Review"
    assert second.run.worktree_path is not None
    assert (second.run.worktree_path / "index.html").read_text() == "<h1>Codex Fleet</h1>\n"
    workspace_events = [event for event in store.list_events(second.run.id) if event.kind == "workspace_prepared"]
    assert workspace_events[-1].payload["base_branch"] == first.run.branch_name


class ProposingRunner(Runner):
    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        return RunResult(
            success=True,
            summary="Implemented initial work.",
            proposed_tasks=(ProposedTask(title="Add regression coverage", description="Cover the follow-up path."),),
        )


class DependentPlanningRunner(Runner):
    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        return RunResult(
            success=True,
            summary="Planned dependent work.",
            proposed_tasks=(
                ProposedTask(title="Scout files", description="Map relevant files.", role="code_scout"),
                ProposedTask(
                    title="Implement page",
                    description="Build the page.",
                    role="implementer",
                    depends_on=("Scout files",),
                ),
            ),
        )


class PlannerIdDependencyRunner(Runner):
    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        return RunResult(
            success=True,
            summary="Planned dependent work.",
            proposed_tasks=(
                ProposedTask(title="Scaffold app", description="Create the app.", role="implementer", planner_id="PLN-2-1"),
                ProposedTask(
                    title="Test app",
                    description="Test the app.",
                    role="test_reviewer",
                    planner_id="PLN-2-2",
                    depends_on=("PLN-2-1",),
                ),
            ),
        )


class HandoffRunner(Runner):
    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        if "Review" in item.title:
            assert (workspace / "index.html").exists()
            return RunResult(success=True, summary="Reviewed implementation.")
        (workspace / "index.html").write_text("<h1>Codex Fleet</h1>\n")
        return RunResult(success=True, summary="Implemented page.", changed_files=("index.html",))


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
