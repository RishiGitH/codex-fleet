---
name: change-verification
description: Choose focused verification commands and preserve proof for Codex changes.
---

# Change Verification

Use this skill before reporting a code change complete.

Check:

- changed files and their nearest tests
- whether docs, prompts, skills, logs, or context packing changed
- whether orchestration, runner, workspace, tracker, API, or security boundaries changed
- whether a focused test is enough or a broader local check is needed
- whether raw command output should be captured as an artifact before summarizing

Return:

- commands run
- result of each command
- raw artifact path when command output is large or security-sensitive
- remaining risk if a broader command was skipped

Prefer focused tests first, then `make local-check` when the change touches shared behavior.
