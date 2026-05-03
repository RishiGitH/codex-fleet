# codex-fleet

**Jira for Codex agents — local, repo-aware, and powered by Plane + Symphony-style orchestration.**

codex-fleet is a local control plane for running Codex agents from issue-board work. It is designed to give a repo the practical workflow of Linear/Jira plus OpenAI Symphony-style agent execution, without requiring teams to adopt Linear or a hosted agent platform.

The intended user experience is:

```bash
codex-fleet up --repo ~/projects/my-app
```

That command will eventually:

1. connect to or start a local Plane instance,
2. create a project and agent-ready workflow states,
3. run a repo doctor,
4. propose harness files such as `AGENTS.md`, `WORKFLOW.md`, `.codex/config.toml`, and focused skills,
5. dispatch Ready work items to isolated Codex worktrees,
6. stream progress back to Plane comments,
7. open a GitHub PR or leave a local diff,
8. move the task to Human Review.

## Current status

Phase 1 foundation is implemented as a contributor-friendly Python package and Codex-native repo harness. The first phase includes:

- CLI skeleton with `doctor`, `status`, `init-harness`, `run-once`, and `token-budget` commands.
- Repo doctor scanner for agent-readiness checks.
- Tracker abstraction with in-memory tracker and Plane REST client skeleton.
- Symphony-style orchestrator core that claims one issue, prepares a worktree, runs a runner, and updates state.
- Fake Codex runner for deterministic tests.
- Worktree/path safety helpers.
- Codex project config, subagents, and skills for developing this repo with Codex.
- Tests and documentation.

The real Codex App Server runner, full Plane bootstrap, and GitHub PR flow are planned next.

## Why not fork Plane or vendor Symphony?

Plane is used as an external service through its API. We do not fork or vendor Plane in this repo because Plane is large, already self-hostable, and AGPL-licensed. codex-fleet talks to Plane through a narrow adapter.

OpenAI Symphony is used as an architectural reference: poll tracker, isolate workspaces, run Codex App Server, track retries/stalls, and return work for human review. We implement a lean Python version so the product is easy to install, test, and extend.

## Quick start for contributors

```bash
git clone https://github.com/RishiGitH/codex-fleet.git
cd codex-fleet
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
python -m codex_fleet doctor --repo .
```

You can also run:

```bash
python -m codex_fleet status --repo .
python -m codex_fleet token-budget --repo .
```

## Development principles

- Keep the product plug-and-play for real repos.
- Keep the repo itself easy for Codex to understand.
- Prefer explicit adapters over deep forks.
- Keep critical state transitions deterministic in the daemon.
- Use Codex subagents for exploration, architecture, implementation, harness review, security review, and token review.
- Default to safe execution: no auto-merge, no deploy, no full filesystem access.

## Roadmap

### Phase 1 — foundation

- [x] Repo harness and docs
- [x] CLI skeleton
- [x] Doctor scanner
- [x] Tracker abstraction
- [x] In-memory tracker
- [x] Plane client skeleton
- [x] Worktree manager
- [x] Fake runner
- [x] Orchestrator core
- [x] Tests

### Phase 2 — real local workflow

- [ ] Plane Docker/bootstrap command
- [ ] Plane project/state creation
- [ ] Real Codex App Server JSON-RPC runner
- [ ] Event/log persistence
- [ ] Ready → Running → Human Review loop against real Plane

### Phase 3 — PR and hardening workflow

- [ ] GitHub branch/PR creation
- [ ] CI status collection
- [ ] Repo doctor apply mode
- [ ] Generated customer repo harness
- [ ] Codex plugin/MCP helper

### Phase 4 — robust local product

- [ ] Multi-repo support
- [ ] Crash recovery
- [ ] Retry/stall dashboards
- [ ] Workspace cleanup policies
- [ ] Security hardening
- [ ] Installer docs

## License

Apache-2.0. See [LICENSE](LICENSE).
