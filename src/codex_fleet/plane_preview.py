from __future__ import annotations

import shutil
import subprocess
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from codex_fleet.local_api import DEFAULT_LOCAL_API_HOST
from codex_fleet.plane_manager import PLANE_SOURCE_DIR, PlaneManagerError, ensure_plane_source


class PlanePreviewError(RuntimeError):
    pass


class PlanePreviewServer(ThreadingHTTPServer):
    url: str
    static_dir: Path


def default_plane_build_dir(repo: Path) -> Path:
    return repo.expanduser().absolute() / PLANE_SOURCE_DIR / "apps" / "web" / "build" / "client"


def create_plane_preview_server(
    repo: Path,
    *,
    host: str = DEFAULT_LOCAL_API_HOST,
    port: int = 3000,
    unsafe_allow_remote: bool = False,
    auto_prepare: bool = True,
) -> PlanePreviewServer:
    if not unsafe_allow_remote and host not in {"127.0.0.1", "localhost", "::1"}:
        raise PlanePreviewError("Plane preview must bind to loopback unless explicitly unsafe.")
    static_dir = default_plane_build_dir(repo)
    if not (static_dir / "index.html").exists():
        if not auto_prepare:
            raise PlanePreviewError(
                f"Plane web build not found at {static_dir}. Build the local Plane fork before previewing it."
            )
        prepare_plane_preview_build(repo)
    handler = partial(_SpaHandler, directory=str(static_dir))
    try:
        server = PlanePreviewServer((host, port), handler)
    except OSError:
        server = PlanePreviewServer((host, 0), handler)
    actual_host, actual_port = server.server_address[:2]
    host_text = actual_host.decode("utf-8") if isinstance(actual_host, bytes) else actual_host
    server.url = f"http://{host_text}:{actual_port}"
    server.static_dir = static_dir
    return server


def prepare_plane_preview_build(repo: Path) -> Path:
    repo = repo.expanduser().absolute()
    try:
        source = ensure_plane_source(repo)
    except PlaneManagerError as exc:
        raise PlanePreviewError(str(exc)) from exc
    _require_pnpm()
    _run_pnpm(source.source_dir, "install", "--frozen-lockfile")
    _run_pnpm(source.source_dir, "--filter", "web", "build")
    build_dir = default_plane_build_dir(repo)
    if not (build_dir / "index.html").exists():
        raise PlanePreviewError(f"Plane web build completed, but index.html was not found at {build_dir}.")
    return build_dir


def _require_pnpm() -> None:
    if shutil.which("pnpm") is None:
        raise PlanePreviewError("pnpm is required to build the branded Plane fork.")


def _run_pnpm(source_dir: Path, *args: str) -> None:
    result = subprocess.run(
        ["pnpm", "--dir", str(source_dir), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        raise PlanePreviewError(f"pnpm {' '.join(args)} failed. {details}")


class _SpaHandler(SimpleHTTPRequestHandler):
    def send_head(self) -> BinaryIO | BytesIO | None:
        request_path = self.path.split("?", 1)[0]
        if request_path != "/" and not Path(self.translate_path(request_path)).exists():
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, format: str, *args: object) -> None:
        return
