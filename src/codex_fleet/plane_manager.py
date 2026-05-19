from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from codex_fleet.config import default_config_path

DEFAULT_PLANE_URL = "http://127.0.0.1:17880"
PLANE_RUNTIME_DIR = Path(".codex-fleet") / "plane-selfhost"
STALE_PLANE_SOURCE_DIR = Path(".codex-fleet") / "plane-src"
PLANE_SOURCE_DIR = Path("apps") / "plane"
PLANE_SOURCE_LOCK_RESOURCE = "plane-source.lock.yml"
PLANE_SETUP_URL = "https://github.com/makeplane/plane/releases/latest/download/setup.sh"
DEFAULT_PLANE_SOURCE_URL = "https://github.com/makeplane/plane.git"
DEFAULT_PLANE_SOURCE_REF = "4c1bdd1d625fa3f1141e8af9c15423946472069e"
PLANE_WEB_STATIC_DIR = "/usr/share/nginx/html"
LOCAL_PLANE_API_KEY_RATE_LIMIT = "1000/minute"


class PlaneManagerError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlaneInstall:
    runtime_dir: Path
    setup_script: Path
    installed: bool
    app_dir: Path | None = None


@dataclass(frozen=True)
class PlaneStatus:
    url: str
    ready: bool
    message: str


@dataclass(frozen=True)
class DockerStatus:
    available: bool
    daemon_ready: bool
    message: str


@dataclass(frozen=True)
class PlaneSource:
    source_dir: Path
    exists: bool
    remote_url: str | None = None
    requested_ref: str | None = None
    current_commit: str | None = None
    manifest_path: Path | None = None


@dataclass(frozen=True)
class PlaneSourceLock:
    source_url: str
    ref: str
    strategy: str
    notes: str


@dataclass(frozen=True)
class PlaneCustomizationCheck:
    name: str
    ok: bool
    message: str


