from __future__ import annotations

import subprocess
from pathlib import Path

from codex_fleet import test_proof


def test_test_proof_skips_when_no_runnable_web_project(tmp_path: Path) -> None:
    result = test_proof.run_test_proof(tmp_path, run_id="run-1")

    assert result.status == "skipped"
    assert result.preview_url is None
    assert result.artifacts[0].name == "test-proof-skipped.txt"


def test_test_proof_installs_builds_previews_and_captures(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"next build","dev":"next dev"}}\n'
    )

    calls: list[list[str]] = []

    def fake_runner(command: list[str], cwd: Path, timeout_seconds: int, artifact: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        artifact.write_text("ok\n")
        if command == ["npm", "install"]:
            (cwd / "node_modules").mkdir()
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    class FakeProcess:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout: int = 0) -> int:
            return 0

        def kill(self) -> None:
            return None

    captured_commands: list[list[str]] = []

    def fake_start(command: str, workspace: Path, port: int, preview_log: Path) -> FakeProcess:
        preview_log.write_text("preview started\n")
        captured_commands.append(command)
        return FakeProcess()

    video = tmp_path / ".codex-fleet" / "artifacts" / "run-1" / "video.webm"
    desktop = tmp_path / ".codex-fleet" / "artifacts" / "run-1" / "desktop.png"
    mobile = tmp_path / ".codex-fleet" / "artifacts" / "run-1" / "mobile.png"

    def fake_capture(preview_url: str, artifact_dir: Path) -> test_proof._CaptureResult:
        video.write_text("video")
        desktop.write_text("desktop")
        mobile.write_text("mobile")
        return test_proof._CaptureResult(
            commands=("python playwright chromium capture",),
            artifacts=(video, desktop, mobile),
            video_path=video,
            screenshot_paths=(desktop, mobile),
        )

    monkeypatch.setattr(test_proof, "ensure_playwright_browsers", lambda _workspace: None)
    monkeypatch.setattr(test_proof, "_wait_for_http", lambda _url, timeout_seconds: None)
    monkeypatch.setattr(test_proof, "_capture_playwright", fake_capture)
    monkeypatch.setattr(test_proof, "_start_preview_process", fake_start)
    monkeypatch.setattr(test_proof, "_serve_artifacts_for_video", lambda _artifact_dir, _video_path: "http://127.0.0.1:5555/video.webm")

    result = test_proof.run_test_proof(tmp_path, run_id="run-1", command_runner=fake_runner)

    assert result.status == "passed"
    assert result.preview_url and result.preview_url.startswith("http://127.0.0.1:")
    assert result.video_path == video
    assert result.video_url == "http://127.0.0.1:5555/video.webm"
    assert result.screenshot_paths == (desktop, mobile)
    assert ["npm", "install"] in calls
    assert ["npm", "run", "build"] in calls
    assert captured_commands[0].startswith("npm run dev")
    assert "--hostname" in captured_commands[0]
    assert "--port" in captured_commands[0]


def test_test_proof_failed_install_returns_needs_input_data(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"dev":"vite --host 127.0.0.1"}}\n')

    def failing_runner(command: list[str], cwd: Path, timeout_seconds: int, artifact: Path) -> subprocess.CompletedProcess[str]:
        artifact.write_text("network failed\n")
        return subprocess.CompletedProcess(command, 1, stdout="network failed\n", stderr="")

    result = test_proof.run_test_proof(tmp_path, run_id="run-2", command_runner=failing_runner)

    assert result.status == "failed"
    assert "Dependency installation failed" in result.summary
    assert any(path.name == "install.log" for path in result.artifacts)
