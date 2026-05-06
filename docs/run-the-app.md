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

This installs the `codex-fleet` command. `python -m codex_fleet` remains available as a fallback.

## 2. Run local checks

```bash
make local-check
```

## 3. Start the Plane-first app

```bash
codex-fleet up --repo .
```

If `.codex-fleet.yml` is missing, this starts official self-hosted Plane
locally, creates or reuses a local single-user Plane workspace/project/API token
through Plane's own Docker API container, writes `.codex-fleet.yml`, installs
the branded Plane frontend, bootstraps states/views, and runs the codex-fleet daemon
path. If Docker or Plane cannot start, it falls back to `/codex-fleet/onboarding`
in the built branded Plane fork.

If `.codex-fleet.yml` already points at local self-hosted Plane, `up` starts
Plane if needed, installs the branded codex-fleet web frontend into the local
Plane web container, bootstraps states/views, starts the loopback API for Plane
UI run controls during long-running runs, and runs the daemon. Use `--stock-plane`
only when you want to debug upstream Plane without the codex-fleet frontend.

## 4. Internal fake workflow smoke test

```bash
make demo
codex-fleet run-configured --repo . --fake
```

Expected behavior:

- a memory work item is selected
- a git worktree is created under `.codex-fleet/workspaces`
- a fake marker file is written
- run state is stored in `.codex-fleet/runs.sqlite3`

## 5. Real Codex runner

The normal product path uses the local Codex CLI. Check it before starting long-running work:

```bash
codex-fleet doctor --repo .
codex-fleet up --repo .
```

By default, real runs use `codex exec` inside the isolated worktree. Set `codex.runner: app-server` and `codex.command: codex app-server` only if you want the experimental App Server boundary.

`doctor` performs a cheap real-runner preflight without starting a model run. It checks that the configured `codex exec` binary is installed, that the exec CLI still exposes the flags codex-fleet uses, and that `codex login status` succeeds. If `doctor` reports `missing_codex_cli`, `codex_exec_contract_changed`, or `codex_cli_not_authenticated`, fix local Codex auth/installation before using Plane run controls. Use `--fake` only for internal smoke testing.

The real Codex CLI runner repeats the same safety preflight immediately before launching `codex exec`. If auth or CLI compatibility is not ready, the runner returns a normal failure result instead of starting Codex, so the orchestrator can comment and move the work item to Rework.

## 6. Configure Plane

Start or check local Plane:

```bash
codex-fleet plane-up --repo .
codex-fleet plane-status --repo .
codex-fleet plane-local-bootstrap --repo .
codex-fleet plane-frontend install --repo .
codex-fleet plane-frontend status --repo .
```

The automatic bootstrap writes `.codex-fleet.yml` and stores the Plane API key
under `.codex-fleet/secrets.env`.

For an existing Plane project and API key:

```bash
PLANE_WORKSPACE_SLUG="..." PLANE_PROJECT_ID="..." PLANE_API_KEY="..." \
  codex-fleet plane-configure --repo .
```

Check the project:

```bash
codex-fleet plane-check --repo .
```

Create missing workflow states if you approve:

```bash
codex-fleet plane-bootstrap --repo .
```

The normal Plane path uses the real Codex CLI runner. Use `--fake` only for
internal smoke testing when Codex credentials are intentionally unavailable.

Restore the original Plane web static files if you need to inspect upstream
Plane without the codex-fleet fork:

```bash
codex-fleet plane-frontend restore --repo .
```

## Cleanup

Stop local preview/API services and local Plane Docker runtime when present:

```bash
codex-fleet down --repo .
```

Remove local run state:

```bash
rm -rf .codex-fleet
```

Remove generated worktrees manually if needed:

```bash
git worktree list
git worktree remove <path>
```
