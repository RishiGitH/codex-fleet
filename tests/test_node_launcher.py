from __future__ import annotations

import shutil
import subprocess
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
