# 0002: Branded Plane fork and loopback codex-fleet API

## Status

Accepted.

## Context

The first MVP used Plane as an external local service through its public API. That proved useful for validating the tracker adapter, workflow states, comments, worktrees, fake runner, and Codex App Server boundary.

The product requirement has changed: the default local experience must feel like a real codex-fleet product, not a manual Plane setup. Users should run one command, land in a branded browser board, add local folders as projects, and run Codex tasks without creating a Plane account, copying API keys, or understanding Plane internals.

## Decision

codex-fleet will use a small, explicit Plane fork for the product UI and a loopback-only codex-fleet API for local agent actions.

The local runtime has three services:

- `codex-fleet-api`: local orchestration API, bound to loopback.
- `plane-api`: Plane's API and data model, kept close to upstream.
- `plane-web`: branded codex-fleet Plane UI fork.

Plane remains the board and review surface. codex-fleet remains the orchestration owner.

Plane web may request structured actions from codex-fleet, such as registering a project or running a work item. Plane web must not run shell commands, create worktrees, start Codex, or decide final task state.

## Boundaries

codex-fleet owns:

- local project registry
- path validation
- local API authentication
- harness planning and application
- work item claiming
- worktree creation
- Codex runner execution
- run status persistence
- state transitions after run completion
- Plane comments/log summaries

Plane owns:

- board UI
- work item editing
- project views
- comments display
- human review workflow

## Fork scope

Allowed Plane fork changes:

- codex-fleet branding, logo, favicon, manifest, navigation labels
- local-first onboarding
- no-login local bootstrap UI
- project add flow that calls codex-fleet API
- work-item run controls
- run status and log panels
- empty states and local setup copy

Avoid unless there is a concrete blocker:

- auth model rewrites
- core Plane models
- migrations
- unrelated product surfaces
- hosted/cloud behavior

## Local no-login model

No-login means local single-user bootstrap, not a global auth bypass.

The bootstrap flow should:

- run only in explicit local mode
- bind codex-fleet API to `127.0.0.1`
- generate a local secret under `.codex-fleet/secrets`
- create or reuse a local Plane user/workspace/project/session
- open the browser directly into the local board

If a normal Plane session cannot be established non-interactively, the fallback is a single local "Continue locally" action backed by codex-fleet bootstrap. The default path must not require email/password or manual API token copy/paste.

## Consequences

This increases packaging complexity and creates Plane fork drift risk. Keep the fork shallow and rebaseable.

The old custom local Kanban is not the product UI. It may remain only as a hidden internal smoke harness.
