# Agent orchestration

codex-fleet uses Plane as the visible control plane. Humans create top-level work in Plane, and the daemon turns durable agent assignments into Plane child tasks so the task graph stays inspectable.

## Workflow Modes

- `execute_only`: create one implementer child task and run that child. The parent moves to Human Review after the required child succeeds.
- `plan_only`: run a planner child task, create child tasks in Backlog/Todo, and leave the parent in Planning.
- `plan_execute`: run a planner child task, create eligible child tasks, auto-run within depth and parallel limits, and move the parent to Human Review.
- `full_auto`: run planner, scout/implementer/reviewer children, move the parent to Done when required children pass, and create a delivery task.

Only `full_auto` can move a parent to Done automatically. No mode pushes, merges, deploys, or opens a pull request by default.

## Task Graph

Every durable subagent assignment becomes a Plane child task:

- planner
- code scout
- implementer
- harness reviewer
- security reviewer
- token reviewer
- delivery manager

`task_metadata` stores the local graph: parent/root ids, depth, dependencies, role, workflow mode, model/settings, delivery status, and terminal outcome. Plane comments stay concise; raw logs and large evidence stay in local artifacts.

## Planner Contract

Planner output is structured JSON only. The Codex CLI runner parses one fenced `codex-fleet-planner-output` block:

````text
```codex-fleet-planner-output
{
  "summary": "Split implementation and review.",
  "tasks": [
    {
      "title": "Add regression coverage",
      "description": "Cover the new state transition.",
      "role": "implementer",
      "priority": "high",
      "depends_on": [],
      "workflow_mode": "execute_only"
    }
  ],
  "reviewers": ["harness_reviewer"]
}
```
````

Invalid planner output creates a blocker and moves the task to Rework/Needs Input. Planners do not edit files. Implementers work only on assigned tasks. Reviewers report findings only. Delivery managers prepare instructions only.

## State Ownership

Plane users and Plane UI can create tasks, edit tasks, move tasks to Ready, and request a run. codex-fleet owns deterministic run state:

```text
Ready -> Running -> isolated worktree -> Codex runner -> evidence collection
```

Successful child work moves to Human Review. Failed work moves to Rework. Local setup/auth/config failures move to Blocked. User questions move to Needs Input.

Planning parents are reconciled before dispatch:

- all required children passed: parent moves to Human Review, or Done in `full_auto`
- any required child failed/cancelled/needs input: parent moves to Blocked with a clear comment
- children still active: parent stays Planning

Max depth and max parallel agents are enforced by daemon code, not only prompt text.

## Evidence

Each run records local evidence in SQLite:

- `runs`: status, branch, worktree, runner, agent identity, model/settings, token usage when available, and error
- `events`: append-only orchestration milestones
- `artifacts`: local file evidence with path, kind, size, SHA-256 when available, and redaction class
- `claims`: duplicate-dispatch prevention
- `task_metadata`: Plane-visible task graph

Token usage is shown only when reliable. Missing usage is reported as unavailable, never fabricated.

## Operator Controls

Retry releases the latest claim, records a retry event, comments in Plane, and moves the item back to Ready. Cancel records cancellation events, marks the latest run cancelled, releases the claim, comments in Plane, and moves the item to Cancelled.

## Safety

- Plane web never shells out or starts Codex directly.
- codex-fleet never broad-mounts the filesystem by default.
- codex-fleet does not auto-merge, deploy, push, or create PRs.
- GitHub is optional; delivery tasks include local merge instructions when no remote exists.
