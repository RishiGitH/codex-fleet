from pathlib import Path

import pytest

from codex_fleet.config import FleetConfig, TrackerConfig
from codex_fleet.factory import build_runner, build_tracker, default_store_path
from codex_fleet.runner import CodexAppServerRunner, FakeRunner
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
    assert isinstance(build_runner(config, fake=False), CodexAppServerRunner)


def test_default_store_path_is_inside_repo(tmp_path: Path) -> None:
    assert default_store_path(tmp_path) == tmp_path / ".codex-fleet" / "runs.sqlite3"
