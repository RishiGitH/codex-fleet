from __future__ import annotations

from dataclasses import dataclass

import httpx

from codex_fleet.plane import PlaneClient

REQUIRED_STATES: tuple[str, ...] = (
    "Backlog",
    "Ready",
    "Running",
    "Human Review",
    "Rework",
    "Done",
    "Blocked",
    "Cancelled",
)

STATE_GROUPS: dict[str, str] = {
    "Backlog": "backlog",
    "Ready": "unstarted",
    "Running": "started",
    "Human Review": "started",
    "Rework": "started",
    "Done": "completed",
    "Blocked": "cancelled",
    "Cancelled": "cancelled",
}

STATE_COLORS: dict[str, str] = {
    "Backlog": "#6B7280",
    "Ready": "#3B82F6",
    "Running": "#F59E0B",
    "Human Review": "#8B5CF6",
    "Rework": "#EF4444",
    "Done": "#10B981",
    "Blocked": "#111827",
    "Cancelled": "#6B7280",
}

REQUIRED_LABELS: dict[str, str] = {
    "human-requested": "#3B82F6",
    "agent-proposed": "#F59E0B",
    "agent-followup": "#8B5CF6",
}


@dataclass(frozen=True)
class PlaneReadiness:
    ok: bool
    missing_states: tuple[str, ...]
    state_count: int
    candidate_count: int


@dataclass(frozen=True)
class PlaneBootstrapResult:
    created_states: tuple[str, ...]
    created_labels: tuple[str, ...]
    created_demo_item: bool
    scrubbed_projects: tuple[str, ...]
    readiness: PlaneReadiness


def check_plane_readiness(client: PlaneClient, active_states: list[str]) -> PlaneReadiness:
    states = client.list_states()
    existing = {str(state.get("name", "")).strip().lower() for state in states}
    states_by_id = {str(state.get("id")): str(state.get("name", "")) for state in states if state.get("id")}
    missing = tuple(state for state in REQUIRED_STATES if state.lower() not in existing)

    active = {state.lower() for state in active_states}
    items = client.list_work_items()
    candidate_count = 0
    for item in items:
        state_value = item.get("state")
        state_name = str(item.get("state_detail", {}).get("name") or states_by_id.get(str(state_value), state_value))
        if state_name.lower() in active:
            candidate_count += 1

    return PlaneReadiness(
        ok=not missing,
        missing_states=missing,
        state_count=len(states),
        candidate_count=candidate_count,
    )


def ensure_plane_states(client: PlaneClient, active_states: list[str]) -> PlaneBootstrapResult:
    before = check_plane_readiness(client, active_states)
    created: list[str] = []
    for state in before.missing_states:
        client.create_state(
            name=state,
            group=STATE_GROUPS[state],
            color=STATE_COLORS[state],
        )
        created.append(state)
    after = check_plane_readiness(client, active_states)
    return PlaneBootstrapResult(
        created_states=tuple(created),
        created_labels=(),
        created_demo_item=False,
        scrubbed_projects=(),
        readiness=after,
    )


def ensure_plane_labels(client: PlaneClient) -> tuple[str, ...]:
    labels = client.list_labels()
    existing = {str(label.get("name", "")).strip().lower() for label in labels}
    created: list[str] = []
    for label, color in REQUIRED_LABELS.items():
        if label in existing:
            continue
        client.create_label(label, color=color)
        created.append(label)
    return tuple(created)


def scrub_plane_seed_projects(client: PlaneClient) -> tuple[str, ...]:
    """Replace stock demo project copy that leaks into fresh local installs."""
    scrubbed: list[str] = []
    for project in client.list_projects():
        project_id = project.get("id")
        if not isinstance(project_id, str) or not project_id:
            continue
        name = str(project.get("name") or "")
        description = str(project.get("description") or "")
        haystack = f"{name}\n{description}"
        if "Plane Demo Project" not in haystack and "driver’s seat of Plane" not in haystack:
            continue
        payload = {
            "name": "codex fleet starter project",
            "description": (
                "A local starter project for trying codex-fleet. Create work items, move them to Ready, "
                "and watch local Codex agents claim the work."
            ),
        }
        try:
            client.update_project(project_id, payload)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 400 and "Project name cannot contain special characters" not in error.response.text:
                continue
            if error.response.status_code != 403:
                raise
            try:
                client.join_projects([project_id])
                client.update_project(project_id, payload)
            except httpx.HTTPStatusError as join_error:
                if join_error.response.status_code not in {401, 403, 404}:
                    raise
                continue
        scrubbed.append(project_id)
    return tuple(scrubbed)


def ensure_plane_bootstrap(client: PlaneClient, active_states: list[str]) -> PlaneBootstrapResult:
    state_result = ensure_plane_states(client, active_states)
    created_labels = ensure_plane_labels(client)
    scrubbed_projects = scrub_plane_seed_projects(client)
    readiness = state_result.readiness
    return PlaneBootstrapResult(
        created_states=state_result.created_states,
        created_labels=created_labels,
        created_demo_item=False,
        scrubbed_projects=scrubbed_projects,
        readiness=readiness,
    )
