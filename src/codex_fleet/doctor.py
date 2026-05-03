from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


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


def scan_repo(repo: Path) -> DoctorReport:
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
                "Add CI before enabling auto PR creation for broader contributors.",
            )
        )

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
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"
