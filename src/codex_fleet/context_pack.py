from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal

from codex_fleet.budget import estimate_tokens_for_bytes

DEFAULT_EXCLUDES = (
    ".git",
    ".venv",
    ".codex-fleet",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "codex_fleet.egg-info",
)
DEFAULT_EXCLUDE_GLOBS = (
    ".agents/skills/impeccable/scripts/**",
    ".agents/skills/impeccable/reference/**",
)
ContextPackProfile = Literal["minimal", "task", "full"]


@dataclass(frozen=True)
class ContextPackResult:
    out_dir: Path
    file_count: int
    estimated_tokens: int
    exclusions: tuple[str, ...]
    profile: str


def write_context_pack(
    repo: Path,
    out_dir: Path,
    *,
    profile: ContextPackProfile = "minimal",
    includes: tuple[str, ...] = (),
    max_tokens: int | None = None,
) -> ContextPackResult:
    repo = repo.expanduser().resolve()
    out_dir = out_dir.expanduser()
    if not out_dir.is_absolute():
        out_dir = repo / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    files = tuple(_iter_files(repo, profile=profile, includes=includes))
    tree_text = _render_tree(repo, files)
    docs_text = _render_docs(repo)
    sources_text = _render_sources(repo, files, profile=profile, max_tokens=max_tokens)
    total_bytes = sum(len(text.encode("utf-8")) for text in (tree_text, docs_text, sources_text))
    estimated_tokens = estimate_tokens_for_bytes(total_bytes)
    exclusions = (*DEFAULT_EXCLUDES, *DEFAULT_EXCLUDE_GLOBS)

    (out_dir / "tree.md").write_text(tree_text)
    (out_dir / "docs.md").write_text(docs_text)
    (out_dir / "sources.md").write_text(sources_text)
    (out_dir / "metadata.json").write_text(
        json.dumps(
            {
                "repo": str(repo),
                "generated_at": datetime.now(UTC).isoformat(),
                "file_count": len(files),
                "estimated_tokens": estimated_tokens,
                "profile": profile,
                "includes": list(includes),
                "max_tokens": max_tokens,
                "exclusions": list(exclusions),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    return ContextPackResult(
        out_dir=out_dir,
        file_count=len(files),
        estimated_tokens=estimated_tokens,
        exclusions=exclusions,
        profile=profile,
    )


def _iter_files(repo: Path, *, profile: ContextPackProfile, includes: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for path in sorted(repo.rglob("*")):
        rel = str(path.relative_to(repo))
        rel_parts = path.relative_to(repo).parts
        if any(part in DEFAULT_EXCLUDES for part in rel_parts):
            continue
        if any(fnmatch(rel, pattern) for pattern in DEFAULT_EXCLUDE_GLOBS):
            continue
        if path.is_file():
            if profile == "task" and includes and not _included_for_task(rel, includes):
                continue
            files.append(path)
    return files


def _render_tree(repo: Path, files: tuple[Path, ...]) -> str:
    lines = ["# File tree", ""]
    for path in files:
        rel = path.relative_to(repo)
        lines.append(f"- {rel}")
    lines.append("")
    return "\n".join(lines)


def _render_docs(repo: Path) -> str:
    lines = ["# Selected docs", ""]
    for rel in ["AGENTS.md", "WORKFLOW.md", "README.md"]:
        _append_doc_summary(lines, repo, rel)
    docs_dir = repo / "docs"
    if docs_dir.exists():
        for path in sorted(docs_dir.glob("*.md")):
            _append_doc_summary(lines, repo, str(path.relative_to(repo)))
    return "\n".join(lines)


def _append_doc_summary(lines: list[str], repo: Path, rel: str) -> None:
    path = repo / rel
    if not path.exists():
        return
    heading = _first_heading(path)
    token_estimate = estimate_tokens_for_bytes(path.stat().st_size)
    lines.append(f"- {rel} ({token_estimate} rough tokens): {heading}")


def _first_heading(path: Path) -> str:
    for line in path.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or "(untitled)"
    return "(no heading)"


def _render_sources(
    repo: Path,
    files: tuple[Path, ...],
    *,
    profile: ContextPackProfile,
    max_tokens: int | None,
) -> str:
    lines = ["# Selected source and test files", ""]
    prefixes = ("src/", "tests/", "scripts/")
    suffixes = (".py", ".js", ".toml", ".yml", ".yaml")
    running_tokens = 0
    for path in files:
        rel = str(path.relative_to(repo))
        if rel.startswith(prefixes) or rel.endswith(suffixes):
            tokens = estimate_tokens_for_bytes(path.stat().st_size)
            if max_tokens is not None and running_tokens + tokens > max_tokens:
                lines.append(f"- {rel} ({tokens} rough tokens, omitted by max token cap)")
                continue
            running_tokens += tokens
            lines.append(f"- {rel} ({tokens} rough tokens)")
            if profile == "full" and path.suffix in {".py", ".toml", ".yml", ".yaml"}:
                lines.append("")
                lines.append("```text")
                lines.append(path.read_text(errors="replace"))
                lines.append("```")
                lines.append("")
    lines.append("")
    return "\n".join(lines)


def _included_for_task(rel: str, includes: tuple[str, ...]) -> bool:
    guidance = {"AGENTS.md", "WORKFLOW.md", "README.md", "pyproject.toml", "package.json"}
    if rel in guidance or rel.startswith("docs/") or rel.startswith(".codex/agents/"):
        return True
    return any(fnmatch(rel, pattern) for pattern in includes)
