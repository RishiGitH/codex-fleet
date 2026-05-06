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
- `WORKFLOW.md`
- `.codex/config.toml`
- `.codex/agents/code-scout.toml`
- `.agents/skills/repo-harness-review/SKILL.md`

Do not overwrite unrelated user content. If a file exists, merge conservatively or leave a clear diff for review.

## UI

The customized Plane UI should show harness status per project:

- not scanned
- needs setup
- ready
- warnings
- blocked

The UI may request scan/apply through codex-fleet API, but codex-fleet performs the filesystem writes.
