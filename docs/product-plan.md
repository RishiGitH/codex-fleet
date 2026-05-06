# Product plan

codex-fleet aims to feel like a local Jira or Linear for Codex agents.

## User promise

A user can point codex-fleet at a repo, approve suggested harness additions, create or import tasks, move a task to Ready, and watch an isolated Codex agent produce a branch or PR.

The detailed product design and implementation handoff live in `docs/product-design.md`.

## Product path

1. Repo harness and CLI foundation.
2. Local project registry for arbitrary folders.
3. Loopback codex-fleet API for Plane UI actions.
4. Branded Plane fork with no-login local onboarding.
5. Plane tracker adapter and workflow bootstrap.
6. Worktree manager.
7. Fake runner for deterministic end-to-end tests.
8. Codex App Server runner.
9. GitHub PR flow.
10. Repo doctor and harness apply mode.

## Design priorities

- Plug-and-play first run.
- Safe defaults.
- Clear docs and contribution path.
- Small focused tasks.
- Human review before merge.
- Shallow Plane fork for local product UX.

## Non-goals for early versions

- Full hosted SaaS.
- Hosted Plane customization.
- Auto-deploy.
- Auto-merge.
- Multi-tenant enterprise auth.
