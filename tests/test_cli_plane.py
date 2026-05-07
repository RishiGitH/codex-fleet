from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from codex_fleet.cli.main import main
from codex_fleet.config import load_config
from codex_fleet.factory import default_store_path
from codex_fleet.lifecycle import StopResult
from codex_fleet.plane_local_bootstrap import PlaneLocalBootstrapResult
from codex_fleet.plane_manager import (
    DockerStatus,
    PlaneFrontendReport,
    PlaneInstall,
    PlaneManagerError,
    PlaneSource,
    PlaneStatus,
)
from codex_fleet.store import RunStore


class FakeServer:
    url = "http://127.0.0.1:3000"

    def serve_forever(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


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
            "http://127.0.0.1:8080",
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
        lambda _url: PlaneStatus(url="http://127.0.0.1:8080", ready=False, message="Connection refused"),
    )

    result = CliRunner().invoke(main, ["plane-status", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert "Docker daemon ready: False" in result.output
    assert "Cannot connect" in result.output


def test_plane_source_status_prints_lock_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "codex_fleet.cli.main.inspect_plane_source",
        lambda _repo, _source_dir: PlaneSource(
            source_dir=tmp_path / ".codex-fleet" / "plane-src",
            exists=True,
            remote_url="https://github.com/makeplane/plane.git",
            requested_ref="4c1bdd1d625fa3f1141e8af9c15423946472069e",
            current_commit="4c1bdd1d625fa3f1141e8af9c15423946472069e",
            manifest_path=tmp_path / ".codex-fleet" / "plane-src" / ".codex-fleet-plane-source.yml",
        ),
    )

    result = CliRunner().invoke(main, ["plane-source", "--repo", str(tmp_path), "--status"])

    assert result.exit_code == 0
    assert "Locked source: https://github.com/makeplane/plane.git" in result.output
    assert "Locked ref: 4c1bdd1d625fa3f1141e8af9c15423946472069e" in result.output
    assert "Patch resource: plane-codex-fleet.patch" in result.output


def test_plane_patch_export_and_apply_commands(tmp_path: Path, monkeypatch) -> None:
    patch_path = tmp_path / "patch.diff"
    source_path = tmp_path / "plane-src"

    monkeypatch.setattr("codex_fleet.cli.main.export_plane_customization_patch", lambda *_args, **_kwargs: patch_path)
    monkeypatch.setattr("codex_fleet.cli.main.apply_plane_customization_patch", lambda *_args, **_kwargs: source_path)

    export_result = CliRunner().invoke(main, ["plane-patch", "export", "--repo", str(tmp_path)])
    apply_result = CliRunner().invoke(main, ["plane-patch", "apply", "--repo", str(tmp_path)])

    assert export_result.exit_code == 0
    assert "Exported Plane customization patch:" in export_result.output
    assert "patch.diff" in export_result.output
    assert apply_result.exit_code == 0
    assert "Applied Plane customization patch to:" in apply_result.output
    assert "plane-src" in apply_result.output


def test_plane_fork_preview_prepare_only(tmp_path: Path, monkeypatch) -> None:
    build_dir = tmp_path / ".codex-fleet" / "plane-src" / "apps" / "web" / "build" / "client"

    monkeypatch.setattr("codex_fleet.cli.main.prepare_plane_preview_build", lambda _repo: build_dir)

    result = CliRunner().invoke(main, ["plane-fork-preview", "--repo", str(tmp_path), "--prepare-only"])

    assert result.exit_code == 0
    assert "Plane fork build ready:" in result.output
    assert "build" in result.output


def test_up_reports_first_run_plane_clone_progress(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("codex_fleet.cli.main.create_local_api_server", lambda *_args, **_kwargs: FakeServer())
    monkeypatch.setattr("codex_fleet.cli.main.create_plane_preview_server", lambda *_args, **_kwargs: FakeServer())
    monkeypatch.setattr(
        "codex_fleet.cli.main.start_plane",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PlaneManagerError("Docker is not available")),
    )

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake", "--once"])

    assert result.exit_code == 0
    assert "Preparing branded Plane fork: cloning pinned Plane source" in result.output
    assert "Onboarding URL:" in result.output


def test_up_opens_project_setup_without_creating_plane_project_when_config_is_missing(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("codex_fleet.cli.main.start_plane", lambda *_args, **_kwargs: calls.append("start-plane"))
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://127.0.0.1:8080", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.prepare_plane_preview_build",
        lambda _repo: tmp_path / ".codex-fleet" / "plane-src" / "apps" / "web" / "build" / "client",
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
    monkeypatch.setattr("codex_fleet.cli.main.create_plane_preview_server", lambda *_args, **_kwargs: FakeServer())

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake", "--once"])

    assert result.exit_code == 0
    assert calls == ["start-plane", "frontend"]
    assert "without creating a project" in result.output
    assert "Onboarding URL:" in result.output


def test_up_starts_configured_loopback_plane_when_not_ready(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://127.0.0.1:8080\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: local\n"
        "  plane_project_id: project-id\n"
    )
    calls: list[str] = []

    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:8080", ready=False, message="Connection refused"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.start_plane",
        lambda _repo, url: calls.append(f"start:{url}"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://127.0.0.1:8080", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr("codex_fleet.cli.main.build_plane_client", lambda _config: object())
    monkeypatch.setattr(
        "codex_fleet.cli.main.ensure_plane_bootstrap",
        lambda _client, _states: calls.append("bootstrap"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.prepare_plane_preview_build",
        lambda _repo: tmp_path / ".codex-fleet" / "plane-src" / "apps" / "web" / "build" / "client",
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
    assert "Starting local Plane at http://127.0.0.1:8080" in result.output
    assert "Installing branded codex-fleet Plane frontend" in result.output
    assert calls == ["start:http://127.0.0.1:8080", "frontend", "bootstrap", "daemon"]


def test_up_can_skip_branded_frontend_install(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://127.0.0.1:8080\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: local\n"
        "  plane_project_id: project-id\n"
    )
    calls: list[str] = []

    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:8080", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://127.0.0.1:8080", ready=True, message="HTTP 200 at /"),
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
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://127.0.0.1:8080\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: local\n"
        "  plane_project_id: project-id\n"
    )
    calls: list[str] = []

    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:8080", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://127.0.0.1:8080", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr("codex_fleet.cli.main.build_plane_client", lambda _config: object())
    monkeypatch.setattr("codex_fleet.cli.main.ensure_plane_bootstrap", lambda _client, _states: None)
    monkeypatch.setattr(
        "codex_fleet.cli.main.prepare_plane_preview_build",
        lambda _repo: tmp_path / ".codex-fleet" / "plane-src" / "apps" / "web" / "build" / "client",
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
    assert "codex-fleet API: http://127.0.0.1:8790" in result.output
    assert "Plane board: http://127.0.0.1:8080/local/projects/project-id/issues/" in result.output
    assert "api-serve" in calls
    assert "open" in calls
    assert calls[-1] == "api-shutdown"


def test_up_matches_local_api_host_to_loopback_plane_host(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://localhost:8080\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: local\n"
        "  plane_project_id: project-id\n"
    )
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://localhost:8080", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://localhost:8080", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr("codex_fleet.cli.main.build_plane_client", lambda _config: object())
    monkeypatch.setattr("codex_fleet.cli.main.ensure_plane_bootstrap", lambda _client, _states: None)
    monkeypatch.setattr(
        "codex_fleet.cli.main.prepare_plane_preview_build",
        lambda _repo: tmp_path / ".codex-fleet" / "plane-src" / "apps" / "web" / "build" / "client",
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
    assert "codex-fleet API: http://localhost:8790" in result.output
    opened_url = next(value for kind, value in calls if kind == "open")
    assert "http://localhost:8790/api/plane/login?" in str(opened_url)
    assert "planeOrigin=http%3A%2F%2Flocalhost%3A8080" in str(opened_url)


def test_up_reports_local_plane_start_failure(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://127.0.0.1:8080\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: local\n"
        "  plane_project_id: project-id\n"
    )

    monkeypatch.setattr(
        "codex_fleet.cli.main.check_plane_url",
        lambda _url: PlaneStatus(url="http://127.0.0.1:8080", ready=False, message="Connection refused"),
    )

    def fail_start(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise PlaneManagerError("Docker daemon is not ready: Cannot connect")

    monkeypatch.setattr("codex_fleet.cli.main.start_plane", fail_start)

    result = CliRunner().invoke(main, ["up", "--repo", str(tmp_path), "--fake", "--once"])

    assert result.exit_code != 0
    assert "Local Plane could not be started" in result.output
    assert "Docker daemon is not ready" in result.output


def test_up_missing_plane_api_key_bootstraps_local_plane(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  active_states: [Ready]\n"
        "  plane_base_url: http://127.0.0.1:8080\n"
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
            "  plane_base_url: http://127.0.0.1:8080\n"
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
        lambda _url: PlaneStatus(url="http://127.0.0.1:8080", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.wait_for_plane",
        lambda _url: PlaneStatus(url="http://127.0.0.1:8080", ready=True, message="HTTP 200 at /"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.ensure_plane_bootstrap",
        lambda _client, _states: calls.append("bootstrap"),
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.prepare_plane_preview_build",
        lambda _repo: tmp_path / ".codex-fleet" / "plane-src" / "apps" / "web" / "build" / "client",
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
            "http://127.0.0.1:3000",
        ],
    )

    assert result.exit_code == 0
    assert "http://127.0.0.1:3000/codex-fleet/onboarding#" in result.output
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
    build_dir = tmp_path / ".codex-fleet" / "plane-src" / "apps" / "web" / "build" / "client"
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


def test_open_dashboard_prints_fragment_token(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "open",
            "--repo",
            str(tmp_path),
            "--plane-url",
            "http://127.0.0.1:3000",
            "--dashboard",
            "--no-browser",
        ],
    )

    assert result.exit_code == 0
    assert "http://127.0.0.1:3000/codex-fleet/projects/#" in result.output
    assert "apiUrl=" in result.output
    assert "token=" in result.output


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
        lambda _ports: [StopResult("tcp:8790", False, "No listener found.")],
    )
    monkeypatch.setattr(
        "codex_fleet.cli.main.stop_plane_runtime",
        lambda _repo: StopResult("plane", False, "No local Plane app runtime found."),
    )

    result = CliRunner().invoke(main, ["down", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert "STOPPED runtime" in result.output
    assert "SKIP tcp:8790" in result.output
    assert "SKIP plane" in result.output


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
