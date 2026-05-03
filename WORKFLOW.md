# WORKFLOW

This repository follows a simple agent workflow.

## States

Backlog, Ready, Running, Human Review, Rework, Done, Blocked, Cancelled.

## Agent run rules

- Ready work can be claimed by codex-fleet.
- A claimed task moves to Running.
- Successful work moves to Human Review.
- Failed work moves to Rework with a comment.
- Done and Cancelled are terminal.

## Coding rules

- Keep changes small.
- Run relevant tests.
- Report changed files and verification commands.
- Do not deploy or merge by default.
- Keep critical state transitions in code, not only in prompts.
