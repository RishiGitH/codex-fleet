# Completion audit

Last updated: 2026-05-06.

This audit maps the Plane-first codex-fleet objective to current artifacts and command evidence. It is intentionally conservative.

## Decision

Status: **complete for the Plane-first local codex-fleet product path**.

The branded Plane fork path, official self-hosted Plane fake flow, local API,
project registry, fake runner flow, worktree isolation, run store, docs, tests,
browser verification, packaging path, and agent-proposed follow-up workflow are
implemented and verified. The default product path is local Plane plus the real
local Codex runner; `--fake` remains an internal smoke/test mode.

- The branded Plane fork dashboard can register projects, show Plane Ready
  items, run a selected item with `Run with Codex`, and display run evidence.
  Plane work-item detail pages and work-item row/card surfaces now include
  embedded codex-fleet run/status controls, so users can trigger or inspect
  agent work without leaving the board/list context.
- The no-login branded onboarding and local Plane board path now work through
  the local codex-fleet API. `plane-local-bootstrap` creates/reuses a local
  Plane user, workspace, project, API token, and fully onboarded Plane profile
  without copying keys through the browser. The local API creates a normal Plane
  browser session and redirects directly to the branded local Plane work-items
  page without an email/password step.
- Multi-project registration, `project_id`-scoped local API execution, and
  automatic Plane project creation/mapping are implemented. Browser verification
  has exercised project registration and harness application; a CLI/API live
  check created a new Plane project for a fresh local folder and dispatched its
  demo task with the fake runner.
- Real authenticated `codex exec` has now been verified both in a memory-backed
  throwaway repo and in a local Plane-backed throwaway work item. Codex App
  Server remains structurally preserved and covered by boundary tests, but the
  default product runner is the local Codex CLI.

## Latest verification, 2026-05-06

- `python -m codex_fleet plane-patch export --repo .`: exported the current branded Plane patch to `patches/plane-codex-fleet.patch`.
- Bundled patch copied to `src/codex_fleet/resources/plane-codex-fleet.patch`.
- `python -m codex_fleet plane-verify --repo .`: passed all structural checks, including root guide, no stock auth screen, dashboard project creation, native Plane project modal create/link controls, inline local API status, folder picker wiring, local API run client, work-item detail integration, kanban/list compact run controls, and branding checks.
- `python -m codex_fleet plane-frontend install --repo . --rebuild`: installed the branded frontend into `plane-app-web-1`.
- Focused tests: `pytest tests/test_plane_manager.py tests/test_local_api.py tests/test_orchestrator.py tests/test_plane.py tests/test_plane_views.py` passed, `58 passed`.
- Typecheck: `pnpm --dir .codex-fleet/plane-src --filter web check:types` passed with only the existing module-type warning from Plane's tailwind package.
- Lint: targeted `ruff check` passed for changed codex-fleet modules and tests.
- `python -m codex_fleet budget --repo . --strict`: passed; all tracked docs, skills, and agent configs were under budget.
- `make full-local-check`: passed; full repo `ruff`, `pytest` (`172 passed`), `doctor` (`95/100`, only expected missing local CI workflow), budget, and smoke tests (`11 passed`) all succeeded.
- One-command bounded Plane flow: `python -m codex_fleet up --repo . --fake --once` reported `Plane ready: True (HTTP 200 at /)`, installed branded frontend into `plane-app-web-1`, bootstrapped saved views (`1 created, 4 existing`), reported `Tracker: plane`, `Ticks: 1`, `Dispatched: 0`, and exited cleanly.
- Browser verification used a separate Brave profile/process against `127.0.0.1:8080`, not the custom smoke UI. Root page showed `Start codex-fleet for this repo`, `codex-fleet up --repo .`, `Open project dashboard`, `Add or create project`, and no `Sign up` or `Welcome to Plane` copy.
- Local login bridge verification in the separate Brave process landed on `http://127.0.0.1:8080/codex-fleet/projects/` with title `codex-fleet - Projects` and visible local Plane projects, without an email/password step.
- Native Plane Add Project modal verification in Brave showed the codex-fleet panel with `Link folder`, `Create project`, `Choose Folder`, `Paste path instead`, and `Add codex-fleet agent harness`.
- Native Plane modal now shows inline local folder/API status near the action, via `localProjectNotice`, instead of relying only on a corner toast.
- Live local API project creation created `.codex-fleet/e2e-projects/codex-fleet-e2e-starter`, initialized git, wrote `index.html`, wrote harness files (`AGENTS.md`, `WORKFLOW.md`, `.codex/config.toml`, `.codex/agents/code-scout.toml`, `.agents/skills/repo-harness-review/SKILL.md`), linked Plane project `f5e4b0df-ca61-44fe-aea2-a24904eda5c3`, created labels `human-requested`, `agent-proposed`, `agent-followup`, and wrote the child `.codex-fleet.yml`.
- Live Plane fake success created item `PLN-1`, claimed it, moved Running, created worktree `.codex-fleet/e2e-projects/codex-fleet-e2e-starter/.codex-fleet/workspaces/codex-fleet-e2e-starter/PLN-1`, wrote `.codex-fleet-fake-run.txt`, and ended in `Human Review` with visible Plane comments containing run id, branch, worktree, and verification.
- Live Plane fake failure created item `PLN-2`, claimed it, moved Running, created worktree `.codex-fleet/e2e-projects/codex-fleet-e2e-starter/.codex-fleet/workspaces/codex-fleet-e2e-starter/PLN-2`, and ended in `Rework` with a failure comment.
- Live agent-proposed task verification created source item `PLN-3`; a local proposing runner completed the source run and created `E2E agent proposed follow-up` in `Backlog`, labeled `agent-proposed`, with a source comment linking it to run `a085917e-70b6-483e-949f-0dddfaac2aec`.
- Verification-only local API and headless Brave processes were stopped afterward. An older `/Users/jq/.local/bin/codex-fleet up` process was observed but was not listening on `8790`; it was left untouched.

