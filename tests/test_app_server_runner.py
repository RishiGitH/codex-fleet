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
        model="gpt-5.5",
        reasoning_effort="low",
        agent_role="implementer",
        timeout_seconds=5,
    )

    result = runner.run(item, tmp_path)

    assert result.success is True
    assert "CF-1" in result.summary
    assert result.codex_thread_id == "thread-1"
    assert result.codex_turn_id == "turn-1"
    assert any("model=gpt-5.5" in message.content for message in result.messages)
    assert any("reasoning=low" in message.content for message in result.messages)
    assert any("Agent role: implementer" in message.content for message in result.messages)


def test_codex_app_server_prompt_strips_plane_description_html(tmp_path: Path) -> None:
    fake_server = Path(__file__).parent / "fixtures" / "fake_app_server.py"
    item = WorkItem(
        id="1",
        identifier="CF-1",
        title="Run fake server",
        description="<div><p><strong>Goal:</strong> Build it.</p><p>Ship proof.</p></div>",
        state="Ready",
    )
    runner = CodexAppServerRunner(
        command=f"{sys.executable} {fake_server}",
        agent_role="implementer",
        reasoning_effort="low",
        timeout_seconds=5,
    )

    result = runner.run(item, tmp_path)
    prompt = next(message.content for message in result.messages if message.kind == "chat_user")

    assert "<div>" not in prompt
    assert "<strong>" not in prompt
    assert "Goal: Build it." in prompt
    assert "Ship proof." in prompt


def test_planner_app_server_run_blocks_when_contract_missing(tmp_path: Path) -> None:
    fake_server = Path(__file__).parent / "fixtures" / "fake_app_server.py"
    item = WorkItem(
        id="1",
        identifier="CF-1",
        title="Plan with fake server",
        description="Exercise planner protocol failure",
        state="Ready",
    )
    runner = CodexAppServerRunner(
        command=f"{sys.executable} {fake_server}",
        model="gpt-5.5",
        reasoning_effort="low",
        agent_role="planner",
        timeout_seconds=5,
    )

    result = runner.run(item, tmp_path)

    assert result.success is False
    assert result.needs_input is not None
    assert "missing codex-fleet-planner-output" in result.needs_input.question


def test_app_server_deltas_are_merged_into_assistant_message(tmp_path: Path) -> None:
    from codex_fleet.runner import _app_server_messages

    item = WorkItem(id="1", identifier="CF-1", title="Plan", description="Plan work", state="Ready")
    messages = _app_server_messages(
        item=item,
        role="planner",
        prompt="Work item CF-1\n\nAgent role: planner",
        notifications=(
            {"method": "item/agentMessage/delta", "params": {"delta": "Hello "}},
            {"method": "item/agentMessage/delta", "params": {"delta": "world"}},
            {"method": "turn/completed", "params": {"summary": "done"}},
        ),
        output_path=tmp_path / "transcript.txt",
    )

    assistant_messages = [message for message in messages if message.kind in {"assistant", "chat_assistant"}]
    assert assistant_messages[0].content == "Hello world"
    assert all(message.content != "item/agentMessage/delta" for message in messages)


def test_app_server_protocol_noise_is_not_chat(tmp_path: Path) -> None:
    from codex_fleet.runner import _app_server_messages

    item = WorkItem(id="1", identifier="CF-1", title="Plan", description="Plan work", state="Ready")
    messages = _app_server_messages(
        item=item,
        role="quality_reviewer",
        prompt="Work item CF-1\n\nAgent role: quality_reviewer",
        notifications=(
            {"method": "item/started", "params": {"message": "userMessage"}},
            {"method": "thread/status/changed", "params": {"message": "thread/status/changed"}},
            {"method": "item/agentMessage/delta", "params": {"delta": "Readable assistant output."}},
        ),
        output_path=tmp_path / "transcript.txt",
    )

    assert [message.content for message in messages if message.kind == "chat_assistant"] == ["Readable assistant output."]
    assert all(message.content not in {"userMessage", "thread/status/changed"} for message in messages)


def test_app_server_assistant_message_keeps_full_content_for_planner_parsing(tmp_path: Path) -> None:
    from codex_fleet.runner import _app_server_messages, parse_planner_tasks

    item = WorkItem(id="1", identifier="CF-1", title="Plan", description="Plan work", state="Ready")
    long_prefix = "x" * 5000
    planner_output = (
        '```codex-fleet-planner-output\n'
        '{"summary":"plan","tasks":[{"title":"Build page","description":"Implement the page.",'
        '"role":"implementer","priority":"high","depends_on":[],"workflow_mode":"execute_only"}],"reviewers":[]}\n'
        '```'
    )
    messages = _app_server_messages(
        item=item,
        role="planner",
        prompt="Work item CF-1\n\nAgent role: planner",
        notifications=(
            {"method": "item/agentMessage/delta", "params": {"delta": long_prefix}},
            {"method": "item/agentMessage/delta", "params": {"delta": planner_output}},
        ),
        output_path=tmp_path / "transcript.txt",
    )
    transcript_text = "\n".join(message.content for message in messages if message.kind != "tool_result")

    assert parse_planner_tasks(transcript_text)[0].title == "Build page"


def test_planner_reviewers_do_not_duplicate_existing_reviewer_tasks() -> None:
    from codex_fleet.runner import parse_planner_tasks

    output = (
        "```codex-fleet-planner-output\n"
        '{"summary":"plan","tasks":['
        '{"title":"Build page","description":"Implement the page.","role":"implementer","depends_on":[]},'
        '{"title":"Quality review","description":"Review the page.","role":"quality_reviewer","depends_on":["Build page"]},'
        '{"title":"Test page","description":"Test the page.","role":"test_reviewer","depends_on":["Build page"]}'
        '],"reviewers":["quality_reviewer","test_reviewer"]}\n'
        "```"
    )

    tasks = parse_planner_tasks(output)

    assert [task.role for task in tasks].count("quality_reviewer") == 1
    assert [task.role for task in tasks].count("test_reviewer") == 1
