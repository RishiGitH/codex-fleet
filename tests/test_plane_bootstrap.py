import httpx

from codex_fleet.plane_bootstrap import (
    check_plane_readiness,
    ensure_plane_bootstrap,
    ensure_plane_labels,
    ensure_plane_states,
    scrub_plane_seed_projects,
)


class FakePlaneClient:
    def __init__(self, items: list[dict[str, object]] | None = None) -> None:
        self.states = [{"id": "ready-id", "name": "Ready"}]
        self.items = items if items is not None else [{"state_detail": {"name": "Ready"}}, {"state_detail": {"name": "Done"}}]
        self.labels: list[dict[str, str]] = []
        self.created: list[tuple[str, str, str]] = []
        self.created_labels: list[tuple[str, str]] = []
        self.created_items: list[dict[str, object]] = []
        self.projects: list[dict[str, object]] = []
        self.updated_projects: list[tuple[str, dict[str, object]]] = []
        self.joined_projects: list[list[str]] = []

    def list_states(self) -> list[dict[str, str]]:
        return self.states

    def list_work_items(self) -> list[dict[str, object]]:
        return self.items

    def create_state(self, name: str, group: str, color: str) -> dict[str, str]:
        self.created.append((name, group, color))
        self.states.append({"name": name})
        return {"name": name, "group": group, "color": color}

    def list_labels(self) -> list[dict[str, str]]:
        return self.labels

    def create_label(self, name: str, color: str) -> dict[str, str]:
        self.created_labels.append((name, color))
        self.labels.append({"name": name, "color": color})
        return {"name": name, "color": color}

    def create_work_item(self, name: str, description_html: str, state_id: str | None = None) -> dict[str, object]:
        item = {"name": name, "description_html": description_html, "state_detail": {"name": "Ready"}, "state": state_id}
        self.created_items.append(item)
        self.items.append(item)
        return item

    def list_projects(self) -> list[dict[str, object]]:
        return self.projects

    def update_project(self, project_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.updated_projects.append((project_id, payload))
        for project in self.projects:
            if project.get("id") == project_id:
                project.update(payload)
                return project
        return {"id": project_id, **payload}

    def join_projects(self, project_ids: list[str]) -> None:
        self.joined_projects.append(project_ids)


def test_check_plane_readiness_reports_missing_states() -> None:
    client = FakePlaneClient()

    readiness = check_plane_readiness(client, active_states=["Ready"])

    assert readiness.ok is False
    assert "Running" in readiness.missing_states
    assert readiness.candidate_count == 1


def test_check_plane_readiness_counts_items_with_state_id_only() -> None:
    client = FakePlaneClient(items=[{"state": "ready-id"}, {"state_detail": {"name": "Done"}}])

    readiness = check_plane_readiness(client, active_states=["Ready"])

    assert readiness.candidate_count == 1


def test_ensure_plane_states_creates_missing_states() -> None:
    client = FakePlaneClient()

    result = ensure_plane_states(client, active_states=["Ready"])

    assert result.readiness.ok is True
    assert "Running" in result.created_states
    assert client.created
    assert result.created_demo_item is False


def test_ensure_plane_labels_creates_task_source_labels() -> None:
    client = FakePlaneClient()

    created = ensure_plane_labels(client)

    assert set(created) == {"human-requested", "agent-proposed", "agent-followup"}
    assert ("agent-proposed", "#F59E0B") in client.created_labels


def test_scrub_plane_seed_projects_replaces_stock_demo_copy() -> None:
    client = FakePlaneClient()
    client.projects = [
        {
            "id": "project-1",
            "name": "Plane Demo Project",
            "description": "Welcome to the Plane Demo Project! This project throws you into the driver’s seat of Plane.",
        },
        {"id": "project-2", "name": "codex-fleet", "description": "Managed by codex-fleet."},
    ]

    scrubbed = scrub_plane_seed_projects(client)

    assert scrubbed == ("project-1",)
    assert client.updated_projects == [
        (
            "project-1",
            {
                "name": "codex fleet starter project",
                "description": (
                    "A local starter project for trying codex-fleet. Create work items, move them to Ready, "
                    "and watch local Codex agents claim the work."
                ),
            },
        )
    ]


def test_scrub_plane_seed_projects_skips_validation_failures() -> None:
    class RejectingPlaneClient(FakePlaneClient):
        def update_project(self, project_id: str, payload: dict[str, object]) -> dict[str, object]:
            request = httpx.Request("PATCH", f"http://plane.test/projects/{project_id}/")
            response = httpx.Response(400, request=request)
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

    client = RejectingPlaneClient()
    client.projects = [
        {
            "id": "project-1",
            "name": "Plane Demo Project",
            "description": "Welcome to the Plane Demo Project! This project throws you into the driver’s seat of Plane.",
        }
    ]

    scrubbed = scrub_plane_seed_projects(client)

    assert scrubbed == ()


def test_ensure_plane_bootstrap_does_not_create_demo_ready_item_when_none_exist() -> None:
    client = FakePlaneClient(items=[{"state_detail": {"name": "Done"}}])

    result = ensure_plane_bootstrap(client, active_states=["Ready"])

    assert result.created_demo_item is False
    assert "human-requested" in result.created_labels
    assert result.readiness.candidate_count == 0
    assert client.created_items == []