## Evidence summary

- `make local-check`: passed as part of `make full-local-check`, `172 passed`, doctor `95/100`, budget OK.
- `make full-local-check`: passed, `172 passed` plus smoke `11 passed`.
- `python -m codex_fleet budget --repo . --strict`: passed, all tracked docs/skills/config budget entries OK.
- `mypy src/codex_fleet`: passed with no issues in 35 source files.
- `python -m codex_fleet plane-verify --repo .`: passed all structural checks for branded Plane source, including the dashboard `Run with Codex` control, embedded work-item run panel, issue-detail integration, kanban card/list row run controls, and `/api/runs` client wiring.
- `python -m codex_fleet plane-status --repo .`: Plane runtime installed, Docker available, Docker daemon ready, Plane URL `http://127.0.0.1:8080`, Plane ready `True (HTTP 200 at /)`.
- `python -m codex_fleet plane-check --repo .`: `Plane states: 10`, `Candidate work items: 0`, `Plane workflow states are ready.`
- `python -m codex_fleet plane-bootstrap --repo .`: created Ready demo work items when no Ready candidates existed and reported `Plane saved views existing: 4`.
- Throwaway real Codex CLI memory run: `/tmp/codex-fleet-real-smoke`, run `14d11578-503e-4297-baa7-81632878e02f`, status `human_review`, branch `codex-fleet/CF-1`, worktree `/private/tmp/codex-fleet-real-smoke/.codex-fleet/workspaces/codex-fleet-real-smoke/CF-1`, created `CF-1.fake-run-marker`, and stored `.codex-fleet-codex-cli-output.txt`.
- Throwaway real Codex CLI Plane run: local Plane item `PLN-21` / `75e80381-1e4d-4998-a0cc-850b4bb1cabc`, run `9184b805-346e-4640-a018-9853ebea0fe2`, status `human_review`, branch `codex-fleet/PLN-21`, worktree `/private/tmp/codex-fleet-real-smoke/.codex-fleet/workspaces/codex-fleet-real-smoke/PLN-21`, created `plane-real-codex-smoke.txt` with `real plane codex smoke`, and Plane comments contain the run id, branch, worktree, summary, and verification.
- Plane backend query: saved views `codex-fleet Ready`, `codex-fleet Running`, `codex-fleet Human Review`, and `codex-fleet Rework` exist with `kanban` layout grouped by `state`.
- `python -m codex_fleet up --repo . --fake --once`: official Plane tracker run dispatched successfully, including latest run `PLN-20` in `human_review`. This run installed the branded codex-fleet frontend into the local Plane web container first, then a follow-up `plane-frontend restore` restored the stock frontend and final status detected stock Plane with `Branded installed: False`.
- `python -m codex_fleet up --repo . --fake --fake-fail --once`: official Plane tracker failure run dispatched successfully.
- `npm pack --dry-run`: package contains 73 files including CLI, docs, assets, `src/codex_fleet/plane_local_bootstrap.py`, `patches/plane-codex-fleet.patch`, and bundled `src/codex_fleet/resources/plane-codex-fleet.patch`; Python cache files are excluded.
- Focused CLI/Plane-manager tests: `pytest tests/test_cli_plane.py tests/test_plane_manager.py` passed, `26 passed`.
- Browser/computer-use: separate Chrome profile opened official local Plane at `127.0.0.1:8080`, logged into local Plane, inspected CF-14 Rework, CF-13 Human Review, and CF-15 Ready before claim and Human Review after claim.
- Browser/computer-use: branded Plane fork preview at `127.0.0.1:3000` showed `PLN-18 Ready`, exposed `Run with Codex`, dispatched the fake runner through the loopback API, and displayed `PLN-18` run evidence with events, artifact, branch/worktree path, and `human_review` status.
- Browser/computer-use: branded Plane fork dashboard project selector switched from `codex-fleet` to `codex-fleet-sample`; after refresh, runs came from `/Volumes/Hub/web_startups/codex-fleet-sample/.codex-fleet/workspaces/...`, proving project-scoped dashboard reads.
- Browser/computer-use: branded Plane fork dashboard added `/Volumes/Hub/web_startups/codex-fleet-dashboard-demo`, selected it, applied harness files, updated that project to `ready`, and showed project-scoped run evidence. A run against the non-git demo folder failed cleanly with `Not a git repository`, proving error visibility for invalid workspaces.
- Browser/computer-use: the branded Plane fork frontend was temporarily loaded into the running local Plane web container for inspection against the real local Plane session at `127.0.0.1:8080`. Work item `CF-13` showed the embedded codex-fleet panel with `Status`, `Run with Codex`, latest run `human_review`, branch `codex-fleet/PLN-13`, worktree path `/Volumes/Hub/web_startups/codex-fleet/.codex-fleet/workspaces/codex-fleet/PLN-13`, and visible Plane activity comments. The stock Plane frontend was restored after verification.
- Browser/computer-use: after rebuilding and installing the branded Plane frontend, the local Plane work-item list showed compact inline codex-fleet controls on rows `CF-20` through `CF-15`: logo/status text, fake-runner toggle, `Status`, and `Run with Codex`. The stock Plane frontend and temporary loopback API were restored/stopped after verification.
- Live local API/Plane mapping check: `POST /api/projects` on loopback port `8791` registered `/Volumes/Hub/web_startups/codex-fleet-plane-map-demo`, linked Plane project `2b93021b-96ab-4d25-8608-a775445d6f15`, created missing states, and wrote that folder's `.codex-fleet.yml`.
- New mapped project fake flow: `python -m codex_fleet plane-bootstrap --repo /Volumes/Hub/web_startups/codex-fleet-plane-map-demo` created a demo Ready item; `python -m codex_fleet up --repo /Volumes/Hub/web_startups/codex-fleet-plane-map-demo --fake --once` dispatched it; run store shows `PLN-1|human_review`; fake marker exists at `/Volumes/Hub/web_startups/codex-fleet-plane-map-demo/.codex-fleet/workspaces/codex-fleet-plane-map-demo/PLN-1/.codex-fleet-fake-run.txt`.
- Managed branded frontend install path: `codex-fleet plane-frontend install|status|restore` is implemented with tests and live command evidence. `up` installs the branded frontend by default for loopback Plane after readiness and before daemon startup; `--stock-plane` skips this for upstream Plane debugging. Existing web builds are reused instead of rebuilt on every run.
- Local Plane no-manual-token bootstrap: `python -m codex_fleet plane-local-bootstrap --repo /tmp/codex-fleet-plane-local-bootstrap-check` reused workspace `codex-fleet`, project `321e9f9e-27b4-4d9a-8ed8-2bad0f77bada`, wrote a fresh `.codex-fleet.yml`, and created `/tmp/codex-fleet-plane-local-bootstrap-check/.codex-fleet/secrets.env` with `0600` permissions without printing the API key.
- Local Plane no-login profile/session evidence: `plane-local-bootstrap --repo .` reports workspace `codex-fleet`, project `321e9f9e-27b4-4d9a-8ed8-2bad0f77bada`, and local user `codex-fleet-local@example.local` without printing the API key. A Plane backend query shows `is_onboarded: true`, all onboarding steps true, `is_tour_completed: true`, `is_navigation_tour_completed: true`, and `last_workspace_slug: codex-fleet`.
- Local API login evidence: curl against `GET /api/plane/login` returned `login_status=302`; the resulting cookie can read Plane `/api/users/me/` as `codex-fleet-local@example.local`, `/api/users/me/profile/` as `onboarded=True;last_workspace_id=True`, and `/api/users/me/settings/` as `last=codex-fleet;fallback=codex-fleet`.
- Browser/computer-use no-login evidence: Brave Browser opened the local API login redirect and landed on `127.0.0.1:8080/codex-fleet/projects/321e9f9e-27b4-4d9a-8ed8-2bad0f77bada/issues/` with title `Codex Fleet - Work items`, sidebar workspace `codex-fleet`, project `Codex Fleet`, and visible work items, without using the Plane email/password form.
- Long-running `up` local API path: command tests cover that `up --repo . --fake` starts the loopback API for Plane UI run/status controls, writes a runtime record, and shuts the API down when the daemon exits. Bounded `--once` runs still exit without keeping the API open.

