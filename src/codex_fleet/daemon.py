from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codex_fleet.config import FleetConfig, TrackerConfig, WorkspaceConfig, load_config
from codex_fleet.execution_settings import (
    config_with_codex_settings,
    merged_work_item_settings,
    settings_value,
)
from codex_fleet.factory import build_plane_client, build_runner, build_tracker, default_store_path
from codex_fleet.models import RunStatus, WorkItem, WorkItemState
from codex_fleet.orchestrator import Orchestrator, OrchestratorResult
from codex_fleet.project_registry import (
    LocalProject,
    ProjectRegistry,
    default_project_registry_path,
    discover_git_root,
)
from codex_fleet.runner import Runner
from codex_fleet.store import RunStore, StoredClaim
from codex_fleet.tracker import Tracker


@dataclass(frozen=True)
class DaemonStats:
    ticks: int
    dispatched: int


@dataclass(frozen=True)
class ProjectDaemonError:
    repo: Path
    message: str


@dataclass(frozen=True)
class ProjectDaemonTick:
    repo: Path
    results: list[OrchestratorResult]
    ready_count: int | None = None
    visible_count: int | None = None
    active_states: tuple[str, ...] = ()
    error: ProjectDaemonError | None = None


@dataclass(frozen=True)
class ProjectDaemonConfig:
    config: FleetConfig
    codex_settings: dict[str, object]


class FleetDaemon:
    def __init__(
        self,
        config: FleetConfig,
        *,
        fake_runner: bool = False,
        fake_runner_succeed: bool = True,
        codex_settings: dict[str, object] | None = None,
    ) -> None:
        self.codex_settings = codex_settings or {}
        self.config = config_with_codex_settings(config, self.codex_settings)
        self.fake_runner = fake_runner
        self.fake_runner_succeed = fake_runner_succeed
        self.store = RunStore(default_store_path(self.config.repo))
        self.tracker: Tracker = build_tracker(self.config)
        self.runner: Runner = build_runner(self.config, fake=fake_runner, fake_succeed=fake_runner_succeed)

    def run(self, *, max_ticks: int | None = None, sleep_seconds: float | None = None) -> DaemonStats:
        ticks = 0
        dispatched = 0
        interval = sleep_seconds
        if interval is None:
            interval = max(1.0, self.config.codex.stall_timeout_ms / 1000)

        while max_ticks is None or ticks < max_ticks:
            results = self.tick_many()
            ticks += 1
            dispatched += sum(1 for result in results if result.dispatched)
            if max_ticks is not None and ticks >= max_ticks:
                break
            time.sleep(interval)

        return DaemonStats(ticks=ticks, dispatched=dispatched)

    def tick(self) -> OrchestratorResult:
        self.reconcile_stale_claims()
        return self._run_one()

    def tick_many(self) -> list[OrchestratorResult]:
        self.reconcile_stale_claims()
        limit = max(1, self.config.agent.max_concurrent_agents)
        results: list[OrchestratorResult] = []
        for _ in range(limit):
            result = self._run_one()
            results.append(result)
            if not result.dispatched:
                break
        return results

    def reconcile_stale_claims(self) -> int:
        stale_claims = self.store.release_stale_claims(max_age_seconds=self._claim_ttl_seconds())
        for claim in stale_claims:
            self._record_stale_claim(claim)
        return len(stale_claims)

    def _run_one(self) -> OrchestratorResult:
        return Orchestrator(
            config=self.config,
            tracker=self.tracker,
            runner=self.runner,
            store=self.store,
            runner_factory=self._runner_for_item,
            agent_task_settings_resolver=self._agent_task_settings_for_item,
        ).run_once()

    def _settings_for_item(self, item: WorkItem) -> dict[str, object]:
        return merged_work_item_settings(self.codex_settings, item, self.store)

    def _runner_for_item(self, item: WorkItem) -> Runner:
        settings = self._settings_for_item(item)
        return build_runner(
            config_with_codex_settings(self.config, settings),
            fake=self.fake_runner,
            fake_succeed=self.fake_runner_succeed,
        )

    def _agent_task_settings_for_item(self, item: WorkItem) -> tuple[str, int]:
        settings = self._settings_for_item(item)
        return str(settings_value(settings, "agent_task_mode")), int(settings_value(settings, "max_task_depth"))

    def _claim_ttl_seconds(self) -> float:
        # A Codex turn may legitimately run much longer than the polling stall
        # interval, so stale recovery must use the longer runner timeout.
        return max(self.config.codex.turn_timeout_ms, self.config.codex.stall_timeout_ms) / 1000

    def _record_stale_claim(self, claim: StoredClaim) -> None:
        message = (
            "codex-fleet released a stale run claim "
            f"`{claim.run_id}` after {int(self._claim_ttl_seconds())} seconds without completion."
        )
        self.store.add_event(
            claim.run_id,
            "stale_claim_released",
            {
                "item_id": claim.item_id,
                "claim_updated_at": claim.updated_at,
                "ttl_seconds": int(self._claim_ttl_seconds()),
            },
        )
        run = self.store.get_run(claim.run_id)
        if run is not None and run.status in _ACTIVE_RUN_STATUSES:
            self.store.update_run_status(claim.run_id, RunStatus.STALLED.value, error=message)

        try:
            items = self.tracker.fetch_items_by_ids([claim.item_id])
        except Exception:  # noqa: BLE001 - stale recovery should not stop normal dispatch.
            self.store.add_event(claim.run_id, "stale_claim_lookup_failed", {"item_id": claim.item_id})
            return
        if not items:
            return
        item = items[0]
        try:
            self.tracker.create_comment(claim.item_id, message)
        except Exception:  # noqa: BLE001 - recovery should continue when comments fail.
            self.store.add_event(claim.run_id, "stale_claim_comment_failed", {"item_id": claim.item_id})
        if item.state == WorkItemState.RUNNING.value:
            try:
                self.tracker.update_item_state(claim.item_id, WorkItemState.REWORK.value)
            except Exception:  # noqa: BLE001 - state failure is recorded for operator visibility.
                self.store.add_event(claim.run_id, "stale_claim_state_update_failed", {"item_id": claim.item_id})


_ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.PREPARING_WORKSPACE.value,
    RunStatus.RUNNING_CODEX.value,
}


def default_daemon_store(repo: Path) -> Path:
    return default_store_path(repo)


class MultiProjectFleetDaemon:
    """Poll the control repo plus Plane projects registered from the local UI."""

    def __init__(
        self,
        config: FleetConfig,
        *,
        fake_runner: bool = False,
        fake_runner_succeed: bool = True,
        registry: ProjectRegistry | None = None,
        on_tick: Callable[[int, list[ProjectDaemonTick]], None] | None = None,
    ) -> None:
        self.config = config
        self.fake_runner = fake_runner
        self.fake_runner_succeed = fake_runner_succeed
        self.registry = registry or ProjectRegistry(default_project_registry_path(config.repo))
        self.last_errors: list[ProjectDaemonError] = []
        self._config_errors: list[ProjectDaemonError] = []
        self.on_tick = on_tick

    def run(self, *, max_ticks: int | None = None, sleep_seconds: float | None = None) -> DaemonStats:
        ticks = 0
        dispatched = 0
        interval = sleep_seconds
        if interval is None:
            interval = max(1.0, self.config.codex.stall_timeout_ms / 1000)

        while max_ticks is None or ticks < max_ticks:
            tick_results = self.tick_many()
            ticks += 1
            if self.on_tick is not None:
                self.on_tick(ticks, tick_results)
            dispatched += sum(1 for project in tick_results for result in project.results if result.dispatched)
            if max_ticks is not None and ticks >= max_ticks:
                break
            time.sleep(interval)

        return DaemonStats(ticks=ticks, dispatched=dispatched)

    def tick_many(self) -> list[ProjectDaemonTick]:
        remaining = max(1, self.config.agent.max_concurrent_agents)
        ticks: list[ProjectDaemonTick] = []
        errors: list[ProjectDaemonError] = []

        for project_config in self._project_configs():
            if remaining <= 0:
                break
            try:
                daemon = FleetDaemon(
                    project_config.config,
                    fake_runner=self.fake_runner,
                    fake_runner_succeed=self.fake_runner_succeed,
                    codex_settings=project_config.codex_settings,
                )
                try:
                    visible_items = daemon.tracker.client.list_work_items() if hasattr(daemon.tracker, "client") else None
                    visible_count = len(visible_items) if visible_items is not None else None
                    ready_count = len(daemon.tracker.fetch_candidate_items())
                except Exception:  # noqa: BLE001 - keep polling even if only the visibility preflight fails.
                    visible_count = None
                    ready_count = None
                result = daemon.tick()
                results = [result]
                remaining -= sum(1 for result in results if result.dispatched)
                ticks.append(
                    ProjectDaemonTick(
                        repo=project_config.config.repo,
                        results=results,
                        ready_count=ready_count,
                        visible_count=visible_count,
                        active_states=tuple(project_config.config.tracker.active_states),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one project must not stop the fleet.
                error = ProjectDaemonError(repo=project_config.config.repo, message=str(exc))
                errors.append(error)
                ticks.append(
                    ProjectDaemonTick(
                        repo=project_config.config.repo,
                        results=[],
                        active_states=tuple(project_config.config.tracker.active_states),
                        error=error,
                    )
                )

        self.last_errors = [*self._config_errors, *errors]
        for error in self._config_errors:
            ticks.append(ProjectDaemonTick(repo=error.repo, results=[], error=error))
        return ticks

    def _project_configs(self) -> list[ProjectDaemonConfig]:
        configs: list[ProjectDaemonConfig] = []
        seen: set[Path] = set()
        seen_plane_projects: set[tuple[str, str]] = set()
        projects = self.registry.list_projects()
        project_by_repo = {project.repo_path.expanduser().absolute(): project for project in projects}

        root_repo = self.config.repo.expanduser().absolute()
        root_project = project_by_repo.get(root_repo)
        configs.append(ProjectDaemonConfig(self.config, root_project.codex_settings if root_project else {}))
        seen.add(root_repo)
        if self.config.tracker.plane_workspace_slug and self.config.tracker.plane_project_id:
            seen_plane_projects.add((self.config.tracker.plane_workspace_slug, self.config.tracker.plane_project_id))
        self._config_errors = []
        memberships = self._plane_project_memberships()

        for project in sorted(projects, key=lambda registered: registered.id, reverse=True):
            repo = project.repo_path.expanduser().absolute()
            if repo in seen or not project.plane_workspace_slug or not project.plane_project_id:
                continue
            plane_key = (project.plane_workspace_slug, project.plane_project_id)
            if plane_key in seen_plane_projects:
                self._config_errors.append(ProjectDaemonError(repo=repo, message="Plane project is already mapped to another local project. Re-link or remove the duplicate registration."))
                continue
            current_git_root = discover_git_root(repo)
            if current_git_root is None:
                self._config_errors.append(ProjectDaemonError(repo=repo, message="Registered project is not a git repository. Re-link it or create a new project."))
                continue
            if memberships.get(project.plane_project_id) is False:
                self._config_errors.append(
                    ProjectDaemonError(
                        repo=repo,
                        message=f"Current Plane user is not a member of project {project.plane_project_id}. Open the project card and click Join, or recreate/link the project.",
                    )
                )
                continue
            try:
                project_config = self._load_registered_project_config(project)
            except Exception as exc:  # noqa: BLE001 - malformed project config is reported and skipped.
                self._config_errors.append(ProjectDaemonError(repo=repo, message=str(exc)))
                continue
            configs.append(ProjectDaemonConfig(project_config, project.codex_settings))
            seen.add(repo)
            seen_plane_projects.add(plane_key)
        return configs

    def _load_registered_project_config(self, project: LocalProject) -> FleetConfig:
        repo = project.repo_path.expanduser().absolute()
        config_path = repo / ".codex-fleet.yml"
        if config_path.exists():
            config = load_config(repo)
            if config.tracker.kind == "plane" and config.tracker.plane_project_id:
                return config

        if self.config.tracker.kind != "plane":
            raise ValueError("Registered Plane project cannot be polled because the control repo is not using Plane.")
        return FleetConfig(
            repo=repo,
            tracker=TrackerConfig(
                kind="plane",
                active_states=list(self.config.tracker.active_states),
                handoff_states=list(self.config.tracker.handoff_states),
                terminal_states=list(self.config.tracker.terminal_states),
                plane_base_url=self.config.tracker.plane_base_url,
                plane_api_key=self.config.tracker.plane_api_key,
                plane_workspace_slug=project.plane_workspace_slug or self.config.tracker.plane_workspace_slug,
                plane_project_id=project.plane_project_id,
            ),
            agent=self.config.agent.model_copy(deep=True),
            workspace=WorkspaceConfig(),
            codex=self.config.codex.model_copy(deep=True),
            token=self.config.token.model_copy(deep=True),
        ).resolved()

    def _plane_project_memberships(self) -> dict[str, bool]:
        if self.config.tracker.kind != "plane":
            return {}
        try:
            projects = build_plane_client(self.config).list_projects()
        except Exception as exc:  # noqa: BLE001 - membership repair is best-effort and visible.
            self._config_errors.append(ProjectDaemonError(repo=self.config.repo, message=f"Could not inspect Plane project membership: {exc}"))
            return {}
        return {str(project.get("id")): bool(project.get("is_member", True)) for project in projects if project.get("id")}
