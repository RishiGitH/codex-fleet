# Plane setup

codex-fleet uses Plane as an external tracker/board. It does not vendor or fork Plane.

## Option A: existing Plane instance

Create or choose a Plane workspace and project, then create these states:

- Backlog
- Ready
- Running
- Human Review
- Rework
- Done
- Blocked
- Cancelled

Create an API key in Plane and configure:

```bash
export PLANE_BASE_URL="https://your-plane.example.com"
export PLANE_API_KEY="..."
export PLANE_WORKSPACE_SLUG="your-workspace"
export PLANE_PROJECT_ID="your-project-id"
```

Then set `.codex-fleet.yml`:

```yaml
tracker:
  kind: plane
  active_states: [Ready, Running, Rework]
  handoff_states: [Human Review]
  terminal_states: [Done, Cancelled]
```

Run one fake tick first:

```bash
python -m codex_fleet run-configured --repo . --fake
```

## Option B: self-host Plane

Use Plane's official self-hosting docs and setup script. codex-fleet will add a helper later, but the safe MVP path is to connect to a working Plane instance through the API first.

## Notes

- codex-fleet targets Plane work items, not deprecated issue endpoints.
- The daemon owns critical state transitions.
- Codex agents may later use Plane MCP for convenience, but the scheduler uses REST for deterministic behavior.
