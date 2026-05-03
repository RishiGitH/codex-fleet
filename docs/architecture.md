# Architecture

codex-fleet is a local control plane for Codex agent work.

## Components

```text
Plane board       -> work item source and human review surface
codex-fleet CLI   -> setup, doctor, local commands
codex-fleet daemon -> Symphony-style scheduler and run manager
Tracker adapter   -> Plane, memory, later GitHub Issues/Jira/Linear
Workspace manager -> isolated git worktrees per work item
Runner            -> fake runner in tests, Codex App Server in production
GitHub adapter    -> branches, PRs, CI status later
```

## Core flow

```text
Ready work item
  -> daemon claims it
  -> state becomes Running
  -> worktree is created
  -> Codex runner executes task
  -> result is recorded
  -> state becomes Human Review or Rework
```

## Why Plane is external

Plane already provides the board, projects, states, comments, views, and work item UI. We use it through its API instead of forking it.

## Why Symphony is not vendored

OpenAI Symphony is treated as an architecture reference. The core loop is small enough to implement directly, and a lean Python implementation is easier to package for local users.

## Boundaries

The daemon owns critical state transitions. Codex agents can write code and produce summaries, but the daemon should decide when a task is claimed, moved to review, retried, or failed.
