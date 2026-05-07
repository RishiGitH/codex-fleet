from __future__ import annotations

from pathlib import Path

import pytest

from codex_fleet.local_work_items import LocalWorkItemStore, LocalWorkItemTracker
from codex_fleet.tracker import TrackerError


def test_local_work_item_store_seeds_ready_item(tmp_path: Path) -> None:
    store = LocalWorkItemStore(tmp_path / "items.sqlite3")

    items = store.list_items()

    assert len(items) == 1
    assert items[0].id == "memory-1"
    assert items[0].identifier == "CF-1"
    assert items[0].state == "Ready"


def test_local_work_item_store_persists_transitions_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "items.sqlite3"
    store = LocalWorkItemStore(path)

    store.update_item_state("memory-1", "Human Review")
    store.add_comment("memory-1", "codex-fleet run completed")

    reopened = LocalWorkItemStore(path)
    item = reopened.fetch_items_by_ids(["memory-1"])[0]
    comments = reopened.list_comments("memory-1")

    assert item.state == "Human Review"
    assert comments[0].body == "codex-fleet run completed"


def test_local_work_item_store_creates_new_ready_items(tmp_path: Path) -> None:
    store = LocalWorkItemStore(tmp_path / "items.sqlite3")

    item = store.create_item(title="Build navbar", description="Use the existing app shell.")

    assert item.id == "cf-2"
    assert item.identifier == "CF-2"
    assert item.state == "Ready"
    assert store.fetch_candidate_items(["Ready"])[1].identifier == "CF-2"


def test_local_work_item_tracker_filters_active_states(tmp_path: Path) -> None:
    store = LocalWorkItemStore(tmp_path / "items.sqlite3")
    store.update_item_state("memory-1", "Rework")
    tracker = LocalWorkItemTracker(store, active_states=["Ready"])

    assert tracker.fetch_candidate_items() == []
    assert tracker.fetch_items_by_ids(["memory-1"])[0].state == "Rework"


def test_local_work_item_tracker_creates_labeled_followup(tmp_path: Path) -> None:
    store = LocalWorkItemStore(tmp_path / "items.sqlite3")
    tracker = LocalWorkItemTracker(store, active_states=["Ready"])

    item = tracker.create_work_item(
        title="Add regression coverage",
        description="Cover the follow-up path.",
        state="Backlog",
        labels=("agent-proposed",),
    )

    assert item.identifier == "CF-2"
    assert item.state == "Backlog"
    assert item.labels == ("agent-proposed",)


def test_local_work_item_store_rejects_unknown_item(tmp_path: Path) -> None:
    store = LocalWorkItemStore(tmp_path / "items.sqlite3")

    with pytest.raises(TrackerError, match="Unknown work item"):
        store.update_item_state("missing", "Running")
