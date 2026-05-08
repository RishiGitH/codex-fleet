from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from codex_fleet.config import FleetConfig
from codex_fleet.models import ProposedTask, RunRecord, RunStatus, WorkItem, WorkItemState
from codex_fleet.runner import Runner
from codex_fleet.store import RunStore
from codex_fleet.tracker import Tracker
from codex_fleet.workspace import WorktreeManager


@dataclass(frozen=True)
class OrchestratorResult:
    dispatched: bool
    run: RunRecord | None
    message: str


class Orchestrator:
    """Small Symphony-style coordinator.

    The production daemon will extend this with polling, retries, stall detection,
    and concurrent dispatch. The core single-run path remains testable here.
    """

    def __init__(
        self,
        config: FleetConfig,
        tracker: Tracker,
        runner: Runner,
        store: RunStore | None = None,
        agent_task_mode: str = "review_and_approve",
        max_task_depth: int = 2,
        max_child_tasks_per_run: int = 8,
        runner_factory: Callable[[WorkItem], Runner] | None = None,
        agent_task_settings_resolver: Callable[[WorkItem], tuple[str, int]] | None = None,
    ) -> None:
        self.config = config
        self.tracker = tracker
        self.runner = runner
        self.store = store
        self.agent_task_mode = agent_task_mode
        self.max_task_depth = max(0, max_task_depth)
        self.max_child_tasks_per_run = max(1, max_child_tasks_per_run)
        self.runner_factory = runner_factory
        self.agent_task_settings_resolver = agent_task_settings_resolver
        self.worktrees = WorktreeManager(config.repo, config.workspace.root)

    def run_once(self) -> OrchestratorResult:
        candidates = self.tracker.fetch_candidate_items()
        candidates = [item for item in candidates if not self._dependency_blocked(item)]
        if not candidates:
            return OrchestratorResult(False, None, "No candidate work items found.")

        item = sorted(candidates, key=_dispatch_sort_key)[0]
        runner = self.runner_factory(item) if self.runner_factory is not None else self.runner
        if self.agent_task_settings_resolver is not None:
            agent_task_mode, max_task_depth = self.agent_task_settings_resolver(item)
            max_task_depth = max(0, max_task_depth)
        else:
            agent_task_mode = self.agent_task_mode
            max_task_depth = self.max_task_depth
        run = RunRecord(id=str(uuid4()), item=item, status=RunStatus.QUEUED)
        run.runner_name = type(runner).__name__
        run.agent_role, run.agent_name, run.agent_avatar = _agent_identity(item)
        run.settings = {
            "agent_task_mode": agent_task_mode,
            "automation_mode": _automation_mode_from_agent_task_mode(agent_task_mode),
            "max_task_depth": max_task_depth,
            "approval_policy": self.config.codex.approval_policy,
            "sandbox_mode": self.config.codex.sandbox_mode,
        }
        if self.store is not None:
            metadata = self.store.get_task_metadata(item.id)
            if metadata is not None and metadata.role:
                run.agent_role, run.agent_name, run.agent_avatar = _agent_identity_from_role(metadata.role)
                run.settings["agent_role"] = run.agent_role
        run.model = _runner_model(runner)
        if self.store is not None and not self.store.try_claim_item(item.id, run.id):
            return OrchestratorResult(False, None, f"Work item already claimed: {item.identifier}")
        self._persist(run)
        self._event(run, "claimed", {"item_id": item.id, "identifier": item.identifier})

        try:
            self.tracker.update_item_state(item.id, WorkItemState.RUNNING.value)
            self.tracker.create_comment(item.id, f"codex-fleet started run `{run.id}` for `{item.identifier}`.")
            self._event(run, "started", {"state": WorkItemState.RUNNING.value})
            if not self._confirm_item_state(item.id, WorkItemState.RUNNING.value):
                message = "Plane did not confirm the item left Ready after codex-fleet claimed it. The run is held to prevent duplicate dispatch."
                run.mark(RunStatus.STALLED, message)
                self._persist(run)
                self._event(run, "state_update_pending", {"requested_state": WorkItemState.RUNNING.value})
                self.tracker.create_comment(item.id, message)
                return OrchestratorResult(True, run, message)
        except Exception:
            if self.store is not None:
                self._event(run, "failed", {"stage": "tracker_start", "error": "tracker update failed"})
                self.store.finish_claim(item.id, run.id, "failed")
            raise

        try:
            run.mark(RunStatus.PREPARING_WORKSPACE)
            self._persist(run)
            self._event(run, "workspace_preparing", {})
            workspace = self.worktrees.prepare(item)
            run.worktree_path = workspace.path
            run.branch_name = workspace.branch_name
            self._persist(run)
            self._event(
                run,
                "workspace_prepared",
                {
                    "branch_name": workspace.branch_name,
                    "worktree_path": str(workspace.path),
                },
            )

            run.mark(RunStatus.RUNNING_CODEX)
            self._persist(run)
            self._event(
                run,
                "runner_started",
                {"runner": type(runner).__name__, "agent_task_mode": agent_task_mode, "max_task_depth": max_task_depth},
            )
            result = runner.run(item, workspace.path)
            run.token_usage = result.token_usage
            self._persist(run)
            self._event(
                run,
                "runner_finished",
                {
                    "success": result.success,
                    "changed_files": list(result.changed_files),
                    "test_commands": list(result.test_commands),
                    "artifact_count": len(result.artifacts),
                    "token_usage": _token_usage_payload(result.token_usage),
                },
            )
            if result.success:
                proposed_items = self._create_proposed_tasks(
                    run,
                    result.proposed_tasks,
                    agent_task_mode=agent_task_mode,
                    max_task_depth=max_task_depth,
                )
                if proposed_items and agent_task_mode == "agent_task_planner":
                    run.mark(RunStatus.HUMAN_REVIEW)
                    self._persist(run)
                    self._artifacts(run, result.artifacts)
                    self._event(
                        run,
                        "parent_waiting",
                        {"state": WorkItemState.PLANNING.value, "child_count": len(proposed_items)},
                    )
                    self.tracker.create_comment(
                        item.id,
                        _success_comment(run, result.summary, result.test_commands, proposed_items)
                        + "\n\nParent is waiting while child tasks run.",
                    )
                    self.tracker.update_item_state(item.id, WorkItemState.PLANNING.value)
                    if self.store is not None:
                        self.store.finish_claim(item.id, run.id, "completed")
                    return OrchestratorResult(True, run, "Run created child tasks and moved parent to Planning.")
                run.mark(RunStatus.HUMAN_REVIEW)
                self._persist(run)
                self._artifacts(run, result.artifacts)
                self._event(
                    run,
                    "completed",
                    {"state": WorkItemState.HUMAN_REVIEW.value, "proposed_task_count": len(proposed_items)},
                )
                self.tracker.create_comment(
                    item.id,
                    _success_comment(run, result.summary, result.test_commands, proposed_items),
                )
                self.tracker.update_item_state(item.id, WorkItemState.HUMAN_REVIEW.value)
                if self._confirm_item_state(item.id, WorkItemState.HUMAN_REVIEW.value):
                    self._event(run, "state_update_confirmed", {"state": WorkItemState.HUMAN_REVIEW.value})
                else:
                    self._event(run, "state_update_pending", {"requested_state": WorkItemState.HUMAN_REVIEW.value})
                    self.tracker.create_comment(item.id, _state_update_pending_comment(run, WorkItemState.HUMAN_REVIEW.value))
                    return OrchestratorResult(True, run, "Run completed, but Plane state confirmation is pending.")
                if self.store is not None:
                    self.store.finish_claim(item.id, run.id, "completed")
                return OrchestratorResult(True, run, "Run completed and moved to Human Review.")

            if result.needs_input is not None:
                run.mark(RunStatus.NEEDS_INPUT, result.needs_input.question)
                self._persist(run)
                self._artifacts(run, result.artifacts)
                self._event(
                    run,
                    "needs_input",
                    {
                        "question": result.needs_input.question,
                        "state": result.needs_input.suggested_state,
                    },
                )
                self.tracker.create_comment(item.id, _needs_input_comment(run, result.needs_input.question))
                self.tracker.update_item_state(item.id, WorkItemState.NEEDS_INPUT.value)
                if self.store is not None:
                    self.store.finish_claim(item.id, run.id, "blocked")
                return OrchestratorResult(True, run, "Run needs human input.")

            run.mark(RunStatus.REWORK, result.error)
            self._persist(run)
            self._artifacts(run, result.artifacts)
            self._event(run, "failed", {"error": result.error or result.summary, "state": WorkItemState.REWORK.value})
            self.tracker.create_comment(item.id, f"codex-fleet run failed: {result.error or result.summary}")
            self.tracker.update_item_state(item.id, WorkItemState.REWORK.value)
            if self._confirm_item_state(item.id, WorkItemState.REWORK.value):
                self._event(run, "state_update_confirmed", {"state": WorkItemState.REWORK.value})
            else:
                self._event(run, "state_update_pending", {"requested_state": WorkItemState.REWORK.value})
                self.tracker.create_comment(item.id, _state_update_pending_comment(run, WorkItemState.REWORK.value))
                return OrchestratorResult(True, run, "Run failed, but Plane state confirmation is pending.")
            if self.store is not None:
                self.store.finish_claim(item.id, run.id, "failed")
            return OrchestratorResult(True, run, "Run failed and moved to Rework.")
        except Exception as exc:  # noqa: BLE001 - orchestration boundary converts failures to tracker status.
            run.mark(RunStatus.BLOCKED, str(exc))
            self._persist(run)
            self._event(run, "blocked", {"error": str(exc), "state": WorkItemState.BLOCKED.value})
            self.tracker.create_comment(item.id, f"codex-fleet is blocked by local setup or orchestration error: {exc}")
            self.tracker.update_item_state(item.id, WorkItemState.BLOCKED.value)
            if self._confirm_item_state(item.id, WorkItemState.BLOCKED.value):
                self._event(run, "state_update_confirmed", {"state": WorkItemState.BLOCKED.value})
            else:
                self._event(run, "state_update_pending", {"requested_state": WorkItemState.BLOCKED.value})
                self.tracker.create_comment(item.id, _state_update_pending_comment(run, WorkItemState.BLOCKED.value))
                return OrchestratorResult(True, run, "Run errored, but Plane state confirmation is pending.")
            if self.store is not None:
                self.store.finish_claim(item.id, run.id, "failed")
            return OrchestratorResult(True, run, "Run errored and moved to Blocked.")

    def _persist(self, run: RunRecord) -> None:
        if self.store is None:
            return
        self.store.upsert_run(
            run_id=run.id,
            item_id=run.item.id,
            identifier=run.item.identifier,
            status=run.status.value,
            branch_name=run.branch_name,
            worktree_path=str(run.worktree_path) if run.worktree_path else None,
            error=run.error,
            runner_name=run.runner_name,
            agent_role=run.agent_role,
            agent_name=run.agent_name,
            agent_avatar=run.agent_avatar,
            model=run.model,
            settings=run.settings,
            token_usage=_token_usage_payload(run.token_usage),
        )

    def _event(self, run: RunRecord, kind: str, payload: dict[str, object]) -> None:
        if self.store is None:
            return
        self.store.add_event(run.id, kind, payload)

    def _artifacts(self, run: RunRecord, artifacts: tuple[Path, ...]) -> None:
        if self.store is None:
            return
        for artifact in artifacts:
            size_bytes = None
            digest = None
            if artifact.exists() and artifact.is_file():
                data = artifact.read_bytes()
                size_bytes = len(data)
                digest = sha256(data).hexdigest()
            self.store.add_artifact(run.id, str(artifact), size_bytes=size_bytes, sha256=digest)

    def _create_proposed_tasks(
        self,
        run: RunRecord,
        tasks: tuple[ProposedTask, ...],
        *,
        agent_task_mode: str,
        max_task_depth: int,
    ) -> tuple[WorkItem, ...]:
        if agent_task_mode == "manual":
            if tasks:
                self._event(run, "proposed_tasks_skipped", {"reason": "agent task creation disabled", "count": len(tasks)})
            return ()
        created: list[WorkItem] = []
        parent_depth = self._task_depth(run.item)
        child_depth = parent_depth + 1
        auto_run = agent_task_mode == "agent_task_planner" and child_depth <= max_task_depth
        state = WorkItemState.READY.value if auto_run else WorkItemState.BACKLOG.value
        source_label = "agent-followup" if auto_run else "agent-proposed"
        parent_metadata = self.store.get_task_metadata(run.item.id) if self.store is not None else None
        root_item_id = parent_metadata.root_item_id if parent_metadata is not None and parent_metadata.root_item_id else run.item.id
        for task in tasks[: self.max_child_tasks_per_run]:
            task_state = task.suggested_state if task.suggested_state in {WorkItemState.BACKLOG.value, WorkItemState.READY.value} else state
            if not auto_run:
                task_state = WorkItemState.BACKLOG.value
            try:
                item = self.tracker.create_work_item(
                    title=task.title,
                    description=_proposed_task_description(run, task, depth=child_depth, auto_run=auto_run),
                    state=task_state,
                    labels=_proposed_task_labels(task, source_label),
                )
            except Exception as exc:  # noqa: BLE001 - follow-up creation must not fail the completed run.
                self._event(run, "proposed_task_failed", {"title": task.title, "error": str(exc)})
                continue
            if item is not None:
                created.append(item)
                if self.store is not None:
                    self.store.upsert_task_metadata(
                        item_id=item.id,
                        source=source_label,
                        depth=child_depth,
                        parent_item_id=run.item.id,
                        parent_identifier=run.item.identifier,
                        parent_run_id=run.id,
                        created_by_run_id=run.id,
                        root_item_id=root_item_id,
                        role=task.role or "worker",
                        depends_on=task.depends_on,
                        generation=child_depth,
                        approval_mode=_automation_mode_from_agent_task_mode(agent_task_mode),
                        settings={
                            "agent_task_mode": agent_task_mode,
                            "automation_mode": _automation_mode_from_agent_task_mode(agent_task_mode),
                            "max_task_depth": max_task_depth,
                            "agent_role": task.role or "worker",
                        },
                    )
                try:
                    self.tracker.create_comment(item.id, _proposed_task_source_comment(run, depth=child_depth, auto_run=auto_run))
                except Exception as exc:  # noqa: BLE001 - source comments should not fail a completed run.
                    self._event(run, "proposed_task_comment_failed", {"item_id": item.id, "error": str(exc)})
                self._event(
                    run,
                    "proposed_task_created",
                    {
                        "item_id": item.id,
                        "identifier": item.identifier,
                        "title": item.title,
                        "labels": list(item.labels),
                        "source": source_label,
                        "depth": child_depth,
                        "parent_item_id": run.item.id,
                        "parent_identifier": run.item.identifier,
                        "state": task_state,
                        "auto_run": auto_run,
                        "role": task.role,
                        "depends_on": list(task.depends_on),
                    },
                )
        if len(tasks) > self.max_child_tasks_per_run:
            self._event(
                run,
                "proposed_tasks_truncated",
                {"received": len(tasks), "created": self.max_child_tasks_per_run},
            )
        return tuple(created)

    def _task_depth(self, item: WorkItem) -> int:
        if self.store is not None:
            metadata = self.store.get_task_metadata(item.id)
            if metadata is not None:
                return metadata.depth
        return _task_depth_from_description(item.description)

    def _dependency_blocked(self, item: WorkItem) -> bool:
        if self.store is None:
            return False
        metadata = self.store.get_task_metadata(item.id)
        if metadata is None or not metadata.depends_on:
            return False
        dependencies = self.tracker.fetch_items_by_ids(list(metadata.depends_on))
        if len(dependencies) < len(metadata.depends_on):
            return True
        return any(dependency.state.lower() not in {"human review", "done"} for dependency in dependencies)

    def _confirm_item_state(self, item_id: str, expected_state: str, *, attempts: int = 4, delay_seconds: float = 0.25) -> bool:
        expected = expected_state.lower()
        for attempt in range(attempts):
            items = self.tracker.fetch_items_by_ids([item_id])
            if items and items[0].state.lower() == expected:
                return True
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
        return False


