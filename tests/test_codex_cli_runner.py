import subprocess
import sys
from pathlib import Path

from codex_fleet.models import WorkItem
from codex_fleet.runner import (
    CodexCliRunner,
    check_codex_cli_preflight,
    parse_needs_input,
    parse_proposed_tasks,
    parse_token_usage,
)


def test_codex_cli_runner_executes_command_in_workspace(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    fake_cli = tmp_path / "fake_codex_cli.py"
    fake_cli.write_text(
        "import pathlib, sys\n"
        "prompt = sys.stdin.read()\n"
        "pathlib.Path('codex-output.txt').write_text(prompt)\n"
        "pathlib.Path('codex-argv.txt').write_text('\\n'.join(sys.argv[1:]))\n"
        "print('fake codex cli completed')\n"
    )
    runner = CodexCliRunner(command=f"{sys.executable} {fake_cli}", timeout_seconds=5)
    item = WorkItem(id="1", identifier="CF-1", title="Use CLI", description="Run direct Codex CLI.", state="Ready")

    result = runner.run(item, tmp_path)

    assert result.success is True
    assert "fake codex cli completed" in result.summary
    assert "codex-output.txt" in result.changed_files
    assert (tmp_path / ".codex-fleet-codex-cli-output.txt").exists()
    argv = (tmp_path / "codex-argv.txt").read_text().splitlines()
    assert "--ask-for-approval" not in argv
    assert "--cd" in argv
    assert "--sandbox" in argv
    assert "-c" in argv
    assert 'approval_policy="on-request"' in argv


def test_codex_cli_runner_reports_failure(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    fake_cli = tmp_path / "fake_codex_cli.py"
    fake_cli.write_text("import sys\nprint('nope')\nsys.exit(7)\n")
    runner = CodexCliRunner(command=f"{sys.executable} {fake_cli}", timeout_seconds=5)
    item = WorkItem(id="1", identifier="CF-1", title="Use CLI", description=None, state="Ready")

    result = runner.run(item, tmp_path)

    assert result.success is False
    assert result.error == "nope"
    assert result.artifacts[0].name == ".codex-fleet-codex-cli-output.txt"


def test_codex_cli_runner_extracts_token_usage(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    fake_cli = tmp_path / "fake_codex_cli.py"
    fake_cli.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('done')\n"
        "print('Token usage: input_tokens=1,234 output_tokens=56 total_tokens=1,290')\n"
    )
    runner = CodexCliRunner(command=f"{sys.executable} {fake_cli}", timeout_seconds=5)
    item = WorkItem(id="1", identifier="CF-1", title="Use CLI", description=None, state="Ready")

    result = runner.run(item, tmp_path)

    assert result.token_usage is not None
    assert result.token_usage.input_tokens == 1234
    assert result.token_usage.output_tokens == 56
    assert result.token_usage.total_tokens == 1290


def test_parse_token_usage_from_json_line() -> None:
    usage = parse_token_usage('{"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}\n')

    assert usage is not None
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.total_tokens == 15


def test_codex_cli_runner_streams_output_to_terminal_and_artifact(tmp_path: Path, capsys) -> None:
    _init_git_repo(tmp_path)
    fake_cli = tmp_path / "fake_codex_cli.py"
    fake_cli.write_text(
        "import sys, time\n"
        "sys.stdin.read()\n"
        "print('stream line one', flush=True)\n"
        "time.sleep(0.05)\n"
        "print('stream line two', flush=True)\n"
    )
    runner = CodexCliRunner(command=f"{sys.executable} {fake_cli}", timeout_seconds=5, stream_logs=True)
    item = WorkItem(id="1", identifier="CF-1", title="Use CLI", description=None, state="Ready")

    result = runner.run(item, tmp_path)

    captured = capsys.readouterr()
    artifact = tmp_path / ".codex-fleet-codex-cli-output.txt"
    assert result.success is True
    assert "stream line one" in captured.out
    assert "stream line two" in captured.out
    assert "stream line one" in artifact.read_text()


def test_parse_proposed_tasks_from_codex_output() -> None:
    output = """done
```codex-fleet-proposed-tasks
[
  {"title": "Add browser verification", "description": "Cover native Plane create project flow."}
]
```
"""

    tasks = parse_proposed_tasks(output)

    assert len(tasks) == 1
    assert tasks[0].title == "Add browser verification"
    assert tasks[0].labels == ("agent-proposed",)


def test_parse_proposed_tasks_supports_canonical_child_metadata() -> None:
    output = """done
```codex-fleet-proposed-tasks
[
  {"title": "Review security", "role": "security_reviewer", "depends_on": ["CF-2"], "suggested_state": "Ready", "labels": ["security"]}
]
```
"""

    tasks = parse_proposed_tasks(output)

    assert len(tasks) == 1
    assert tasks[0].role == "security_reviewer"
    assert tasks[0].depends_on == ("CF-2",)
    assert tasks[0].suggested_state == "Ready"
    assert "security" in tasks[0].labels


def test_parse_needs_input_from_codex_output() -> None:
    output = """blocked
```codex-fleet-needs-input
{"question": "Which deployment target should I use?", "needed_to_continue": true}
```
"""

    needs_input = parse_needs_input(output)

    assert needs_input is not None
    assert needs_input.question == "Which deployment target should I use?"


def test_codex_cli_runner_blocks_missing_auth_before_running(monkeypatch, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    fake_invoked = tmp_path / "invoked"
    monkeypatch.setattr("codex_fleet.runner.shutil.which", lambda binary: f"/usr/bin/{binary}")

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command == ["codex", "exec", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="--cd <DIR>\n--sandbox <SANDBOX_MODE>\n-c, --config <key=value>\nstdin prompt\n",
                stderr="",
            )
        if command == ["codex", "login", "status"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Not logged in\n")
        fake_invoked.write_text("yes")
        return subprocess.CompletedProcess(command, 0, stdout="should not run\n", stderr="")

    monkeypatch.setattr("codex_fleet.runner.subprocess.run", fake_run)
    runner = CodexCliRunner(command="codex exec", timeout_seconds=5)
    item = WorkItem(id="1", identifier="CF-1", title="Use CLI", description=None, state="Ready")

    result = runner.run(item, tmp_path)

    assert result.success is False
    assert result.summary == "Codex CLI preflight failed."
    assert result.error is not None
    assert "authentication was not confirmed" in result.error
    assert not fake_invoked.exists()


def test_codex_cli_runner_blocks_contract_change(monkeypatch, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    monkeypatch.setattr("codex_fleet.runner.shutil.which", lambda binary: f"/usr/bin/{binary}")

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command == ["codex", "exec", "--help"]:
            return subprocess.CompletedProcess(command, 0, stdout="different help\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("codex_fleet.runner.subprocess.run", fake_run)
    runner = CodexCliRunner(command="codex exec", timeout_seconds=5)
    item = WorkItem(id="1", identifier="CF-1", title="Use CLI", description=None, state="Ready")

    result = runner.run(item, tmp_path)

    assert result.success is False
    assert result.error is not None
    assert "Missing support" in result.error
    assert "--cd" in result.error


def test_codex_cli_preflight_skips_custom_command(tmp_path: Path) -> None:
    result = check_codex_cli_preflight([sys.executable, str(tmp_path / "fake.py")])

    assert result.ok is True
    assert "skipped" in result.message


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
