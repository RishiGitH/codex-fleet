from pathlib import Path

import pytest

from codex_fleet.config import CodexConfig, FleetConfig, TrackerConfig
from codex_fleet.factory import build_runner, build_tracker, default_store_path
from codex_fleet.runner import CodexAppServerRunner, CodexCliRunner, FakeRunner
from codex_fleet.tracker import MemoryTracker


def test_build_memory_tracker() -> None:
    tracker = build_tracker(FleetConfig(tracker=TrackerConfig(kind="memory")))

    assert isinstance(tracker, MemoryTracker)
    assert tracker.fetch_candidate_items()


def test_build_plane_tracker_requires_settings() -> None:
    config = FleetConfig(tracker=TrackerConfig(kind="plane"))

    with pytest.raises(ValueError):
        build_tracker(config)


def test_build_runner_selects_fake_or_codex() -> None:
    config = FleetConfig()

    assert isinstance(build_runner(config, fake=True), FakeRunner)
    runner = build_runner(config, fake=False)
    assert isinstance(runner, CodexCliRunner)
    assert runner.stream_logs is True


def test_build_runner_preserves_app_server_mode() -> None:
    config = FleetConfig(codex=CodexConfig(runner="app-server", command="codex app-server"))

    assert isinstance(build_runner(config, fake=False), CodexAppServerRunner)


def test_build_runner_can_force_fake_failure() -> None:
    config = FleetConfig()
    runner = build_runner(config, fake=True, fake_succeed=False)

    assert isinstance(runner, FakeRunner)
    assert runner.succeed is False


def test_default_store_path_is_inside_repo(tmp_path: Path) -> None:
    assert default_store_path(tmp_path) == tmp_path / ".codex-fleet" / "runs.sqlite3"
