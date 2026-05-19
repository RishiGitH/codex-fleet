from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from codex_fleet.config import FleetConfig
from codex_fleet.models import ProposedTask, RunRecord, RunStatus, WorkItem, WorkItemState
from codex_fleet.project_registry import normalize_codex_settings
from codex_fleet.runner import Runner
from codex_fleet.store import RunStore, StoredRun, TaskMetadata
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
        workflow_mode: str = "plan_execute",
        max_depth: int = 2,
        max_child_tasks_per_run: int = 8,
        runner_factory: Callable[[WorkItem], Runner] | None = None,
        agent_task_settings_resolver: Callable[[WorkItem], tuple[str, int]] | None = None,
    ) -> None:
        self.config = config
        self.tracker = tracker
        self.runner = runner
        self.store = store
        self.workflow_mode = _normalize_workflow_mode(workflow_mode)
        self.max_depth = max(0, max_depth)
        self.max_child_tasks_per_run = max(1, max_child_tasks_per_run)
        self.runner_factory = runner_factory
        self.agent_task_settings_resolver = agent_task_settings_resolver
        self.worktrees = WorktreeManager(config.repo, config.workspace.root)

    def run_once(self) -> OrchestratorResult:
        self._normalize_full_auto_child_review_states()
        completed_parent = self._complete_ready_full_auto_parent()
        if completed_parent is not None:
            return completed_parent
        candidates = self.tracker.fetch_candidate_items()
        candidates = [item for item in candidates if not self._dependency_blocked(item)]
        if not candidates:
            return OrchestratorResult(False, None, "No candidate work items found.")

        item = sorted(candidates, key=_dispatch_sort_key)[0]
        runner = self.runner_factory(item) if self.runner_factory is not None else self.runner
        if self.agent_task_settings_resolver is not None:
            workflow_mode, max_depth = self.agent_task_settings_resolver(item)
            workflow_mode = _normalize_workflow_mode(workflow_mode)
            max_depth = max(0, max_depth)
        else:
            workflow_mode = self.workflow_mode
            max_depth = self.max_depth
        if self._task_depth(item) == 0 and self._has_child_tasks(item.id):
            if item.state != WorkItemState.PLANNING.value:
                self.tracker.update_item_state(item.id, WorkItemState.PLANNING.value)
            return OrchestratorResult(False, None, "Parent already has child tasks; waiting for children.")
        if (
            workflow_mode in {"plan_only", "plan_execute", "full_auto"}
            and self._task_depth(item) == 0
            and not self._has_child_tasks(item.id)
            and self._has_durable_task_settings(item)
        ):
            planner = self._create_planner_child_task(item, workflow_mode=workflow_mode, max_depth=max_depth)
            if planner is not None:
                self.tracker.create_comment(item.id, f"codex-fleet created planner child task `{planner.identifier}`.")
                self.tracker.update_item_state(item.id, WorkItemState.PLANNING.value)
                return OrchestratorResult(False, None, "Created planner child task.")
        if workflow_mode == "execute_only" and self._task_depth(item) == 0 and not self._has_child_tasks(item.id):
            created = self._create_proposed_tasks(
                RunRecord(id=str(uuid4()), item=item, status=RunStatus.QUEUED),
                (
                    ProposedTask(
                        title=f"Implement {item.identifier}: {item.title}",
                        description="Complete the assigned human request and report changed files plus verification.",
                        role="implementer",
                    ),
                ),
                workflow_mode="plan_execute",
                max_depth=max_depth,
            )
            if created:
                self.tracker.create_comment(item.id, f"codex-fleet created implementer child task `{created[0].identifier}`.")
                self.tracker.update_item_state(item.id, WorkItemState.PLANNING.value)
                return OrchestratorResult(False, None, "Execute-only created an implementer child task.")
        run = RunRecord(id=str(uuid4()), item=item, status=RunStatus.QUEUED)
        run.runner_name = type(runner).__name__
        run.agent_role, run.agent_name, run.agent_avatar = _agent_identity(item)
        run.settings = {
            "workflow_mode": workflow_mode,
            "max_depth": max_depth,
            "approval_policy": self.config.codex.approval_policy,
            "sandbox_mode": self.config.codex.sandbox_mode,
            "model": self.config.codex.model,
            "default_model": self.config.codex.model,
            "reasoning_effort": self.config.codex.reasoning_effort,
            "settings_source": "project_default",
        }
        if self.store is not None:
            metadata = self.store.get_task_metadata(item.id)
            if metadata is not None and metadata.role:
                run.settings = normalize_codex_settings({**run.settings, **metadata.settings, "workflow_mode": workflow_mode, "max_depth": max_depth})
                run.agent_role, run.agent_name, run.agent_avatar = _agent_identity_from_role(metadata.role)
                run.settings["agent_role"] = run.agent_role
                run.settings["settings_source"] = str(metadata.settings.get("settings_source") or run.settings.get("settings_source") or "project_default")
                if metadata.settings.get("parent_workflow_mode"):
                    run.settings["parent_workflow_mode"] = metadata.settings.get("parent_workflow_mode")
                if isinstance(metadata.settings.get("human_answers"), list):
                    run.settings["human_answers"] = metadata.settings["human_answers"]
        run.model = _runner_model(runner)
        run.reasoning_effort = _runner_reasoning_effort(runner) or self.config.codex.reasoning_effort
        if run.model:
            run.settings["model"] = run.model
            run.settings["default_model"] = run.model
        if run.reasoning_effort:
            run.settings["reasoning_effort"] = run.reasoning_effort
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
            base_branch = self._dependency_base_branch(item)
            workspace = self.worktrees.prepare(item, base_branch=base_branch)
            run.worktree_path = workspace.path
            run.branch_name = workspace.branch_name
            self._persist(run)
            self._event(
                run,
                "workspace_prepared",
                {
                    "branch_name": workspace.branch_name,
                    "worktree_path": str(workspace.path),
                    "base_branch": base_branch,
                },
            )

            run.mark(RunStatus.RUNNING_CODEX)
            self._persist(run)
            self._event(
                run,
                "runner_started",
                {"runner": type(runner).__name__, "workflow_mode": workflow_mode, "max_depth": max_depth},
            )
            if type(runner).__name__ == "CodexAppServerRunner":
                self._event(
                    run,
                    "agent_session_started",
                    {
                        "runner": type(runner).__name__,
                        "workflow_mode": workflow_mode,
                        "max_depth": max_depth,
                        "model": run.model,
                        "reasoning_effort": run.reasoning_effort,
                        "agent_role": run.agent_role,
                    },
                )
            if hasattr(runner, "run_id"):
                runner.run_id = run.id
            result = runner.run(item, workspace.path)
            run.codex_thread_id = result.codex_thread_id
            run.codex_turn_id = result.codex_turn_id
            run.token_usage = result.token_usage
            if result.preview_url:
                run.settings["preview_url"] = result.preview_url
            if result.test_video_path:
                run.settings["test_video_path"] = str(result.test_video_path)
            if result.test_video_url:
                run.settings["test_video_url"] = result.test_video_url
            if result.screenshot_paths:
                run.settings["screenshot_paths"] = [str(path) for path in result.screenshot_paths]
            if result.test_proof_status:
                run.settings["test_proof_status"] = result.test_proof_status
                run.settings["proof_status"] = result.test_proof_status
            if result.proof_kind:
                run.settings["proof_kind"] = result.proof_kind
            if result.proof_warning:
                run.settings["proof_warning"] = result.proof_warning
            if result.proof_log_paths:
                run.settings["proof_log_paths"] = [str(path) for path in result.proof_log_paths]
            self._persist(run)
            self._messages(run, result.messages)
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
            if type(runner).__name__ == "CodexAppServerRunner":
                self._event(
                    run,
                    "agent_session_finished",
                    {
                        "success": result.success,
                        "changed_files": list(result.changed_files),
                        "test_commands": list(result.test_commands),
                        "artifact_count": len(result.artifacts),
                        "token_usage": _token_usage_payload(result.token_usage),
                    },
            )
            if result.success:
                commit_id = self._commit_workspace_changes(run)
                if commit_id:
                    self._event(run, "workspace_changes_committed", {"commit": commit_id, "branch_name": run.branch_name})
                orchestration_mode = str(run.settings.get("parent_workflow_mode") or workflow_mode)
                proposed_items = self._create_proposed_tasks(
                    run,
                    result.proposed_tasks,
                    workflow_mode=orchestration_mode,
                    max_depth=max_depth,
                )
                if proposed_items and orchestration_mode in {"plan_only", "plan_execute", "full_auto"}:
                    child_done_state = WorkItemState.DONE.value if orchestration_mode == "full_auto" else WorkItemState.HUMAN_REVIEW.value
                    run.mark(RunStatus.DONE if orchestration_mode == "full_auto" else RunStatus.HUMAN_REVIEW)
                    self._persist(run)
                    self._artifacts(run, result.artifacts)
                    parent_id = self._planner_parent_id(item) or item.id
                    self._event(run, "parent_waiting", {"parent_item_id": parent_id, "state": WorkItemState.PLANNING.value, "child_count": len(proposed_items)})
                    self.tracker.create_comment(parent_id, _success_comment(run, result.summary, result.test_commands, proposed_items))
                    self.tracker.update_item_state(parent_id, WorkItemState.PLANNING.value)
                    if parent_id != item.id:
                        self.tracker.update_item_state(item.id, child_done_state)
                    if self.store is not None:
                        self.store.finish_claim(item.id, run.id, "completed")
                    return OrchestratorResult(True, run, "Run created child tasks and moved parent to Planning.")
                if workflow_mode == "full_auto" and run.agent_role != "planner":
                    delivery_item = self._create_delivery_task(run, result.summary, result.test_commands)
                    run.mark(RunStatus.DONE)
                    self._persist(run)
                    self._artifacts(run, result.artifacts)
                    self._event(run, "parent_completed", {"state": WorkItemState.DONE.value, "delivery_item_id": delivery_item.id if delivery_item else None})
                    self.tracker.create_comment(
                        item.id,
                        _success_comment(run, result.summary, result.test_commands)
                        + ("\n\nFull auto completed and created delivery task `" + delivery_item.identifier + "`." if delivery_item else "\n\nFull auto completed."),
                    )
                    self.tracker.update_item_state(item.id, WorkItemState.DONE.value)
                    if self.store is not None:
                        self.store.finish_claim(item.id, run.id, "completed")
                    return OrchestratorResult(True, run, "Full auto completed and moved parent to Done.")

                final_state = WorkItemState.HUMAN_REVIEW.value
                final_status = RunStatus.HUMAN_REVIEW
                if str(run.settings.get("parent_workflow_mode") or workflow_mode) == "full_auto" and self._task_depth(item) > 0:
                    final_state = WorkItemState.DONE.value
                    final_status = RunStatus.DONE
                run.mark(final_status)
                self._persist(run)
                self._artifacts(run, result.artifacts)
                self._event(
                    run,
                    "completed",
                    {"state": final_state, "proposed_task_count": len(proposed_items)},
                )
                self.tracker.create_comment(
                    item.id,
                    _success_comment(run, result.summary, result.test_commands, proposed_items),
                )
                self.tracker.update_item_state(item.id, final_state)
                if self._confirm_item_state(item.id, final_state):
                    self._event(run, "state_update_confirmed", {"state": final_state})
                else:
                    self._event(run, "state_update_pending", {"requested_state": final_state})
                    self.tracker.create_comment(item.id, _state_update_pending_comment(run, final_state))
                    return OrchestratorResult(True, run, "Run completed, but Plane state confirmation is pending.")
                if self.store is not None:
                    self.store.finish_claim(item.id, run.id, "completed")
                return OrchestratorResult(True, run, f"Run completed and moved to {final_state}.")

            if result.needs_input is not None:
                question = result.needs_input.question
                if _is_repeated_needs_input_question(question, run.settings):
                    question = (
                        "Codex asked for the same input again even though a human answer is already attached. "
                        "Review the latest answer in this task, clarify the work item if needed, then retry."
                    )
                full_auto_recovery = self._recover_full_auto_blocker(run, item, question, max_depth=max_depth)
                if full_auto_recovery is not None:
                    self._persist(run)
                    self._artifacts(run, result.artifacts)
                    if self.store is not None:
                        self.store.finish_claim(item.id, run.id, "completed")
                    return full_auto_recovery
                run.mark(RunStatus.NEEDS_INPUT, question)
                self._persist(run)
                self._artifacts(run, result.artifacts)
                self._event(
                    run,
                    "needs_input",
                    {
                        "question": question,
                        "original_question": result.needs_input.question,
                        "state": result.needs_input.suggested_state,
                    },
                )
                if self.store is not None:
                    self.store.record_needs_input(run.id, item.id, question)
                self.tracker.create_comment(item.id, _needs_input_comment(run, question))
                self.tracker.update_item_state(item.id, WorkItemState.NEEDS_INPUT.value)
                if self.store is not None:
                    self.store.finish_claim(item.id, run.id, "blocked")
                return OrchestratorResult(True, run, "Run needs human input.")

            run.mark(RunStatus.REWORK, result.error)
            full_auto_recovery = self._recover_full_auto_blocker(run, item, result.error or result.summary, max_depth=max_depth)
            if full_auto_recovery is not None:
                self._persist(run)
                self._artifacts(run, result.artifacts)
                if self.store is not None:
                    self.store.finish_claim(item.id, run.id, "completed")
                return full_auto_recovery
            self._persist(run)
            self._artifacts(run, result.artifacts)
            target_state = WorkItemState.NEEDS_INPUT.value
            self._event(run, "failed", {"error": result.error or result.summary, "state": target_state, "run_status": RunStatus.REWORK.value})
            self.tracker.create_comment(
                item.id,
                f"codex-fleet needs human input because this run requires rework: {result.error or result.summary}",
            )
            self.tracker.update_item_state(item.id, target_state)
            if self._confirm_item_state(item.id, target_state):
                self._event(run, "state_update_confirmed", {"state": target_state})
            else:
                self._event(run, "state_update_pending", {"requested_state": target_state})
                self.tracker.create_comment(item.id, _state_update_pending_comment(run, target_state))
                return OrchestratorResult(True, run, "Run failed, but Plane state confirmation is pending.")
            if self.store is not None:
                self.store.finish_claim(item.id, run.id, "failed")
            return OrchestratorResult(True, run, "Run failed and moved to Needs Input.")
        except Exception as exc:  # noqa: BLE001 - orchestration boundary converts failures to tracker status.
            run.mark(RunStatus.BLOCKED, str(exc))
            self._persist(run)
            target_state = WorkItemState.NEEDS_INPUT.value
            self._event(run, "blocked", {"error": str(exc), "state": target_state, "run_status": RunStatus.BLOCKED.value})
            self.tracker.create_comment(
                item.id,
                "codex-fleet could not start the agent because local setup or App Server rejected the request. "
                "Restart with `make stop && make up`, then click Retry.\n\n"
                f"Details: {exc}",
            )
            self.tracker.update_item_state(item.id, target_state)
            if self._confirm_item_state(item.id, target_state):
                self._event(run, "state_update_confirmed", {"state": target_state})
            else:
                self._event(run, "state_update_pending", {"requested_state": target_state})
                self.tracker.create_comment(item.id, _state_update_pending_comment(run, target_state))
                return OrchestratorResult(True, run, "Run errored, but Plane state confirmation is pending.")
            if self.store is not None:
                self.store.finish_claim(item.id, run.id, "failed")
            return OrchestratorResult(True, run, "Run errored and moved to Needs Input.")

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
            reasoning_effort=run.reasoning_effort,
            codex_thread_id=run.codex_thread_id,
            codex_turn_id=run.codex_turn_id,
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
            self.store.add_artifact(run.id, str(artifact), kind=_artifact_kind(artifact), size_bytes=size_bytes, sha256=digest)

    def _messages(self, run: RunRecord, messages: tuple[object, ...]) -> None:
        if self.store is None:
            return
        for message in messages:
            self.store.add_run_message(
                run.id,
                sequence=int(getattr(message, "sequence", 0)),
                kind=str(getattr(message, "kind", "system_event")),
                content=str(getattr(message, "content", "")),
                agent_role=getattr(message, "agent_role", None) or run.agent_role,
                agent_name=getattr(message, "agent_name", None) or run.agent_name,
                artifact_path=str(getattr(message, "artifact_path", "")) if getattr(message, "artifact_path", None) else None,
                payload=getattr(message, "payload", None) if isinstance(getattr(message, "payload", None), dict) else {},
            )
            self._event(run, "agent_message", {"kind": str(getattr(message, "kind", "system_event")), "sequence": int(getattr(message, "sequence", 0))})

    def _create_proposed_tasks(
        self,
        run: RunRecord,
        tasks: tuple[ProposedTask, ...],
        *,
        workflow_mode: str,
        max_depth: int,
    ) -> tuple[WorkItem, ...]:
        if workflow_mode == "execute_only":
            if tasks:
                self._event(run, "proposed_tasks_skipped", {"reason": "planner task creation disabled", "count": len(tasks)})
            return ()
        created: list[WorkItem] = []
        created_by_title: dict[str, str] = {}
        target_parent_id = run.item.id
        target_parent_identifier = run.item.identifier
        parent_depth = self._task_depth(run.item)
        parent_metadata = self.store.get_task_metadata(run.item.id) if self.store is not None else None
        if parent_metadata is not None and parent_metadata.role == "planner" and parent_metadata.parent_item_id:
            target_parent_id = parent_metadata.parent_item_id
            target_parent_identifier = parent_metadata.parent_identifier or target_parent_identifier
            parent_depth = max(0, parent_metadata.depth - 1)
        child_depth = parent_depth + 1
        auto_run = workflow_mode in {"plan_execute", "full_auto"} and child_depth <= max_depth
        state = WorkItemState.READY.value if auto_run else WorkItemState.BACKLOG.value
        source_label = "agent-followup" if auto_run else "agent-proposed"
        root_item_id = parent_metadata.root_item_id if parent_metadata is not None and parent_metadata.root_item_id else run.item.id
        if parent_metadata is not None and parent_metadata.role == "planner" and parent_metadata.parent_item_id:
            root_item_id = parent_metadata.root_item_id or parent_metadata.parent_item_id
        for task in tasks[: self.max_child_tasks_per_run]:
            task_role = _normalize_agent_role(task.role or "implementer")
            inherited_settings = parent_metadata.settings if parent_metadata is not None else run.settings
            if not _role_enabled(inherited_settings, task_role):
                self._event(
                    run,
                    "proposed_task_skipped",
                    {
                        "title": task.title,
                        "role": task_role,
                        "reason": "agent role is disabled in project settings",
                    },
                )
                continue
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
                dependency_keys = {_dependency_key(task.title)}
                if task.planner_id:
                    dependency_keys.add(_dependency_key(task.planner_id))
                for dependency_key in dependency_keys:
                    created_by_title[dependency_key] = item.id
                dependency_ids = tuple(
                    created_by_title[_dependency_key(dependency)]
                    for dependency in task.depends_on
                    if _dependency_key(dependency) in created_by_title
                )
                unresolved_dependencies = tuple(
                    dependency
                    for dependency in task.depends_on
                    if _dependency_key(dependency) not in created_by_title
                )
                if self.store is not None:
                    self.store.upsert_task_metadata(
                        item_id=item.id,
                        source=source_label,
                        depth=child_depth,
                        parent_item_id=target_parent_id,
                        parent_identifier=target_parent_identifier,
                        parent_run_id=run.id,
                        created_by_run_id=run.id,
                        root_item_id=root_item_id,
                        role=task_role,
                        depends_on=dependency_ids,
                        generation=child_depth,
                        approval_mode=workflow_mode,
                        settings=_child_task_settings(
                            inherited_settings,
                            task_role,
                            parent_workflow_mode=workflow_mode,
                            max_depth=max_depth,
                        ),
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
                        "parent_item_id": target_parent_id,
                        "parent_identifier": target_parent_identifier,
                        "state": task_state,
                        "auto_run": auto_run,
                        "role": task_role,
                        "depends_on": list(dependency_ids),
                        "unresolved_depends_on": list(unresolved_dependencies),
                    },
                )
        if len(tasks) > self.max_child_tasks_per_run:
            self._event(
                run,
                "proposed_tasks_truncated",
                {"received": len(tasks), "created": self.max_child_tasks_per_run},
            )
        return tuple(created)

    def _create_planner_child_task(self, item: WorkItem, *, workflow_mode: str, max_depth: int) -> WorkItem | None:
        try:
            planner = self.tracker.create_work_item(
                title=f"Plan {item.identifier}: {item.title}",
                description=(
                    "Break the parent request into durable Plane child tasks. "
                    "Return only the codex-fleet planner JSON contract."
                ),
                state=WorkItemState.READY.value,
                labels=("agent-followup", "agent-planner"),
            )
        except Exception:
            return None
        if self.store is not None and planner is not None:
            parent_metadata = self.store.get_task_metadata(item.id)
            inherited_settings: dict[str, object] = (
                dict(parent_metadata.settings)
                if parent_metadata is not None
                else {
                    "default_model": self.config.codex.model,
                    "model": self.config.codex.model,
                    "reasoning_effort": self.config.codex.reasoning_effort,
                    "approval_policy": self.config.codex.approval_policy,
                    "sandbox_mode": self.config.codex.sandbox_mode,
                }
            )
            self.store.upsert_task_metadata(
                item_id=planner.id,
                source="agent-followup",
                depth=1,
                parent_item_id=item.id,
                parent_identifier=item.identifier,
                parent_run_id=None,
                created_by_run_id=None,
                root_item_id=item.id,
                role="planner",
                generation=1,
                approval_mode=workflow_mode,
                settings=_child_task_settings(
                    inherited_settings,
                    "planner",
                    parent_workflow_mode=workflow_mode,
                    max_depth=max_depth,
                ),
            )
        return planner

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

    def _dependency_base_branch(self, item: WorkItem) -> str | None:
        if self.store is None:
            return None
        metadata = self.store.get_task_metadata(item.id)
        if metadata is None or not metadata.depends_on:
            return None
        dependency_runs: list[StoredRun] = []
        for dependency_id in metadata.depends_on:
            dependency_run = self.store.latest_run_for_item(dependency_id)
            if dependency_run is None or not dependency_run.branch_name:
                continue
            if dependency_run.status not in {RunStatus.HUMAN_REVIEW.value, RunStatus.DONE.value}:
                continue
            dependency_runs.append(dependency_run)
        if not dependency_runs:
            return None
        preferred_roles = ("implementer", "test_reviewer", "quality_reviewer", "code_scout")
        for role in preferred_roles:
            for dependency_run in dependency_runs:
                if _normalize_agent_role(dependency_run.agent_role) == role:
                    return dependency_run.branch_name
        return dependency_runs[-1].branch_name

    def _commit_workspace_changes(self, run: RunRecord) -> str | None:
        if run.worktree_path is None:
            return None
        workspace = Path(run.worktree_path)
        if not workspace.exists():
            return None
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=workspace,
            text=True,
            capture_output=True,
            check=False,
        )
        if status.returncode != 0:
            self._event(run, "workspace_commit_skipped", {"reason": status.stderr.strip() or "git status failed"})
            return None
        paths = _committable_paths(status.stdout)
        if not paths:
            return None
        add = subprocess.run(["git", "add", "--", *paths], cwd=workspace, text=True, capture_output=True, check=False)
        if add.returncode != 0:
            self._event(run, "workspace_commit_skipped", {"reason": add.stderr.strip() or "git add failed"})
            return None
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=workspace, text=True, capture_output=True, check=False)
        if staged.returncode == 0:
            return None
        message = f"codex-fleet: {run.item.identifier} {_normalize_agent_role(run.agent_role)}"
        commit = subprocess.run(
            [
                "git",
                "-c",
                "user.name=codex-fleet",
                "-c",
                "user.email=codex-fleet@example.local",
                "commit",
                "-m",
                message,
            ],
            cwd=workspace,
            text=True,
            capture_output=True,
            check=False,
        )
        if commit.returncode != 0:
            self._event(run, "workspace_commit_skipped", {"reason": commit.stderr.strip() or "git commit failed"})
            return None
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=workspace, text=True, capture_output=True, check=False)
        return rev.stdout.strip() if rev.returncode == 0 else "committed"

    def _has_child_tasks(self, item_id: str) -> bool:
        return self.store is not None and bool(self.store.list_child_task_metadata(item_id))

    def _planner_parent_id(self, item: WorkItem) -> str | None:
        if self.store is None:
            return None
        metadata = self.store.get_task_metadata(item.id)
        if metadata is not None and metadata.role == "planner":
            return metadata.parent_item_id
        return None

    def _has_durable_task_settings(self, item: WorkItem) -> bool:
        if self.store is not None and self.store.get_task_metadata(item.id) is not None:
            return True
        return "codex-fleet task settings" in (item.description or "")

    def _complete_ready_full_auto_parent(self) -> OrchestratorResult | None:
        if self.store is None:
            return None
        parent_ids = self.store.list_parent_item_ids_with_children()
        if not parent_ids:
            return None
        parents = self.tracker.fetch_items_by_ids(parent_ids)
        for parent in parents:
            metadata = self.store.get_task_metadata(parent.id)
            settings = metadata.settings if metadata is not None else {}
            if settings.get("workflow_mode") != "full_auto" or parent.state == WorkItemState.DONE.value:
                continue
            children_metadata = self.store.list_child_task_metadata(parent.id)
            child_ids = [child.item_id for child in children_metadata if child.role != "delivery_manager"]
            children = self.tracker.fetch_items_by_ids(child_ids)
            if not child_ids or len(children) != len(child_ids):
                continue
            failed = [child for child in children if child.state in {WorkItemState.REWORK.value, WorkItemState.BLOCKED.value, WorkItemState.CANCELLED.value, WorkItemState.NEEDS_INPUT.value}]
            if failed:
                self._record_parent_waiting_on_child(parent, failed, children_metadata)
                continue
            if all(child.state in {WorkItemState.HUMAN_REVIEW.value, WorkItemState.DONE.value} for child in children):
                synthetic = RunRecord(id=str(uuid4()), item=parent, status=RunStatus.DONE)
                primary = self._primary_child_run(children_metadata)
                if primary is not None:
                    synthetic.branch_name = primary.branch_name
                    synthetic.worktree_path = Path(primary.worktree_path) if primary.worktree_path else None
                    synthetic.model = primary.model
                    synthetic.reasoning_effort = primary.reasoning_effort
                    synthetic.settings = dict(primary.settings)
                synthetic.settings.update(self._aggregate_child_proof(children_metadata))
                self._create_delivery_task(synthetic, "All required child tasks passed.", ("child tasks passed",))
                self.tracker.create_comment(parent.id, "codex-fleet full auto completed all required child tasks and moved this parent to Done.")
                self.tracker.update_item_state(parent.id, WorkItemState.DONE.value)
                return OrchestratorResult(False, None, "Full auto parent completed.")
        return None

    def _normalize_full_auto_child_review_states(self) -> int:
        """Accept successful full-auto child tasks automatically.

        Full-auto parents are orchestration containers. Their child agents may
        still land in Human Review after older daemon versions, stale Plane
        state, or compatibility paths. Normalize those passing children to Done
        before parent reconciliation so users do not need to manually review
        every agent task in a full-auto demo flow.
        """
        if self.store is None:
            return 0
        accepted = 0
        for parent_id in self.store.list_parent_item_ids_with_children():
            parent_metadata = self.store.get_task_metadata(parent_id)
            parent_settings = parent_metadata.settings if parent_metadata is not None else {}
            if parent_settings.get("workflow_mode") != "full_auto":
                continue
            children_metadata = [
                metadata
                for metadata in self.store.list_child_task_metadata(parent_id)
                if metadata.role != "delivery_manager"
            ]
            if not children_metadata:
                continue
            children = {
                child.id: child
                for child in self.tracker.fetch_items_by_ids([metadata.item_id for metadata in children_metadata])
            }
            for metadata in children_metadata:
                child = children.get(metadata.item_id)
                if child is None or child.state != WorkItemState.HUMAN_REVIEW.value:
                    continue
                latest = self.store.latest_run_for_item(metadata.item_id)
                event_run_id = latest.id if latest is not None else _parent_event_run_id(parent_id)
                duplicate = [
                    event
                    for event in self.store.list_events(event_run_id)
                    if event.kind == "full_auto_child_accepted"
                    and event.payload.get("item_id") == metadata.item_id
                ]
                if duplicate:
                    self.tracker.update_item_state(metadata.item_id, WorkItemState.DONE.value)
                    accepted += 1
                    continue
                if latest is not None and latest.status == RunStatus.HUMAN_REVIEW.value:
                    self.store.update_run_status(latest.id, RunStatus.DONE.value)
                self.store.add_event(
                    event_run_id,
                    "full_auto_child_accepted",
                    {
                        "item_id": metadata.item_id,
                        "identifier": child.identifier,
                        "state": WorkItemState.DONE.value,
                        "parent_item_id": parent_id,
                    },
                )
                self.tracker.create_comment(
                    metadata.item_id,
                    "codex-fleet accepted this agent task automatically because the parent is Full auto.",
                )
                self.tracker.update_item_state(metadata.item_id, WorkItemState.DONE.value)
                accepted += 1
        return accepted

    def _primary_child_run(self, children_metadata: Sequence[TaskMetadata]) -> StoredRun | None:
        if self.store is None:
            return None
        for preferred_role in ("implementer", "test_reviewer", "quality_reviewer", "code_scout"):
            for metadata in children_metadata:
                if getattr(metadata, "role", None) == preferred_role:
                    run = self.store.latest_run_for_item(str(metadata.item_id))
                    if run is not None:
                        return run
        for metadata in children_metadata:
            run = self.store.latest_run_for_item(str(metadata.item_id))
            if run is not None:
                return run
        return None

    def _aggregate_child_proof(self, children_metadata: Sequence[TaskMetadata]) -> dict[str, object]:
        if self.store is None:
            return {}
        candidates: list[tuple[int, StoredRun]] = []
        for metadata in children_metadata:
            run = self.store.latest_run_for_item(str(metadata.item_id))
            if run is None:
                continue
            settings = run.settings or {}
            score = _proof_score(settings)
            if score:
                candidates.append((score, run))
        if not candidates:
            return {}
        candidates.sort(key=lambda item: item[0], reverse=True)
        run = candidates[0][1]
        settings = run.settings or {}
        proof: dict[str, object] = {
            "proof_source_run_id": run.id,
            "proof_source_identifier": run.identifier,
            "proof_source_role": run.agent_role,
        }
        for key in (
            "preview_url",
            "test_video_path",
            "test_video_url",
            "screenshot_paths",
            "test_proof_status",
            "proof_status",
            "proof_kind",
            "proof_warning",
            "proof_log_paths",
        ):
            value = settings.get(key)
            if value:
                proof[key] = value
        return proof

    def _record_parent_waiting_on_child(
        self,
        parent: WorkItem,
        failed_children: list[WorkItem],
        children_metadata: Sequence[object],
    ) -> None:
        payload = {
            "parent_item_id": parent.id,
            "blockers": [
                {"item_id": item.id, "identifier": item.identifier, "state": item.state}
                for item in failed_children
            ],
        }
        if self.store is not None:
            parent_run_id = next(
                (
                    str(parent_run_id)
                    for metadata in children_metadata
                    if (parent_run_id := getattr(metadata, "parent_run_id", None))
                ),
                None,
            )
            run_id = parent_run_id or _parent_event_run_id(parent.id)
            existing = [
                event
                for event in self.store.list_events(run_id)
                if event.kind == "parent_blocked" and event.payload == payload
            ]
            if existing:
                return
            self.store.add_event(run_id, "parent_blocked", payload)
        first = failed_children[0]
        self.tracker.create_comment(parent.id, f"codex-fleet is waiting on child `{first.identifier}`: `{first.state}`.")

    def _confirm_item_state(self, item_id: str, expected_state: str, *, attempts: int = 4, delay_seconds: float = 0.25) -> bool:
        expected = expected_state.lower()
        for attempt in range(attempts):
            items = self.tracker.fetch_items_by_ids([item_id])
            if items and items[0].state.lower() == expected:
                return True
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
        return False

    def _recover_full_auto_blocker(
        self,
        run: RunRecord,
        item: WorkItem,
        blocker: str | None,
        *,
        max_depth: int,
    ) -> OrchestratorResult | None:
        if self.store is None:
            return None
        parent_mode = str(run.settings.get("parent_workflow_mode") or run.settings.get("workflow_mode") or "")
        if parent_mode != "full_auto" or self._task_depth(item) == 0:
            return None
        blocker_text = (blocker or "Agent reported a blocker.").strip()
        if _is_hard_human_blocker(blocker_text):
            return None
        role = _normalize_agent_role(run.agent_role)
        if role == "planner":
            proposed_items = self._create_full_auto_fallback_tasks(run, item, blocker_text, max_depth=max_depth)
            if not proposed_items:
                return None
            parent_id = self._planner_parent_id(item) or item.id
            run.mark(RunStatus.DONE)
            self._event(run, "planner_fallback_created", {"parent_item_id": parent_id, "child_count": len(proposed_items), "blocker": blocker_text})
            self.tracker.create_comment(
                parent_id,
                "codex-fleet kept full auto moving by using reasonable assumptions and creating fallback child tasks:\n"
                + "\n".join(f"- `{child.identifier}` {child.title}" for child in proposed_items),
            )
            self.tracker.update_item_state(parent_id, WorkItemState.PLANNING.value)
            self.tracker.create_comment(
                item.id,
                "codex-fleet converted the planner blocker into fallback child tasks for full auto.\n\n"
                f"Original blocker: {blocker_text}",
            )
            self.tracker.update_item_state(item.id, WorkItemState.DONE.value)
            return OrchestratorResult(True, run, "Full auto planner blocker converted into child tasks.")

        repair = self._create_repair_task(run, item, blocker_text, max_depth=max_depth)
        if repair is None:
            return None
        run.mark(RunStatus.DONE)
        self._event(run, "auto_repair_task_created", {"item_id": repair.id, "identifier": repair.identifier, "source_item_id": item.id, "blocker": blocker_text})
        self.tracker.create_comment(
            item.id,
            "codex-fleet converted this full-auto blocker into a repair task and marked this agent task complete.\n\n"
            f"Repair task: `{repair.identifier}`\n\n"
            f"Original blocker: {blocker_text}",
        )
        self.tracker.update_item_state(item.id, WorkItemState.DONE.value)
        root_parent_id = self._root_parent_id(item)
        if root_parent_id:
            self.tracker.create_comment(root_parent_id, f"codex-fleet created repair task `{repair.identifier}` for `{item.identifier}`.")
            self.tracker.update_item_state(root_parent_id, WorkItemState.PLANNING.value)
        return OrchestratorResult(True, run, f"Full auto created repair task {repair.identifier}.")

    def _create_full_auto_fallback_tasks(
        self,
        run: RunRecord,
        item: WorkItem,
        blocker: str,
        *,
        max_depth: int,
    ) -> tuple[WorkItem, ...]:
        parent_id = self._planner_parent_id(item)
        parent_items = self.tracker.fetch_items_by_ids([parent_id]) if parent_id else []
        parent = parent_items[0] if parent_items else item
        implement_title = f"Build {parent.identifier}: {parent.title}"
        tasks = (
            ProposedTask(
                title=implement_title,
                description=(
                    "Implement the parent request end to end. The planner asked for more information, but this is "
                    "a full-auto run, so make reasonable product assumptions and proceed. "
                    f"Original planner blocker: {blocker}"
                ),
                role="implementer",
            ),
            ProposedTask(
                title="Quality review implementation",
                description="Review build correctness, harness fit, token/context efficiency, and residual risks. Do not edit product source.",
                role="quality_reviewer",
                depends_on=(implement_title,),
            ),
            ProposedTask(
                title="Test implementation and record proof",
                description="Run the app or available tests, capture preview proof, screenshots, and video when possible. Do not edit product source.",
                role="test_reviewer",
                depends_on=(implement_title,),
            ),
        )
        return self._create_proposed_tasks(run, tasks, workflow_mode="full_auto", max_depth=max_depth)

    def _create_repair_task(
        self,
        run: RunRecord,
        item: WorkItem,
        blocker: str,
        *,
        max_depth: int,
    ) -> WorkItem | None:
        if self.store is None:
            return None
        metadata = self.store.get_task_metadata(item.id)
        if metadata is None or not metadata.parent_item_id:
            return None
        if metadata.depth >= max_depth:
            return None
        repair_count = self._repair_task_count(metadata.parent_item_id)
        if repair_count >= 2:
            return None
        inherited_settings = metadata.settings or run.settings
        if not _role_enabled(inherited_settings, "implementer"):
            return None
        try:
            repair = self.tracker.create_work_item(
                title=f"Fix blocker from {item.identifier}: {item.title}",
                description=(
                    "Resolve the blocker found by a full-auto child agent, then run relevant verification.\n\n"
                    f"Blocked task: `{item.identifier}`\n\n"
                    f"Agent role that found it: `{_normalize_agent_role(run.agent_role)}`\n\n"
                    f"Blocker:\n{blocker}"
                ),
                state=WorkItemState.READY.value,
                labels=("agent-followup", "agent-implementer", "agent-repair"),
            )
        except Exception as exc:  # noqa: BLE001
            self._event(run, "repair_task_failed", {"error": str(exc), "source_item_id": item.id})
            return None
        if repair is None:
            return None
        self.store.upsert_task_metadata(
            item_id=repair.id,
            source="agent-repair",
            depth=metadata.depth + 1,
            parent_item_id=metadata.parent_item_id,
            parent_identifier=metadata.parent_identifier,
            parent_run_id=run.id,
            created_by_run_id=run.id,
            root_item_id=metadata.root_item_id or metadata.parent_item_id,
            role="implementer",
            generation=metadata.generation + 1,
            approval_mode="full_auto",
            settings={
                **_child_task_settings(inherited_settings, "implementer", parent_workflow_mode="full_auto", max_depth=max_depth),
                "repair_of_item_id": item.id,
                "repair_of_identifier": item.identifier,
                "repair_blocker": blocker,
            },
        )
        try:
            self.tracker.create_comment(repair.id, f"codex-fleet created this repair task from `{item.identifier}` during full auto. Keep the fix narrow and verify it.")
        except Exception as exc:  # noqa: BLE001
            self._event(run, "repair_task_comment_failed", {"item_id": repair.id, "error": str(exc)})
        return repair

    def _repair_task_count(self, parent_item_id: str) -> int:
        if self.store is None:
            return 0
        return sum(1 for metadata in self.store.list_child_task_metadata(parent_item_id) if metadata.source == "agent-repair")

    def _root_parent_id(self, item: WorkItem) -> str | None:
        if self.store is None:
            return None
        metadata = self.store.get_task_metadata(item.id)
        if metadata is None:
            return None
        return metadata.parent_item_id or metadata.root_item_id

    def _create_delivery_task(self, run: RunRecord, summary: str, test_commands: tuple[str, ...]) -> WorkItem | None:
        title = f"Publish or merge result for {run.item.identifier}"
        tests = "\n".join(f"- `{command}`" for command in test_commands) or "- Not reported"
        preview_url = str(run.settings.get("preview_url") or "Not reported by the latest agent run.")
        video_path = str(run.settings.get("test_video_path") or "Not reported")
        video_url = str(run.settings.get("test_video_url") or "")
        proof_kind = str(run.settings.get("proof_kind") or "transcript_only")
        proof_status = str(run.settings.get("proof_status") or run.settings.get("test_proof_status") or "not_reported")
        proof_warning = str(run.settings.get("proof_warning") or "")
        proof_logs = run.settings.get("proof_log_paths")
        proof_log_lines = (
            "\n".join(f"- `{path}`" for path in proof_logs if isinstance(path, str))
            if isinstance(proof_logs, list) and proof_logs
            else "- No proof logs reported"
        )
        screenshots = run.settings.get("screenshot_paths")
        screenshot_lines = (
            "\n".join(f"- `{path}`" for path in screenshots if isinstance(path, str))
            if isinstance(screenshots, list) and screenshots
            else "- Not reported"
        )
        description = (
            f"Prepare delivery for `{run.item.identifier}`.\n\n"
            f"Parent id: `{run.item.id}`\n\n"
            f"Branch: `{run.branch_name or 'Not reported'}`\n\n"
            f"Worktree: `{run.worktree_path or 'Not reported'}`\n\n"
            f"Summary: {summary}\n\n"
            f"Test results:\n{tests}\n\n"
            f"Preview URL: {preview_url}\n\n"
            f"Playwright video URL: {video_url or 'Not reported'}\n\n"
            + (
                f'<video controls preload="metadata" width="720" src="{video_url}">'
                f'<a href="{video_url}">Open Playwright video</a>'
                "</video>\n\n"
                if video_url
                else ""
            )
            + f"Playwright video: `{video_path}`\n\n"
            f"Screenshots:\n{screenshot_lines}\n\n"
            f"Proof kind: `{proof_kind}`\n\n"
            f"Proof status: `{proof_status}`\n\n"
            f"Proof warning: {proof_warning or 'None'}\n\n"
            f"Proof logs:\n{proof_log_lines}\n\n"
            "PR URL: Not created yet. If this repo has a GitHub origin, codex-fleet should create a draft PR before delivery completion.\n\n"
            "Mark this delivery task Done to merge the result and clean up the worktree. If merge or cleanup fails, codex-fleet will move this task to Needs Input and keep the worktree intact."
        )
        try:
            item = self.tracker.create_work_item(
                title=title,
                description=description,
                state=WorkItemState.BACKLOG.value,
                labels=("agent-delivery-manager", "delivery"),
            )
        except Exception as exc:  # noqa: BLE001 - delivery creation should not hide successful work.
            self._event(run, "delivery_task_failed", {"error": str(exc)})
            return None
        if item is None:
            self._event(run, "delivery_task_failed", {"error": "tracker did not return an item"})
            return None
        if self.store is not None:
            parent_metadata = self.store.get_task_metadata(run.item.id)
            self.store.upsert_task_metadata(
                item_id=item.id,
                source="delivery",
                depth=(parent_metadata.depth + 1) if parent_metadata is not None else 1,
                parent_item_id=run.item.id,
                parent_identifier=run.item.identifier,
                parent_run_id=run.id,
                created_by_run_id=run.id,
                root_item_id=parent_metadata.root_item_id if parent_metadata is not None and parent_metadata.root_item_id else run.item.id,
                role="delivery_manager",
                depends_on=(run.item.id,),
                generation=(parent_metadata.generation + 1) if parent_metadata is not None else 1,
                approval_mode="full_auto",
                settings={
                    "workflow_mode": "execute_only",
                    "phase": "delivery",
                    "agent_role": "delivery_manager",
                    "delivery_status": "task_created",
                    "branch": run.branch_name,
                    "worktree": str(run.worktree_path) if run.worktree_path else None,
                    "preview_url": preview_url,
                    "test_video_path": video_path,
                    "test_video_url": video_url or None,
                    "screenshot_paths": screenshots if isinstance(screenshots, list) else [],
                    "proof_kind": proof_kind,
                    "proof_status": proof_status,
                    "proof_warning": proof_warning or None,
                    "proof_log_paths": proof_logs if isinstance(proof_logs, list) else [],
                    "proof_source_run_id": run.settings.get("proof_source_run_id"),
                    "proof_source_identifier": run.settings.get("proof_source_identifier"),
                    "proof_source_role": run.settings.get("proof_source_role"),
                },
            )
            self._event(run, "delivery_task_created", {"item_id": item.id, "identifier": item.identifier})
        return item


