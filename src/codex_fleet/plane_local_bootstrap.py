from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codex_fleet.plane_manager import inspect_plane_runtime


class PlaneLocalBootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlaneLocalBootstrapResult:
    workspace_slug: str
    workspace_created: bool
    project_id: str
    project_created: bool
    project_name: str
    api_key: str
    token_created: bool
    user_email: str


@dataclass(frozen=True)
class PlaneLocalSessionResult:
    session_key: str
    user_email: str


def bootstrap_local_plane(
    repo: Path,
    *,
    workspace_slug: str = "codex-fleet",
    workspace_name: str = "codex-fleet",
    project_name: str = "Codex Fleet",
    project_identifier: str = "CF",
    user_email: str = "codex-fleet-local@example.local",
) -> PlaneLocalBootstrapResult:
    """Create/reuse local Plane data for codex-fleet without browser setup."""
    install = inspect_plane_runtime(repo.expanduser().absolute())
    compose_file = install.app_dir / "docker-compose.yaml" if install.app_dir is not None else None
    if compose_file is None or not compose_file.exists():
        raise PlaneLocalBootstrapError("local Plane Docker app is not installed")

    env = os.environ.copy()
    env["CODEX_FLEET_PLANE_WORKSPACE_SLUG"] = workspace_slug
    env["CODEX_FLEET_PLANE_WORKSPACE_NAME"] = workspace_name
    env["CODEX_FLEET_PLANE_PROJECT_NAME"] = project_name
    env["CODEX_FLEET_PLANE_PROJECT_IDENTIFIER"] = project_identifier
    env["CODEX_FLEET_PLANE_USER_EMAIL"] = user_email
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "exec",
            "-T",
            "-e",
            "CODEX_FLEET_PLANE_WORKSPACE_SLUG",
            "-e",
            "CODEX_FLEET_PLANE_WORKSPACE_NAME",
            "-e",
            "CODEX_FLEET_PLANE_PROJECT_NAME",
            "-e",
            "CODEX_FLEET_PLANE_PROJECT_IDENTIFIER",
            "-e",
            "CODEX_FLEET_PLANE_USER_EMAIL",
            "api",
            "python",
            "manage.py",
            "shell",
            "-c",
            _DJANGO_LOCAL_BOOTSTRAP_SCRIPT,
        ],
        cwd=install.app_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PlaneLocalBootstrapError(detail or "failed to bootstrap local Plane")
    payload = _load_last_json_line(result.stdout)
    return PlaneLocalBootstrapResult(
        workspace_slug=str(payload["workspace_slug"]),
        workspace_created=bool(payload["workspace_created"]),
        project_id=str(payload["project_id"]),
        project_created=bool(payload["project_created"]),
        project_name=str(payload["project_name"]),
        api_key=str(payload["api_key"]),
        token_created=bool(payload["token_created"]),
        user_email=str(payload["user_email"]),
    )


def create_local_plane_session(
    repo: Path,
    *,
    user_email: str = "codex-fleet-local@example.local",
) -> PlaneLocalSessionResult:
    """Create a Plane browser session for the local codex-fleet user."""
    install = inspect_plane_runtime(repo.expanduser().absolute())
    compose_file = install.app_dir / "docker-compose.yaml" if install.app_dir is not None else None
    if compose_file is None or not compose_file.exists():
        raise PlaneLocalBootstrapError("local Plane Docker app is not installed")

    env = os.environ.copy()
    env["CODEX_FLEET_PLANE_USER_EMAIL"] = user_email
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "exec",
            "-T",
            "-e",
            "CODEX_FLEET_PLANE_USER_EMAIL",
            "api",
            "python",
            "manage.py",
            "shell",
            "-c",
            _DJANGO_LOCAL_SESSION_SCRIPT,
        ],
        cwd=install.app_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PlaneLocalBootstrapError(detail or "failed to create local Plane session")
    payload = _load_last_json_line(result.stdout)
    return PlaneLocalSessionResult(
        session_key=str(payload["session_key"]),
        user_email=str(payload["user_email"]),
    )


def _load_last_json_line(output: str) -> dict[str, object]:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise PlaneLocalBootstrapError("local Plane bootstrap did not return JSON")


