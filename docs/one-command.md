# One-command usage

codex-fleet should feel like a modern open-source devtool: clone it, run one command, see something work.

## Plane-first local demo

```bash
git clone https://github.com/RishiGitH/codex-fleet.git
cd codex-fleet
make up
```

This creates a local Python environment, installs codex-fleet, and starts
official self-hosted Plane locally when `.codex-fleet.yml` is missing. The
first-run path creates or reuses a local single-user Plane
workspace/project/API token through Plane's own Docker API container, marks the
local Plane profile onboarded, writes `.codex-fleet.yml`, installs the branded
Plane frontend, bootstraps states and views, opens the local board through a
normal Plane browser session, starts the loopback API, and runs the daemon.

No Plane Cloud, hosted Plane account, GitHub token, email, or manually copied
Plane API key are required for the local Plane flow. Real runs use the local
Codex CLI after it is installed and authenticated. If Docker or Plane cannot
start, `up` falls back to the branded fork onboarding preview.

If `.codex-fleet.yml` already points at a loopback Plane project, `up` attempts to start the local self-hosted Plane runtime before checking readiness. Once Plane is ready, `up` installs the branded codex-fleet web build into the running local Plane web container and keeps a stock frontend backup under `.codex-fleet/plane-selfhost/`. For long-running runs, it also starts the loopback codex-fleet API so Plane UI controls can call run/status endpoints and so the browser can enter local Plane without a manual email/password login. If Docker is installed but the daemon is stopped, the command exits with the Docker daemon diagnostic from `plane-status`.

## Node launcher

From any project directory, use the package-style launcher:

```bash
npx github:RishiGitH/codex-fleet up
```

The launcher creates its Python tool environment under the target project's `.codex-fleet/tooling/` directory and runs against the current directory by default. Users do not need to create a shell alias.

From a cloned repo during development:

```bash
node scripts/codex-fleet-npx.js up
```

After npm publishing, the intended UX is:

```bash
npx codex-fleet up
```

## Operator commands

```bash
codex-fleet logs --repo .
make demo
codex-fleet plane-frontend status --repo .
codex-fleet plane-frontend restore --repo .
codex-fleet down --repo .
```

`logs` shows recent stored runs. `make demo` runs the internal no-Codex smoke path. `plane-frontend status|restore` inspects or restores the static frontend files inside the local Plane web container. `down` stops the local branded preview/API process when recorded, stops default preview/API ports, and runs Docker Compose down for the local Plane self-host runtime when present.

## Local Plane

For the branded local Plane fork preview:

```bash
codex-fleet plane-source --repo . --status
codex-fleet plane-verify --repo .
codex-fleet plane-fork-preview --repo . --project-path /path/to/project
```

For the official self-hosted Plane backend path:

```bash
codex-fleet plane-up --repo .
codex-fleet plane-status --repo .
codex-fleet plane-local-bootstrap --repo .
codex-fleet plane-frontend install --repo .
```

This launches Plane's official self-host installer under `.codex-fleet/plane-selfhost/`.

After Plane is running, `plane-local-bootstrap` can create/reuse the local Plane
workspace, project, and API token and write `.codex-fleet.yml`.

The manual path remains available for an existing Plane workspace/project:

```bash
PLANE_WORKSPACE_SLUG=<workspace-slug> PLANE_PROJECT_ID=<project-id> PLANE_API_KEY=<api-key> \
  codex-fleet plane-configure --repo .
codex-fleet plane-bootstrap --repo .
codex-fleet up --repo .
```

## Real Codex

After Codex CLI is installed and authenticated:

```bash
codex-fleet doctor --repo .
codex-fleet up --repo .
```

Use `make demo` for an internal no-Codex smoke run. The default real runner is direct `codex exec`; the experimental App Server path remains available with `codex.runner: app-server`.
