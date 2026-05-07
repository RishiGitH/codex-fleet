# codex-fleet Product Context

register: product

## Product Purpose

codex-fleet is a local control plane for running Codex agents from work-item boards. A developer runs one command in a local repo, opens a branded local Plane workspace, adds or creates projects, moves work items to Ready, and reviews Codex output with evidence.

## Users

- Developers who already use local repos and want Codex work to be visible, reviewable, and repeatable.
- Technical founders and small teams who need a local, no-cloud agent board before adopting hosted workflow tools.
- Agent operators who need worktree isolation, clear run status, comments, and human review before merge.

## Product Promise

Run `codex-fleet up --repo .`, open the dashboard, create or link a project from the board, add tasks, move them to Ready, and watch local Codex runs produce worktree-backed results.

## Principles

- Plane is the product UI. codex-fleet is the local automation engine.
- Keep the first run calm: no Plane Cloud, no generic signup ceremony, no hosted dependency.
- Put normal actions inside Plane: project creation, work item creation, Ready movement, run review.
- Reserve fallback setup pages for local API/token recovery, not the main flow.
- Keep execution safe: loopback API, validated paths, isolated git worktrees, human review before Done.

## Tone

Operational, clear, premium developer tooling. The product should feel closer to Linear, Raycast, and Stripe quality than a toy demo.

## Anti-References

- Generic dark SaaS dashboard.
- Decorative gradients and glass cards.
- Exposed test/fake-runner concepts in the product path.
- Setup screens that compete with the actual board.