@dataclass(frozen=True)
class PlaneCustomizationReport:
    source_dir: Path
    checks: tuple[PlaneCustomizationCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


@dataclass(frozen=True)
class PlaneFrontendReport:
    container: str
    build_dir: Path
    backup_dir: Path
    installed: bool
    message: str


def inspect_plane_source(repo: Path, source_dir: Path | None = None) -> PlaneSource:
    root = _source_dir(repo, source_dir)
    manifest_path = root / ".codex-fleet-plane-source.yml"
    if not _looks_like_plane_source(root):
        return PlaneSource(source_dir=root, exists=False, manifest_path=manifest_path)
    return PlaneSource(
        source_dir=root,
        exists=True,
        remote_url=_git_output(root, "config", "--get", "remote.origin.url") or _manifest_value(manifest_path, "source_url"),
        requested_ref=_manifest_ref(manifest_path),
        current_commit=_git_output(root, "rev-parse", "HEAD") or _manifest_value(manifest_path, "current_commit"),
        manifest_path=manifest_path,
    )


def default_plane_source_lock_path() -> Path:
    return Path(__file__).parent / "resources" / PLANE_SOURCE_LOCK_RESOURCE


def load_plane_source_lock(path: Path | None = None) -> PlaneSourceLock:
    lock_path = path or default_plane_source_lock_path()
    raw = yaml.safe_load(lock_path.read_text())
    if not isinstance(raw, dict):
        raise PlaneManagerError(f"Plane source lock must be a mapping: {lock_path}")
    try:
        return PlaneSourceLock(
            source_url=str(raw["source_url"]),
            ref=str(raw["ref"]),
            strategy=str(raw["strategy"]),
            notes=str(raw.get("notes", "")),
        )
    except KeyError as exc:
        raise PlaneManagerError(f"Plane source lock missing required field: {exc.args[0]}") from exc


def require_plane_source(repo: Path, source_dir: Path | None = None) -> PlaneSource:
    repo = repo.expanduser().absolute()
    stale = repo / STALE_PLANE_SOURCE_DIR
    if stale.exists():
        raise PlaneManagerError(
            f"Stale Plane runtime source exists at {stale}. Delete it before running codex-fleet; "
            "Plane product source now lives at apps/plane."
        )
    source = inspect_plane_source(repo, source_dir)
    if not source.exists:
        raise PlaneManagerError(
            f"Plane product source is missing at {source.source_dir}. Restore tracked apps/plane before running codex-fleet."
        )
    return source


def write_plane_source_manifest(
    repo: Path,
    *,
    source_url: str = DEFAULT_PLANE_SOURCE_URL,
    ref: str | None = DEFAULT_PLANE_SOURCE_REF,
    source_dir: Path | None = None,
) -> PlaneSource:
    root = require_plane_source(repo, source_dir).source_dir
    repo = repo.expanduser().absolute()
    commit = _git_output(root, "rev-parse", "HEAD") or ref
    manifest_path = root / ".codex-fleet-plane-source.yml"
    lock = load_plane_source_lock()
    manifest = {
        "source_url": source_url,
        "requested_ref": ref,
        "current_commit": commit,
        "lock_source_url": lock.source_url,
        "lock_ref": lock.ref,
        "notes": "Tracked Plane product source for codex-fleet customization.",
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    return inspect_plane_source(repo, source_dir)


def verify_plane_customization(repo: Path, source_dir: Path | None = None) -> PlaneCustomizationReport:
    source = inspect_plane_source(repo, source_dir)
    root = source.source_dir
    checks = [
        _check("source_exists", source.exists, f"Plane source exists at {root}"),
        _check_contains(root / "AGENTS.md", "shell out", "agents_guidance"),
        _check_contains(root / "apps/web/app/root.tsx", "codex-fleet", "root_branding"),
        _check_contains(root / "apps/web/app/layout.tsx", "codex-fleet", "layout_branding"),
        _check_contains(root / "apps/web/app/(home)/page.tsx", "codex-fleet", "home_branding"),
        _check_contains(root / "apps/web/app/(home)/page.tsx", "Star us on GitHub", "home_github_link"),
        _check_contains(root / "apps/web/app/(all)/[workspaceSlug]/(projects)/star-us-link.tsx", "Star on GitHub", "top_nav_github_link"),
        _check_contains(
            root / "apps/web/app/(all)/[workspaceSlug]/(projects)/star-us-link.tsx",
            "https://github.com/RishiGitH/codex-fleet",
            "top_nav_github_link_target",
        ),
        _check_contains(
            root / "apps/web/app/(home)/page.tsx",
            "https://github.com/RishiGitH/codex-fleet",
            "home_github_link_target",
        ),
        _check_contains(root / "apps/web/app/(home)/page.tsx", "Open project dashboard", "home_projects_button"),
        _check_contains(root / "apps/web/app/(home)/page.tsx", "/codex-fleet/dashboard", "home_projects_link"),
        _check_contains(root / "apps/web/app/(home)/page.tsx", "Connection setup", "home_connection_setup_fallback"),
        _check_contains(
            root / "apps/web/app/(home)/page.tsx",
            "Add or create projects from the dashboard's Add Project button",
            "home_project_creation_points_to_dashboard",
        ),
        _check_not_contains(root / "apps/web/app/(home)/page.tsx", "AuthBase", "home_no_stock_auth_screen"),
        _check_not_contains(root / "apps/web/app/(home)/page.tsx", "Plane", "home_hides_plane_branding"),
        _check_not_contains(root / "apps/web/app/root.tsx", "Plane-based", "root_description_hides_plane_branding"),
        _check_not_contains(root / "apps/web/app/layout.tsx", "Plane-based", "layout_description_hides_plane_branding"),
        _check_contains(
            root / "apps/web/core/lib/wrappers/instance-wrapper.tsx",
            'startsWith("/codex-fleet")',
            "first_run_allows_codex_fleet_routes",
        ),
        _check_contains(
            root / "apps/web/core/components/instance/not-ready-view.tsx",
            "Open Codex Fleet",
            "first_run_codex_fleet_action",
        ),
        _check_contains(
            root / "apps/web/core/components/instance/not-ready-view.tsx",
            "/codex-fleet/dashboard",
            "first_run_dashboard_link",
        ),
        _check_contains(
            root / "apps/web/core/components/instance/not-ready-view.tsx",
            "/codex-fleet/onboarding",
            "first_run_onboarding_link",
        ),
        _check_not_contains(
            root / "apps/web/core/components/instance/not-ready-view.tsx",
            "Welcome to Plane",
            "first_run_hides_plane_welcome",
        ),
        _check_not_contains(
            root / "apps/web/core/components/instance/not-ready-view.tsx",
            "PlaneLockup",
            "first_run_hides_plane_logo",
        ),
        _check_not_contains(
            root / "apps/web/core/components/instance/not-ready-view.tsx",
            "GOD_MODE_URL",
            "first_run_avoids_stock_setup_route",
        ),
        _check_contains(root / "apps/web/app/routes/extended.ts", "codex-fleet/onboarding", "onboarding_route"),
        _check_contains(root / "apps/web/app/routes/extended.ts", "codex-fleet/dashboard", "dashboard_route"),
        _check_contains(
            root / "apps/web/app/routes/core.ts",
            ":workspaceSlug/settings/projects/:projectId/codex-fleet",
            "project_settings_codex_fleet_route",
        ),
        _check_contains(root / "apps/web/app/codex-fleet/local-api.ts", "/api/work-items/", "local_api_client"),
        _check_contains(root / "apps/web/app/codex-fleet/local-api.ts", "/api/runs", "local_api_run_client"),
        _check_contains(root / "apps/web/app/codex-fleet/local-api.ts", "/api/folders/pick", "local_api_folder_picker"),
        _check_contains(root / "apps/web/app/codex-fleet/local-api.ts", "/api/plane/login-url", "local_api_plane_login_url"),
        _check_contains(root / "apps/web/app/codex-fleet/local-api.ts", "/api/plane/connect", "local_api_plane_connect"),
        _check_contains(root / "apps/web/app/codex-fleet/local-api.ts", "sessionExchangePromise", "local_api_serializes_session_exchange"),
        _check_contains(
            root / "apps/web/app/codex-fleet/local-session-bootstrap.tsx",
            "ensureCodexFleetLocalConnection",
            "local_session_bootstrap_exchanges_code",
        ),
        _check_contains(
            root / "apps/web/app/root.tsx",
            "CodexFleetLocalSessionBootstrap",
            "root_mounts_local_session_bootstrap",
        ),
        _check_contains(root / "apps/web/app/codex-fleet/onboarding.tsx", "CodexFleetLocalApi", "onboarding_page"),
        _check_contains(root / "apps/web/app/codex-fleet/onboarding.tsx", "Choose Folder", "onboarding_folder_picker"),
        _check_contains(root / "apps/web/app/codex-fleet/dashboard.tsx", "Run with Codex", "dashboard_run_control"),
        _check_contains(root / "apps/web/app/codex-fleet/dashboard.tsx", "harness.scan.commands", "dashboard_harness_scan"),
        _check_contains(root / "apps/web/app/codex-fleet/dashboard.tsx", "Choose Folder", "dashboard_folder_picker"),
        _check_contains(root / "apps/web/app/codex-fleet/dashboard.tsx", "min-h-dvh", "dashboard_uses_dynamic_viewport_height"),
        _check_contains(root / "apps/web/app/codex-fleet/dashboard.tsx", "Create project", "dashboard_create_project_mode"),
        _check_contains(root / "apps/web/app/codex-fleet/dashboard.tsx", "project_type", "dashboard_starter_project_type"),
        _check_contains(root / "apps/web/app/codex-fleet/onboarding.tsx", "harness.scan.commands", "onboarding_harness_scan"),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "Choose folder",
            "native_project_folder_picker",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "Codex workspace setup",
            "native_project_guided_setup",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "What should Codex build first?",
            "native_project_initial_goal_prompt",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "workflow_mode",
            "native_project_workflow_mode",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "Automation mode",
            "native_project_automation_mode_label",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "Max depth",
            "native_project_max_depth_label",
        ),
        _check_not_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "Follow-up tasks",
            "native_project_no_legacy_follow_up_label",
        ),
        _check_not_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "Max follow-up depth",
            "native_project_no_legacy_follow_up_depth_label",
        ),
        _check_not_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "Local project folder",
            "native_project_no_path_first_copy",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "apply_harness",
            "native_project_harness_option",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "Create new project",
            "native_project_create_mode",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "project_type",
            "native_project_starter_type",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "localProjectNotice",
            "native_project_inline_local_api_status",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "CodexFleetLocalApi",
            "native_project_local_api_client",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "Reconnect Codex Fleet",
            "native_project_reconnect_action",
        ),
        _check_contains(
            root / "apps/web/ce/components/projects/create/root.tsx",
            "planeProjectId",
            "native_project_uses_linked_plane_project",
        ),
        _check_not_contains(
            root / "apps/web/app/codex-fleet/dashboard.tsx",
            "Plane project",
            "dashboard_hides_plane_branding",
        ),
        _check_not_contains(
            root / "apps/web/app/codex-fleet/onboarding.tsx",
            "Plane project",
            "onboarding_hides_plane_branding",
        ),
        _check_contains(root / "apps/web/public/sw.js", "registration.unregister", "service_worker_disabled"),
        _check_contains(
            root / "apps/web/app/entry.client.tsx",
            "@/lib/polyfills",
            "safari_request_idle_callback_polyfill",
        ),
        _check_contains(
            root / "apps/web/core/components/common/logo-spinner.tsx",
            "codexFleetOrbit",
            "codex_loading_animation",
        ),
        _check_contains(
            root / "apps/web/core/components/common/logo-spinner.tsx",
            "/codex-fleet-logo.svg",
            "codex_loading_uses_logo",
        ),
        _check_contains(root / "apps/web/app/codex-fleet/local-api.ts", "CodexFleetHarnessScan", "local_api_harness_scan"),
        _check_contains(
            root / "apps/web/app/codex-fleet/work-item-run-panel.tsx",
            "Run with Codex",
            "work_item_run_panel",
        ),
        _check_contains(
            root / "apps/web/app/codex-fleet/work-item-run-panel.tsx",
            "Agent proposed",
            "work_item_source_badge",
        ),
        _check_contains(
            root / "apps/web/core/components/issues/filters.tsx",
            "Fleet Logs",
            "project_board_fleet_logs_button",
        ),
        _check_contains(
            root / "apps/web/core/components/analytics/work-items/modal/header.tsx",
            "Fleet Logs for",
            "project_fleet_logs_modal_header",
        ),
        _check_contains(
            root / "apps/web/core/components/analytics/work-items/modal/content.tsx",
            "Task tree",
            "project_fleet_logs_modal_task_tree",
        ),
        _check_not_contains(
            root / "apps/web/core/components/analytics/work-items/modal/content.tsx",
            "useAnalytics",
            "project_fleet_logs_modal_no_analytics_store",
        ),
        _check_contains(
            root / "apps/web/core/components/issues/issue-modal/form.tsx",
            "Codex task settings",
            "work_item_modal_codex_settings",
        ),
        _check_contains(
            root / "apps/web/core/components/issues/issue-modal/form.tsx",
            "data-codex-fleet-task-settings",
            "work_item_modal_settings_metadata",
        ),
        _check_contains(
            root / "apps/web/core/components/issues/issue-modal/form.tsx",
            "Automation mode",
            "work_item_modal_automation_mode_label",
        ),
        _check_contains(
            root / "apps/web/core/components/issues/issue-modal/form.tsx",
            "plan_execute",
            "work_item_modal_plan_execute_default",
        ),
        _check_not_contains(
            root / "apps/web/core/components/issues/issue-modal/form.tsx",
            "Follow-up tasks",
            "work_item_modal_no_legacy_follow_up_label",
        ),
        _check_not_contains(
            root / "apps/web/core/components/issues/issue-modal/form.tsx",
            "project-default",
            "work_item_modal_no_project_default",
        ),
        _check_contains(
            root / "apps/web/ce/components/sidebar/project-navigation-root.tsx",
            "Fleet Logs",
            "project_sidebar_fleet_logs",
        ),
        _check_contains(
            root / "apps/web/ce/components/sidebar/project-navigation-root.tsx",
            "Agents",
            "project_sidebar_agents",
        ),
        _check_contains(
            root / "apps/web/ce/components/sidebar/project-navigation-root.tsx",
            "Runs",
            "project_sidebar_runs",
        ),
        _check_contains(
            root / "apps/web/ce/components/sidebar/project-navigation-root.tsx",
            "Artifacts",
            "project_sidebar_artifacts",
        ),
        _check_contains(
            root / "apps/web/ce/components/sidebar/project-navigation-root.tsx",
            "Codex Settings",
            "project_sidebar_codex_settings",
        ),
        _check_file(
            root / "apps/web/app/(all)/[workspaceSlug]/(projects)/projects/(detail)/[projectId]/fleet-logs/page.tsx",
            "project_surface_fleet_logs_page",
        ),
        _check_file(
            root / "apps/web/app/(all)/[workspaceSlug]/(projects)/projects/(detail)/[projectId]/agents/page.tsx",
            "project_surface_agents_page",
        ),
        _check_file(
            root / "apps/web/app/(all)/[workspaceSlug]/(projects)/projects/(detail)/[projectId]/runs/page.tsx",
            "project_surface_runs_page",
        ),
        _check_file(
            root / "apps/web/app/(all)/[workspaceSlug]/(projects)/projects/(detail)/[projectId]/artifacts/page.tsx",
            "project_surface_artifacts_page",
        ),
        _check_file(
            root / "apps/web/app/(all)/[workspaceSlug]/(projects)/projects/(detail)/[projectId]/codex-settings/page.tsx",
            "project_surface_codex_settings_page",
        ),
        _check_contains(
            root / "apps/web/app/codex-fleet/project-surfaces.tsx",
            "apps/plane-codex-fleet-mission-control-v3",
            "project_surfaces_cockpit_build_marker",
        ),
        _check_contains(
            root / "apps/web/app/codex-fleet/project-surfaces.tsx",
            "Local Mission Control",
            "project_surfaces_command_center",
        ),
        _check_contains(
            root / "apps/web/app/codex-fleet/project-surfaces.tsx",
            "Agent roster",
            "project_surfaces_agent_roster",
        ),
        _check_contains(
            root / "apps/web/app/codex-fleet/project-surfaces.tsx",
            "Raw settings",
            "project_surfaces_raw_payload_hidden",
        ),
        _check_contains(
            root / "apps/web/core/components/settings/project/sidebar/item-categories.tsx",
            "codex_fleet",
            "project_settings_codex_fleet_tab",
        ),
        _check_contains(
            root / "apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/codex-fleet/page.tsx",
            "codex-fleet project settings",
            "project_settings_codex_fleet_page",
        ),
        _check_contains(
            root / "apps/web/core/components/issues/issue-detail/main-content.tsx",
            "CodexFleetWorkItemRunPanel",
            "work_item_detail_integration",
        ),
        _check_contains(
            root / "apps/web/core/components/issues/issue-layouts/kanban/block.tsx",
            "CodexFleetWorkItemRunCompact",
            "kanban_card_run_control",
        ),
        _check_contains(
            root / "apps/web/core/components/issues/issue-layouts/list/block.tsx",
            "CodexFleetWorkItemRunCompact",
            "list_row_run_control",
        ),
        _check_file(root / "apps/web/public/codex-fleet-logo.svg", "plane_logo"),
        _check_not_contains(
            root / "apps/web/public/site.webmanifest.json",
            "Plane-based",
            "site_manifest_description_hides_plane_branding",
        ),
        _check_not_contains(
            root / "apps/api/plane/seeds/data/projects.json",
            "Plane Demo Project",
            "seed_project_hides_plane_branding",
        ),
        _check_not_contains(
            root / "packages/i18n/src/locales/en/translations.ts",
            "Welcome to Plane",
            "english_locale_hides_plane_branding",
        ),
        _check_not_contains(
            root / "apps/api/plane/seeds/data/issues.json",
            "Plane",
            "seed_issues_hide_plane_branding",
        ),
        _check_not_contains(
            root / "apps/api/plane/seeds/data/cycles.json",
            "Plane",
            "seed_cycles_hide_plane_branding",
        ),
        _check_not_contains(
            root / "apps/api/plane/seeds/data/pages.json",
            "Plane",
            "seed_pages_hide_plane_branding",
        ),
        _check_not_contains(
            root / "apps/web/core/components/account/auth-forms/auth-header.tsx",
            "Welcome back to Plane",
            "auth_header_hides_plane_branding",
        ),
        _check_not_contains(
            root / "apps/web/app/error/prod.tsx",
            "plane.so",
            "error_page_hides_plane_links",
        ),
        _check_not_contains(
            root / "apps/web/app/(all)/create-workspace/page.tsx",
            "PlaneLogo",
            "create_workspace_uses_codex_logo",
        ),
        _check_not_contains(
            root / "apps/web/app/(all)/invitations/page.tsx",
            "PlaneLogo",
            "invitations_use_codex_logo",
        ),
        _check_not_contains(
            root / "apps/web/app/codex-fleet/dashboard.tsx",
            "Plane item",
            "dashboard_hides_plane_item_copy",
        ),
        _check_not_contains(
            root / "apps/web/app/(all)/workspace-invitations/page.tsx",
            "Star us on GitHub",
            "github_link_hides_stock_copy",
        ),
        _check_contains(
            root / "apps/web/core/components/project/card.tsx",
            "codex-fleet starter project",
            "project_card_sanitizes_stock_demo_copy",
        ),
        _check_contains(
            root / "apps/web/core/components/project/card.tsx",
            "control center",
            "project_card_sanitizes_control_plane_copy",
        ),
        _check_manifest_name(root / "apps/web/public/manifest.json"),
        _check_manifest_name(root / "apps/web/public/site.webmanifest.json"),
        _check_manifest_name(root / "apps/web/manifest.json"),
    ]
    return PlaneCustomizationReport(source_dir=root, checks=tuple(checks))


