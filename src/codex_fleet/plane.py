from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
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
        return {"X-Api-Key": self.settings.api_key, "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.settings.base_url.rstrip('/')}{path}"

    def _project_path(self, suffix: str) -> str:
        return f"/api/v1/workspaces/{self.settings.workspace_slug}/projects/{self.settings.project_id}/{suffix}"

    def _workspace_project_path(self, suffix: str = "") -> str:
        return f"/api/v1/workspaces/{self.settings.workspace_slug}/projects/{suffix}"

    def list_projects(self) -> list[dict[str, Any]]:
        payload = self._get_json(self._workspace_project_path())
        if isinstance(payload, dict) and "results" in payload:
            return list(payload["results"])
        if isinstance(payload, list):
            return payload
        raise TrackerError(f"Unexpected Plane projects response: {type(payload).__name__}")

    def create_project(
        self,
        *,
        name: str,
        identifier: str,
        external_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": plane_project_name(name),
            "identifier": identifier,
            "description": "Managed by codex-fleet local control center.",
            "module_view": False,
            "cycle_view": False,
            "issue_views_view": True,
            "page_view": False,
            "intake_view": False,
            "network": 2,
        }
        if external_id:
            payload["external_source"] = "codex-fleet"
            payload["external_id"] = external_id
        with httpx.Client(timeout=self.timeout) as client:
            response = self._request_with_backoff(client, "POST", self._url(self._workspace_project_path()), json=payload)
            response_payload = response.json()
        if not isinstance(response_payload, dict):
            raise TrackerError(f"Unexpected Plane create project response: {type(response_payload).__name__}")
        return response_payload

    def update_project(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = self._request_with_backoff(
                client,
                "PATCH",
                self._url(self._workspace_project_path(f"{project_id}/")),
                json=payload,
            )
            response_payload = response.json()
        if not isinstance(response_payload, dict):
            raise TrackerError(f"Unexpected Plane update project response: {type(response_payload).__name__}")
        return response_payload

    def join_projects(self, project_ids: list[str]) -> None:
        if not project_ids:
            return
        path = f"/api/users/me/workspaces/{self.settings.workspace_slug}/projects/invitations/"
        with httpx.Client(timeout=self.timeout) as client:
            self._request_with_backoff(client, "POST", self._url(path), json={"project_ids": project_ids})

    def ensure_project(
        self,
        *,
        name: str,
        identifier_seed: str,
        external_id: str | None = None,
    ) -> dict[str, Any]:
        projects = self.list_projects()
        for project in projects:
            if external_id and project.get("external_source") == "codex-fleet" and project.get("external_id") == external_id:
                return project
        if external_id is None:
            target_name = plane_project_name(name).lower()
            for project in projects:
                if str(project.get("name", "")).strip().lower() == target_name:
                    return project

        used_identifiers = {str(project.get("identifier", "")).strip().upper() for project in projects}
        for identifier in plane_project_identifier_candidates(identifier_seed):
            if identifier not in used_identifiers:
                return self.create_project(name=name, identifier=identifier, external_id=external_id)
        raise TrackerError(f"No available Plane project identifier for: {identifier_seed}")

    def list_work_items(self) -> list[dict[str, Any]]:
        path = self._project_path("work-items/")
        payload = self._get_json(path)
        if isinstance(payload, dict) and "results" in payload:
            return list(payload["results"])
        if isinstance(payload, list):
            return payload
        raise TrackerError(f"Unexpected Plane list response: {type(payload).__name__}")

    def list_states(self) -> list[dict[str, Any]]:
        path = self._project_path("states/")
        payload = self._get_json(path)
        if isinstance(payload, dict) and "results" in payload:
            return list(payload["results"])
        if isinstance(payload, list):
            return payload
        raise TrackerError(f"Unexpected Plane states response: {type(payload).__name__}")

    def list_labels(self) -> list[dict[str, Any]]:
        path = self._project_path("labels/")
        payload = self._get_json(path)
        if isinstance(payload, dict) and "results" in payload:
            return list(payload["results"])
        if isinstance(payload, list):
            return payload
        raise TrackerError(f"Unexpected Plane labels response: {type(payload).__name__}")

    def create_label(self, name: str, color: str = "#6B7280") -> dict[str, Any]:
        path = self._project_path("labels/")
        with httpx.Client(timeout=self.timeout) as client:
            response = self._request_with_backoff(client, "POST", self._url(path), json={"name": name, "color": color})
            payload = response.json()
        if not isinstance(payload, dict):
            raise TrackerError(f"Unexpected Plane create label response: {type(payload).__name__}")
        return payload

    def create_state(self, name: str, group: str, color: str = "#6B7280") -> dict[str, Any]:
        path = self._project_path("states/")
        with httpx.Client(timeout=self.timeout) as client:
            response = self._request_with_backoff(
                client,
                "POST",
                self._url(path),
                json={"name": name, "group": group, "color": color},
            )
            payload = response.json()
        if not isinstance(payload, dict):
            raise TrackerError(f"Unexpected Plane create state response: {type(payload).__name__}")
        return payload

    def create_work_item(
        self,
        name: str,
        description_html: str,
        state_id: str | None = None,
        label_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        path = self._project_path("work-items/")
        payload: dict[str, Any] = {"name": name, "description_html": description_html}
        if state_id is not None:
            payload["state"] = state_id
        if label_ids:
            payload["labels"] = list(label_ids)
        with httpx.Client(timeout=self.timeout) as client:
            response = self._request_with_backoff(client, "POST", self._url(path), json=payload)
            response_payload = response.json()
        if not isinstance(response_payload, dict):
            raise TrackerError(f"Unexpected Plane create work item response: {type(response_payload).__name__}")
        return response_payload

    def label_ids_by_names(self, names: tuple[str, ...]) -> tuple[str, ...]:
        wanted = {name.strip().lower() for name in names if name.strip()}
        if not wanted:
            return ()
        label_ids: list[str] = []
        for label in self.list_labels():
            name = str(label.get("name", "")).strip().lower()
            label_id = label.get("id")
            if name in wanted and isinstance(label_id, str) and label_id:
                label_ids.append(label_id)
        return tuple(label_ids)

    def update_work_item(self, item_id: str, payload: dict[str, Any]) -> None:
        path = self._project_path(f"work-items/{item_id}/")
        with httpx.Client(timeout=self.timeout) as client:
            self._request_with_backoff(client, "PATCH", self._url(path), json=payload)

    def create_work_item_comment(self, item_id: str, body: str) -> None:
        path = self._project_path(f"work-items/{item_id}/comments/")
        with httpx.Client(timeout=self.timeout) as client:
            self._request_with_backoff(client, "POST", self._url(path), json={"comment_html": body})

    def _get_json(self, path: str) -> Any:
        with httpx.Client(timeout=self.timeout) as client:
            response = self._request_with_backoff(client, "GET", self._url(path))
            return response.json()

    def _request_with_backoff(self, client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_response: httpx.Response | None = None
        retryable_statuses = {429, 502, 503, 504}
        for attempt in range(8):
            response = client.request(method, url, headers=self.headers, **kwargs)
            if response.status_code not in retryable_statuses:
                response.raise_for_status()
                return response
            last_response = response
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 1.5 * (attempt + 1)
            except ValueError:
                delay = 1.5 * (attempt + 1)
            time.sleep(min(delay, 8.0))
        assert last_response is not None
        last_response.raise_for_status()
        return last_response


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


def plane_project_identifier_candidates(seed: str) -> list[str]:
    base = re.sub(r"[^A-Z0-9]+", "", seed.upper())
    if not base:
        base = "CF"
    base = base[:12]
    candidates = [base]
    prefix = base[:10] or "CF"
    candidates.extend(f"{prefix}{index}"[:12] for index in range(2, 100))
    return candidates


def plane_project_name(seed: str) -> str:
    name = re.sub(r"[^A-Za-z0-9 ]+", " ", seed).strip()
    name = re.sub(r"\s+", " ", name)
    return name or "Codex Fleet Project"


def plane_project_external_id(repo_path: Path) -> str:
    return str(repo_path.expanduser().resolve())


class PlaneTracker(Tracker):
    def __init__(self, client: PlaneClient, active_states: list[str]) -> None:
        self.client = client
        self.active_states = {state.lower() for state in active_states}
        self._state_cache: list[dict[str, Any]] | None = None

    def fetch_candidate_items(self) -> list[WorkItem]:
        items = [normalize_plane_item(self._with_state_detail(raw)) for raw in self.client.list_work_items()]
        return [item for item in items if item.state.lower() in self.active_states]

    def fetch_items_by_ids(self, ids: list[str]) -> list[WorkItem]:
        wanted = set(ids)
        items = [normalize_plane_item(self._with_state_detail(raw)) for raw in self.client.list_work_items()]
        return [item for item in items if item.id in wanted]

    def update_item_state(self, item_id: str, state: str) -> None:
        state_id = state_id_by_name(self._states(), state)
        self.client.update_work_item(item_id, {"state": state_id})

    def create_comment(self, item_id: str, body: str) -> None:
        self.client.create_work_item_comment(item_id, body)

    def create_work_item(
        self,
        *,
        title: str,
        description: str | None,
        state: str,
        labels: tuple[str, ...] = (),
    ) -> WorkItem:
        state_id = state_id_by_name(self._states(), state)
        label_ids = self.client.label_ids_by_names(labels)
        return normalize_plane_item(
            self._with_state_detail(
                self.client.create_work_item(
                    name=title,
                    description_html=description or "",
                    state_id=state_id,
                    label_ids=label_ids,
                )
            )
        )

    def _states(self) -> list[dict[str, Any]]:
        if self._state_cache is None:
            self._state_cache = self.client.list_states()
        return self._state_cache

    def _with_state_detail(self, raw: dict[str, Any]) -> dict[str, Any]:
        if isinstance(raw.get("state_detail"), dict):
            return raw
        state_id = raw.get("state")
        if not isinstance(state_id, str):
            return raw
        for state in self._states():
            if state.get("id") == state_id:
                return {**raw, "state_detail": {"name": state.get("name"), "id": state_id}}
        return raw
