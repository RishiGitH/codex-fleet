# One-command usage

codex-fleet should feel like a modern open-source devtool: clone it, run one command, see something work.

## Instant local demo

```bash
git clone https://github.com/RishiGitH/codex-fleet.git
cd codex-fleet
make up
```

This mode does not require Plane, Codex credentials, GitHub credentials, or cloud services.

It creates a local Python environment, installs codex-fleet, runs the repo doctor, and runs one fake tracked work item through the scheduler.

## Node launcher

From a cloned repo:

```bash
node scripts/codex-fleet-npx.js up --fake --once
```

After npm publishing, the intended UX is:

```bash
npx codex-fleet up --fake --once
```

## Local Plane

```bash
make plane-up
```

This launches Plane's official self-host installer under `.codex-fleet/plane-selfhost/`.

After Plane is running, configure `.codex-fleet.yml`, then run:

```bash
python -m codex_fleet plane-check --repo .
python -m codex_fleet plane-bootstrap --repo .
python -m codex_fleet up --repo . --fake --once
```

## Real Codex

After Codex CLI is installed and authenticated:

```bash
python -m codex_fleet up --repo . --once
```

Run the fake path first before allowing real Codex to edit code.
