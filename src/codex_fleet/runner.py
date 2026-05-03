from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from codex_fleet.models import RunResult, WorkItem


class Runner(ABC):
    @abstractmethod
    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        raise NotImplementedError


class FakeRunner(Runner):
    """Deterministic runner used for tests and local smoke checks."""

    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed

    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        marker = workspace / ".codex-fleet-fake-run.txt"
        marker.write_text(f"Fake run for {item.identifier}: {item.title}\n")
        if self.succeed:
            return RunResult(
                success=True,
                summary=f"Fake runner completed {item.identifier}.",
                changed_files=(str(marker),),
                test_commands=("fake-tests: passed",),
                artifacts=(marker,),
            )
        return RunResult(success=False, summary="Fake runner failed.", error="configured failure")


class CodexAppServerRunner(Runner):
    """Placeholder for the real Codex App Server runner.

    Phase 2 will implement JSON-RPC over stdio for `codex app-server`.
    Keeping the interface now lets the orchestrator and tests become stable first.
    """

    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        raise NotImplementedError("Real Codex App Server runner is planned for Phase 2")
