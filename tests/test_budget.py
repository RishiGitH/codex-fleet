from pathlib import Path

from click.testing import CliRunner

from codex_fleet.budget import scan_budget
from codex_fleet.cli.main import main
from codex_fleet.config import load_config


def test_budget_scanner_includes_guidance_docs_skills_and_agents(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Agents\n")
    (tmp_path / "WORKFLOW.md").write_text("# Workflow\n")
    (tmp_path / "README.md").write_text("# Readme\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "token-policy.md").write_text("# Token policy\n")
    (tmp_path / ".agents" / "skills" / "x").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "x" / "SKILL.md").write_text("---\nname: x\n---\n")
    (tmp_path / ".codex" / "agents").mkdir(parents=True)
    (tmp_path / ".codex" / "agents" / "token-reviewer.toml").write_text('name = "x"\n')

    summary = scan_budget(tmp_path)
    paths = {entry.path for entry in summary.entries}

    assert "AGENTS.md" in paths
    assert "WORKFLOW.md" in paths
    assert "README.md" in paths
    assert "docs/token-policy.md" in paths
    assert ".agents/skills/x/SKILL.md" in paths
    assert ".codex/agents/token-reviewer.toml" in paths


def test_budget_cli_strict_vs_non_strict(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x" * 100)
    (tmp_path / "WORKFLOW.md").write_text("# Workflow\n")
    (tmp_path / "README.md").write_text("# Readme\n")
    (tmp_path / ".codex-fleet.yml").write_text(
        "repo: .\n"
        "token:\n"
        "  default_doc_limit: 1\n"
        "  skill_limit: 1\n"
    )
    runner = CliRunner()

    non_strict = runner.invoke(main, ["budget", "--repo", str(tmp_path)])
    strict = runner.invoke(main, ["budget", "--repo", str(tmp_path), "--strict"])

    assert non_strict.exit_code == 0
    assert "too large" in non_strict.output
    assert strict.exit_code != 0


def test_token_config_loads_optional_placeholders(tmp_path: Path) -> None:
    config_path = tmp_path / ".codex-fleet.yml"
    config_path.write_text(
        "repo: .\n"
        "token:\n"
        "  default_doc_limit: 123\n"
        "  skill_limit: 45\n"
        "  raw_artifact_retention: 30d\n"
        "  compression_mode: native\n"
        "  context_pack_profile: task\n"
        "  enable_rtk: true\n"
        "  enable_caveman: false\n"
        "  enable_repomix: true\n"
    )

    config = load_config(tmp_path, config_path=config_path)

    assert config.token.default_doc_limit == 123
    assert config.token.skill_limit == 45
    assert config.token.raw_artifact_retention == "30d"
    assert config.token.compression_mode == "native"
    assert config.token.context_pack_profile == "task"
    assert config.token.enable_rtk is True
    assert config.token.enable_caveman is False
    assert config.token.enable_repomix is True
