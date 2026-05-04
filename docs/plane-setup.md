# Plane setup

codex-fleet is designed to use Plane locally by default. Users should not need Plane Cloud credits or a hosted Plane account to try the product.

## Instant demo without Plane

For a zero-credential demo:

```bash
make up
```

This runs codex-fleet with the memory tracker and fake runner.

## Local Plane setup

Start Plane locally with:

```bash
make plane-up
```

This wrapper downloads and runs Plane's official self-host setup script under `.codex-fleet/plane-selfhost/`.

Plane's installer is interactive. The first time, choose install. After install, choose start.

After Plane is running:

1. Open the local Plane URL shown by the installer.
2. Create a local account.
3. Create a workspace and project.
4. Create a Plane API key.
5. Copy `examples/codex-fleet.plane.yml` to `.codex-fleet.yml`.
6. Export local Plane values.

```bash
cp examples/codex-fleet.plane.yml .codex-fleet.yml
export PLANE_BASE_URL="http://localhost:3000"
export PLANE_API_KEY="your-local-key"
export PLANE_WORKSPACE_SLUG="your-workspace"
export PLANE_PROJECT_ID="your-project-id"
```

Check the Plane project:

```bash
python -m codex_fleet plane-check --repo .
```

Create missing workflow states if you approve:

```bash
python -m codex_fleet plane-bootstrap --repo .
```

Run the Plane loop with the fake runner first:

```bash
python -m codex_fleet run-configured --repo . --fake
```

Then remove `--fake` only after Codex CLI is installed and authenticated:

```bash
python -m codex_fleet run-configured --repo .
```

## Existing Plane instance

You can also connect to any existing self-hosted or cloud Plane instance by setting the same environment variables above.

## Notes

- codex-fleet targets Plane work items, not deprecated issue endpoints.
- The daemon owns critical state transitions.
- Plane state creation is explicit through `plane-bootstrap`; codex-fleet does not mutate Plane projects silently.
- Codex agents may later use Plane MCP for convenience, but the scheduler uses REST for deterministic behavior.