def _child_task_settings(
    inherited_settings: dict[str, object] | None,
    role: str,
    *,
    parent_workflow_mode: str,
    max_depth: int,
) -> dict[str, object]:
    role = _normalize_agent_role(role)
    settings = normalize_codex_settings(inherited_settings or {})
    profile = settings.get("agent_profiles")
    role_profile = profile.get(role) if isinstance(profile, dict) else None
    if not isinstance(role_profile, dict):
        subagents = settings.get("subagents")
        role_profile = subagents.get(role) if isinstance(subagents, dict) else None
    if isinstance(role_profile, dict):
        if isinstance(role_profile.get("model"), str) and role_profile["model"].strip():
            settings["default_model"] = role_profile["model"].strip()
            settings["model"] = role_profile["model"].strip()
        if isinstance(role_profile.get("reasoning_effort"), str) and role_profile["reasoning_effort"].strip():
            settings["reasoning_effort"] = role_profile["reasoning_effort"].strip()
        if isinstance(role_profile.get("sandbox_mode"), str) and role_profile["sandbox_mode"].strip():
            settings["sandbox_mode"] = role_profile["sandbox_mode"].strip()
    settings.update(
        {
            "workflow_mode": "execute_only",
            "max_depth": max_depth,
            "agent_role": role,
            "parent_workflow_mode": parent_workflow_mode,
            "settings_source": "role_profile" if isinstance(role_profile, dict) else settings.get("settings_source", "project_default"),
        }
    )
    return settings


