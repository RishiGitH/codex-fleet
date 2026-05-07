# Agent orchestration

codex-fleet follows a Symphony-style orchestration model: the tracker is the control plane, each eligible work item becomes a run, the run happens in an isolated workspace, and humans review the result.

## MVP model

Use one implementation agent per work item.

Multi-agent execution means multiple independent work items can be dispatched within configured limits. The current daemon honors `agent.max_concurrent_agents` as the number of Ready items it may claim in one tick, while each claimed item still runs through the same deterministic single-item orchestrator path. Do not start with unconstrained agent-to-agent delegation for a single task.

## State ownership

Plane users and Plane UI can create tasks, edit tasks, move tasks to Ready, and request a run.

codex-fleet owns deterministic run state:

```text
Ready
  -> claimed
  -> Running
  -> isolated worktree
  -> Codex runner
  -> Human Review on success
  -> Rework on failure
```

Agents can produce code, summaries, logs, and verification output. They do not decide whether a run is complete.

Agents can propose follow-up Plane tasks, but those proposals must be structured
and reviewable. A runner may return proposed tasks directly, and the Codex CLI
runner also parses this fenced block from successful output:

````text
```codex-fleet-proposed-tasks
[
  {"title": "Add regression coverage", "description": "Cover the new state transition."}
]
```
````

codex-fleet creates these as Backlog work items labeled `agent-proposed`, records
events, and links them from the original completion comment. They do not run
until a human intentionally moves them to Ready.

Each run has durable local evidence in SQLite:

- `runs` stores current status, branch, worktree, and error.
- `events` stores append-only orchestration milestones.
- `artifacts` stores local file paths emitted by runners.
- `claims` prevents duplicate dispatch and records active, completed, failed, or stale ownership.

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
