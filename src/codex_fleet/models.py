from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class WorkItemState(str, Enum):
    BACKLOG = "Backlog"
    READY = "Ready"
    RUNNING = "Running"
    HUMAN_REVIEW = "Human Review"
    REWORK = "Rework"
    DONE = "Done"
    BLOCKED = "Blocked"
    CANCELLED = "Cancelled"


class RunStatus(str, Enum):
    QUEUED = "queued"
    PREPARING_WORKSPACE = "preparing_workspace"
    RUNNING_CODEX = "running_codex"
    HUMAN_REVIEW = "human_review"
    DONE = "done"
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
class RunResult:
    success: bool
    summary: str
    changed_files: tuple[str, ...] = ()
    test_commands: tuple[str, ...] = ()
    artifacts: tuple[Path, ...] = ()
    error: str | None = None


@dataclass
class RunRecord:
    id: str
    item: WorkItem
    status: RunStatus
    worktree_path: Path | None = None
    branch_name: str | None = None
    codex_thread_id: str | None = None
    codex_turn_id: str | None = None
    attempts: int = 0
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def mark(self, status: RunStatus, error: str | None = None) -> None:
        self.status = status
        self.error = error
        self.updated_at = datetime.now(timezone.utc)
