import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from click.testing import CliRunner

from codex_fleet.cli.main import _daemon_tick_logger, main
from codex_fleet.config import load_config
from codex_fleet.factory import default_store_path
from codex_fleet.lifecycle import StopResult
from codex_fleet.plane_local_bootstrap import PlaneLocalBootstrapResult
from codex_fleet.plane_manager import (
    DockerStatus,
    PlaneFrontendReport,
    PlaneInstall,
    PlaneManagerError,
    PlaneStatus,
)
from codex_fleet.project_registry import ProjectRegistry, default_project_registry_path
from codex_fleet.store import RunStore


class FakeServer:
    url = "http://127.0.0.1:17300"
    server_address = ("127.0.0.1", 18790)

    def serve_forever(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


def write_plane_source_marker(repo: Path) -> None:
    source = repo / "apps" / "plane"
    (source / "apps" / "web").mkdir(parents=True)
    (source / "package.json").write_text("{}\n")


def test_ui_command_is_not_public() -> None:
    result = CliRunner().invoke(main, ["ui", "--help"])

    assert result.exit_code != 0


def test_plane_configure_writes_config(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "plane-configure",
            "--repo",
            str(tmp_path),
            "--url",
            "http://127.0.0.1:17880",
            "--workspace-slug",
            "local",
            "--project-id",
            "project-id",
            "--api-key-ref",
            "literal-key",
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / ".codex-fleet.yml").exists()


def test_plane_configure_pauses_with_resume_instructions(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["plane-configure", "--repo", str(tmp_path)])

    assert result.exit_code != 0
    assert "plane-configure" in result.output
    assert "PLANE_WORKSPACE_SLUG" in result.output


def test_plane_status_reports_docker_daemon_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "codex_fleet.cli.main.inspect_plane_runtime",
        lambda _repo: PlaneInstall(tmp_path / ".codex-fleet" / "plane-selfhost", tmp_path / "setup.sh", False, None),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.check_docker_status",
        lambda: DockerStatus(available=True, daemon_ready=False, message="Cannot connect"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=False, message="Connection refused"),
    )

    result = CliRunner().invoke(main, ["plane-status", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert "Docker daemon ready: False" in result.output
    assert "Cannot connect" in result.output


def test_removed_plane_source_and_patch_commands_are_not_public() -> None:
    source_result = CliRunner().invoke(main, ["plane-source", "--help"])
    patch_result = CliRunner().invoke(main, ["plane-patch", "--help"])

    assert source_result.exit_code != 0
    assert patch_result.exit_code != 0


def test_project_prune_stale_is_dry_run_until_apply(tmp_path: Path) -> None:
    stale = tmp_path / "stale"
    stale.mkdir()
    registry = ProjectRegistry(default_project_registry_path(tmp_path))
    project = registry.add_project(stale, name="Stale Project")
    stale.rmdir()

    dry_run = CliRunner().invoke(main, ["project", "prune-stale", "--repo", str(tmp_path)])

    assert dry_run.exit_code == 0
    assert "Dry run only" in dry_run.output
    assert "folder missing" in dry_run.output
    assert registry.get_project(project.id) is not None

    applied = CliRunner().invoke(main, ["project", "prune-stale", "--repo", str(tmp_path), "--apply"])

    assert applied.exit_code == 0
    assert "Removed 1 stale" in applied.output
    assert registry.get_project(project.id) is None


def test_project_prune_stale_removes_older_duplicate_plane_mapping(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    subprocess.run(["git", "-C", str(first), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(second), "init"], check=True, capture_output=True)
    registry = ProjectRegistry(default_project_registry_path(tmp_path))
    old = registry.add_project(first, name="Old")
    new = registry.add_project(second, name="New")
    registry.update_plane_mapping(old.id, workspace_slug="codex-local", project_id_in_plane="plane-project")
    registry.update_plane_mapping(new.id, workspace_slug="codex-local", project_id_in_plane="plane-project")

    applied = CliRunner().invoke(main, ["project", "prune-stale", "--repo", str(tmp_path), "--apply"])

    assert applied.exit_code == 0
    assert "duplicate Plane project mapping" in applied.output
    assert registry.get_project(old.id) is None
    assert registry.get_project(new.id) is not None


def test_daemon_tick_logger_marks_stale_project_as_skipped(capsys) -> None:
    _daemon_tick_logger(
        1,
        [
            SimpleNamespace(
                repo="/tmp/missing",
                error=SimpleNamespace(message="Project folder is missing."),
            )
        ],
    )

    output = capsys.readouterr().out
    assert "skipped stale project" in output
    assert "error:" not in output


def test_plane_fork_preview_prepare_only(tmp_path: Path, monkeypatch) -> None:
    build_dir = tmp_path / "apps" / "plane" / "apps" / "web" / "build" / "client"

    monkeypatch.setattr("codex_fleet.cli.main.prepare_plane_preview_build", lambda _repo: build_dir)

    result = CliRunner().invoke(main, ["plane-fork-preview", "--repo", str(tmp_path), "--prepare-only"])

    assert result.exit_code == 0
    assert "Plane fork build ready:" in result.output
    assert "build" in result.output


def test_up_fails_when_apps_plane_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("codex_fleet.cli.main.create_local_api_server", lambda *_args, **_kwargs: FakeServer())
    monkeypatch.setattr(
        "codex_fleet.cli.main.start_plane",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PlaneManagerError("Docker is not available")),
    )

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake", "--once"])

    assert result.exit_code != 0
    assert "apps/plane" in result.output


def test_up_bootstraps_plane_and_opens_board_when_config_is_missing(tmp_path: Path, monkeypatch) -> None:
    write_plane_source_marker(tmp_path)
    calls: list[str] = []

    def fake_bootstrap(_repo: Path, _plane_url: str):  # type: ignore[no-untyped-def]
        calls.append("local-bootstrap")
        (tmp_path / ".codex-fleet.yml").write_text(
            "repo: .\n"
            "tracker:\n"
            "  kind: plane\n"
            "  active_states: [Ready]\n"
            "  plane_base_url: http://127.0.0.1:17880\n"
            "  plane_api_key: test-key\n"
            "  plane_workspace_slug: local\n"
            "  plane_project_id: project-id\n"
        )
        return load_config(tmp_path)

    monkeypatch.setattr(
        "codex_fleet.cli.main._bootstrap_default_local_plane_config",
        fake_bootstrap,
    )
    monkeypatch.setattr("codex_fleet.cli.main.build_plane_client", lambda _config: object())
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.prepare_plane_preview_build",
        lambda _repo: tmp_path / "apps" / "plane" / "apps" / "web" / "build" / "client",
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.install_branded_plane_frontend",
        lambda _repo, _build_dir: calls.append("frontend")
        or PlaneFrontendReport(
            container="plane-app-web-1",
            build_dir=tmp_path / "build",
            backup_dir=tmp_path / "backup",
            installed=True,
            message="installed",
        ),
    )
    monkeypatch.setattr("codex_fleet.cli.main.create_local_api_server", lambda *_args, **_kwargs: FakeServer())
    opened_urls: list[str] = []
    monkeypatch.setattr("codex_fleet.cli.main.open_plane", lambda url: opened_urls.append(str(url)) or calls.append("open") or True)
    monkeypatch.setattr("codex_fleet.cli.main.ensure_plane_bootstrap", lambda _client, _states: calls.append("bootstrap"))
    monkeypatch.setattr("codex_fleet.cli.main.ensure_local_plane_project_views", lambda _config: SimpleNamespace(skipped=True, skipped_reason="test"))

    class FakeDaemon:
        def __init__(self, *_args, **_kwargs) -> None:
            calls.append("daemon")

        def run(self, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(ticks=1, dispatched=0)

    monkeypatch.setattr("codex_fleet.cli.main.FleetDaemon", FakeDaemon)

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake"])

    assert result.exit_code == 0
    assert calls == ["local-bootstrap", "frontend", "bootstrap", "open", "daemon"]
    assert "No codex-fleet config found. Bootstrapping local Plane." in result.output
    assert "Plane board: http://127.0.0.1:17880/local/projects/project-id/issues/" in result.output
    assert opened_urls
    assert "/api/plane/login?" in opened_urls[0]
    assert "token=" not in opened_urls[0]


def test_up_replaces_memory_tracker_with_local_plane_product_flow(tmp_path: Path, monkeypatch) -> None:
    write_plane_source_marker(tmp_path)
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: memory\n"
        "  active_states: [Ready]\n"
    )
    calls: list[str] = []

    def fake_bootstrap(_repo: Path, _plane_url: str):  # type: ignore[no-untyped-def]
        calls.append("local-bootstrap")
        (tmp_path / ".codex-fleet.yml").write_text(
            "repo: .\n"
            "tracker:\n"
            "  kind: plane\n"
            "  active_states: [Ready]\n"
            "  plane_base_url: http://127.0.0.1:17880\n"
            "  plane_api_key: test-key\n"
            "  plane_workspace_slug: local\n"
            "  plane_project_id: project-id\n"
        )
        return load_config(tmp_path)

    monkeypatch.setattr("codex_fleet.cli.main._bootstrap_default_local_plane_config", fake_bootstrap)
    monkeypatch.setattr("codex_fleet.cli.main.build_plane_client", lambda _config: object())
    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.prepare_plane_preview_build",
        lambda _repo: tmp_path / "apps" / "plane" / "apps" / "web" / "build" / "client",
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.install_branded_plane_frontend",
        lambda _repo, _build_dir: calls.append("frontend")
        or PlaneFrontendReport(
            container="plane-app-web-1",
            build_dir=tmp_path / "build",
            backup_dir=tmp_path / "backup",
            installed=True,
            message="installed",
        ),
    )
    monkeypatch.setattr("codex_fleet.cli.main.create_local_api_server", lambda *_args, **_kwargs: FakeServer())
    opened_urls: list[str] = []
    monkeypatch.setattr("codex_fleet.cli.main.open_plane", lambda url: opened_urls.append(str(url)) or calls.append("open") or True)
    monkeypatch.setattr("codex_fleet.cli.main.ensure_plane_bootstrap", lambda _client, _states: calls.append("bootstrap"))
    monkeypatch.setattr(
        "codex_fleet.cli.main.ensure_local_plane_project_views",
        lambda _config: SimpleNamespace(skipped=True, skipped_reason="test"),
    )

    class FakeDaemon:
        def __init__(self, *_args, **_kwargs) -> None:
            calls.append("daemon")

        def run(self, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(ticks=1, dispatched=0)

    monkeypatch.setattr("codex_fleet.cli.main.FleetDaemon", FakeDaemon)

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake"])

    assert result.exit_code == 0
    assert "Configured memory tracker; bootstrapping local Plane" in result.output
    assert "full Codex Fleet" in result.output
    assert calls == ["local-bootstrap", "frontend", "bootstrap", "open", "daemon"]
    assert opened_urls
    assert "/api/plane/login?" in opened_urls[0]


def test_up_starts_configured_loopback_plane_when_not_ready(tmp_path: Path, monkeypatch) -> None:
    write_plane_source_marker(tmp_path)
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://127.0.0.1:17880\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: local\n"
        "  plane_project_id: project-id\n"
    )
    calls: list[str] = []

    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=False, message="Connection refused"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.start_plane",
        lambda _repo, url: calls.append(f"start:{url}"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr("codex_fleet.cli.main.build_plane_client", lambda _config: object())
    monkeypatch.setattr(
        "codex_fleet.cli.main.ensure_plane_bootstrap",
        lambda _client, _states: calls.append("bootstrap"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.prepare_plane_preview_build",
        lambda _repo: tmp_path / "apps" / "plane" / "apps" / "web" / "build" / "client",
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.install_branded_plane_frontend",
        lambda _repo, _build_dir: calls.append("frontend")
        or PlaneFrontendReport(
            container="plane-app-web-1",
            build_dir=tmp_path / "build",
            backup_dir=tmp_path / "backup",
            installed=True,
            message="installed",
        ),
    )

    class FakeDaemon:
        def __init__(self, *_args, **_kwargs) -> None:
            calls.append("daemon")

        def run(self, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(ticks=1, dispatched=0)

    monkeypatch.setattr("codex_fleet.cli.main.FleetDaemon", FakeDaemon)

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake", "--once"])

    assert result.exit_code == 0
    assert "Starting local Plane at http://127.0.0.1:17880" in result.output
    assert "Installing branded codex-fleet Plane frontend" in result.output
    assert calls == ["start:http://127.0.0.1:17880", "frontend", "bootstrap", "daemon"]


def test_up_can_skip_branded_frontend_install(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://127.0.0.1:17880\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: local\n"
        "  plane_project_id: project-id\n"
    )
    calls: list[str] = []

    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr("codex_fleet.cli.main.build_plane_client", lambda _config: object())
    monkeypatch.setattr(
        "codex_fleet.cli.main.ensure_plane_bootstrap",
        lambda _client, _states: calls.append("bootstrap"),
    )

    def unexpected_prepare(_repo: Path) -> Path:
        raise AssertionError("stock Plane mode should not prepare the branded build")

    monkeypatch.setattr("codex_fleet.cli.main.prepare_plane_preview_build", unexpected_prepare)

    class FakeDaemon:
        def __init__(self, *_args, **_kwargs) -> None:
            calls.append("daemon")

        def run(self, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(ticks=1, dispatched=0)

    monkeypatch.setattr("codex_fleet.cli.main.FleetDaemon", FakeDaemon)

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake", "--once", "--stock-plane"])

    assert result.exit_code == 0
    assert "Installing branded codex-fleet Plane frontend" not in result.output
    assert calls == ["bootstrap", "daemon"]


def test_up_starts_local_api_for_long_running_plane_ui(tmp_path: Path, monkeypatch) -> None:
    write_plane_source_marker(tmp_path)
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://127.0.0.1:17880\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: local\n"
        "  plane_project_id: project-id\n"
    )
    calls: list[str] = []

    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr("codex_fleet.cli.main.build_plane_client", lambda _config: object())
    monkeypatch.setattr("codex_fleet.cli.main.ensure_plane_bootstrap", lambda _client, _states: None)
    monkeypatch.setattr(
        "codex_fleet.cli.main.prepare_plane_preview_build",
        lambda _repo: tmp_path / "apps" / "plane" / "apps" / "web" / "build" / "client",
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.install_branded_plane_frontend",
        lambda _repo, _build_dir: PlaneFrontendReport(
            container="plane-app-web-1",
            build_dir=tmp_path / "build",
            backup_dir=tmp_path / "backup",
            installed=True,
            message="installed",
        ),
    )

    class FakeApiServer:
        server_address = ("127.0.0.1", 18790)

        def serve_forever(self) -> None:
            calls.append("api-serve")

        def shutdown(self) -> None:
            calls.append("api-shutdown")

    monkeypatch.setattr("codex_fleet.cli.main.create_local_api_server", lambda *_args, **_kwargs: FakeApiServer())
    monkeypatch.setattr("codex_fleet.cli.main.open_plane", lambda _url: calls.append("open") or True)

    class FakeDaemon:
        def __init__(self, *_args, **_kwargs) -> None:
            calls.append("daemon")

        def run(self, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(ticks=1, dispatched=0)

    monkeypatch.setattr("codex_fleet.cli.main.FleetDaemon", FakeDaemon)

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake"])

    assert result.exit_code == 0
    assert "codex-fleet API: http://127.0.0.1:18790" in result.output
    assert "Plane board: http://127.0.0.1:17880/local/projects/project-id/issues/" in result.output
    assert "api-serve" in calls
    assert "open" in calls
    assert calls[-1] == "api-shutdown"


def test_up_matches_local_api_host_to_loopback_plane_host(tmp_path: Path, monkeypatch) -> None:
    write_plane_source_marker(tmp_path)
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://localhost:17880\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: local\n"
        "  plane_project_id: project-id\n"
    )
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://localhost:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://localhost:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr("codex_fleet.cli.main.build_plane_client", lambda _config: object())
    monkeypatch.setattr("codex_fleet.cli.main.ensure_plane_bootstrap", lambda _client, _states: None)
    monkeypatch.setattr(
        "codex_fleet.cli.main.prepare_plane_preview_build",
        lambda _repo: tmp_path / "apps" / "plane" / "apps" / "web" / "build" / "client",
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.install_branded_plane_frontend",
        lambda _repo, _build_dir: PlaneFrontendReport(
            container="plane-app-web-1",
            build_dir=tmp_path / "build",
            backup_dir=tmp_path / "backup",
            installed=True,
            message="installed",
        ),
    )

    class FakeApiServer:
        server_address = ("localhost", 18790)

        def serve_forever(self) -> None:
            calls.append(("api-serve", None))

        def shutdown(self) -> None:
            calls.append(("api-shutdown", None))

    def fake_create_local_api_server(*_args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(("api-host", kwargs["host"]))
        return FakeApiServer()

    monkeypatch.setattr("codex_fleet.cli.main.create_local_api_server", fake_create_local_api_server)
    monkeypatch.setattr("codex_fleet.cli.main.open_plane", lambda url: calls.append(("open", url)) or True)

    class FakeDaemon:
        def __init__(self, *_args, **_kwargs) -> None:
            calls.append(("daemon", None))

        def run(self, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(ticks=1, dispatched=0)

    monkeypatch.setattr("codex_fleet.cli.main.FleetDaemon", FakeDaemon)

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake"])

    assert result.exit_code == 0
    assert ("api-host", "localhost") in calls
    assert "codex-fleet API: http://localhost:18790" in result.output
    opened_url = next(value for kind, value in calls if kind == "open")
    assert "http://localhost:18790/api/plane/login?" in str(opened_url)
    assert "planeOrigin=http%3A%2F%2Flocalhost%3A17880" in str(opened_url)


def test_up_reports_local_plane_start_failure(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://127.0.0.1:17880\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: local\n"
        "  plane_project_id: project-id\n"
    )

    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=False, message="Connection refused"),
    )

    def fail_start(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise PlaneManagerError("Docker daemon is not ready: Cannot connect")

    monkeypatch.setattr("codex_fleet.cli.main.start_plane", fail_start)

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake", "--once"])

    assert result.exit_code != 0
    assert "Local Plane could not be started" in result.output
    assert "Docker daemon is not ready" in result.output


def test_up_missing_plane_api_key_bootstraps_local_plane(tmp_path: Path, monkeypatch) -> None:
    write_plane_source_marker(tmp_path)
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://127.0.0.1:17880\n"
        "  plane_api_key: $PLANE_API_KEY\n"
        "  plane_workspace_slug: local\n"
        "  plane_project_id: project-id\n"
    )

    calls: list[str] = []
    monkeypatch.delenv("PLANE_API_KEY", raising=False)

    def fake_reconfigure(_repo: Path, _plane_url: str):  # type: ignore[no-untyped-def]
        calls.append("local-bootstrap")
        (tmp_path / ".codex-fleet.yml").write_text(
            "repo: .\n"
            "tracker:\n"
            "  kind: plane\n"
            "  active_states: [Ready]\n"
            "  plane_base_url: http://127.0.0.1:17880\n"
            "  plane_api_key: test-key\n"
            "  plane_workspace_slug: local\n"
            "  plane_project_id: project-id\n"
        )
        return load_config(tmp_path)

    monkeypatch.setattr("codex_fleet.cli.main._bootstrap_default_local_plane_config", fake_reconfigure)
    build_calls = {"count": 0}

    def fake_build_plane_client(_config):  # type: ignore[no-untyped-def]
        build_calls["count"] += 1
        if build_calls["count"] == 1:
            raise ValueError("Missing required setting: PLANE_API_KEY")
        return object()

    monkeypatch.setattr("codex_fleet.cli.main.build_plane_client", fake_build_plane_client)
    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://127.0.0.1:17880", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.ensure_plane_bootstrap",
        lambda _client, _states: calls.append("bootstrap"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.prepare_plane_preview_build",
        lambda _repo: tmp_path / "apps" / "plane" / "apps" / "web" / "build" / "client",
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.install_branded_plane_frontend",
        lambda _repo, _build_dir: calls.append("frontend")
        or PlaneFrontendReport(
            container="plane-app-web-1",
            build_dir=tmp_path / "build",
            backup_dir=tmp_path / "backup",
            installed=True,
            message="installed",
        ),
    )

    class FakeDaemon:
        def __init__(self, *_args, **_kwargs) -> None:
            calls.append("daemon")

        def run(self, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(ticks=1, dispatched=0)

    monkeypatch.setattr("codex_fleet.cli.main.FleetDaemon", FakeDaemon)

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake", "--once"])

    assert result.exit_code == 0
    assert "Configured Plane is missing a required setting: Missing required setting:" in result.output
    assert "PLANE_API_KEY" in result.output
    assert "Bootstrapping local Plane credentials automatically" in result.output
    assert calls == ["local-bootstrap", "frontend", "bootstrap", "daemon"]


def test_plane_onboarding_url_prefills_project_without_query_token(tmp_path: Path) -> None:
    project_dir = tmp_path / "app"
    project_dir.mkdir()

    result = CliRunner().invoke(
        main,
        [
            "plane-onboarding-url",
            "--repo",
            str(tmp_path),
            "--path",
            str(project_dir),
            "--plane-url",
            "http://127.0.0.1:17300",
        ],
    )

    assert result.exit_code == 0
    assert "http://127.0.0.1:17300/codex-fleet/onboarding#" in result.output
    assert "path=" in result.output
    assert "?token=" not in result.output


def test_plane_local_bootstrap_command_writes_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "codex_fleet.cli.main.bootstrap_local_plane",
        lambda *_args, **_kwargs: PlaneLocalBootstrapResult(
            workspace_slug="codex-fleet",
            workspace_created=True,
            project_id="project-id",
            project_created=True,
            project_name="Codex Fleet",
            api_key="plane_api_secret",
            token_created=True,
            user_email="codex-fleet-local@example.local",
        ),
    )

    result = CliRunner().invoke(main, ["plane-local-bootstrap", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert "Config ready:" in result.output
    assert "Workspace: codex-fleet" in result.output
    assert "plane_api_secret" not in result.output
    assert "PLANE_API_KEY=plane_api_secret" in (tmp_path / ".codex-fleet" / "secrets.env").read_text()


def test_plane_frontend_install_command(tmp_path: Path, monkeypatch) -> None:
    write_plane_source_marker(tmp_path)
    build_dir = tmp_path / "build"
    report = PlaneFrontendReport(
        container="plane-app-web-1",
        build_dir=build_dir,
        backup_dir=tmp_path / "backup",
        installed=True,
        message="installed",
    )

    monkeypatch.setattr("codex_fleet.cli.main.prepare_plane_preview_build", lambda _repo: build_dir)
    monkeypatch.setattr("codex_fleet.cli.main.install_branded_plane_frontend", lambda *_args, **_kwargs: report)

    result = CliRunner().invoke(main, ["plane-frontend", "install", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert "installed" in result.output
    assert "plane-app-web-1" in result.output


def test_plane_frontend_install_reuses_existing_build(tmp_path: Path, monkeypatch) -> None:
    write_plane_source_marker(tmp_path)
    build_dir = tmp_path / "apps" / "plane" / "apps" / "web" / "build" / "client"
    build_dir.mkdir(parents=True)
    (build_dir / "index.html").write_text("codex-fleet\n")
    report = PlaneFrontendReport(
        container="plane-app-web-1",
        build_dir=build_dir,
        backup_dir=tmp_path / "backup",
        installed=True,
        message="installed",
    )

    def unexpected_prepare(_repo: Path) -> Path:
        raise AssertionError("existing Plane build should be reused")

    monkeypatch.setattr("codex_fleet.cli.main.prepare_plane_preview_build", unexpected_prepare)
    monkeypatch.setattr("codex_fleet.cli.main.install_branded_plane_frontend", lambda *_args, **_kwargs: report)

    result = CliRunner().invoke(main, ["plane-frontend", "install", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert "installed" in result.output


def test_plane_frontend_restore_command(tmp_path: Path, monkeypatch) -> None:
    report = PlaneFrontendReport(
        container="plane-app-web-1",
        build_dir=tmp_path / "backup",
        backup_dir=tmp_path / "backup",
        installed=False,
        message="restored",
    )

    monkeypatch.setattr("codex_fleet.cli.main.restore_stock_plane_frontend", lambda *_args, **_kwargs: report)

    result = CliRunner().invoke(main, ["plane-frontend", "restore", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert "restored" in result.output
    assert "Branded installed: False" in result.output


def test_open_dashboard_prints_connected_login_url(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "open",
            "--repo",
            str(tmp_path),
            "--plane-url",
            "http://127.0.0.1:17300",
            "--dashboard",
            "--no-browser",
        ],
    )

    assert result.exit_code == 0
    printed = result.output.strip()
    parsed = urlparse(printed)
    query = parse_qs(parsed.query)
    redirect = urlparse(query["redirect"][0])
    redirect_fragment = parse_qs(redirect.fragment)

    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:18790"
    assert parsed.path == "/api/plane/login"
    assert query["nonce"][0]
    assert redirect.netloc == "127.0.0.1:17300"
    assert redirect.path == "/codex-fleet/dashboard"
    assert redirect_fragment["apiUrl"] == ["http://127.0.0.1:18790"]
    assert "token=" not in printed


def test_logs_lists_recent_runs(tmp_path: Path) -> None:
    store = RunStore(default_store_path(tmp_path))
    store.upsert_run(
        run_id="run-1",
        item_id="item-1",
        identifier="CF-1",
        status="human_review",
        worktree_path="/tmp/worktree",
    )

    result = CliRunner().invoke(main, ["logs", "--repo", str(tmp_path), "--limit", "1"])

    assert result.exit_code == 0
    assert "CF-1" in result.output
    assert "human_review" in result.output


def test_down_reports_runtime_and_port_shutdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "codex_fleet.cli.main.stop_runtime_process",
        lambda _repo: StopResult("runtime", True, "Stopped preview."),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.stop_loopback_ports",
        lambda _ports: [StopResult("tcp:18790", False, "No listener found.")],
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.stop_plane_runtime",
        lambda _repo: StopResult("plane", False, "No local Plane app runtime found."),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.stop_plane_app_containers",
        lambda: StopResult("plane-app containers", True, "Stopped 2 running plane-app-* container(s)."),
    )

    result = CliRunner().invoke(main, ["down", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert "STOPPED runtime" in result.output
    assert "SKIP tcp:18790" in result.output
    assert "SKIP plane" in result.output
    assert "STOPPED plane-app containers" in result.output


def test_project_add_and_list(tmp_path: Path) -> None:
    project_dir = tmp_path / "app"
    project_dir.mkdir()

    add_result = CliRunner().invoke(
        main,
        ["project", "add", str(project_dir), "--repo", str(tmp_path)],
    )
    list_result = CliRunner().invoke(main, ["project", "list", "--repo", str(tmp_path)])

    assert add_result.exit_code == 0
    assert "Project: app" in add_result.output
    assert list_result.exit_code == 0
    assert "app" in list_result.output
    assert "codex" in list_result.output