def _dispatch_sort_key(item: object) -> tuple[int, str]:
    priority = getattr(item, "priority", None)
    rank = priority if isinstance(priority, int) and 1 <= priority <= 4 else 5
    identifier = getattr(item, "identifier", "")
    return rank, str(identifier)


def _success_comment(
    run: RunRecord,
    summary: str,
    test_commands: tuple[str, ...],
    proposed_items: tuple[WorkItem, ...] = (),
) -> str:
    tests = "\n".join(f"- {command}" for command in test_commands) or "- not reported"
    proposed = ""
    if proposed_items:
        proposed_lines = "\n".join(f"- `{item.identifier}` {item.title}" for item in proposed_items)
        proposed = f"\n\nAgent-proposed follow-up tasks:\n{proposed_lines}"
    return (
        f"codex-fleet completed run `{run.id}`.\n\n"
        f"Branch: `{run.branch_name}`\n"
        f"Workspace: `{run.worktree_path}`\n\n"
        f"Summary: {summary}\n\n"
        f"Verification:\n{tests}"
        f"{proposed}"
    )


def _state_update_pending_comment(run: RunRecord, requested_state: str) -> str:
    return (
        f"codex-fleet requested `{requested_state}` for run `{run.id}`, but Plane still reports this item in an active state. "
        "The claim is being held so this work is not dispatched again."
    )


