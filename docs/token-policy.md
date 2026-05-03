# Token policy

codex-fleet should stay efficient for both humans and agents.

## Defaults

- Use cheap or fast models for exploration.
- Use stronger models for architecture and security review.
- Keep `AGENTS.md` concise.
- Keep skills focused and trigger-specific.
- Store raw logs as artifacts instead of pasting huge logs into prompts.

## Context hygiene

Prefer targeted file reads and search over whole-repo dumps.

When a task spans many files, generate a small context pack with only relevant paths.

## File size checks

`python -m codex_fleet budget --repo .` reports guidance file sizes. This is intentionally simple in Phase 1 and will become a stricter token budget checker later.

## Compression tools

Future integrations may support RTK for shell output compression and Repomix for targeted repo packs. These should be optional helpers, not required dependencies for a clean install.

## Rule

Compressed context is a view. Raw logs and exact outputs remain the source of truth.
