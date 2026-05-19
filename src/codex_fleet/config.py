from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class TrackerConfig(BaseModel):
    kind: Literal["memory", "plane"] = "memory"
    active_states: list[str] = Field(default_factory=lambda: ["Ready"])
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
    root: Path = Field(default_factory=lambda: Path(".codex-fleet") / "workspaces")


class CodexConfig(BaseModel):
    runner: Literal["cli", "app-server"] = "app-server"
    command: str = "codex app-server"
    approval_policy: str = "never"
    sandbox_mode: str = "workspace-write"
    model: str | None = None
    reasoning_effort: str | None = None
    turn_timeout_ms: int = 3_600_000
    stall_timeout_ms: int = 300_000
    stream_logs: bool = True

    @model_validator(mode="after")
    def infer_legacy_app_server_runner(self) -> CodexConfig:
        if "runner" not in self.model_fields_set and "app-server" in self.command:
            self.runner = "app-server"
        return self


class TokenConfig(BaseModel):
    default_doc_limit: int = 8_000
    skill_limit: int = 4_000
    raw_artifact_retention: str = "keep"
    enable_rtk: bool = False
    enable_caveman: bool = False
    enable_repomix: bool = False


class FleetConfig(BaseModel):
    repo: Path = Field(default_factory=Path.cwd)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    codex: CodexConfig = Field(default_factory=CodexConfig)
    token: TokenConfig = Field(default_factory=TokenConfig)

    def resolved(self) -> FleetConfig:
        self.repo = self.repo.expanduser().absolute()
        workspace_root = self.workspace.root.expanduser()
        if not workspace_root.is_absolute():
            workspace_root = self.repo / workspace_root
        self.workspace.root = workspace_root.absolute()
        self.tracker.plane_base_url = resolve_env_ref(self.tracker.plane_base_url)
        self.tracker.plane_api_key = resolve_env_ref(self.tracker.plane_api_key)
        self.tracker.plane_workspace_slug = resolve_env_ref(self.tracker.plane_workspace_slug)
        self.tracker.plane_project_id = resolve_env_ref(self.tracker.plane_project_id)
        return self


def default_config_path(repo: Path) -> Path:
    return repo / ".codex-fleet.yml"


def load_config(repo: Path, config_path: Path | None = None) -> FleetConfig:
    repo = repo.expanduser().absolute()
    load_local_secrets(repo)
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
    repo = repo.expanduser().absolute()
    target = path or default_config_path(repo)
    if target.exists():
        return target
    target.write_text(
        "repo: .\n"
        "tracker:\n"
        "  kind: memory\n"
        "  active_states: [Ready]\n"
        "  handoff_states: [Human Review]\n"
        "  terminal_states: [Done, Cancelled]\n"
        "agent:\n"
        "  max_concurrent_agents: 2\n"
        "workspace:\n"
        "  root: .codex-fleet/workspaces\n"
        "codex:\n"
        "  runner: app-server\n"
        "  command: codex app-server\n"
        "  approval_policy: never\n"
        "  sandbox_mode: workspace-write\n"
        "  model: gpt-5.5\n"
        "  reasoning_effort: low\n"
        "  stream_logs: true\n"
        "token:\n"
        "  default_doc_limit: 8000\n"
        "  skill_limit: 4000\n"
        "  raw_artifact_retention: keep\n"
        "  enable_rtk: false\n"
        "  enable_caveman: false\n"
        "  enable_repomix: false\n"
    )
    return target


