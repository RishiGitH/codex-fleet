from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

from codex_fleet.config import FleetConfig
from codex_fleet.plane_manager import inspect_plane_runtime

REQUIRED_PROJECT_VIEWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "codex-fleet Cockpit",
        ("Backlog", "Planning", "Ready", "Running", "Needs Input", "Human Review", "Rework", "Done", "Blocked", "Cancelled"),
    ),
    ("codex-fleet Ready", ("Ready",)),
    ("codex-fleet Running", ("Running",)),
    ("codex-fleet Planning", ("Planning",)),
    ("codex-fleet Needs Input", ("Needs Input",)),
    ("codex-fleet Human Review", ("Human Review",)),
    ("codex-fleet Rework", ("Rework",)),
    ("codex-fleet Blocked", ("Blocked",)),
    ("codex-fleet Done", ("Done",)),
    ("codex-fleet Agent proposals", ("Backlog",)),
)


class PlaneViewBootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlaneViewBootstrapResult:
    created: tuple[str, ...]
    existing: tuple[str, ...]
    skipped_reason: str | None = None

    @property
    def skipped(self) -> bool:
        return self.skipped_reason is not None


def ensure_local_plane_project_views(config: FleetConfig) -> PlaneViewBootstrapResult:
    """Create codex-fleet saved project views in local self-hosted Plane.

    Plane's project saved-view endpoints live on the app route and require a
    browser/session auth context in the current self-host release. The API-key
    `/api/v1` surface used by `PlaneClient` does not expose them. For the local
    one-command demo, use Plane's own Django container to create the same
    `IssueView` records without changing Plane models or migrations.
    """
    if config.tracker.kind != "plane":
        return PlaneViewBootstrapResult(created=(), existing=(), skipped_reason="tracker is not Plane")
    base_url = config.tracker.plane_base_url or ""
    if not _is_loopback_url(base_url):
        return PlaneViewBootstrapResult(created=(), existing=(), skipped_reason="Plane URL is not loopback")
    if not config.tracker.plane_api_key:
        return PlaneViewBootstrapResult(created=(), existing=(), skipped_reason="Plane API key is not configured")
    if not config.tracker.plane_workspace_slug or not config.tracker.plane_project_id:
        return PlaneViewBootstrapResult(created=(), existing=(), skipped_reason="Plane workspace/project is not configured")

    install = inspect_plane_runtime(config.repo)
    compose_file = install.app_dir / "docker-compose.yaml" if install.app_dir is not None else None
    if compose_file is None or not compose_file.exists():
        return PlaneViewBootstrapResult(created=(), existing=(), skipped_reason="local Plane Docker app is not installed")

    env = os.environ.copy()
    env["CODEX_FLEET_PLANE_API_KEY"] = config.tracker.plane_api_key
    env["CODEX_FLEET_PLANE_WORKSPACE_SLUG"] = config.tracker.plane_workspace_slug
    env["CODEX_FLEET_PLANE_PROJECT_ID"] = config.tracker.plane_project_id
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "exec",
            "-T",
            "-e",
            "CODEX_FLEET_PLANE_API_KEY",
            "-e",
            "CODEX_FLEET_PLANE_WORKSPACE_SLUG",
            "-e",
            "CODEX_FLEET_PLANE_PROJECT_ID",
            "api",
            "python",
            "manage.py",
            "shell",
            "-c",
            _DJANGO_VIEW_BOOTSTRAP_SCRIPT,
        ],
        cwd=install.app_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PlaneViewBootstrapError(detail or "failed to create Plane saved views")
    created: list[str] = []
    existing: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("created:"):
            created.append(line.removeprefix("created:"))
        elif line.startswith("existing:"):
            existing.append(line.removeprefix("existing:"))
    return PlaneViewBootstrapResult(created=tuple(created), existing=tuple(existing))


def _is_loopback_url(url: str) -> bool:
    host = urlparse(url).hostname
    return host in {"127.0.0.1", "localhost", "::1"}


_DJANGO_VIEW_BOOTSTRAP_SCRIPT = r"""
import os

from plane.db.models import APIToken, IssueView, Label, Project, State, Workspace

api_key = os.environ["CODEX_FLEET_PLANE_API_KEY"]
workspace_slug = os.environ["CODEX_FLEET_PLANE_WORKSPACE_SLUG"]
project_id = os.environ["CODEX_FLEET_PLANE_PROJECT_ID"]

workspace = Workspace.objects.get(slug=workspace_slug)
project = Project.objects.get(id=project_id, workspace=workspace)
token = APIToken.objects.select_related("user").get(token=api_key, is_active=True)
owner = token.user
required = (
    ("codex-fleet Cockpit", ("Backlog", "Planning", "Ready", "Running", "Needs Input", "Human Review", "Rework", "Done", "Blocked", "Cancelled"), ()),
    ("codex-fleet Ready", ("Ready",), ()),
    ("codex-fleet Running", ("Running",), ()),
    ("codex-fleet Planning", ("Planning",), ()),
    ("codex-fleet Needs Input", ("Needs Input",), ()),
    ("codex-fleet Human Review", ("Human Review",), ()),
    ("codex-fleet Rework", ("Rework",), ()),
    ("codex-fleet Blocked", ("Blocked",), ()),
    ("codex-fleet Done", ("Done",), ()),
    ("codex-fleet Agent proposals", ("Backlog",), ("agent-proposed",)),
)
state_ids = {
    state.name: str(state.id)
    for state in State.objects.filter(workspace=workspace, project=project, name__in={name for _, names, _ in required for name in names})
}
label_ids = {
    label.name: str(label.id)
    for label in Label.objects.filter(workspace=workspace, project=project, name__in={name for _, _, names in required for name in names})
}

display_filters = {
    "layout": "kanban",
    "group_by": "state",
    "order_by": "sort_order",
    "sub_issue": True,
    "show_empty_groups": True,
}
display_properties = {
    "key": True,
    "state": True,
    "priority": True,
    "assignee": True,
    "labels": True,
    "due_date": True,
    "updated_on": True,
    "created_on": True,
    "attachment_count": True,
    "sub_issue_count": True,
}
logo_props = {"in_use": "icon", "icon": "kanban", "color": "#22d3ee"}

for view_name, state_names, label_names in required:
    filters = {"state": [state_ids[name] for name in state_names if name in state_ids]}
    if label_names:
        filters["labels"] = [label_ids[name] for name in label_names if name in label_ids]
    view, created = IssueView.objects.get_or_create(
        workspace=workspace,
        project=project,
        name=view_name,
        defaults={
            "description": "codex-fleet local control-plane view",
            "filters": filters,
            "display_filters": display_filters,
            "display_properties": display_properties,
            "rich_filters": {},
            "access": 1,
            "logo_props": logo_props,
            "owned_by": owner,
            "created_by": owner,
            "updated_by": owner,
        },
    )
    if not created:
        view.filters = filters
        view.display_filters = display_filters
        view.display_properties = display_properties
        view.rich_filters = {}
        view.access = 1
        view.logo_props = logo_props
        view.owned_by = owner
        view.updated_by = owner
        view.save()
    print(("created:" if created else "existing:") + view_name)
"""
