from pathlib import Path

from codex_fleet.plane_local_bootstrap import (
    _DJANGO_LOCAL_BOOTSTRAP_SCRIPT,
    _DJANGO_LOCAL_SESSION_SCRIPT,
    bootstrap_local_plane,
)


def test_bootstrap_local_plane_runs_django_shell_without_secret_in_command(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / ".codex-fleet" / "plane-selfhost" / "plane-app"
    runtime.mkdir(parents=True)
    compose_file = runtime / "docker-compose.yaml"
    compose_file.write_text("services: {}\n")
    calls: list[tuple[list[str], dict[str, str]]] = []

    monkeypatch.setattr(
        "codex_fleet.plane_local_bootstrap.inspect_plane_runtime",
        lambda _repo: type("Install", (), {"app_dir": runtime})(),
    )

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((command, kwargs["env"]))

        class Result:
            returncode = 0
            stderr = ""
            stdout = (
                "noise\n"
                '{"workspace_slug":"codex-fleet","workspace_created":true,'
                '"project_id":"project-id","project_created":true,'
                '"project_name":"Codex Fleet","api_key":"plane_api_secret",'
                '"token_created":true,"user_email":"codex-fleet-local@example.local"}\n'
            )

        return Result()

    monkeypatch.setattr("codex_fleet.plane_local_bootstrap.subprocess.run", fake_run)

    result = bootstrap_local_plane(tmp_path)

    command, env = calls[0]
    assert result.project_id == "project-id"
    assert result.api_key == "plane_api_secret"
    assert command[:5] == ["docker", "compose", "-f", str(compose_file), "exec"]
    assert "plane_api_secret" not in " ".join(command)
    assert env["CODEX_FLEET_PLANE_WORKSPACE_SLUG"] == "codex-fleet"
    assert env["CODEX_FLEET_PLANE_PROJECT_IDENTIFIER"] == "CF"


def test_local_plane_bootstrap_marks_user_onboarded_for_no_login_flow() -> None:
    assert "Profile.objects.update_or_create" in _DJANGO_LOCAL_BOOTSTRAP_SCRIPT
    assert '"is_onboarded": True' in _DJANGO_LOCAL_BOOTSTRAP_SCRIPT
    assert '"workspace_create": True' in _DJANGO_LOCAL_BOOTSTRAP_SCRIPT
    assert '"workspace_join": True' in _DJANGO_LOCAL_BOOTSTRAP_SCRIPT
    assert '"last_workspace_id": workspace.id' in _DJANGO_LOCAL_BOOTSTRAP_SCRIPT


def test_local_plane_session_repairs_legacy_unonboarded_profile() -> None:
    assert "Profile.objects.get_or_create" in _DJANGO_LOCAL_SESSION_SCRIPT
    assert "profile.is_onboarded = True" in _DJANGO_LOCAL_SESSION_SCRIPT
    assert "profile.last_workspace_id = workspace.id" in _DJANGO_LOCAL_SESSION_SCRIPT
