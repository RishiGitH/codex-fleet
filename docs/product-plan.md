# Product plan

codex-fleet aims to feel like a local Jira or Linear for Codex agents.

## User promise

A user can point codex-fleet at a repo, approve suggested harness additions, create or import tasks, move a task to Ready, and watch an isolated Codex agent produce a branch or PR.

## MVP path

1. Repo harness and CLI foundation.
2. Plane bootstrap or connection.
3. Plane tracker adapter.
4. Worktree manager.
5. Fake runner for deterministic end-to-end tests.
6. Codex App Server runner.
7. GitHub PR flow.
8. Repo doctor apply mode.

## Design priorities

- Plug-and-play first run.
- Safe defaults.
- Clear docs and contribution path.
- Small focused tasks.
- Human review before merge.
- No deep forks unless unavoidable.

## Non-goals for early versions

- Full hosted SaaS.
- Custom Plane fork.
- Auto-deploy.
- Auto-merge.
- Multi-tenant enterprise auth.
