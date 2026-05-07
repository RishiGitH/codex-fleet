from pathlib import Path

from codex_fleet.config import load_config
from codex_fleet.plane_manager import (
    DEFAULT_PLANE_SOURCE_REF,
    DEFAULT_PLANE_SOURCE_URL,
    apply_plane_customization_patch,
    branded_plane_frontend_status,
    check_docker_status,
    default_plane_patch_path,
    default_plane_source_lock_path,
    ensure_plane_runtime_config,
    ensure_plane_source,
    export_plane_customization_patch,
    inspect_plane_runtime,
    inspect_plane_source,
    install_branded_plane_frontend,
    load_plane_source_lock,
    restore_stock_plane_frontend,
    start_plane,
    verify_plane_customization,
    write_plane_config,
)


def test_write_plane_config_uses_plane_ready_only(tmp_path: Path) -> None:
    path = write_plane_config(
        tmp_path,
        base_url="http://127.0.0.1:8080",
        workspace_slug="local",
        project_id="project-id",
        api_key_ref="literal-key",
    )

    config = load_config(tmp_path, path)

    assert path == tmp_path / ".codex-fleet.yml"
    assert config.tracker.kind == "plane"
    assert config.tracker.active_states == ["Ready"]
    assert config.tracker.plane_base_url == "http://127.0.0.1:8080"
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
        "APP_DOMAIN=127.0.0.1:8080\nLISTEN_HTTP_PORT=8080\nAPI_KEY_RATE_LIMIT=1000/minute\n"
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

    changed = ensure_plane_runtime_config(tmp_path, url="http://127.0.0.1:8080")

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
    assert source.source_dir == tmp_path / ".codex-fleet" / "plane-src"


def test_default_plane_patch_path_falls_back_to_bundled_resource(tmp_path: Path) -> None:
    patch = default_plane_patch_path(tmp_path)

    assert patch.exists()
    assert "codex-fleet/dashboard" in patch.read_text()


def test_default_plane_source_lock_matches_runtime_defaults() -> None:
    lock = load_plane_source_lock()

    assert default_plane_source_lock_path().exists()
    assert lock.source_url == DEFAULT_PLANE_SOURCE_URL
    assert lock.ref == DEFAULT_PLANE_SOURCE_REF
    assert lock.patch_resource == "plane-codex-fleet.patch"
    assert "runtime" in lock.strategy


