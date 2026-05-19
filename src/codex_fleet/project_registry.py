from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProjectRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class LocalProject:
    id: int
    name: str
    slug: str
    repo_path: Path
    git_root: Path | None
    plane_workspace_slug: str | None
    plane_project_id: str | None
    harness_status: str
    runner_mode: str
    codex_settings: dict[str, Any]


DEFAULT_CODEX_SETTINGS: dict[str, Any] = {
    "runner_mode": "codex",
    "default_model": "gpt-5.5",
    "reasoning_effort": "low",
    "approval_policy": "never",
    "sandbox_mode": "workspace-write",
    "max_parallel_agents": 3,
    "max_task_depth": 2,
    "max_child_tasks_per_run": 8,
    "max_total_agent_created_tasks_per_parent": 20,
    "job_timeout_seconds": 1200,
    "automation_mode": "assisted",
    "agent_task_mode": "review_and_approve",
    "agent_role": "worker",
    "skill_policy": "minimal",
    "max_prompt_protocol_tokens": 800,
    "max_plane_comment_chars": 4000,
    "subagents": {
        "code_scout": {"model": "gpt-5.4-mini", "reasoning_effort": "medium", "sandbox_mode": "read-only"},
        "implementer": {"model": "gpt-5.5", "reasoning_effort": "low", "sandbox_mode": "workspace-write"},
        "harness_reviewer": {"model": "gpt-5.4-mini", "reasoning_effort": "high", "sandbox_mode": "read-only"},
        "security_reviewer": {"model": "gpt-5.5", "reasoning_effort": "medium", "sandbox_mode": "read-only"},
        "token_reviewer": {"model": "gpt-5.4-mini", "reasoning_effort": "high", "sandbox_mode": "read-only"},
    },
}


def default_project_registry_path(root: Path) -> Path:
    return root.expanduser().absolute() / ".codex-fleet" / "projects.sqlite3"


class ProjectRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                create table if not exists projects (
                    id integer primary key autoincrement,
                    name text not null,
                    slug text not null unique,
                    repo_path text not null unique,
                    git_root text,
                    plane_workspace_slug text,
                    plane_project_id text,
                    harness_status text not null default 'unknown',
                    runner_mode text not null default 'codex',
                    codex_settings text not null default '{}',
                    created_at text default current_timestamp,
                    updated_at text default current_timestamp
                )
                """
            )
            columns = {row["name"] for row in db.execute("pragma table_info(projects)").fetchall()}
            if "codex_settings" not in columns:
                db.execute("alter table projects add column codex_settings text not null default '{}'")
            db.execute("update projects set runner_mode = 'codex' where runner_mode = 'fake'")

    def add_project(
        self,
        repo_path: Path,
        *,
        name: str | None = None,
        slug: str | None = None,
        plane_workspace_slug: str | None = None,
        plane_project_id: str | None = None,
        runner_mode: str = "codex",
        codex_settings: dict[str, Any] | None = None,
    ) -> LocalProject:
        resolved = validate_project_path(repo_path)
        project_name = name or resolved.name
        git_root = discover_git_root(resolved)
        settings = normalize_codex_settings({**(codex_settings or {}), "runner_mode": runner_mode})
        with self._connect() as db:
            existing = db.execute("select slug from projects where repo_path = ?", (str(resolved),)).fetchone()
            project_slug = slug or (str(existing["slug"]) if existing is not None else _unique_project_slug(db, slugify(project_name)))
            db.execute(
                """
                insert into projects (
                    name,
                    slug,
                    repo_path,
                    git_root,
                    plane_workspace_slug,
                    plane_project_id,
                    runner_mode,
                    codex_settings
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(repo_path) do update set
                    name = excluded.name,
                    slug = excluded.slug,
                    git_root = excluded.git_root,
                    plane_workspace_slug = coalesce(excluded.plane_workspace_slug, projects.plane_workspace_slug),
                    plane_project_id = coalesce(excluded.plane_project_id, projects.plane_project_id),
                    runner_mode = excluded.runner_mode,
                    codex_settings = excluded.codex_settings,
                    updated_at = current_timestamp
                """,
                (
                    project_name,
                    project_slug,
                    str(resolved),
                    str(git_root) if git_root else None,
                    plane_workspace_slug,
                    plane_project_id,
                    str(settings["runner_mode"]),
                    json.dumps(settings, sort_keys=True),
                ),
            )
            row = db.execute("select * from projects where repo_path = ?", (str(resolved),)).fetchone()
        if row is None:
            raise ProjectRegistryError(f"Failed to register project: {resolved}")
        return _project_from_row(row)

    def list_projects(self) -> list[LocalProject]:
        with self._connect() as db:
            rows = db.execute("select * from projects order by name, id").fetchall()
        return [_project_from_row(row) for row in rows]

    def get_project(self, project_id: int) -> LocalProject | None:
        with self._connect() as db:
            row = db.execute("select * from projects where id = ?", (project_id,)).fetchone()
        return _project_from_row(row) if row is not None else None

    def get_project_by_plane_id(self, *, workspace_slug: str, plane_project_id: str) -> LocalProject | None:
        with self._connect() as db:
            row = db.execute(
                """
                select * from projects
                where plane_workspace_slug = ? and plane_project_id = ?
                """,
                (workspace_slug, plane_project_id),
            ).fetchone()
        return _project_from_row(row) if row is not None else None

    def update_harness_status(self, project_id: int, status: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update projects
                set harness_status = ?, updated_at = current_timestamp
                where id = ?
                """,
                (status, project_id),
            )

    def update_plane_mapping(self, project_id: int, *, workspace_slug: str, project_id_in_plane: str) -> LocalProject:
        with self._connect() as db:
            db.execute(
                """
                update projects
                set
                    plane_workspace_slug = ?,
                    plane_project_id = ?,
                    updated_at = current_timestamp
                where id = ?
                """,
                (workspace_slug, project_id_in_plane, project_id),
            )
            row = db.execute("select * from projects where id = ?", (project_id,)).fetchone()
        if row is None:
            raise ProjectRegistryError("Project not found.")
        return _project_from_row(row)

    def get_project_settings(self, project_id: int) -> dict[str, Any] | None:
        project = self.get_project(project_id)
        return project.codex_settings if project is not None else None

    def update_project_settings(self, project_id: int, settings: dict[str, Any]) -> LocalProject:
        current = self.get_project(project_id)
        if current is None:
            raise ProjectRegistryError("Project not found.")
        merged = normalize_codex_settings({**current.codex_settings, **settings})
        with self._connect() as db:
            db.execute(
                """
                update projects
                set runner_mode = ?, codex_settings = ?, updated_at = current_timestamp
                where id = ?
                """,
                (str(merged["runner_mode"]), json.dumps(merged, sort_keys=True), project_id),
            )
            row = db.execute("select * from projects where id = ?", (project_id,)).fetchone()
        if row is None:
            raise ProjectRegistryError("Project not found.")
        return _project_from_row(row)


