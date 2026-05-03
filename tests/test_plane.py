import pytest

from codex_fleet.plane import PlaneTracker, normalize_plane_item, state_id_by_name
from codex_fleet.tracker import TrackerError


def test_normalize_plane_item_maps_core_fields() -> None:
    item = normalize_plane_item(
        {
            "id": "abc",
            "sequence_id": 42,
            "name": "Add runner",
            "description_stripped": "Build it",
            "priority": "high",
            "state_detail": {"name": "Ready"},
            "project_detail": {"identifier": "CF"},
            "label_details": [{"name": "Backend"}],
        }
    )

    assert item.id == "abc"
    assert item.identifier == "CF-42"
    assert item.title == "Add runner"
    assert item.description == "Build it"
    assert item.priority == 2
    assert item.state == "Ready"
    assert item.labels == ("backend",)


def test_state_id_by_name_is_case_insensitive() -> None:
    states = [{"id": "ready-id", "name": "Ready"}]

    assert state_id_by_name(states, "ready") == "ready-id"


def test_state_id_by_name_raises_for_missing_state() -> None:
    with pytest.raises(TrackerError):
        state_id_by_name([], "Ready")


class FakePlaneClient:
    def __init__(self) -> None:
        self.updated: list[tuple[str, dict[str, str]]] = []

    def list_work_items(self) -> list[dict[str, object]]:
        return []

    def list_states(self) -> list[dict[str, str]]:
        return [{"id": "running-id", "name": "Running"}]

    def update_work_item(self, item_id: str, payload: dict[str, str]) -> None:
        self.updated.append((item_id, payload))

    def create_work_item_comment(self, item_id: str, body: str) -> None:
        return None


def test_plane_tracker_updates_state_by_resolved_id() -> None:
    client = FakePlaneClient()
    tracker = PlaneTracker(client=client, active_states=["Ready"])  # type: ignore[arg-type]

    tracker.update_item_state("item-1", "Running")

    assert client.updated == [("item-1", {"state": "running-id"})]
