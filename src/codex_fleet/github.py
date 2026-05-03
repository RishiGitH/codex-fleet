from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class PullRequestInfo:
    url: str
    number: str | None = None


class GitHubClient:
    """Small wrapper around GitHub CLI for local-first PR creation.

    We intentionally do not auto-merge. The helper only pushes a branch and opens
    a PR when the user has `gh` installed and authenticated.
    """

    def __init__(self, repo: Path) -> None:
        self.repo = repo.expanduser().resolve()

    def push_branch(self, branch_name: str) -> None:
        _run(["git", "push", "-u", "origin", branch_name], self.repo)

    def create_pr(self, title: str, body: str, base: str = "main", draft: bool = True) -> PullRequestInfo:
        command = ["gh", "pr", "create", "--title", title, "--body", body, "--base", base]
        if draft:
            command.append("--draft")
        result = _run(command, self.repo)
        url = result.strip().splitlines()[-1]
        return PullRequestInfo(url=url)

    def current_branch(self) -> str:
        return _run(["git", "branch", "--show-current"], self.repo).strip()


def github_available() -> bool:
    return _which("gh") and _which("git")


def _run(command: list[str], cwd: Path) -> str:
    env = os.environ.copy()
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, env=env, check=False)
    if result.returncode != 0:
        raise GitHubError(result.stderr.strip() or result.stdout.strip() or f"Command failed: {command}")
    return result.stdout


def _which(binary: str) -> bool:
    result = subprocess.run(["which", binary], text=True, capture_output=True, check=False)
    return result.returncode == 0
