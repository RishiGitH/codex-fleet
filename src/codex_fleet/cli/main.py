import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import click
import httpx
from rich.console import Console
from rich.table import Table

from codex_fleet.budget import scan_budget
from codex_fleet.capture import capture_command
from codex_fleet.codex.protocol import ProtocolError, validate_app_server_turn_start_shape
from codex_fleet.config import FleetConfig, load_config, write_default_config
from codex_fleet.context_pack import write_context_pack
from codex_fleet.daemon import MultiProjectFleetDaemon as FleetDaemon
from codex_fleet.daemon import ProjectDaemonTick
from codex_fleet.doctor import render_report, scan_repo
from codex_fleet.factory import build_plane_client, build_runner, build_tracker, default_store_path
from codex_fleet.harness import apply_harness, plan_harness
from codex_fleet.lifecycle import (
    RuntimeRecord,
    read_runtime_record,
    remove_runtime_record,
    stop_loopback_ports,
    stop_plane_app_containers,
    stop_plane_runtime,
    stop_runtime_process,
    write_runtime_record,
)
from codex_fleet.local_api import (
    DEFAULT_LOCAL_API_HOST,
    DEFAULT_LOCAL_API_PORT,
    LocalApiError,
    LocalApiServer,
    build_onboarding_url,
    build_plane_login_url,
    create_local_api_server,
)
from codex_fleet.local_ui import create_local_ui_server
from codex_fleet.models import WorkItem
from codex_fleet.orchestrator import Orchestrator
from codex_fleet.plane import PlaneClient
from codex_fleet.plane_bootstrap import check_plane_readiness, ensure_plane_bootstrap
from codex_fleet.plane_local_bootstrap import PlaneLocalBootstrapError, bootstrap_local_plane
from codex_fleet.plane_manager import (
    DEFAULT_PLANE_URL,
    PlaneManagerError,
    branded_plane_frontend_status,
    check_docker_status,
    check_plane_url,
    ensure_plane_runtime_config,
    inspect_plane_runtime,
    install_branded_plane_frontend,
    open_plane,
    require_plane_source,
    restore_stock_plane_frontend,
    start_plane,
    verify_plane_customization,
    wait_for_plane,
    write_plane_config,
)
from codex_fleet.plane_preview import (
    PlanePreviewError,
    create_plane_preview_server,
    default_plane_build_dir,
    prepare_plane_preview_build,
)
from codex_fleet.plane_views import PlaneViewBootstrapError, ensure_local_plane_project_views
from codex_fleet.pr_flow import PrRequest, create_draft_pr
from codex_fleet.project_reconcile import ProjectReconciliation, reconcile_registered_projects
from codex_fleet.project_registry import (
    ProjectRegistry,
    default_project_registry_path,
    discover_git_root,
)
from codex_fleet.runner import FakeRunner
from codex_fleet.store import RunStore
from codex_fleet.tracker import MemoryTracker

console = Console()

DEFAULT_PLANE_PREVIEW_PORT = 17300


@click.group()
def main() -> None:
    """Local control plane for Codex work runs."""


@main.command()
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--apply", "apply_files", is_flag=True, help="Write missing harness files.")
def bootstrap(repo: Path, apply_files: bool) -> None:
    """Prepare a repo for codex-fleet and print the next command."""
    config_path = write_default_config(repo)
    console.print(f"Config ready: {config_path}")
    report = scan_repo(repo)
    console.print(render_report(report))
    plan = plan_harness(repo)
    if plan.missing:
        console.print("Missing harness files:")
        for file in plan.missing:
            console.print(f"- {file.path}")
        if apply_files:
            written = apply_harness(repo)
            console.print("Wrote harness files:")
            for path in written:
                console.print(f"- {path}")
        else:
            console.print("Run again with --apply to write them.")
    else:
        console.print("Harness files already exist.")
    console.print("Next: codex-fleet up --repo .")


@main.command()
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
def doctor(repo: Path) -> None:
    """Scan a repository for readiness."""
    config = load_config(repo)
    console.print(render_report(scan_repo(config.repo, codex_command=config.codex.command)))


@main.command("init-harness")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
def init_harness(repo: Path) -> None:
    """Create a local codex-fleet config file."""
    path = write_default_config(repo)
    console.print(f"Config ready: {path}")


@main.command("plan-harness")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
def plan_harness_cmd(repo: Path) -> None:
    """Show recommended Codex harness files for a repo."""
    plan = plan_harness(repo)
    if not plan.missing:
        console.print("Harness files already exist.")
        return
    console.print("Missing harness files:")
    for file in plan.missing:
        console.print(f"- {file.path}")


@main.command("apply-harness")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--overwrite", is_flag=True, help="Overwrite existing generated harness files.")
def apply_harness_cmd(repo: Path, overwrite: bool) -> None:
    """Write recommended Codex harness files into a repo."""
    written = apply_harness(repo, overwrite=overwrite)
    if not written:
        console.print("No harness files written.")
        return
    console.print("Wrote harness files:")
    for path in written:
        console.print(f"- {path}")


@main.command()
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
def status(repo: Path) -> None:
    """Show basic repo status."""
    config = load_config(repo)
    report = scan_repo(config.repo, codex_command=config.codex.command)
    console.print(f"Repo: {config.repo}")
    console.print(f"Tracker: {config.tracker.kind}")
    console.print(f"Workspace root: {config.workspace.root}")
    console.print(f"Readiness: {report.score}/100")
    console.print(f"Run store: {default_store_path(config.repo)}")


@main.group("project")
def project_group() -> None:
    """Manage local project folders known to codex-fleet."""


@project_group.command("add")
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd(), help="codex-fleet runtime root.")
@click.option("--name", default=None, help="Display name for the project.")
def project_add(path: Path, repo: Path, name: str | None) -> None:
    """Register a local folder as a codex-fleet project."""
    registry = ProjectRegistry(default_project_registry_path(repo))
    project = registry.add_project(path, name=name)
    console.print(f"Project: {project.name}")
    console.print(f"Slug: {project.slug}")
    console.print(f"Path: {project.repo_path}")
    console.print(f"Git root: {project.git_root or 'not detected'}")


