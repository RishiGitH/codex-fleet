from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import codex_fleet.project_reconcile as project_reconcile
from codex_fleet.config import FleetConfig, TrackerConfig
from codex_fleet.plane import PlaneSettings, plane_project_external_id
from codex_fleet.project_reconcile import reconcile_project
from codex_fleet.project_registry import ProjectRegistry


def test_reconcile_missing_folder_remains_visible(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    _init_git_repo(repo)
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    project = registry.add_project(repo, plane_workspace_slug="codex-local", plane_project_id="stale-project")
    shutil.rmtree(repo)

    result = reconcile_project(tmp_path, registry, project, control_config=_control_config(tmp_path), allow_bootstrap=False)

    assert result.path_status == "missing_folder"
    assert result.plane_status == "skipped"
    assert result.can_run is False
    assert "missing" in result.status_message


def test_reconcile_non_git_folder_remains_visible(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    _init_git_repo(repo)
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    project = registry.add_project(repo, plane_workspace_slug="codex-local", plane_project_id="stale-project")
    shutil.rmtree(repo / ".git")

    result = reconcile_project(tmp_path, registry, project, control_config=_control_config(tmp_path), allow_bootstrap=False)

    assert result.path_status == "not_git"
    assert result.plane_status == "skipped"
    assert result.can_run is False
    assert "not a git" in result.status_message


def test_reconcile_valid_mapping_remains_linked(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    _init_git_repo(repo)
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    project = registry.add_project(repo, plane_workspace_slug="codex-local", plane_project_id="plane-app")
    fake_client = _FakePlaneClient([{"id": "plane-app", "is_member": True}])
    _patch_plane(monkeypatch, fake_client)

    result = reconcile_project(tmp_path, registry, project, control_config=_control_config(tmp_path), allow_bootstrap=False)

    assert result.plane_status == "linked"
    assert result.can_run is True
    assert result.project.plane_project_id == "plane-app"
    assert fake_client.created == []


def test_reconcile_stale_mapping_relinks_by_external_id(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    _init_git_repo(repo)
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    project = registry.add_project(repo, plane_workspace_slug="codex-local", plane_project_id="old-plane-app")
    fake_client = _FakePlaneClient(
        [
            {
                "id": "new-plane-app",
                "is_member": True,
                "external_source": "codex-fleet",
                "external_id": plane_project_external_id(repo),
            }
        ]
    )
    _patch_plane(monkeypatch, fake_client)

    result = reconcile_project(tmp_path, registry, project, control_config=_control_config(tmp_path), allow_bootstrap=False)

    assert result.plane_status == "relinked"
    assert result.project.plane_project_id == "new-plane-app"
    assert registry.get_project(project.id).plane_project_id == "new-plane-app"
    assert fake_client.created == []


def test_reconcile_stale_mapping_creates_project(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    _init_git_repo(repo)
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    project = registry.add_project(repo, plane_workspace_slug="codex-local", plane_project_id="old-plane-app")
    fake_client = _FakePlaneClient([])
    _patch_plane(monkeypatch, fake_client)

    result = reconcile_project(tmp_path, registry, project, control_config=_control_config(tmp_path), allow_bootstrap=False)

    assert result.plane_status == "created"
    assert result.project.plane_project_id == "created-app"
    assert registry.get_project(project.id).plane_project_id == "created-app"
    assert fake_client.created == [{"name": "app", "identifier_seed": "app", "external_id": plane_project_external_id(repo)}]


class _FakePlaneClient:
    settings = PlaneSettings(
        base_url="http://plane.test",
        api_key="key",
        workspace_slug="codex-local",
        project_id="plane-control",
    )

    def __init__(self, projects: list[dict[str, object]]) -> None:
        self.projects = projects
        self.created: list[dict[str, str]] = []
        self.joined: list[list[str]] = []

    def list_projects(self) -> list[dict[str, object]]:
        return self.projects

    def ensure_project(self, *, name: str, identifier_seed: str, external_id: str) -> dict[str, object]:
        self.created.append({"name": name, "identifier_seed": identifier_seed, "external_id": external_id})
        return {"id": f"created-{identifier_seed}", "name": name, "identifier": identifier_seed}

    def join_projects(self, project_ids: list[str]) -> None:
        self.joined.append(project_ids)


def _patch_plane(monkeypatch, fake_client: _FakePlaneClient) -> None:
    monkeypatch.setattr(project_reconcile, "build_plane_client", lambda config: fake_client)
    monkeypatch.setattr(project_reconcile, "ensure_plane_states", lambda client, active_states: type("StateResult", (), {"created_states": ()})())
    monkeypatch.setattr(project_reconcile, "ensure_plane_labels", lambda client: ())
    monkeypatch.setattr(project_reconcile, "write_plane_tracker_config", lambda repo, **kwargs: repo / ".codex-fleet.yml")


def _control_config(repo: Path) -> FleetConfig:
    return FleetConfig(
        repo=repo,
        tracker=TrackerConfig(
            kind="plane",
            plane_base_url="http://plane.test",
            plane_api_key="key",
            plane_workspace_slug="codex-local",
            plane_project_id="plane-control",
        ),
    ).resolved()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
