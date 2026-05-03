# Run codex-fleet locally

This is the current local MVP runbook.

## 1. Clone and install

```bash
git clone https://github.com/RishiGitH/codex-fleet.git
cd codex-fleet
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## 2. Run local checks

```bash
make local-check
```

## 3. Initialize local config

```bash
python -m codex_fleet init-harness --repo .
```

This creates `.codex-fleet.yml` if missing. The default uses the memory tracker.

## 4. Run the fake workflow

```bash
python -m codex_fleet up --repo . --fake --once
```

Expected behavior:

- repo doctor prints readiness
- a memory work item is selected
- a git worktree is created under `~/.codex-fleet/workspaces`
- a fake marker file is written
- run state is stored in `.codex-fleet/runs.sqlite3`

## 5. Try the configured fake runner

```bash
python -m codex_fleet run-configured --repo . --fake
```

## 6. Try real Codex runner

Only after Codex CLI is installed and authenticated:

```bash
python -m codex_fleet run-configured --repo .
```

## 7. Connect Plane

Copy the example config:

```bash
cp examples/codex-fleet.plane.yml .codex-fleet.yml
```

Set environment variables:

```bash
export PLANE_BASE_URL="https://your-plane-host"
export PLANE_API_KEY="..."
export PLANE_WORKSPACE_SLUG="..."
export PLANE_PROJECT_ID="..."
```

Check the project:

```bash
python -m codex_fleet plane-check --repo .
```

Create missing workflow states if you approve:

```bash
python -m codex_fleet plane-bootstrap --repo .
```

Run against Plane with fake runner first:

```bash
python -m codex_fleet run-configured --repo . --fake
```

Then remove `--fake` when you are ready to let Codex work.

## Cleanup

Remove local run state:

```bash
rm -rf .codex-fleet
```

Remove generated worktrees manually if needed:

```bash
git worktree list
git worktree remove <path>
```
