from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from codex_fleet.models import WorkItem
from codex_fleet.workspace import WorktreeManager


def test_worktree_manager_prunes_stale_registered_worktree_and_retries(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "workspaces"
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path, text: bool, capture_output: bool, check: bool) -> Any:
        calls.append(command)
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, stdout=str(repo), stderr="")
        if command == ["git", "worktree", "prune"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:4] == ["git", "worktree", "add", "-b"] and calls.count(command) == 1:
            return subprocess.CompletedProcess(
                command,
                128,
                stdout="",
                stderr="'workspaces/repo/CF-1' is a missing but already registered worktree; use 'add -f' to override",
            )
        if command[:4] == ["git", "worktree", "add", "-b"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="unexpected")

    monkeypatch.setattr("codex_fleet.workspace.subprocess.run", fake_run)

    workspace = WorktreeManager(repo, root).prepare(
        WorkItem(id="1", identifier="CF-1", title="Task", description=None, state="Ready")
    )

    assert workspace.created_now is True
    assert workspace.path == root / "repo" / "CF-1"
    assert ["git", "worktree", "prune"] in calls


def test_worktree_manager_fast_forwards_existing_dependency_worktree(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "workspaces"
    path = root / "repo" / "CF-1"
    path.mkdir(parents=True)
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path, text: bool, capture_output: bool, check: bool) -> Any:
        calls.append(command)
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, stdout=str(repo), stderr="")
        if command[:3] == ["git", "merge", "--ff-only"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="unexpected")

    monkeypatch.setattr("codex_fleet.workspace.subprocess.run", fake_run)

    workspace = WorktreeManager(repo, root).prepare(
        WorkItem(id="1", identifier="CF-1", title="Task", description=None, state="Ready"),
        base_branch="codex-fleet/CF-0",
    )

    assert workspace.created_now is False
    assert ["git", "merge", "--ff-only", "codex-fleet/CF-0"] in calls
