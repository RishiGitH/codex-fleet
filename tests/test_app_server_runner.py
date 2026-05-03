import sys
from pathlib import Path

from codex_fleet.models import WorkItem
from codex_fleet.runner import CodexAppServerRunner


def test_codex_app_server_runner_with_fake_server(tmp_path: Path) -> None:
    fake_server = Path(__file__).parent / "fixtures" / "fake_app_server.py"
    item = WorkItem(
        id="1",
        identifier="CF-1",
        title="Run fake server",
        description="Exercise protocol",
        state="Ready",
    )
    runner = CodexAppServerRunner(
        command=f"{sys.executable} {fake_server}",
        timeout_seconds=5,
    )

    result = runner.run(item, tmp_path)

    assert result.success is True
    assert "CF-1" in result.summary
