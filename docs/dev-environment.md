# Development environment

This repo works best with a small, predictable local toolchain. The goal is to make Codex and humans faster without making every task depend on a large stack of optional tools.

## Required baseline

- Codex CLI: real agent runs use `codex exec`.
- Git: worktrees, branches, and local status checks.
- Python 3.11 or newer: package and CLI runtime.
- Docker: local Plane self-host flow.
- Node 18 or newer plus `pnpm`: branded Plane frontend preparation.
- GitHub CLI: optional PR and repository workflows.

Install the repo environment with:

```bash
make install
```

Run the normal validation gate with:

```bash
make local-check
```

## Recommended helpers

Install the focused helper set with Homebrew:

```bash
brew install fd bat git-delta hyperfine tokei yq direnv mise
```

Use these for day-to-day work:

- `fd`: fast file discovery when `rg --files` is too broad.
- `bat`: readable source previews.
- `delta`: cleaner diffs when reviewing Codex changes.
- `hyperfine`: benchmark CLI paths such as doctor, budget, context pack, and Plane checks.
- `tokei`: quick language and size inventory before larger refactors.
- `yq`: inspect YAML configs without hand parsing.
- `direnv`: optional repo-local shell environment loading.
- `mise`: optional version pinning for Python and Node.

## Token and context helpers

RTK is useful for large command output, but raw logs remain the source of truth.

Use capture when output may be large:

```bash
python -m codex_fleet capture --repo . --compress auto -- make local-check
```

Use context packs instead of whole-repo dumps:

```bash
python -m codex_fleet pack-context --repo . --out .codex-fleet/context --profile minimal
python -m codex_fleet pack-context --repo . --out .codex-fleet/context --profile task --include 'src/codex_fleet/*.py'
```

Default preference:

- `minimal`: normal investigation.
- `task`: implementation work spanning multiple files.
- `full`: rare architecture/debugging pass.

Do not install Caveman, Repomix, or Graphify as default dependencies. Add them later only if the native workflow becomes painful.

## Optional direnv setup

This repo includes `.envrc.example` only. Copy it to `.envrc` if you want `direnv` to activate the local Python environment automatically:

```bash
cp .envrc.example .envrc
direnv allow
```

Do not put secrets in `.envrc`; keep local secrets under `.codex-fleet/` or your shell secret manager.
