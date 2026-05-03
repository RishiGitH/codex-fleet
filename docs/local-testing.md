# Local testing

codex-fleet does not run GitHub Actions CI right now. Testing is local-only.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Run checks

```bash
make local-check
```

Equivalent manual commands:

```bash
ruff check .
pytest
python -m codex_fleet doctor --repo .
python -m codex_fleet budget --repo .
```

## Smoke run without Plane or Codex

```bash
python -m codex_fleet run-configured --repo . --fake
```

This uses the in-memory tracker and fake runner, creates a git worktree, writes a fake marker file, and records run state in `.codex-fleet/runs.sqlite3`.

## One-tick daemon smoke run

```bash
python -m codex_fleet daemon --repo . --fake --ticks 1
```

## Real Codex App Server run

Use this only after Codex CLI is installed and authenticated:

```bash
python -m codex_fleet run-configured --repo .
```

The current runner uses the `codex app-server` command from `.codex-fleet.yml` or the default config.
