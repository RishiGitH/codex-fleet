from pathlib import Path

from codex_fleet.config import load_config
from codex_fleet.plane_manager import (
    DEFAULT_PLANE_SOURCE_REF,
    DEFAULT_PLANE_SOURCE_URL,
    branded_plane_frontend_status,
    check_docker_status,
    default_plane_source_lock_path,
    ensure_plane_runtime_config,
    inspect_plane_runtime,
    inspect_plane_source,
    install_branded_plane_frontend,
    load_plane_source_lock,
    require_plane_source,
    restore_stock_plane_frontend,
    start_plane,
    verify_plane_customization,
    write_plane_config,
    write_plane_source_manifest,
)


def test_write_plane_config_uses_plane_ready_only(tmp_path: Path) -> None:
    path = write_plane_config(
        tmp_path,
        base_url="http://127.0.0.1:17880",
        workspace_slug="local",
        project_id="project-id",
        api_key_ref="literal-key",
    )

    config = load_config(tmp_path, path)

    assert path == tmp_path / ".codex-fleet.yml"
    assert config.tracker.kind == "plane"
    assert config.tracker.active_states == ["Ready"]
    assert config.tracker.plane_base_url == "http://127.0.0.1:17880"
    assert config.tracker.plane_api_key == "literal-key"
    assert config.tracker.plane_workspace_slug == "local"
    assert config.tracker.plane_project_id == "project-id"


def test_inspect_plane_runtime_detects_installed_plane_app(tmp_path: Path) -> None:
    runtime = tmp_path / ".codex-fleet" / "plane-selfhost"
    runtime.mkdir(parents=True)
    (runtime / "setup.sh").write_text("#!/usr/bin/env bash\n")
    (runtime / "plane-app").mkdir()

    install = inspect_plane_runtime(tmp_path)

    assert install.installed is True
    assert install.app_dir == runtime / "plane-app"


