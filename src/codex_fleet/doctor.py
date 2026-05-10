from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codex_fleet.project_registry import ProjectRegistry, default_project_registry_path


@dataclass(frozen=True)
class DoctorFinding:
    code: str
    severity: str
    message: str
    recommendation: str


@dataclass(frozen=True)
class DoctorReport:
    repo: Path
    score: int
    findings: tuple[DoctorFinding, ...]

    @property
    def passed(self) -> bool:
        return self.score >= 80 and not any(f.severity == "error" for f in self.findings)


def scan_repo(repo: Path, *, codex_command: str = "codex exec") -> DoctorReport:
    repo = repo.expanduser().resolve()
    findings: list[DoctorFinding] = []

    def missing(path: str, code: str, message: str, recommendation: str, severity: str = "warning") -> None:
        if not (repo / path).exists():
            findings.append(DoctorFinding(code, severity, message, recommendation))

    if not _is_git_repo(repo):
        findings.append(
            DoctorFinding(
                "missing_git",
                "error",
                "Repo is not a git repository.",
                "Initialize git or run codex-fleet against a cloned repository.",
            )
        )

    missing("AGENTS.md", "missing_agents_md", "Missing AGENTS.md.", "Add concise repo guidance for Codex.")
    missing("WORKFLOW.md", "missing_workflow_md", "Missing WORKFLOW.md.", "Add the per-task workflow contract.")
    missing(".codex/config.toml", "missing_codex_config", "Missing .codex/config.toml.", "Add project-level Codex defaults and subagent registrations.")
    missing(".env.example", "missing_env_example", "Missing .env.example.", "Document required environment variables without secrets.")
    missing("README.md", "missing_readme", "Missing README.md.", "Add setup and usage docs.")

    if not (repo / "apps" / "plane").exists():
        findings.append(
            DoctorFinding(
                "missing_apps_plane",
                "error",
                "Tracked Plane product source is missing at apps/plane.",
                "Restore apps/plane from the repository. Codex Fleet no longer recreates Plane source under .codex-fleet.",
            )
        )
    if (repo / ".codex-fleet" / "plane-src").exists():
        findings.append(
            DoctorFinding(
                "stale_plane_src",
                "error",
                "Stale Plane runtime source exists at .codex-fleet/plane-src.",
                "Delete .codex-fleet/plane-src. Plane product source now lives at apps/plane.",
            )
        )
    for stale_path in (
        "patches/plane-codex-fleet.patch",
        "src/codex_fleet/resources/plane-codex-fleet.patch",
        "scripts/plane-fork-clone",
    ):
        if (repo / stale_path).exists():
            findings.append(
                DoctorFinding(
                    "stale_plane_patch_resource",
                    "error",
                    f"Removed Plane patch-system resource still exists: {stale_path}.",
                    "Delete Plane patch resources. Codex Fleet now uses tracked apps/plane source only.",
                )
            )

    findings.extend(_registered_project_findings(repo))

    has_tests = any((repo / path).exists() for path in ["tests", "test", "spec", "__tests__"])
    if not has_tests:
        findings.append(
            DoctorFinding(
                "missing_tests_dir",
                "warning",
                "No obvious tests directory found.",
                "Add a small deterministic smoke/unit test suite before running many agents.",
            )
        )

    has_package_marker = any(
        (repo / path).exists()
        for path in ["pyproject.toml", "package.json", "Cargo.toml", "go.mod", "mix.exs", "pom.xml"]
    )
    if not has_package_marker:
        findings.append(
            DoctorFinding(
                "unknown_stack",
                "warning",
                "Could not detect a known package/project manifest.",
                "Document install, test, and run commands in AGENTS.md.",
            )
        )

    has_ci = (repo / ".github" / "workflows").exists()
    if not has_ci:
        findings.append(
            DoctorFinding(
                "missing_ci",
                "info",
                "No GitHub Actions workflow found.",
                "This is expected while the project uses local-only testing; add CI later only when desired.",
            )
        )

    codex_binary = _command_binary(codex_command)
    if codex_binary and shutil.which(codex_binary) is None:
        findings.append(
            DoctorFinding(
                "missing_codex_cli",
                "info",
                f"Configured Codex command binary was not found: {codex_binary}",
                "Install and authenticate Codex CLI before running without --fake. The fake demo does not require it.",
            )
        )
    elif codex_binary:
        findings.extend(_codex_cli_preflight_findings(codex_command))

    penalty = sum({"error": 35, "warning": 12, "info": 5}[f.severity] for f in findings)
    score = max(0, 100 - penalty)
    return DoctorReport(repo=repo, score=score, findings=tuple(findings))


