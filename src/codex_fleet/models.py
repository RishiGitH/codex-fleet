from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class WorkItemState(StrEnum):
    BACKLOG = "Backlog"
    PLANNING = "Planning"
    READY = "Ready"
    RUNNING = "Running"
    NEEDS_INPUT = "Needs Input"
    HUMAN_REVIEW = "Human Review"
    REWORK = "Rework"
    DONE = "Done"
    BLOCKED = "Blocked"
    CANCELLED = "Cancelled"


class RunStatus(StrEnum):
    QUEUED = "queued"
    CLAIM_ACQUIRED = "claim_acquired"
    PREPARING_WORKSPACE = "preparing_workspace"
    WORKSPACE_READY = "workspace_ready"
    RUNNER_STARTED = "runner_started"
    RUNNER_STREAMING = "runner_streaming"
    RUNNING_CODEX = "running_codex"
    RUNNER_COMPLETED = "runner_completed"
    COLLECTING_EVIDENCE = "collecting_evidence"
    PLANNING = "planning"
    NEEDS_INPUT = "needs_input"
    HUMAN_REVIEW = "human_review"
    DONE = "done"
    REWORK = "rework"
    BLOCKED = "blocked"
    CANCEL_REQUESTED = "cancel_requested"
    FAILED = "failed"
    STALLED = "stalled"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class BlockerRef:
    id: str | None = None
    identifier: str | None = None
    state: str | None = None


@dataclass(frozen=True)
class WorkItem:
    id: str
    identifier: str
    title: str
    description: str | None
    state: str
    priority: int | None = None
    branch_name: str | None = None
    url: str | None = None
    labels: tuple[str, ...] = ()
    blocked_by: tuple[BlockerRef, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def safe_identifier(self) -> str:
        return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in self.identifier)


@dataclass(frozen=True)
class WorkItemComment:
    id: str
    author_display_name: str
    created_at: datetime | None
    body_text: str
    is_codex_fleet: bool = False


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class RunMessage:
    sequence: int
    kind: str
    content: str
    agent_role: str | None = None
    agent_name: str | None = None
    artifact_path: Path | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    success: bool
    summary: str
    changed_files: tuple[str, ...] = ()
    test_commands: tuple[str, ...] = ()
    artifacts: tuple[Path, ...] = ()
    proposed_tasks: tuple[ProposedTask, ...] = ()
    needs_input: NeedsInput | None = None
    token_usage: TokenUsage | None = None
    messages: tuple[RunMessage, ...] = ()
    codex_thread_id: str | None = None
    codex_turn_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class NeedsInput:
    question: str
    needed_to_continue: bool = True
    suggested_state: str = WorkItemState.NEEDS_INPUT.value


@dataclass(frozen=True)
class ProposedTask:
    title: str
    description: str | None = None
    role: str | None = None
    depends_on: tuple[str, ...] = ()
    suggested_state: str | None = None
    labels: tuple[str, ...] = ("agent-proposed",)


@dataclass
class RunRecord:
    id: str
    item: WorkItem
    status: RunStatus
    worktree_path: Path | None = None
    branch_name: str | None = None
    codex_thread_id: str | None = None
    codex_turn_id: str | None = None
    runner_name: str | None = None
    agent_role: str | None = None
    agent_name: str | None = None
    agent_avatar: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    token_usage: TokenUsage | None = None
    attempts: int = 0
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def mark(self, status: RunStatus, error: str | None = None) -> None:
        self.status = status
        self.error = error
        self.updated_at = datetime.now(UTC)
