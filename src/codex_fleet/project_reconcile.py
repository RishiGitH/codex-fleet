from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from codex_fleet.config import FleetConfig, load_config, write_plane_tracker_config
from codex_fleet.factory import build_plane_client
from codex_fleet.plane import PlaneClient, PlaneSettings, plane_project_external_id
from codex_fleet.plane_bootstrap import ensure_plane_labels, ensure_plane_states
from codex_fleet.plane_local_bootstrap import bootstrap_local_plane
from codex_fleet.plane_manager import DEFAULT_PLANE_URL
from codex_fleet.project_registry import LocalProject, ProjectRegistry, discover_git_root

ProjectPathStatus = Literal["ok", "missing_folder", "not_git"]
ProjectPlaneStatus = Literal["linked", "relinked", "created", "stale", "error", "skipped"]


class PlaneProjectClient(Protocol):
    settings: PlaneSettings

    def list_projects(self) -> list[dict[str, object]]: ...

    def ensure_project(self, *, name: str, identifier_seed: str, external_id: str | None = None) -> dict[str, object]: ...

    def join_projects(self, project_ids: list[str]) -> None: ...


@dataclass(frozen=True)
class ProjectReconciliation:
    project: LocalProject
    path_status: ProjectPathStatus
    plane_status: ProjectPlaneStatus
    status_message: str
    can_run: bool
    workspace_slug: str | None = None
    project_id: str | None = None
    created_states: tuple[str, ...] = ()
    created_labels: tuple[str, ...] = ()
    config_path: Path | None = None

    def plane_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.plane_status,
            "reason": self.status_message,
        }
        if self.workspace_slug:
            payload["workspace_slug"] = self.workspace_slug
        if self.project_id:
            payload["project_id"] = self.project_id
        if self.created_states:
            payload["created_states"] = list(self.created_states)
        if self.created_labels:
            payload["created_labels"] = list(self.created_labels)
        if self.config_path is not None:
            payload["config_path"] = str(self.config_path)
        return payload


def reconcile_registered_projects(
    repo: Path,
    registry: ProjectRegistry,
    *,
    control_config: FleetConfig | None = None,
    allow_bootstrap: bool = True,
) -> list[ProjectReconciliation]:
    return [
        reconcile_project(
            repo,
            registry,
            project,
            control_config=control_config,
            allow_bootstrap=allow_bootstrap,
        )
        for project in registry.list_projects()
    ]


def reconcile_project(
    repo: Path,
    registry: ProjectRegistry,
    project: LocalProject,
    *,
    control_config: FleetConfig | None = None,
    allow_bootstrap: bool = True,
) -> ProjectReconciliation:
    path_status = project_path_status(project)
    if path_status == "missing_folder":
        return ProjectReconciliation(
            project=project,
            path_status=path_status,
            plane_status="skipped",
            status_message="Project folder is missing.",
            can_run=False,
        )
    if path_status == "not_git":
        return ProjectReconciliation(
            project=project,
            path_status=path_status,
            plane_status="skipped",
            status_message="Project folder is not a git repository.",
            can_run=False,
        )

    try:
        control_config = control_config or load_or_bootstrap_plane_config(repo, allow_bootstrap=allow_bootstrap)
    except Exception as exc:
        return ProjectReconciliation(
            project=project,
            path_status=path_status,
            plane_status="skipped",
            status_message=f"Control config unavailable: {exc}",
            can_run=False,
        )
    if control_config.tracker.kind != "plane":
        return ProjectReconciliation(
            project=project,
            path_status=path_status,
            plane_status="skipped",
            status_message="Control repo is not board-backed.",
            can_run=False,
        )

    try:
        control_client: PlaneProjectClient = build_plane_client(control_config)
    except ValueError as exc:
        return ProjectReconciliation(
            project=project,
            path_status=path_status,
            plane_status="skipped",
            status_message=str(exc),
            can_run=False,
        )

    try:
        return _reconcile_plane_mapping(control_config, control_client, registry, project, path_status)
    except Exception as exc:
        return ProjectReconciliation(
            project=project,
            path_status=path_status,
            plane_status="error",
            status_message=str(exc),
            can_run=False,
        )


def project_path_status(project: LocalProject) -> ProjectPathStatus:
    repo = project.repo_path.expanduser().absolute()
    if not repo.exists():
        return "missing_folder"
    if discover_git_root(repo) is None:
        return "not_git"
    return "ok"


