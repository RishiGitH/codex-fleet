# AI workflow

Use Codex like a careful engineer with good tools, not like a full-repo paste machine.

## Daily loop

1. Start with targeted search.

```bash
rg "symbol_or_error"
rg --files
fd partial-name
```

2. Read the smallest useful files.

Prefer source files, nearby tests, and docs listed in `AGENTS.md`. Avoid broad context packs until search stops being enough.

3. Make small changes.

Keep edits scoped to the work item. Prefer existing adapters, state-machine code, and local patterns over new abstractions.

4. Verify with focused commands.

Use the nearest tests first. Escalate to `make local-check` when shared behavior, runner logic, doctor/budget, context packing, local API, workspace, or orchestration changes.

5. Preserve evidence.

For noisy commands, use:

```bash
python -m codex_fleet capture --repo . --compress auto -- <command>
```

Raw output is saved first. Summaries and compression are convenience views only.

## Which skill to use

- `change-verification`: before reporting code changes complete.
- `repo-doctor`: when checking whether a repo is ready for Codex.
- `orchestrator-review`: for runner, tracker, workspace, daemon, or state-transition changes.
- `token-budget-review`: for docs, skills, prompts, logs, capture, and context packing.
- `impeccable`: only for frontend/product UI work.

Use subagents sparingly:

- Small fix: no subagent.
- Unknown area: one `code_scout`.
- Scoped implementation: main agent or one `implementer`.
- Security-sensitive path: `security_reviewer`.
- Prompt/log/context changes: `token_reviewer`.

## Context policy

Default order:

1. `rg` and direct file reads.
2. `pack-context --profile minimal`.
3. `pack-context --profile task --include ...`.
4. `pack-context --profile full` only for rare deep architecture work.

Large frontend skill assets stay opt-in. Impeccable is useful for UI craft, but its scripts and references should not dominate backend/orchestration context.

## Tool policy

- RTK: good for large logs after raw output is saved.
- Repomix: not needed by default; native context packs come first.
- Graphify: not needed for routine codex-fleet work; use only for architecture archaeology.
- Caveman: not needed by default; use only for long prose/handoffs if installed later.

## Good final reports

A useful Codex handoff says:

- what changed
- why it changed
- which files matter
- which commands passed
- what remains risky or unverified

Do not paste large raw logs into Plane comments, docs, or final summaries.