def _proposed_task_description(run: RunRecord, task: ProposedTask, *, depth: int, auto_run: bool) -> str:
    state = "Ready for automatic follow-up run." if auto_run else "Needs human review before running."
    source = (
        f"\n\n<p><strong>Source:</strong> proposed by codex-fleet run "
        f"<code>{run.id}</code> while working on <code>{run.item.identifier}</code>.</p>"
        f"\n<p><strong>Depth:</strong> {depth}</p>"
        f"\n<p><strong>Role:</strong> {task.role or 'worker'}</p>"
        f"\n<p><strong>Depends on:</strong> {', '.join(task.depends_on) if task.depends_on else 'none'}</p>"
        f"\n<p><strong>Automation:</strong> {state}</p>"
    )
    body = task.description or "Agent proposed this follow-up task."
    return f"{body}{source}"


def _proposed_task_source_comment(run: RunRecord, *, depth: int, auto_run: bool) -> str:
    action = "It was placed in Ready because auto-followups are enabled." if auto_run else "Review it, then move it to Ready when it should run."
    return (
        f"codex-fleet proposed this follow-up from run `{run.id}` while working on "
        f"`{run.item.identifier}`. Depth: `{depth}`. {action}"
    )


def _proposed_task_labels(task: ProposedTask, source_label: str) -> tuple[str, ...]:
    labels = [source_label]
    if task.role:
        labels.append(f"agent-{task.role}")
    labels.extend(label for label in task.labels if label not in {"agent-proposed", "agent-followup"})
    return tuple(dict.fromkeys(labels))


