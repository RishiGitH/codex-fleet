from __future__ import annotations

import os
from pathlib import Path

from codex_fleet.config import FleetConfig
from codex_fleet.models import WorkItem
from codex_fleet.plane import PlaneClient, PlaneSettings, PlaneTracker
from codex_fleet.runner import CodexAppServerRunner, FakeRunner, Runner
from codex_fleet.tracker import MemoryTracker, Tracker


def build_tracker(config: FleetConfig) -> Tracker:
    if config.tracker.kind == "memory":
        item = WorkItem(
            id="memory-1",
            identifier="CF-1",
            title="Smoke task",
            description="Create a fake run marker in an isolated worktree.",
            state="Ready",
            priority=2,
        )
        return MemoryTracker([item], active_states=config.tracker.active_states)

    if config.tracker.kind == "plane":
        return PlaneTracker(build_plane_client(config), active_states=config.tracker.active_states)

    raise ValueError(f"Unsupported tracker kind: {config.tracker.kind}")


def build_plane_client(config: FleetConfig) -> PlaneClient:
    settings = PlaneSettings(
        base_url=_required(config.tracker.plane_base_url or os.getenv("PLANE_BASE_URL"), "PLANE_BASE_URL"),
        api_key=_required(config.tracker.plane_api_key or os.getenv("PLANE_API_KEY"), "PLANE_API_KEY"),
        workspace_slug=_required(
            config.tracker.plane_workspace_slug or os.getenv("PLANE_WORKSPACE_SLUG"),
            "PLANE_WORKSPACE_SLUG",
        ),
        project_id=_required(
            config.tracker.plane_project_id or os.getenv("PLANE_PROJECT_ID"),
            "PLANE_PROJECT_ID",
        ),
    )
    return PlaneClient(settings)


def build_runner(
    config: FleetConfig,
    *,
    fake: bool = False,
    fake_succeed: bool = True,
    agent_role: str | None = None,
    human_answers: list[dict[str, object]] | None = None,
) -> Runner:
    if fake:
        return FakeRunner(succeed=fake_succeed)
    return CodexAppServerRunner(
        command=config.codex.command if "app-server" in config.codex.command else "codex app-server",
        approval_policy=config.codex.approval_policy,
        sandbox_mode=config.codex.sandbox_mode,
        model=config.codex.model,
        reasoning_effort=config.codex.reasoning_effort,
        agent_role=agent_role,
        human_answers=human_answers,
        timeout_seconds=max(1, config.codex.turn_timeout_ms // 1000),
    )


def default_store_path(repo: Path) -> Path:
    return repo.expanduser().absolute() / ".codex-fleet" / "runs.sqlite3"


def _required(value: str | None, name: str) -> str:
    if value is None or value.strip() == "":
        raise ValueError(f"Missing required setting: {name}")
    return value
