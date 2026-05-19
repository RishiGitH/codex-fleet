# AGENTS.md

This folder is Codex Fleet's tracked Plane product UI source.

## Boundary

Plane is the browser board and review surface. codex-fleet is the local orchestration brain.

Plane UI may call the loopback Codex Fleet API only, for structured actions:

- local bootstrap
- project registration
- harness plan/apply
- run work item
- run status
- fleet dashboard/settings
- delivery task creation

Plane UI must not:

- shell out
- run Codex directly
- create git worktrees
- inspect arbitrary local files
- decide that a run succeeded

## Safe customization scope

Keep this fork shallow and rebaseable.

Allowed areas:

- `apps/web` branding
- app name, favicon, manifest, local onboarding, empty states
- add-local-folder flow
- work item run controls
- Codex run status/log panels
- Fleet Logs, Agents, Runs, Artifacts, and Codex Settings surfaces

Avoid unless there is a documented blocker:

- auth model rewrites outside explicit local mode
- core models
- migrations
- hosted/cloud behavior
- unrelated Plane surfaces

## codex-fleet API

Default local API:

```text
http://127.0.0.1:18790
```

Use a generated local token from `.codex-fleet/secrets/local_api_token` in the codex-fleet repo runtime. Never commit this token.

## Source pin

This is a normal tracked folder in the parent repo, not a submodule. Keep upstream Plane changes isolated and rebaseable.