@project_group.command("list")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd(), help="codex-fleet runtime root.")
def project_list(repo: Path) -> None:
    """List local project folders known to codex-fleet."""
    registry = ProjectRegistry(default_project_registry_path(repo))
    table = Table(title="codex-fleet projects")
    table.add_column("id", justify="right")
    table.add_column("name")
    table.add_column("slug")
    table.add_column("path")
    table.add_column("runner")
    for project in registry.list_projects():
        table.add_row(
            str(project.id),
            project.name,
            project.slug,
            str(project.repo_path),
            project.runner_mode,
        )
    console.print(table)


@project_group.command("prune-stale")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd(), help="codex-fleet runtime root.")
@click.option("--apply", "apply_changes", is_flag=True, help="Remove stale registrations. Without this, only prints what would change.")
def project_prune_stale(repo: Path, apply_changes: bool) -> None:
    """Remove stale local project registrations from codex-fleet runtime state."""
    registry = ProjectRegistry(default_project_registry_path(repo))
    projects = registry.list_projects()
    stale: list[tuple[int, str, str]] = []
    seen_plane: dict[tuple[str, str], int] = {}
    for project in sorted(projects, key=lambda item: item.id, reverse=True):
        reason: str | None = None
        if not project.repo_path.exists():
            reason = f"folder missing: {project.repo_path}"
        elif discover_git_root(project.repo_path) is None:
            reason = f"not a git repository: {project.repo_path}"
        if reason is None and project.plane_workspace_slug and project.plane_project_id:
            plane_key = (project.plane_workspace_slug, project.plane_project_id)
            kept_id = seen_plane.get(plane_key)
            if kept_id is None:
                seen_plane[plane_key] = project.id
            else:
                reason = f"duplicate Plane project mapping; keeping newer registration {kept_id}"
        if reason is not None:
            stale.append((project.id, project.name, reason))

    if not stale:
        console.print("No stale project registrations found.")
        return

    table = Table(title="stale codex-fleet project registrations")
    table.add_column("id", justify="right")
    table.add_column("name")
    table.add_column("reason")
    for project_id, name, reason in sorted(stale):
        table.add_row(str(project_id), name, reason)
    console.print(table)
    if not apply_changes:
        console.print("Dry run only. Re-run with `--apply` to remove these local registry rows.")
        return

    removed = 0
    for project_id, _name, _reason in stale:
        if registry.delete_project(project_id):
            removed += 1
    console.print(f"Removed {removed} stale local project registration(s).")


