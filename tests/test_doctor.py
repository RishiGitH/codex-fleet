import subprocess
from pathlib import Path

from codex_fleet.doctor import scan_repo


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def test_doctor_scores_empty_non_git_repo(tmp_path: Path) -> None:
    report = scan_repo(tmp_path)

    assert report.score < 80
    assert any(f.code == "missing_git" for f in report.findings)
    assert any(f.code == "missing_agents_md" for f in report.findings)


def test_doctor_scores_basic_ready_repo(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# Agents\n")
    (tmp_path / "WORKFLOW.md").write_text("# Workflow\n")
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text("model = 'gpt-5.4-mini'\n")
    (tmp_path / ".env.example").write_text("OPENAI_API_KEY=\n")
    (tmp_path / "README.md").write_text("# Example\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "apps" / "plane").mkdir(parents=True)

    report = scan_repo(tmp_path)

    assert report.score >= 80
    assert not any(f.severity == "error" for f in report.findings)


def test_doctor_reports_missing_real_codex_binary(monkeypatch, tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    monkeypatch.setattr("codex_fleet.doctor.shutil.which", lambda _binary: None)

    report = scan_repo(tmp_path, codex_command="codex exec")

    assert any(f.code == "missing_codex_cli" for f in report.findings)
    assert any("--fake" in f.recommendation for f in report.findings if f.code == "missing_codex_cli")


def test_doctor_accepts_configured_codex_binary(monkeypatch, tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    monkeypatch.setattr("codex_fleet.doctor.shutil.which", lambda binary: f"/usr/bin/{binary}")

    report = scan_repo(tmp_path, codex_command="python fake_codex.py")

    assert not any(f.code == "missing_codex_cli" for f in report.findings)


def test_doctor_reports_codex_auth_preflight_failure(monkeypatch, tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    monkeypatch.setattr("codex_fleet.doctor.shutil.which", lambda binary: f"/usr/bin/{binary}")

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, stdout="true\n", stderr="")
        if command == ["codex", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="codex-cli 0.128.0\n", stderr="")
        if command == ["codex", "exec", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="--cd <DIR>\n--sandbox <SANDBOX_MODE>\n-c, --config <key=value>\nstdin prompt\n",
                stderr="",
            )
        if command == ["codex", "login", "status"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Not logged in\n")
        raise AssertionError(command)

    monkeypatch.setattr("codex_fleet.doctor.subprocess.run", fake_run)

    report = scan_repo(tmp_path, codex_command="codex exec")

    assert any(f.code == "codex_cli_not_authenticated" for f in report.findings)
    assert not any(f.code == "codex_exec_contract_changed" for f in report.findings)


def test_doctor_reports_codex_exec_contract_change(monkeypatch, tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    monkeypatch.setattr("codex_fleet.doctor.shutil.which", lambda binary: f"/usr/bin/{binary}")

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, stdout="true\n", stderr="")
        if command == ["codex", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="codex-cli future\n", stderr="")
        if command == ["codex", "exec", "--help"]:
            return subprocess.CompletedProcess(command, 0, stdout="different help\n", stderr="")
        if command == ["codex", "login", "status"]:
            return subprocess.CompletedProcess(command, 0, stdout="Logged in\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("codex_fleet.doctor.subprocess.run", fake_run)

    report = scan_repo(tmp_path, codex_command="codex exec")

    finding = next(f for f in report.findings if f.code == "codex_exec_contract_changed")
    assert "--cd" in finding.recommendation
    assert "stdin prompt" in finding.recommendation


def test_doctor_reports_missing_apps_plane(tmp_path: Path) -> None:
    init_git_repo(tmp_path)

    report = scan_repo(tmp_path)

    assert any(f.code == "missing_apps_plane" and f.severity == "error" for f in report.findings)


def test_doctor_reports_stale_plane_src(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "apps" / "plane").mkdir(parents=True)
    (tmp_path / ".codex-fleet" / "plane-src").mkdir(parents=True)

    report = scan_repo(tmp_path)

    assert any(f.code == "stale_plane_src" and f.severity == "error" for f in report.findings)


def test_doctor_reports_stale_plane_patch_resources(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "apps" / "plane").mkdir(parents=True)
    (tmp_path / "patches").mkdir()
    (tmp_path / "patches" / "plane-codex-fleet.patch").write_text("stale\n")

    report = scan_repo(tmp_path)

    assert any(f.code == "stale_plane_patch_resource" and f.severity == "error" for f in report.findings)


def test_doctor_checks_registered_project_harness(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "apps" / "plane").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    init_git_repo(project)
    from codex_fleet.project_registry import ProjectRegistry, default_project_registry_path

    ProjectRegistry(default_project_registry_path(tmp_path)).add_project(project, name="Project")

    report = scan_repo(tmp_path)

    assert any(f.code == "registered_project_harness_missing" for f in report.findings)
    assert any(f.code == "registered_project_plane_unlinked" for f in report.findings)
