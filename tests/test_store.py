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
        agent_role="worker",
        agent_name="Worker",
        agent_avatar="W",
        settings={"approval_policy": "never"},
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
    assert run.agent_role == "worker"
    assert run.agent_name == "Worker"
    assert run.settings == {}
    assert store.list_events("run-1")[0].payload == {"ok": True}
    artifact = store.list_artifacts("run-1")[0]
    assert artifact.path == "/tmp/worktree/.codex-fleet-fake-run.txt"
    assert artifact.redaction == "local"


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
    claim = store.get_claim("item-fresh")
    assert claim is not None
    assert claim.status == "active"


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
        root_item_id="parent-1",
        role="worker",
        depends_on=("dep-1",),
        generation=1,
        approval_mode="full_agent",
        terminal_outcome="human_review",
        settings={"agent_task_mode": "agent_task_planner"},
    )

    metadata = store.get_task_metadata("item-1")

    assert metadata is not None
    assert metadata.source == "agent-followup"
    assert metadata.depth == 1
    assert metadata.parent_item_id == "parent-1"
    assert metadata.root_item_id == "parent-1"
    assert metadata.role == "worker"
    assert metadata.depends_on == ("dep-1",)
    assert metadata.generation == 1
    assert metadata.approval_mode == "full_agent"
    assert metadata.terminal_outcome == "human_review"
    assert metadata.settings == {"agent_task_mode": "agent_task_planner"}
