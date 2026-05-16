from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from codex_fleet.config import FleetConfig, TrackerConfig, WorkspaceConfig, load_config
from codex_fleet.execution_settings import (
    config_with_codex_settings,
    merged_work_item_settings,
    settings_value,
)
from codex_fleet.factory import build_runner, build_tracker, default_store_path
from codex_fleet.models import RunStatus, WorkItem, WorkItemState
from codex_fleet.orchestrator import Orchestrator, OrchestratorResult
from codex_fleet.project_reconcile import reconcile_project
from codex_fleet.project_registry import (
    LocalProject,
    ProjectRegistry,
    default_project_registry_path,
    discover_git_root,
)
from codex_fleet.runner import Runner
from codex_fleet.store import RunStore, StoredClaim, TaskMetadata
from codex_fleet.tracker import Tracker
from codex_fleet.transitions import (
    is_active_child_state,
    is_blocking_child_state,
    is_successful_child_state,
)


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
        self.reconcile_needs_input_answers()
        self.reconcile_completed_parents()
        return self._run_one()

    def tick_many(self) -> list[OrchestratorResult]:
        self.reconcile_stale_claims()
        self.reconcile_needs_input_answers()
        self.reconcile_completed_parents()
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

    def reconcile_needs_input_answers(self) -> int:
        resolved = 0
        for pending in self.store.list_open_needs_input():
            items = self.tracker.fetch_items_by_ids([pending.item_id])
            if not items or items[0].state.lower() != WorkItemState.NEEDS_INPUT.value.lower():
                continue
            try:
                comments = self.tracker.list_comments(pending.item_id)
            except Exception as exc:  # noqa: BLE001 - comment sync should not stop dispatch.
                self.store.add_event(pending.run_id, "needs_input_comment_sync_failed", {"item_id": pending.item_id, "error": str(exc)})
                continue
            answer_comment = next(
                (
                    comment
                    for comment in reversed(comments)
                    if _is_human_answer_comment(comment, asked_at=pending.asked_at)
                ),
                None,
            )
            if answer_comment is None:
                continue
            self._resolve_needs_input(
                pending.run_id,
                item_id=pending.item_id,
                question=pending.question,
                answer=answer_comment.body_text,
                answer_comment_id=answer_comment.id,
            )
            resolved += 1
        return resolved

    def _resolve_needs_input(
        self,
        run_id: str,
        *,
        item_id: str,
        question: str,
        answer: str,
        answer_comment_id: str | None,
    ) -> None:
        clean_answer = answer.strip()
        self.store.resolve_needs_input(run_id, answer=clean_answer, answer_comment_id=answer_comment_id)
        self._append_human_answer(item_id, question=question, answer=clean_answer, run_id=run_id, comment_id=answer_comment_id)
        self.store.add_event(
            run_id,
            "needs_input_resolved",
            {"item_id": item_id, "answer_comment_id": answer_comment_id, "state": WorkItemState.READY.value},
        )
        self.tracker.create_comment(item_id, "codex-fleet captured your answer and moved this task back to Ready.")
        self.tracker.update_item_state(item_id, WorkItemState.READY.value)

    def _append_human_answer(
        self,
        item_id: str,
        *,
        question: str,
        answer: str,
        run_id: str,
        comment_id: str | None,
    ) -> None:
        metadata = self.store.get_task_metadata(item_id)
        if metadata is None:
            self.store.upsert_task_metadata(
                item_id=item_id,
                source="human-answer",
                settings={
                    "human_answers": [
                        {
                            "question": question,
                            "answer": answer,
                            "run_id": run_id,
                            "comment_id": comment_id,
                        }
                    ]
                },
            )
            return
        settings = dict(metadata.settings)
        answers = settings.get("human_answers")
        answer_list = [entry for entry in answers if isinstance(entry, dict)] if isinstance(answers, list) else []
        answer_list.append({"question": question, "answer": answer, "run_id": run_id, "comment_id": comment_id})
        settings["human_answers"] = answer_list[-10:]
        self.store.update_task_settings(item_id, settings)

    def reconcile_completed_parents(self) -> int:
        completed = 0
        for parent_id in self.store.list_parent_item_ids_with_children():
            child_metadata = self.store.list_child_task_metadata(parent_id)
            if not child_metadata:
                continue
            item_ids = [parent_id, *(metadata.item_id for metadata in child_metadata)]
            items = {item.id: item for item in self.tracker.fetch_items_by_ids(item_ids)}
            parent = items.get(parent_id)
            if parent is None or parent.state.lower() != WorkItemState.PLANNING.value.lower():
                continue
            parent_metadata = self.store.get_task_metadata(parent_id)
            parent_settings = parent_metadata.settings if parent_metadata is not None else {}
            if parent_settings.get("workflow_mode") == "full_auto":
                continue
            child_items = [items.get(metadata.item_id) for metadata in child_metadata]
            if any(item is None for item in child_items):
                continue
            blocker_items = [item for item in child_items if item is not None and is_blocking_child_state(item.state)]
            if blocker_items:
                self._record_parent_blocked(parent, blocker_items, child_metadata)
                continue
            if any(item is not None and is_active_child_state(item.state) for item in child_items):
                continue
            if not all(item is not None and is_successful_child_state(item.state) for item in child_items):
                continue
            self.tracker.update_item_state(parent_id, WorkItemState.HUMAN_REVIEW.value)
            self.tracker.create_comment(parent_id, _parent_completed_comment(child_metadata))
            run_id = child_metadata[0].parent_run_id
            if run_id is not None:
                self.store.add_event(
                    run_id,
                    "parent_children_completed",
                    {
                        "parent_item_id": parent_id,
                        "state": WorkItemState.HUMAN_REVIEW.value,
                        "child_count": len(child_metadata),
                    },
                )
            completed += 1
        return completed

    def _record_parent_blocked(
        self,
        parent: WorkItem,
        blocker_items: list[WorkItem],
        child_metadata: list[TaskMetadata],
    ) -> None:
        payload = {
            "parent_item_id": parent.id,
            "blockers": [
                {"item_id": item.id, "identifier": item.identifier, "state": item.state}
                for item in blocker_items
            ],
        }
        run_id = child_metadata[0].parent_run_id or _parent_event_run_id(parent.id)
        existing = [
            event
            for event in self.store.list_events(run_id)
            if event.kind == "parent_blocked" and event.payload == payload
        ]
        if existing:
            return
        self.store.add_event(run_id, "parent_blocked", payload)
        self.tracker.create_comment(parent.id, _parent_blocked_comment(blocker_items))

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
            agent_role=str(settings.get("agent_role") or "implementer"),
            human_answers=_human_answers_from_settings(settings),
        )

    def _agent_task_settings_for_item(self, item: WorkItem) -> tuple[str, int]:
        settings = self._settings_for_item(item)
        return str(settings_value(settings, "workflow_mode")), int(str(settings_value(settings, "max_depth")))

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
                self.tracker.update_item_state(claim.item_id, WorkItemState.NEEDS_INPUT.value)
            except Exception:  # noqa: BLE001 - state failure is recorded for operator visibility.
                self.store.add_event(claim.run_id, "stale_claim_state_update_failed", {"item_id": claim.item_id})


_ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.CLAIM_ACQUIRED.value,
    RunStatus.PREPARING_WORKSPACE.value,
    RunStatus.WORKSPACE_READY.value,
    RunStatus.RUNNER_STARTED.value,
    RunStatus.RUNNER_STREAMING.value,
    RunStatus.RUNNING_CODEX.value,
    RunStatus.CANCEL_REQUESTED.value,
}


def _parent_completed_comment(child_metadata: list[TaskMetadata]) -> str:
    child_count = len(child_metadata)
    return (
        "codex-fleet moved this parent to Human Review because all "
        f"{child_count} child task{'s' if child_count != 1 else ''} reached Human Review or Done."
    )


def _parent_blocked_comment(blocker_items: list[WorkItem]) -> str:
    lines = "\n".join(f"- `{item.identifier}` is `{item.state}`" for item in blocker_items)
    return (
        "codex-fleet is keeping this parent in Planning because child work needs attention:\n\n"
        f"{lines}\n\n"
        "Resolve the child task, then codex-fleet will reconcile the parent again."
    )


def _parent_event_run_id(parent_item_id: str) -> str:
    return f"parent:{parent_item_id}"


def _human_answers_from_settings(settings: dict[str, object]) -> list[dict[str, object]]:
    answers = settings.get("human_answers")
    if not isinstance(answers, list):
        return []
    return [answer for answer in answers if isinstance(answer, dict)]


def _is_human_answer_comment(comment: object, *, asked_at: str) -> bool:
    if bool(getattr(comment, "is_codex_fleet", False)):
        return False
    body = str(getattr(comment, "body_text", "") or "").strip()
    if not body:
        return False
    normalized = body.lower()
    if normalized.startswith("codex-fleet") or "codex-fleet started run" in normalized:
        return False
    created_at = getattr(comment, "created_at", None)
    if isinstance(created_at, datetime):
        asked = _parse_store_datetime(asked_at)
        if asked is not None and created_at.replace(tzinfo=None) <= asked.replace(tzinfo=None):
            return False
    return True


def _parse_store_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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

        for project in sorted(projects, key=lambda registered: registered.id, reverse=True):
            repo = project.repo_path.expanduser().absolute()
            if repo in seen:
                continue
            reconciliation = reconcile_project(
                self.config.repo,
                self.registry,
                project,
                control_config=self.config,
                allow_bootstrap=False,
            )
            project = reconciliation.project
            if not reconciliation.can_run:
                self._config_errors.append(ProjectDaemonError(repo=repo, message=reconciliation.status_message))
                continue
            if not project.plane_workspace_slug or not project.plane_project_id:
                continue
            plane_key = (project.plane_workspace_slug, project.plane_project_id)
            if plane_key in seen_plane_projects:
                self._config_errors.append(ProjectDaemonError(repo=repo, message="Plane project is already mapped to another local project. Re-link or remove the duplicate registration."))
                continue
            current_git_root = discover_git_root(repo)
            if current_git_root is None:
                self._config_errors.append(ProjectDaemonError(repo=repo, message="Registered project is not a git repository. Re-link it or create a new project."))
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
