from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codex_fleet.budget import scan_budget
from codex_fleet.token_tools import detect_token_tools


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

    if (repo / "pyproject.toml").exists():
        _python_findings(repo, findings)

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

    budget = scan_budget(repo)
    if not budget.ok:
        findings.append(
            DoctorFinding(
                "context_budget_exceeded",
                "warning",
                f"{budget.too_large_count} guidance/context file(s) exceed configured token budgets.",
                "Run `python -m codex_fleet budget --repo . --strict` and trim always-loaded guidance or large skills.",
            )
        )

    if (repo / ".agents" / "skills" / "impeccable" / "scripts").exists():
        findings.append(
            DoctorFinding(
                "large_frontend_skill_present",
                "info",
                "Large frontend skill assets are present.",
                "Keep Impeccable opt-in for UI work and exclude its scripts from routine context packs.",
            )
        )

    missing_optional_tools = [tool.name for tool in detect_token_tools() if not tool.available]
    if missing_optional_tools:
        findings.append(
            DoctorFinding(
                "optional_token_tools_missing",
                "info",
                "Some optional token/context helper tools are not installed: " + ", ".join(missing_optional_tools),
                "No action required. codex-fleet falls back to native capture, budget, and context-pack behavior.",
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


def _python_findings(repo: Path, findings: list[DoctorFinding]) -> None:
    if shutil.which("python") is None and shutil.which("python3") is not None:
        findings.append(
            DoctorFinding(
                "python_command_mismatch",
                "info",
                "`python` was not found, but `python3` is available.",
                "Use `make install` or `python3 -m venv .venv`; generated docs should prefer the repo Makefile or detected interpreter.",
            )
        )
    venv_python = repo / ".venv" / "bin" / "python"
    if not venv_python.exists():
        findings.append(
            DoctorFinding(
                "venv_not_created",
                "info",
                "Local `.venv` is not present.",
                "Run `make install` before local checks; npx launcher creates its own tool venv for package-style use.",
            )
        )


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
