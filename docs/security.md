# Security notes

codex-fleet runs coding agents against real repositories. Treat it as powerful local automation.

## Safe defaults

- No auto-merge.
- No deploy.
- No broad filesystem mounts.
- No secrets in prompts.
- Parent tasks move to Done automatically only in `full_auto` after required children and reviewers pass.
- Workspace-write is preferred over full access.

## Sensitive areas

- Plane API keys.
- codex-fleet local API token.
- GitHub tokens.
- Codex authentication state.
- Shell command execution.
- Docker volume mounts.
- Worktree path construction.

## Path safety

All workspace paths must resolve under the configured workspace root. Never accept a work item identifier as a raw filesystem path.

Local project paths must be validated before registration. The local API accepts folder paths as project inputs, but it must reject missing paths, non-directories, and filesystem root.

Project creation receives a parent folder and creates one child project folder inside it. Existing empty target folders may be reused; non-empty target folders are rejected unless the user chooses a different folder name.

## Local API

The customized Plane UI talks to codex-fleet through a loopback-only API.

- Bind to `127.0.0.1` by default.
- Require the generated local token for project and run actions.
- Store the token under `.codex-fleet/secrets`.
- First-run onboarding may place the token in the URL fragment. Browser fragments are not sent to the static Plane web server, but they can remain in local browser history and screenshots, so keep the preview loopback-only.
- Expose structured operations only.
- Do not expose an arbitrary shell command endpoint.
- Plane UI may request a run; codex-fleet must create worktrees and run Codex.
- Plane UI may request delivery task creation; codex-fleet must not push, merge, deploy, or create PRs by default.

## Local Plane frontend

`plane-frontend install` replaces only static web files inside the local Plane
web container and keeps a stock backup under `.codex-fleet/plane-selfhost/`.
It must not alter Plane backend services, auth state, database migrations, data
volumes, or Docker mounts. Restore the stock frontend with
`plane-frontend restore` when debugging upstream Plane behavior.

## Token handling

Environment variables should be referenced by name in docs and configs. Do not write actual secret values to repo files, logs, or Plane comments.

`plane-local-bootstrap` writes the local Plane API key only to
`.codex-fleet/secrets.env` with `0600` permissions and keeps `.codex-fleet.yml`
pointing at `$PLANE_API_KEY`. Do not print that key in command output or browser
UI.

## Future hardening

- Secret scan before PR creation.
- GitHub token scope checks.
- Docker mount policy checks.
- Optional devcontainer isolation.
- Audit log for state transitions.
