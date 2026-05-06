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

## Internal smoke run without Plane or Codex

```bash
python -m codex_fleet run-configured --repo . --fake
```

This uses the in-memory tracker and fake runner as an internal harness, creates a git worktree, writes a fake marker file, and records run state in `.codex-fleet/runs.sqlite3`. It is not the product Kanban path.

## One-tick daemon smoke run

```bash
python -m codex_fleet daemon --repo . --fake --ticks 1
```

## Main local entrypoint

```bash
python -m codex_fleet up --repo . --fake --once
```

This starts or checks local Plane, bootstraps the configured Plane project states, runs the doctor, prints config, runs one scheduler tick, and exits.

## Harness generation for another repo

Preview:

```bash
python -m codex_fleet plan-harness --repo /path/to/repo
```

Apply missing files:

```bash
python -m codex_fleet apply-harness --repo /path/to/repo
```

## Real Codex CLI run

Use this only after Codex CLI is installed and authenticated:

```bash
python -m codex_fleet run-configured --repo .
```

The default real runner uses `codex exec` from `.codex-fleet.yml` and passes the work item prompt on stdin inside the isolated git worktree. To use the experimental App Server boundary instead, set:

```yaml
codex:
  runner: app-server
  command: codex app-server
```

## Plane checks

Install/start/check local Plane:

```bash
python -m codex_fleet plane-up --repo .
python -m codex_fleet plane-status --repo .
python -m codex_fleet plane-local-bootstrap --repo .
```

`plane-local-bootstrap` creates or reuses the local Plane user, workspace,
project, and API token and writes `.codex-fleet.yml`.

For an existing Plane project and API key:

```bash
PLANE_WORKSPACE_SLUG=<workspace-slug> PLANE_PROJECT_ID=<project-id> PLANE_API_KEY=<api-key> \
  python -m codex_fleet plane-configure --repo .
python -m codex_fleet plane-bootstrap --repo .
python -m codex_fleet plane-check --repo .
```

`plane-bootstrap` creates missing workflow states in your configured Plane project.
