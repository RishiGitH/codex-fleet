# Plane fork guidance

codex-fleet uses Plane as the product board, but the default product experience requires a branded local Plane fork.

## Source strategy

Use a pinned source checkout, not an uncontrolled vendored copy.

Preferred shape:

```text
third_party/plane -> git submodule pointing at the codex-fleet Plane fork
```

The fork should start from upstream Plane and stay shallow. Use upstream Plane as the source of truth for board behavior, data models, and migrations.

If a fork URL is not available yet, runtime clone support may use `.codex-fleet/plane-src` temporarily, but the product target remains a pinned fork/submodule.

Clone or inspect the runtime source with:

```bash
codex-fleet plane-source --repo . --url https://github.com/<org>/plane-codex-fleet.git --ref <pinned-commit>
codex-fleet plane-source --repo . --status
codex-fleet plane-verify --repo .
```

`plane-source` writes `.codex-fleet/plane-src/.codex-fleet-plane-source.yml` with the source URL, requested ref, and current commit. This is a runtime checkout for local customization work; product releases should still pin the fork as a submodule when the fork URL is stable.

Until a public fork URL exists, the codex-fleet Plane customization is preserved as `patches/plane-codex-fleet.patch` and bundled into the Python package as `codex_fleet.resources/plane-codex-fleet.patch`. `plane-source` applies this patch automatically after cloning when the checkout is not already customized. Maintainers can update or reapply it explicitly:

```bash
codex-fleet plane-patch export --repo .
codex-fleet plane-patch apply --repo .
```

The default source pin is a packaged release artifact:

```text
src/codex_fleet/resources/plane-source.lock.yml
```

It records the upstream Plane URL, exact commit ref, patch resource, and runtime strategy. `plane-source --status` prints the lock metadata next to the local checkout manifest so maintainers can see whether `.codex-fleet/plane-src` was cloned from the expected pin. Keep this lock, `DEFAULT_PLANE_SOURCE_REF`, and the exported patch in sync when rebasing the fork.

Use `--no-apply` with `plane-source` only when inspecting a clean upstream Plane checkout.

`plane-verify` checks the local source for codex-fleet branding, manifests, AGENTS guidance, onboarding and dashboard routes, the local API client, and the embedded work-item run panel. It is a structural check only; run the Plane web type/build checks when Node and pnpm are installed.

The branded preview server also prepares the web build automatically when `apps/web/build/client/index.html` is missing:

1. clone/pin Plane source under `.codex-fleet/plane-src`
2. apply `patches/plane-codex-fleet.patch`
3. run `pnpm install --frozen-lockfile`
4. run `pnpm --filter web build`

This keeps the first-run path reproducible without committing a full Plane checkout or generated web build.

To prepare the build without opening a browser or keeping preview/API ports open:

```bash
codex-fleet plane-fork-preview --repo . --prepare-only
make plane-fork-prepare
```

For the local self-hosted Plane runtime, codex-fleet can install the branded
web build into the running Plane web container:

```bash
codex-fleet plane-frontend install --repo .
codex-fleet plane-frontend status --repo .
codex-fleet plane-frontend restore --repo .
```

`install` copies the current Plane web static files from the container to
`.codex-fleet/plane-selfhost/web-static-stock-backup/`, replaces only
`/usr/share/nginx/html` inside the local Plane web container, and reloads nginx.
It does not change Plane backend services, auth, models, migrations, or data.
`up` runs this install step automatically for loopback Plane unless
`--stock-plane` is passed.

The current runtime fork includes:

- `/codex-fleet/onboarding` for local token/project/harness setup.
- `/codex-fleet/dashboard` for local projects, Ready work, recent runs, worktree paths, run evidence, local fallback task creation, and a `Run Ready` action.
- an embedded codex-fleet run/status panel in Plane work-item detail pages. The panel dispatches by Plane project UUID so it can map back to the registered local folder.

The dashboard task form is only for the no-login memory fallback and persists through codex-fleet's local SQLite store. In Plane-backed mode, Plane remains the board of record and task creation happens in Plane.

## Allowed customization

Keep changes focused on local codex-fleet product UX:

- logo, favicon, manifest, app name. Use `assets/brand/codex-fleet-logo.svg` as the canonical source.
- navigation/sidebar branding
- local onboarding
- no-login local bootstrap flow
- add-local-folder project flow
- local fallback task creation while Plane auth/project setup is not available
- run with Codex action
- run status panel
- worktree/branch/log display
- empty states for local projects and agent runs

## Avoid

Do not change these without a written blocker:

- Plane core models
- migrations
- hosted/cloud flows
- auth internals outside explicit local mode
- unrelated product areas
- broad dependency rewrites

## Integration boundary

Plane web calls codex-fleet API for agent actions.

Plane web must not:

- shell out
- start Codex directly
- create worktrees
- inspect arbitrary local files
- mark a run successful by itself

codex-fleet API must validate every project path and expose structured operations only.

## AGENTS.md for the fork

When `third_party/plane` is added as a submodule, add an `AGENTS.md` inside that checkout with:

```text
This Plane fork is customized only for codex-fleet local product UX.
Keep the fork shallow and rebaseable.
Do not alter auth, models, migrations, or hosted Plane behavior unless a documented blocker requires it.
Plane UI calls the loopback codex-fleet API for agent actions. Plane must not shell out or run Codex.
```
