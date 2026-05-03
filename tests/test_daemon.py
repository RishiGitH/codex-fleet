import subprocess
from pathlib import Path

from codex_fleet.config import FleetConfig, WorkspaceConfig
from codex_fleet.daemon import FleetDaemon


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_daemon_runs_one_tick_with_fake_runner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    config = FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()

    stats = FleetDaemon(config, fake_runner=True).run(max_ticks=1, sleep_seconds=0)

    assert stats.ticks == 1
    assert stats.dispatched == 1
