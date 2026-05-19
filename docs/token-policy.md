# Token policy

codex-fleet should stay efficient for both humans and agents. Token tools are guardrails for context quality, not a replacement for raw artifacts or targeted repo inspection.

## Model and review defaults

- Use cheap read-only scouting for file mapping, search, and low-risk context gathering.
- Use stronger review settings for architecture, orchestration state machines, token handling, path safety, shell execution, Docker mounts, and security-sensitive changes.
- Keep `AGENTS.md` concise and move durable detail into docs or focused skills.
- Keep skills narrow, trigger-specific, and short enough to load only when useful.

## Context hygiene

- Prefer targeted search and file reads over whole-repo dumps.
- Generate a small context pack only when a task spans enough files that a curated map helps.
- Never compress exact code before editing it. Read the source file directly before changing code.
- Do not paste large logs into prompts. Store them as artifacts and provide the smallest useful summary.

## Raw artifacts

Raw logs and exact command output are the source of truth. Compressed output, summaries, and context packs are only views for agent context.

Security-sensitive logs must not be summarized destructively unless the raw artifact is preserved outside the prompt. Do not write secrets into repo files, Plane comments, or summaries.

## Budget checks

`python -m codex_fleet budget --repo .` scans guidance files, docs, skills, and Codex agent configs with rough token estimates.

Use `--strict` in local validation when a change intentionally tightens token budgets. The default command should report useful output without failing first-run UX.

## Compression strategy

- Native capture compression: always available, filters repeated output, keeps errors and command metadata, and always saves raw output first.
- RTK-style command compression: optional external helper for large command output. Use only after raw output is saved.
- Caveman-style doc compression: optional helper for long prose, plans, or handoffs only. Do not use it for exact code, exact error traces, or security evidence.
- Repomix-style repo packing: optional inspiration for broad repo maps. Prefer codex-fleet `pack-context --profile minimal` or `--profile task` first.
- Graphify-style architecture maps: optional for large or unfamiliar repos. Do not use it for routine edits.

RTK, Caveman, and Repomix are optional inspirations or future integrations. codex-fleet must run cleanly without them installed.

## Context pack profiles

- `minimal`: guidance docs, command map, tree, and source/test inventory. Default.
- `task`: minimal pack plus explicit include globs for the files relevant to a task.
- `full`: rare architecture/debugging mode. It may include source excerpts, but still excludes generated, private, cache, and heavy skill implementation assets.

Keep large frontend skill assets such as Impeccable opt-in for UI work; do not let them dominate ordinary backend or orchestration context.
