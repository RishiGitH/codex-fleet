from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HarnessFile:
    path: Path
    content: str
    exists: bool


@dataclass(frozen=True)
class HarnessPlan:
    repo: Path
    files: tuple[HarnessFile, ...]

    @property
    def missing(self) -> tuple[HarnessFile, ...]:
        return tuple(file for file in self.files if not file.exists)


def plan_harness(repo: Path) -> HarnessPlan:
    repo = repo.expanduser().resolve()
    files = [
        _file(repo, "AGENTS.md", _agents_md()),
        _file(repo, "WORKFLOW.md", _workflow_md()),
        _file(repo, ".codex/config.toml", _codex_config()),
        _file(repo, ".codex/agents/code-scout.toml", _code_scout_agent()),
        _file(repo, ".agents/skills/repo-harness-review/SKILL.md", _repo_harness_skill()),
    ]
    return HarnessPlan(repo=repo, files=tuple(files))


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


def _agents_md() -> str:
    return """# AGENTS.md

This repo is configured for Codex.

## Commands

Document install, test, lint, and run commands here.

## Rules

- Keep changes small.
- Run relevant tests.
- Do not commit secrets.
- Do not deploy or merge without human approval.
- Update docs when behavior changes.
"""


def _workflow_md() -> str:
    return """# WORKFLOW

Default work item flow:

Backlog -> Ready -> Running -> Human Review -> Done

Failed or incomplete work goes to Rework.
Blocked work goes to Blocked.

Agents should make the smallest correct change, run relevant tests, and report proof of work.
"""


def _codex_config() -> str:
    return """model = \"gpt-5.4-mini\"
model_reasoning_effort = \"medium\"
sandbox_mode = \"workspace-write\"
approval_policy = \"on-request\"

[agents]
max_threads = 4
max_depth = 1

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