## Requirement checklist

| Requirement | Status | Evidence |
| --- | --- | --- |
| Plane is the serious/default Kanban UI, not the custom local Kanban | Done | `README.md`, `docs/one-command.md`, `docs/plane-fork.md`, and `docs/local-api.md` describe Plane/fork as product UI. CLI hides the old UI behind `internal-smoke-ui`; `tests/test_cli_plane.py::test_ui_command_is_not_public` covers no public `ui` command. |
| Remove or demote custom local Kanban | Done | `src/codex_fleet/local_ui.py` remains only as internal smoke harness; public command is hidden as `internal-smoke-ui`. Docs state it is not the product Kanban path. |
| One command starts local demo: `make up` | Done | `Makefile` target `up` runs `python -m codex_fleet up --repo .` with the real local Codex runner by default. Live `python -m codex_fleet up --repo . --once` started local Plane, reported `Plane ready: True`, installed the branded frontend into `plane-app-web-1`, bootstrapped saved views, reported `Repo readiness: 95/100`, and completed one daemon tick. |
| One command starts local demo: `python -m codex_fleet up --repo .` | Done for bounded run | `up` handles missing config by starting local self-hosted Plane, running `plane-local-bootstrap`, writing config/secrets, installing the branded Plane frontend, and bootstrapping states/views. If local Plane cannot start, it falls back to branded fork onboarding. Latest `--once` run installed the branded Plane frontend into `plane-app-web-1`, reported `Plane ready: True`, `Tracker: plane`, `Ticks: 1`, and exited without leaving a daemon running. |
| Ensure local Plane is available | Done | Branded fork source exists at `.codex-fleet/plane-src` and passes `plane-verify`. Official self-host runtime exists at `.codex-fleet/plane-selfhost`; `plane-status` reports Docker daemon ready and Plane ready at `http://127.0.0.1:8080`. |
| Clone or install Plane locally if missing | Done | `ensure_plane_source` clones pinned upstream Plane to `.codex-fleet/plane-src` and applies the bundled patch. `start_plane` installs/starts official self-host Plane under `.codex-fleet/plane-selfhost`. |
| Pinned Plane source strategy | Done | The default Plane source pin is a packaged release artifact at `src/codex_fleet/resources/plane-source.lock.yml`. `plane-source --status` printed remote `https://github.com/makeplane/plane.git`, requested/current ref `4c1bdd1d625fa3f1141e8af9c15423946472069e`, locked ref `4c1bdd1d625fa3f1141e8af9c15423946472069e`, and patch resource `plane-codex-fleet.patch`. Tests cover lock/default consistency and CLI lock output. |
| Start local Plane with no Plane Cloud/hosted Plane | Done | Official self-host Plane is running locally via Docker at `127.0.0.1:8080`; branded fork preview/local API require no cloud. |
| Wait for Plane readiness | Done | `plane-status` and `up` both observed `Plane ready: True (HTTP 200 at /)`. |
| Bootstrap workspace/project/states/views | Done for local official Plane | `plane-local-bootstrap` creates/reuses local Plane user/workspace/project/API token through Plane's own Docker API container. Workspace/project/states and demo Ready work items are present. `plane-check` reports 10 states and workflow ready. `plane-bootstrap` creates/updates four local saved views through Plane's own Django container because saved views are not exposed on Plane's `/api/v1` API-key surface. Backend evidence shows the four codex-fleet views with `kanban` layout grouped by `state`. |
| Configure codex-fleet to use local Plane project | Done | `.codex-fleet.yml` has `tracker.kind: plane`, local URL, workspace slug, project id, and `$PLANE_API_KEY` ref; `.codex-fleet/secrets.env` provides the local API key without printing it. Live `plane-local-bootstrap` wrote the same shape into a fresh temp control directory. |
| Start daemon with real Codex runner by default | Done | `make up` and `python -m codex_fleet up --repo .` use the real local Codex runner by default. `--fake` remains available only for internal smoke tests; factory tests prove explicit `fake=True` returns `FakeRunner` and default `fake=False` returns the configured Codex runner. |
| Print/open local Plane URL | Done | `up`, `open`, `plane-status`, and `plane-fork-preview` print local URLs. Official Plane was opened in Chrome at `127.0.0.1:8080`. |
| No-login local Plane board session | Done | `plane-local-bootstrap` now marks the local user profile onboarded and sets `last_workspace_id`. Curl evidence shows `/api/plane/login` issues a valid Plane session cookie and Plane profile/settings APIs see the local user as onboarded. Browser/computer-use landed directly on the branded local Plane work-items page without email/password. |
| Branded Plane fork run control | Done for dashboard, detail, and row/card surfaces | `.codex-fleet/plane-src/apps/web/app/codex-fleet/dashboard.tsx` exposes add-project, project selector, scan/apply harness, per-item `Run with Codex`, and `Status` controls. `CodexFleetWorkItemRunCompact` is wired into Plane kanban cards and list rows. `plane-verify` checks dashboard, detail, kanban-card, list-row, and harness-scan integrations; browser verification showed inline run controls in local Plane work-item rows and the rebuilt branded dashboard showed `Scan`, harness status, detected commands, git root, and warnings. |
| Embedded Plane work-item run/status panel | Done and browser-verified | `.codex-fleet/plane-src/apps/web/app/codex-fleet/work-item-run-panel.tsx` adds `Run with Codex` and `Status` controls to work-item detail pages through `.codex-fleet/plane-src/apps/web/core/components/issues/issue-detail/main-content.tsx`. `plane-verify` checks both files, `plane-fork-preview --prepare-only` built successfully, and local API tests cover dispatch/status by `plane_project_id`. Browser verification in the real local Plane session showed `CF-13` with the embedded panel, latest `human_review` run, branch, worktree path, and visible Plane comments; the temporary Docker static-frontend swap was restored afterward. |
| Product local API contract | Done for core run/project/status endpoints | `src/codex_fleet/local_api.py` exposes unauthenticated `GET /health`/`/api/health`/`/api/status`, project list/detail/create endpoints, `POST /api/runs`, `POST /api/runs/next-ready`, `GET /api/events`, legacy dashboard work-item routes, and harness endpoints. Tests cover health, project detail, project registration with automatic Plane mapping/config write, recent events, product run aliases, `project_id`-scoped ready/runs/worktrees, fake success, fake failure, and long-running `up` starting/stopping this API for Plane UI controls. |
| Harness scanner metadata | Done for common local stacks | `plan_harness` now reports git root, dirty state, stack, package manager, install/test/lint/typecheck/build/dev commands, warnings, and status (`blocked`, `needs_setup`, `warnings`, `ready`). Local API harness payload includes this scan object. Tests cover Node/pnpm command detection, Python command detection, dirty warnings, non-git blocked status, and API response shape. Browser/computer-use against the rebuilt branded Plane fork dashboard showed the codex-fleet project with `node`, `npm`, `dirty`, git root, `python -m build`, `npm install`, `make lint`, `make test`, `mypy .`, and the dirty-worktree warning. |
| Added folder becomes a Plane project when possible | Done | `POST /api/projects` now calls Plane project create/detect when the control repo is Plane-backed, stores `plane_workspace_slug`/`plane_project_id`, creates required states, and writes the target folder's `.codex-fleet.yml` tracker config. Tests cover the behavior; a live local API check linked `/Volumes/Hub/web_startups/codex-fleet-plane-map-demo` to Plane project `2b93021b-96ab-4d25-8608-a775445d6f15`. |
| User can create/use work item, move Ready, and codex-fleet claims it | Done | `plane-bootstrap` created CF-15 in Ready; browser showed State `Ready`; `up --fake --once` claimed it and browser activity shows Running, started comment, completion comment, and Human Review. |
| Fake worker runs in isolated git worktree | Done | Official Plane runs created worktrees including `.codex-fleet/workspaces/codex-fleet/PLN-13` through `PLN-23`; fake marker files exist. |
| Running -> Human Review on success | Done | Official Plane CF-13 and CF-15 browser pages show Running activity, success comment with run id/branch/workspace/verification, and final state Human Review. Latest bounded `up` evidence shows `PLN-22` in `human_review`, with run `06454959-ef4f-42ff-95bd-7bd3fa0bbb16`. |
| Running -> Rework on failure | Done | Official Plane CF-14 browser page shows Running activity, failure comment, and final state Rework. SQLite run store has `PLN-14|failed` with event payload `state: Rework`. |
| Visible Plane comments/log/status | Done | Official Plane browser pages show start comments, failure/success comments, state activity, worktree path, branch, and verification text. |
| Duplicate-claim protection | Done | `RunStore.try_claim_item` and orchestrator event flow implemented; tests cover store/orchestrator behavior. |
| Stale claim recovery | Done for local run store/daemon | `RunStore.release_stale_claims` marks expired active claims stale. `FleetDaemon.reconcile_stale_claims` records `stale_claim_released`, marks active runs `stalled`, releases stale Ready items for retry, and moves stale Running items to Rework with a comment. Covered by `tests/test_store.py::test_run_store_releases_only_stale_active_claims`, `tests/test_daemon.py::test_daemon_releases_stale_ready_claim_and_dispatches`, and `tests/test_daemon.py::test_daemon_reports_stale_running_claim_to_rework`. |
| Real Codex runner path | Done for Codex CLI e2e; App Server live e2e not run | `CodexAppServerRunner` remains available via `codex.runner: app-server`; default real path is `codex exec`. Local CLI preflight observed `/opt/homebrew/bin/codex`, `codex-cli 0.128.0`, `codex exec --help` with current `--cd`, `--sandbox`, `-c`, and stdin prompt support, and `codex login status` returned logged in. `doctor` runs this preflight without spending a model run; local `doctor --repo .` reports only expected `missing_ci`. `CodexCliRunner` repeats preflight before launching `codex exec`, no longer passes obsolete `--ask-for-approval`, and tests cover auth failure, contract drift, command contract, fake app-server, and custom command behavior. Live real Codex CLI evidence: memory-backed run `14d11578-503e-4297-baa7-81632878e02f` and Plane-backed run `9184b805-346e-4640-a018-9853ebea0fe2` both completed to `human_review` in isolated throwaway worktrees with output artifacts and changed marker files. |
| No Codex credentials for fake demo | Done | Docs and doctor state fake demo does not require Codex CLI/auth. Official Plane evidence used `FakeRunner`. |
| Tests for Plane bootstrap/state/comments/transitions | Done | `tests/test_plane.py`, `tests/test_plane_bootstrap.py`, `tests/test_plane_views.py`, `tests/test_orchestrator.py`, `tests/test_store.py`, `tests/test_local_work_items.py`, `tests/test_local_api.py`, and `tests/test_plane_local_bootstrap.py` are included in `154 passed`. |
| Integration-style fake Plane API tests | Done | Plane adapter/bootstrap tests use fake HTTP/client behavior where real Plane is too heavy. |
| Command tests for one-command path | Done for bounded command | `tests/test_cli_plane.py` covers hidden UI, Plane status, local Plane bootstrap command, patch apply/export, preview prepare, onboarding URLs, logs, down, project add/list, first-run local Plane bootstrap, configured local Plane auto-start, missing API key automatic bootstrap, stock frontend opt-out, and Docker-daemon start failure. |
| Regression test custom Kanban is not primary UI | Done | `test_ui_command_is_not_public` plus docs demotion. |
| Browser/computer-use verification against local Plane UI | Done | Separate Chrome window/profile opened official self-host Plane at `127.0.0.1:8080`. Browser verified CF-15 Ready before claim, CF-15 Running/Human Review after claim, CF-13 Human Review success details, CF-14 Rework failure details, and the embedded codex-fleet work-item panel in the real local Plane session. Separate Safari browser verification against the rebuilt branded Plane fork at `127.0.0.1:3000` showed the codex-fleet dashboard harness scan UI. |
| Required gate: `make local-check` | Done | Passed with `154 passed`, doctor `95/100`, budget OK. |
| Required gate: `make full-local-check` | Done | Passed with `154 passed` and smoke `9 passed`. |
| Required gate: `python -m codex_fleet budget --repo . --strict` | Done | Passed; all listed entries OK. |
| Required gate: one-command local Plane fake flow | Done for bounded run | Official Plane `up --fake --once` succeeded with `Plane ready: True`, branded frontend install into `plane-app-web-1`, `Tracker: plane`, `Ticks: 1`, `Dispatched: 1`, and latest run `PLN-22` in `human_review`. |
| Required gate: browser verification against local Plane, not custom Kanban | Done | Official self-host Plane browser pages were inspected; this was not the custom Kanban and not the branded fallback dashboard. |
| Required gate: worktree creation evidence | Done | Official Plane worktrees exist under `.codex-fleet/workspaces/codex-fleet/PLN-13` through `PLN-23`; fake marker files exist, including `PLN-23/.codex-fleet-fake-run.txt`. |
| Required gate: Plane comments/status evidence | Done | Official Plane browser shows comments and state activity for CF-13, CF-14, and CF-15. |

