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
                    error text,
                    created_at text default current_timestamp,
                    updated_at text default current_timestamp
                )
                """
            )
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
                    created_at text default current_timestamp
                )
                """
            )
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
                    settings text not null default '{}',
                    created_at text default current_timestamp,
                    updated_at text default current_timestamp
                )
                """
            )

    def upsert_run(
        self,
        *,
        run_id: str,
        item_id: str,
        identifier: str,
        status: str,
        branch_name: str | None = None,
        worktree_path: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into runs (id, item_id, identifier, status, branch_name, worktree_path, error)
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    status = excluded.status,
                    branch_name = excluded.branch_name,
                    worktree_path = excluded.worktree_path,
                    error = excluded.error,
                    updated_at = current_timestamp
                """,
                (run_id, item_id, identifier, status, branch_name, worktree_path, error),
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

    def add_artifact(self, run_id: str, path: str, *, kind: str = "file") -> None:
        with self._connect() as db:
            db.execute(
                "insert into artifacts (run_id, path, kind) values (?, ?, ?)",
                (run_id, path, kind),
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
                    settings
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(item_id) do update set
                    source = excluded.source,
                    depth = excluded.depth,
                    parent_item_id = excluded.parent_item_id,
                    parent_identifier = excluded.parent_identifier,
                    parent_run_id = excluded.parent_run_id,
                    created_by_run_id = excluded.created_by_run_id,
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
                    json.dumps(settings or {}, sort_keys=True),
                ),
            )

    def get_task_metadata(self, item_id: str) -> TaskMetadata | None:
        with self._connect() as db:
            row = db.execute("select * from task_metadata where item_id = ?", (item_id,)).fetchone()
        return _task_metadata_from_row(row) if row is not None else None


def _decode_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


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
        settings=_decode_payload(str(row["settings"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
