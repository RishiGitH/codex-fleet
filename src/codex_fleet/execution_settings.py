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
    if settings.get("automation_mode") == "project-default":
        settings.pop("automation_mode", None)
    if "agent_task_mode" in settings and "automation_mode" not in settings:
        settings["automation_mode"] = {
            "manual": "manual",
            "review_and_approve": "assisted",
            "agent_task_planner": "full_agent",
        }.get(str(settings["agent_task_mode"]), "assisted")
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
    settings = normalize_codex_settings(
        {
            **(base_settings or {}),
            **metadata_settings,
            **codex_settings_from_work_item(item),
        }
    )
    role = resolve_agent_role(item, store)
    role_settings = _role_profile_settings(settings, role)
    return normalize_codex_settings({**settings, **role_settings, "agent_role": role})


def resolve_agent_role(item: WorkItem, store: RunStore | None = None) -> str:
    if store is not None:
        metadata = store.get_task_metadata(item.id)
        if metadata is not None and metadata.role:
            return _normalize_role(metadata.role)
    labels = {label.lower() for label in item.labels}
    if "agent-lead" in labels:
        return "lead"
    if "agent-planner" in labels:
        return "planner"
    if "agent-code_scout" in labels or "agent-code-scout" in labels or "agent-scout" in labels:
        return "code_scout"
    if "agent-security_reviewer" in labels or "agent-security-reviewer" in labels:
        return "security_reviewer"
    if "agent-token_reviewer" in labels or "agent-token-reviewer" in labels:
        return "token_reviewer"
    if "agent-harness_reviewer" in labels or "agent-harness-reviewer" in labels:
        return "harness_reviewer"
    if "agent-reviewer" in labels:
        return "reviewer"
    if "agent-worker" in labels:
        return "worker"
    return "worker"


def settings_value(settings: dict[str, Any] | None, key: str) -> object:
    return normalize_codex_settings(settings).get(key, DEFAULT_CODEX_SETTINGS[key])


def _normalize_role(role: str) -> str:
    return role.strip().lower().replace("-", "_") or "worker"


def _role_profile_settings(settings: dict[str, Any], role: str) -> dict[str, Any]:
    subagents = settings.get("subagents")
    if not isinstance(subagents, dict):
        return {}
    aliases = {
        "lead": "implementer",
        "planner": "code_scout",
        "worker": "implementer",
        "reviewer": "harness_reviewer",
    }
    profile_name = role if role in subagents else aliases.get(role, role)
    profile = subagents.get(profile_name)
    if not isinstance(profile, dict):
        return {}
    mapped: dict[str, Any] = {}
    if isinstance(profile.get("model"), str):
        mapped["default_model"] = profile["model"]
    if isinstance(profile.get("reasoning_effort"), str):
        mapped["reasoning_effort"] = profile["reasoning_effort"]
    if isinstance(profile.get("sandbox_mode"), str):
        mapped["sandbox_mode"] = profile["sandbox_mode"]
    return mapped


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
