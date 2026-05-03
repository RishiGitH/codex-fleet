from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from codex_fleet.models import WorkItem
from codex_fleet.tracker import Tracker, TrackerError


@dataclass(frozen=True)
class PlaneSettings:
    base_url: str
    api_key: str
    workspace_slug: str
    project_id: str


class PlaneClient:
    def __init__(self, settings: PlaneSettings, timeout: float = 20.0) -> None:
        self.settings = settings
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.settings.api_key, "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.settings.base_url.rstrip('/')}{path}"

    def list_work_items(self) -> list[dict[str, Any]]:
        path = f"/api/v1/workspaces/{self.settings.workspace_slug}/projects/{self.settings.project_id}/work-items/"
        payload = self._get_json(path)
        if isinstance(payload, dict) and "results" in payload:
            return list(payload["results"])
        if isinstance(payload, list):
            return payload
        raise TrackerError(f"Unexpected Plane list response: {type(payload).__name__}")

    def list_states(self) -> list[dict[str, Any]]:
        path = f"/api/v1/workspaces/{self.settings.workspace_slug}/projects/{self.settings.project_id}/states/"
        payload = self._get_json(path)
        if isinstance(payload, dict) and "results" in payload:
            return list(payload["results"])
        if isinstance(payload, list):
            return payload
        raise TrackerError(f"Unexpected Plane states response: {type(payload).__name__}")

    def update_work_item(self, item_id: str, payload: dict[str, Any]) -> None:
        path = f"/api/v1/workspaces/{self.settings.workspace_slug}/projects/{self.settings.project_id}/work-items/{item_id}/"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.patch(self._url(path), headers=self.headers, json=payload)
            response.raise_for_status()

    def create_work_item_comment(self, item_id: str, body: str) -> None:
        path = f"/api/v1/workspaces/{self.settings.workspace_slug}/projects/{self.settings.project_id}/work-items/{item_id}/comments/"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self._url(path), headers=self.headers, json={"comment_html": body})
            response.raise_for_status()

    def _get_json(self, path: str) -> Any:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(self._url(path), headers=self.headers)
            response.raise_for_status()
            return response.json()


def normalize_plane_item(raw: dict[str, Any]) -> WorkItem:
    project_identifier = str(raw.get("project_detail", {}).get("identifier") or raw.get("project_identifier") or "PLN")
    sequence = raw.get("sequence_id") or raw.get("identifier") or raw.get("id")
    state = raw.get("state_detail", {}).get("name") or raw.get("state", "Backlog")
    priority = _priority_to_int(raw.get("priority"))
    labels = tuple(_normalize_label(label) for label in raw.get("label_details", raw.get("labels", [])))
    return WorkItem(
        id=str(raw["id"]),
        identifier=f"{project_identifier}-{sequence}",
        title=str(raw.get("name") or raw.get("title") or "Untitled"),
        description=raw.get("description_stripped") or raw.get("description_html") or raw.get("description"),
        state=str(state),
        priority=priority,
        url=raw.get("url"),
        labels=labels,
        raw=raw,
    )


def _normalize_label(label: Any) -> str:
    if isinstance(label, dict):
        return str(label.get("name") or label.get("id") or "").lower()
    return str(label).lower()


def _priority_to_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    mapping = {"urgent": 1, "high": 2, "medium": 3, "low": 4, "none": None, None: None}
    return mapping.get(str(value).lower() if value is not None else None)


def state_id_by_name(states: list[dict[str, Any]], state_name: str) -> str:
    target = state_name.strip().lower()
    for state in states:
        if str(state.get("name", "")).strip().lower() == target:
            state_id = state.get("id")
            if isinstance(state_id, str) and state_id:
                return state_id
    raise TrackerError(f"Plane state not found: {state_name}")


class PlaneTracker(Tracker):
    def __init__(self, client: PlaneClient, active_states: list[str]) -> None:
        self.client = client
        self.active_states = {state.lower() for state in active_states}
        self._state_cache: list[dict[str, Any]] | None = None

    def fetch_candidate_items(self) -> list[WorkItem]:
        items = [normalize_plane_item(raw) for raw in self.client.list_work_items()]
        return [item for item in items if item.state.lower() in self.active_states]

    def fetch_items_by_ids(self, ids: list[str]) -> list[WorkItem]:
        wanted = set(ids)
        items = [normalize_plane_item(raw) for raw in self.client.list_work_items()]
        return [item for item in items if item.id in wanted]

    def update_item_state(self, item_id: str, state: str) -> None:
        state_id = state_id_by_name(self._states(), state)
        self.client.update_work_item(item_id, {"state": state_id})

    def create_comment(self, item_id: str, body: str) -> None:
        self.client.create_work_item_comment(item_id, body)

    def _states(self) -> list[dict[str, Any]]:
        if self._state_cache is None:
            self._state_cache = self.client.list_states()
        return self._state_cache
