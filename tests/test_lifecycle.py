import json
import signal
import subprocess
from pathlib import Path

from codex_fleet.lifecycle import (
    read_runtime_record,
    remove_runtime_record,
    runtime_record_path,
    stop_loopback_ports,
    stop_plane_app_containers,
    stop_runtime_process,
    write_runtime_record,
)


def test_runtime_record_round_trip(tmp_path: Path) -> None:
    path = write_runtime_record(tmp_path, kind="plane-fork-preview", url="http://127.0.0.1:17300", api_url=None)

    record = read_runtime_record(tmp_path)

    assert path == runtime_record_path(tmp_path)
    assert record is not None
    assert record.kind == "plane-fork-preview"
    assert record.url == "http://127.0.0.1:17300"

    remove_runtime_record(tmp_path)
    assert read_runtime_record(tmp_path) is None


def test_stop_runtime_process_removes_stale_record(tmp_path: Path, monkeypatch) -> None:
    path = write_runtime_record(tmp_path, kind="plane-fork-preview", url="http://127.0.0.1:17300")
    payload = json.loads(path.read_text())
    payload["pid"] = 999999
    path.write_text(json.dumps(payload))

    def fake_kill(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr("os.kill", fake_kill)

    result = stop_runtime_process(tmp_path)

    assert result.stopped is False
    assert "not running" in result.message
    assert not runtime_record_path(tmp_path).exists()


def test_stop_loopback_ports_terminates_listeners(monkeypatch) -> None:
    killed: list[tuple[int, int]] = []

    def fake_run(command, **_kwargs):
        assert command == ["lsof", "-tiTCP:8790", "-sTCP:LISTEN"]
        return subprocess.CompletedProcess(command, 0, stdout="123\n456\n", stderr="")

    def fake_kill(pid: int, sig: int) -> None:
        killed.append((pid, sig))

    monkeypatch.setattr("shutil.which", lambda _binary: "/usr/sbin/lsof")
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("os.kill", fake_kill)

    results = stop_loopback_ports([8790])

    assert results[0].stopped is True
    assert killed == [(123, signal.SIGTERM), (456, signal.SIGTERM)]


def test_stop_plane_app_containers_skips_without_docker(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _binary: None)

    result = stop_plane_app_containers()

    assert result.stopped is False
    assert "Docker is not available" in result.message


def test_stop_plane_app_containers_stops_running_plane_containers(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command[:3] == ["docker", "ps", "--format"]:
            return subprocess.CompletedProcess(command, 0, stdout="plane-app-web-1\nplane-app-api-1\nother\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="plane-app-web-1\nplane-app-api-1\n", stderr="")

    monkeypatch.setattr("shutil.which", lambda _binary: "/usr/local/bin/docker")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = stop_plane_app_containers()

    assert result.stopped is True
    assert commands[-1] == ["docker", "stop", "plane-app-api-1", "plane-app-web-1"]