def validate_project_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise ProjectRegistryError(f"Project path does not exist: {resolved}")
    if not resolved.is_dir():
        raise ProjectRegistryError(f"Project path is not a directory: {resolved}")
    if resolved == Path(resolved.anchor):
        raise ProjectRegistryError("Refusing to register the filesystem root as a project.")
    return resolved


def discover_git_root(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    candidate = Path(result.stdout.strip()).expanduser().resolve()
    return candidate if candidate.exists() else None


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "project"


def _unique_project_slug(db: sqlite3.Connection, base_slug: str) -> str:
    rows = db.execute("select slug from projects where slug = ? or slug glob ?", (base_slug, f"{base_slug}-[0-9]*")).fetchall()
    used = {str(row["slug"]) for row in rows}
    if base_slug not in used:
        return base_slug
    suffix = 2
    while f"{base_slug}-{suffix}" in used:
        suffix += 1
    return f"{base_slug}-{suffix}"


def normalize_codex_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    settings = json.loads(json.dumps(DEFAULT_CODEX_SETTINGS))
    subagents = settings["subagents"]
    for key in (
        "runner_mode",
        "default_model",
        "reasoning_effort",
        "approval_policy",
        "sandbox_mode",
        "agent_task_mode",
        "automation_mode",
        "agent_role",
        "skill_policy",
    ):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            settings[key] = value.strip()
    if "automation_mode" not in source and "agent_task_mode" in source:
        settings["automation_mode"] = _automation_mode_from_agent_task_mode(settings["agent_task_mode"])
    elif "automation_mode" in source:
        settings["agent_task_mode"] = _agent_task_mode_from_automation_mode(settings["automation_mode"])
    for key in (
        "max_parallel_agents",
        "max_task_depth",
        "max_child_tasks_per_run",
        "max_total_agent_created_tasks_per_parent",
        "job_timeout_seconds",
        "max_prompt_protocol_tokens",
        "max_plane_comment_chars",
    ):
        value = source.get(key)
        if isinstance(value, int) and value > 0:
            settings[key] = value
    raw_subagents = source.get("subagents")
    if isinstance(raw_subagents, dict):
        for name, agent_settings in raw_subagents.items():
            if not isinstance(name, str) or not isinstance(agent_settings, dict):
                continue
            current = subagents.setdefault(name, {})
            for key in ("model", "reasoning_effort", "sandbox_mode"):
                value = agent_settings.get(key)
                if isinstance(value, str) and value.strip():
                    current[key] = value.strip()
    if settings["automation_mode"] not in {"manual", "assisted", "full_agent"}:
        settings["automation_mode"] = "assisted"
    settings["agent_task_mode"] = _agent_task_mode_from_automation_mode(str(settings["automation_mode"]))
    if settings["skill_policy"] not in {"minimal", "auto", "full"}:
        settings["skill_policy"] = "minimal"
    return dict(settings)


def _automation_mode_from_agent_task_mode(value: str) -> str:
    return {
        "manual": "manual",
        "review_and_approve": "assisted",
        "agent_task_planner": "full_agent",
    }.get(value, "assisted")


def _agent_task_mode_from_automation_mode(value: str) -> str:
    return {
        "manual": "manual",
        "assisted": "review_and_approve",
        "full_agent": "agent_task_planner",
    }.get(value, "review_and_approve")


def _project_from_row(row: sqlite3.Row) -> LocalProject:
    git_root = row["git_root"]
    try:
        raw_settings = json.loads(row["codex_settings"] or "{}")
    except (TypeError, ValueError):
        raw_settings = {}
    settings = normalize_codex_settings(raw_settings if isinstance(raw_settings, dict) else {})
    row_runner_mode = str(row["runner_mode"])
    if row_runner_mode and row_runner_mode != settings["runner_mode"]:
        settings["runner_mode"] = row_runner_mode
    return LocalProject(
        id=int(row["id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        repo_path=Path(str(row["repo_path"])),
        git_root=Path(str(git_root)) if git_root else None,
        plane_workspace_slug=row["plane_workspace_slug"],
        plane_project_id=row["plane_project_id"],
        harness_status=str(row["harness_status"]),
        runner_mode=str(row["runner_mode"]),
        codex_settings=settings,
    )