def _role_enabled(settings: dict[str, object] | None, role: str) -> bool:
    role = _normalize_agent_role(role)
    if role in {"orchestrator", "planner", "delivery_manager"}:
        return True
    if not settings or not any(key in settings for key in ("subagents_enabled", "enabled_agent_roles", "agent_profiles")):
        return True
    normalized = normalize_codex_settings(settings or {})
    if not normalized.get("subagents_enabled"):
        return role == "implementer"
    enabled_roles = normalized.get("enabled_agent_roles")
    if isinstance(enabled_roles, list) and role not in {str(value) for value in enabled_roles}:
        return False
    profiles = normalized.get("agent_profiles")
    profile = profiles.get(role) if isinstance(profiles, dict) else None
    return not (isinstance(profile, dict) and profile.get("enabled") is False)


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
    proof_lines: list[str] = []
    preview_url = run.settings.get("preview_url")
    if isinstance(preview_url, str) and preview_url.strip():
        proof_lines.append(f"- Preview URL: {preview_url}")
    video_path = run.settings.get("test_video_path")
    video_url = run.settings.get("test_video_url")
    if isinstance(video_url, str) and video_url.strip():
        proof_lines.append(f"- Playwright video: {video_url}")
        proof_lines.append(
            f'<video controls preload="metadata" width="720" src="{video_url}">'
            f'<a href="{video_url}">Open Playwright video</a>'
            "</video>"
        )
    if isinstance(video_path, str) and video_path.strip():
        proof_lines.append(f"- Playwright video path: `{video_path}`")
    screenshots = run.settings.get("screenshot_paths")
    if isinstance(screenshots, list) and screenshots:
        proof_lines.append("- Screenshots:")
        proof_lines.extend(f"  - `{path}`" for path in screenshots if isinstance(path, str))
    proof_kind = run.settings.get("proof_kind")
    proof_status = run.settings.get("proof_status") or run.settings.get("test_proof_status")
    if isinstance(proof_kind, str) and proof_kind.strip():
        proof_lines.append(f"- Proof kind: {proof_kind}")
    if isinstance(proof_status, str) and proof_status.strip():
        proof_lines.append(f"- Proof status: {proof_status}")
    proof_warning = run.settings.get("proof_warning")
    if isinstance(proof_warning, str) and proof_warning.strip():
        proof_lines.append(f"- Proof warning: {proof_warning}")
    proof_logs = run.settings.get("proof_log_paths")
    if isinstance(proof_logs, list) and proof_logs:
        proof_lines.append("- Proof logs:")
        proof_lines.extend(f"  - `{path}`" for path in proof_logs if isinstance(path, str))
    proof = "\n\nTest proof:\n" + "\n".join(proof_lines) if proof_lines else ""
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
        f"{proof}"
        f"{proposed}"
    )


