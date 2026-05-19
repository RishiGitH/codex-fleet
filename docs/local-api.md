# codex-fleet local API

The customized Plane UI talks to codex-fleet through a local API. This API is the only browser-facing surface that can request agent work.

## Runtime

```bash
codex-fleet api --repo .
```

`codex-fleet up --repo .` starts this API automatically for long-running
local Plane daemon runs so the branded Plane UI controls can call run/status
endpoints. `--once` exits after one scheduler tick and does not keep the API
open. Use `codex-fleet api --repo .` when you want only the API. Fake runner
payload fields remain available for internal smoke tests, but the normal UI path
does not expose fake mode.

Defaults:

- host: `127.0.0.1`
- port: `8790`
- project registry: `.codex-fleet/projects.sqlite3`
- local token: `.codex-fleet/secrets/local_api_token`
- no-login local work items: `.codex-fleet/local-work-items.sqlite3`

The API refuses to bind outside loopback unless `--unsafe-allow-remote` is explicitly passed.

## Auth

Send the local token with either header:

```text
Authorization: Bearer <token>
X-Codex-Fleet-Token: <token>
```

`GET /health`, `GET /api/health`, and `GET /api/status` are intentionally unauthenticated for local readiness checks. Project and run mutation endpoints require the token.

## Current endpoints

```text
GET  /api/status
GET  /health
GET  /api/health
GET  /api/plane/login
POST /api/onboarding/local-bootstrap
GET  /api/projects
GET  /api/projects/:id
GET  /api/projects/:id/agent-analytics
GET  /api/projects/:id/control-plane-status
POST /api/projects
POST /api/projects/:id/harness/plan
POST /api/projects/:id/harness/apply
GET  /api/runs
POST /api/runs
GET  /api/runs/:id
GET  /api/runs/:id/events
GET  /api/runs/:id/artifacts/:artifact_id
POST /api/runs/:id/retry
POST /api/runs/:id/cancel
GET  /api/work-items/:plane_id/children
GET  /api/work-items/:plane_id/parent
POST /api/work-items/:plane_id/answer-input
POST /api/work-items/:plane_id/settings
POST /api/work-items/:plane_id/plan
POST /api/work-items/:plane_id/retry
POST /api/work-items/:plane_id/cancel
POST /api/runs/next-ready
GET  /api/events
GET  /api/work-items/ready
POST /api/work-items
POST /api/work-items/next/run
POST /api/work-items/:plane_id/run
GET  /api/work-items/:plane_id/run-status
```

`POST /api/onboarding/local-bootstrap` accepts an optional `path` and `name`, registers a local project, and reports harness status for onboarding.

`GET /api/plane/login` is the local no-login Plane session bridge. It accepts
the loopback local API token and a loopback Plane redirect URL, creates a normal
Plane `session-id` cookie for the local single-user account, and redirects to
the requested local Plane board. The endpoint must remain loopback-only; it is
not an auth bypass for remote or hosted Plane.

```json
{
  "path": "/absolute/or/relative/project/path",
  "name": "Optional display name"
}
```

`POST /api/projects` accepts:

```json
{
  "path": "/absolute/or/relative/project/path",
  "name": "Optional display name",
  "apply_harness": true
}
```

The path must exist and must be a directory. The API does not expose arbitrary
shell execution. The response includes the local project, Plane mapping status,
harness scan, and files written when `apply_harness` is true. This is the
browser path used by the branded Plane project setup flow.

When the codex-fleet runtime repo is already configured for Plane, project
registration also tries to create or detect a matching Plane project in the same
workspace. It stores the Plane workspace/project mapping in
`.codex-fleet/projects.sqlite3` and writes the target folder's
`.codex-fleet.yml` tracker section so runs for that folder read from that Plane
project. The local Plane API key is copied into the target folder's
`.codex-fleet/secrets.env` with `0600` permissions and is never returned in API
responses. If Plane is not configured or unavailable, the folder is still
registered and the response includes a `plane.status` of `skipped` or `error`.

Run, event, and ready-item endpoints accept an optional `project_id` or
`plane_project_id`. When provided, codex-fleet resolves the registered folder
and uses that folder's own config, run store, local fallback work items, and
worktree root. `plane_project_id` is for embedded Plane work-item controls,
which know Plane's project UUID but not codex-fleet's local numeric registry id.
Without either value, endpoints use the repo passed to `codex-fleet api --repo`.

Examples:

```text
GET  /api/work-items/ready?project_id=2
GET  /api/work-items/ready?plane_project_id=2b93021b-96ab-4d25-8608-a775445d6f15
GET  /api/runs?project_id=2
GET  /api/events?project_id=2
POST /api/runs/next-ready
```

```json
{
  "project_id": 2
}
```

```json
{
  "plane_project_id": "2b93021b-96ab-4d25-8608-a775445d6f15"
}
```

`POST /api/projects/:id/harness/plan` returns the harness files codex-fleet expects for that local project and which files are missing. The `scan` object also reports git root, dirty state, detected stack, package manager, common commands, and warnings:

