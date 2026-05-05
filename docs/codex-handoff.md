# Codex handoff

codex-fleet is a local-first control plane for Codex agent fleets.

## Vision

The product should feel like a local Jira or Linear for Codex agents. A user creates or imports work items, moves one to Ready, and codex-fleet dispatches a Codex agent to work on it in an isolated git worktree. The result comes back to Human Review with proof of work.

Plane is the local board. codex-fleet is the dispatcher. Codex is the worker. GitHub is optional review and PR infrastructure.

## Current state

The repo has a Python CLI, repo doctor, harness generator, memory tracker, Plane REST adapter, Plane state bootstrap, worktree manager, fake runner, Codex App Server runner boundary, SQLite run store, daemon loop, manual draft PR helper, Codex config, subagents, skills, and local docs.

Testing is local-only. There is no GitHub Actions CI.

## Important files

- `AGENTS.md`
- `WORKFLOW.md`
- `.codex/config.toml`
- `.codex/agents/`
- `.agents/skills/`
- `docs/architecture.md`
- `docs/run-the-app.md`
- `docs/local-testing.md`
- `docs/plane-setup.md`
- `docs/security.md`

## Next priorities

1. Run the local tests and fix failures.
2. Verify the fake local flow works with `make up`.
3. Verify the Node launcher works.
4. Test local Plane through `make plane-up`.
5. Validate Plane check and bootstrap commands.
6. Test a real Codex App Server run.
7. Add PR link comments back to Plane.
8. Improve the repo doctor apply flow.
9. Add a better local run status view.

## Safety rules

No auto-merge. No deploy. No hidden Plane mutation. No secrets in logs. Keep critical state transitions in deterministic code. Fake mode must pass before real Codex mode.
