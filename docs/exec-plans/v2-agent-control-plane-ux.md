# Plan: V2 Agent Control Plane UX

## Goal

Make codex-fleet feel like a local team of visible Codex employees working inside Plane:

- A human creates or moves work to Ready.
- A lead agent can plan work, ask for missing input, or split work into child Plane tasks.
- Worker/reviewer agents execute child tasks in isolated worktrees.
- Plane shows who did what, what model/settings were used, what happened in the run, and what remains blocked.
- The daemon, not prompt text, owns deterministic state transitions.

## Non-goals

- No hosted SaaS.
- No deep Plane backend fork.
- No auto-merge or deploy.
- No arbitrary shell endpoint from Plane.
- No raw large Codex logs pasted into Plane comments.

## UX Principles

- Plane is the source of truth for human-visible work.
- Local SQLite is the source of truth for run evidence.
- Agent-created Plane tasks are the audit trail, not hidden subagent chatter.
- Human Review and Done remain separate: agents can finish a run, but humans accept.
- Defaults must work for unattended local automation without CLI approval prompts.

## User-Facing Modes

### Manual

Use when the user wants direct control.

- Agent completes only the selected task.
- Follow-up tasks are listed in a comment.
- No child tasks are automatically created.
- If the agent needs input, the task moves to Needs Input.

### Assisted

Use as the default.

- Agent completes the selected task.
- Follow-up tasks are created in Backlog with `agent-proposed`.
- Human reviews, edits, and moves them to Ready.
- This is safest for normal users because agents cannot recursively create runnable work without review.

### Full Agent

Use when the user explicitly wants autonomous orchestration.

- A lead agent can create child Plane tasks in Ready.
- Child tasks carry role labels such as `agent-worker`, `agent-reviewer`, or `agent-code_scout`.
- Parent moves to Planning while children run.
- Parent auto-completes to Human Review only when all children are terminal and no child failed.
- Depth and count limits prevent runaway task creation.

## Approval UX

The UI must not expose raw Codex CLI words without explanation.

Display these labels:

- `Auto local edits`: stores `approval_policy=never`.
- `Ask before risky actions`: stores `approval_policy=on-request`.
- `Untrusted sandbox`: stores `approval_policy=untrusted`.

Default:

- Project onboarding default is `Auto local edits`.
- The default is acceptable because runs happen in isolated git worktrees.
- The UI must still show that sandbox is `workspace-write` and no merge/deploy happens automatically.

## Plane States

Required states:

- Backlog
- Planning
- Ready
- Running
- Needs Input
- Human Review
- Rework
- Done
- Blocked
- Cancelled

Dispatch rule:

- Only Ready is dispatchable.

Terminal states:

- Human Review
- Done
- Blocked
- Cancelled
- Rework

Parent aggregation:

- If all children are Human Review or Done, parent becomes Human Review.
- If any child is Rework, Blocked, Cancelled, or Needs Input, parent stays Planning and gets a concise blocker comment.
- Parent never becomes Done automatically.

## Agent Identity

Every run payload must expose:

- `agent_role`
- `agent_name`
- `agent_avatar`
- `model`
- `reasoning_effort`
- `approval_policy`
- `sandbox_mode`
- `automation_mode`
- `total_tokens` when reliably available

Default identity mapping:

- `agent-lead` -> `Lead`
- `agent-code_scout` -> `Scout`
- `agent-worker` -> `Worker`
- `agent-reviewer` -> `Reviewer`
- no label -> `Implementer`

Plane cards and panels must show this identity near the run status so the user can answer: "Which agent did this?"

## Runner Protocol

Do not rely on a huge prompt or a giant skill every time.

The runner prompt should include a short codex-fleet protocol:

- Report concise summary and verification.
- Do not call Plane directly.
- Emit `codex-fleet-needs-input` for blocking questions.
- Emit `codex-fleet-proposed-tasks` for follow-up work.
- Keep raw logs out of Plane comments.

Use a codex-fleet skill only when the task is specifically orchestration-heavy.
For ordinary implementation tasks, the small protocol is cheaper and less error-prone.

## Required Backend Changes

### Retry Endpoint

Add:

```text
POST /api/work-items/:id/retry
```

Behavior:

- Resolve project/repo from query params.
- Fetch item from tracker.
- If a claim is active, mark claim `retry_requested`.
- If latest run is active, mark run `cancelled` or `stalled` with a retry note.
- Comment: `codex-fleet queued this item for retry.`
- Move item to Ready.
- Return latest run and item id.

Tests:

- Retry a Rework item moves it to Ready.
- Retry releases active claim.
- Retry rejects unknown item.

### Cancel Endpoint

Add:

```text
POST /api/work-items/:id/cancel
```

Behavior:

- Resolve project/repo from query params.
- Fetch item from tracker.
- If a claim is active, mark claim `cancelled`.
- If latest run is active, mark run `cancelled`.
- Comment: `codex-fleet cancelled this run by operator request.`
- Move item to Cancelled.
- Return latest run and item id.

Tests:

- Cancel Running moves it to Cancelled.
- Cancel active claim records status `cancelled`.
- Cancel unknown item returns 404.

### Parent And Children Endpoints

Keep:

```text
GET /api/work-items/:id/children
GET /api/work-items/:id/parent
POST /api/work-items/:id/answer-input
```

Add tests that prove:

- A child exposes parent metadata.
- A parent exposes all child metadata.
- Answering input comments and moves the task to Ready.

### Run Events Endpoint

Add:

```text
GET /api/runs/:id/events
```

Behavior:

