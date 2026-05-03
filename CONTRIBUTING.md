# Contributing to codex-fleet

Thanks for helping build codex-fleet.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Before opening a PR

Run:

```bash
ruff check .
pytest
python -m codex_fleet doctor --repo .
python -m codex_fleet budget --repo .
```

## Contribution style

- Keep PRs focused.
- Add tests for behavior changes.
- Update docs when changing user-facing behavior or architecture.
- Keep `AGENTS.md` short; put detailed knowledge in `docs/` or focused skills.
- Do not vendor large upstream projects unless a design document explicitly approves it.

## Architecture decisions

For major choices, add a short file in `docs/decisions/`.

Use this format:

```markdown
# Decision: short title

## Context

## Decision

## Consequences
```