def test_start_plane_runs_install_then_start_and_sets_port(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / ".codex-fleet" / "plane-selfhost"
    runtime.mkdir(parents=True)
    setup = runtime / "setup.sh"
    setup.write_text("#!/usr/bin/env bash\n")
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr("codex_fleet.plane_manager.ensure_plane_runtime", lambda repo: inspect_plane_runtime(repo))
    monkeypatch.setattr("codex_fleet.plane_manager._require", lambda binary, message: None)
    monkeypatch.setattr(
        "codex_fleet.plane_manager.check_docker_status",
        lambda: type("Docker", (), {"daemon_ready": True, "message": "ready"})(),
    )

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((command[-1], kwargs.get("input")))
        if kwargs.get("input") == "1\n8\n":
            app_dir = runtime / "plane-app"
            app_dir.mkdir()
            (app_dir / "plane.env").write_text("APP_DOMAIN=localhost\nLISTEN_HTTP_PORT=80\n")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    install = start_plane(tmp_path, url="http://127.0.0.1:8765")

    assert install.installed is True
    assert [call[1] for call in calls] == ["1\n8\n", "2\n8\n"]
    assert (runtime / "plane-app" / "plane.env").read_text() == (
        "APP_DOMAIN=127.0.0.1:8765\n"
        "LISTEN_HTTP_PORT=8765\n"
        "API_KEY_RATE_LIMIT=1000/minute\n"
    )


def test_start_plane_restarts_api_services_when_rate_limit_env_changes(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / ".codex-fleet" / "plane-selfhost"
    app_dir = runtime / "plane-app"
    app_dir.mkdir(parents=True)
    (runtime / "setup.sh").write_text("#!/usr/bin/env bash\n")
    (app_dir / "docker-compose.yaml").write_text("services: {}\n")
    (app_dir / "plane.env").write_text("APP_DOMAIN=localhost\nLISTEN_HTTP_PORT=80\nAPI_KEY_RATE_LIMIT=60/minute\n")
    commands: list[list[str]] = []

    monkeypatch.setattr("codex_fleet.plane_manager.ensure_plane_runtime", lambda repo: inspect_plane_runtime(repo))
    monkeypatch.setattr("codex_fleet.plane_manager._require", lambda binary, message: None)
    monkeypatch.setattr(
        "codex_fleet.plane_manager.check_docker_status",
        lambda: type("Docker", (), {"daemon_ready": True, "message": "ready"})(),
    )

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        commands.append(command)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    start_plane(tmp_path, url="http://127.0.0.1:8765")

    assert any(command[-3:] == ["api", "worker", "beat-worker"] for command in commands)
    restart_command = next(command for command in commands if command[-3:] == ["api", "worker", "beat-worker"])
    assert "--env-file" in restart_command
    assert str(app_dir / "plane.env") in restart_command
    assert "API_KEY_RATE_LIMIT=1000/minute" in (app_dir / "plane.env").read_text()


def test_plane_runtime_config_restarts_stale_running_api_env(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / ".codex-fleet" / "plane-selfhost"
    app_dir = runtime / "plane-app"
    app_dir.mkdir(parents=True)
    (runtime / "setup.sh").write_text("#!/usr/bin/env bash\n")
    (app_dir / "docker-compose.yaml").write_text("services: {}\n")
    (app_dir / "plane.env").write_text(
        "APP_DOMAIN=127.0.0.1:17880\nLISTEN_HTTP_PORT=8080\nAPI_KEY_RATE_LIMIT=1000/minute\n"
    )
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        commands.append(command)

        class Result:
            returncode = 0
            stderr = ""

            def __init__(self, stdout: str = "") -> None:
                self.stdout = stdout

        if command[-4:] == ["api", "sh", "-lc", 'printf %s "$API_KEY_RATE_LIMIT"']:
            return Result("60/minute")
        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    changed = ensure_plane_runtime_config(tmp_path, url="http://127.0.0.1:17880")

    assert changed is True
    restart_command = next(command for command in commands if command[-3:] == ["api", "worker", "beat-worker"])
    assert "--env-file" in restart_command
    assert str(app_dir / "plane.env") in restart_command


def test_start_plane_fails_when_docker_daemon_is_down(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / ".codex-fleet" / "plane-selfhost"
    runtime.mkdir(parents=True)
    (runtime / "setup.sh").write_text("#!/usr/bin/env bash\n")
    (runtime / "plane-app").mkdir()

    monkeypatch.setattr("codex_fleet.plane_manager.ensure_plane_runtime", lambda repo: inspect_plane_runtime(repo))
    monkeypatch.setattr("codex_fleet.plane_manager._require", lambda binary, message: None)
    monkeypatch.setattr(
        "codex_fleet.plane_manager.check_docker_status",
        lambda: type("Docker", (), {"daemon_ready": False, "message": "Cannot connect"})(),
    )

    try:
        start_plane(tmp_path)
    except RuntimeError as exc:
        assert "Docker daemon is not ready" in str(exc)
    else:
        raise AssertionError("start_plane should fail when Docker daemon is down")


def test_check_docker_status_reports_daemon_failure(monkeypatch) -> None:
    monkeypatch.setattr("codex_fleet.plane_manager.shutil.which", lambda _binary: "/usr/bin/docker")

    class Result:
        returncode = 1
        stdout = ""
        stderr = "Cannot connect to the Docker daemon"

    monkeypatch.setattr("codex_fleet.plane_manager.subprocess.run", lambda *_args, **_kwargs: Result())

    status = check_docker_status()

    assert status.available is True
    assert status.daemon_ready is False
    assert "Cannot connect" in status.message


def test_install_branded_plane_frontend_backs_up_and_copies_build(monkeypatch, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "index.html").write_text("codex-fleet\n")
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "codex_fleet.plane_manager.check_docker_status",
        lambda: type("Docker", (), {"daemon_ready": True, "message": "ready"})(),
    )

    def fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(command)
        stdout = ""
        if command[:3] == ["docker", "ps", "--format"]:
            stdout = "plane-app-web-1\n"
        if command[:2] == ["docker", "cp"] and command[2] == "plane-app-web-1:/usr/share/nginx/html/.":
            target = Path(command[3])
            target.mkdir(parents=True, exist_ok=True)
            (target / "index.html").write_text("stock plane\n")

        class Result:
            returncode = 0
            stderr = ""

            def __init__(self, stdout: str) -> None:
                self.stdout = stdout

        return Result(stdout)

    monkeypatch.setattr("codex_fleet.plane_manager.subprocess.run", fake_run)

    report = install_branded_plane_frontend(tmp_path, build_dir)

    assert report.installed is True
    assert report.container == "plane-app-web-1"
    assert (report.backup_dir / "index.html").read_text() == "stock plane\n"
    assert ["docker", "cp", f"{build_dir}/.", "plane-app-web-1:/usr/share/nginx/html/"] in calls
    assert ["docker", "exec", "plane-app-web-1", "sh", "-lc", "nginx -s reload"] in calls


def test_restore_stock_plane_frontend_copies_backup(monkeypatch, tmp_path: Path) -> None:
    backup = tmp_path / ".codex-fleet" / "plane-selfhost" / "web-static-stock-backup"
    backup.mkdir(parents=True)
    (backup / "index.html").write_text("stock plane\n")
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "codex_fleet.plane_manager.check_docker_status",
        lambda: type("Docker", (), {"daemon_ready": True, "message": "ready"})(),
    )

    def fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(command)
        stdout = "plane-app-web-1\n" if command[:3] == ["docker", "ps", "--format"] else ""

        class Result:
            returncode = 0
            stderr = ""

            def __init__(self, stdout: str) -> None:
                self.stdout = stdout

        return Result(stdout)

    monkeypatch.setattr("codex_fleet.plane_manager.subprocess.run", fake_run)

    report = restore_stock_plane_frontend(tmp_path)

    assert report.installed is False
    assert ["docker", "cp", f"{backup}/.", "plane-app-web-1:/usr/share/nginx/html/"] in calls


def test_branded_plane_frontend_status_detects_brand(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "codex_fleet.plane_manager.check_docker_status",
        lambda: type("Docker", (), {"daemon_ready": True, "message": "ready"})(),
    )

    def fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        stdout = "plane-app-web-1\n" if command[:3] == ["docker", "ps", "--format"] else ""
        returncode = 0

        class Result:
            stderr = ""

            def __init__(self, stdout: str, returncode: int) -> None:
                self.stdout = stdout
                self.returncode = returncode

        return Result(stdout, returncode)

    monkeypatch.setattr("codex_fleet.plane_manager.subprocess.run", fake_run)

    report = branded_plane_frontend_status(tmp_path)

    assert report.installed is True
    assert "detected" in report.message


def test_inspect_plane_source_reports_missing_checkout(tmp_path: Path) -> None:
    source = inspect_plane_source(tmp_path)

    assert source.exists is False
    assert source.source_dir == tmp_path / "apps" / "plane"


def test_default_plane_source_lock_matches_runtime_defaults() -> None:
    lock = load_plane_source_lock()

    assert default_plane_source_lock_path().exists()
    assert lock.source_url == DEFAULT_PLANE_SOURCE_URL
    assert lock.ref == DEFAULT_PLANE_SOURCE_REF
    assert "apps-plane" in lock.strategy


def test_write_plane_source_manifest_requires_existing_apps_plane(tmp_path: Path) -> None:
    root = tmp_path / "apps" / "plane"
    (root / "apps" / "web").mkdir(parents=True)
    (root / "package.json").write_text("{}\n")

    source = write_plane_source_manifest(
        tmp_path,
        source_url="https://example.test/plane.git",
        ref="abc123",
    )

    assert source.exists is True
    assert source.remote_url == "https://example.test/plane.git"
    assert source.requested_ref == "abc123"
    assert source.current_commit == "abc123"
    manifest = (source.source_dir / ".codex-fleet-plane-source.yml").read_text()
    assert "requested_ref: abc123" in manifest
    assert "lock_ref:" in manifest


def test_require_plane_source_rejects_stale_runtime_source(tmp_path: Path) -> None:
    root = tmp_path / "apps" / "plane"
    (root / "apps" / "web").mkdir(parents=True)
    (root / "package.json").write_text("{}\n")
    (tmp_path / ".codex-fleet" / "plane-src").mkdir(parents=True)

    try:
        require_plane_source(tmp_path)
    except RuntimeError as exc:
        assert ".codex-fleet" in str(exc)
        assert "apps/plane" in str(exc)
    else:
        raise AssertionError("stale runtime Plane source should be rejected")


def test_verify_plane_customization_reports_required_branding(tmp_path: Path) -> None:
    root = tmp_path / "apps" / "plane"
    (root / ".git").mkdir(parents=True)
    (root / "package.json").write_text("{}\n")
    (root / "apps/web/app/codex-fleet").mkdir(parents=True)
    (root / "apps/web/public").mkdir(parents=True)
    (root / "apps/web").mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text("Plane must not shell out\n")
    (root / "apps/web/app/root.tsx").write_text("codex-fleet\n")
    (root / "apps/web/app/layout.tsx").write_text("codex-fleet\n")
    (root / "apps/web/core/components/instance").mkdir(parents=True)
    (root / "apps/web/core/components/instance/not-ready-view.tsx").write_text(
        "codex-fleet\nOpen Codex Fleet\n/codex-fleet/dashboard\n/codex-fleet/onboarding\n"
    )
    (root / "apps/web/core/lib/wrappers").mkdir(parents=True)
    (root / "apps/web/core/lib/wrappers/instance-wrapper.tsx").write_text('startsWith("/codex-fleet")\n')
    (root / "apps/web/app/(home)").mkdir(parents=True)
    (root / "apps/web/app/(home)/page.tsx").write_text(
        "codex-fleet Star us on GitHub https://github.com/RishiGitH/codex-fleet Open project dashboard /codex-fleet/dashboard Connection setup Add or create projects from the dashboard's Add Project button\n"
    )
    (root / "apps/web/app/(all)/[workspaceSlug]/(projects)").mkdir(parents=True)
    (root / "apps/web/app/(all)/[workspaceSlug]/(projects)/star-us-link.tsx").write_text(
        "Star on GitHub\nhttps://github.com/RishiGitH/codex-fleet\n"
    )
    (root / "apps/web/app/routes").mkdir(parents=True)
    (root / "apps/web/app/routes/core.ts").write_text(":workspaceSlug/settings/projects/:projectId/codex-fleet\n")
    (root / "apps/web/app/routes/extended.ts").write_text("codex-fleet/onboarding\ncodex-fleet/dashboard\n")
    (root / "apps/web/app/codex-fleet/local-api.ts").write_text(
        "/api/work-items/\n/api/runs\n/api/folders/pick\n/api/plane/login-url\n/api/plane/connect\nsessionExchangePromise\nCodexFleetHarnessScan\n"
    )
    (root / "apps/web/app/codex-fleet/local-session-bootstrap.tsx").write_text("ensureCodexFleetLocalConnection\n")
    (root / "apps/web/app/root.tsx").write_text("codex-fleet\nCodexFleetLocalSessionBootstrap\n")
    (root / "apps/web/app/codex-fleet/onboarding.tsx").write_text(
        "CodexFleetLocalApi\nChoose Folder\nharness.scan.commands\n"
    )
    (root / "apps/web/app/codex-fleet/dashboard.tsx").write_text(
        "Run Ready\nRun with Codex\nChoose Folder\nCreate project\nproject_type\nharness.scan.commands\nmin-h-dvh\n"
    )
    (root / "apps/web/ce/components/projects/create").mkdir(parents=True)
    (root / "apps/web/ce/components/projects/create/root.tsx").write_text(
        "Choose folder\nCodex workspace setup\nWhat should Codex build first?\nAutomation mode\nMax depth\nworkflow_mode\napply_harness\nCreate new project\nproject_type\nlocalProjectNotice\nCodexFleetLocalApi\nReconnect Codex Fleet\nplaneProjectId\n"
    )
    (root / "apps/web/app/codex-fleet/work-item-run-panel.tsx").write_text("Run with Codex\nAgent proposed\n")
    (root / "apps/web/app/codex-fleet/project-surfaces.tsx").write_text(
        "apps/plane-codex-fleet-mission-control-v3\nLocal Mission Control\nAgent roster\nRaw settings\n"
    )
    (root / "apps/web/core/components/issues/issue-modal").mkdir(parents=True)
    (root / "apps/web/core/components/issues/issue-modal/form.tsx").write_text(
        "Codex task settings\ndata-codex-fleet-task-settings\nAutomation mode\nplan_execute\n"
    )
    (root / "apps/web/ce/components/sidebar").mkdir(parents=True)
    (root / "apps/web/ce/components/sidebar/project-navigation-root.tsx").write_text(
        "Fleet Logs\nAgents\nRuns\nArtifacts\nCodex Settings\n"
    )
    surface_root = root / "apps/web/app/(all)/[workspaceSlug]/(projects)/projects/(detail)/[projectId]"
    for name in ("fleet-logs", "agents", "runs", "artifacts", "codex-settings"):
        (surface_root / name).mkdir(parents=True, exist_ok=True)
        (surface_root / name / "page.tsx").write_text(f"{name}\n")
    (root / "apps/web/core/components/issues/issue-detail").mkdir(parents=True)
    (root / "apps/web/core/components/issues/issue-detail/main-content.tsx").write_text("CodexFleetWorkItemRunPanel\n")
    (root / "apps/web/core/components/issues/issue-layouts/kanban").mkdir(parents=True)
    (root / "apps/web/core/components/issues/issue-layouts/kanban/block.tsx").write_text(
        "CodexFleetWorkItemRunCompact\n"
    )
    (root / "apps/web/core/components/issues/issue-layouts/list").mkdir(parents=True)
    (root / "apps/web/core/components/issues/issue-layouts/list/block.tsx").write_text(
        "CodexFleetWorkItemRunCompact\n"
    )
    (root / "apps/web/core/components/project").mkdir(parents=True)
    (root / "apps/web/core/components/common").mkdir(parents=True)
    (root / "apps/web/core/components/settings/project/sidebar").mkdir(parents=True)
    (root / "apps/web/core/components/project/card.tsx").write_text("codex-fleet starter project\ncontrol center\n")
    (root / "apps/web/core/components/common/logo-spinner.tsx").write_text("codexFleetOrbit\n/codex-fleet-logo.svg\n")
    (root / "apps/web/core/components/settings/project/sidebar/item-categories.tsx").write_text("codex_fleet\n")
    (root / "apps/web/core/components/settings/project/sidebar/item-icon.tsx").write_text("codex_fleet\n")
    (root / "apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/codex-fleet").mkdir(parents=True)
    (root / "apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/codex-fleet/page.tsx").write_text(
        "codex-fleet project settings\n"
    )
    (root / "apps/web/public/codex-fleet-logo.svg").write_text("<svg />\n")
    (root / "apps/api/plane/seeds/data").mkdir(parents=True)
    (root / "apps/api/plane/seeds/data/projects.json").write_text('[{"name":"codex-fleet starter project"}]\n')
    (root / "apps/api/plane/seeds/data/issues.json").write_text("codex-fleet\n")
    (root / "apps/api/plane/seeds/data/cycles.json").write_text("codex-fleet\n")
    (root / "apps/api/plane/seeds/data/pages.json").write_text("codex-fleet\n")
    (root / "packages/i18n/src/locales/en").mkdir(parents=True)
    (root / "packages/constants/src/settings").mkdir(parents=True)
    (root / "packages/types/src").mkdir(parents=True)
    (root / "packages/constants/src/settings/project.ts").write_text("codex_fleet\n")
    (root / "packages/types/src/settings.ts").write_text("codex_fleet\n")
    (root / "packages/i18n/src/locales/en/translations.ts").write_text('star_us_on_github: "codex-fleet"\n')
    (root / "apps/web/core/components/account/auth-forms").mkdir(parents=True)
    (root / "apps/web/core/components/account/auth-forms/auth-header.tsx").write_text("codex-fleet\n")
    (root / "apps/web/app/error").mkdir(parents=True)
    (root / "apps/web/app/error/prod.tsx").write_text("codex-fleet\n")
    (root / "apps/web/app/(all)/create-workspace").mkdir(parents=True)
    (root / "apps/web/app/(all)/create-workspace/page.tsx").write_text("codex-fleet-logo\n")
    (root / "apps/web/app/(all)/invitations").mkdir(parents=True)
    (root / "apps/web/app/(all)/invitations/page.tsx").write_text("codex-fleet-logo\n")
    (root / "apps/web/app/(all)/workspace-invitations").mkdir(parents=True)
    (root / "apps/web/app/(all)/workspace-invitations/page.tsx").write_text("codex-fleet\n")
    manifest = '{"name":"codex-fleet","icons":[{"src":"/codex-fleet-logo.svg"}]}'
    (root / "apps/web/public/manifest.json").write_text(manifest)
    (root / "apps/web/public/site.webmanifest.json").write_text(manifest)
    (root / "apps/web/manifest.json").write_text(manifest)
    (root / "apps/web/public/sw.js").write_text("registration.unregister\n")
    (root / "apps/web/app/entry.client.tsx").write_text("@/lib/polyfills\n")
    (root / "apps/web/core/components/issues").mkdir(parents=True, exist_ok=True)
    (root / "apps/web/core/components/issues/filters.tsx").write_text("Fleet Logs\n")
    (root / "apps/web/core/components/analytics/work-items/modal").mkdir(parents=True)
    (root / "apps/web/core/components/analytics/work-items/modal/header.tsx").write_text("Fleet Logs for\n")
    (root / "apps/web/core/components/analytics/work-items/modal/content.tsx").write_text("Task tree\n")

    report = verify_plane_customization(tmp_path)

    assert report.ok is True
