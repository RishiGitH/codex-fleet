from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from codex_fleet.budget import estimate_tokens


@dataclass(frozen=True)
class CaptureResult:
    artifact_dir: Path
    returncode: int
    raw_path: Path
    summary_path: Path
    metadata_path: Path


def capture_command(repo: Path, command: tuple[str, ...]) -> CaptureResult:
    if not command:
        raise ValueError("capture requires a command after --")

    repo = repo.expanduser().resolve()
    artifact_dir = repo / ".codex-fleet" / "artifacts" / _timestamp()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    completed = subprocess.run(command, cwd=repo, text=True, capture_output=True, check=False)
    duration = time.monotonic() - started
    raw = _combined_output(completed.stdout, completed.stderr)
    summary = summarize_output(command, completed.returncode, duration, raw)

    raw_path = artifact_dir / "raw.txt"
    summary_path = artifact_dir / "summary.txt"
    metadata_path = artifact_dir / "metadata.json"
    raw_path.write_text(raw)
    summary_path.write_text(summary)
    metadata_path.write_text(
        json.dumps(
            {
                "command": list(command),
                "returncode": completed.returncode,
                "duration_seconds": round(duration, 3),
                "raw_path": str(raw_path),
                "summary_path": str(summary_path),
                "raw_bytes": len(raw.encode("utf-8")),
                "raw_estimated_tokens": estimate_tokens(raw),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    return CaptureResult(
        artifact_dir=artifact_dir,
        returncode=completed.returncode,
        raw_path=raw_path,
        summary_path=summary_path,
        metadata_path=metadata_path,
    )


def summarize_output(command: tuple[str, ...], returncode: int, duration: float, raw: str) -> str:
    lines = raw.splitlines()
    interesting = [
        line
        for line in lines
        if any(
            marker in line.lower()
            for marker in ("error", "failed", "failure", "traceback", "assert", "exception")
        )
    ]
    preview = _dedupe_preserve_order(lines[:20] + interesting[:40] + lines[-20:])
    rendered = [
        "# Command output summary",
        "",
        f"Command: {' '.join(command)}",
        f"Exit code: {returncode}",
        f"Duration: {duration:.3f}s",
        f"Raw lines: {len(lines)}",
        f"Raw rough tokens: {estimate_tokens(raw)}",
        "",
        "## Selected output",
        "",
    ]
    rendered.extend(preview or ["(no output)"])
    rendered.append("")
    return "\n".join(rendered)


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _combined_output(stdout: str, stderr: str) -> str:
    if stdout and stderr:
        return f"{stdout}\n[stderr]\n{stderr}"
    return stdout or stderr


def _dedupe_preserve_order(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if line not in seen:
            result.append(line)
            seen.add(line)
    return result
