from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class PlaneReadiness:
    ok: bool
    missing_states: tuple[str, ...]
    state_count: int
    candidate_count: int


@dataclass(frozen=True)
class PlaneBootstrapResult:
    created_states: tuple[str, ...]
    readiness: PlaneReadiness


def check_plane_readiness(client: PlaneClient, active_states: list[str]) -> PlaneReadiness:
    states = client.list_states()
    existing = {str(state.get("name", "")).strip().lower() for state in states}
    missing = tuple(state for state in REQUIRED_STATES if state.lower() not in existing)

    active = {state.lower() for state in active_states}
    items = client.list_work_items()
    candidate_count = 0
    for item in items:
        state_name = str(item.get("state_detail", {}).get("name") or item.get("state", ""))
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
    return PlaneBootstrapResult(created_states=tuple(created), readiness=after)
