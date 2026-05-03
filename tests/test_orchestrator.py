import subprocess
from pathlib import Path

from codex_fleet.config import FleetConfig, WorkspaceConfig
from codex_fleet.models import RunStatus, WorkItem
from codex_fleet.orchestrator import Orchestrator
from codex_fleet.runner import FakeRunner
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
