from codex_fleet.models import WorkItem
from codex_fleet.tracker import MemoryTracker


def test_memory_tracker_filters_active_items() -> None:
    ready = WorkItem(id="1", identifier="CF-1", title="Ready", description=None, state="Ready")
    done = WorkItem(id="2", identifier="CF-2", title="Done", description=None, state="Done")

    tracker = MemoryTracker([ready, done], active_states=["Ready"])

    assert tracker.fetch_candidate_items() == [ready]


def test_memory_tracker_updates_state_and_comments() -> None:
    item = WorkItem(id="1", identifier="CF-1", title="Ready", description=None, state="Ready")
    tracker = MemoryTracker([item], active_states=["Ready"])

    tracker.update_item_state("1", "Running")
    tracker.create_comment("1", "started")

    assert tracker.fetch_items_by_ids(["1"])[0].state == "Running"
    assert tracker.comments["1"] == ["started"]
