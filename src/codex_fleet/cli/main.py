from pathlib import Path

import click
from rich.console import Console

from codex_fleet.budget import file_size
from codex_fleet.config import load_config, write_default_config
from codex_fleet.doctor import render_report, scan_repo
from codex_fleet.models import WorkItem
from codex_fleet.orchestrator import Orchestrator
from codex_fleet.runner import FakeRunner
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


@main.command("budget")
@click.option("--repo", type=click.Path(path_type=Path), default=Path.cwd())
def budget(repo: Path) -> None:
    """Show size of important guidance files."""
    repo = repo.expanduser().resolve()
    for rel in ["AGENTS.md", "README.md"]:
        path = repo / rel
        console.print(f"{rel}: {file_size(path)} bytes")


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
    console.print(result.message)
    if result.run is not None:
        console.print(f"Run: {result.run.id}")
        console.print(f"Status: {result.run.status.value}")
        console.print(f"Worktree: {result.run.worktree_path}")


if __name__ == "__main__":
    main()
