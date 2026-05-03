# codex-fleet

**Local control plane for Codex agent fleets.**

codex-fleet is a local-first project for running Codex work from a tracker board. It combines a Plane-compatible tracker adapter, a Symphony-style orchestration loop, isolated git worktrees, a Codex App Server runner boundary, and a repo-hardening doctor.

## Current status

The repo now has a runnable local MVP foundation:

- Python package and CLI.
- Local repo doctor.
- Harness planner and writer.
- Memory tracker for smoke tests.
- Plane REST adapter with state-name resolution.
- Worktree manager.
- Fake runner for deterministic local tests.
- Codex App Server JSON-RPC runner boundary.
- SQLite run store.
- Polling daemon loop.
- Manual draft PR helper boundary.
- Codex project config, subagents, and skills.
- Local tests and local check scripts.

There is no GitHub Actions CI. Run tests locally.

## Quick start

```bash
git clone https://github.com/RishiGitH/codex-fleet.git
cd codex-fleet
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
make local-check
```

Try the local fake flow:

```bash
python -m codex_fleet up --repo . --fake --once
```

More instructions:

- `docs/local-testing.md`
- `docs/plane-setup.md`
- `docs/architecture.md`
- `docs/product-plan.md`

## Why not fork Plane or vendor Symphony?

Plane is used as an external service through its API. We do not fork or vendor Plane in this repo for the MVP.

OpenAI Symphony is used as an architecture reference: poll tracker, isolate workspaces, run a coding agent, track state, and return work for human review. codex-fleet implements a lean Python version.

## Roadmap

- Plane bootstrap helper.
- Live Plane integration test on your machine.
- Real Codex App Server run test on your machine.
- PR link comments back to Plane.
- Stronger repo doctor apply mode.
- Multi-repo and crash recovery.

## License

Apache-2.0. See [LICENSE](LICENSE).
