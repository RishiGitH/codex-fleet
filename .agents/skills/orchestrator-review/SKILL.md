---
name: orchestrator-review
description: Review changes to scheduling, workspace preparation, run state, retries, or tracker transitions.
---

# Orchestrator Review

Use this skill for changes in `src/codex_fleet/orchestrator.py`, tracker adapters, runners, and workspace code.

Check:

- work item state transitions
- duplicate dispatch prevention
- failure and retry behavior
- workspace path safety
- deterministic comments and status updates
- test coverage for success and failure paths

Return only concrete findings with suggested fixes.
