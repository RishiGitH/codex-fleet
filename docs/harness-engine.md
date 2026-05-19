# Harness engine

The harness engine prepares a local project so Codex agents can work effectively and safely.

## Goals

- detect how the project is built and tested
- write concise local agent guidance
- keep raw logs as artifacts
- avoid overwriting user files unexpectedly
- make setup visible in the codex-fleet Plane UI

## Scan

The scanner should detect:

- git root and dirty state
- package manager
- framework/language
- test command
- lint command
- typecheck command
- build command
- install command
- dev command
- existing `AGENTS.md`
- existing `.codex/config.toml`
- env files that may contain secrets
- CI hints

Current scan output includes `git_root`, dirty state, stack, package manager, common commands, and warnings. Non-git folders are reported as `blocked` instead of `needs_setup` so the UI can tell users to choose a real repository before running agents.

## Apply

Harness apply should propose changes first, then write only accepted files.

Initial generated files:

- `AGENTS.md`
- `README.md`
- `WORKFLOW.md`
- `.codex-fleet/project.json`
- `.codex/config.toml`
- `.codex/agents/code-scout.toml`
- `.agents/skills/repo-harness-review/SKILL.md`

`.codex-fleet/project.json` records the detected stack, package manager, common commands, and canonical codex-fleet defaults such as `workflow_mode`, model, reasoning effort, sandbox/approval, and max depth. It is project runtime metadata, not a secret store.

Starters are intentionally small and runnable:

- `blank`: git repo plus harness
- `simple-web`: root `index.html`
- `node-next`: minimal Next app with scripts
- `python`: minimal package plus pytest smoke test

Do not overwrite unrelated user content. If a file exists, merge conservatively or leave a clear diff for review.

## UI

The customized Plane UI should show harness status per project:

- not scanned
- needs setup
- ready
- warnings
- blocked

The UI may request scan/apply through codex-fleet API, but codex-fleet performs the filesystem writes.

## Doctor

Doctor checks the control repo and any registered local projects. It reports missing project folders, invalid git repos, missing harness files, missing Plane links, missing `apps/plane`, stale `.codex-fleet/plane-src`, and removed Plane patch resources. Findings are actionable and point to either the Plane project flow or the CLI harness command.
