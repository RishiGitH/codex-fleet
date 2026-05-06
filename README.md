# codex-fleet

**Local control plane for Codex agent fleets.**

codex-fleet is a local-first project for running Codex work from a tracker board. It combines a branded local Plane experience, a loopback codex-fleet API, a Symphony-style orchestration loop, isolated git worktrees, direct Codex CLI execution, a preserved Codex App Server runner boundary, and a repo-hardening doctor.

The goal is simple: one command should give a developer a local agent workflow for a repo.

## One-command demo

Use the repo directly:

```bash
git clone https://github.com/RishiGitH/codex-fleet.git
cd codex-fleet
make up
```

Or use the package-style launcher from any project directory:

```bash
npx github:RishiGitH/codex-fleet up
```

The installed command is `codex-fleet`; no shell alias is required. The Node launcher creates a project-local tool venv under `.codex-fleet/tooling/` and runs codex-fleet with the current directory as the target repo. The Python fallback is `python -m codex_fleet`.

The first run tries to start official self-hosted Plane locally when the repo
has no `.codex-fleet.yml` yet. codex-fleet then creates or reuses a local
single-user Plane workspace/project/API token through Plane's own Docker API
container, writes `.codex-fleet.yml`, installs the branded Plane frontend, and
runs the codex-fleet daemon. If Docker or Plane cannot start, it falls back to
the branded fork onboarding preview. The local Plane path requires no Plane
Cloud, hosted Plane, GitHub token, email, or manually copied Plane API key.
Real Codex runs require the local `codex` CLI to be installed and logged in.

If the branded Plane web build is missing, codex-fleet clones the pinned Plane source, applies `patches/plane-codex-fleet.patch`, and builds the local web client. That preparation needs `git`, `pnpm`, and network access the first time.

When `.codex-fleet.yml` is already configured for local self-hosted Plane,
`codex-fleet up` starts Plane if needed and installs the branded
codex-fleet frontend into the running local Plane web container. For long-running
runs it also starts the loopback codex-fleet API so the Plane UI run controls
can dispatch/status runs without a separate command. The command keeps a stock
static-file backup under `.codex-fleet/plane-selfhost/` so maintainers can
restore the original Plane web UI with `codex-fleet plane-frontend restore
--repo .`. Use `--stock-plane` only when debugging upstream Plane without
codex-fleet UI customizations.

## Local product architecture

The target local runtime has three services:

- `codex-fleet-api`: loopback-only API for onboarding, projects, harness actions, runs, and status.
- `plane-api`: local Plane API and data.
- `plane-web`: branded codex-fleet Plane UI fork.

Plane web requests structured actions from codex-fleet. codex-fleet creates worktrees, runs Codex, writes comments, and moves work items to Human Review or Rework.

The detailed product design, fork boundary, runner strategy, harness flow, and completion audit checklist live in [`docs/product-design.md`](docs/product-design.md).

## Local Plane setup

```bash
make plane-up
```

This wraps Plane's official self-host installer instead of asking users to manually bring Plane Cloud credentials. After Plane is installed and running, configure codex-fleet automatically:

```bash
codex-fleet plane-local-bootstrap --repo .
codex-fleet plane-bootstrap --repo .
codex-fleet up --repo .
```

The manual path remains available if you want to point at an existing Plane workspace, project, and API key:

```bash
PLANE_WORKSPACE_SLUG="your-workspace" PLANE_PROJECT_ID="your-project-id" PLANE_API_KEY="your-local-plane-key" \
  codex-fleet plane-configure --repo .
codex-fleet plane-bootstrap --repo .
codex-fleet up --repo .
```

For an internal no-Codex smoke run, use `make demo` or pass `--fake` explicitly.

## Current status

The repo now has a runnable local MVP foundation:

- Python package and CLI.
- Local repo doctor.
- Harness planner and writer.
- Memory tracker for smoke tests.
- Plane REST adapter with state-name resolution.
- Plane readiness and workflow state bootstrap helpers.
- Worktree manager.
- Internal fake runner for deterministic smoke tests.
- Direct Codex CLI runner, plus Codex App Server JSON-RPC runner boundary when configured.
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
make down
make docker-up
codex-fleet up --repo .
codex-fleet down --repo .
make demo
codex-fleet logs --repo .
codex-fleet plane-frontend install --repo .
codex-fleet plane-frontend status --repo .
codex-fleet plane-frontend restore --repo .
codex-fleet plane-fork-preview --repo .
codex-fleet plane-onboarding-url --repo . --path /path/to/project
codex-fleet api --repo .
codex-fleet project add /path/to/project
codex-fleet project list
codex-fleet plan-harness --repo /path/to/repo
codex-fleet apply-harness --repo /path/to/repo
```

## npx-style launcher

This repo includes a Node launcher for local development:

```bash
node scripts/codex-fleet-npx.js up
```

The package `bin` exposes:

```bash
npx codex-fleet up
```

## Plane and Symphony

The product UI uses a small branded Plane fork. Keep it shallow and rebaseable: branding, local onboarding, add-project flow, run controls, and run status panels.

Until a public Plane fork URL exists, the local Plane customization is tracked as `patches/plane-codex-fleet.patch`; `codex-fleet plane-source` can apply it to a pinned upstream Plane checkout. The default pin is recorded in `src/codex_fleet/resources/plane-source.lock.yml`, and `codex-fleet plane-source --status` prints the lock metadata beside the local checkout manifest.

OpenAI Symphony is used as an architecture reference: poll tracker, isolate workspaces, run a coding agent, track state, and return work for human review. codex-fleet implements a lean Python version.

## Roadmap

- Pin the Plane fork/submodule once the public fork URL is stable.
- Add stronger Plane fork build/browser verification gates.
- Prove the real Codex CLI runner end to end on a tiny local task.
- Add stale claim recovery and restart reconciliation.
- Expand harness scan/apply into a preview-first setup flow.
- Add PR links and optional review-agent pipelines after the local flow is solid.

## License

Apache-2.0. See [LICENSE](LICENSE).
