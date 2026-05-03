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