def _needs_input_comment(run: RunRecord, question: str) -> str:
    return (
        f"codex-fleet needs input for run `{run.id}`.\n\n"
        f"Question: {question}\n\n"
        "Answer in a comment, then move this work item back to Ready."
    )


def _task_depth_from_description(description: str | None) -> int:
    if not description:
        return 0
    import re

    match = re.search(r"<strong>Depth:</strong>\s*(\d+)", description)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _runner_model(runner: Runner) -> str | None:
    command = getattr(runner, "command", None)
    if not isinstance(command, str):
        return None
    parts = command.split()
    for index, part in enumerate(parts):
        if part in {"--model", "-m"} and index + 1 < len(parts):
            return parts[index + 1]
        if part.startswith("--model="):
            return part.split("=", 1)[1]
    return None


def _agent_identity(item: WorkItem) -> tuple[str, str, str]:
    labels = {label.lower() for label in item.labels}
    if "agent-lead" in labels:
        return "lead", "Lead", "L"
    if "agent-code_scout" in labels or "agent-code-scout" in labels or "agent-scout" in labels:
        return "code_scout", "Scout", "S"
    if "agent-reviewer" in labels or "agent-harness_reviewer" in labels or "agent-security_reviewer" in labels:
        return "reviewer", "Reviewer", "R"
    if "agent-worker" in labels:
        return "worker", "Worker", "W"
    return "implementer", "Implementer", "I"


