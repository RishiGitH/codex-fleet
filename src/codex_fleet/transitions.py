from __future__ import annotations

from dataclasses import dataclass

from codex_fleet.models import RunStatus, WorkItemState


@dataclass(frozen=True)
class TransitionRule:
    source: str
    target: str
    actor: str
    event: str
    comment: str | None = None


WORK_ITEM_TRANSITIONS: tuple[TransitionRule, ...] = (
    TransitionRule(WorkItemState.READY.value, WorkItemState.RUNNING.value, "daemon", "started", "codex-fleet started work."),
    TransitionRule(WorkItemState.RUNNING.value, WorkItemState.PLANNING.value, "daemon", "parent_waiting", "codex-fleet created child tasks."),
    TransitionRule(WorkItemState.RUNNING.value, WorkItemState.NEEDS_INPUT.value, "daemon", "needs_input", "codex-fleet needs input."),
    TransitionRule(WorkItemState.RUNNING.value, WorkItemState.HUMAN_REVIEW.value, "daemon", "completed", "codex-fleet completed work."),
    TransitionRule(WorkItemState.RUNNING.value, WorkItemState.NEEDS_INPUT.value, "daemon", "failed", "codex-fleet run failed and needs input."),
    TransitionRule(WorkItemState.RUNNING.value, WorkItemState.NEEDS_INPUT.value, "daemon", "blocked", "codex-fleet is blocked by local setup."),
    TransitionRule(WorkItemState.PLANNING.value, WorkItemState.HUMAN_REVIEW.value, "daemon", "parent_children_completed", "all child tasks are ready for review."),
    TransitionRule(WorkItemState.NEEDS_INPUT.value, WorkItemState.READY.value, "human", "input_answered", "human answered codex-fleet input."),
    TransitionRule(WorkItemState.NEEDS_INPUT.value, WorkItemState.READY.value, "human", "retry_requested", "codex-fleet retry requested."),
    TransitionRule(WorkItemState.RUNNING.value, WorkItemState.NEEDS_INPUT.value, "human", "cancelled", "codex-fleet cancelled work."),
    TransitionRule(WorkItemState.HUMAN_REVIEW.value, WorkItemState.DONE.value, "human", "accepted", "human accepted work."),
)

RUN_TRANSITIONS: tuple[TransitionRule, ...] = (
    TransitionRule(RunStatus.QUEUED.value, RunStatus.CLAIM_ACQUIRED.value, "daemon", "claimed"),
    TransitionRule(RunStatus.CLAIM_ACQUIRED.value, RunStatus.PREPARING_WORKSPACE.value, "daemon", "workspace_preparing"),
    TransitionRule(RunStatus.PREPARING_WORKSPACE.value, RunStatus.WORKSPACE_READY.value, "daemon", "workspace_prepared"),
    TransitionRule(RunStatus.WORKSPACE_READY.value, RunStatus.RUNNER_STARTED.value, "daemon", "runner_started"),
    TransitionRule(RunStatus.RUNNER_STARTED.value, RunStatus.RUNNING_CODEX.value, "runner", "runner_streaming"),
    TransitionRule(RunStatus.RUNNING_CODEX.value, RunStatus.RUNNER_COMPLETED.value, "runner", "runner_finished"),
    TransitionRule(RunStatus.RUNNER_COMPLETED.value, RunStatus.COLLECTING_EVIDENCE.value, "daemon", "collecting_evidence"),
    TransitionRule(RunStatus.COLLECTING_EVIDENCE.value, RunStatus.PLANNING.value, "daemon", "parent_waiting"),
    TransitionRule(RunStatus.COLLECTING_EVIDENCE.value, RunStatus.NEEDS_INPUT.value, "daemon", "needs_input"),
    TransitionRule(RunStatus.COLLECTING_EVIDENCE.value, RunStatus.HUMAN_REVIEW.value, "daemon", "completed"),
    TransitionRule(RunStatus.COLLECTING_EVIDENCE.value, RunStatus.REWORK.value, "daemon", "failed"),
    TransitionRule(RunStatus.COLLECTING_EVIDENCE.value, RunStatus.BLOCKED.value, "daemon", "blocked"),
    TransitionRule(RunStatus.RUNNING_CODEX.value, RunStatus.CANCEL_REQUESTED.value, "human", "cancel_requested"),
    TransitionRule(RunStatus.CANCEL_REQUESTED.value, RunStatus.CANCELLED.value, "daemon", "cancelled"),
)

SUCCESS_CHILD_STATES = frozenset({WorkItemState.HUMAN_REVIEW.value.lower(), WorkItemState.DONE.value.lower()})
BLOCKING_CHILD_STATES = frozenset(
    {
        WorkItemState.NEEDS_INPUT.value.lower(),
        WorkItemState.REWORK.value.lower(),
        WorkItemState.BLOCKED.value.lower(),
        WorkItemState.CANCELLED.value.lower(),
    }
)
ACTIVE_CHILD_STATES = frozenset({WorkItemState.READY.value.lower(), WorkItemState.RUNNING.value.lower(), WorkItemState.PLANNING.value.lower()})


def is_successful_child_state(state: str) -> bool:
    return state.lower() in SUCCESS_CHILD_STATES


def is_blocking_child_state(state: str) -> bool:
    return state.lower() in BLOCKING_CHILD_STATES


def is_active_child_state(state: str) -> bool:
    return state.lower() in ACTIVE_CHILD_STATES
