# Product Design: Branded Plane Control Plane

This document is the durable product plan for making `codex-fleet` feel like a real local app instead of a collection of setup commands.

## Product Promise

A developer can install `codex-fleet`, run one command in or for a local project, open a branded local Plane board with no Plane Cloud and no login ceremony, add tasks, run Codex against those tasks, and review the result with clear evidence.

The happy path is:

```bash
codex-fleet up --repo .
```

or from a package-style launcher:

```bash
npx codex-fleet up
```

The normal product path uses the local Codex CLI. The internal fake runner is
reserved for tests and smoke checks where Codex credentials are intentionally
absent.

## Product Shape

`codex-fleet` is not a custom Kanban app. The product UI is a shallow branded fork of Plane.

Plane provides:

- browser board, list, and detail views
- work items
- workflow states
- comments
- project and workspace concepts
- human review surface

codex-fleet provides:

- one-command local setup
- project folder registry
- loopback local API
- harness scanner and writer
- deterministic task claiming
- isolated git worktrees
- internal fake runner for tests and smoke checks
- Codex CLI runner for normal local work
- Codex App Server runner boundary for richer future runs
- run records, events, artifacts, and status comments

The UI may request actions. It does not execute them.

## Architecture

```text
branded Plane web
  -> calls loopback codex-fleet API with structured intents

local Plane API
  -> stores workspaces, projects, work items, states, comments, views

codex-fleet API
  -> validates local requests, project ids, tokens, paths
  -> dispatches orchestrator actions

codex-fleet daemon/orchestrator
  -> claims Ready work
  -> moves item to Running
  -> creates isolated worktree
  -> invokes Codex runner, or the internal fake runner in smoke mode
  -> stores evidence
  -> comments in Plane
  -> moves item to Human Review or Rework

project registry
  -> maps local folders to Plane projects

run store
  -> records runs, events, artifacts, and claims
```

The hard boundary is:

- Plane UI is the human control surface.
- codex-fleet is the privileged local automation engine.
- The daemon owns final state transitions.

## Why Plane Is Forked

Official Plane is a serious Kanban UI, but a generic local Plane install still exposes product friction that does not belong in a one-command local Codex control plane:

- signup/login screens
- generic Plane onboarding
- generic empty states
- no local project folder picker
- no Codex run controls
- no worktree/run evidence panel

The fork solves local UX. It must not become a deep Plane product fork.

Allowed fork scope:

- codex-fleet logo and naming
- local-first onboarding
- no-login local session bridge
- project/folder setup flow
- Codex run/status controls on work item detail, list rows, and Kanban cards
- concise run evidence panels
- local setup defaults

Disallowed fork scope unless a blocker is documented:

- Plane auth core
- Plane backend models
- Plane migrations
- Plane permissions
- unrelated settings pages
- unrelated product surfaces

## One-Command Setup Flow

`codex-fleet up --repo .` should perform this flow without requiring a Plane Cloud account, hosted Plane, GitHub token, or manual Plane API key copy. It should use the local Codex CLI for real runs and reserve `--fake` for internal smoke tests when Codex credentials are intentionally unavailable:

1. Resolve target repo path.
2. Detect local prerequisites.
3. Start or install local self-hosted Plane.
4. Wait for Plane readiness with clear errors.
5. Clone or update pinned Plane source if the customized frontend is missing.
6. Apply and verify the codex-fleet Plane patch.
7. Build/install the branded Plane frontend.
8. Create or reuse the local Plane user.
9. Create or reuse the local workspace.
10. Create or reuse the local Plane project.
11. Mark local Plane profile as onboarded.
12. Create required workflow states.
13. Create required saved views.
14. Register the local folder as a codex-fleet project.
15. Write `.codex-fleet.yml` with non-secret config.
16. Write local secrets under `.codex-fleet/secrets.env` with restrictive permissions.
17. Start the loopback codex-fleet API.
18. Start the daemon.
19. Print and open the local Plane URL.

If a dependency is missing, fail with a precise next action. Do not fail with a Python traceback for ordinary user setup problems.

## Workflow States

The required Plane states are:

- Backlog
- Planning
- Ready
- Running
- Needs Input
- Human Review
- Rework
- Done
- Blocked
- Cancelled

Only Ready items are eligible for automatic dispatch.

Automation modes:

- `manual`: follow-up tasks are reported, not created.
- `assisted`: follow-up tasks are created in Backlog for review.
- `full_agent`: follow-up child tasks are created in Ready within configured depth/count limits.

## Run State Machine

The orchestrator should make run progress explicit and durable:

```text
Ready
  -> claim_acquired
  -> Running
  -> preparing_workspace
  -> workspace_ready
  -> runner_started
  -> runner_event
  -> runner_completed
  -> collecting_evidence
  -> Planning | Needs Input | Human Review | Rework | Blocked | Cancelled
```

Tracker transitions:

