# Plane setup

codex-fleet is designed to use Plane locally by default. Users should not need Plane Cloud credits or a hosted Plane account to try the product.

## Plane-first demo

Start the local Plane-backed demo:

```bash
make up
```

This installs codex-fleet and, on a first run without `.codex-fleet.yml`, starts
official self-hosted Plane locally. codex-fleet creates or reuses a local
single-user Plane user, workspace, project, and API token through Plane's own
Docker API container, writes `.codex-fleet.yml`, installs the branded Plane
frontend, bootstraps the project, and runs the local Codex daemon path. If Docker or
Plane cannot start, it falls back to the branded fork onboarding preview.

After `.codex-fleet.yml` exists for a real Plane project, `make up` runs the configured daemon loop.

## Branded Plane fork preview

```bash
make plane-fork-preview
```

This serves the already-built local Plane fork from `.codex-fleet/plane-src/apps/web/build/client` and starts the codex-fleet API on loopback. The URL token is stored in `.codex-fleet/secrets/local_api_token` and is passed in the URL fragment so it is not sent to the static web server.

If the branded Plane web build is missing, codex-fleet prepares it automatically from the pinned Plane source and tracked patch. That preparation requires `git`, `pnpm`, and network access for the first clone/install.

To prepare the branded Plane build without opening a browser:

```bash
make plane-fork-prepare
```

Stop local preview/API services and the self-hosted Plane Docker runtime when present:

```bash
make down
```

## Official self-hosted Plane setup

Start Plane locally with:

```bash
make plane-up
```

This wrapper downloads and runs Plane's official self-host setup script under `.codex-fleet/plane-selfhost/`.

Check readiness with:

```bash
codex-fleet plane-status --repo .
```

`plane-status` reports the local runtime path, whether a Plane app directory exists, Docker availability, Docker daemon readiness, and the Plane HTTP readiness result. If Docker is installed but the daemon is down, start Docker Desktop or your Docker service before running `plane-up`.

Plane's official self-host installer may be interactive. The first time, choose install. After install, choose start.

After Plane is running, configure codex-fleet automatically:

```bash
codex-fleet plane-local-bootstrap --repo .
codex-fleet plane-bootstrap --repo .
codex-fleet up --repo .
```

`plane-local-bootstrap` writes `.codex-fleet.yml` and stores the local Plane API
key in `.codex-fleet/secrets.env`. It does not print the key.

The manual path remains available for an existing Plane workspace/project:

```bash
PLANE_WORKSPACE_SLUG="your-workspace" PLANE_PROJECT_ID="your-project-id" PLANE_API_KEY="your-local-key" \
  codex-fleet plane-configure --repo .
```

Check the Plane project:

```bash
codex-fleet plane-check --repo .
```

Create missing workflow states if you approve:

```bash
codex-fleet plane-bootstrap --repo .
```

Run the Plane loop with the local Codex runner:

```bash
codex-fleet up --repo .
```

Use the fake runner only for internal no-Codex smoke testing:

```bash
codex-fleet up --repo . --fake
```

## Existing Plane instance

You can also connect to any existing self-hosted or cloud Plane instance by setting the same environment variables above.

## Notes

- codex-fleet targets Plane work items, not deprecated issue endpoints.
- The daemon owns critical state transitions.
- `plane-bootstrap` creates required workflow states in the configured Plane project.
- The branded Add Project/onboarding flow may create or link a Plane project for a registered local folder when the control repo is already Plane-backed. The API response reports whether that mapping was linked, skipped, or failed.
- Codex agents may later use Plane MCP for convenience, but the scheduler uses REST for deterministic behavior.