@main.command("api")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--host", default=DEFAULT_LOCAL_API_HOST, show_default=True)
@click.option("--port", type=int, default=DEFAULT_LOCAL_API_PORT, show_default=True)
@click.option("--unsafe-allow-remote", is_flag=True, help="Allow binding outside loopback.")
def api(repo: Path, host: str, port: int, unsafe_allow_remote: bool) -> None:
    """Start the loopback API used by the customized Plane UI."""
    server = create_local_api_server(
        repo,
        host=host,
        port=port,
        unsafe_allow_remote=unsafe_allow_remote,
    )
    console.print(f"codex-fleet API: http://{host}:{port}")
    console.print(f"Runtime root: {repo.expanduser().absolute()}")
    console.print("Plane UI should send the local token via Authorization or X-Codex-Fleet-Token.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        console.print("Stopped codex-fleet API.")


@main.command("budget")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--strict", is_flag=True, help="Exit nonzero when any guidance file is too large.")
def budget(repo: Path, strict: bool) -> None:
    """Show size of important guidance files."""
    config = load_config(repo)
    summary = scan_budget(
        config.repo,
        default_doc_limit=config.token.default_doc_limit,
        skill_limit=config.token.skill_limit,
    )
    table = Table(title="Context budget")
    table.add_column("path")
    table.add_column("bytes", justify="right")
    table.add_column("tokens", justify="right")
    table.add_column("limit", justify="right")
    table.add_column("status")
    for entry in summary.entries:
        table.add_row(
            entry.path,
            str(entry.bytes),
            str(entry.estimated_tokens),
            str(entry.limit),
            entry.status,
        )
    console.print(table)
    if strict and not summary.ok:
        raise click.ClickException(f"{summary.too_large_count} file(s) exceed token budget")


@main.command("pack-context")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--out", "out_dir", type=click.Path(path_type=Path), required=True)
def pack_context(repo: Path, out_dir: Path) -> None:
    """Write a small targeted context pack."""
    config = load_config(repo)
    result = write_context_pack(config.repo, out_dir)
    console.print(f"Context pack: {result.out_dir}")
    console.print(f"Files indexed: {result.file_count}")
    console.print(f"Rough tokens: {result.estimated_tokens}")
    console.print(f"Exclusions: {', '.join(result.exclusions)}")


@main.command(
    "capture",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
def capture(repo: Path, command: tuple[str, ...]) -> None:
    """Run a command and store raw plus summarized output artifacts."""
    config = load_config(repo)
    result = capture_command(config.repo, command)
    console.print(f"Artifacts: {result.artifact_dir}")
    console.print(f"Raw output: {result.raw_path}")
    console.print(f"Summary: {result.summary_path}")
    raise click.exceptions.Exit(result.returncode)


@main.command("plane-check")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
def plane_check(repo: Path) -> None:
    """Check configured Plane project states and Ready work count."""
    config = load_config(repo)
    client = build_plane_client(config)
    readiness = check_plane_readiness(client, config.tracker.active_states)
    console.print(f"Plane states: {readiness.state_count}")
    console.print(f"Candidate work items: {readiness.candidate_count}")
    if readiness.missing_states:
        console.print("Missing states:")
        for state in readiness.missing_states:
            console.print(f"- {state}")
    else:
        console.print("Plane workflow states are ready.")


@main.command("plane-up")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--url", default=DEFAULT_PLANE_URL, show_default=True)
@click.option("--no-open", is_flag=True, help="Do not open the local Plane URL in a browser.")
def plane_up(repo: Path, url: str, no_open: bool) -> None:
    """Install or start local self-hosted Plane."""
    try:
        install = start_plane(repo.expanduser().absolute(), url=url)
    except PlaneManagerError as exc:
        raise click.ClickException(str(exc)) from exc
    status = wait_for_plane(url)
    console.print(f"Plane runtime: {install.runtime_dir}")
    console.print(f"Plane URL: {url}")
    console.print(f"Plane ready: {status.ready} ({status.message})")
    if not status.ready:
        raise click.ClickException("Plane did not become ready before the timeout.")
    if not no_open:
        open_plane(url)


@main.command("plane-status")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--url", default=DEFAULT_PLANE_URL, show_default=True)
def plane_status(repo: Path, url: str) -> None:
    """Show local Plane runtime and readiness status."""
    install = inspect_plane_runtime(repo.expanduser().absolute())
    docker = check_docker_status()
    status = check_plane_url(url)
    console.print(f"Plane runtime: {install.runtime_dir}")
    console.print(f"Plane installed: {install.installed}")
    if install.app_dir is not None:
        console.print(f"Plane app dir: {install.app_dir}")
    console.print(f"Docker available: {docker.available}")
    console.print(f"Docker daemon ready: {docker.daemon_ready} ({docker.message})")
    console.print(f"Plane URL: {url}")
    console.print(f"Plane ready: {status.ready} ({status.message})")


@main.command("plane-verify")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--dir", "source_dir", type=click.Path(path_type=Path), default=None)
def plane_verify(repo: Path, source_dir: Path | None) -> None:
    """Verify local Plane source has codex-fleet customization hooks."""
    repo = repo.expanduser().absolute()
    try:
        require_plane_source(repo, source_dir)
    except PlaneManagerError as exc:
        raise click.ClickException(str(exc)) from exc
    report = verify_plane_customization(repo, source_dir)
    table = Table(title=f"Plane customization: {report.source_dir}")
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail")
    for check in report.checks:
        table.add_row(check.name, "OK" if check.ok else "MISSING", check.message)
    console.print(table)
    if not report.ok:
        raise click.ClickException("Plane customization verification failed.")


@main.command("plane-frontend")
@click.argument("action", type=click.Choice(["install", "restore", "status"]))
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--container", default=None, help="Plane web container name. Auto-detected by default.")
@click.option("--rebuild", is_flag=True, help="Rebuild the branded Plane web frontend before installing.")
def plane_frontend(action: str, repo: Path, container: str | None, rebuild: bool) -> None:
    """Install, restore, or inspect the branded Plane web frontend."""
    repo = repo.expanduser().absolute()
    try:
        if action == "install":
            build_dir = prepare_plane_preview_build(repo) if rebuild else _ensure_plane_frontend_build(repo)
            report = install_branded_plane_frontend(repo, build_dir, container_name=container)
        elif action == "restore":
            report = restore_stock_plane_frontend(repo, container_name=container)
        else:
            report = branded_plane_frontend_status(repo, container_name=container)
    except (PlaneManagerError, PlanePreviewError) as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(report.message)
    console.print(f"Plane web container: {report.container}")
    console.print(f"Build/static source: {report.build_dir}")
    console.print(f"Stock backup: {report.backup_dir}")
    console.print(f"Branded installed: {report.installed}")


@main.command("plane-onboarding-url")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--path", "project_path", type=click.Path(path_type=Path), default=None, help="Project folder to prefill.")
@click.option("--plane-url", default=DEFAULT_PLANE_URL, show_default=True)
@click.option(
    "--api-url",
    default=f"http://{DEFAULT_LOCAL_API_HOST}:{DEFAULT_LOCAL_API_PORT}",
    show_default=True,
)
@click.option("--no-token", is_flag=True, help="Do not include the local API token in the URL fragment.")
def plane_onboarding_url(repo: Path, project_path: Path | None, plane_url: str, api_url: str, no_token: bool) -> None:
    """Print the branded Plane fork onboarding URL for this machine."""
    url = build_onboarding_url(
        repo,
        plane_url=plane_url,
        project_path=project_path,
        api_url=api_url,
        include_token=not no_token,
    )
    console.print(url)
    if not no_token:
        console.print("The local API token is in the URL fragment and is intended only for loopback use.")


@main.command("plane-fork-preview")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--host", default=DEFAULT_LOCAL_API_HOST, show_default=True)
@click.option("--port", type=int, default=DEFAULT_PLANE_PREVIEW_PORT, show_default=True)
@click.option("--project-path", type=click.Path(path_type=Path), default=None, help="Project folder to prefill.")
@click.option("--no-open", is_flag=True)
@click.option("--unsafe-allow-remote", is_flag=True, help="Allow binding outside loopback.")
@click.option("--prepare-only", is_flag=True, help="Prepare the branded Plane build, then exit without serving.")
def plane_fork_preview(
    repo: Path,
    host: str,
    port: int,
    project_path: Path | None,
    no_open: bool,
    unsafe_allow_remote: bool,
    prepare_only: bool,
) -> None:
    """Serve the built branded Plane fork and loopback API for local onboarding."""
    repo = repo.expanduser().absolute()
    if prepare_only:
        try:
            build_dir = prepare_plane_preview_build(repo)
        except PlanePreviewError as exc:
            raise click.ClickException(str(exc)) from exc
        console.print(f"Plane fork build ready: {build_dir}")
        return
    _serve_plane_fork_onboarding(
        repo,
        project_path=project_path,
        host=host,
        port=port,
        no_open=no_open,
        unsafe_allow_remote=unsafe_allow_remote,
        once=False,
    )


@main.command("plane-bootstrap")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
def plane_bootstrap(repo: Path) -> None:
    """Create missing codex-fleet workflow states in Plane."""
    config = load_config(repo)
    client = build_plane_client(config)
    result = ensure_plane_bootstrap(client, config.tracker.active_states)
    if result.created_states:
        console.print("Created states:")
        for state in result.created_states:
            console.print(f"- {state}")
    else:
        console.print("No states created.")
    if result.created_labels:
        console.print("Created labels:")
        for label in result.created_labels:
            console.print(f"- {label}")
    else:
        console.print("No labels created.")
    if result.created_demo_item:
        console.print("Created demo Ready work item.")
    try:
        view_result = ensure_local_plane_project_views(config)
    except PlaneViewBootstrapError as exc:
        raise click.ClickException(f"Plane saved views could not be bootstrapped: {exc}") from exc
    if view_result.skipped:
        console.print(f"Plane saved views skipped: {view_result.skipped_reason}")
    else:
        console.print(f"Plane saved views created: {len(view_result.created)}")
        console.print(f"Plane saved views existing: {len(view_result.existing)}")
    console.print(f"Plane ready: {result.readiness.ok}")


@main.command("plane-configure")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--url", default=DEFAULT_PLANE_URL, show_default=True)
@click.option("--workspace-slug", envvar="PLANE_WORKSPACE_SLUG")
@click.option("--project-id", envvar="PLANE_PROJECT_ID")
@click.option("--api-key-ref", default="$PLANE_API_KEY", show_default=True)
def plane_configure(repo: Path, url: str, workspace_slug: str | None, project_id: str | None, api_key_ref: str) -> None:
    """Write .codex-fleet.yml for a local Plane project."""
    if not workspace_slug or not project_id:
        raise click.ClickException(
            "Plane workspace/project are required. Create or open the local Plane project, then run:\n"
            "  PLANE_WORKSPACE_SLUG=<workspace-slug> PLANE_PROJECT_ID=<project-id> "
            "PLANE_API_KEY=<api-key> codex-fleet plane-configure --repo ."
        )
    path = write_plane_config(
        repo.expanduser().absolute(),
        base_url=url,
        workspace_slug=workspace_slug,
        project_id=project_id,
        api_key_ref=api_key_ref,
        api_key_value=os.getenv("PLANE_API_KEY"),
    )
    console.print(f"Config ready: {path}")
    if api_key_ref == "$PLANE_API_KEY" and os.getenv("PLANE_API_KEY"):
        console.print("Saved local Plane API key to .codex-fleet/secrets.env.")


@main.command("plane-local-bootstrap")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--url", default=DEFAULT_PLANE_URL, show_default=True)
@click.option("--workspace-slug", default="codex-fleet", show_default=True)
@click.option("--workspace-name", default="codex-fleet", show_default=True)
@click.option("--project-name", default="Codex Fleet", show_default=True)
@click.option("--project-identifier", default="CF", show_default=True)
def plane_local_bootstrap_cmd(
    repo: Path,
    url: str,
    workspace_slug: str,
    workspace_name: str,
    project_name: str,
    project_identifier: str,
) -> None:
    """Create/reuse local Plane data and configure codex-fleet without manual API key setup."""
    repo = repo.expanduser().absolute()
    try:
        result = bootstrap_local_plane(
            repo,
            workspace_slug=workspace_slug,
            workspace_name=workspace_name,
            project_name=project_name,
            project_identifier=project_identifier,
        )
    except PlaneLocalBootstrapError as exc:
        raise click.ClickException(str(exc)) from exc
    path = write_plane_config(
        repo,
        base_url=url,
        workspace_slug=result.workspace_slug,
        project_id=result.project_id,
        api_key_ref="$PLANE_API_KEY",
        api_key_value=result.api_key,
    )
    console.print(f"Config ready: {path}")
    console.print(f"Workspace: {result.workspace_slug} ({'created' if result.workspace_created else 'existing'})")
    console.print(f"Project: {result.project_name} ({result.project_id})")
    console.print(f"Local Plane user: {result.user_email}")
    console.print("Saved local Plane API key to .codex-fleet/secrets.env.")


@main.command("run-once")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
def run_once(repo: Path) -> None:
    """Run one deterministic memory-backed task."""
    config = load_config(repo)
    item = WorkItem(
        id="memory-1",
        identifier="CF-1",
        title="Smoke task",
        description="Create a fake run marker in an isolated worktree.",
        state="Ready",
        priority=2,
    )
    tracker = MemoryTracker([item], active_states=config.tracker.active_states)
    result = Orchestrator(config=config, tracker=tracker, runner=FakeRunner()).run_once()
    _print_result(result)


@main.command("run-configured")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--fake", is_flag=True, help="Use fake runner instead of real Codex runner.")
@click.option("--fake-fail", is_flag=True, help="Make the fake runner fail for Rework-path demos.")
def run_configured(repo: Path, fake: bool, fake_fail: bool) -> None:
    """Run one configured work item using .codex-fleet.yml."""
    config = load_config(repo)
    tracker = build_tracker(config)
    runner = build_runner(config, fake=fake, fake_succeed=not fake_fail)
    store = RunStore(default_store_path(config.repo))
    result = Orchestrator(config=config, tracker=tracker, runner=runner, store=store).run_once()
    _print_result(result)


@main.command("run")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--fake", is_flag=True, help="Use fake runner instead of real Codex runner.")
@click.option("--fake-fail", is_flag=True, help="Make the fake runner fail for Rework-path demos.")
def run_cmd(repo: Path, fake: bool, fake_fail: bool) -> None:
    """Run the next Ready work item once."""
    config = load_config(repo)
    tracker = build_tracker(config)
    runner = build_runner(config, fake=fake, fake_succeed=not fake_fail)
    store = RunStore(default_store_path(config.repo))
    result = Orchestrator(config=config, tracker=tracker, runner=runner, store=store).run_once()
    _print_result(result)


@main.command("logs")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--limit", type=int, default=10, show_default=True)
def logs(repo: Path, limit: int) -> None:
    """Show recent codex-fleet runs."""
    store = RunStore(default_store_path(repo.expanduser().absolute()))
    table = Table(title="codex-fleet runs")
    table.add_column("identifier")
    table.add_column("status")
    table.add_column("run")
    table.add_column("worktree")
    table.add_column("error")
    for run in store.list_runs(limit=limit):
        table.add_row(
            run.identifier,
            run.status,
            run.id,
            run.worktree_path or "",
            run.error or "",
        )
    console.print(table)


@main.command("open")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--plane-url", default=DEFAULT_PLANE_URL, show_default=True)
@click.option("--path", "project_path", type=click.Path(path_type=Path), default=None, help="Project folder to prefill.")
@click.option("--dashboard", is_flag=True, help="Open the codex-fleet dashboard instead of onboarding.")
@click.option("--no-browser", is_flag=True, help="Print the URL without opening a browser.")
def open_cmd(repo: Path, plane_url: str, project_path: Path | None, dashboard: bool, no_browser: bool) -> None:
    """Open the branded local Plane fork URL."""
    repo = repo.expanduser().absolute()
    if dashboard:
        api_url = f"http://{DEFAULT_LOCAL_API_HOST}:{DEFAULT_LOCAL_API_PORT}"
        url = build_plane_login_url(
            repo,
            api_url=api_url,
            plane_url=plane_url,
            redirect_path="codex-fleet/dashboard",
        )
    else:
        url = build_onboarding_url(repo, plane_url=plane_url, project_path=project_path)
    console.print(url)
    if not no_browser:
        open_plane(url)


@main.command("daemon")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--fake", is_flag=True, help="Use fake runner instead of real Codex runner.")
@click.option("--fake-fail", is_flag=True, help="Make the fake runner fail for Rework-path demos.")
@click.option("--ticks", type=int, default=None, help="Optional max ticks for tests or smoke runs.")
@click.option("--sleep", "sleep_seconds", type=float, default=5.0, show_default=True)
@click.option("--verbose/--quiet", default=True, show_default=True, help="Print per-tick dispatch activity.")
def daemon(repo: Path, fake: bool, fake_fail: bool, ticks: int | None, sleep_seconds: float, verbose: bool) -> None:
    """Run the polling loop."""
    config = load_config(repo)
    _print_watch_banner(config, fake=fake, fake_fail=fake_fail)
    _ensure_app_server_protocol_ready(fake=fake)
    stats = FleetDaemon(
        config,
        fake_runner=fake,
        fake_runner_succeed=not fake_fail,
        on_tick=_daemon_tick_logger if verbose else None,
    ).run(
        max_ticks=ticks,
        sleep_seconds=sleep_seconds,
    )
    console.print(f"Ticks: {stats.ticks}")
    console.print(f"Dispatched: {stats.dispatched}")


@main.command("up")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--fake", is_flag=True, help="Use fake runner instead of real Codex runner.")
@click.option("--fake-fail", is_flag=True, help="Make the fake runner fail for Rework-path demos.")
@click.option("--once", is_flag=True, help="Run one tick and exit.")
@click.option("--sleep", "sleep_seconds", type=float, default=5.0, show_default=True)
@click.option("--stock-plane", is_flag=True, help="Do not install the branded codex-fleet Plane frontend.")
@click.option("--verbose/--quiet", default=True, show_default=True, help="Print per-tick dispatch activity.")
def up(repo: Path, fake: bool, fake_fail: bool, once: bool, sleep_seconds: float, stock_plane: bool, verbose: bool) -> None:
    """Main local entrypoint. Starts local Plane, then starts the loop."""
    repo = repo.expanduser().absolute()
    config_path = repo / ".codex-fleet.yml"
    plane_client: PlaneClient | None = None
    if not config_path.exists():
        console.print("No codex-fleet config found. Bootstrapping local Plane.")
        try:
            config = _bootstrap_default_local_plane_config(repo, DEFAULT_PLANE_URL)
            plane_client = build_plane_client(config)
        except (PlaneManagerError, PlaneLocalBootstrapError, ValueError) as exc:
            console.print(f"Local Plane could not be prepared automatically: {exc}")
            console.print("Opening fallback onboarding. Create or link a project from the UI.")
            _serve_plane_fork_onboarding(
                repo,
                project_path=repo,
                host=DEFAULT_LOCAL_API_HOST,
                port=DEFAULT_PLANE_PREVIEW_PORT,
                no_open=False,
                unsafe_allow_remote=False,
                once=once,
            )
            return
    else:
        config = load_config(repo)
    if config.tracker.kind == "memory":
        console.print("Configured memory tracker; bootstrapping local Plane for the full Codex Fleet product.")
        try:
            config = _bootstrap_default_local_plane_config(repo, DEFAULT_PLANE_URL)
            plane_client = build_plane_client(config)
        except (PlaneManagerError, PlaneLocalBootstrapError, ValueError) as exc:
            console.print(f"Local Plane could not be prepared automatically: {exc}")
            console.print(
                "Plane UI actions are not connected because this repo is still using the memory tracker. "
                "Run `make plane-up` and `python -m codex_fleet plane-local-bootstrap --repo .` to configure Plane."
            )
    if config.tracker.kind == "plane":
        plane_url = config.tracker.plane_base_url or DEFAULT_PLANE_URL
        try:
            plane_client = plane_client or build_plane_client(config)
        except ValueError as exc:
            if _is_loopback_url(plane_url):
                console.print(f"Configured Plane is missing a required setting: {exc}")
                console.print("Bootstrapping local Plane credentials automatically.")
                try:
                    config = _bootstrap_default_local_plane_config(repo, plane_url)
                    plane_client = build_plane_client(config)
                except (PlaneManagerError, PlaneLocalBootstrapError, ValueError) as bootstrap_exc:
                    console.print(f"Local Plane bootstrap could not run automatically: {bootstrap_exc}")
                    console.print("Opening the branded local Plane fork onboarding path instead.")
                    _serve_plane_fork_onboarding(
                        repo,
                        project_path=repo,
                        host=DEFAULT_LOCAL_API_HOST,
                        port=DEFAULT_PLANE_PREVIEW_PORT,
                        no_open=False,
                        unsafe_allow_remote=False,
                        once=once,
                    )
                    return
            else:
                raise click.ClickException(f"Configured Plane is missing a required setting: {exc}") from exc
        status = check_plane_url(plane_url)
        if not status.ready and _is_loopback_url(plane_url):
            console.print(f"Starting local Plane at {plane_url}...")
            try:
                start_plane(config.repo, url=plane_url)
            except PlaneManagerError as exc:
                raise click.ClickException(f"Local Plane could not be started: {exc}") from exc
        elif status.ready and _is_loopback_url(plane_url):
            try:
                if ensure_plane_runtime_config(config.repo, url=plane_url):
                    console.print("Updated local Plane runtime settings.")
            except PlaneManagerError as exc:
                raise click.ClickException(f"Local Plane runtime could not be configured: {exc}") from exc
        status = wait_for_plane(plane_url)
        console.print(f"Plane URL: {plane_url}")
        console.print(f"Plane ready: {status.ready} ({status.message})")
        if not status.ready:
            raise click.ClickException("Configured Plane is not ready.")
        if _is_loopback_url(plane_url) and not stock_plane:
            console.print("Installing branded codex-fleet Plane frontend...")
            try:
                build_dir = _ensure_plane_frontend_build(config.repo)
                frontend = install_branded_plane_frontend(config.repo, build_dir)
            except (PlaneManagerError, PlanePreviewError) as exc:
                raise click.ClickException(f"Branded Plane frontend could not be installed: {exc}") from exc
            console.print(f"{frontend.message} Container: {frontend.container}")
        try:
            ensure_plane_bootstrap(plane_client, config.tracker.active_states)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            console.print("Configured Plane project no longer exists. Opening project setup without creating a project.")
            _serve_plane_fork_onboarding(
                repo,
                project_path=repo,
                host=DEFAULT_LOCAL_API_HOST,
                port=DEFAULT_PLANE_PREVIEW_PORT,
                no_open=False,
                unsafe_allow_remote=False,
                once=once,
            )
            return
        try:
            view_result = ensure_local_plane_project_views(config)
        except PlaneViewBootstrapError as exc:
            raise click.ClickException(f"Plane saved views could not be bootstrapped: {exc}") from exc
        if view_result.skipped:
            console.print(f"Plane saved views skipped: {view_result.skipped_reason}")
        else:
            console.print(
                f"Plane saved views: {len(view_result.created)} created, {len(view_result.existing)} existing"
            )
        reconciled = reconcile_registered_projects(
            config.repo,
            ProjectRegistry(default_project_registry_path(config.repo)),
            control_config=config,
            allow_bootstrap=False,
        )
        _print_project_reconciliation_summary(reconciled)
    report = scan_repo(config.repo, codex_command=config.codex.command)
    console.print(render_report(report))
    console.print(f"Tracker: {config.tracker.kind}")
    if config.tracker.kind == "memory":
        console.print(
            "Plane UI actions are not connected because this repo is using the memory tracker. "
            "Run plane-local-bootstrap or plane-configure to use the Plane board."
        )
    console.print(f"Workspace root: {config.workspace.root}")
    _print_watch_banner(config, fake=fake, fake_fail=fake_fail)
    _ensure_app_server_protocol_ready(fake=fake)
    max_ticks = 1 if once else None
    api_server: LocalApiServer | None = None
    if config.tracker.kind == "plane" and _is_loopback_url(config.tracker.plane_base_url or "") and not once:
        api_host = _loopback_host_for_url(config.tracker.plane_base_url or DEFAULT_PLANE_URL)
        api_server, api_url = _create_local_api_server_with_fallback(config.repo, host=api_host)
        api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
        api_thread.start()
        console.print(f"codex-fleet API: {api_url}")
        console.print("Connected Plane session: local API handoff will be opened automatically.")
        console.print("Logs: terminal output is live; use `make up 2>&1 | tee /tmp/codex-fleet-up.log` to save it.")
        board_path = _plane_board_path(config)
        if board_path:
            board_url = f"{(config.tracker.plane_base_url or DEFAULT_PLANE_URL).rstrip('/')}/{board_path.lstrip('/')}"
            login_url = build_plane_login_url(
                config.repo,
                api_url=api_url,
                plane_url=config.tracker.plane_base_url or DEFAULT_PLANE_URL,
                redirect_path=board_path,
            )
            console.print(f"Plane board: {board_url}")
            console.print("Opening connected Plane session through codex-fleet local login.")
            open_plane(login_url)
        write_runtime_record(
            config.repo,
            kind="plane-daemon",
            url=config.tracker.plane_base_url or DEFAULT_PLANE_URL,
            api_url=api_url,
        )
    try:
        stats = FleetDaemon(
            config,
            fake_runner=fake,
            fake_runner_succeed=not fake_fail,
            on_tick=_daemon_tick_logger if verbose else None,
        ).run(
            max_ticks=max_ticks,
            sleep_seconds=sleep_seconds,
        )
    finally:
        if api_server is not None:
            api_server.shutdown()
            remove_runtime_record(config.repo)
    console.print(f"Ticks: {stats.ticks}")
    console.print(f"Dispatched: {stats.dispatched}")


@main.command("down")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--preview-port", type=int, default=DEFAULT_PLANE_PREVIEW_PORT, show_default=True)
@click.option("--api-port", type=int, default=DEFAULT_LOCAL_API_PORT, show_default=True)
@click.option("--plane/--no-plane", default=True, show_default=True, help="Stop local self-host Plane Docker runtime.")
@click.option("--ports/--no-ports", default=True, show_default=True, help="Stop local preview/API listeners by port.")
def down(repo: Path, preview_port: int, api_port: int, plane: bool, ports: bool) -> None:
    """Stop local codex-fleet preview/API services and local Plane when present."""
    repo = repo.expanduser().absolute()
    runtime = read_runtime_record(repo)
    results = [stop_runtime_process(repo)]
    if ports:
        results.extend(stop_loopback_ports(_runtime_ports(runtime, defaults=[api_port, preview_port])))
    if plane:
        plane_result = stop_plane_runtime(repo)
        results.append(plane_result)
        if not plane_result.stopped:
            results.append(stop_plane_app_containers())
    for result in results:
        status = "stopped" if result.stopped else "skip"
        console.print(f"{status.upper()} {result.target}: {result.message}")


def _create_local_api_server_with_fallback(repo: Path, *, host: str) -> tuple[LocalApiServer, str]:
    try:
        server = create_local_api_server(repo, host=host, port=DEFAULT_LOCAL_API_PORT)
    except OSError:
        server = create_local_api_server(repo, host=host, port=0)
    except LocalApiError as exc:
        raise click.ClickException(f"codex-fleet local API could not be started: {exc}") from exc
    actual_host, actual_port = server.server_address[:2]
    host_text = host if host in {"127.0.0.1", "localhost"} else str(actual_host)
    return server, f"http://{host_text}:{actual_port}"


def _runtime_ports(record: RuntimeRecord | None, *, defaults: list[int]) -> list[int]:
    ports: list[int] = []
    for url in (record.url, record.api_url) if record is not None else ():
        if not url:
            continue
        port = urlparse(url).port
        if port is not None:
            ports.append(port)
    ports.extend(defaults)
    return list(dict.fromkeys(ports))


def _serve_plane_fork_onboarding(
    repo: Path,
    *,
    project_path: Path | None,
    host: str,
    port: int,
    no_open: bool,
    unsafe_allow_remote: bool,
    once: bool,
) -> None:
    source_dir = repo / "apps" / "plane"
    build_dir = source_dir / "apps" / "web" / "build" / "client"
    if not (build_dir / "index.html").exists():
        if not (source_dir / "apps" / "web").exists():
            console.print(
                "Preparing branded Plane source: cloning pinned Plane source. "
                "This can take a few minutes on first run."
            )
        else:
            console.print(
                "Preparing branded Plane source: installing web dependencies and building Plane UI."
            )
    try:
        api_server, api_url = _create_local_api_server_with_fallback(repo, host=host)
        preview_server = create_plane_preview_server(
            repo,
            host=host,
            port=port,
            unsafe_allow_remote=unsafe_allow_remote,
        )
    except (LocalApiError, PlanePreviewError) as exc:
        raise click.ClickException(str(exc)) from exc

    api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
    preview_thread = threading.Thread(target=preview_server.serve_forever, daemon=True)
    api_thread.start()
    preview_thread.start()

    url = build_onboarding_url(
        repo,
        plane_url=preview_server.url,
        project_path=project_path or repo,
        api_url=api_url,
    )
    console.print(f"codex-fleet API: {api_url}")
    console.print(f"Plane fork URL: {preview_server.url}")
    console.print(f"Fallback onboarding URL: {url}")
    console.print("The local API token is in the URL fragment and is intended only for loopback use.")
    write_runtime_record(
        repo,
        kind="plane-fork-preview",
        url=preview_server.url,
        api_url=api_url,
    )
    if not no_open and not once:
        open_plane(url)
    if once:
        api_server.shutdown()
        preview_server.shutdown()
        remove_runtime_record(repo)
        return
    console.print("Press Ctrl-C to stop local codex-fleet onboarding.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
            api_server.shutdown()
            preview_server.shutdown()
            remove_runtime_record(repo)
            console.print("Stopped local codex-fleet onboarding.")


def _bootstrap_default_local_plane_config(repo: Path, plane_url: str) -> FleetConfig:
    console.print(f"Starting local Plane at {plane_url}...")
    start_plane(repo, url=plane_url)
    status = wait_for_plane(plane_url)
    console.print(f"Plane URL: {plane_url}")
    console.print(f"Plane ready: {status.ready} ({status.message})")
    if not status.ready:
        raise PlaneManagerError("Local Plane did not become ready before the timeout.")
    result = bootstrap_local_plane(repo)
    path = write_plane_config(
        repo,
        base_url=plane_url,
        workspace_slug=result.workspace_slug,
        project_id=result.project_id,
        api_key_ref="$PLANE_API_KEY",
        api_key_value=result.api_key,
    )
    console.print(f"Config ready: {path}")
    console.print(f"Workspace: {result.workspace_slug} ({'created' if result.workspace_created else 'existing'})")
    console.print(f"Project: {result.project_name} ({result.project_id})")
    console.print("Saved local Plane API key to .codex-fleet/secrets.env.")
    return load_config(repo)


def _plane_board_path(config: FleetConfig) -> str | None:
    if not config.tracker.plane_workspace_slug or not config.tracker.plane_project_id:
        return None
    return f"{config.tracker.plane_workspace_slug}/projects/{config.tracker.plane_project_id}/issues/"


def _ensure_plane_frontend_build(repo: Path) -> Path:
    try:
        require_plane_source(repo)
    except PlaneManagerError as exc:
        raise PlanePreviewError(str(exc)) from exc
    build_dir = default_plane_build_dir(repo)
    if (build_dir / "index.html").exists() and not _plane_frontend_build_is_stale(repo, build_dir):
        return build_dir
    return prepare_plane_preview_build(repo)


def _plane_frontend_build_is_stale(repo: Path, build_dir: Path) -> bool:
    source_root = repo.expanduser().absolute() / "apps" / "plane" / "apps" / "web"
    stamp = build_dir / "index.html"
    if not stamp.exists():
        return True
    build_mtime = stamp.stat().st_mtime
    watched_paths = [
        source_root / "app" / "codex-fleet",
        source_root / "app" / "(all)" / "[workspaceSlug]" / "(projects)" / "projects" / "(detail)" / "[projectId]" / "fleet-logs",
        source_root / "app" / "(all)" / "[workspaceSlug]" / "(projects)" / "projects" / "(detail)" / "[projectId]" / "agents",
        source_root / "app" / "(all)" / "[workspaceSlug]" / "(projects)" / "projects" / "(detail)" / "[projectId]" / "runs",
        source_root / "app" / "(all)" / "[workspaceSlug]" / "(projects)" / "projects" / "(detail)" / "[projectId]" / "artifacts",
        source_root / "app" / "(all)" / "[workspaceSlug]" / "(projects)" / "projects" / "(detail)" / "[projectId]" / "codex-settings",
        source_root / "ce" / "components" / "projects" / "create",
        source_root / "core" / "components" / "instance",
        source_root / "core" / "lib" / "wrappers" / "instance-wrapper.tsx",
        source_root / "core" / "components" / "issues" / "issue-modal",
        source_root / "components" / "codex-fleet",
    ]
    for path in watched_paths:
        if path.is_file() and path.stat().st_mtime > build_mtime:
            return True
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and child.suffix in {".ts", ".tsx", ".css", ".js", ".jsx"} and child.stat().st_mtime > build_mtime:
                    return True
    return False


def _print_project_reconciliation_summary(results: list[ProjectReconciliation]) -> None:
    if not results:
        return
    ready = sum(1 for result in results if result.can_run)
    relinked = sum(1 for result in results if result.plane_status == "relinked")
    created = sum(1 for result in results if result.plane_status == "created")
    needs_attention = sum(1 for result in results if not result.can_run)
    console.print(
        "Projects restored: "
        f"{ready} ready, {relinked} relinked, {created} recreated, {needs_attention} need attention"
    )
    if needs_attention:
        console.print(
            "Tip: run `codex-fleet project prune-stale --repo .` to preview cleanup of missing, non-git, or duplicate local project registrations."
        )


def _is_loopback_url(url: str) -> bool:
    host = urlparse(url).hostname
    return host in {"127.0.0.1", "localhost", "::1"}


def _loopback_host_for_url(url: str) -> str:
    host = urlparse(url).hostname
    return host if host in {"127.0.0.1", "localhost"} else DEFAULT_LOCAL_API_HOST


def _print_watch_banner(config: FleetConfig, *, fake: bool, fake_fail: bool) -> None:
    active = ", ".join(config.tracker.active_states)
    runner = "fake runner" if fake else "Codex App Server runner"
    if fake and fake_fail:
        runner = "fake failing runner"
    console.print(f"Watching Plane states: {active}")
    console.print("Move work items to Ready to run them. Running is treated as already claimed.")
    console.print(f"Runner: {runner}")


def _ensure_app_server_protocol_ready(*, fake: bool) -> None:
    if fake:
        return
    try:
        validate_app_server_turn_start_shape()
    except ProtocolError as exc:
        raise click.ClickException(
            f"Codex Fleet App Server protocol check failed: {exc}. Restart with `make stop && make up` after updating."
        ) from exc


def _daemon_tick_logger(tick_number: int, tick_results: list[ProjectDaemonTick]) -> None:
    dispatched = 0
    quiet_messages = 0
    timestamp = time.strftime("%H:%M:%S")
    console.print(f"[bold cyan]{timestamp} tick {tick_number}[/bold cyan] polling registered projects")
    for project_tick in tick_results:
        repo = getattr(project_tick, "repo", None)
        active_states = ", ".join(getattr(project_tick, "active_states", ()) or ("Ready",))
        ready_count = getattr(project_tick, "ready_count", None)
        error = getattr(project_tick, "error", None)
        if error is not None:
            message = str(getattr(error, "message", error))
            if _is_stale_project_daemon_error(message):
                console.print(f"[yellow]  {repo}: skipped stale project: {message}[/yellow]")
                quiet_messages += 1
            else:
                console.print(f"[red]  {repo}: error: {message}[/red]")
            continue
        visible_count = getattr(project_tick, "visible_count", None)
        if ready_count is None:
            console.print(f"  {repo}: checking states [{active_states}]")
        else:
            suffix = f" / {visible_count} total on board" if visible_count is not None else ""
            console.print(f"  {repo}: {ready_count} item(s) in [{active_states}]{suffix}")
        results = getattr(project_tick, "results", [])
        for result in results:
            message = getattr(result, "message", "")
            if getattr(result, "dispatched", False):
                dispatched += 1
                run = getattr(result, "run", None)
                run_id = getattr(run, "id", "unknown")
                item = getattr(run, "item", None)
                identifier = getattr(item, "identifier", "unknown")
                worktree = getattr(run, "worktree_path", None)
                console.print(f"[green]  {repo}: dispatched {identifier} run={run_id}[/green]")
                if worktree:
                    console.print(f"    worktree: {worktree}")
            else:
                quiet_messages += 1
                console.print(f"    {message}")
    if dispatched == 0 and quiet_messages == 0:
        console.print("  no registered project work checked")
    if dispatched == 0:
        console.print("[dim]  waiting: move a work item to Ready in a linked project[/dim]")


def _is_stale_project_daemon_error(message: str) -> bool:
    stale_markers = (
        "Project folder is missing.",
        "Project folder is not a git repository.",
        "Plane project is already mapped to another local project.",
        "Registered project is not a git repository.",
    )
    return any(marker in message for marker in stale_markers)


@main.command("internal-smoke-ui", hidden=True)
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=int, default=8765, show_default=True)
@click.option("--unsafe-allow-remote", is_flag=True, help="Allow binding the unauthenticated demo UI outside loopback.")
def ui(repo: Path, host: str, port: int, unsafe_allow_remote: bool) -> None:
    """Start the internal no-credentials smoke harness UI."""
    config = load_config(repo)
    server = create_local_ui_server(
        config,
        host=host,
        port=port,
        unsafe_allow_remote=unsafe_allow_remote,
    )
    console.print(f"Internal smoke harness UI: {server.url}")
    console.print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        console.print("Stopped internal smoke harness UI.")


@main.command("create-pr")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--branch", required=True)
@click.option("--title", required=True)
@click.option("--body", default="Created by codex-fleet.")
@click.option("--base", default="main", show_default=True)
def create_pr(repo: Path, branch: str, title: str, body: str, base: str) -> None:
    """Manually push a branch and open a draft PR using local git and gh."""
    result = create_draft_pr(
        PrRequest(repo=repo, branch_name=branch, title=title, body=body, base_branch=base)
    )
    console.print(result.message)


def _print_result(result: object) -> None:
    message = getattr(result, "message", "")
    console.print(message)
    run = getattr(result, "run", None)
    if run is not None:
        console.print(f"Run: {run.id}")
        console.print(f"Status: {run.status.value}")
        console.print(f"Worktree: {run.worktree_path}")


if __name__ == "__main__":
    main()