def _proof_score(settings: dict[str, object]) -> int:
    proof_kind = str(settings.get("proof_kind") or "")
    proof_status = str(settings.get("proof_status") or settings.get("test_proof_status") or "")
    if settings.get("test_video_url") or settings.get("test_video_path"):
        return 50
    screenshots = settings.get("screenshot_paths")
    if isinstance(screenshots, list) and screenshots:
        return 40
    if proof_kind == "browser_video" or proof_status in {"passed", "video_failed"}:
        return 35
    if proof_kind == "cli_logs" or proof_status.startswith("cli_"):
        return 25
    if proof_kind == "transcript_only" or proof_status == "transcript_only":
        return 10
    return 0


def _artifact_kind(path: Path) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name == "install.log" or "install" in name:
        return "install_log"
    if name == "build.log" or "build" in name:
        return "build_log"
    if name == "preview.log" or "preview" in name:
        return "preview_log"
    if name.endswith("desktop.png"):
        return "desktop_screenshot"
    if name.endswith("mobile.png"):
        return "mobile_screenshot"
    if suffix in {".webm", ".mp4"} or "video" in path.parts:
        return "playwright_video"
    if "summary" in name or "proof" in name:
        return "test_summary"
    if "metadata" in name:
        return "preview_metadata"
    if "transcript" in name:
        return "transcript"
    return "file"


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
        f"\n<p><strong>Role:</strong> {task.role or 'implementer'}</p>"
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
        role = _normalize_agent_role(task.role)
        labels.append(f"agent-{role.replace('_', '-')}")
    labels.extend(label for label in task.labels if label not in {"agent-proposed", "agent-followup"})
    return tuple(dict.fromkeys(labels))


