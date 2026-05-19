from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StoredRun:
    id: str
    item_id: str
    identifier: str
    status: str
    branch_name: str | None
    worktree_path: str | None
    runner_name: str | None
    agent_role: str | None
    agent_name: str | None
    agent_avatar: str | None
    model: str | None
    settings: dict[str, Any]
    token_usage: dict[str, Any]
    error: str | None


@dataclass(frozen=True)
class StoredEvent:
    id: int
    run_id: str
    kind: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class StoredArtifact:
    id: int
    run_id: str
    path: str
    kind: str
    size_bytes: int | None
    sha256: str | None
    redaction: str
    created_at: str


@dataclass(frozen=True)
class StoredClaim:
    item_id: str
    run_id: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskMetadata:
    item_id: str
    source: str
    depth: int
    parent_item_id: str | None
    parent_identifier: str | None
    parent_run_id: str | None
    created_by_run_id: str | None
    root_item_id: str | None
    role: str | None
    depends_on: tuple[str, ...]
    generation: int
    approval_mode: str | None
    terminal_outcome: str | None
    settings: dict[str, Any]
    created_at: str
    updated_at: str


class RunStore:
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
                create table if not exists runs (
                    id text primary key,
                    item_id text not null,
                    identifier text not null,
                    status text not null,
                    branch_name text,
                    worktree_path text,
                    runner_name text,
                    agent_role text,
                    agent_name text,
                    agent_avatar text,
                    model text,
                    settings text not null default '{}',
                    token_usage text not null default '{}',
                    error text,
                    created_at text default current_timestamp,
                    updated_at text default current_timestamp
                )
                """
            )
            _ensure_column(db, "runs", "runner_name", "text")
            _ensure_column(db, "runs", "agent_role", "text")
            _ensure_column(db, "runs", "agent_name", "text")
            _ensure_column(db, "runs", "agent_avatar", "text")
            _ensure_column(db, "runs", "model", "text")
            _ensure_column(db, "runs", "settings", "text not null default '{}'")
            _ensure_column(db, "runs", "token_usage", "text not null default '{}'")
            db.execute(
                """
                create table if not exists events (
                    id integer primary key autoincrement,
                    run_id text not null,
                    kind text not null,
                    payload text not null,
                    created_at text default current_timestamp
                )
                """
            )
            db.execute(
                """
                create table if not exists claims (
                    item_id text primary key,
                    run_id text not null,
                    status text not null,
                    created_at text default current_timestamp,
                    updated_at text default current_timestamp
                )
                """
            )
            db.execute(
                """
                create table if not exists artifacts (
                    id integer primary key autoincrement,
                    run_id text not null,
                    path text not null,
                    kind text not null,
                    size_bytes integer,
                    sha256 text,
                    redaction text not null default 'local',
                    created_at text default current_timestamp
                )
                """
            )
            _ensure_column(db, "artifacts", "size_bytes", "integer")
            _ensure_column(db, "artifacts", "sha256", "text")
            _ensure_column(db, "artifacts", "redaction", "text not null default 'local'")
            db.execute(
                """
                create table if not exists task_metadata (
                    item_id text primary key,
                    source text not null,
                    depth integer not null default 0,
                    parent_item_id text,
                    parent_identifier text,
                    parent_run_id text,
                    created_by_run_id text,
                    root_item_id text,
                    role text,
                    depends_on text not null default '[]',
                    generation integer not null default 0,
                    approval_mode text,
                    terminal_outcome text,
                    settings text not null default '{}',
                    created_at text default current_timestamp,
                    updated_at text default current_timestamp
                )
                """
            )
            _ensure_column(db, "task_metadata", "root_item_id", "text")
            _ensure_column(db, "task_metadata", "role", "text")
            _ensure_column(db, "task_metadata", "depends_on", "text not null default '[]'")
            _ensure_column(db, "task_metadata", "generation", "integer not null default 0")
            _ensure_column(db, "task_metadata", "approval_mode", "text")
            _ensure_column(db, "task_metadata", "terminal_outcome", "text")

    def upsert_run(
        self,
        *,
        run_id: str,
        item_id: str,
        identifier: str,
        status: str,
        branch_name: str | None = None,
        worktree_path: str | None = None,
        runner_name: str | None = None,
        agent_role: str | None = None,
        agent_name: str | None = None,
        agent_avatar: str | None = None,
        model: str | None = None,
        settings: dict[str, Any] | None = None,
        token_usage: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into runs (
                    id, item_id, identifier, status, branch_name, worktree_path,
                    runner_name, agent_role, agent_name, agent_avatar, model, settings, token_usage, error
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    status = excluded.status,
                    branch_name = coalesce(excluded.branch_name, runs.branch_name),
                    worktree_path = coalesce(excluded.worktree_path, runs.worktree_path),
                    runner_name = coalesce(excluded.runner_name, runs.runner_name),
                    agent_role = coalesce(excluded.agent_role, runs.agent_role),
                    agent_name = coalesce(excluded.agent_name, runs.agent_name),
                    agent_avatar = coalesce(excluded.agent_avatar, runs.agent_avatar),
                    model = coalesce(excluded.model, runs.model),
                    settings = excluded.settings,
                    token_usage = excluded.token_usage,
                    error = excluded.error,
                    updated_at = current_timestamp
                """,
                (
                    run_id,
                    item_id,
                    identifier,
                    status,
                    branch_name,
                    worktree_path,
                    runner_name,
                    agent_role,
                    agent_name,
                    agent_avatar,
                    model,
                    json.dumps(settings or {}, sort_keys=True),
                    json.dumps(token_usage or {}, sort_keys=True),
                    error,
                ),
            )

    def add_event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                "insert into events (run_id, kind, payload) values (?, ?, ?)",
                (run_id, kind, json.dumps(payload, sort_keys=True)),
            )

    def list_events(self, run_id: str) -> list[StoredEvent]:
        with self._connect() as db:
            rows = db.execute(
                "select * from events where run_id = ? order by id asc",
                (run_id,),
            ).fetchall()
        return [
            StoredEvent(
                id=int(row["id"]),
                run_id=str(row["run_id"]),
                kind=str(row["kind"]),
                payload=_decode_payload(str(row["payload"])),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def list_recent_events(self, *, limit: int = 100) -> list[StoredEvent]:
        with self._connect() as db:
            rows = db.execute(
                "select * from events order by created_at desc, id desc limit ?",
                (limit,),
            ).fetchall()
        return [
            StoredEvent(
                id=int(row["id"]),
                run_id=str(row["run_id"]),
                kind=str(row["kind"]),
                payload=_decode_payload(str(row["payload"])),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def add_artifact(
        self,
        run_id: str,
        path: str,
        *,
        kind: str = "file",
        size_bytes: int | None = None,
        sha256: str | None = None,
        redaction: str = "local",
    ) -> None:
        with self._connect() as db:
            db.execute(
                "insert into artifacts (run_id, path, kind, size_bytes, sha256, redaction) values (?, ?, ?, ?, ?, ?)",
                (run_id, path, kind, size_bytes, sha256, redaction),
            )

    def list_artifacts(self, run_id: str) -> list[StoredArtifact]:
        with self._connect() as db:
            rows = db.execute(
                "select * from artifacts where run_id = ? order by id asc",
                (run_id,),
            ).fetchall()
        return [
            StoredArtifact(
                id=int(row["id"]),
                run_id=str(row["run_id"]),
                path=str(row["path"]),
                kind=str(row["kind"]),
                size_bytes=int(row["size_bytes"]) if row["size_bytes"] is not None else None,
                sha256=row["sha256"],
                redaction=str(row["redaction"] or "local"),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def try_claim_item(self, item_id: str, run_id: str) -> bool:
        with self._connect() as db:
            row = db.execute("select status from claims where item_id = ?", (item_id,)).fetchone()
            if row is not None and str(row["status"]) == "active":
                return False
            db.execute(
                """
                insert into claims (item_id, run_id, status)
                values (?, ?, 'active')
                on conflict(item_id) do update set
                    run_id = excluded.run_id,
                    status = 'active',
                    updated_at = current_timestamp
                """,
                (item_id, run_id),
            )
            return True

    def finish_claim(self, item_id: str, run_id: str, status: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update claims
                set status = ?, updated_at = current_timestamp
                where item_id = ? and run_id = ?
                """,
                (status, item_id, run_id),
            )

    def get_claim(self, item_id: str) -> StoredClaim | None:
        with self._connect() as db:
            row = db.execute("select * from claims where item_id = ?", (item_id,)).fetchone()
        return _claim_from_row(row) if row is not None else None

    def release_stale_claims(self, *, max_age_seconds: float) -> list[StoredClaim]:
        age_seconds = max(0, int(max_age_seconds))
        modifier = f"-{age_seconds} seconds"
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from claims
                where status = 'active'
                  and updated_at <= datetime('now', ?)
                order by updated_at asc, item_id asc
                """,
                (modifier,),
            ).fetchall()
            claims = [_claim_from_row(row) for row in rows]
            for claim in claims:
                db.execute(
                    """
                    update claims
                    set status = 'stale', updated_at = current_timestamp
                    where item_id = ? and run_id = ? and status = 'active'
                    """,
                    (claim.item_id, claim.run_id),
                )
        return claims

    def update_run_status(self, run_id: str, status: str, *, error: str | None = None) -> None:
        with self._connect() as db:
            db.execute(
                """
                update runs
                set status = ?, error = ?, updated_at = current_timestamp
                where id = ?
                """,
                (status, error, run_id),
            )

    def get_run(self, run_id: str) -> StoredRun | None:
        with self._connect() as db:
            row = db.execute("select * from runs where id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return StoredRun(
            id=str(row["id"]),
            item_id=str(row["item_id"]),
            identifier=str(row["identifier"]),
            status=str(row["status"]),
            branch_name=row["branch_name"],
            worktree_path=row["worktree_path"],
            runner_name=row["runner_name"],
            agent_role=row["agent_role"],
            agent_name=row["agent_name"],
            agent_avatar=row["agent_avatar"],
            model=row["model"],
            settings=_decode_payload(str(row["settings"])),
            token_usage=_decode_payload(str(row["token_usage"])),
            error=row["error"],
        )

    def list_runs(self, *, limit: int = 50) -> list[StoredRun]:
        with self._connect() as db:
            rows = db.execute(
                "select * from runs order by created_at desc, id desc limit ?",
                (limit,),
            ).fetchall()
        return [
            StoredRun(
                id=str(row["id"]),
                item_id=str(row["item_id"]),
                identifier=str(row["identifier"]),
                status=str(row["status"]),
                branch_name=row["branch_name"],
                worktree_path=row["worktree_path"],
                runner_name=row["runner_name"],
                agent_role=row["agent_role"],
                agent_name=row["agent_name"],
                agent_avatar=row["agent_avatar"],
                model=row["model"],
                settings=_decode_payload(str(row["settings"])),
                token_usage=_decode_payload(str(row["token_usage"])),
                error=row["error"],
            )
            for row in rows
        ]

    def latest_run_for_item(self, item_id: str) -> StoredRun | None:
        with self._connect() as db:
            row = db.execute(
                """
                select *
                from runs
                where item_id = ?
                order by created_at desc, id desc
                limit 1
                """,
                (item_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredRun(
            id=str(row["id"]),
            item_id=str(row["item_id"]),
            identifier=str(row["identifier"]),
            status=str(row["status"]),
            branch_name=row["branch_name"],
            worktree_path=row["worktree_path"],
            runner_name=row["runner_name"],
            agent_role=row["agent_role"],
            agent_name=row["agent_name"],
            agent_avatar=row["agent_avatar"],
            model=row["model"],
            settings=_decode_payload(str(row["settings"])),
            token_usage=_decode_payload(str(row["token_usage"])),
            error=row["error"],
        )

    def upsert_task_metadata(
        self,
        *,
        item_id: str,
        source: str,
        depth: int = 0,
        parent_item_id: str | None = None,
        parent_identifier: str | None = None,
        parent_run_id: str | None = None,
        created_by_run_id: str | None = None,
        root_item_id: str | None = None,
        role: str | None = None,
        depends_on: tuple[str, ...] = (),
        generation: int = 0,
        approval_mode: str | None = None,
        terminal_outcome: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into task_metadata (
                    item_id,
                    source,
                    depth,
                    parent_item_id,
                    parent_identifier,
                    parent_run_id,
                    created_by_run_id,
                    root_item_id,
                    role,
                    depends_on,
                    generation,
                    approval_mode,
                    terminal_outcome,
                    settings
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(item_id) do update set
                    source = excluded.source,
                    depth = excluded.depth,
                    parent_item_id = excluded.parent_item_id,
                    parent_identifier = excluded.parent_identifier,
                    parent_run_id = excluded.parent_run_id,
                    created_by_run_id = excluded.created_by_run_id,
                    root_item_id = excluded.root_item_id,
                    role = excluded.role,
                    depends_on = excluded.depends_on,
                    generation = excluded.generation,
                    approval_mode = excluded.approval_mode,
                    terminal_outcome = excluded.terminal_outcome,
                    settings = excluded.settings,
                    updated_at = current_timestamp
                """,
                (
                    item_id,
                    source,
                    max(0, int(depth)),
                    parent_item_id,
                    parent_identifier,
                    parent_run_id,
                    created_by_run_id,
                    root_item_id,
                    role,
                    json.dumps(list(depends_on), sort_keys=True),
                    max(0, int(generation)),
                    approval_mode,
                    terminal_outcome,
                    json.dumps(settings or {}, sort_keys=True),
                ),
            )

    def get_task_metadata(self, item_id: str) -> TaskMetadata | None:
        with self._connect() as db:
            row = db.execute("select * from task_metadata where item_id = ?", (item_id,)).fetchone()
        return _task_metadata_from_row(row) if row is not None else None

    def list_child_task_metadata(self, parent_item_id: str) -> list[TaskMetadata]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from task_metadata
                where parent_item_id = ?
                order by created_at asc, item_id asc
                """,
                (parent_item_id,),
            ).fetchall()
        return [_task_metadata_from_row(row) for row in rows]

    def list_parent_item_ids_with_children(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                """
                select distinct parent_item_id
                from task_metadata
                where parent_item_id is not null
                order by parent_item_id asc
                """
            ).fetchall()
        return [str(row["parent_item_id"]) for row in rows]


def _decode_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in db.execute(f"pragma table_info({table})").fetchall()}
    if column not in columns:
        db.execute(f"alter table {table} add column {column} {definition}")


def _claim_from_row(row: sqlite3.Row) -> StoredClaim:
    return StoredClaim(
        item_id=str(row["item_id"]),
        run_id=str(row["run_id"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _task_metadata_from_row(row: sqlite3.Row) -> TaskMetadata:
    return TaskMetadata(
        item_id=str(row["item_id"]),
        source=str(row["source"]),
        depth=max(0, int(row["depth"])),
        parent_item_id=row["parent_item_id"],
        parent_identifier=row["parent_identifier"],
        parent_run_id=row["parent_run_id"],
        created_by_run_id=row["created_by_run_id"],
        root_item_id=row["root_item_id"],
        role=row["role"],
        depends_on=_decode_string_tuple(str(row["depends_on"])),
        generation=max(0, int(row["generation"])),
        approval_mode=row["approval_mode"],
        terminal_outcome=row["terminal_outcome"],
        settings=_decode_payload(str(row["settings"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _decode_string_tuple(raw: str) -> tuple[str, ...]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, list):
        return ()
    return tuple(str(item) for item in payload if isinstance(item, str) and item.strip())
