from pathlib import Path

from codex_fleet.config import load_config, resolve_env_ref


def test_resolve_env_ref(monkeypatch) -> None:
    monkeypatch.setenv("PLANE_API_KEY", "secret")

    assert resolve_env_ref("$PLANE_API_KEY") == "secret"
    assert resolve_env_ref("literal") == "literal"
    assert resolve_env_ref(None) is None


def test_load_config_resolves_repo_relative_to_config(tmp_path: Path) -> None:
    config_path = tmp_path / ".codex-fleet.yml"
    config_path.write_text("repo: .\ntracker:\n  kind: memory\n")

    config = load_config(tmp_path, config_path=config_path)

    assert config.repo == tmp_path.resolve()


def test_load_config_resolves_plane_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PLANE_BASE_URL", "http://plane.local")
    config_path = tmp_path / ".codex-fleet.yml"
    config_path.write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: plane\n"
        "  plane_base_url: $PLANE_BASE_URL\n"
        "  plane_api_key: literal-key\n"
        "  plane_workspace_slug: workspace\n"
        "  plane_project_id: project\n"
    )

    config = load_config(tmp_path, config_path=config_path)

    assert config.tracker.plane_base_url == "http://plane.local"
    assert config.tracker.plane_api_key == "literal-key"
