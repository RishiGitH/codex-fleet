from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class TrackerConfig(BaseModel):
    kind: Literal["memory", "plane"] = "memory"
    active_states: list[str] = Field(default_factory=lambda: ["Ready", "Running", "Rework"])
    handoff_states: list[str] = Field(default_factory=lambda: ["Human Review"])
    terminal_states: list[str] = Field(default_factory=lambda: ["Done", "Cancelled"])
    plane_base_url: str | None = None
    plane_api_key: str | None = None
    plane_workspace_slug: str | None = None
    plane_project_id: str | None = None


class AgentConfig(BaseModel):
    max_concurrent_agents: int = 2
    max_turns: int = 8
    max_retry_backoff_ms: int = 300_000


class WorkspaceConfig(BaseModel):
    root: Path = Field(default_factory=lambda: Path.home() / ".codex-fleet" / "workspaces")


class CodexConfig(BaseModel):
    command: str = "codex app-server"
    approval_policy: str = "on-request"
    sandbox_mode: str = "workspace-write"
    turn_timeout_ms: int = 3_600_000
    stall_timeout_ms: int = 300_000


class FleetConfig(BaseModel):
    repo: Path = Field(default_factory=Path.cwd)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    codex: CodexConfig = Field(default_factory=CodexConfig)

    def resolved(self) -> "FleetConfig":
        self.repo = self.repo.expanduser().resolve()
        self.workspace.root = self.workspace.root.expanduser().resolve()
        self.tracker.plane_base_url = resolve_env_ref(self.tracker.plane_base_url)
        self.tracker.plane_api_key = resolve_env_ref(self.tracker.plane_api_key)
        self.tracker.plane_workspace_slug = resolve_env_ref(self.tracker.plane_workspace_slug)
        self.tracker.plane_project_id = resolve_env_ref(self.tracker.plane_project_id)
        return self


def default_config_path(repo: Path) -> Path:
    return repo / ".codex-fleet.yml"


def load_config(repo: Path, config_path: Path | None = None) -> FleetConfig:
    repo = repo.expanduser().resolve()
    path = config_path or default_config_path(repo)
    if not path.exists():
        return FleetConfig(repo=repo).resolved()

    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(".codex-fleet.yml must contain a mapping")
    raw.setdefault("repo", str(repo))
    raw = _resolve_repo_relative_paths(raw, path.parent)
    return FleetConfig.model_validate(raw).resolved()


def write_default_config(repo: Path, path: Path | None = None) -> Path:
    repo = repo.expanduser().resolve()
    target = path or default_config_path(repo)
    if target.exists():
        return target
    target.write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: memory\n"
        "  active_states: [Ready, Running, Rework]\n"
        "  handoff_states: [Human Review]\n"
        "  terminal_states: [Done, Cancelled]\n"
        "agent:\n"
        "  max_concurrent_agents: 2\n"
        "workspace:\n"
        "  root: ~/.codex-fleet/workspaces\n"
        "codex:\n"
        "  command: codex app-server\n"
        "  approval_policy: on-request\n"
        "  sandbox_mode: workspace-write\n"
    )
    return target


def resolve_env_ref(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("$") and len(value) > 1:
        return os.getenv(value[1:])
    return value


def _resolve_repo_relative_paths(raw: dict[str, Any], config_dir: Path) -> dict[str, Any]:
    repo_value = raw.get("repo")
    if isinstance(repo_value, str) and repo_value not in {"", "."}:
        repo_path = Path(repo_value).expanduser()
        if not repo_path.is_absolute():
            raw["repo"] = str((config_dir / repo_path).resolve())
    elif repo_value == ".":
        raw["repo"] = str(config_dir.resolve())
    return raw