- Ready -> Running before the runner starts.
- Running -> Human Review on success.
- Running -> Planning when a full-agent lead creates Ready child tasks.
- Running -> Needs Input when Codex asks a blocking question.
- Running -> Rework on runner failure.
- Running -> Blocked when local setup or credentials are missing.
- Running -> Cancelled only through explicit operator cancellation.
- Done remains a human-controlled final acceptance state.

Agents can produce code, summaries, verification, and artifacts. Agents do not decide final state.

## Project Registry

Users should be able to add any local folder as a project from Plane or CLI.

Registry metadata should include:

- local path
- display name
- Plane workspace slug
- Plane project id
- default branch
- runner mode
- harness status
- last scan time
- last run status
- last error

The primary user path is Plane-first: add a project folder from the branded
Plane setup flow, let codex-fleet create or map the Plane project, optionally
apply harness files, then create Plane work items and move them to Ready. The
daemon must watch every registered Plane project, not only the control repo.

Path validation must reject:

- missing paths
- file paths
- filesystem root
- symlink escapes
- duplicate canonical paths
- broad home-directory registration unless explicitly confirmed

Browser requests must not provide arbitrary `cwd`, shell command, worktree path, branch path, or artifact path. They should reference an existing project id or Plane project id.

## Harness Setup

codex-fleet should help make a project agent-ready before running real Codex.

Scan should detect:

- git root
- dirty state
- language/framework
- package manager
- install command
- test command
- lint command
- typecheck command
- build command
- dev server command where applicable
- existing `AGENTS.md`
- existing `.codex/config.toml`
- docs map
- env files that may contain secrets
- CI hints

Harness status values:

- `not_scanned`
- `blocked`
- `needs_setup`
- `ready`
- `warnings`
- `blocked`

Apply behavior:

- preview first
- write only accepted files
- never overwrite user files silently
- never copy `.env` or secrets into docs, prompts, comments, or artifacts
- keep `AGENTS.md` short and point to deeper docs
- include task-source guidance so follow-up work can be marked
  `human-requested`, `agent-proposed`, or `agent-followup`

Initial generated files may include:

- `AGENTS.md`
- `WORKFLOW.md`
- `.codex/config.toml`
- `.codex/agents/code-scout.toml`
- `.agents/skills/repo-harness-review/SKILL.md`

## Runner Strategy

### Internal Fake Runner

The fake runner is an internal smoke and test runner.

It must:

- require no Codex credentials
- require no GitHub token
- create visible worktree evidence
- support forced success and forced failure
- produce deterministic comments and artifacts

### Codex CLI Runner

The Codex CLI runner is the first stable real runner.

It must:

- use `codex exec`
- preflight binary availability, `codex exec` option compatibility, and `codex login status` before launching a run
- use argv lists only
- never use `shell=True`
- run with `cwd` set to the isolated worktree
- use `--sandbox workspace-write` or stricter
- pass prompts through stdin or a safe argument contract
- enforce timeouts
- capture structured output where possible
- save raw output as artifacts
- post concise summaries to Plane
- fail before model execution with a clear message when auth or CLI compatibility is missing

Codex can propose follow-up work by emitting a fenced JSON block:

````text
```codex-fleet-proposed-tasks
[
  {
    "title": "Add browser verification for project creation",
    "description": "Exercise the native Plane create-project folder flow."
  }
]
```
````

codex-fleet parses that block after a successful run, creates Backlog Plane work
items labeled `agent-proposed`, records `proposed_task_created` events, and
mentions the created items in the completion comment. Proposed work does not run
automatically until a human reviews it and moves it to Ready.

### Codex App Server Runner

The App Server runner is the richer future path because it can expose thread lifecycle, events, diffs, approvals, and UI-ready updates through a bidirectional protocol.

It should remain optional until the CLI path is proven end-to-end.

## Multi-Agent Strategy

Default mode:

- one implementation agent per work item
- multiple independent work items may run concurrently up to `agent.max_concurrent_agents`
- agents may propose follow-up Plane tasks using the structured output block
  above; humans decide whether to move those tasks to Ready
- each work item gets its own isolated worktree

Advanced mode:

- a user can choose `Run with review agents`
- the pipeline may include scout, implementer, harness reviewer, security reviewer, and token reviewer
- specialist agents feed evidence into the same run record
- the daemon still owns final state

Do not start with unconstrained agent-to-agent delegation inside one task. Make independent-item parallelism reliable first.

## UI Requirements

The branded Plane fork should expose:

- codex-fleet logo and local product name
- no-login local onboarding
- add local folder/project flow
- harness scan/apply status
- Ready work view
- Running work view
- Human Review view
- Rework view
- run controls on work item detail
- compact run controls on list rows
- compact run controls on Kanban cards
- run status panel with run id, runner mode, branch, worktree, summary, verification, and artifacts
- clear blocked/error messages with next action

## Security Requirements

The local product is privileged automation and must be treated as such.

Required boundaries:

- codex-fleet API binds to `127.0.0.1` by default.
- Mutation endpoints require a local API token.
- Plane UI never shells out.
- Browser requests never choose arbitrary commands or paths.
- Project registration validates local paths.
- Worktrees stay under the configured workspace root.
- Docker operations only target local Plane runtime files needed for the branded frontend.
- No default auto-merge, deploy, push, or PR write.