_DJANGO_LOCAL_BOOTSTRAP_SCRIPT = r"""
import json
import os

from plane.db.models import APIToken, Profile, Project, ProjectMember, User, Workspace, WorkspaceMember

workspace_slug = os.environ["CODEX_FLEET_PLANE_WORKSPACE_SLUG"]
workspace_name = os.environ["CODEX_FLEET_PLANE_WORKSPACE_NAME"]
project_name = os.environ["CODEX_FLEET_PLANE_PROJECT_NAME"]
project_identifier = os.environ["CODEX_FLEET_PLANE_PROJECT_IDENTIFIER"]
user_email = os.environ["CODEX_FLEET_PLANE_USER_EMAIL"].lower().strip()

user, user_created = User.objects.get_or_create(
    email=user_email,
    defaults={
        "username": user_email,
        "display_name": "codex-fleet local",
        "first_name": "codex-fleet",
        "last_name": "local",
        "is_active": True,
        "is_email_verified": True,
        "is_password_autoset": True,
    },
)
if user_created:
    user.set_unusable_password()
    user.save()

workspace, workspace_created = Workspace.objects.get_or_create(
    slug=workspace_slug,
    defaults={
        "name": workspace_name,
        "owner": user,
        "organization_size": "1-10",
    },
)
WorkspaceMember.objects.update_or_create(
    workspace=workspace,
    member=user,
    defaults={"role": 20, "is_active": True},
)
Profile.objects.update_or_create(
    user=user,
    defaults={
        "is_onboarded": True,
        "is_tour_completed": True,
        "is_navigation_tour_completed": True,
        "is_mobile_onboarded": True,
        "last_workspace_id": workspace.id,
        "company_name": workspace_name,
        "role": "Local codex-fleet operator",
        "use_case": "Run local Codex agents from Plane work items.",
        "onboarding_step": {
            "profile_complete": True,
            "workspace_create": True,
            "workspace_invite": True,
            "workspace_join": True,
        },
        "mobile_onboarding_step": {
            "profile_complete": True,
            "workspace_create": True,
            "workspace_join": True,
        },
    },
)

project = Project.objects.filter(
    workspace=workspace,
    external_source="codex-fleet",
    external_id="default-local-project",
).first()
project_created = False
if project is None:
    project = Project.objects.filter(workspace=workspace, identifier=project_identifier).first()
if project is None:
    identifier = project_identifier
    suffix = 2
    while Project.objects.filter(workspace=workspace, identifier=identifier).exists():
        identifier = f"{project_identifier}{suffix}"
        suffix += 1
    project = Project.objects.create(
        workspace=workspace,
        name=project_name,
        identifier=identifier,
        project_lead=user,
        default_assignee=user,
        external_source="codex-fleet",
        external_id="default-local-project",
        network=0,
    )
    project_created = True
else:
    changed = False
    if project.external_source != "codex-fleet":
        project.external_source = "codex-fleet"
        changed = True
    if project.external_id != "default-local-project":
        project.external_id = "default-local-project"
        changed = True
    if project.project_lead_id is None:
        project.project_lead = user
        changed = True
    if project.default_assignee_id is None:
        project.default_assignee = user
        changed = True
    if changed:
        project.save()

ProjectMember.objects.update_or_create(
    workspace=workspace,
    project=project,
    member=user,
    defaults={"role": 20, "is_active": True},
)

token, token_created = APIToken.objects.get_or_create(
    user=user,
    workspace=workspace,
    label="codex-fleet local",
    defaults={
        "description": "Local codex-fleet API token created by codex-fleet.",
        "user_type": 0,
        "is_service": False,
        "is_active": True,
        "allowed_rate_limit": "1000/min",
    },
)
if not token.is_active:
    token.is_active = True
    token.save()

print(json.dumps({
    "workspace_slug": workspace.slug,
    "workspace_created": workspace_created,
    "project_id": str(project.id),
    "project_created": project_created,
    "project_name": project.name,
    "api_key": token.token,
    "token_created": token_created,
    "user_email": user.email,
}))
"""


_DJANGO_LOCAL_SESSION_SCRIPT = r"""
import json
import os

from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.utils import timezone
from plane.db.models import Profile, User, Workspace
from plane.db.models.session import SessionStore

user_email = os.environ["CODEX_FLEET_PLANE_USER_EMAIL"].lower().strip()
user = User.objects.get(email=user_email, is_active=True)
workspace = (
    Workspace.objects.filter(workspace_member__member_id=user.id, workspace_member__is_active=True)
    .order_by("created_at")
    .first()
)
profile, _ = Profile.objects.get_or_create(user=user)
profile_changed = False
if workspace is not None and profile.last_workspace_id != workspace.id:
    profile.last_workspace_id = workspace.id
    profile_changed = True
if not profile.is_onboarded:
    profile.is_onboarded = True
    profile_changed = True
if not profile.is_tour_completed:
    profile.is_tour_completed = True
    profile_changed = True
if not profile.is_navigation_tour_completed:
    profile.is_navigation_tour_completed = True
    profile_changed = True
expected_onboarding_step = {
    "profile_complete": True,
    "workspace_create": True,
    "workspace_invite": True,
    "workspace_join": True,
}
if profile.onboarding_step != expected_onboarding_step:
    profile.onboarding_step = expected_onboarding_step
    profile_changed = True
if profile_changed:
    profile.save()

session = SessionStore()
session[SESSION_KEY] = str(user.pk)
session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
session[HASH_SESSION_KEY] = user.get_session_auth_hash()
session["device_info"] = {
    "user_agent": "codex-fleet local",
    "ip_address": "127.0.0.1",
    "domain": "codex-fleet local",
}
session.set_expiry(604800)
session.save()
user.last_login_time = timezone.now()
user.last_login_medium = "codex-fleet-local"
user.save(update_fields=["last_login_time", "last_login_medium", "updated_at"])

print(json.dumps({
    "session_key": session.session_key,
    "user_email": user.email,
}))
"""
