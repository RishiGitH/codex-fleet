from pathlib import Path

import click
from rich.console import Console

from codex_fleet.budget import file_size
from codex_fleet.config import load_config, write_default_config
from codex_fleet.daemon import FleetDaemon
from codex_fleet.doctor import render_report, scan_repo
from codex_fleet.factory import build_plane_client, build_runner, build_tracker, default_store_path
from codex_fleet.harness import apply_harness, plan_harness
from codex_fleet.models import WorkItem
from codex_fleet.orchestrator import Orchestrator
from codex_fleet.plane_bootstrap import check_plane_readiness, ensure_plane_states
from codex_fleet.pr_flow import PrRequest, create_draft_pr
from codex_fleet.runner import FakeRunner
from codex_fleet.store import RunStore
from codex_fleet.tracker import MemoryTracker

console = Console()


@click.group()
def main() -> None:
    """Local control plane for Codex work runs."""


@main.command()
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
def doctor(repo: Path) -> None:
    """Scan a repository for readiness."""
    console.print(render_report(scan_repo(repo)))


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
    report = scan_repo(config.repo)
    console.print(f"Repo: {config.repo}")
    console.print(f"Tracker: {config.tracker.kind}")
    console.print(f"Workspace root: {config.workspace.root}")
    console.print(f"Readiness: {report.score}/100")
    console.print(f"Run store: {default_store_path(config.repo)}")


@main.command("budget")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
def budget(repo: Path) -> None:
    """Show size of important guidance files."""
    repo = repo.expanduser().resolve()
    for rel in ["AGENTS.md", "README.md"]:
        path = repo / rel
        console.print(f"{rel}: {file_size(path)} bytes")


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


@main.command("plane-bootstrap")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
def plane_bootstrap(repo: Path) -> None:
    """Create missing codex-fleet workflow states in Plane."""
    config = load_config(repo)
    client = build_plane_client(config)
    result = ensure_plane_states(client, config.tracker.active_states)
    if result.created_states:
        console.print("Created states:")
        for state in result.created_states:
            console.print(f"- {state}")
    else:
        console.print("No states created.")
    console.print(f"Plane ready: {result.readiness.ok}")


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
@click.option("--fake", is_flag=True, help="Use fake runner instead of real Codex App Server.")
def run_configured(repo: Path, fake: bool) -> None:
    """Run one configured work item using .codex-fleet.yml."""
    config = load_config(repo)
    tracker = build_tracker(config)
    runner = build_runner(config, fake=fake)
    store = RunStore(default_store_path(config.repo))
    result = Orchestrator(config=config, tracker=tracker, runner=runner, store=store).run_once()
    _print_result(result)


@main.command("daemon")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--fake", is_flag=True, help="Use fake runner instead of real Codex App Server.")
@click.option("--ticks", type=int, default=None, help="Optional max ticks for tests or smoke runs.")
@click.option("--sleep", "sleep_seconds", type=float, default=5.0, show_default=True)
def daemon(repo: Path, fake: bool, ticks: int | None, sleep_seconds: float) -> None:
    """Run the polling loop."""
    config = load_config(repo)
    stats = FleetDaemon(config, fake_runner=fake).run(max_ticks=ticks, sleep_seconds=sleep_seconds)
    console.print(f"Ticks: {stats.ticks}")
    console.print(f"Dispatched: {stats.dispatched}")


@main.command("up")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
@click.option("--fake", is_flag=True, help="Use fake runner instead of real Codex App Server.")
@click.option("--once", is_flag=True, help="Run one tick and exit.")
@click.option("--sleep", "sleep_seconds", type=float, default=5.0, show_default=True)
def up(repo: Path, fake: bool, once: bool, sleep_seconds: float) -> None:
    """Main local entrypoint. Runs doctor, shows config, then starts the loop."""
    config = load_config(repo)
    report = scan_repo(config.repo)
    console.print(render_report(report))
    console.print(f"Tracker: {config.tracker.kind}")
    console.print(f"Workspace root: {config.workspace.root}")
    max_ticks = 1 if once else None
    stats = FleetDaemon(config, fake_runner=fake).run(max_ticks=max_ticks, sleep_seconds=sleep_seconds)
    console.print(f"Ticks: {stats.ticks}")
    console.print(f"Dispatched: {stats.dispatched}")


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
