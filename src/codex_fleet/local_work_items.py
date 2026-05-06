from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from codex_fleet.models import WorkItem, WorkItemState
from codex_fleet.tracker import Tracker, TrackerError


@dataclass(frozen=True)
class LocalWorkItemComment:
    id: int
    item_id: str
    body: str
    created_at: str


class LocalWorkItemStore:
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
                create table if not exists items (
                    id text primary key,
                    identifier text not null unique,
                    title text not null,
                    description text,
                    state text not null,
                    priority integer,
                    created_at text default current_timestamp,
                    updated_at text default current_timestamp
                )
                """
            )
            db.execute(
                """
                create table if not exists comments (
                    id integer primary key autoincrement,
                    item_id text not null,
                    body text not null,
                    created_at text default current_timestamp
                )
                """
            )

    def ensure_seed_item(self) -> None:
        with self._connect() as db:
            row = db.execute("select 1 from items limit 1").fetchone()
            if row is not None:
                return
            db.execute(
                """
                insert into items (id, identifier, title, description, state, priority)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    "memory-1",
                    "CF-1",
                    "Smoke task",
                    "Create a fake run marker in an isolated worktree.",
                    WorkItemState.READY.value,
                    2,
                ),
            )

    def list_items(self) -> list[WorkItem]:
        self.ensure_seed_item()
        with self._connect() as db:
            rows = db.execute("select * from items order by created_at asc, identifier asc").fetchall()
        return [_row_to_work_item(row) for row in rows]

    def fetch_items_by_ids(self, ids: list[str]) -> list[WorkItem]:
        self.ensure_seed_item()
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as db:
            rows = db.execute(f"select * from items where id in ({placeholders})", ids).fetchall()
        by_id = {str(row["id"]): _row_to_work_item(row) for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def fetch_candidate_items(self, active_states: list[str]) -> list[WorkItem]:
        states = {state.lower() for state in active_states}
        return [item for item in self.list_items() if item.state.lower() in states]

    def create_item(
        self,
        *,
        title: str,
        description: str | None = None,
        state: str = WorkItemState.READY.value,
        priority: int | None = 2,
    ) -> WorkItem:
        title = title.strip()
        if not title:
            raise ValueError("Work item title is required.")
        identifier = self._next_identifier()
        item_id = identifier.lower()
        with self._connect() as db:
            db.execute(
                """
                insert into items (id, identifier, title, description, state, priority)
                values (?, ?, ?, ?, ?, ?)
                """,
                (item_id, identifier, title, description.strip() if description else None, state, priority),
            )
            row = db.execute("select * from items where id = ?", (item_id,)).fetchone()
        if row is None:
            raise TrackerError(f"Unable to create work item: {identifier}")
        return _row_to_work_item(row)

    def update_item_state(self, item_id: str, state: str) -> None:
        self.ensure_seed_item()
        with self._connect() as db:
            cursor = db.execute(
                """
                update items
                set state = ?, updated_at = current_timestamp
                where id = ?
                """,
                (state, item_id),
            )
            if cursor.rowcount == 0:
                raise TrackerError(f"Unknown work item: {item_id}")

    def add_comment(self, item_id: str, body: str) -> None:
        self.ensure_seed_item()
        with self._connect() as db:
            row = db.execute("select 1 from items where id = ?", (item_id,)).fetchone()
            if row is None:
                raise TrackerError(f"Unknown work item: {item_id}")
            db.execute(
                "insert into comments (item_id, body) values (?, ?)",
                (item_id, body),
            )

    def list_comments(self, item_id: str) -> list[LocalWorkItemComment]:
        with self._connect() as db:
            rows = db.execute(
                "select * from comments where item_id = ? order by id asc",
                (item_id,),
            ).fetchall()
        return [
            LocalWorkItemComment(
                id=int(row["id"]),
                item_id=str(row["item_id"]),
                body=str(row["body"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def _next_identifier(self) -> str:
        self.ensure_seed_item()
        with self._connect() as db:
            rows = db.execute("select identifier from items").fetchall()
        max_number = 0
        for row in rows:
            value = str(row["identifier"])
            if not value.startswith("CF-"):
                continue
            try:
                max_number = max(max_number, int(value.removeprefix("CF-")))
            except ValueError:
                continue
        return f"CF-{max_number + 1}"


class LocalWorkItemTracker(Tracker):
    def __init__(self, store: LocalWorkItemStore, *, active_states: list[str]) -> None:
        self.store = store
        self.active_states = active_states

    def fetch_candidate_items(self) -> list[WorkItem]:
        return self.store.fetch_candidate_items(self.active_states)

    def fetch_items_by_ids(self, ids: list[str]) -> list[WorkItem]:
        return self.store.fetch_items_by_ids(ids)

    def update_item_state(self, item_id: str, state: str) -> None:
        self.store.update_item_state(item_id, state)

    def create_comment(self, item_id: str, body: str) -> None:
        self.store.add_comment(item_id, body)

    def create_work_item(
        self,
        *,
        title: str,
        description: str | None,
        state: str,
        labels: tuple[str, ...] = (),
    ) -> WorkItem:
        item = self.store.create_item(title=title, description=description, state=state)
        return WorkItem(
            id=item.id,
            identifier=item.identifier,
            title=item.title,
            description=item.description,
            state=item.state,
            priority=item.priority,
            labels=labels,
        )


def default_local_work_item_store_path(repo: Path) -> Path:
    return repo.expanduser().absolute() / ".codex-fleet" / "local-work-items.sqlite3"


def _row_to_work_item(row: sqlite3.Row) -> WorkItem:
    return WorkItem(
        id=str(row["id"]),
        identifier=str(row["identifier"]),
        title=str(row["title"]),
        description=row["description"],
        state=str(row["state"]),
        priority=row["priority"],
    )