def _needs_input_comment(run: RunRecord, question: str) -> str:
    return (
        f"codex-fleet needs input for run `{run.id}`.\n\n"
        f"Question: {question}\n\n"
        "Reply in a comment. Codex Fleet will resume automatically."
    )


def _is_repeated_needs_input_question(question: str, settings: dict[str, object]) -> bool:
    answers = settings.get("human_answers")
    if not isinstance(answers, list) or not answers:
        return False
    question_words = _question_words(question)
    if not question_words:
        return False
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        previous = str(answer.get("question") or "")
        previous_words = _question_words(previous)
        if not previous_words:
            continue
        overlap = len(question_words & previous_words) / max(1, len(question_words | previous_words))
        if overlap >= 0.55:
            return True
    return False


def _question_words(value: str) -> set[str]:
    import re

    stop = {"the", "a", "an", "and", "or", "for", "to", "of", "is", "are", "what", "please", "provide", "any"}
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if len(word) > 2 and word not in stop}


def _is_hard_human_blocker(value: str) -> bool:
    lowered = value.lower()
    hard_markers = (
        "app server",
        "authentication",
        "credential",
        "permission denied",
        "not authorized",
        "missing api key",
        "openai_api_key",
        "rate limit",
        "quota",
        "restart with",
        "local setup",
        "filedescriptor",
        "invalid json-rpc",
        "dependency installation failed",
        "npm install failed",
        "pnpm install failed",
        "yarn install failed",
        "pip install failed",
        "uv sync failed",
    )
    return any(marker in lowered for marker in hard_markers)


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
    model = getattr(runner, "model", None)
    if isinstance(model, str) and model.strip():
        return model.strip()
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