## Current Plane status

`python -m codex_fleet plane-status --repo .` reports:

```text
Plane runtime: .codex-fleet/plane-selfhost
Plane installed: True
Docker daemon ready: True (Docker daemon ready (28.3.2).)
Plane URL: http://127.0.0.1:8080
Plane ready: True (HTTP 200 at /)
```

## Security notes

- The local API binds to loopback by default and refuses remote bind unless explicitly unsafe.
- The no-login onboarding token is stored under `.codex-fleet/secrets/local_api_token`; onboarding URLs place it in the browser URL fragment, which is not sent to the static server but can remain in local browser history/screenshots. The local Plane session bridge currently accepts the same token as a loopback query parameter so the API can issue a normal Plane session cookie and redirect to the board; this must stay loopback-only and should not be printed in logs.
- Plane/Codex tokens are not printed or committed; `.codex-fleet.yml` uses `$PLANE_API_KEY` by default for official Plane.
- `plane-local-bootstrap` captures the generated/reused Plane API key from the Docker shell output and writes it to `.codex-fleet/secrets.env` with `0600` permissions; command output reports workspace/project/user but not the key.
- When Add Project links another folder to local Plane, codex-fleet writes that folder's `.codex-fleet.yml` tracker section and stores the local Plane API key in that folder's `.codex-fleet/secrets.env` with `0600` permissions. The key is not returned to the browser.
- Browser verification uses the local single-user session bridge rather than a documented local Plane password.
- Plane web never shells out or runs Codex directly. It calls codex-fleet API; codex-fleet owns path validation, worktree creation, runner dispatch, comments, and final state transitions.
