# Security notes

codex-fleet runs coding agents against real repositories. Treat it as powerful local automation.

## Safe defaults

- No auto-merge.
- No deploy.
- No broad filesystem mounts.
- No secrets in prompts.
- Human Review before merge.
- Workspace-write is preferred over full access.

## Sensitive areas

- Plane API keys.
- GitHub tokens.
- Codex authentication state.
- Shell command execution.
- Docker volume mounts.
- Worktree path construction.

## Path safety

All workspace paths must resolve under the configured workspace root. Never accept a work item identifier as a raw filesystem path.

## Token handling

Environment variables should be referenced by name in docs and configs. Do not write actual secret values to repo files, logs, or Plane comments.

## Future hardening

- Secret scan before PR creation.
- GitHub token scope checks.
- Docker mount policy checks.
- Optional devcontainer isolation.
- Audit log for state transitions.
