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