```json
{
  "harness": {
    "status": "needs_setup",
    "scan": {
      "git_root": "/path/to/project",
      "dirty": false,
      "stack": "node",
      "package_manager": "pnpm",
      "commands": {
        "install": "pnpm install",
        "test": "pnpm test",
        "lint": "pnpm lint",
        "typecheck": "pnpm typecheck",
        "build": "pnpm build",
        "dev": "pnpm dev"
      },
      "warnings": []
    }
  }
}
```

Status values are `blocked`, `needs_setup`, `warnings`, and `ready`. A non-git folder is `blocked`.

`POST /api/projects/:id/harness/apply` writes missing harness files. It accepts:

```json
{
  "overwrite": false
}
```

codex-fleet performs the filesystem writes; Plane UI only requests the structured action.

`GET /api/work-items/ready` returns candidate work items from the configured tracker. This is the dashboard read path for showing what codex-fleet can claim.

When the repo is still using the memory tracker, the local API stores those fallback work items in `.codex-fleet/local-work-items.sqlite3` so state persists across browser refreshes and API restarts. This is a no-login bootstrap fallback for the branded Plane fork, not the product board replacement. Once a repo is configured for Plane, humans should create and move work items in Plane.

`POST /api/work-items` is only accepted for the memory fallback and creates a Ready local item:

```json
{
  "title": "Build pricing page",
  "description": "Use the existing app shell."
}
```

For Plane-backed projects this endpoint returns an error; the work item source of truth is Plane.

`GET /api/runs/:id` and `GET /api/work-items/:plane_id/run-status` return the stored run plus append-only `events` and `artifacts`. Runs also include agent/model/settings metadata when available: `agent_role`, `agent_name`, `agent_avatar`, `runner_name`, `model`, `settings`, and `token_usage`. Events record orchestration boundaries such as claim, workspace preparation, runner start/finish, completion, input requests, child task creation, parent blocking, retry, and cancellation. Artifacts include local path, kind, size, SHA-256 when available, and redaction class.

`GET /api/runs/:id/events` returns only the run's ordered event timeline.

`GET /api/runs/:id/artifacts/:artifact_id` streams a local artifact only when it is inside the project repo or recorded worktree path. It rejects path traversal and outside-root paths.

`GET /api/projects/:id/agent-analytics` returns local run analytics grouped by agent role, including success/failure/active/cancelled counts, token totals when available, and recent events. This endpoint is codex-fleet's agent visibility surface; it does not depend on Plane's built-in analytics.

`GET /api/projects/:id/control-plane-status` returns local readiness for API, daemon store, Plane connectivity, runner command, and local auth.

`GET /api/work-items/:plane_id/children` returns codex-fleet task metadata for child work items created from the parent.

`GET /api/work-items/:plane_id/parent` returns codex-fleet task metadata linking the item back to the parent that created it, when present.

`POST /api/work-items/:plane_id/answer-input` accepts `{ "answer": "..." }`, posts the answer as a comment, and moves the item back to Ready.

`POST /api/work-items/:plane_id/settings` persists per-task codex-fleet settings in local metadata so the task does not need to rely only on hidden description HTML.

`POST /api/work-items/:plane_id/plan` moves the item to Ready, stores Full Agent planner settings, and dispatches that item as a planner run.

`POST /api/work-items/:plane_id/retry` releases the latest claim when present, records a retry event, comments in Plane, and moves the item back to Ready. If the latest run was still active, it is marked cancelled with a retry note.

`POST /api/work-items/:plane_id/cancel` records `cancel_requested`, marks the latest run cancelled when present, releases the latest claim, comments in Plane, and moves the item to Cancelled. `POST /api/runs/:id/cancel` performs the same operation from a known run id.

`GET /api/events?limit=100` returns recent run events across the local repo-scoped run store. The optional limit is clamped to a safe range.

`POST /api/runs` is the product run request endpoint. With a work item id it runs that specific item:

```json
{
  "project_id": 2,
  "plane_work_item_id": "plane-or-local-item-id"
}
```

If no item id is provided, `POST /api/runs` behaves like `POST /api/runs/next-ready` and runs the next active item.

`POST /api/runs/next-ready` accepts:

```json
{
  "project_id": 2
}
```

It is the preferred product alias for running the next Ready item. The older dashboard route below remains for compatibility.

`POST /api/work-items/next/run` accepts:

```json
{
  "project_id": 2
}
```

The endpoint runs the normal orchestrator against the next active work item. It is the browser-safe equivalent of one daemon tick: codex-fleet still owns duplicate claims, state transitions, worktree creation, runner execution, comments, and stored run records.

`POST /api/work-items/:plane_id/run` accepts:

```json
{
  "project_id": 2
}
```

The endpoint loads the configured tracker, fetches the exact work item, verifies it is in an active state, and then runs the existing orchestrator path. Duplicate claims, worktree creation, comments, and final state transitions stay in codex-fleet. Internal smoke tests may pass `fake` and `fake_succeed`; the normal Plane UI path does not.

Future mutation endpoints must preserve the same safety model: loopback binding, local token, structured intents only, path validation, and no direct shell command endpoint.
