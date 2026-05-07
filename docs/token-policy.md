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

- RTK-style command compression: filter repeated progress lines, keep errors and command metadata, and always save raw output first.
- Caveman-style doc compression: summarize long prose, plans, or handoffs only. Do not use it for exact code, exact error traces, or security evidence.
- Repomix-style repo packing: produce targeted file lists, tree summaries, token counts, and explicit exclusions. Do not dump the whole repo by default.

RTK, Caveman, and Repomix are optional inspirations or future integrations. codex-fleet must run cleanly without them installed.