- Return events ordered oldest to newest.
- Keep `/api/runs/:id` as the detail endpoint including events/artifacts.

### Agent Analytics Endpoint

Add:

```text
GET /api/projects/:id/agent-analytics
```

Return:

- runs by agent role
- success/failure counts
- active runs
- total tokens when available
- recent events

This can be local-only and approximate in V2. It should not require Plane analytics.

## Required Daemon Changes

### Parent Auto-Completion

Add reconciliation before dispatch in `FleetDaemon.tick()` and `tick_many()`.

Algorithm:

1. Read task metadata rows with `parent_item_id`.
2. Group child metadata by parent.
3. Fetch parent and child Plane items by id.
4. For each parent in Planning:
   - If no children exist, do nothing.
   - If any child is Ready or Running, do nothing.
   - If any child is Needs Input, Rework, Blocked, or Cancelled, keep parent Planning and add one concise blocker comment if not already recorded.
   - If all children are Human Review or Done, comment summary and move parent to Human Review.
5. Record `parent_reconciled` events in the parent run if known.

Tests:

- Parent with two Human Review children moves to Human Review.
- Parent with one Running child stays Planning.
- Parent with one Rework child stays Planning and receives blocker comment.
- Reconciliation is idempotent.

## Required Plane UI Changes

### Onboarding Form

Replace raw controls:

- `Approval` -> `Local edit approval`
- `on-request` -> `Ask before risky actions`
- `never` -> `Auto local edits`
- `untrusted` -> `Untrusted sandbox`
- `Follow-up tasks` -> remove from onboarding
- `Automation mode` -> show Manual, Assisted, Full Agent

Default values:

- `automation_mode=assisted`
- `approval_policy=never`
- `sandbox_mode=workspace-write`
- `max_task_depth=2`
- `max_child_tasks_per_run=8`
- `max_total_agent_created_tasks_per_parent=20`

Add helper copy:

```text
Runs happen in isolated git worktrees. codex-fleet never merges or deploys automatically.
```

### Project Settings

Project settings must show:

- Model
- Reasoning
- Automation mode
- Local edit approval
- Sandbox
- Max child tasks per run
- Max total child tasks per parent
- Max depth
- Skill policy

Add one small explanation per mode.

### Work Item Run Panel

The panel must show:

- latest run status
- agent name/avatar/role
- model/reasoning/sandbox/approval
- automation mode
- branch
- workspace path
- token usage when available
- parent task link
- child task list with status
- event timeline
- artifacts
- retry button
- cancel button
- answer input action when state is Needs Input

Event timeline should use friendly labels:

- `claim_acquired` -> `Claimed`
- `workspace_ready` -> `Workspace ready`
- `runner_started` -> `Codex started`
- `runner_completed` -> `Codex completed`
- `needs_input` -> `Needs input`
- `child_tasks_created` -> `Child tasks created`

### Compact Card Control

Kanban card/list compact view must show:

- run status chip
- agent avatar/role if latest run exists
- `Run with Codex` button only when item is not already Running
- `Retry` when item is Rework, Blocked, or Cancelled
- no raw log text

### Logs UI

Add a project-level `Fleet Logs` view:

- recent events
- filter by agent role
- filter by task id
- link to work item
- link to run detail

Do not depend on Plane's built-in analytics for agent visibility. Plane analytics can remain normal project analytics; codex-fleet analytics should be its own local panel.

## V2 Full-Agent Flow

### Human-Created Task

1. User creates a Plane work item.
2. User chooses automation mode per task or inherits project default.
3. User moves item to Ready.
4. Daemon claims item and moves it to Running.
5. Agent either:
   - completes it and moves it to Human Review,
   - asks input and moves it to Needs Input,
   - creates child tasks and moves parent to Planning.

### Agent-Created Child Task

1. Lead emits `codex-fleet-proposed-tasks`.
2. Orchestrator validates title/description/role/depth/count.
3. In Assisted mode, child goes to Backlog.
4. In Full Agent mode, child goes to Ready.
5. Child carries parent metadata and agent role label.
6. Worker/reviewer runs independently.
7. Parent reconciliation updates parent when children finish.

## V2 Acceptance Tests

Run:

```bash
PYTHONPATH=src .venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/pytest tests/test_codex_cli_runner.py tests/test_orchestrator.py tests/test_daemon.py tests/test_local_api.py tests/test_store.py tests/test_plane_bootstrap.py
git diff --check
```

Expected:

- No corrupt Plane patch.
- Focused tests pass.
- Full pytest may still expose unrelated environment failures; document them separately.

## Browser/Safari Smoke Test

Use a local test project, not the user's main work:

1. Start `make up` or equivalent local launcher.
2. Open the branded Plane URL in Safari.
3. Create a project with defaults.
4. Confirm onboarding shows the Plane URL, not a localhost 3000 developer URL.
5. Confirm project appears on the Plane board.
6. Create a task.
7. Confirm per-task controls show automation mode and approval labels.
8. Move task to Ready.
9. Confirm run panel shows status, agent, model/settings, events, and action buttons.
10. Confirm Fleet Logs view shows recent events.

Do not use the user's active browser profile if a background or temporary profile is available.

## Risks

- Plane patch drift can reintroduce corrupt patch failures.
- Cancel cannot reliably kill an already-running Codex process unless the runner tracks process handles; V2 cancel is a state/control-plane cancellation unless process termination is added.
- Token usage parsing depends on Codex CLI output format and must be optional.
- Full Agent mode can create noisy work unless depth/count limits are enforced in deterministic code.
- Large run logs can bloat Plane comments and future prompts; keep raw output in artifacts/events.
