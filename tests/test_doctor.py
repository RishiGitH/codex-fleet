import subprocess
from pathlib import Path

from codex_fleet.doctor import scan_repo


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def test_doctor_scores_empty_non_git_repo(tmp_path: Path) -> None:
    report = scan_repo(tmp_path)

    assert report.score < 80
    assert any(f.code == "missing_git" for f in report.findings)
    assert any(f.code == "missing_agents_md" for f in report.findings)


def test_doctor_scores_basic_ready_repo(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# Agents\n")
    (tmp_path / "WORKFLOW.md").write_text("# Workflow\n")
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text("model = 'gpt-5.4-mini'\n")
    (tmp_path / ".env.example").write_text("OPENAI_API_KEY=\n")
    (tmp_path / "README.md").write_text("# Example\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "tests").mkdir()

    report = scan_repo(tmp_path)

    assert report.score >= 80
    assert not any(f.severity == "error" for f in report.findings)
