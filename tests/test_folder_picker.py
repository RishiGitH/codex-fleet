from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import codex_fleet.folder_picker as folder_picker
from codex_fleet.folder_picker import FolderPickerError, pick_folder


def test_pick_folder_returns_normalized_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = tmp_path / "app"
    selected.mkdir()

    monkeypatch.setattr(folder_picker, "_pick_folder_path", lambda: str(selected))

    picked = pick_folder()

    assert picked.path == selected.resolve()
    assert picked.name == "app"


def test_pick_folder_rejects_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = tmp_path / "file.txt"
    selected.write_text("not a folder\n")

    monkeypatch.setattr(folder_picker, "_pick_folder_path", lambda: str(selected))

    with pytest.raises(FolderPickerError, match="not a folder"):
        pick_folder()


def test_macos_picker_uses_osascript(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="/tmp/app\n", stderr="")

    monkeypatch.setattr(folder_picker.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(folder_picker.subprocess, "run", fake_run)

    assert folder_picker._pick_folder_path() == "/tmp/app"
    assert calls[0][0] == "osascript"


def test_picker_cancel_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(folder_picker.subprocess, "run", fake_run)

    with pytest.raises(FolderPickerError, match="cancelled"):
        folder_picker._run_picker(["picker"], "cancelled")