def _runner_reasoning_effort(runner: Runner) -> str | None:
    reasoning = getattr(runner, "reasoning_effort", None)
    return reasoning.strip() if isinstance(reasoning, str) and reasoning.strip() else None


def _agent_identity(item: WorkItem) -> tuple[str, str, str]:
    labels = {label.lower() for label in item.labels}
    if "agent-lead" in labels or "agent-orchestrator" in labels:
        return "orchestrator", "Orchestrator", "O"
    if "agent-code_scout" in labels or "agent-code-scout" in labels or "agent-scout" in labels:
        return "code_scout", "Scout", "S"
    if "agent-quality_reviewer" in labels or "agent-quality-reviewer" in labels or "agent-harness_reviewer" in labels or "agent-harness-reviewer" in labels or "agent-token_reviewer" in labels or "agent-token-reviewer" in labels:
        return "quality_reviewer", "Quality Reviewer", "Q"
    if "agent-test_reviewer" in labels or "agent-test-reviewer" in labels or "agent-test-agent" in labels:
        return "test_reviewer", "Test Agent", "T"
    if "agent-security_reviewer" in labels or "agent-security-reviewer" in labels:
        return "security_reviewer", "Security Reviewer", "S"
    if (
        "agent-reviewer" in labels
    ):
        return "reviewer", "Reviewer", "R"
    if "agent-worker" in labels:
        return "implementer", "Implementer", "I"
    return "implementer", "Implementer", "I"


