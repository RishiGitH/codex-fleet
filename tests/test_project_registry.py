import sqlite3
from pathlib import Path

import pytest

from codex_fleet.project_registry import ProjectRegistry, ProjectRegistryError, slugify


def test_project_registry_adds_and_lists_folder(tmp_path: Path) -> None:
    project_dir = tmp_path / "My App"
    project_dir.mkdir()
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")

    project = registry.add_project(project_dir)

    assert project.name == "My App"
    assert project.slug == "my-app"
    assert project.repo_path == project_dir.resolve()
    assert project.git_root is None
    assert project.runner_mode == "codex"
    assert registry.list_projects() == [project]


def test_project_registry_updates_plane_mapping(tmp_path: Path) -> None:
    project_dir = tmp_path / "app"
    project_dir.mkdir()
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    project = registry.add_project(project_dir)

    mapped = registry.update_plane_mapping(
        project.id,
        workspace_slug="codex-local",
        project_id_in_plane="plane-project-id",
    )

    assert mapped.plane_workspace_slug == "codex-local"
    assert mapped.plane_project_id == "plane-project-id"
    assert registry.get_project(project.id) == mapped
    assert (
        registry.get_project_by_plane_id(
            workspace_slug="codex-local",
            plane_project_id="plane-project-id",
        )
        == mapped
    )


def test_project_registry_generates_unique_slugs_for_same_named_projects(tmp_path: Path) -> None:
    first = tmp_path / "first" / "app"
    second = tmp_path / "second" / "app"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")

    first_project = registry.add_project(first)
    second_project = registry.add_project(second)

    assert first_project.slug == "app"
    assert second_project.slug == "app-2"
    assert [project.slug for project in registry.list_projects()] == ["app", "app-2"]


def test_project_registry_migrates_fake_runner_mode_to_codex(tmp_path: Path) -> None:
    project_dir = tmp_path / "app"
    project_dir.mkdir()
    db_path = tmp_path / "projects.sqlite3"
    registry = ProjectRegistry(db_path)
    project = registry.add_project(project_dir, runner_mode="fake")
    with sqlite3.connect(db_path) as db:
        db.execute("update projects set runner_mode = 'fake' where id = ?", (project.id,))

    migrated = ProjectRegistry(db_path).get_project(project.id)

    assert migrated is not None
    assert migrated.runner_mode == "codex"


def test_project_registry_persists_codex_settings(tmp_path: Path) -> None:
    project_dir = tmp_path / "app"
    project_dir.mkdir()
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")
    project = registry.add_project(
        project_dir,
        codex_settings={
            "default_model": "gpt-5.4-mini",
            "reasoning_effort": "high",
            "automation_mode": "full_agent",
            "max_parallel_agents": 5,
        },
    )

    assert project.codex_settings["default_model"] == "gpt-5.4-mini"
    assert project.codex_settings["reasoning_effort"] == "high"
    assert project.codex_settings["automation_mode"] == "full_agent"
    assert project.codex_settings["agent_task_mode"] == "agent_task_planner"
    assert project.codex_settings["max_parallel_agents"] == 5
    assert project.codex_settings["max_child_tasks_per_run"] == 8
    assert project.codex_settings["skill_policy"] == "minimal"
    assert project.codex_settings["subagents"]["implementer"]["model"] == "gpt-5.5"

    updated = registry.update_project_settings(project.id, {"default_model": "gpt-5.5", "max_task_depth": 2})

    assert updated.codex_settings["default_model"] == "gpt-5.5"
    assert updated.codex_settings["max_task_depth"] == 2


def test_project_registry_rejects_missing_folder(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "projects.sqlite3")

    with pytest.raises(ProjectRegistryError, match="does not exist"):
        registry.add_project(tmp_path / "missing")


def test_slugify_has_stable_fallback() -> None:
    assert slugify("Codex Fleet!") == "codex-fleet"
    assert slugify("!!!") == "project"
