from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from pathlib import Path

DEFAULT_DOC_LIMIT = 8_000
DEFAULT_SKILL_LIMIT = 4_000
DEFAULT_AGENT_LIMIT = 4_000


@dataclass(frozen=True)
class BudgetEntry:
    path: str
    bytes: int
    estimated_tokens: int
    limit: int

    @property
    def ok(self) -> bool:
        return self.estimated_tokens <= self.limit

    @property
    def status(self) -> str:
        return "OK" if self.ok else "too large"


@dataclass(frozen=True)
class BudgetSummary:
    entries: tuple[BudgetEntry, ...]

    @property
    def too_large_count(self) -> int:
        return sum(1 for entry in self.entries if not entry.ok)

    @property
    def ok(self) -> bool:
        return self.too_large_count == 0


def estimate_tokens_for_bytes(size: int) -> int:
    return ceil(size / 4)


def estimate_tokens(text: str) -> int:
    return estimate_tokens_for_bytes(len(text.encode("utf-8")))


def file_size(path: Path) -> int:
    if not path.exists():
        return 0
    return path.stat().st_size


def scan_budget(
    repo: Path,
    *,
    default_doc_limit: int = DEFAULT_DOC_LIMIT,
    skill_limit: int = DEFAULT_SKILL_LIMIT,
    agent_limit: int = DEFAULT_AGENT_LIMIT,
) -> BudgetSummary:
    repo = repo.expanduser().resolve()
    entries: list[BudgetEntry] = []

    for rel in _budget_paths(repo):
        path = repo / rel
        limit = _limit_for_path(rel, default_doc_limit, skill_limit, agent_limit)
        size = file_size(path)
        entries.append(
            BudgetEntry(
                path=rel,
                bytes=size,
                estimated_tokens=estimate_tokens_for_bytes(size),
                limit=limit,
            )
        )

    return BudgetSummary(entries=tuple(entries))


def _budget_paths(repo: Path) -> tuple[str, ...]:
    paths = ["AGENTS.md", "WORKFLOW.md", "README.md"]

    docs_dir = repo / "docs"
    if docs_dir.exists():
        paths.extend(str(path.relative_to(repo)) for path in sorted(docs_dir.glob("*.md")))

    skills_dir = repo / ".agents" / "skills"
    if skills_dir.exists():
        paths.extend(
            str(path.relative_to(repo)) for path in sorted(skills_dir.glob("*/SKILL.md"))
        )

    agents_dir = repo / ".codex" / "agents"
    if agents_dir.exists():
        paths.extend(str(path.relative_to(repo)) for path in sorted(agents_dir.glob("*.toml")))

    return tuple(paths)


def _limit_for_path(rel: str, default_doc_limit: int, skill_limit: int, agent_limit: int) -> int:
    if rel.startswith(".agents/skills/"):
        return skill_limit
    if rel.startswith(".codex/agents/"):
        return agent_limit
    return default_doc_limit
