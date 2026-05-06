import json
import sys
from pathlib import Path

from click.testing import CliRunner

from codex_fleet.capture import capture_command
from codex_fleet.cli.main import main


def test_capture_command_stores_raw_summary_and_metadata(tmp_path: Path) -> None:
    result = capture_command(
        tmp_path,
        (sys.executable, "-c", "import sys; print('raw ok'); print('warn', file=sys.stderr)"),
    )

    assert result.returncode == 0
    assert "raw ok" in result.raw_path.read_text()
    assert "warn" in result.raw_path.read_text()
    assert "Exit code: 0" in result.summary_path.read_text()
    metadata = json.loads(result.metadata_path.read_text())
    assert metadata["returncode"] == 0


def test_capture_cli_propagates_exit_code_and_writes_raw(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "capture",
            "--repo",
            str(tmp_path),
            "--",
            sys.executable,
            "-c",
            "print('bad'); raise SystemExit(7)",
        ],
    )

    assert result.exit_code == 7
    artifacts = sorted((tmp_path / ".codex-fleet" / "artifacts").glob("*/raw.txt"))
    assert artifacts
    assert "bad" in artifacts[-1].read_text()
