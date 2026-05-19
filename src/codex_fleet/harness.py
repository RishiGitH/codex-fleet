from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HarnessFile:
    path: Path
    content: str
    exists: bool


@dataclass(frozen=True)
class HarnessScan:
    git_root: Path | None
    dirty: bool | None
    stack: str | None
    package_manager: str | None
    install_command: str | None
    test_command: str | None
    lint_command: str | None
    typecheck_command: str | None
    build_command: str | None
    dev_command: str | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class HarnessPlan:
    repo: Path
    files: tuple[HarnessFile, ...]
    scan: HarnessScan

    @property
    def missing(self) -> tuple[HarnessFile, ...]:
        return tuple(file for file in self.files if not file.exists)

    @property
    def status(self) -> str:
        if self.scan.git_root is None:
            return "blocked"
        if self.missing:
            return "needs_setup"
        if self.scan.warnings:
            return "warnings"
        return "ready"


def plan_harness(repo: Path) -> HarnessPlan:
    repo = repo.expanduser().resolve()
    scan = _scan_repo(repo)
    files = [
        _file(repo, "AGENTS.md", _agents_md(scan)),
        _file(repo, "WORKFLOW.md", _workflow_md()),
        _file(repo, ".codex/config.toml", _codex_config()),
        _file(repo, ".codex/agents/code-scout.toml", _code_scout_agent()),
        _file(repo, ".agents/skills/repo-harness-review/SKILL.md", _repo_harness_skill()),
    ]
    return HarnessPlan(repo=repo, files=tuple(files), scan=scan)


def apply_harness(repo: Path, *, overwrite: bool = False) -> list[Path]:
    plan = plan_harness(repo)
    written: list[Path] = []
    for file in plan.files:
        target = plan.repo / file.path
        if target.exists() and not overwrite:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content)
        written.append(target)
    return written


def _file(repo: Path, relative: str, content: str) -> HarnessFile:
    path = Path(relative)
    return HarnessFile(path=path, content=content, exists=(repo / path).exists())


def _scan_repo(repo: Path) -> HarnessScan:
    git_root = _git_root(repo)
    stack = _detect_stack(repo)
    package_manager = _detect_package_manager(repo)
    commands = _detect_commands(repo)
    warnings: list[str] = []
    dirty = _dirty(repo) if git_root is not None else None
    if git_root is None:
        warnings.append("not a git repository")
    if dirty:
        warnings.append("git worktree has uncommitted changes")
    if commands.get("test") is None:
        warnings.append("test command not detected")
    return HarnessScan(
        git_root=git_root,
        dirty=dirty,
        stack=stack,
        package_manager=package_manager,
        install_command=commands.get("install"),
        test_command=commands.get("test"),
        lint_command=commands.get("lint"),
        typecheck_command=commands.get("typecheck"),
        build_command=commands.get("build"),
        dev_command=commands.get("dev"),
        warnings=tuple(warnings),
    )


