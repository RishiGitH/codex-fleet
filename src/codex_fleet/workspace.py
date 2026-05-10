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

    def prepare(self, item: WorkItem, *, base_branch: str | None = None) -> Workspace:
        self._validate_repo()
        self.root.mkdir(parents=True, exist_ok=True)
        branch = item.branch_name or f"codex-fleet/{item.safe_identifier}"
        path = (self.root / self.repo.name / item.safe_identifier).absolute()
        self._ensure_under_root(path)

        if path.exists():
            return Workspace(path=path, branch_name=branch, created_now=False)

        path.parent.mkdir(parents=True, exist_ok=True)
        command = ["git", "worktree", "add", "-b", branch, str(path)]
        if base_branch:
            command.append(base_branch)
        result = subprocess.run(command, cwd=self.repo, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            if self._looks_like_stale_worktree(result.stderr):
                self._prune_stale_worktrees()
                retry_primary = subprocess.run(command, cwd=self.repo, text=True, capture_output=True, check=False)
                if retry_primary.returncode == 0:
                    return Workspace(path=path, branch_name=branch, created_now=True)
                result = retry_primary
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
            if self._looks_like_stale_worktree(fallback.stderr):
                self._prune_stale_worktrees()
                retry_fallback = subprocess.run(
                    ["git", "worktree", "add", str(path), branch],
                    cwd=self.repo,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if retry_fallback.returncode == 0:
                    return Workspace(path=path, branch_name=branch, created_now=True)
                fallback = retry_fallback

            retry = self._try_suffixed_branch(path, branch, base_branch=base_branch)
            if retry is None:
                raise WorkspaceError(
                    "Failed to create git worktree. "
                    f"primary={result.stderr.strip()} fallback={fallback.stderr.strip()}"
                )
            return retry
        return Workspace(path=path, branch_name=branch, created_now=True)

    def _try_suffixed_branch(self, path: Path, branch_prefix: str, *, base_branch: str | None = None) -> Workspace | None:
        for index in range(2, 20):
            branch = f"{branch_prefix}-{index}"
            retry_path = path.with_name(f"{path.name}-{index}")
            self._ensure_under_root(retry_path)
            command = ["git", "worktree", "add", "-b", branch, str(retry_path)]
            if base_branch:
                command.append(base_branch)
            result = subprocess.run(command, cwd=self.repo, text=True, capture_output=True, check=False)
            if result.returncode == 0:
                return Workspace(path=retry_path, branch_name=branch, created_now=True)
        return None

    def _looks_like_stale_worktree(self, stderr: str) -> bool:
        lowered = stderr.lower()
        return "missing but already registered worktree" in lowered or "use 'add -f' to override" in lowered

    def _prune_stale_worktrees(self) -> None:
        self._ensure_under_root(self.root)
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo, text=True, capture_output=True, check=False)

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