def write_plane_tracker_config(
    repo: Path,
    *,
    base_url: str,
    workspace_slug: str,
    project_id: str,
    api_key_ref: str = "$PLANE_API_KEY",
    api_key_value: str | None = None,
    codex_settings: dict[str, Any] | None = None,
) -> Path:
    repo = repo.expanduser().absolute()
    target = default_config_path(repo)
    if target.exists():
        raw = yaml.safe_load(target.read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError(".codex-fleet.yml must contain a mapping")
    else:
        raw = {"repo": "."}

    tracker = raw.get("tracker")
    if not isinstance(tracker, dict):
        tracker = {}
    tracker.update(
        {
            "kind": "plane",
            "active_states": tracker.get("active_states", ["Ready"]),
            "handoff_states": tracker.get("handoff_states", ["Human Review"]),
            "terminal_states": tracker.get("terminal_states", ["Done", "Cancelled"]),
            "plane_base_url": base_url,
            "plane_api_key": api_key_ref,
            "plane_workspace_slug": workspace_slug,
            "plane_project_id": project_id,
        }
    )
    raw["tracker"] = tracker
    raw.setdefault("repo", ".")
    raw.setdefault("agent", {"max_concurrent_agents": 1})
    raw.setdefault("workspace", {"root": ".codex-fleet/workspaces"})
    codex = raw.get("codex")
    if not isinstance(codex, dict):
        codex = {}
    codex.setdefault("runner", "app-server")
    codex.setdefault("command", "codex app-server")
    codex.setdefault("approval_policy", "never")
    codex.setdefault("sandbox_mode", "workspace-write")
    codex.setdefault("model", "gpt-5.5")
    codex.setdefault("reasoning_effort", "low")
    codex.setdefault("turn_timeout_ms", 3_600_000)
    codex.setdefault("stall_timeout_ms", 300_000)
    codex.setdefault("stream_logs", True)
    agent = raw.get("agent")
    if not isinstance(agent, dict):
        agent = {}
    agent.setdefault("max_concurrent_agents", 1)
    if codex_settings:
        _apply_codex_settings_to_raw_config(codex, agent, codex_settings)
    raw["agent"] = agent
    raw["codex"] = codex
    raw.setdefault(
        "token",
        {
            "default_doc_limit": 8000,
            "skill_limit": 4000,
            "raw_artifact_retention": "keep",
            "enable_rtk": False,
            "enable_caveman": False,
            "enable_repomix": False,
        },
    )
    target.write_text(yaml.safe_dump(raw, sort_keys=False))

    if api_key_ref == "$PLANE_API_KEY" and api_key_value:
        write_local_secret(repo, "PLANE_API_KEY", api_key_value)
    return target


def _apply_codex_settings_to_raw_config(codex: dict[str, Any], agent: dict[str, Any], settings: dict[str, Any]) -> None:
    codex["runner"] = "app-server"
    command = settings.get("command")
    codex["command"] = command if isinstance(command, str) and command.strip() else "codex app-server"

    key_map = {
        "approval_policy": "approval_policy",
        "sandbox_mode": "sandbox_mode",
        "default_model": "model",
        "reasoning_effort": "reasoning_effort",
    }
    for source_key, target_key in key_map.items():
        value = settings.get(source_key)
        if isinstance(value, str) and value.strip():
            codex[target_key] = value.strip()

    timeout_seconds = settings.get("job_timeout_seconds")
    if isinstance(timeout_seconds, int) and timeout_seconds > 0:
        codex["turn_timeout_ms"] = timeout_seconds * 1000

    max_agents = settings.get("max_parallel_agents")
    if isinstance(max_agents, int) and max_agents > 0:
        agent["max_concurrent_agents"] = max_agents


def write_local_secret(repo: Path, key: str, value: str) -> Path:
    secrets_dir = repo.expanduser().absolute() / ".codex-fleet"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    secrets_path = secrets_dir / "secrets.env"
    lines = secrets_path.read_text().splitlines() if secrets_path.exists() else []
    prefix = f"{key}="
    updated = False
    next_lines: list[str] = []
    for line in lines:
        if line.strip().startswith(prefix):
            next_lines.append(f"{key}={value}")
            updated = True
        else:
            next_lines.append(line)
    if not updated:
        next_lines.append(f"{key}={value}")
    secrets_path.write_text("\n".join(next_lines).rstrip() + "\n")
    secrets_path.chmod(0o600)
    return secrets_path


def resolve_env_ref(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("$") and len(value) > 1:
        return os.getenv(value[1:])
    return value


def load_local_secrets(repo: Path) -> None:
    path = repo / ".codex-fleet" / "secrets.env"
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")


def _resolve_repo_relative_paths(raw: dict[str, Any], config_dir: Path) -> dict[str, Any]:
    repo_value = raw.get("repo")
    if isinstance(repo_value, str) and repo_value not in {"", "."}:
        repo_path = Path(repo_value).expanduser()
        if not repo_path.is_absolute():
            raw["repo"] = str((config_dir / repo_path).absolute())
    elif repo_value == ".":
        raw["repo"] = str(config_dir.absolute())
    return raw
