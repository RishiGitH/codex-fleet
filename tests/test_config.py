from pathlib import Path

from codex_fleet.config import load_config, resolve_env_ref, write_plane_tracker_config


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
    assert config.workspace.root == (tmp_path / ".codex-fleet" / "workspaces").resolve()
    assert config.codex.stream_logs is True


def test_load_config_resolves_workspace_relative_to_repo(tmp_path: Path) -> None:
    config_path = tmp_path / ".codex-fleet.yml"
    config_path.write_text("repo: .\nworkspace:\n  root: .codex-fleet/workspaces\n")

    config = load_config(tmp_path, config_path=config_path)

    assert config.workspace.root == (tmp_path / ".codex-fleet" / "workspaces").resolve()


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


def test_load_config_infers_legacy_app_server_runner(tmp_path: Path) -> None:
    config_path = tmp_path / ".codex-fleet.yml"
    config_path.write_text("repo: .\ncodex:\n  command: codex app-server\n")

    config = load_config(tmp_path, config_path=config_path)

    assert config.codex.runner == "app-server"


def test_write_plane_tracker_config_applies_codex_settings(tmp_path: Path) -> None:
    write_plane_tracker_config(
        tmp_path,
        base_url="http://127.0.0.1:17880",
        workspace_slug="codex-fleet",
        project_id="project-1",
        codex_settings={
            "default_model": "gpt-5.4-mini",
            "reasoning_effort": "high",
            "approval_policy": "never",
            "sandbox_mode": "workspace-write",
            "max_parallel_agents": 5,
            "job_timeout_seconds": 900,
        },
    )

    config = load_config(tmp_path)

    assert config.tracker.kind == "plane"
    assert config.agent.max_concurrent_agents == 5
    assert config.codex.runner == "app-server"
    assert config.codex.command == "codex app-server"
    assert config.codex.model == "gpt-5.4-mini"
    assert config.codex.reasoning_effort == "high"
    assert config.codex.approval_policy == "never"
    assert config.codex.turn_timeout_ms == 900_000
