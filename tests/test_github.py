from pathlib import Path

import pytest

from codex_fleet.github import GitHubClient, GitHubError


def test_github_client_raises_for_non_repo_command(tmp_path: Path) -> None:
    client = GitHubClient(tmp_path)

    with pytest.raises(GitHubError):
        client.current_branch()
