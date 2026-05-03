# AGENTS.md

This file gives Codex concise project guidance for working in `codex-fleet`.

## What this repo is

`codex-fleet` is a local control plane for running Codex agents from issue-board work. It uses Plane as the local board/tracker, implements a Symphony-style orchestration loop, creates isolated git worktrees, and runs Codex App Server sessions as task agents.

## Source of truth

- Architecture: `docs/architecture.md`
- Product plan: `docs/product-plan.md`
- Token policy: `docs/token-policy.md`
- Security notes: `docs/security.md`
- Execution plans: `docs/exec-plans/`
- Skills: `.agents/skills/`
- Codex project config/subagents: `.codex/`

## Commands

Install locally:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Run tests:

```bash
pytest
```

Run lint/type checks:

```bash
ruff check .
mypy src/codex_fleet
```

Run the repo doctor:

```bash
python -m codex_fleet doctor --repo .
```

Run token budget check:

```bash
python -m codex_fleet token-budget --repo .
```

## Development rules

- Keep changes small and focused.
- Prefer adapters over forks.
- Do not vendor Plane or Symphony into this repo without an explicit architecture decision.
- Keep critical state transitions in deterministic daemon code, not only in prompts.
- Default to safe local execution: no auto-merge, no deploy, no broad filesystem writes.
- Add or update tests for orchestrator, tracker, workspace, doctor, and runner changes.
- Update docs when changing architecture, workflow states, safety policy, or user-facing CLI.
- Keep this file concise. Put durable detail in `docs/` or focused skills.

## When to use subagents

Use project subagents for non-trivial work:

- `code_scout`: map files and tests before editing.
- `architect`: review state-machine or product architecture changes.
- `implementer`: make approved code changes.
- `harness_reviewer`: check that the repo remains agent-friendly.
- `security_reviewer`: check secrets, path safety, shell execution, Docker, and token handling.
- `token_reviewer`: check context/log/skill/doc bloat.

For small edits, do not over-orchestrate. Use scout → implementer → tests.

## Safety boundaries

- Never print or commit secrets.
- Never add broad `~` or `/` Docker mounts by default.
- Never auto-merge or deploy in default workflows.
- Validate workspace paths before shelling out.
- Treat Plane, GitHub, and Codex tokens as scoped credentials.
