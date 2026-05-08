# Agent orchestration

codex-fleet follows a Symphony-style orchestration model: the tracker is the control plane, each eligible work item becomes a run, the run happens in an isolated workspace, and humans review the result.

## Modes

codex-fleet supports three automation modes:

- `manual`: agents may finish the current task, but follow-up tasks are only listed in comments.
- `assisted`: agent-proposed tasks are created in Backlog for human review.
- `full_agent`: agent-proposed child tasks are created in Ready until depth/count limits are reached.

Plane child tasks are the durable visible work log. Codex-native subagents may still be used inside a run later, but they do not replace Plane tasks as the source of truth.

## State ownership

Plane users and Plane UI can create tasks, edit tasks, move tasks to Ready, and request a run.

codex-fleet owns deterministic run state:

```text
Ready
  -> claimed
  -> Running
  -> isolated worktree
  -> Codex runner
  -> collecting evidence
  -> Needs Input when user input blocks progress
  -> Human Review on success
  -> Rework on failure
  -> Blocked on local setup/auth/config failures
```

Agents can produce code, summaries, logs, and verification output. They do not decide whether a run is complete.

Agents can propose follow-up Plane tasks, but those proposals must be structured.
A runner may return proposed tasks directly, and the Codex CLI runner also
parses this fenced block from successful output:

````text
```codex-fleet-proposed-tasks
[
  {
    "title": "Add regression coverage",
    "description": "Cover the new state transition.",
    "role": "worker",
    "depends_on": [],
    "suggested_state": "Ready"
  }
]
```
````

codex-fleet decides the final state from the project mode. In `assisted`, child
tasks are Backlog. In `full_agent`, child tasks are Ready until `max_task_depth`
and `max_child_tasks_per_run` are reached.

When a `full_agent` lead creates child tasks, the parent moves to Planning. The
daemon reconciles Planning parents before dispatch:

- if every child is `Human Review` or `Done`, the parent moves to `Human Review`
- if any child is `Needs Input`, `Rework`, `Blocked`, or `Cancelled`, the parent stays `Planning` and receives one blocker comment
- if any child is still `Ready`, `Running`, or `Planning`, the parent stays `Planning` quietly

The daemon never moves a parent to Done automatically. A configured terminal
state such as `Cancelled` is not treated as a successful child outcome.

If Codex needs human input, it emits:

````text
```codex-fleet-needs-input
{"question": "Which deployment target should I use?"}
```
````

codex-fleet comments the question and moves the task to `Needs Input`.

Each run has durable local evidence in SQLite:

- `runs` stores current status, branch, worktree, runner, agent identity, model/settings metadata, token usage when available, and error.
- `events` stores append-only orchestration milestones.
- `artifacts` stores typed local file evidence emitted by runners, including path, kind, size, SHA-256 when available, and redaction class.
- `claims` prevents duplicate dispatch and records active, completed, failed, or stale ownership.
- `task_metadata` stores the Plane-visible task graph: parent, root, depth, role, dependencies, approval mode, and inherited settings.

Plane comments stay concise and human-readable. Raw run material belongs in artifacts so future agents can inspect evidence without pasting large logs into prompts.

## Stale Claim Recovery

The daemon reconciles stale active claims before dispatching work. The claim TTL is based on the longer of the Codex turn timeout and stall timeout, because a real Codex turn may legitimately run longer than the polling interval.

When a stale claim is found:

- codex-fleet marks the claim `stale`
- codex-fleet records a `stale_claim_released` event
- active local run status becomes `stalled`
- if the Plane work item is still Ready, it can be claimed again
- if the Plane work item is Running, codex-fleet comments and moves it to Rework

This keeps interrupted daemon runs from permanently stranding Ready or Running work.

## Operator Controls

The local API exposes structured retry and cancel actions for the Plane UI.

- Retry releases the latest claim, records a `retry_requested` event, comments in Plane, and moves the item back to Ready.
- Cancel records `cancel_requested`, marks the latest run cancelled, releases the claim, comments in Plane, and moves the item to Cancelled.

These controls update codex-fleet state and Plane state. V2 cancellation is a
control-plane cancellation; it does not yet guarantee process termination for an
already-running Codex subprocess unless the runner adds process-handle tracking.

## Agent Visibility

Every run should expose enough metadata for the user to see which "employee"
handled it:

- agent role/name/avatar
- model and reasoning/sandbox/approval settings
- token usage when the runner reports it
- branch and workspace path
- ordered event timeline
- artifacts

Plane comments remain concise. The run panel and Fleet Logs view should use the
local run store for detailed evidence.

## Future specialist agents

Specialist agents can be added as bounded substeps after the core loop is reliable:

- code scout before implementation
- harness reviewer after harness changes
- security reviewer for local API, Docker, tokens, and shell boundaries
- token reviewer for prompts, docs, and logs

These should feed evidence into the run record and Plane comments. They should not replace the daemon's state machine.

## Default safety

- no auto-merge
- no deploy
- no broad filesystem writes
- no arbitrary shell endpoint from Plane
- human review before Done
- raw logs stored as local artifacts, concise summaries posted to Plane