def ensure_plane_runtime(repo: Path) -> PlaneInstall:
    runtime_dir = (repo / PLANE_RUNTIME_DIR).absolute()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    setup_script = runtime_dir / "setup.sh"
    if not setup_script.exists():
        _require("curl", "curl is required to download Plane's official self-host installer.")
        result = subprocess.run(
            ["curl", "-fsSL", "-o", str(setup_script), PLANE_SETUP_URL],
            cwd=runtime_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise PlaneManagerError(result.stderr.strip() or "Failed to download Plane setup.sh")
        setup_script.chmod(0o755)
    return PlaneInstall(
        runtime_dir=runtime_dir,
        setup_script=setup_script,
        installed=_plane_app_dir(runtime_dir) is not None,
        app_dir=_plane_app_dir(runtime_dir),
    )


def inspect_plane_runtime(repo: Path) -> PlaneInstall:
    runtime_dir = (repo / PLANE_RUNTIME_DIR).absolute()
    setup_script = runtime_dir / "setup.sh"
    app_dir = _plane_app_dir(runtime_dir)
    return PlaneInstall(
        runtime_dir=runtime_dir,
        setup_script=setup_script,
        installed=app_dir is not None,
        app_dir=app_dir,
    )


def install_branded_plane_frontend(
    repo: Path,
    build_dir: Path,
    *,
    container_name: str | None = None,
) -> PlaneFrontendReport:
    """Replace the local Plane web static files with the branded codex-fleet build.

    This only changes files inside the local Plane web container. It does not
    touch Plane data, auth, backend models, migrations, or Docker volumes.
    """
    repo = repo.expanduser().absolute()
    build_dir = build_dir.expanduser().absolute()
    if not (build_dir / "index.html").exists():
        raise PlaneManagerError(f"Plane web build not found at {build_dir}")
    docker = check_docker_status()
    if not docker.daemon_ready:
        raise PlaneManagerError(f"Docker daemon is not ready: {docker.message}")
    container = container_name or _detect_plane_web_container()
    if container is None:
        raise PlaneManagerError("Could not find a running local Plane web container.")
    backup_dir = _plane_frontend_backup_dir(repo)
    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    if not (backup_dir / "index.html").exists():
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        _docker_checked("cp", f"{container}:{PLANE_WEB_STATIC_DIR}/.", str(backup_dir))
    _docker_checked("exec", container, "sh", "-lc", f"rm -rf {PLANE_WEB_STATIC_DIR}/*")
    _docker_checked("cp", f"{build_dir}/.", f"{container}:{PLANE_WEB_STATIC_DIR}/")
    cache_stamp = _plane_frontend_cache_stamp(build_dir)
    _docker_checked(
        "exec",
        container,
        "sh",
        "-lc",
        (
            f"manifest=$(basename $(ls {PLANE_WEB_STATIC_DIR}/assets/manifest-*.js | head -n 1)); "
            f"sed -i \"s|/assets/$manifest|/assets/$manifest?cf={cache_stamp}|g\" {PLANE_WEB_STATIC_DIR}/index.html"
        ),
    )
    _docker_checked("exec", container, "sh", "-lc", "nginx -s reload")
    return PlaneFrontendReport(
        container=container,
        build_dir=build_dir,
        backup_dir=backup_dir,
        installed=True,
        message="Branded codex-fleet Plane frontend installed.",
    )


def restore_stock_plane_frontend(repo: Path, *, container_name: str | None = None) -> PlaneFrontendReport:
    """Restore the saved stock Plane web static files into the local Plane web container."""
    repo = repo.expanduser().absolute()
    backup_dir = _plane_frontend_backup_dir(repo)
    if not (backup_dir / "index.html").exists():
        raise PlaneManagerError(f"No Plane frontend backup found at {backup_dir}")
    docker = check_docker_status()
    if not docker.daemon_ready:
        raise PlaneManagerError(f"Docker daemon is not ready: {docker.message}")
    container = container_name or _detect_plane_web_container()
    if container is None:
        raise PlaneManagerError("Could not find a running local Plane web container.")
    _docker_checked("exec", container, "sh", "-lc", f"rm -rf {PLANE_WEB_STATIC_DIR}/*")
    _docker_checked("cp", f"{backup_dir}/.", f"{container}:{PLANE_WEB_STATIC_DIR}/")
    _docker_checked("exec", container, "sh", "-lc", "nginx -s reload")
    return PlaneFrontendReport(
        container=container,
        build_dir=backup_dir,
        backup_dir=backup_dir,
        installed=False,
        message="Stock Plane frontend restored.",
    )


def _plane_frontend_cache_stamp(build_dir: Path) -> str:
    newest = 0
    for path in build_dir.rglob("*"):
        if path.is_file():
            newest = max(newest, path.stat().st_mtime_ns)
    return str(newest or time.time_ns())


def branded_plane_frontend_status(repo: Path, *, container_name: str | None = None) -> PlaneFrontendReport:
    repo = repo.expanduser().absolute()
    backup_dir = _plane_frontend_backup_dir(repo)
    docker = check_docker_status()
    if not docker.daemon_ready:
        raise PlaneManagerError(f"Docker daemon is not ready: {docker.message}")
    container = container_name or _detect_plane_web_container()
    if container is None:
        raise PlaneManagerError("Could not find a running local Plane web container.")
    result = subprocess.run(
        ["docker", "exec", container, "sh", "-lc", f"grep -R \"codex-fleet\" -m 1 {PLANE_WEB_STATIC_DIR} >/dev/null"],
        text=True,
        capture_output=True,
        check=False,
    )
    installed = result.returncode == 0
    return PlaneFrontendReport(
        container=container,
        build_dir=Path(PLANE_WEB_STATIC_DIR),
        backup_dir=backup_dir,
        installed=installed,
        message="Branded codex-fleet frontend detected." if installed else "Stock Plane frontend detected.",
    )


def start_plane(repo: Path, url: str = DEFAULT_PLANE_URL) -> PlaneInstall:
    install = ensure_plane_runtime(repo)
    _require("docker", "Docker is required to run local Plane.")
    docker = check_docker_status()
    if not docker.daemon_ready:
        raise PlaneManagerError(f"Docker daemon is not ready: {docker.message}")
    if not install.installed:
        _run_setup_action(install, "1")
        install = inspect_plane_runtime(repo)
        if not install.installed:
            raise PlaneManagerError("Plane installer completed, but no plane-app directory was created.")
    ensure_plane_runtime_config(repo, url=url)
    _run_setup_action(install, "2")
    return inspect_plane_runtime(repo)


def ensure_plane_runtime_config(repo: Path, url: str = DEFAULT_PLANE_URL) -> bool:
    install = inspect_plane_runtime(repo)
    env_changed = _configure_plane_env(install, url)
    live_rate_limit = _plane_api_rate_limit(install)
    live_env_stale = live_rate_limit is not None and live_rate_limit != LOCAL_PLANE_API_KEY_RATE_LIMIT
    if env_changed or live_env_stale:
        _restart_plane_api_services(install)
    return env_changed or live_env_stale


def check_plane_url(url: str = DEFAULT_PLANE_URL, timeout_seconds: float = 2.0) -> PlaneStatus:
    candidates = ("/", "/api/health/", "/god-mode/")
    last_error = "not checked"
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        for path in candidates:
            try:
                response = client.get(f"{url.rstrip('/')}{path}")
            except httpx.HTTPError as exc:
                last_error = str(exc)
                continue
            if response.status_code < 500:
                return PlaneStatus(url=url, ready=True, message=f"HTTP {response.status_code} at {path}")
            last_error = f"HTTP {response.status_code} at {path}"
    return PlaneStatus(url=url, ready=False, message=last_error)


def check_docker_status() -> DockerStatus:
    if shutil.which("docker") is None:
        return DockerStatus(available=False, daemon_ready=False, message="Docker is not installed or not on PATH.")
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return DockerStatus(available=True, daemon_ready=False, message=str(exc))
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "docker info failed"
        return DockerStatus(available=True, daemon_ready=False, message=message)
    version = result.stdout.strip()
    return DockerStatus(
        available=True,
        daemon_ready=True,
        message=f"Docker daemon ready{f' ({version})' if version else ''}.",
    )


def wait_for_plane(
    url: str = DEFAULT_PLANE_URL,
    *,
    timeout_seconds: float = 180.0,
    interval_seconds: float = 3.0,
) -> PlaneStatus:
    deadline = time.monotonic() + timeout_seconds
    status = check_plane_url(url)
    while not status.ready and time.monotonic() < deadline:
        time.sleep(interval_seconds)
        status = check_plane_url(url)
    return status


def write_plane_config(
    repo: Path,
    *,
    base_url: str,
    workspace_slug: str,
    project_id: str,
    api_key_ref: str = "$PLANE_API_KEY",
    api_key_value: str | None = None,
) -> Path:
    target = default_config_path(repo)
    data = {
        "repo": ".",
        "tracker": {
            "kind": "plane",
            "active_states": ["Ready"],
            "handoff_states": ["Human Review"],
            "terminal_states": ["Done", "Cancelled"],
            "plane_base_url": base_url,
            "plane_api_key": api_key_ref,
            "plane_workspace_slug": workspace_slug,
            "plane_project_id": project_id,
        },
        "agent": {"max_concurrent_agents": 1},
        "workspace": {"root": ".codex-fleet/workspaces"},
        "codex": {
            "runner": "app-server",
            "command": "codex app-server",
            "approval_policy": "never",
            "sandbox_mode": "workspace-write",
            "model": "gpt-5.5",
            "reasoning_effort": "low",
            "turn_timeout_ms": 3_600_000,
            "stall_timeout_ms": 300_000,
        },
        "token": {
            "default_doc_limit": 8000,
            "skill_limit": 4000,
            "raw_artifact_retention": "keep",
            "enable_rtk": False,
            "enable_caveman": False,
            "enable_repomix": False,
        },
    }
    target.write_text(yaml.safe_dump(data, sort_keys=False))
    if api_key_ref == "$PLANE_API_KEY" and api_key_value:
        secrets_dir = repo / ".codex-fleet"
        secrets_dir.mkdir(parents=True, exist_ok=True)
        secrets_path = secrets_dir / "secrets.env"
        secrets_path.write_text(f"PLANE_API_KEY={api_key_value}\n")
        secrets_path.chmod(0o600)
    return target


def open_plane(url: str) -> bool:
    if os.getenv("CODEX_FLEET_NO_BROWSER"):
        return False
    browser = os.getenv("CODEX_FLEET_BROWSER")
    if browser:
        subprocess.Popen(["open", "-a", browser, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    if shutil.which("open"):
        subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    return webbrowser.open(url, new=2)


def _require(binary: str, message: str) -> None:
    if shutil.which(binary) is None:
        raise PlaneManagerError(message)


def _plane_frontend_backup_dir(repo: Path) -> Path:
    return (repo / PLANE_RUNTIME_DIR / "web-static-stock-backup").absolute()


def _detect_plane_web_container() -> str | None:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise PlaneManagerError(result.stderr.strip() or "Could not list Docker containers.")
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    preferred = ("plane-app-web-1", "plane-app-preview-web-1")
    for name in preferred:
        if name in names:
            return name
    for name in names:
        lowered = name.lower()
        if "plane" in lowered and "web" in lowered:
            return name
    return None


def _docker_checked(*args: str) -> None:
    result = subprocess.run(
        ["docker", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise PlaneManagerError(result.stderr.strip() or f"docker {' '.join(args)} failed")


def _source_dir(repo: Path, source_dir: Path | None) -> Path:
    if source_dir is not None:
        path = source_dir.expanduser()
        return path.absolute() if path.is_absolute() else (repo.expanduser().absolute() / path).absolute()
    return (repo.expanduser().absolute() / PLANE_SOURCE_DIR).absolute()


def _looks_like_plane_source(root: Path) -> bool:
    return (root / "apps" / "web").exists() and (root / "package.json").exists()


def _git_output(cwd: Path, *args: str) -> str | None:
    if not (cwd / ".git").exists():
        return None
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _manifest_ref(path: Path) -> str | None:
    return _manifest_value(path, "requested_ref")


def _manifest_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    return str(value) if value else None


def _check(name: str, ok: bool, message: str) -> PlaneCustomizationCheck:
    return PlaneCustomizationCheck(name=name, ok=ok, message=message if ok else f"Missing: {message}")


def _check_file(path: Path, name: str) -> PlaneCustomizationCheck:
    return _check(name, path.exists(), str(path))


def _check_contains(path: Path, needle: str, name: str) -> PlaneCustomizationCheck:
    if not path.exists():
        return PlaneCustomizationCheck(name=name, ok=False, message=f"Missing file: {path}")
    return _check(name, needle in path.read_text(), f"{path} contains {needle!r}")


def _check_not_contains(path: Path, needle: str, name: str) -> PlaneCustomizationCheck:
    if not path.exists():
        return PlaneCustomizationCheck(name=name, ok=False, message=f"Missing file: {path}")
    return _check(name, needle not in path.read_text(), f"{path} does not contain {needle!r}")


def _check_manifest_name(path: Path) -> PlaneCustomizationCheck:
    if not path.exists():
        return PlaneCustomizationCheck(name=f"manifest:{path.name}", ok=False, message=f"Missing file: {path}")
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return PlaneCustomizationCheck(name=f"manifest:{path.name}", ok=False, message=f"Invalid JSON: {exc}")
    name = payload.get("name")
    icons = payload.get("icons")
    icon_ok = isinstance(icons, list) and any(
        isinstance(icon, dict) and icon.get("src") == "/codex-fleet-logo.svg" for icon in icons
    )
    ok = name == "codex-fleet" and icon_ok
    return PlaneCustomizationCheck(
        name=f"manifest:{path.name}",
        ok=ok,
        message=f"{path} names codex-fleet and references logo" if ok else f"{path} is not branded for codex-fleet",
    )


def _run_setup_action(install: PlaneInstall, action: str) -> None:
    result = subprocess.run(
        ["bash", str(install.setup_script)],
        cwd=install.runtime_dir,
        input=f"{action}\n8\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        raise PlaneManagerError(f"Plane setup action {action} failed. {details}")


def _plane_app_dir(runtime_dir: Path) -> Path | None:
    for name in ("plane-app", "plane-app-preview"):
        app_dir = runtime_dir / name
        if app_dir.exists():
            return app_dir
    return None


def _configure_plane_env(install: PlaneInstall, url: str) -> bool:
    if install.app_dir is None:
        return False
    env_path = _plane_env_path(install.app_dir)
    if not env_path.exists():
        return False
    original = env_path.read_text()
    host_port = _host_port_from_url(url)
    port = _port_from_url(url)
    replacements = {
        "APP_DOMAIN": host_port,
        "LISTEN_HTTP_PORT": port,
        "API_KEY_RATE_LIMIT": LOCAL_PLANE_API_KEY_RATE_LIMIT,
    }
    lines = original.splitlines()
    replaced = {key: False for key in replacements}
    updated: list[str] = []
    for line in lines:
        key = line.split("=", maxsplit=1)[0]
        if key in replacements:
            updated.append(f"{key}={replacements[key]}")
            replaced[key] = True
        else:
            updated.append(line)
    for key, value in replacements.items():
        if not replaced[key]:
            updated.append(f"{key}={value}")
    next_text = "\n".join(updated) + "\n"
    if next_text == original:
        return False
    env_path.write_text(next_text)
    return True


def _plane_env_path(app_dir: Path) -> Path:
    for name in ("plane.env", "variables.env"):
        path = app_dir / name
        if path.exists():
            return path
    return app_dir / "plane.env"


def _restart_plane_api_services(install: PlaneInstall) -> None:
    if install.app_dir is None:
        return
    compose_file = install.app_dir / "docker-compose.yaml"
    if not compose_file.exists():
        compose_file = install.app_dir / "docker-compose.yml"
    if not compose_file.exists():
        return
    env_path = _plane_env_path(install.app_dir)
    command = ["docker", "compose", "-f", str(compose_file)]
    if env_path.exists():
        command.extend(["--env-file", str(env_path)])
    command.extend(["up", "-d", "api", "worker", "beat-worker"])
    result = subprocess.run(
        command,
        cwd=install.app_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PlaneManagerError(f"Plane API restart after env update failed. {detail}")


def _plane_api_rate_limit(install: PlaneInstall) -> str | None:
    if install.app_dir is None:
        return None
    compose_file = install.app_dir / "docker-compose.yaml"
    if not compose_file.exists():
        compose_file = install.app_dir / "docker-compose.yml"
    if not compose_file.exists():
        return None
    env_path = _plane_env_path(install.app_dir)
    command = ["docker", "compose", "-f", str(compose_file)]
    if env_path.exists():
        command.extend(["--env-file", str(env_path)])
    command.extend(["exec", "-T", "api", "sh", "-lc", "printf %s \"$API_KEY_RATE_LIMIT\""])
    result = subprocess.run(
        command,
        cwd=install.app_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _port_from_url(url: str) -> str:
    tail = url.rsplit(":", maxsplit=1)[-1]
    if "/" in tail:
        tail = tail.split("/", maxsplit=1)[0]
    return tail if tail.isdigit() else "8080"


def _host_port_from_url(url: str) -> str:
    without_scheme = url.split("://", maxsplit=1)[-1]
    return without_scheme.split("/", maxsplit=1)[0]
