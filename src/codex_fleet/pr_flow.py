from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codex_fleet.github import GitHubClient, GitHubError, PullRequestInfo


@dataclass(frozen=True)
class PrRequest:
    repo: Path
    branch_name: str
    title: str
    body: str
    base_branch: str = "main"
    draft: bool = True


@dataclass(frozen=True)
class PrResult:
    created: bool
    message: str
    pr: PullRequestInfo | None = None


def create_draft_pr(request: PrRequest) -> PrResult:
    client = GitHubClient(request.repo)
    try:
        client.push_branch(request.branch_name)
        pr = client.create_pr(
            title=request.title,
            body=request.body,
            base=request.base_branch,
            draft=request.draft,
        )
    except GitHubError as exc:
        return PrResult(created=False, message=str(exc), pr=None)
    return PrResult(created=True, message=f"Created PR: {pr.url}", pr=pr)
