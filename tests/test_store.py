import sqlite3
from pathlib import Path

from codex_fleet.store import RunStore


def test_run_store_upserts_and_fetches_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")

    store.upsert_run(
        run_id="run-1",
        item_id="item-1",
        identifier="CF-1",
        status="running",
        branch_name="codex-fleet/CF-1",
        worktree_path="/tmp/worktree",
    )
    store.add_event("run-1", "started", {"ok": True})
    store.add_artifact("run-1", "/tmp/worktree/.codex-fleet-fake-run.txt")
    store.upsert_run(
        run_id="run-1",
        item_id="item-1",
        identifier="CF-1",
        status="done",
    )

    run = store.get_run("run-1")

    assert run is not None
    assert run.status == "done"
    assert run.identifier == "CF-1"
    assert store.list_events("run-1")[0].payload == {"ok": True}
    assert store.list_artifacts("run-1")[0].path == "/tmp/worktree/.codex-fleet-fake-run.txt"


def test_run_store_releases_only_stale_active_claims(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    assert store.try_claim_item("item-old", "run-old") is True
    assert store.try_claim_item("item-fresh", "run-fresh") is True
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update claims set updated_at = datetime('now', '-2 hours') where item_id = ?",
            ("item-old",),
        )

    stale = store.release_stale_claims(max_age_seconds=300)

    assert [claim.item_id for claim in stale] == ["item-old"]
    assert store.try_claim_item("item-old", "run-new") is True
    assert store.try_claim_item("item-fresh", "run-other") is False


def test_run_store_updates_run_status(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    store.upsert_run(run_id="run-1", item_id="item-1", identifier="CF-1", status="running_codex")

    store.update_run_status("run-1", "stalled", error="claim expired")

    run = store.get_run("run-1")
    assert run is not None
    assert run.status == "stalled"
    assert run.error == "claim expired"


def test_run_store_upserts_task_metadata(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")

    store.upsert_task_metadata(
        item_id="item-1",
        source="human-requested",
        depth=0,
        settings={"default_model": "gpt-5.5"},
    )
    store.upsert_task_metadata(
        item_id="item-1",
        source="agent-followup",
        depth=1,
        parent_item_id="parent-1",
        parent_identifier="CF-1",
        parent_run_id="run-1",
        created_by_run_id="run-1",
        settings={"agent_task_mode": "agent_task_planner"},
    )

    metadata = store.get_task_metadata("item-1")

    assert metadata is not None
    assert metadata.source == "agent-followup"
    assert metadata.depth == 1
    assert metadata.parent_item_id == "parent-1"
    assert metadata.settings == {"agent_task_mode": "agent_task_planner"}
