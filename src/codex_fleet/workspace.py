from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from codex_fleet.models import WorkItem


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Workspace:
    path: Path
    branch_name: str
    created_now: bool


class WorktreeManager:
    def __init__(self, repo: Path, root: Path) -> None:
        self.repo = repo.expanduser().absolute()
        self.root = root.expanduser().absolute()

    def prepare(self, item: WorkItem) -> Workspace:
        self._validate_repo()
        self.root.mkdir(parents=True, exist_ok=True)
        branch = item.branch_name or f"codex-fleet/{item.safe_identifier}"
        path = (self.root / self.repo.name / item.safe_identifier).absolute()
        self._ensure_under_root(path)

        if path.exists():
            return Workspace(path=path, branch_name=branch, created_now=False)

        path.parent.mkdir(parents=True, exist_ok=True)
        command = ["git", "worktree", "add", "-b", branch, str(path)]
        result = subprocess.run(command, cwd=self.repo, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            # Branch may already exist from a previous failed attempt. Try attaching the worktree.
            fallback = subprocess.run(
                ["git", "worktree", "add", str(path), branch],
                cwd=self.repo,
                text=True,
                capture_output=True,
                check=False,
            )
            if fallback.returncode == 0:
                return Workspace(path=path, branch_name=branch, created_now=True)

            retry = self._try_suffixed_branch(path, branch)
            if retry is None:
                raise WorkspaceError(
                    "Failed to create git worktree. "
                    f"primary={result.stderr.strip()} fallback={fallback.stderr.strip()}"
                )
            return retry
        return Workspace(path=path, branch_name=branch, created_now=True)

    def _try_suffixed_branch(self, path: Path, base_branch: str) -> Workspace | None:
        for index in range(2, 20):
            branch = f"{base_branch}-{index}"
            result = subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(path)],
                cwd=self.repo,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                return Workspace(path=path, branch_name=branch, created_now=True)
        return None

    def _validate_repo(self) -> None:
        if not self.repo.exists():
            raise WorkspaceError(f"Repo does not exist: {self.repo}")
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise WorkspaceError(f"Not a git repository: {self.repo}")

    def _ensure_under_root(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError(f"Workspace path escaped root: {path}") from exc