def _agent_identity_from_role(role: str) -> tuple[str, str, str]:
    normalized = role.strip().lower().replace("-", "_")
    labels = {
        "lead": ("lead", "Lead", "L"),
        "planner": ("planner", "Planner", "P"),
        "code_scout": ("code_scout", "Scout", "S"),
        "worker": ("worker", "Worker", "W"),
        "reviewer": ("reviewer", "Reviewer", "R"),
        "harness_reviewer": ("harness_reviewer", "Harness reviewer", "H"),
        "security_reviewer": ("security_reviewer", "Security reviewer", "S"),
        "token_reviewer": ("token_reviewer", "Token reviewer", "T"),
    }
    return labels.get(normalized, ("worker", "Worker", "W"))


def _automation_mode_from_agent_task_mode(value: str) -> str:
    return {
        "manual": "manual",
        "review_and_approve": "assisted",
        "agent_task_planner": "full_agent",
    }.get(value, "assisted")


def _token_usage_payload(token_usage: object) -> dict[str, int]:
    if token_usage is None:
        return {}
    return {
        key: value
        for key, value in {
            "input_tokens": getattr(token_usage, "input_tokens", None),
            "output_tokens": getattr(token_usage, "output_tokens", None),
            "total_tokens": getattr(token_usage, "total_tokens", None),
        }.items()
        if isinstance(value, int)
    }
