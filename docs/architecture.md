# Architecture

codex-fleet is a local control plane for Codex agent work.

## Components

```text
plane-web fork     -> branded local board and review surface
plane-api          -> Plane data model, projects, work items, states, comments
codex-fleet API    -> loopback-only local actions for Plane web
codex-fleet CLI    -> setup, doctor, local commands
codex-fleet daemon -> Symphony-style scheduler and run manager
Project registry   -> local folders known to codex-fleet
Tracker adapter    -> Plane, memory, later GitHub Issues/Jira/Linear
Workspace manager  -> isolated git worktrees per work item
Runner             -> fake runner in tests, Codex CLI by default, Codex App Server when configured
GitHub adapter     -> branches, PRs, CI status later
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

## Plane fork boundary

Plane provides the board, projects, states, comments, views, and work item UI. The product UI uses a small branded Plane fork so local users do not have to understand Plane setup, accounts, or tokens.

The fork is intentionally shallow. Plane web can request local actions from codex-fleet API, but codex-fleet owns project registration, path validation, worktree creation, runner dispatch, and final state transitions.

## Why Symphony is not vendored

OpenAI Symphony is treated as an architecture reference. The core loop is small enough to implement directly, and a lean Python implementation is easier to package for local users.

## Boundaries

The daemon owns critical state transitions. Codex agents can write code and produce summaries, but the daemon should decide when a task is claimed, moved to review, retried, or failed.

Plane UI must not shell out or run Codex directly. It calls the loopback codex-fleet API with structured intents such as registering a project or requesting a run for a Plane work item.
