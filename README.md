# codex-fleet

**Local control plane for Codex agent fleets.**

codex-fleet is a local-first project for running Codex work from a tracker board. It combines a Plane-compatible tracker adapter, a Symphony-style orchestration loop, isolated git worktrees, a Codex App Server runner boundary, and a repo-hardening doctor.

The goal is simple: one command should give a developer a local agent workflow for a repo.

## One-command demo

```bash
git clone https://github.com/RishiGitH/codex-fleet.git
cd codex-fleet
make up
```

This creates a local venv, installs codex-fleet, runs the repo doctor, and executes one fake agent work item through the scheduler. It requires no Plane account and no Codex credentials.

## Local Plane setup

```bash
make plane-up
```

This wraps Plane's official self-host installer instead of asking users to manually bring Plane Cloud credentials. After Plane is installed and running, create a local Plane account, project, and API key.

Then configure codex-fleet:

```bash
cp examples/codex-fleet.plane.yml .codex-fleet.yml
export PLANE_BASE_URL="http://localhost:3000"
export PLANE_API_KEY="your-local-plane-key"
export PLANE_WORKSPACE_SLUG="your-workspace"
export PLANE_PROJECT_ID="your-project-id"
python -m codex_fleet plane-check --repo .
python -m codex_fleet plane-bootstrap --repo .
python -m codex_fleet run-configured --repo . --fake
```

Remove `--fake` only after Codex CLI is installed and authenticated.

## Current status

The repo now has a runnable local MVP foundation:

- Python package and CLI.
- Local repo doctor.
- Harness planner and writer.
- Memory tracker for smoke tests.
- Plane REST adapter with state-name resolution.
- Plane readiness and workflow state bootstrap helpers.
- Worktree manager.
- Fake runner for deterministic local tests.
- Codex App Server JSON-RPC runner boundary.
- SQLite run store.
- Polling daemon loop.
- Manual draft PR helper boundary.
- Codex project config, subagents, and skills.
- Local tests and local check scripts.
- No GitHub Actions CI. Run tests locally.

## Useful commands

```bash
make local-check
make full-local-check
make docker-up
python -m codex_fleet up --repo . --fake --once
python -m codex_fleet plan-harness --repo /path/to/repo
python -m codex_fleet apply-harness --repo /path/to/repo
```

## npx-style launcher

This repo includes a Node launcher for local development:

```bash
node scripts/codex-fleet-npx.js up --fake --once
```

After publishing to npm, the intended command is:

```bash
npx codex-fleet up --fake --once
```

## Why not fork Plane or vendor Symphony?

Plane is used as an external local service through its API. We do not fork or vendor Plane in this repo for the MVP.

OpenAI Symphony is used as an architecture reference: poll tracker, isolate workspaces, run a coding agent, track state, and return work for human review. codex-fleet implements a lean Python version.

## Roadmap

- Fully automate the Plane local project/key bootstrap after Plane exposes a stable non-interactive path.
- Live Plane integration test on your machine.
- Real Codex App Server run test on your machine.
- PR link comments back to Plane.
- Stronger repo doctor apply mode.
- Multi-repo and crash recovery.

## License

Apache-2.0. See [LICENSE](LICENSE).
