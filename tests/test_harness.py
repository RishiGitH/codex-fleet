from pathlib import Path

from codex_fleet.harness import apply_harness, plan_harness


def test_plan_harness_lists_missing_files(tmp_path: Path) -> None:
    plan = plan_harness(tmp_path)

    missing = {str(file.path) for file in plan.missing}

    assert "AGENTS.md" in missing
    assert "WORKFLOW.md" in missing
    assert ".codex/config.toml" in missing


def test_apply_harness_writes_missing_files_without_overwriting(tmp_path: Path) -> None:
    existing = tmp_path / "AGENTS.md"
    existing.write_text("custom\n")

    written = apply_harness(tmp_path)

    assert existing.read_text() == "custom\n"
    assert tmp_path / "WORKFLOW.md" in written
    assert (tmp_path / ".codex" / "config.toml").exists()
    assert (tmp_path / ".agents" / "skills" / "repo-harness-review" / "SKILL.md").exists()


def test_apply_harness_can_overwrite(tmp_path: Path) -> None:
    existing = tmp_path / "AGENTS.md"
    existing.write_text("custom\n")

    apply_harness(tmp_path, overwrite=True)

    assert "This repo is configured for Codex" in existing.read_text()