Never expose these in API responses, terminal output, Plane comments, or docs:

- Plane API key
- Codex auth
- GitHub token
- local API token
- session cookies
- `.env` values

## Recovery Requirements

The product should handle restarts and crashes.

Required behavior:

- duplicate claims are rejected
- active claims have a TTL
- stale claims are recoverable before dispatch
- daemon restart reconciles stale Ready items by releasing them for retry
- daemon restart reconciles stale Running items by commenting and moving them to Rework
- failed preflight moves work to Blocked or Rework with a useful comment
- cancellation is explicit and recorded

## Packaging

User-facing installation should not require shell aliases.

Supported paths:

- Python package console script: `codex-fleet`
- Node wrapper: `npx codex-fleet`
- local repo: `make up`
- future: Homebrew formula

The package wrapper may create its own tool venv under `.codex-fleet/tooling/`, but this should be an implementation detail.

## Validation Gates

Release-quality work must pass:

```bash
pytest
ruff check .
mypy src/codex_fleet
python -m codex_fleet doctor --repo .
python -m codex_fleet budget --repo . --strict
python -m codex_fleet plane-verify --repo .
npm pack --dry-run
```

Local product verification must include:

```bash
codex-fleet up --repo . --fake --once
```

Browser verification must prove:

- branded local Plane opens
- no email/password login is required
- project can be added
- harness scan/apply is visible
- Ready item moves to Running
- fake success moves to Human Review
- fake failure moves to Rework
- worktree path is visible
- Plane comments/status/logs are visible

Real-runner verification must prove one of:

- authenticated Codex CLI e2e completes a tiny safe task, or
- doctor/preflight confirms Codex CLI install, auth, and expected `codex exec` contract, or
- missing Codex auth is detected and surfaced as Blocked/Rework without crashing

## Completion Audit

Do not mark this product work complete until the audit maps every requirement to evidence:

- Plane clone/pin path and commit
- Plane patch/customization verification
- local Plane URL and readiness
- no-login onboarding evidence
- config and secrets file evidence
- required Plane states/views
- local project registration
- harness scan/apply evidence
- Ready -> Running -> Human Review evidence
- Ready -> Running -> Rework evidence
- worktree creation evidence
- fake runner evidence
- real runner preflight or e2e evidence
- Plane comments/log/status evidence
- browser observations against local Plane
- test command outputs
- token budget output
- security notes
- explicit note that custom local Kanban is not the product UI

## Research Anchors

- OpenAI's Symphony post frames the tracker as the control plane for coding agents, with each active issue mapped to its own workspace and humans reviewing results: <https://openai.com/index/open-source-codex-orchestration-symphony/>
- Symphony's published spec emphasizes a portable loop of tracker task -> workspace lifecycle -> agent runner -> status/logging surface, plus safety invariants around per-issue workspace paths and sanitized workspace keys: <https://openai.com/index/open-source-codex-orchestration-symphony/>
- OpenAI's harness engineering guidance favors short `AGENTS.md` files as maps to deeper repo-local docs, structured docs as source of truth, and automated checks as part of the agent workflow: <https://openai.com/index/harness-engineering/>
- OpenAI's Agents SDK harness/sandbox guidance reinforces the same boundary codex-fleet uses: predictable workspaces, explicit instructions, tools for evidence inspection, and separation between harness/control credentials and compute: <https://openai.com/index/the-next-evolution-of-the-agents-sdk/>
- Codex CLI `exec` is the stable non-interactive automation path.
- Codex App Server is the richer protocol path for thread lifecycle, events, diffs, approvals, and client UIs.

## `/goal` Handoff Prompt

Use this prompt for a future implementation run:

```text
Build codex-fleet into a polished one-command local product using a branded local Plane fork as the UI and codex-fleet as the orchestration engine.

Deliver the user experience where `codex-fleet up --repo .` or `npx codex-fleet up` starts local Plane, installs the branded fork, creates a no-login local workspace/project, registers the current folder, starts the loopback API and daemon, opens the board, and lets the user run Ready work items through the real local Codex CLI in isolated git worktrees. `--fake` is an internal smoke-test mode only.

Keep Plane as the product UI. Do not build a custom Kanban UI. Do not let Plane shell out or run Codex directly. Plane UI may only call token-protected loopback codex-fleet API intents. codex-fleet owns project registration, path validation, duplicate claims, worktrees, runner execution, comments, artifacts, and final state transitions.

Implement and verify: branded Plane no-login onboarding, project folder registry, harness scan/apply flow, work-item run controls, run status panels, Ready -> Running -> Human Review success, Ready -> Running -> Rework failure, stale claim recovery, safe real Codex CLI preflight, docs, security tests, browser verification, and completion audit.

Before marking complete, run the validation gates in docs/product-design.md and update docs/completion-audit.md with concrete evidence.
```
