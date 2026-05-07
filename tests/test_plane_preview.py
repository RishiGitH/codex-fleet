from __future__ import annotations

import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from codex_fleet.plane_preview import (
    PlanePreviewError,
    create_plane_preview_server,
    default_plane_build_dir,
    prepare_plane_preview_build,
)


def test_plane_preview_requires_built_web_client(tmp_path: Path) -> None:
    with pytest.raises(PlanePreviewError, match="Plane web build not found"):
        create_plane_preview_server(tmp_path, port=0, auto_prepare=False)


def test_plane_preview_auto_prepares_missing_build(monkeypatch, tmp_path: Path) -> None:
    build_dir = default_plane_build_dir(tmp_path)
    source_dir = tmp_path / ".codex-fleet" / "plane-src"
    calls: list[tuple[str, ...]] = []

    class Source:
        def __init__(self) -> None:
            self.source_dir = source_dir

    monkeypatch.setattr("codex_fleet.plane_preview.ensure_plane_source", lambda _repo: Source())
    monkeypatch.setattr("codex_fleet.plane_preview.shutil.which", lambda _binary: "/usr/bin/pnpm")

    def fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(tuple(command))
        if "build" in command:
            build_dir.mkdir(parents=True)
            (build_dir / "index.html").write_text("<h1>codex-fleet</h1>\n")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("codex_fleet.plane_preview.subprocess.run", fake_run)

    prepared = prepare_plane_preview_build(tmp_path)

    assert prepared == build_dir
    assert calls == [
        ("pnpm", "--dir", str(source_dir), "install", "--frozen-lockfile"),
        ("pnpm", "--dir", str(source_dir), "--filter", "web", "build"),
    ]


def test_plane_preview_serves_spa_fallback(tmp_path: Path) -> None:
    build_dir = default_plane_build_dir(tmp_path)
    build_dir.mkdir(parents=True)
    (build_dir / "index.html").write_text("<h1>codex-fleet</h1>\n")

    server = create_plane_preview_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"{server.url}/codex-fleet/onboarding", timeout=5) as response:  # noqa: S310 - loopback.
            body = response.read().decode("utf-8")

        assert response.status == 200
        assert "codex-fleet" in body
    finally:
        server.shutdown()
        thread.join(timeout=5)
