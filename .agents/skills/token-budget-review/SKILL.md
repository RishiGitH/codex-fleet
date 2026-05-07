---
name: token-budget-review
description: Review token budget risk when AGENTS.md, WORKFLOW.md, skills, large docs, prompts, runner logs/events, capture artifacts, or context packing behavior changes.
---

# Token Budget Review

Use this skill when a change can increase context size or weaken raw-output preservation.

Check:

- repeated instructions across AGENTS, WORKFLOW, docs, and skills
- oversized docs, skills, prompts, logs, and generated summaries
- broad repo dumps where targeted search or a small context pack would work
- whether raw command output is saved before summarization
- whether compressed summaries are safe for the task
- whether exact code or security-sensitive evidence is being compressed unsafely

Return concrete reductions and the smallest safe context boundary. Do not remove safety, security, setup, or workflow instructions.