def _git_root(repo: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _dirty(repo: Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def _detect_stack(repo: Path) -> str | None:
    if (repo / "package.json").exists():
        return "node"
    if (repo / "pyproject.toml").exists():
        return "python"
    if (repo / "Cargo.toml").exists():
        return "rust"
    if (repo / "go.mod").exists():
        return "go"
    if (repo / "mix.exs").exists():
        return "elixir"
    return None


def _detect_package_manager(repo: Path) -> str | None:
    if (repo / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (repo / "yarn.lock").exists():
        return "yarn"
    if (repo / "package-lock.json").exists() or (repo / "package.json").exists():
        return "npm"
    if (repo / "uv.lock").exists():
        return "uv"
    if (repo / "poetry.lock").exists():
        return "poetry"
    if (repo / "pyproject.toml").exists():
        return "pip"
    if (repo / "Cargo.toml").exists():
        return "cargo"
    if (repo / "go.mod").exists():
        return "go"
    return None


def _detect_commands(repo: Path) -> dict[str, str | None]:
    commands: dict[str, str | None] = {
        "install": None,
        "test": None,
        "lint": None,
        "typecheck": None,
        "build": None,
        "dev": None,
    }
    _merge_commands(commands, _node_commands(repo))
    _merge_commands(commands, _make_commands(repo))
    _merge_commands(commands, _python_commands(repo))
    _merge_commands(commands, _rust_commands(repo))
    _merge_commands(commands, _go_commands(repo))
    return commands


def _merge_commands(target: dict[str, str | None], source: dict[str, str | None]) -> None:
    for key, value in source.items():
        if target.get(key) is None and value is not None:
            target[key] = value


def _node_commands(repo: Path) -> dict[str, str | None]:
    package_json = repo / "package.json"
    if not package_json.exists():
        return {}
    manager = _detect_package_manager(repo) or "npm"
    run = "pnpm" if manager == "pnpm" else "yarn" if manager == "yarn" else "npm run"
    install = "pnpm install" if manager == "pnpm" else "yarn install" if manager == "yarn" else "npm install"
    try:
        data = json.loads(package_json.read_text())
    except json.JSONDecodeError:
        return {"install": install}
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return {"install": install}
    return {
        "install": install,
        "test": _script_command(run, scripts, "test"),
        "lint": _script_command(run, scripts, "lint"),
        "typecheck": _script_command(run, scripts, "typecheck") or _script_command(run, scripts, "type-check"),
        "build": _script_command(run, scripts, "build"),
        "dev": _script_command(run, scripts, "dev"),
    }


def _script_command(run: str, scripts: dict[object, object], name: str) -> str | None:
    if name not in scripts:
        return None
    return f"{run} {name}" if run != "yarn" else f"yarn {name}"


def _make_commands(repo: Path) -> dict[str, str | None]:
    makefile = repo / "Makefile"
    if not makefile.exists():
        return {}
    targets = set(re.findall(r"^([A-Za-z0-9_.-]+):", makefile.read_text(), flags=re.MULTILINE))
    return {
        "install": "make install" if "install" in targets else None,
        "test": "make test" if "test" in targets else None,
        "lint": "make lint" if "lint" in targets else None,
        "typecheck": "make typecheck" if "typecheck" in targets else None,
        "build": "make build" if "build" in targets else None,
        "dev": "make dev" if "dev" in targets else None,
    }


def _python_commands(repo: Path) -> dict[str, str | None]:
    if not (repo / "pyproject.toml").exists():
        return {}
    text = (repo / "pyproject.toml").read_text()
    package_manager = _detect_package_manager(repo)
    install = "uv sync" if package_manager == "uv" else "poetry install" if package_manager == "poetry" else "pip install -e '.[dev]'"
    return {
        "install": install,
        "test": "pytest" if (repo / "tests").exists() or "pytest" in text else None,
        "lint": "ruff check ." if "ruff" in text else None,
        "typecheck": "mypy ." if "mypy" in text else None,
        "build": "python -m build" if "[build-system]" in text else None,
    }


def _rust_commands(repo: Path) -> dict[str, str | None]:
    if not (repo / "Cargo.toml").exists():
        return {}
    return {"test": "cargo test", "lint": "cargo clippy", "build": "cargo build"}


def _go_commands(repo: Path) -> dict[str, str | None]:
    if not (repo / "go.mod").exists():
        return {}
    return {"test": "go test ./...", "build": "go build ./..."}


def _agents_md(scan: HarnessScan) -> str:
    commands = _detected_command_block(scan)
    return f"""# AGENTS.md

This repo is configured for Codex.

## Commands

{commands}

## Rules

- Keep changes small.
- Run relevant tests.
- Do not commit secrets.
- Do not deploy or merge without human approval.
- Update docs when behavior changes.
- If more work is discovered, propose follow-up Plane tasks as `agent-proposed`
  instead of silently widening scope.
"""


def _detected_command_block(scan: HarnessScan) -> str:
    rows = [
        ("Install", scan.install_command),
        ("Test", scan.test_command),
        ("Lint", scan.lint_command),
        ("Typecheck", scan.typecheck_command),
        ("Build", scan.build_command),
        ("Dev", scan.dev_command),
    ]
    detected = [f"- {label}: `{command}`" for label, command in rows if command]
    if detected:
        return "\n".join(detected)
    return "Document install, test, lint, typecheck, build, and run commands here."


def _workflow_md() -> str:
    return """# WORKFLOW

Default work item flow:

Backlog -> Ready -> Running -> Human Review -> Done

Failed or incomplete work goes to Rework.
Blocked work goes to Blocked.

Task source labels:

- `human-requested`: created by a human.
- `agent-proposed`: proposed by an agent during a run and needs human approval.
- `agent-followup`: approved follow-up work derived from a previous run.

Agents should make the smallest correct change, run relevant tests, and report proof of work.
"""


def _codex_config() -> str:
    return """model = \"gpt-5.5\"
model_reasoning_effort = \"low\"
sandbox_mode = \"workspace-write\"
approval_policy = \"on-request\"

[agents]
max_threads = 3
max_depth = 1
job_max_runtime_seconds = 1200

[agents.code_scout]
description = \"Read-only repo explorer for finding relevant files and tests.\"
config_file = \"agents/code-scout.toml\"
"""


def _code_scout_agent() -> str:
    return """name = \"code_scout\"
description = \"Read-only repo explorer for finding relevant files and tests.\"
model = \"gpt-5.4-mini\"
model_reasoning_effort = \"medium\"
sandbox_mode = \"read-only\"

developer_instructions = \"\"\"
Do not edit files.
Find relevant files, commands, and tests.
Return concise findings and uncertainty.
\"\"\"
"""


def _repo_harness_skill() -> str:
    return """---
name: repo-harness-review
description: Review whether a change keeps this repo easy for Codex agents to use.
---

# Repo Harness Review

Check setup commands, tests, docs, secrets, and unclear architecture.
Return concrete fixes only.
"""
