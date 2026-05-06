from pathlib import Path

from codex_fleet.config import load_config
from codex_fleet.plane_manager import PlaneInstall
from codex_fleet.plane_views import (
    PlaneViewBootstrapError,
    ensure_local_plane_project_views,
)


def _write_plane_config(repo: Path, *, url: str = "http://127.0.0.1:8080") -> None:
    repo.joinpath(".codex-fleet.yml").write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  plane_base_url: " + url + "\n"
        "  plane_api_key: test-key\n"
        "  plane_workspace_slug: codex-fleet\n"
        "  plane_project_id: project-id\n"
    )


def test_ensure_local_plane_project_views_skips_non_loopback(tmp_path: Path) -> None:
    _write_plane_config(tmp_path, url="https://plane.example.test")

    result = ensure_local_plane_project_views(load_config(tmp_path))

    assert result.skipped is True
    assert result.skipped_reason == "Plane URL is not loopback"


def test_ensure_local_plane_project_views_runs_plane_container_without_token_in_command(
    tmp_path: Path, monkeypatch
) -> None:
    _write_plane_config(tmp_path)
    app_dir = tmp_path / ".codex-fleet" / "plane-selfhost" / "plane-app"
    app_dir.mkdir(parents=True)
    compose = app_dir / "docker-compose.yaml"
    compose.write_text("services: {}\n")

    monkeypatch.setattr(
        "codex_fleet.plane_views.inspect_plane_runtime",
        lambda repo: PlaneInstall(
            runtime_dir=repo / ".codex-fleet" / "plane-selfhost",
            setup_script=repo / ".codex-fleet" / "plane-selfhost" / "setup.sh",
            installed=True,
            app_dir=app_dir,
        ),
    )

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        assert "test-key" not in command
        assert kwargs["cwd"] == app_dir
        assert kwargs["env"]["CODEX_FLEET_PLANE_API_KEY"] == "test-key"
        assert command[0] == "docker"
        assert str(compose) in command

        class Result:
            returncode = 0
            stdout = "created:codex-fleet Ready\nexisting:codex-fleet Running\ncreated:codex-fleet Agent proposals\n"
            stderr = ""

        return Result()

    monkeypatch.setattr("codex_fleet.plane_views.subprocess.run", fake_run)

    result = ensure_local_plane_project_views(load_config(tmp_path))

    assert result.created == ("codex-fleet Ready", "codex-fleet Agent proposals")
    assert result.existing == ("codex-fleet Running",)


def test_plane_view_bootstrap_script_includes_agent_proposals_filter() -> None:
    from codex_fleet.plane_views import _DJANGO_VIEW_BOOTSTRAP_SCRIPT

    assert "codex-fleet Agent proposals" in _DJANGO_VIEW_BOOTSTRAP_SCRIPT
    assert "agent-proposed" in _DJANGO_VIEW_BOOTSTRAP_SCRIPT
    assert "filters[\"labels\"]" in _DJANGO_VIEW_BOOTSTRAP_SCRIPT


def test_ensure_local_plane_project_views_raises_on_container_failure(tmp_path: Path, monkeypatch) -> None:
    _write_plane_config(tmp_path)
    app_dir = tmp_path / ".codex-fleet" / "plane-selfhost" / "plane-app"
    app_dir.mkdir(parents=True)
    (app_dir / "docker-compose.yaml").write_text("services: {}\n")
    monkeypatch.setattr(
        "codex_fleet.plane_views.inspect_plane_runtime",
        lambda repo: PlaneInstall(
            runtime_dir=repo / ".codex-fleet" / "plane-selfhost",
            setup_script=repo / ".codex-fleet" / "plane-selfhost" / "setup.sh",
            installed=True,
            app_dir=app_dir,
        ),
    )

    class Result:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr("codex_fleet.plane_views.subprocess.run", lambda *_args, **_kwargs: Result())

    try:
        ensure_local_plane_project_views(load_config(tmp_path))
    except PlaneViewBootstrapError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected PlaneViewBootstrapError")
