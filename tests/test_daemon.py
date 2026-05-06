import shutil
import sqlite3
import subprocess
from pathlib import Path

from codex_fleet.config import AgentConfig, CodexConfig, FleetConfig, TrackerConfig, WorkspaceConfig
from codex_fleet.daemon import DaemonStats, FleetDaemon, MultiProjectFleetDaemon
from codex_fleet.models import WorkItem
from codex_fleet.project_registry import ProjectRegistry
from codex_fleet.store import RunStore
from codex_fleet.tracker import MemoryTracker


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


def test_daemon_dispatches_up_to_agent_limit_per_tick(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    config = FleetConfig(
        repo=repo,
        agent=AgentConfig(max_concurrent_agents=2),
        workspace=WorkspaceConfig(root=tmp_path / "workspaces"),
    ).resolved()
    tracker = MemoryTracker(
        [
            WorkItem(id="1", identifier="CF-1", title="One", description=None, state="Ready"),
            WorkItem(id="2", identifier="CF-2", title="Two", description=None, state="Ready"),
            WorkItem(id="3", identifier="CF-3", title="Three", description=None, state="Ready"),
        ],
        active_states=["Ready"],
    )
    daemon = FleetDaemon(config, fake_runner=True)
    daemon.tracker = tracker

    stats = daemon.run(max_ticks=1, sleep_seconds=0)

    assert stats.ticks == 1
    assert stats.dispatched == 2
    states = {item.identifier: item.state for item in tracker.fetch_all_items()}
    assert states == {
        "CF-1": "Human Review",
        "CF-2": "Human Review",
        "CF-3": "Ready",
    }


def test_daemon_releases_stale_ready_claim_and_dispatches(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    config = FleetConfig(
        repo=repo,
        codex=CodexConfig(turn_timeout_ms=1, stall_timeout_ms=1),
        workspace=WorkspaceConfig(root=tmp_path / "workspaces"),
    ).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_run(run_id="stale-run", item_id="1", identifier="CF-1", status="queued")
    assert store.try_claim_item("1", "stale-run") is True
    with sqlite3.connect(store.path) as db:
        db.execute("update claims set updated_at = datetime('now', '-10 seconds') where item_id = '1'")
    tracker = MemoryTracker([WorkItem(id="1", identifier="CF-1", title="One", description=None, state="Ready")])
    daemon = FleetDaemon(config, fake_runner=True)
    daemon.store = store
    daemon.tracker = tracker

    result = daemon.tick()

    assert result.dispatched is True
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Human Review"
    stale_run = store.get_run("stale-run")
    assert stale_run is not None
    assert stale_run.status == "stalled"
    assert [event.kind for event in store.list_events("stale-run")] == ["stale_claim_released"]
    assert tracker.comments["1"][0].startswith("codex-fleet released a stale run claim")


def test_daemon_reports_stale_running_claim_to_rework(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    config = FleetConfig(
        repo=repo,
        codex=CodexConfig(turn_timeout_ms=1, stall_timeout_ms=1),
        workspace=WorkspaceConfig(root=tmp_path / "workspaces"),
    ).resolved()
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_run(run_id="stale-run", item_id="1", identifier="CF-1", status="running_codex")
    assert store.try_claim_item("1", "stale-run") is True
    with sqlite3.connect(store.path) as db:
        db.execute("update claims set updated_at = datetime('now', '-10 seconds') where item_id = '1'")
    tracker = MemoryTracker([WorkItem(id="1", identifier="CF-1", title="One", description=None, state="Running")])
    daemon = FleetDaemon(config, fake_runner=True)
    daemon.store = store
    daemon.tracker = tracker

    result = daemon.tick()

    assert result.dispatched is False
    assert result.message == "No candidate work items found."
    assert tracker.fetch_items_by_ids(["1"])[0].state == "Rework"
    stale_run = store.get_run("stale-run")
    assert stale_run is not None
    assert stale_run.status == "stalled"
    assert stale_run.error is not None
    assert "released a stale run claim" in stale_run.error


def test_multi_project_daemon_polls_registered_plane_projects(tmp_path: Path, monkeypatch) -> None:
    control = tmp_path / "control"
    app = tmp_path / "app"
    control.mkdir()
    app.mkdir()
    init_git_repo(app)
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    registry.add_project(app, plane_workspace_slug="codex-local", plane_project_id="plane-app")
    control_config = FleetConfig(
        repo=control,
        agent=AgentConfig(max_concurrent_agents=2),
        tracker=TrackerConfig(kind="plane", plane_workspace_slug="codex-local", plane_project_id="plane-control"),
    ).resolved()
    app_config = FleetConfig(
        repo=app,
        tracker=TrackerConfig(kind="plane", plane_workspace_slug="codex-local", plane_project_id="plane-app"),
    ).resolved()
    calls: list[Path] = []

    monkeypatch.setattr("codex_fleet.daemon.load_config", lambda repo: app_config if repo == app.resolve() else control_config)

    class FakePlaneClient:
        def list_projects(self) -> list[dict[str, object]]:
            return [{"id": "plane-app", "is_member": True}]

    monkeypatch.setattr("codex_fleet.daemon.build_plane_client", lambda config: FakePlaneClient())

    class FakeProjectDaemon:
        def __init__(self, config: FleetConfig, **_kwargs) -> None:
            self.config = config

        def tick(self):  # type: ignore[no-untyped-def]
            calls.append(self.config.repo)
            return type("Result", (), {"dispatched": True})()

    monkeypatch.setattr("codex_fleet.daemon.FleetDaemon", FakeProjectDaemon)

    stats = MultiProjectFleetDaemon(control_config, registry=registry, fake_runner=False).run(
        max_ticks=1,
        sleep_seconds=0,
    )

    assert stats == DaemonStats(ticks=1, dispatched=2)
    assert calls == [control.resolve(), app.resolve()]


def test_multi_project_daemon_skips_plane_project_when_user_is_not_member(tmp_path: Path, monkeypatch) -> None:
    control = tmp_path / "control"
    app = tmp_path / "app"
    control.mkdir()
    app.mkdir()
    init_git_repo(app)
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    registry.add_project(app, plane_workspace_slug="codex-local", plane_project_id="plane-app")
    control_config = FleetConfig(
        repo=control,
        agent=AgentConfig(max_concurrent_agents=2),
        tracker=TrackerConfig(kind="plane", plane_workspace_slug="codex-local", plane_project_id="plane-control"),
    ).resolved()
    calls: list[Path] = []

    class FakePlaneClient:
        def list_projects(self) -> list[dict[str, object]]:
            return [{"id": "plane-app", "is_member": False}]

    class FakeProjectDaemon:
        def __init__(self, config: FleetConfig, **_kwargs) -> None:
            self.config = config

        def tick(self):  # type: ignore[no-untyped-def]
            calls.append(self.config.repo)
            return type("Result", (), {"dispatched": False})()

    monkeypatch.setattr("codex_fleet.daemon.build_plane_client", lambda config: FakePlaneClient())
    monkeypatch.setattr("codex_fleet.daemon.FleetDaemon", FakeProjectDaemon)

    daemon = MultiProjectFleetDaemon(control_config, registry=registry, fake_runner=False)
    stats = daemon.run(max_ticks=1, sleep_seconds=0)

    assert stats == DaemonStats(ticks=1, dispatched=0)
    assert calls == [control.resolve()]
    assert daemon.last_errors
    assert "not a member" in daemon.last_errors[0].message


def test_multi_project_daemon_skips_registered_non_git_project(tmp_path: Path, monkeypatch) -> None:
    control = tmp_path / "control"
    app = tmp_path / "app"
    control.mkdir()
    app.mkdir()
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    registry.add_project(app, plane_workspace_slug="codex-local", plane_project_id="plane-app")
    control_config = FleetConfig(
        repo=control,
        agent=AgentConfig(max_concurrent_agents=2),
        tracker=TrackerConfig(kind="plane", plane_workspace_slug="codex-local", plane_project_id="plane-control"),
    ).resolved()
    calls: list[Path] = []

    class FakeProjectDaemon:
        def __init__(self, config: FleetConfig, **_kwargs) -> None:
            self.config = config

        def tick(self):  # type: ignore[no-untyped-def]
            calls.append(self.config.repo)
            return type("Result", (), {"dispatched": False})()

    monkeypatch.setattr("codex_fleet.daemon.FleetDaemon", FakeProjectDaemon)

    stats = MultiProjectFleetDaemon(control_config, registry=registry, fake_runner=False).run(
        max_ticks=1,
        sleep_seconds=0,
    )

    assert stats == DaemonStats(ticks=1, dispatched=0)
    assert calls == [control.resolve()]


def test_multi_project_daemon_skips_registered_project_when_git_root_is_stale(tmp_path: Path, monkeypatch) -> None:
    control = tmp_path / "control"
    app = tmp_path / "app"
    control.mkdir()
    app.mkdir()
    init_git_repo(app)
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    registry.add_project(app, plane_workspace_slug="codex-local", plane_project_id="plane-app")
    shutil.rmtree(app / ".git")
    control_config = FleetConfig(
        repo=control,
        agent=AgentConfig(max_concurrent_agents=2),
        tracker=TrackerConfig(kind="plane", plane_workspace_slug="codex-local", plane_project_id="plane-control"),
    ).resolved()
    calls: list[Path] = []

    class FakePlaneClient:
        def list_projects(self) -> list[dict[str, object]]:
            return [{"id": "plane-app", "is_member": True}]

    class FakeProjectDaemon:
        def __init__(self, config: FleetConfig, **_kwargs) -> None:
            self.config = config

        def tick(self):  # type: ignore[no-untyped-def]
            calls.append(self.config.repo)
            return type("Result", (), {"dispatched": False})()

    monkeypatch.setattr("codex_fleet.daemon.build_plane_client", lambda config: FakePlaneClient())
    monkeypatch.setattr("codex_fleet.daemon.FleetDaemon", FakeProjectDaemon)

    daemon = MultiProjectFleetDaemon(control_config, registry=registry, fake_runner=False)
    stats = daemon.run(max_ticks=1, sleep_seconds=0)

    assert stats == DaemonStats(ticks=1, dispatched=0)
    assert calls == [control.resolve()]
    assert daemon.last_errors
    assert "not a git repository" in daemon.last_errors[0].message


def test_multi_project_daemon_synthesizes_plane_config_when_registered_project_config_is_missing(tmp_path: Path, monkeypatch) -> None:
    control = tmp_path / "control"
    app = tmp_path / "app"
    control.mkdir()
    app.mkdir()
    init_git_repo(app)
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    registry.add_project(app, plane_workspace_slug="codex-local", plane_project_id="plane-app")
    control_config = FleetConfig(
        repo=control,
        agent=AgentConfig(max_concurrent_agents=2),
        tracker=TrackerConfig(
            kind="plane",
            active_states=["Ready"],
            plane_base_url="http://plane.test",
            plane_api_key="key",
            plane_workspace_slug="codex-local",
            plane_project_id="plane-control",
        ),
    ).resolved()
    seen_configs: list[FleetConfig] = []

    class FakePlaneClient:
        def list_projects(self) -> list[dict[str, object]]:
            return [{"id": "plane-app", "is_member": True}]

    class FakeProjectDaemon:
        def __init__(self, config: FleetConfig, **_kwargs) -> None:
            self.config = config

        def tick(self):  # type: ignore[no-untyped-def]
            seen_configs.append(self.config)
            return type("Result", (), {"dispatched": False})()

    monkeypatch.setattr("codex_fleet.daemon.build_plane_client", lambda config: FakePlaneClient())
    monkeypatch.setattr("codex_fleet.daemon.FleetDaemon", FakeProjectDaemon)

    stats = MultiProjectFleetDaemon(control_config, registry=registry, fake_runner=False).run(max_ticks=1, sleep_seconds=0)

    assert stats == DaemonStats(ticks=1, dispatched=0)
    assert [config.repo for config in seen_configs] == [control.resolve(), app.resolve()]
    assert seen_configs[1].tracker.kind == "plane"
    assert seen_configs[1].tracker.plane_project_id == "plane-app"
    assert seen_configs[1].tracker.plane_base_url == "http://plane.test"


def test_multi_project_daemon_dedupes_duplicate_plane_project_mapping(tmp_path: Path, monkeypatch) -> None:
    control = tmp_path / "control"
    first = tmp_path / "first"
    second = tmp_path / "second"
    control.mkdir()
    first.mkdir()
    second.mkdir()
    init_git_repo(first)
    init_git_repo(second)
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    registry.add_project(first, plane_workspace_slug="codex-local", plane_project_id="plane-app")
    registry.add_project(second, plane_workspace_slug="codex-local", plane_project_id="plane-app")
    control_config = FleetConfig(
        repo=control,
        agent=AgentConfig(max_concurrent_agents=3),
        tracker=TrackerConfig(kind="plane", plane_workspace_slug="codex-local", plane_project_id="plane-control"),
    ).resolved()
    calls: list[Path] = []

    class FakePlaneClient:
        def list_projects(self) -> list[dict[str, object]]:
            return [{"id": "plane-app", "is_member": True}]

    class FakeProjectDaemon:
        def __init__(self, config: FleetConfig, **_kwargs) -> None:
            self.config = config

        def tick(self):  # type: ignore[no-untyped-def]
            calls.append(self.config.repo)
            return type("Result", (), {"dispatched": False})()

    monkeypatch.setattr("codex_fleet.daemon.build_plane_client", lambda config: FakePlaneClient())
    monkeypatch.setattr("codex_fleet.daemon.FleetDaemon", FakeProjectDaemon)

    daemon = MultiProjectFleetDaemon(control_config, registry=registry, fake_runner=False)
    stats = daemon.run(max_ticks=1, sleep_seconds=0)

    assert stats == DaemonStats(ticks=1, dispatched=0)
    assert calls == [control.resolve(), second.resolve()]
    assert daemon.last_errors
    assert "already mapped" in daemon.last_errors[0].message
