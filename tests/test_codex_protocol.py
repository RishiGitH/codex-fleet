import pytest

from codex_fleet.codex.protocol import (
    ProtocolError,
    extract_thread_id,
    extract_turn_id,
    initialize_message,
    parse_json_line,
    response_result,
    thread_start_message,
    turn_start_message,
)


def test_initialize_message_serializes_as_line() -> None:
    line = initialize_message(1).to_line()

    assert line.endswith("\n")
    assert '"method":"initialize"' in line
    assert '"id":1' in line


def test_thread_start_message_contains_cwd_and_sandbox() -> None:
    payload = thread_start_message(2, cwd="/repo", sandbox="workspace-write").payload

    assert payload["method"] == "thread/start"
    assert payload["params"]["cwd"] == "/repo"
    assert payload["params"]["sandbox"] == "workspace-write"


def test_turn_start_message_contains_prompt_and_policy() -> None:
    payload = turn_start_message(
        3,
        thread_id="thread-1",
        prompt="do work",
        cwd="/repo",
        title="CF-1",
        approval_policy="on-request",
        sandbox_policy={"mode": "workspace-write"},
    ).payload

    assert payload["method"] == "turn/start"
    assert payload["params"]["threadId"] == "thread-1"
    assert payload["params"]["input"][0]["text"] == "do work"


def test_response_result_validates_id_and_error() -> None:
    result = response_result({"id": 7, "result": {"ok": True}}, 7)

    assert result == {"ok": True}

    with pytest.raises(ProtocolError):
        response_result({"id": 8, "result": {}}, 7)

    with pytest.raises(ProtocolError):
        response_result({"id": 7, "error": {"message": "bad"}}, 7)


def test_extract_ids() -> None:
    assert extract_thread_id({"thread": {"id": "thread-1"}}) == "thread-1"
    assert extract_turn_id({"turn": {"id": "turn-1"}}) == "turn-1"


def test_parse_json_line_rejects_invalid_payload() -> None:
    assert parse_json_line('{"id":1}') == {"id": 1}
    with pytest.raises(ProtocolError):
        parse_json_line("not json")
