from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from codex_fleet.config import FleetConfig
from codex_fleet.models import RunRecord, RunStatus, WorkItemState
from codex_fleet.runner import Runner
from codex_fleet.tracker import Tracker
from codex_fleet.workspace import WorktreeManager


@dataclass(frozen=True)
class OrchestratorResult:
    dispatched: bool
    run: RunRecord | None
    message: str


class Orchestrator:
    """Small Symphony-style coordinator.

    Phase 1 intentionally runs one item at a time. The production daemon will extend
    this with polling, retries, stall detection, and persistent state.
    """

    def __init__(self, config: FleetConfig, tracker: Tracker, runner: Runner) -> None:
        self.config = config
        self.tracker = tracker
        self.runner = runner
        self.worktrees = WorktreeManager(config.repo, config.workspace.root)

    def run_once(self) -> OrchestratorResult:
        candidates = self.tracker.fetch_candidate_items()
        if not candidates:
            return OrchestratorResult(False, None, "No candidate work items found.")

        item = sorted(candidates, key=_dispatch_sort_key)[0]
        run = RunRecord(id=str(uuid4()), item=item, status=RunStatus.QUEUED)

        self.tracker.update_item_state(item.id, WorkItemState.RUNNING.value)
        self.tracker.create_comment(item.id, f"🤖 codex-fleet started run `{run.id}` for `{item.identifier}`.")

        try:
            run.mark(RunStatus.PREPARING_WORKSPACE)
            workspace = self.worktrees.prepare(item)
            run.worktree_path = workspace.path
            run.branch_name = workspace.branch_name

            run.mark(RunStatus.RUNNING_CODEX)
            result = self.runner.run(item, workspace.path)
            if result.success:
                run.mark(RunStatus.HUMAN_REVIEW)
                self.tracker.create_comment(
                    item.id,
                    _success_comment(run, result.summary, result.test_commands),
                )
                self.tracker.update_item_state(item.id, WorkItemState.HUMAN_REVIEW.value)
                return OrchestratorResult(True, run, "Run completed and moved to Human Review.")

            run.mark(RunStatus.FAILED, result.error)
            self.tracker.create_comment(item.id, f"❌ codex-fleet run failed: {result.error or result.summary}")
            self.tracker.update_item_state(item.id, WorkItemState.REWORK.value)
            return OrchestratorResult(True, run, "Run failed and moved to Rework.")
        except Exception as exc:  # noqa: BLE001 - orchestration boundary converts failures to tracker status.
            run.mark(RunStatus.FAILED, str(exc))
            self.tracker.create_comment(item.id, f"❌ codex-fleet orchestration error: {exc}")
            self.tracker.update_item_state(item.id, WorkItemState.REWORK.value)
            return OrchestratorResult(True, run, "Run errored and moved to Rework.")


def _dispatch_sort_key(item: object) -> tuple[int, str]:
    priority = getattr(item, "priority", None)
    rank = priority if isinstance(priority, int) and 1 <= priority <= 4 else 5
    identifier = getattr(item, "identifier", "")
    return rank, str(identifier)


def _success_comment(run: RunRecord, summary: str, test_commands: tuple[str, ...]) -> str:
    tests = "\n".join(f"- {command}" for command in test_commands) or "- not reported"
    return (
        f"✅ codex-fleet completed run `{run.id}`.\n\n"
        f"Branch: `{run.branch_name}`\n"
        f"Workspace: `{run.worktree_path}`\n\n"
        f"Summary: {summary}\n\n"
        f"Verification:\n{tests}"
    )
