from __future__ import annotations

import shlex
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

TOKEN_TOOL_DEFAULT_COMMANDS: dict[str, str] = {
    "rtk": "rtk",
    "caveman": "caveman",
    "repomix": "repomix",
    "graphify": "graphify",
}


@dataclass(frozen=True)
class ToolCapability:
    name: str
    command: str
    available: bool
    purpose: str
    recommendation: str


def detect_token_tools(commands: Mapping[str, str] | None = None) -> tuple[ToolCapability, ...]:
    configured = {**TOKEN_TOOL_DEFAULT_COMMANDS, **(commands or {})}
    return tuple(
        ToolCapability(
            name=name,
            command=command,
            available=_command_available(command),
            purpose=_purpose(name),
            recommendation=_recommendation(name),
        )
        for name, command in configured.items()
    )


def capabilities_payload(commands: Mapping[str, str] | None = None) -> dict[str, dict[str, object]]:
    return {
        capability.name: {
            "command": capability.command,
            "available": capability.available,
            "purpose": capability.purpose,
            "recommendation": capability.recommendation,
        }
        for capability in detect_token_tools(commands)
    }


def native_compress_output(raw: str, *, max_lines: int = 120) -> str:
    lines = raw.splitlines()
    if not lines:
        return "(no output)\n"

    important_markers = (
        "error",
        "failed",
        "failure",
        "traceback",
        "assert",
        "exception",
        "warning",
        "fatal",
        "exit code",
    )
    important = [
        line for line in lines if any(marker in line.lower() for marker in important_markers)
    ]
    selected = _dedupe_preserve_order(lines[:20] + important[:80] + lines[-20:])
    if len(selected) > max_lines:
        edge_count = max(2, max_lines // 4)
        important_count = max(0, max_lines - (edge_count * 2) - 1)
        selected = _dedupe_preserve_order(
            lines[:edge_count] + important[:important_count] + ["..."] + lines[-edge_count:]
        )
    return "\n".join(selected).rstrip() + "\n"


def external_compress_output(raw: str, command: str, *, timeout_seconds: int = 30) -> str | None:
    parts = _split(command)
    if not parts or not _command_available(command):
        return None
    try:
        completed = subprocess.run(
            parts,
            input=raw,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = completed.stdout.strip()
    if completed.returncode != 0 or not output:
        return None
    return output + "\n"


def tool_commands_from_config(config: object) -> dict[str, str]:
    return {
        "rtk": str(getattr(config, "rtk_command", TOKEN_TOOL_DEFAULT_COMMANDS["rtk"])),
        "caveman": str(getattr(config, "caveman_command", TOKEN_TOOL_DEFAULT_COMMANDS["caveman"])),
        "repomix": str(getattr(config, "repomix_command", TOKEN_TOOL_DEFAULT_COMMANDS["repomix"])),
        "graphify": str(getattr(config, "graphify_command", TOKEN_TOOL_DEFAULT_COMMANDS["graphify"])),
    }


def _command_available(command: str) -> bool:
    parts = _split(command)
    return bool(parts and shutil.which(parts[0]))


def _split(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _purpose(name: str) -> str:
    purposes = {
        "rtk": "Compress long command output while preserving raw artifacts.",
        "caveman": "Compress prose docs and handoffs, not exact code or traces.",
        "repomix": "Build repository context packs when a broad map is explicitly needed.",
        "graphify": "Generate architecture graphs for large or unfamiliar codebases.",
    }
    return purposes.get(name, "Optional token or context helper.")


def _recommendation(name: str) -> str:
    recommendations = {
        "rtk": "optional; use for large logs after raw output is saved",
        "caveman": "optional; use for long prose only",
        "repomix": "optional; prefer targeted codex-fleet context packs first",
        "graphify": "optional; use for architecture archaeology, not routine edits",
    }
    return recommendations.get(name, "optional")


def _dedupe_preserve_order(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if line not in seen:
            result.append(line)
            seen.add(line)
    return result