def _agent_identity_from_role(role: str) -> tuple[str, str, str]:
    normalized = _normalize_agent_role(role)
    labels = {
        "lead": ("orchestrator", "Orchestrator", "O"),
        "orchestrator": ("orchestrator", "Orchestrator", "O"),
        "planner": ("planner", "Planner", "P"),
        "code_scout": ("code_scout", "Scout", "S"),
        "worker": ("implementer", "Implementer", "I"),
        "implementer": ("implementer", "Implementer", "I"),
        "reviewer": ("reviewer", "Reviewer", "R"),
        "quality_reviewer": ("quality_reviewer", "Quality Reviewer", "Q"),
        "security_reviewer": ("security_reviewer", "Security Reviewer", "S"),
        "test_reviewer": ("test_reviewer", "Test Agent", "T"),
        "delivery_manager": ("delivery_manager", "Delivery Manager", "D"),
    }
    return labels.get(normalized, ("implementer", "Implementer", "I"))


def _normalize_agent_role(role: str | None) -> str:
    normalized = (role or "").strip().lower().replace("-", "_")
    return {
        "harness_reviewer": "quality_reviewer",
        "token_reviewer": "quality_reviewer",
        "qa_reviewer": "test_reviewer",
        "tester": "test_reviewer",
        "test_agent": "test_reviewer",
        "worker": "implementer",
    }.get(normalized, normalized or "implementer")


def _normalize_workflow_mode(value: str) -> str:
    return value if value in {"execute_only", "plan_only", "plan_execute", "full_auto"} else "plan_execute"


def _parent_event_run_id(parent_item_id: str) -> str:
    return f"parent:{parent_item_id}"


def _dependency_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _committable_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines():
        if not line:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if not path or path.startswith(".codex-fleet"):
            continue
        paths.append(path)
    return paths


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
