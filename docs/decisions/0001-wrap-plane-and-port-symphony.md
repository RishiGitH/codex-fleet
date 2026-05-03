# Decision: wrap Plane and port Symphony ideas

## Context

codex-fleet needs a local issue board and a Symphony-style runner.

Plane already provides a strong open-source project board with work items, states, comments, and self-hosting. OpenAI Symphony provides the orchestration pattern but is a reference implementation and currently Linear-oriented.

## Decision

- Use Plane as an external service through its API.
- Do not fork or vendor Plane in this repo for the MVP.
- Implement a lean Python orchestration layer inspired by Symphony.
- Do not vendor OpenAI Symphony directly in Phase 1.

## Consequences

Good:

- Faster setup.
- Smaller repo.
- Cleaner licensing boundary.
- Easier Python packaging.
- Easier tests.

Tradeoffs:

- We must implement the Codex App Server runner ourselves.
- We must keep Plane API compatibility current.
- Deep Plane UI customizations require a future fork or extension strategy.
