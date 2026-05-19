import json
import subprocess
import threading
import time
from pathlib import Path
from urllib import error, request

import pytest

from codex_fleet.config import FleetConfig, WorkspaceConfig
from codex_fleet.local_ui import DemoBoard, create_local_ui_server
from codex_fleet.store import RunStore


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_local_ui_runs_ready_item_to_human_review(tmp_path: Path) -> None:
    board = make_board(tmp_path)
    item_id = board.snapshot()["items"][0]["id"]

    ready = board.move_to_ready(str(item_id))
    assert ready["items"][0]["state"] == "Ready"

    result = board.run_ready_item()
    assert result["message"] == "Started fake run for LOCAL-1."
    assert result["items"][0]["state"] == "Running"

    terminal = wait_for_state(board, "Human Review")
    assert terminal["items"][0]["run"]["status"] == "human_review"
    assert terminal["items"][0]["run"]["branch_name"] == "codex-fleet/LOCAL-1"
    assert terminal["items"][0]["run"]["worktree_path"]
    assert "codex-fleet completed run" in "\n".join(terminal["items"][0]["comments"])


def test_local_ui_can_route_failure_to_needs_input(tmp_path: Path) -> None:
    board = make_board(tmp_path)
    item_id = board.snapshot()["items"][0]["id"]
    board.move_to_ready(str(item_id))

    result = board.run_ready_item(succeed=False)
    assert result["message"] == "Started fake run for LOCAL-1."
    assert result["items"][0]["state"] == "Running"

    terminal = wait_for_state(board, "Needs Input")
    assert terminal["items"][0]["run"]["status"] == "rework"
    assert terminal["items"][0]["run"]["error"] == "run failed"
    assert "codex-fleet needs human input" in "\n".join(terminal["items"][0]["comments"])


def test_local_ui_http_requires_csrf_for_mutations(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    try:
        server = create_local_ui_server(config, port=0, store_path=tmp_path / "runs.sqlite3")
    except PermissionError as exc:
        pytest.skip(f"Socket bind unavailable in this sandbox: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        state = _json_get(f"{server.url}/api/state")
        item_id = state["items"][0]["id"]

        response = _post_expect_error(f"{server.url}/api/items/{item_id}/ready", csrf=None)
        assert response == 403

        html = _text_get(server.url)
        csrf = html.split('const csrfToken = "')[1].split('"')[0]
        ready = _json_post(f"{server.url}/api/items/{item_id}/ready", csrf=csrf)
        assert ready["items"][0]["state"] == "Ready"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_ui_rejects_non_loopback_host_without_unsafe_flag(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    try:
        create_local_ui_server(config, host="0.0.0.0", port=0, store_path=tmp_path / "runs.sqlite3")
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("Expected non-loopback bind to be rejected")


def test_local_ui_is_labeled_internal_smoke_harness(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    try:
        server = create_local_ui_server(config, port=0, store_path=tmp_path / "runs.sqlite3")
    except PermissionError as exc:
        pytest.skip(f"Socket bind unavailable in this sandbox: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = _text_get(server.url)
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert "internal smoke harness" in body
    assert "local Kanban" not in body


def make_board(tmp_path: Path) -> DemoBoard:
    config = make_config(tmp_path)
    return DemoBoard(config=config, store=RunStore(tmp_path / "runs.sqlite3"))


def make_config(tmp_path: Path) -> FleetConfig:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    return FleetConfig(repo=repo, workspace=WorkspaceConfig(root=tmp_path / "workspaces")).resolved()


def wait_for_state(board: DemoBoard, state: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = board.snapshot()
        if snapshot["items"][0]["state"] == state:
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {state}")


def _text_get(url: str) -> str:
    with request.urlopen(url, timeout=5) as response:
        return response.read().decode("utf-8")


def _json_get(url: str) -> dict[str, object]:
    with request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


def _json_post(url: str, *, csrf: str) -> dict[str, object]:
    http_request = request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "X-Codex-Fleet-CSRF": csrf},
    )
    with request.urlopen(http_request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


def _post_expect_error(url: str, *, csrf: str | None) -> int:
    headers = {"Content-Type": "application/json"}
    if csrf is not None:
        headers["X-Codex-Fleet-CSRF"] = csrf
    http_request = request.Request(url, data=b"{}", method="POST", headers=headers)
    try:
        request.urlopen(http_request, timeout=5)
    except error.HTTPError as exc:
        return exc.code
    raise AssertionError("Expected HTTP error")
