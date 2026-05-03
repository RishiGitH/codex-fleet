from codex_fleet.plane import normalize_plane_item


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
