from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace

from codex_fleet.models import WorkItem, WorkItemComment, WorkItemState


class TrackerError(RuntimeError):
    pass


class Tracker(ABC):
    @abstractmethod
    def fetch_candidate_items(self) -> list[WorkItem]:
        raise NotImplementedError

    @abstractmethod
    def fetch_items_by_ids(self, ids: list[str]) -> list[WorkItem]:
        raise NotImplementedError

    @abstractmethod
    def update_item_state(self, item_id: str, state: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_comment(self, item_id: str, body: str) -> None:
        raise NotImplementedError

    def list_comments(self, item_id: str) -> list[WorkItemComment]:
        return []

    def create_work_item(
        self,
        *,
        title: str,
        description: str | None,
        state: str,
        labels: tuple[str, ...] = (),
    ) -> WorkItem | None:
        raise TrackerError("Tracker does not support creating work items.")


class MemoryTracker(Tracker):
    def __init__(self, items: list[WorkItem] | None = None, active_states: list[str] | None = None) -> None:
        self._items = {item.id: item for item in items or []}
        self._comments: dict[str, list[str]] = {}
        self._active_states = {state.lower() for state in (active_states or [WorkItemState.READY.value])}

    @property
    def comments(self) -> dict[str, list[str]]:
        return self._comments

    def fetch_candidate_items(self) -> list[WorkItem]:
        return [item for item in self._items.values() if item.state.lower() in self._active_states]

    def fetch_items_by_ids(self, ids: list[str]) -> list[WorkItem]:
        return [self._items[item_id] for item_id in ids if item_id in self._items]

    def fetch_all_items(self) -> list[WorkItem]:
        return list(self._items.values())

    def add_item(self, item: WorkItem) -> None:
        self._items[item.id] = item

    def update_item_state(self, item_id: str, state: str) -> None:
        if item_id not in self._items:
            raise TrackerError(f"Unknown work item: {item_id}")
        self._items[item_id] = replace(self._items[item_id], state=state)

    def create_comment(self, item_id: str, body: str) -> None:
        if item_id not in self._items:
            raise TrackerError(f"Unknown work item: {item_id}")
        self._comments.setdefault(item_id, []).append(body)

    def list_comments(self, item_id: str) -> list[WorkItemComment]:
        return [
            WorkItemComment(
                id=f"memory-{item_id}-{index}",
                author_display_name="codex-fleet local" if _is_codex_fleet_comment(body) else "human",
                created_at=None,
                body_text=body,
                is_codex_fleet=_is_codex_fleet_comment(body),
            )
            for index, body in enumerate(self._comments.get(item_id, []), start=1)
        ]

    def create_work_item(
        self,
        *,
        title: str,
        description: str | None,
        state: str,
        labels: tuple[str, ...] = (),
    ) -> WorkItem:
        identifier = f"CF-{len(self._items) + 1}"
        item = WorkItem(
            id=str(len(self._items) + 1),
            identifier=identifier,
            title=title,
            description=description,
            state=state,
            labels=labels,
        )
        self.add_item(item)
        return item


def _is_codex_fleet_comment(body: str) -> bool:
    normalized = body.strip().lower()
    return normalized.startswith("codex-fleet") or "human answer for codex-fleet" in normalized
