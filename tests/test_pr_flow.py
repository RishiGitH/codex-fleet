from pathlib import Path

import codex_fleet.pr_flow as pr_flow
from codex_fleet.github import PullRequestInfo
from codex_fleet.pr_flow import PrRequest, create_draft_pr


class FakeClient:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def push_branch(self, branch_name: str) -> None:
        assert branch_name == "feature/test"

    def create_pr(self, title: str, body: str, base: str = "main", draft: bool = True) -> PullRequestInfo:
        assert title == "Test"
        assert body == "Body"
        assert base == "main"
        assert draft is True
        return PullRequestInfo(url="local-pr", number="1")


def test_create_draft_pr_uses_client(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pr_flow, "GitHubClient", FakeClient)

    result = create_draft_pr(
        PrRequest(
            repo=tmp_path,
            branch_name="feature/test",
            title="Test",
            body="Body",
        )
    )

    assert result.created is True
    assert result.pr is not None
    assert result.pr.url == "local-pr"
