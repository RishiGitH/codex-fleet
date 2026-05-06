from __future__ import annotations

import copy
import html
import json
import re
from typing import Any

from codex_fleet.config import FleetConfig
from codex_fleet.models import WorkItem
from codex_fleet.project_registry import DEFAULT_CODEX_SETTINGS, normalize_codex_settings
from codex_fleet.store import RunStore


def codex_settings_from_work_item(item: WorkItem) -> dict[str, Any]:
    description = item.description or ""
    if "codex-fleet task settings" not in description:
        return {}

    match = re.search(r"\{\s*&quot;default_model&quot;.*?\}", description, flags=re.DOTALL)
    raw = match.group(0) if match else ""
    if not raw:
        match = re.search(r"\{\s*\"default_model\".*?\}", description, flags=re.DOTALL)
        raw = match.group(0) if match else ""
    if not raw:
        return {}
    try:
        parsed = json.loads(html.unescape(raw))
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    settings = {key: value for key, value in parsed.items() if key != "skills"}
    if settings.get("agent_task_mode") == "project-default":
        settings.pop("agent_task_mode", None)
    return settings


def merged_work_item_settings(
    base_settings: dict[str, Any] | None,
    item: WorkItem,
    store: RunStore | None = None,
) -> dict[str, Any]:
    metadata_settings: dict[str, Any] = {}
    if store is not None:
        metadata = store.get_task_metadata(item.id)
        if metadata is not None:
            metadata_settings = metadata.settings
    return normalize_codex_settings(
        {
            **(base_settings or {}),
            **metadata_settings,
            **codex_settings_from_work_item(item),
        }
    )


def settings_value(settings: dict[str, Any] | None, key: str) -> object:
    return normalize_codex_settings(settings).get(key, DEFAULT_CODEX_SETTINGS[key])


def config_with_codex_settings(config: FleetConfig, settings: dict[str, Any] | None) -> FleetConfig:
    if not settings:
        return copy.deepcopy(config)
    normalized = normalize_codex_settings(settings)
    updated = copy.deepcopy(config)
    runner_mode = normalized.get("runner_mode")
    if runner_mode == "codex":
        updated.codex.runner = "cli"
        if "app-server" in updated.codex.command:
            updated.codex.command = "codex exec"
    elif runner_mode == "app-server":
        updated.codex.runner = "app-server"
        if updated.codex.command == "codex exec":
            updated.codex.command = "codex app-server"
    approval = normalized.get("approval_policy")
    if isinstance(approval, str) and approval.strip():
        updated.codex.approval_policy = approval.strip()
    sandbox = normalized.get("sandbox_mode")
    if isinstance(sandbox, str) and sandbox.strip():
        updated.codex.sandbox_mode = sandbox.strip()
    timeout_seconds = normalized.get("job_timeout_seconds")
    if isinstance(timeout_seconds, int) and timeout_seconds > 0:
        updated.codex.turn_timeout_ms = timeout_seconds * 1000
    max_agents = normalized.get("max_parallel_agents")
    if isinstance(max_agents, int) and max_agents > 0:
        updated.agent.max_concurrent_agents = max_agents
    model = normalized.get("default_model")
    if updated.codex.runner == "cli" and isinstance(model, str) and model.strip():
        updated.codex.command = _codex_command_with_model(updated.codex.command, model.strip())
    return updated


def _codex_command_with_model(command: str, model: str) -> str:
    parts = command.split()
    if any(part in {"--model", "-m"} or part.startswith("--model=") for part in parts):
        return command
    if parts[:2] == ["codex", "exec"]:
        return f"{command} --model {model}"
    return command
