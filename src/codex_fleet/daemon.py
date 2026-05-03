from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from codex_fleet.config import FleetConfig
from codex_fleet.factory import build_runner, build_tracker, default_store_path
from codex_fleet.orchestrator import Orchestrator, OrchestratorResult
from codex_fleet.store import RunStore


@dataclass(frozen=True)
class DaemonStats:
    ticks: int
    dispatched: int


class FleetDaemon:
    def __init__(self, config: FleetConfig, *, fake_runner: bool = False) -> None:
        self.config = config
        self.fake_runner = fake_runner
        self.store = RunStore(default_store_path(config.repo))

    def run(self, *, max_ticks: int | None = None, sleep_seconds: float | None = None) -> DaemonStats:
        ticks = 0
        dispatched = 0
        interval = sleep_seconds
        if interval is None:
            interval = max(1.0, self.config.codex.stall_timeout_ms / 1000)

        while max_ticks is None or ticks < max_ticks:
            result = self.tick()
            ticks += 1
            if result.dispatched:
                dispatched += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            time.sleep(interval)

        return DaemonStats(ticks=ticks, dispatched=dispatched)

    def tick(self) -> OrchestratorResult:
        tracker = build_tracker(self.config)
        runner = build_runner(self.config, fake=self.fake_runner)
        return Orchestrator(
            config=self.config,
            tracker=tracker,
            runner=runner,
            store=self.store,
        ).run_once()


def default_daemon_store(repo: Path) -> Path:
    return default_store_path(repo)
