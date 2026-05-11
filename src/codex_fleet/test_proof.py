from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from codex_fleet.harness import plan_harness

CommandRunner = Callable[[Sequence[str], Path, int, Path], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class TestProofResult:
    status: str
    summary: str
    preview_url: str | None
    commands: tuple[str, ...]
    artifacts: tuple[Path, ...]
    video_path: Path | None = None
    video_url: str | None = None
    screenshot_paths: tuple[Path, ...] = ()
    error: str | None = None


def run_test_proof(
    workspace: Path,
    *,
    run_id: str,
    timeout_seconds: int = 1200,
    command_runner: CommandRunner | None = None,
) -> TestProofResult:
    workspace = workspace.expanduser().resolve()
    artifact_dir = workspace / ".codex-fleet" / "artifacts" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    commands: list[str] = []
    artifacts: list[Path] = []

    scan = plan_harness(workspace).scan
    if scan.stack != "node" or scan.dev_command is None:
        summary = "No runnable web preview was detected, so Test Agent proof was skipped."
        skipped = artifact_dir / "test-proof-skipped.txt"
        skipped.write_text(summary + "\n")
        return TestProofResult(
            status="skipped",
            summary=summary,
            preview_url=None,
            commands=("test proof skipped: no runnable web preview",),
            artifacts=(skipped,),
        )

    runner = command_runner or _run_command_artifact
    install_command = scan.install_command
    if install_command and _dependencies_missing(workspace, scan.package_manager):
        install_log = artifact_dir / "install.log"
        install_result = runner(_split_command(install_command), workspace, min(900, timeout_seconds), install_log)
        commands.append(install_command)
        artifacts.append(install_log)
        if install_result.returncode != 0:
            return _failed("Dependency installation failed.", commands, artifacts, install_result.stdout, install_log)

    if scan.build_command:
        build_log = artifact_dir / "build.log"
        build_result = runner(_split_command(scan.build_command), workspace, min(900, timeout_seconds), build_log)
        commands.append(scan.build_command)
        artifacts.append(build_log)
        if build_result.returncode != 0:
            return _failed("Build failed before preview proof could run.", commands, artifacts, build_result.stdout, build_log)

    browser_error = ensure_playwright_browsers(workspace)
    if browser_error:
        proof = artifact_dir / "playwright-setup-error.txt"
        proof.write_text(browser_error + "\n")
        return _failed("Playwright browser setup failed.", commands, [*artifacts, proof], browser_error, proof)

    port = _free_port()
    preview_url = f"http://127.0.0.1:{port}"
    preview_log = artifact_dir / "preview.log"
    dev_command = _command_with_port(scan.dev_command, port, workspace)
    commands.append(dev_command)
    preview_process = _start_preview_process(dev_command, workspace, port, preview_log)
    artifacts.append(preview_log)
    keep_preview = False
    try:
        ready_error = _wait_for_http(preview_url, timeout_seconds=min(60, timeout_seconds))
        if ready_error:
            return _failed("Preview server did not become ready.", commands, artifacts, ready_error, preview_log)
        capture = _capture_playwright(preview_url, artifact_dir)
        artifacts.extend(capture.artifacts)
        commands.extend(capture.commands)
        if capture.error:
            return _failed("Playwright capture failed.", commands, artifacts, capture.error, capture.artifacts[-1] if capture.artifacts else preview_log)
        video_url = _serve_artifacts_for_video(artifact_dir, capture.video_path)
        keep_preview = True
        preview_pid = getattr(preview_process, "pid", None)
        if preview_pid is not None:
            (artifact_dir / "preview-pid.txt").write_text(f"{preview_pid}\n")
        metadata = artifact_dir / "preview-metadata.json"
        metadata.write_text(
            json.dumps(
                {
                    "preview_url": preview_url,
                    "video_path": str(capture.video_path) if capture.video_path else None,
                    "video_url": video_url,
                    "screenshots": [str(path) for path in capture.screenshot_paths],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        summary_path = artifact_dir / "test-summary.txt"
        summary = "Preview proof passed: build completed, preview loaded, screenshots and video were captured."
        summary_path.write_text(summary + f"\nPreview URL: {preview_url}\nVideo URL: {video_url or 'Unavailable'}\n")
        artifacts.extend([metadata, summary_path])
        return TestProofResult(
            status="passed",
            summary=summary,
            preview_url=preview_url,
            commands=tuple(commands),
            artifacts=tuple(dict.fromkeys(artifacts)),
            video_path=capture.video_path,
            video_url=video_url,
            screenshot_paths=capture.screenshot_paths,
        )
    finally:
        if not keep_preview:
            _stop_process(preview_process)


def ensure_playwright_browsers(workspace: Path) -> str | None:
    tooling_root = workspace.parents[2] if len(workspace.parents) >= 3 and workspace.parents[2].name == ".codex-fleet" else workspace / ".codex-fleet"
    browsers_path = tooling_root / "tooling" / "playwright-browsers"
    browsers_path.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers_path)}
    try:
        import playwright.sync_api  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return "Python package `playwright` is not installed in the Codex Fleet tooling environment."
    if any(path.name.startswith("chromium") for path in browsers_path.iterdir()):
        return None
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=900,
        check=False,
    )
    if result.returncode != 0:
        return result.stdout or "playwright install chromium failed"
    return None


@dataclass(frozen=True)
class _CaptureResult:
    commands: tuple[str, ...]
    artifacts: tuple[Path, ...]
    video_path: Path | None
    screenshot_paths: tuple[Path, ...]
    error: str | None = None


def _capture_playwright(preview_url: str, artifact_dir: Path) -> _CaptureResult:
    desktop = artifact_dir / "desktop.png"
    mobile = artifact_dir / "mobile.png"
    video_dir = artifact_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    capture_log = artifact_dir / "playwright-capture.log"
    commands = ("python playwright chromium capture",)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(record_video_dir=str(video_dir), viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.goto(preview_url, wait_until="networkidle", timeout=60_000)
            body_text = page.locator("body").inner_text(timeout=10_000).strip()
            if not body_text:
                raise RuntimeError("preview page rendered blank body text")
            page.screenshot(path=str(desktop), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(mobile), full_page=True)
            video = page.video
            context.close()
            browser.close()
            video_path = Path(video.path()).resolve() if video is not None else None
            capture_log.write_text(f"Captured {preview_url}\nBody text length: {len(body_text)}\n")
            artifacts = tuple(path for path in (desktop, mobile, video_path, capture_log) if path is not None)
            return _CaptureResult(commands=commands, artifacts=artifacts, video_path=video_path, screenshot_paths=(desktop, mobile))
    except Exception as exc:  # noqa: BLE001 - proof capture converts browser failures into artifacts.
        capture_log.write_text(str(exc) + "\n")
        return _CaptureResult(commands=commands, artifacts=(capture_log,), video_path=None, screenshot_paths=(), error=str(exc))


def _dependencies_missing(workspace: Path, package_manager: str | None) -> bool:
    if package_manager in {"npm", "pnpm", "yarn"}:
        return not (workspace / "node_modules").exists()
    if package_manager in {"uv", "poetry", "pip"}:
        return not (workspace / ".venv").exists()
    return False


def _run_command_artifact(command: Sequence[str], cwd: Path, timeout_seconds: int, artifact: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        check=False,
    )
    artifact.write_text(completed.stdout or "")
    return completed


def _start_preview_process(command: str, workspace: Path, port: int, preview_log: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        _split_command(command),
        cwd=workspace,
        stdout=preview_log.open("w"),
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PORT": str(port), "HOST": "127.0.0.1"},
        start_new_session=True,
    )


def _serve_artifacts_for_video(artifact_dir: Path, video_path: Path | None) -> str | None:
    if video_path is None or not video_path.exists():
        return None
    try:
        relative = video_path.resolve().relative_to(artifact_dir.resolve())
    except ValueError:
        return None
    port = _free_port()
    log_path = artifact_dir / "artifact-server.log"
    process = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=artifact_dir,
        stdout=log_path.open("w"),
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    (artifact_dir / "artifact-server-pid.txt").write_text(f"{process.pid}\n")
    return f"http://127.0.0.1:{port}/{relative.as_posix()}"


def _command_with_port(command: str, port: int, workspace: Path) -> str:
    if "--port" in command or " -p " in command:
        return command
    dev_script = _package_dev_script(workspace)
    if "next" in dev_script:
        return f"{command} -- --hostname 127.0.0.1 --port {port}"
    executable = _split_command(command)[0] if command.strip() else ""
    if executable in {"npm", "pnpm", "yarn"} and " run " in f" {command} ":
        return f"{command} -- --host 127.0.0.1 --port {port}"
    return f"{command} --host 127.0.0.1 --port {port}"


def _package_dev_script(workspace: Path) -> str:
    package_json = workspace / "package.json"
    if not package_json.exists():
        return ""
    try:
        data = json.loads(package_json.read_text())
    except ValueError:
        return ""
    scripts = data.get("scripts") if isinstance(data, dict) else None
    dev = scripts.get("dev") if isinstance(scripts, dict) else None
    return dev if isinstance(dev, str) else ""


def _split_command(command: str) -> list[str]:
    import shlex

    return shlex.split(command)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(url: str, *, timeout_seconds: int) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            request = Request(url, headers={"User-Agent": "codex-fleet-test-proof"})
            with urlopen(request, timeout=2) as response:  # noqa: S310 - localhost preview only.
                if 200 <= response.status < 500:
                    return None
        except Exception as exc:  # noqa: BLE001 - keep polling until timeout.
            last_error = str(exc)
        time.sleep(0.5)
    return last_error or f"{url} did not respond"


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _failed(summary: str, commands: list[str], artifacts: list[Path], error_text: str, primary_artifact: Path) -> TestProofResult:
    return TestProofResult(
        status="failed",
        summary=f"{summary} {error_text}".strip(),
        preview_url=None,
        commands=tuple(commands),
        artifacts=tuple(dict.fromkeys(artifacts)),
        error=f"{summary} See {primary_artifact}.",
    )
