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


@dataclass(frozen=True)
class PlaneReadiness:
    ok: bool
    missing_states: tuple[str, ...]
    state_count: int
    candidate_count: int


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
