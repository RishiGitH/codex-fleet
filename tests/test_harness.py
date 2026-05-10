import subprocess
from pathlib import Path

from codex_fleet.harness import apply_harness, plan_harness


def test_plan_harness_lists_missing_files(tmp_path: Path) -> None:
    plan = plan_harness(tmp_path)

    missing = {str(file.path) for file in plan.missing}

    assert "AGENTS.md" in missing
    assert "README.md" in missing
    assert "WORKFLOW.md" in missing
    assert ".codex-fleet/project.json" in missing
    assert ".codex/config.toml" in missing


def test_apply_harness_writes_missing_files_without_overwriting(tmp_path: Path) -> None:
    existing = tmp_path / "AGENTS.md"
    existing.write_text("custom\n")

    written = apply_harness(tmp_path)

    assert existing.read_text() == "custom\n"
    assert tmp_path / "WORKFLOW.md" in written
    assert (tmp_path / ".codex-fleet" / "project.json").exists()
    assert (tmp_path / ".codex" / "config.toml").exists()
    assert (tmp_path / ".agents" / "skills" / "repo-harness-review" / "SKILL.md").exists()


def test_apply_harness_can_overwrite(tmp_path: Path) -> None:
    existing = tmp_path / "AGENTS.md"
    existing.write_text("custom\n")

    apply_harness(tmp_path, overwrite=True)

    assert "This repo is configured for Codex" in existing.read_text()


def test_plan_harness_detects_node_commands(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest","lint":"eslint .","typecheck":"tsc --noEmit","build":"vite build","dev":"vite"}}\n'
    )
    (tmp_path / "pnpm-lock.yaml").write_text("")

    plan = plan_harness(tmp_path)

    assert plan.scan.git_root == tmp_path
    assert plan.scan.stack == "node"
    assert plan.scan.package_manager == "pnpm"
    assert plan.scan.install_command == "pnpm install"
    assert plan.scan.test_command == "pnpm test"
    assert plan.scan.lint_command == "pnpm lint"
    assert plan.scan.typecheck_command == "pnpm typecheck"
    assert plan.scan.build_command == "pnpm build"
    assert plan.scan.dev_command == "pnpm dev"


def test_harness_project_json_records_detected_commands(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest"}}\n')

    apply_harness(tmp_path)

    data = (tmp_path / ".codex-fleet" / "project.json").read_text()
    assert '"workflow_mode": "plan_execute"' in data
    assert '"test": "npm run test"' in data


def test_plan_harness_detects_python_commands_and_status(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[build-system]\nrequires=[]\n[tool.ruff]\n[tool.mypy]\n")
    (tmp_path / "tests").mkdir()
    apply_harness(tmp_path)

    plan = plan_harness(tmp_path)

    assert plan.status == "warnings"
    assert plan.scan.stack == "python"
    assert plan.scan.install_command == "pip install -e '.[dev]'"
    assert plan.scan.test_command == "pytest"
    assert plan.scan.lint_command == "ruff check ."
    assert plan.scan.typecheck_command == "mypy ."
    assert plan.scan.build_command == "python -m build"
    assert "git worktree has uncommitted changes" in plan.scan.warnings


def test_plan_harness_blocks_non_git_repo(tmp_path: Path) -> None:
    plan = plan_harness(tmp_path)

    assert plan.status == "blocked"
    assert "not a git repository" in plan.scan.warnings


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
