from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


def test_node_launcher_runs_cli_in_calling_project(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    script = Path(__file__).resolve().parents[1] / "scripts" / "codex-fleet-npx.js"

    result = subprocess.run(
        [node, str(script), "bootstrap"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert (tmp_path / ".codex-fleet.yml").exists()
    assert (tmp_path / ".codex-fleet" / "tooling" / "codex-fleet-venv").exists()
    assert "Next: codex-fleet up --repo ." in result.stdout

    second = subprocess.run(
        [node, str(script), "--help"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "Obtaining file://" not in second.stdout
    assert "Commands:" in second.stdout


def test_node_launcher_keeps_starting_when_playwright_install_fails(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    script = Path(__file__).resolve().parents[1] / "scripts" / "codex-fleet-npx.js"
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-calls.log"
    cli_started = tmp_path / "cli-started.txt"
    fake_python.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import os
            import pathlib
            import shutil
            import sys

            log = pathlib.Path(os.environ["FAKE_PYTHON_LOG"])
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a") as handle:
                handle.write(repr(sys.argv[1:]) + "\\n")

            args = sys.argv[1:]
            if args == ["--version"]:
                print("Python 3.11.0")
                sys.exit(0)
            if len(args) >= 3 and args[0] == "-m" and args[1] == "venv":
                venv = pathlib.Path(args[2])
                bindir = venv / ("Scripts" if os.name == "nt" else "bin")
                bindir.mkdir(parents=True, exist_ok=True)
                target = bindir / ("python.exe" if os.name == "nt" else "python")
                shutil.copyfile(__file__, target)
                target.chmod(0o755)
                sys.exit(0)
            if args == ["-c", "import codex_fleet"]:
                sys.exit(0)
            if args[:4] == ["-m", "playwright", "install", "chromium"]:
                print("simulated chromium download failure", file=sys.stderr)
                sys.exit(42)
            if args[:2] == ["-m", "codex_fleet"]:
                pathlib.Path(os.environ["FAKE_CODEX_FLEET_STARTED"]).write_text(" ".join(args))
                sys.exit(0)
            sys.exit(1)
            """
        )
    )
    fake_python.chmod(0o755)

    env = {
        **os.environ,
        "PYTHON": str(fake_python),
        "FAKE_PYTHON_LOG": str(log_path),
        "FAKE_CODEX_FLEET_STARTED": str(cli_started),
    }
    result = subprocess.run(
        [node, str(script), "up", "--fake", "--once"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert cli_started.read_text() == "-m codex_fleet up --fake --once"
    assert "Playwright Chromium setup failed; startup will continue" in result.stderr
    assert "playwright install chromium exited with status 42" in result.stderr
    assert "['-m', 'playwright', 'install', 'chromium']" in log_path.read_text()


def test_node_launcher_does_not_setup_playwright_for_help(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    script = Path(__file__).resolve().parents[1] / "scripts" / "codex-fleet-npx.js"
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-calls.log"
    fake_python.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import os
            import pathlib
            import shutil
            import sys

            log = pathlib.Path(os.environ["FAKE_PYTHON_LOG"])
            with log.open("a") as handle:
                handle.write(repr(sys.argv[1:]) + "\\n")

            args = sys.argv[1:]
            if args == ["--version"]:
                sys.exit(0)
            if len(args) >= 3 and args[0] == "-m" and args[1] == "venv":
                venv = pathlib.Path(args[2])
                bindir = venv / ("Scripts" if os.name == "nt" else "bin")
                bindir.mkdir(parents=True, exist_ok=True)
                target = bindir / ("python.exe" if os.name == "nt" else "python")
                shutil.copyfile(__file__, target)
                target.chmod(0o755)
                sys.exit(0)
            if args == ["-c", "import codex_fleet"]:
                sys.exit(0)
            if args[:2] == ["-m", "codex_fleet"]:
                sys.exit(0)
            if args[:4] == ["-m", "playwright", "install", "chromium"]:
                sys.exit(99)
            sys.exit(1)
            """
        )
    )
    fake_python.chmod(0o755)

    subprocess.run(
        [node, str(script), "--help"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHON": str(fake_python), "FAKE_PYTHON_LOG": str(log_path)},
    )

    assert "playwright" not in log_path.read_text()