def test_ensure_plane_source_clones_pins_and_writes_manifest(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr("codex_fleet.plane_manager._require", lambda binary, message: None)

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(command)
        cwd = kwargs.get("cwd")
        if command[:2] == ["git", "clone"]:
            target = Path(command[-1])
            (target / ".git").mkdir(parents=True)
        stdout = ""
        if command[:4] == ["git", "config", "--get", "remote.origin.url"]:
            stdout = "https://example.test/plane.git\n"
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            stdout = "abc123\n"
        assert cwd is None or isinstance(cwd, Path)

        class Result:
            returncode = 0
            stderr = ""

            def __init__(self, stdout: str) -> None:
                self.stdout = stdout

        return Result(stdout)

    monkeypatch.setattr("subprocess.run", fake_run)

    source = ensure_plane_source(
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
    assert "patch_resource: plane-codex-fleet.patch" in manifest
    assert ["git", "fetch", "--depth", "1", "origin", "abc123"] in calls
    assert ["git", "checkout", "abc123"] in calls


def test_verify_plane_customization_reports_required_branding(tmp_path: Path) -> None:
    root = tmp_path / ".codex-fleet" / "plane-src"
    (root / ".git").mkdir(parents=True)
    (root / "apps/web/app/codex-fleet").mkdir(parents=True)
    (root / "apps/web/public").mkdir(parents=True)
    (root / "apps/web").mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text("Plane must not shell out\n")
    (root / "apps/web/app/root.tsx").write_text("codex-fleet\n")
    (root / "apps/web/app/layout.tsx").write_text("codex-fleet\n")
    (root / "apps/web/app/(home)").mkdir(parents=True)
    (root / "apps/web/app/(home)/page.tsx").write_text(
        "codex-fleet Star us on GitHub https://github.com/RishiGitH/codex-fleet Open project dashboard /codex-fleet/projects/ Connection setup Add or create projects from the dashboard's Add Project button\n"
    )
    (root / "apps/web/app/(all)/[workspaceSlug]/(projects)").mkdir(parents=True)
    (root / "apps/web/app/(all)/[workspaceSlug]/(projects)/star-us-link.tsx").write_text(
        "Star on GitHub\nhttps://github.com/RishiGitH/codex-fleet\n"
    )
    (root / "apps/web/app/routes").mkdir(parents=True)
    (root / "apps/web/app/routes/core.ts").write_text(":workspaceSlug/settings/projects/:projectId/codex-fleet\n")
    (root / "apps/web/app/routes/extended.ts").write_text("codex-fleet/onboarding\ncodex-fleet/dashboard\n")
    (root / "apps/web/app/codex-fleet/local-api.ts").write_text(
        "/api/work-items/\n/api/runs\n/api/folders/pick\nCodexFleetHarnessScan\n"
    )
    (root / "apps/web/app/codex-fleet/onboarding.tsx").write_text(
        "CodexFleetLocalApi\nChoose Folder\nharness.scan.commands\n"
    )
    (root / "apps/web/app/codex-fleet/dashboard.tsx").write_text(
        "Run Ready\nRun with Codex\nChoose Folder\nCreate project\nproject_type\nharness.scan.commands\nmin-h-dvh\n"
    )
    (root / "apps/web/ce/components/projects/create").mkdir(parents=True)
    (root / "apps/web/ce/components/projects/create/root.tsx").write_text(
        "Choose folder\nCodex workspace setup\nWhat should Codex build first?\nagent_task_mode\napply_harness\nCreate new project\nproject_type\nlocalProjectNotice\nCodexFleetLocalApi\nplaneProjectId\n"
    )
    (root / "apps/web/app/codex-fleet/work-item-run-panel.tsx").write_text("Run with Codex\nAgent proposed\n")
    (root / "apps/web/core/components/issues/issue-modal").mkdir(parents=True)
    (root / "apps/web/core/components/issues/issue-modal/form.tsx").write_text(
        "Codex task settings\ndata-codex-fleet-task-settings\n"
    )
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

    report = verify_plane_customization(tmp_path)

    assert report.ok is True


def test_plane_customization_patch_round_trips(tmp_path: Path) -> None:
    source = tmp_path / ".codex-fleet" / "plane-src"
    _init_minimal_plane_repo(source)
    (source / "AGENTS.md").write_text("Plane must not shell out\n")
    (source / "apps/web/app/root.tsx").write_text("codex-fleet\n")
    (source / "apps/web/app/layout.tsx").write_text("codex-fleet\n")
    (source / "apps/web/app/(home)").mkdir(parents=True, exist_ok=True)
    (source / "apps/web/app/(home)/page.tsx").write_text(
        "codex-fleet Star us on GitHub https://github.com/RishiGitH/codex-fleet Open project dashboard /codex-fleet/projects/ Connection setup Add or create projects from the dashboard's Add Project button\n"
    )
    (source / "apps/web/app/(all)/[workspaceSlug]/(projects)").mkdir(parents=True, exist_ok=True)
    (source / "apps/web/app/(all)/[workspaceSlug]/(projects)/star-us-link.tsx").write_text(
        "Star on GitHub\nhttps://github.com/RishiGitH/codex-fleet\n"
    )
    (source / "apps/web/app/routes/core.ts").write_text(":workspaceSlug/settings/projects/:projectId/codex-fleet\n")
    (source / "apps/web/app/codex-fleet").mkdir(parents=True)
    (source / "apps/web/app/codex-fleet/local-api.ts").write_text("CodexFleetHarnessScan\n/api/folders/pick\n")
    (source / "apps/web/app/codex-fleet/onboarding.tsx").write_text("harness.scan.commands\nChoose Folder\n")
    (source / "apps/web/app/codex-fleet/dashboard.tsx").write_text(
        "Run Ready\nRun with Codex\nharness.scan.commands\nChoose Folder\nCreate project\nproject_type\nmin-h-dvh\n"
    )
    (source / "apps/web/ce/components/projects/create").mkdir(parents=True)
    (source / "apps/web/ce/components/projects/create/root.tsx").write_text(
        "Choose folder\nCodex workspace setup\nWhat should Codex build first?\nagent_task_mode\napply_harness\nCreate new project\nproject_type\nlocalProjectNotice\nCodexFleetLocalApi\nplaneProjectId\n"
    )
    (source / "apps/web/app/codex-fleet/work-item-run-panel.tsx").write_text("Run with Codex\nAgent proposed\n")
    (source / "apps/web/core/components/issues/issue-modal").mkdir(parents=True)
    (source / "apps/web/core/components/issues/issue-modal/form.tsx").write_text(
        "Codex task settings\ndata-codex-fleet-task-settings\n"
    )
    (source / "apps/web/core/components/issues/issue-detail").mkdir(parents=True)
    (source / "apps/web/core/components/issues/issue-detail/main-content.tsx").write_text("CodexFleetWorkItemRunPanel\n")
    (source / "apps/web/core/components/issues/issue-layouts/kanban").mkdir(parents=True)
    (source / "apps/web/core/components/issues/issue-layouts/kanban/block.tsx").write_text(
        "CodexFleetWorkItemRunCompact\n"
    )
    (source / "apps/web/core/components/issues/issue-layouts/list").mkdir(parents=True)
    (source / "apps/web/core/components/issues/issue-layouts/list/block.tsx").write_text(
        "CodexFleetWorkItemRunCompact\n"
    )
    (source / "apps/web/core/components/project").mkdir(parents=True, exist_ok=True)
    (source / "apps/web/core/components/common").mkdir(parents=True, exist_ok=True)
    (source / "apps/web/core/components/settings/project/sidebar").mkdir(parents=True, exist_ok=True)
    (source / "apps/web/core/components/project/card.tsx").write_text("codex-fleet starter project\ncontrol center\n")
    (source / "apps/web/core/components/common/logo-spinner.tsx").write_text("codexFleetOrbit\n/codex-fleet-logo.svg\n")
    (source / "apps/web/core/components/settings/project/sidebar/item-icon.tsx").write_text("codex_fleet\n")
    (source / "apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/codex-fleet").mkdir(
        parents=True,
        exist_ok=True,
    )
    (source / "apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/codex-fleet/page.tsx").write_text(
        "codex-fleet project settings\n"
    )
    (source / "apps/web/public/codex-fleet-logo.svg").write_text("<svg />\n")
    (source / "apps/api/plane/seeds/data").mkdir(parents=True, exist_ok=True)
    (source / "apps/api/plane/seeds/data/projects.json").write_text('[{"name":"codex-fleet starter project"}]\n')
    (source / "apps/api/plane/seeds/data/issues.json").write_text("codex-fleet\n")
    (source / "apps/api/plane/seeds/data/cycles.json").write_text("codex-fleet\n")
    (source / "apps/api/plane/seeds/data/pages.json").write_text("codex-fleet\n")
    (source / "packages/i18n/src/locales/en").mkdir(parents=True, exist_ok=True)
    (source / "packages/constants/src/settings").mkdir(parents=True, exist_ok=True)
    (source / "packages/types/src").mkdir(parents=True, exist_ok=True)
    (source / "packages/constants/src/settings/project.ts").write_text("codex_fleet\n")
    (source / "packages/types/src/settings.ts").write_text("codex_fleet\n")
    (source / "packages/i18n/src/locales/en/translations.ts").write_text('star_us_on_github: "codex-fleet"\n')
    (source / "apps/web/core/components/account/auth-forms").mkdir(parents=True)
    (source / "apps/web/core/components/account/auth-forms/auth-header.tsx").write_text("codex-fleet\n")
    (source / "apps/web/app/error").mkdir(parents=True)
    (source / "apps/web/app/error/prod.tsx").write_text("codex-fleet\n")
    (source / "apps/web/app/(all)/create-workspace").mkdir(parents=True)
    (source / "apps/web/app/(all)/create-workspace/page.tsx").write_text("codex-fleet-logo\n")
    (source / "apps/web/app/(all)/invitations").mkdir(parents=True)
    (source / "apps/web/app/(all)/invitations/page.tsx").write_text("codex-fleet-logo\n")
    (source / "apps/web/app/(all)/workspace-invitations").mkdir(parents=True)
    (source / "apps/web/app/(all)/workspace-invitations/page.tsx").write_text("codex-fleet\n")

    patch = export_plane_customization_patch(tmp_path)

    fresh = tmp_path / "fresh-plane"
    _init_minimal_plane_repo(fresh)
    apply_plane_customization_patch(tmp_path, source_dir=fresh, patch_path=patch)

    assert (fresh / "apps/web/app/codex-fleet/dashboard.tsx").read_text() == (
        "Run Ready\nRun with Codex\nharness.scan.commands\nChoose Folder\nCreate project\nproject_type\nmin-h-dvh\n"
    )
    assert "CodexFleetHarnessScan" in (fresh / "apps/web/app/codex-fleet/local-api.ts").read_text()
    assert "/api/folders/pick" in (fresh / "apps/web/app/codex-fleet/local-api.ts").read_text()
    assert "harness.scan.commands" in (fresh / "apps/web/app/codex-fleet/onboarding.tsx").read_text()
    assert "Choose Folder" in (fresh / "apps/web/app/codex-fleet/onboarding.tsx").read_text()
    fresh_create = (fresh / "apps/web/ce/components/projects/create/root.tsx").read_text()
    assert "Choose folder" in fresh_create
    assert "CodexFleetLocalApi" in fresh_create
    assert "planeProjectId" in fresh_create
    assert "localProjectNotice" in fresh_create
    assert (fresh / "apps/web/app/codex-fleet/work-item-run-panel.tsx").exists()
    assert "Codex task settings" in (fresh / "apps/web/core/components/issues/issue-modal/form.tsx").read_text()
    assert "data-codex-fleet-task-settings" in (
        fresh / "apps/web/core/components/issues/issue-modal/form.tsx"
    ).read_text()
    assert "CodexFleetWorkItemRunCompact" in (
        fresh / "apps/web/core/components/issues/issue-layouts/kanban/block.tsx"
    ).read_text()
    assert "CodexFleetWorkItemRunCompact" in (
        fresh / "apps/web/core/components/issues/issue-layouts/list/block.tsx"
    ).read_text()
    assert (fresh / "apps/web/public/codex-fleet-logo.svg").exists()
    assert "codex-fleet" in (fresh / "apps/web/app/root.tsx").read_text()
    assert "codex-fleet" in (fresh / "apps/web/app/(home)/page.tsx").read_text()
    assert "codexFleetOrbit" in (fresh / "apps/web/core/components/common/logo-spinner.tsx").read_text()
    assert "Plane Demo Project" not in (fresh / "apps/api/plane/seeds/data/projects.json").read_text()
    assert "codex-fleet starter project" in (fresh / "apps/web/core/components/project/card.tsx").read_text()


def test_plane_customization_patch_apply_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / ".codex-fleet" / "plane-src"
    _init_minimal_plane_repo(source)
    (source / "AGENTS.md").write_text("Plane must not shell out\n")
    (source / "apps/web/app/root.tsx").write_text("codex-fleet\n")
    (source / "apps/web/app/layout.tsx").write_text("codex-fleet\n")
    (source / "apps/web/app/(home)").mkdir(parents=True, exist_ok=True)
    (source / "apps/web/app/(home)/page.tsx").write_text(
        "codex-fleet Star us on GitHub https://github.com/RishiGitH/codex-fleet Open project dashboard /codex-fleet/projects/ Connection setup Add or create projects from the dashboard's Add Project button\n"
    )
    (source / "apps/web/app/(all)/[workspaceSlug]/(projects)").mkdir(parents=True, exist_ok=True)
    (source / "apps/web/app/(all)/[workspaceSlug]/(projects)/star-us-link.tsx").write_text(
        "Star on GitHub\nhttps://github.com/RishiGitH/codex-fleet\n"
    )
    (source / "apps/web/app/routes/core.ts").write_text(":workspaceSlug/settings/projects/:projectId/codex-fleet\n")
    (source / "apps/web/app/routes/extended.ts").write_text("codex-fleet/onboarding\ncodex-fleet/dashboard\n")
    (source / "apps/web/app/codex-fleet").mkdir(parents=True)
    (source / "apps/web/app/codex-fleet/local-api.ts").write_text(
        "/api/work-items/\n/api/runs\n/api/folders/pick\nCodexFleetHarnessScan\n"
    )
    (source / "apps/web/app/codex-fleet/onboarding.tsx").write_text("CodexFleetLocalApi\nChoose Folder\nharness.scan.commands\n")
    (source / "apps/web/app/codex-fleet/dashboard.tsx").write_text(
        "Run Ready\nRun with Codex\nChoose Folder\nCreate project\nproject_type\nharness.scan.commands\nmin-h-dvh\n"
    )
    (source / "apps/web/ce/components/projects/create").mkdir(parents=True)
    (source / "apps/web/ce/components/projects/create/root.tsx").write_text(
        "Choose folder\nCodex workspace setup\nWhat should Codex build first?\nagent_task_mode\napply_harness\nCreate new project\nproject_type\nlocalProjectNotice\nCodexFleetLocalApi\nplaneProjectId\n"
    )
    (source / "apps/web/app/codex-fleet/work-item-run-panel.tsx").write_text("Run with Codex\nAgent proposed\n")
    (source / "apps/web/core/components/issues/issue-modal").mkdir(parents=True)
    (source / "apps/web/core/components/issues/issue-modal/form.tsx").write_text(
        "Codex task settings\ndata-codex-fleet-task-settings\n"
    )
    (source / "apps/web/core/components/issues/issue-detail").mkdir(parents=True)
    (source / "apps/web/core/components/issues/issue-detail/main-content.tsx").write_text("CodexFleetWorkItemRunPanel\n")
    (source / "apps/web/core/components/issues/issue-layouts/kanban").mkdir(parents=True)
    (source / "apps/web/core/components/issues/issue-layouts/kanban/block.tsx").write_text(
        "CodexFleetWorkItemRunCompact\n"
    )
    (source / "apps/web/core/components/issues/issue-layouts/list").mkdir(parents=True)
    (source / "apps/web/core/components/issues/issue-layouts/list/block.tsx").write_text(
        "CodexFleetWorkItemRunCompact\n"
    )
    (source / "apps/web/core/components/project").mkdir(parents=True, exist_ok=True)
    (source / "apps/web/core/components/common").mkdir(parents=True, exist_ok=True)
    (source / "apps/web/core/components/settings/project/sidebar").mkdir(parents=True, exist_ok=True)
    (source / "apps/web/core/components/project/card.tsx").write_text("codex-fleet starter project\ncontrol center\n")
    (source / "apps/web/core/components/common/logo-spinner.tsx").write_text("codexFleetOrbit\n/codex-fleet-logo.svg\n")
    (source / "apps/web/core/components/settings/project/sidebar/item-icon.tsx").write_text("codex_fleet\n")
    (source / "apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/codex-fleet").mkdir(
        parents=True,
        exist_ok=True,
    )
    (source / "apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/codex-fleet/page.tsx").write_text(
        "codex-fleet project settings\n"
    )
    (source / "apps/web/public/codex-fleet-logo.svg").write_text("<svg />\n")
    (source / "apps/api/plane/seeds/data").mkdir(parents=True, exist_ok=True)
    (source / "apps/api/plane/seeds/data/projects.json").write_text('[{"name":"codex-fleet starter project"}]\n')
    (source / "apps/api/plane/seeds/data/issues.json").write_text("codex-fleet\n")
    (source / "apps/api/plane/seeds/data/cycles.json").write_text("codex-fleet\n")
    (source / "apps/api/plane/seeds/data/pages.json").write_text("codex-fleet\n")
    (source / "packages/i18n/src/locales/en").mkdir(parents=True, exist_ok=True)
    (source / "packages/constants/src/settings").mkdir(parents=True, exist_ok=True)
    (source / "packages/types/src").mkdir(parents=True, exist_ok=True)
    (source / "packages/constants/src/settings/project.ts").write_text("codex_fleet\n")
    (source / "packages/types/src/settings.ts").write_text("codex_fleet\n")
    (source / "packages/i18n/src/locales/en/translations.ts").write_text('star_us_on_github: "codex-fleet"\n')
    (source / "apps/web/core/components/account/auth-forms").mkdir(parents=True)
    (source / "apps/web/core/components/account/auth-forms/auth-header.tsx").write_text("codex-fleet\n")
    (source / "apps/web/app/error").mkdir(parents=True)
    (source / "apps/web/app/error/prod.tsx").write_text("codex-fleet\n")
    (source / "apps/web/app/(all)/create-workspace").mkdir(parents=True)
    (source / "apps/web/app/(all)/create-workspace/page.tsx").write_text("codex-fleet-logo\n")
    (source / "apps/web/app/(all)/invitations").mkdir(parents=True)
    (source / "apps/web/app/(all)/invitations/page.tsx").write_text("codex-fleet-logo\n")
    (source / "apps/web/app/(all)/workspace-invitations").mkdir(parents=True)
    (source / "apps/web/app/(all)/workspace-invitations/page.tsx").write_text("codex-fleet\n")
    manifest = '{"name":"codex-fleet","icons":[{"src":"/codex-fleet-logo.svg"}]}'
    (source / "apps/web/manifest.json").write_text(manifest)
    (source / "apps/web/public/manifest.json").write_text(manifest)
    (source / "apps/web/public/site.webmanifest.json").write_text(manifest)
    (source / "apps/web/public/sw.js").write_text("registration.unregister\n")
    patch = tmp_path / "noop.patch"
    patch.write_text("")

    assert apply_plane_customization_patch(tmp_path, patch_path=patch) == source


def _init_minimal_plane_repo(path: Path) -> None:
    import subprocess

    (path / "apps/web/app/routes").mkdir(parents=True)
    (path / "apps/web/app/routes/core.ts").write_text("stock routes\n")
    (path / "apps/web/public").mkdir(parents=True)
    (path / "apps/web").mkdir(parents=True, exist_ok=True)
    (path / "AGENTS.md").write_text("base\n")
    (path / "apps/web/app/root.tsx").write_text("Plane\n")
    (path / "apps/web/app/layout.tsx").write_text("Plane\n")
    (path / "apps/web/app/(home)").mkdir(parents=True)
    (path / "apps/web/app/(home)/page.tsx").write_text("Plane\nAuthBase\n")
    (path / "apps/web/app/routes/extended.ts").write_text("export default [];\n")
    (path / "apps/web/manifest.json").write_text('{"name":"Plane","icons":[]}\n')
    (path / "apps/web/public/manifest.json").write_text('{"name":"Plane","icons":[]}\n')
    (path / "apps/web/public/site.webmanifest.json").write_text('{"name":"Plane","icons":[]}\n')
    (path / "apps/api/plane/seeds/data").mkdir(parents=True)
    (path / "apps/api/plane/seeds/data/projects.json").write_text('[{"name":"Plane Demo Project"}]\n')
    (path / "packages/i18n/src/locales/en").mkdir(parents=True)
    (path / "packages/i18n/src/locales/en/translations.ts").write_text('star_us_on_github: "Star us on GitHub"\n')
    (path / "apps/web/core/components/project").mkdir(parents=True)
    (path / "apps/web/core/components/project/card.tsx").write_text("Plane Demo Project\n")
    (path / "apps/web/public/sw.js").write_text("workbox\n")
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True)