def _reconcile_plane_mapping(
    control_config: FleetConfig,
    control_client: PlaneProjectClient,
    registry: ProjectRegistry,
    project: LocalProject,
    path_status: ProjectPathStatus,
) -> ProjectReconciliation:
    workspace_slug = str(control_client.settings.workspace_slug)
    base_url = str(control_client.settings.base_url)
    api_key = str(control_client.settings.api_key)

    projects = control_client.list_projects()
    projects_by_id = {str(candidate.get("id")): candidate for candidate in projects if candidate.get("id")}
    previous_project_id = project.plane_project_id
    plane_project_id: str | None = previous_project_id
    plane_status: ProjectPlaneStatus = "linked"

    if project.repo_path.expanduser().absolute() == control_config.repo.expanduser().absolute() and control_client.settings.project_id:
        plane_project_id = str(control_client.settings.project_id)
        plane_status = "linked" if previous_project_id == plane_project_id else "relinked"
    elif plane_project_id and plane_project_id in projects_by_id:
        existing = projects_by_id[plane_project_id]
        if existing.get("is_member") is False:
            control_client.join_projects([plane_project_id])
        plane_status = "linked"
    else:
        plane_project = _find_or_create_plane_project(
            control_client,
            projects,
            project,
        )
        plane_project_id = str(plane_project["id"])
        plane_status = _resolved_plane_status(previous_project_id, plane_project, projects)

    mapped = registry.update_plane_mapping(
        project.id,
        workspace_slug=workspace_slug,
        project_id_in_plane=plane_project_id,
    )
    project_client = PlaneClient(
        PlaneSettings(
            base_url=base_url,
            api_key=api_key,
            workspace_slug=workspace_slug,
            project_id=plane_project_id,
        )
    )
    state_result = ensure_plane_states(project_client, control_config.tracker.active_states)
    created_labels = ensure_plane_labels(project_client)
    config_path = write_plane_tracker_config(
        mapped.repo_path,
        base_url=base_url,
        workspace_slug=workspace_slug,
        project_id=plane_project_id,
        api_key_value=api_key,
        codex_settings=mapped.codex_settings,
    )
    return ProjectReconciliation(
        project=mapped,
        path_status=path_status,
        plane_status=plane_status,
        status_message=_status_message(plane_status),
        can_run=True,
        workspace_slug=workspace_slug,
        project_id=plane_project_id,
        created_states=tuple(state_result.created_states),
        created_labels=tuple(created_labels),
        config_path=config_path,
    )


def _find_or_create_plane_project(
    control_client: PlaneProjectClient,
    projects: list[dict[str, object]],
    project: LocalProject,
) -> dict[str, object]:
    external_id = plane_project_external_id(project.repo_path)
    for candidate in projects:
        if candidate.get("external_source") == "codex-fleet" and candidate.get("external_id") == external_id:
            return candidate

    target_name = _plane_project_name(project.name).lower()
    target_identifier = _identifier(project.slug)
    for candidate in projects:
        candidate_name = str(candidate.get("name", "")).strip().lower()
        candidate_identifier = _identifier(str(candidate.get("identifier", "")))
        if candidate_name == target_name or (target_identifier and candidate_identifier == target_identifier):
            return candidate

    return control_client.ensure_project(
        name=project.name,
        identifier_seed=project.slug,
        external_id=external_id,
    )


def _resolved_plane_status(
    previous_project_id: str | None,
    plane_project: dict[str, object],
    existing_projects: list[dict[str, object]],
) -> ProjectPlaneStatus:
    project_id = str(plane_project.get("id", ""))
    if not previous_project_id:
        return "created" if project_id not in {str(project.get("id")) for project in existing_projects} else "relinked"
    if previous_project_id == project_id:
        return "linked"
    return "relinked" if project_id in {str(project.get("id")) for project in existing_projects} else "created"


def load_or_bootstrap_plane_config(repo: Path, *, allow_bootstrap: bool = True) -> FleetConfig:
    config = load_config(repo)
    if config.tracker.kind == "plane" or not allow_bootstrap:
        return config
    result = bootstrap_local_plane(repo)
    write_plane_tracker_config(
        repo,
        base_url=DEFAULT_PLANE_URL,
        workspace_slug=result.workspace_slug,
        project_id=result.project_id,
        api_key_value=result.api_key,
    )
    return load_config(repo)


def _plane_project_name(name: str) -> str:
    return " ".join(name.strip().split())[:255] or "Codex Fleet Project"


def _identifier(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())[:12]


def _status_message(status: ProjectPlaneStatus) -> str:
    if status == "linked":
        return "Project is linked and ready."
    if status == "relinked":
        return "Project was relinked to the current local workspace."
    if status == "created":
        return "Project backing board was recreated in the current local workspace."
    return "Project needs attention."