def render_report(report: DoctorReport) -> str:
    lines = [f"Repo readiness: {report.score}/100", ""]
    if not report.findings:
        lines.append("No findings. Repo looks ready for basic Codex work.")
        return "\n".join(lines)

    for finding in report.findings:
        lines.append(f"[{finding.severity.upper()}] {finding.code}: {finding.message}")
        lines.append(f"  Recommendation: {finding.recommendation}")
    return "\n".join(lines)


def _is_git_repo(repo: Path) -> bool:
    if not repo.exists():
        return False
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _registered_project_findings(repo: Path) -> list[DoctorFinding]:
    registry_path = default_project_registry_path(repo)
    if not registry_path.exists():
        return []
    findings: list[DoctorFinding] = []
    try:
        projects = ProjectRegistry(registry_path).list_projects()
    except Exception as exc:  # noqa: BLE001 - doctor should report broken runtime state.
        return [
            DoctorFinding(
                "project_registry_unreadable",
                "warning",
                f"Local project registry could not be read: {exc}",
                "Delete or reset .codex-fleet/projects.sqlite3 if this local runtime state is stale.",
            )
        ]
    for project in projects:
        prefix = f"Registered project {project.name!r}"
        if not project.repo_path.exists():
            findings.append(
                DoctorFinding(
                    "registered_project_missing",
                    "error",
                    f"{prefix} folder is missing: {project.repo_path}",
                    "Delete/reset stale .codex-fleet runtime state or recreate the local project.",
                )
            )
            continue
        if not _is_git_repo(project.repo_path):
            findings.append(
                DoctorFinding(
                    "registered_project_not_git",
                    "error",
                    f"{prefix} is not a git repository.",
                    "Initialize git in the project folder or recreate it through the Plane project flow.",
                )
            )
        for relative in ("AGENTS.md", ".codex/config.toml", ".codex-fleet/project.json"):
            if not (project.repo_path / relative).exists():
                findings.append(
                    DoctorFinding(
                        "registered_project_harness_missing",
                        "warning",
                        f"{prefix} is missing harness file {relative}.",
                        "Run the project harness apply action from Plane or `python -m codex_fleet apply-harness --repo <project>`.",
                    )
                )
        if not project.plane_project_id:
            findings.append(
                DoctorFinding(
                    "registered_project_plane_unlinked",
                    "warning",
                    f"{prefix} is not linked to a Plane project.",
                    "Create/link the project from the Plane Add Project flow.",
                )
            )
    return findings


def _command_binary(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    return parts[0] if parts else None


def _codex_cli_preflight_findings(command: str) -> list[DoctorFinding]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return [
            DoctorFinding(
                "invalid_codex_command",
                "warning",
                "Configured Codex command could not be parsed.",
                "Set codex.command to a valid argv-style command such as `codex exec`.",
            )
        ]
    if not parts or Path(parts[0]).name != "codex" or (len(parts) > 1 and parts[1] != "exec"):
        return []

    findings: list[DoctorFinding] = []
    try:
        version = subprocess.run(
            [parts[0], "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        version = None
    if version is not None and version.returncode != 0:
        findings.append(
            DoctorFinding(
                "codex_cli_version_failed",
                "info",
                "Codex CLI version check failed.",
                "Run `codex --version` locally before using the real runner.",
            )
        )

    try:
        exec_help = subprocess.run(
            [*parts, "--help"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        exec_help = None
    if exec_help is None or exec_help.returncode != 0:
        findings.append(
            DoctorFinding(
                "codex_exec_help_failed",
                "warning",
                "Codex exec help could not be inspected.",
                "Run `codex exec --help` and confirm it supports `--cd`, `--sandbox`, config overrides, and stdin prompts.",
            )
        )
    else:
        help_text = f"{exec_help.stdout}\n{exec_help.stderr}"
        missing = [
            flag
            for flag in ("--cd", "--sandbox", "--config")
            if flag not in help_text
        ]
        if "stdin" not in help_text.lower():
            missing.append("stdin prompt")
        if missing:
            findings.append(
                DoctorFinding(
                    "codex_exec_contract_changed",
                    "warning",
                    "Codex exec CLI contract appears different from codex-fleet's runner expectations.",
                    "Update Codex CLI or codex-fleet runner support. Missing support: " + ", ".join(missing),
                )
            )

    try:
        login_status = subprocess.run(
            [parts[0], "login", "status"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        login_status = None
    if login_status is None or login_status.returncode != 0:
        findings.append(
            DoctorFinding(
                "codex_cli_not_authenticated",
                "info",
                "Codex CLI authentication was not confirmed.",
                "Run `codex login status` and `codex login` before using the real runner. Keep using `--fake` for demos.",
            )
        )
    return findings
