from codex_fleet.plane_bootstrap import check_plane_readiness, ensure_plane_states


class FakePlaneClient:
    def __init__(self) -> None:
        self.states = [{"name": "Ready"}]
        self.created: list[tuple[str, str, str]] = []

    def list_states(self) -> list[dict[str, str]]:
        return self.states

    def list_work_items(self) -> list[dict[str, object]]:
        return [{"state_detail": {"name": "Ready"}}, {"state_detail": {"name": "Done"}}]

    def create_state(self, name: str, group: str, color: str) -> dict[str, str]:
        self.created.append((name, group, color))
        self.states.append({"name": name})
        return {"name": name, "group": group, "color": color}


def test_check_plane_readiness_reports_missing_states() -> None:
    client = FakePlaneClient()

    readiness = check_plane_readiness(client, active_states=["Ready"])

    assert readiness.ok is False
    assert "Running" in readiness.missing_states
    assert readiness.candidate_count == 1


def test_ensure_plane_states_creates_missing_states() -> None:
    client = FakePlaneClient()

    result = ensure_plane_states(client, active_states=["Ready"])

    assert result.readiness.ok is True
    assert "Running" in result.created_states
    assert client.created
