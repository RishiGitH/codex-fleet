from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from codex_fleet.plane_manager import inspect_plane_runtime

RUNTIME_FILE = Path(".codex-fleet") / "runtime.json"


@dataclass(frozen=True)
class RuntimeRecord:
    pid: int
    kind: str
    url: str
    api_url: str | None
    created_at: str


@dataclass(frozen=True)
class StopResult:
    target: str
    stopped: bool
    message: str


def write_runtime_record(repo: Path, *, kind: str, url: str, api_url: str | None = None) -> Path:
    path = runtime_record_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = RuntimeRecord(
        pid=os.getpid(),
        kind=kind,
        url=url,
        api_url=api_url,
        created_at=datetime.now(UTC).isoformat(),
    )
    path.write_text(json.dumps(asdict(record), indent=2, sort_keys=True) + "\n")
    return path


def remove_runtime_record(repo: Path) -> None:
    path = runtime_record_path(repo)
    if path.exists():
        path.unlink()


def runtime_record_path(repo: Path) -> Path:
    return repo.expanduser().absolute() / RUNTIME_FILE


def read_runtime_record(repo: Path) -> RuntimeRecord | None:
    path = runtime_record_path(repo)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    pid = payload.get("pid")
    kind = payload.get("kind")
    url = payload.get("url")
    created_at = payload.get("created_at")
    api_url = payload.get("api_url")
    if not isinstance(pid, int) or not isinstance(kind, str) or not isinstance(url, str) or not isinstance(created_at, str):
        return None
    return RuntimeRecord(
        pid=pid,
        kind=kind,
        url=url,
        api_url=api_url if isinstance(api_url, str) else None,
        created_at=created_at,
    )


def stop_runtime_process(repo: Path) -> StopResult:
    record = read_runtime_record(repo)
    path = runtime_record_path(repo)
    if record is None:
        return StopResult(target=str(path), stopped=False, message="No codex-fleet runtime record found.")
    if record.pid == os.getpid():
        return StopResult(target=str(path), stopped=False, message="Refusing to stop the current process.")
    if not _process_exists(record.pid):
        remove_runtime_record(repo)
        return StopResult(target=str(path), stopped=False, message=f"Runtime process {record.pid} is not running.")
    os.kill(record.pid, signal.SIGTERM)
    remove_runtime_record(repo)
    return StopResult(target=str(path), stopped=True, message=f"Stopped {record.kind} process {record.pid}.")


def stop_loopback_ports(ports: list[int]) -> list[StopResult]:
    if shutil.which("lsof") is None:
        return [
            StopResult(target="ports", stopped=False, message="lsof is not available; cannot discover preview processes.")
        ]
    results: list[StopResult] = []
    for port in ports:
        pids = _pids_for_port(port)
        if not pids:
            results.append(StopResult(target=f"tcp:{port}", stopped=False, message="No listener found."))
            continue
        stopped = 0
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                stopped += 1
            except ProcessLookupError:
                continue
        results.append(StopResult(target=f"tcp:{port}", stopped=stopped > 0, message=f"Stopped {stopped} listener(s)."))
    return results


def stop_plane_runtime(repo: Path) -> StopResult:
    install = inspect_plane_runtime(repo.expanduser().absolute())
    if install.app_dir is None:
        return StopResult(target=str(install.runtime_dir), stopped=False, message="No local Plane app runtime found.")
    compose_file = _compose_file(install.app_dir)
    if compose_file is None:
        return StopResult(target=str(install.app_dir), stopped=False, message="No docker-compose file found.")
    if shutil.which("docker") is None:
        return StopResult(target=str(install.app_dir), stopped=False, message="Docker is not available.")
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "down"],
        cwd=install.app_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "docker compose down failed"
        return StopResult(target=str(install.app_dir), stopped=False, message=message)
    return StopResult(target=str(install.app_dir), stopped=True, message="Stopped local Plane Docker Compose runtime.")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pids_for_port(port: int) -> list[int]:
    result = subprocess.run(
        ["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return pids


def _compose_file(app_dir: Path) -> Path | None:
    for name in ("docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml"):
        candidate = app_dir / name
        if candidate.exists():
            return candidate
    return None
