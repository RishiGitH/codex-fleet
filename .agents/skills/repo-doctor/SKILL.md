---
name: repo-doctor
description: Audit a repository for Codex readiness before running automated work.
---

# Repo Doctor

Use this skill when asked to check whether a repo is ready for Codex agents.

Return:

- readiness score
- missing guidance files
- missing setup or test commands
- missing environment documentation
- unclear architecture areas
- Python/venv command mismatches that will confuse agents
- optional token/context helper availability without making them required
- recommended hardening tasks

Prefer concrete tasks over generic advice.
