# Plan: Phase 2 real local workflow

## Goal

Turn the Phase 1 foundation into a real local loop with Plane and Codex App Server.

This plan is historical. The current product direction is captured in
`docs/product-design.md`: use a shallow branded Plane fork for local product UX
and keep codex-fleet as the orchestration engine.

## Non-goals

- No custom Kanban replacement for Plane.
- No auto-merge.
- No hosted SaaS.
- No multi-repo dashboard yet.

## Steps

1. Add Plane state resolution.
   - Fetch project states.
   - Map state names to IDs.
   - Implement `PlaneTracker.update_item_state`.

2. Add Plane bootstrap.
   - Connect to existing Plane first.
   - Add Docker Compose only after direct API flow works.
   - Ensure project states exist.

3. Add local run database.
   - SQLite tables for repos, runs, events, and artifacts.
   - Persist run state between process restarts.

4. Add Codex App Server runner.
   - Launch configured command in worktree.
   - Initialize JSON-RPC session.
   - Start thread and turn.
   - Stream events to local artifacts.
   - Convert success/failure into `RunResult`.

5. Add daemon command.
   - Poll Plane.
   - Reconcile active runs.
   - Run bounded concurrency.
   - Handle graceful shutdown.

6. Add better CLI.
   - `up`
   - `down`
   - `logs`
   - `open`
   - `run`

## Tests

- Plane state-name to state-ID resolution with fake HTTP.
- Ready item moves to Running and Human Review.
- Failed run moves to Rework.
- Codex fake app-server completes a turn.
- Stalled run is marked failed or retried.

## Risks

- Plane API differences between Cloud and self-hosted.
- Codex App Server protocol changes.
- Long-running daemon failure recovery.
- User tokens with insufficient permissions.
