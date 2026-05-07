# AGENTS.md

This file gives Codex concise project guidance for working in `codex-fleet`.

## What this repo is

`codex-fleet` is a local control plane for running Codex agents from issue-board work. It uses a branded local Plane fork as the board/tracker UI, exposes a loopback-only codex-fleet API for local UI actions, implements a Symphony-style orchestration loop, creates isolated git worktrees, and runs Codex App Server sessions as task agents.

## Source of truth

- Architecture: `docs/architecture.md`
- Product plan: `docs/product-plan.md`
- Product design/goal: `docs/product-design.md`
- Brand: `docs/brand.md`
- Plane fork: `docs/plane-fork.md`
- Local API: `docs/local-api.md`
- Agent orchestration: `docs/agent-orchestration.md`
- Harness engine: `docs/harness-engine.md`
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

Run context budget check:

```bash
python -m codex_fleet budget --repo .
```

Use `token_reviewer` when editing docs, skills, prompts, logging, or context packing. Keep raw logs as artifacts and summarize only the context needed by agents.

Run local smoke flow:

```bash
python -m codex_fleet up --repo . --fake --once
```

Start the local API used by the customized Plane UI:

```bash
python -m codex_fleet api --repo .
```

## Development rules

- Keep changes small and focused.
- Prefer adapters over forks.
- The explicit Plane decision is a shallow branded fork for local product UX. Keep it rebaseable and avoid unrelated Plane changes.
- Do not vendor Symphony into this repo.
- Plane UI must call codex-fleet API for agent actions; it must not shell out or run Codex directly.
- Keep critical state transitions in deterministic daemon code, not only in prompts.
- Default to safe local execution: no auto-merge, no deploy, no broad filesystem writes.
- Add or update tests for orchestrator, tracker, workspace, doctor, and runner changes.
- Update docs when changing architecture, workflow states, safety policy, or user-facing CLI.
- Keep this file concise. Put durable detail in `docs/` or focused skills.

## When to use subagents

Use project subagents only when the work is large enough to benefit from parallelism or independent review. For normal edits, use one local pass.

Default cost policy:

- Small fix: no subagent.
- Unknown code area: one `code_scout`.
- Scoped implementation: main agent or one `implementer`.
- UI/design review: use mini-model reviewers where possible.
- Architecture/security/token reviewers: opt in only when the risk matches their specialty.

Available subagents:

- `code_scout`: map files and tests before editing.
- `architect`: review state-machine or product architecture changes.
- `implementer`: make approved code changes.
- `harness_reviewer`: check that the repo remains agent-friendly.
- `security_reviewer`: check secrets, path safety, shell execution, Docker, and token handling.
- `token_reviewer`: check context/log/skill/doc bloat.

Do not spawn architect, security, and token reviewers automatically. Prefer targeted context packs, changed files, and relevant tests over full-repo context.

## Safety boundaries

- Never print or commit secrets.
- Never add broad `~` or `/` Docker mounts by default.
- Never auto-merge or deploy in default workflows.
- Validate workspace paths before shelling out.
- Treat Plane, GitHub, and Codex tokens as scoped credentials.
