from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from codex_fleet.codex.app_server import AppServerClient, AppServerError
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
    """Runs one work item through Codex App Server."""

    def __init__(
        self,
        command: str = "codex app-server",
        approval_policy: str = "on-request",
        sandbox_mode: str = "workspace-write",
        timeout_seconds: int = 3600,
    ) -> None:
        self.command = command
        self.approval_policy = approval_policy
        self.sandbox_mode = sandbox_mode
        self.timeout_seconds = timeout_seconds

    def run(self, item: WorkItem, workspace: Path) -> RunResult:
        prompt = _prompt_for_item(item)
        client = AppServerClient(
            self.command,
            workspace,
            approval_policy=self.approval_policy,
            sandbox_mode=self.sandbox_mode,
            timeout_seconds=self.timeout_seconds,
        )
        try:
            outcome = client.run_turn(prompt=prompt, title=f"{item.identifier}: {item.title}")
        except (AppServerError, OSError) as exc:
            return RunResult(success=False, summary="Codex App Server failed.", error=str(exc))

        return RunResult(
            success=outcome.completed,
            summary=f"Codex {outcome.summary} for {item.identifier}.",
            test_commands=("reported by Codex",),
            error=None if outcome.completed else outcome.summary,
        )


def _prompt_for_item(item: WorkItem) -> str:
    description = item.description or "No description provided."
    return (
        f"Work item {item.identifier}: {item.title}\n\n"
        f"Description:\n{description}\n\n"
        "Make the smallest correct change, run relevant tests, and summarize the result."
    )
