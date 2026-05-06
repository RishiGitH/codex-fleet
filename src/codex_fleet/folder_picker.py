from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FolderPickerError(RuntimeError):
    pass


@dataclass(frozen=True)
class PickedFolder:
    path: Path

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)


def pick_folder() -> PickedFolder:
    raw_path = _pick_folder_path()
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise FolderPickerError(f"Selected folder does not exist: {path}")
    if not path.is_dir():
        raise FolderPickerError(f"Selected path is not a folder: {path}")
    return PickedFolder(path=path)


def _pick_folder_path() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return _run_picker(
            [
                "osascript",
                "-e",
                'POSIX path of (choose folder with prompt "Choose a project folder for codex-fleet")',
            ],
            "Folder selection was cancelled.",
        )
    if system == "windows":
        return _run_picker(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
                    "$dialog.Description = 'Choose a project folder for codex-fleet'; "
                    "if ($dialog.ShowDialog() -eq 'OK') { $dialog.SelectedPath } else { exit 1 }"
                ),
            ],
            "Folder selection was cancelled.",
        )
    for command in (
        ["zenity", "--file-selection", "--directory", "--title=Choose a project folder for codex-fleet"],
        ["kdialog", "--getexistingdirectory", str(Path.home())],
    ):
        if shutil.which(command[0]):
            return _run_picker(command, "Folder selection was cancelled.")
    return _run_tkinter_picker()


def _run_picker(command: list[str], cancel_message: str) -> str:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FolderPickerError(f"Folder picker failed: {exc}") from exc
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip() or cancel_message
        raise FolderPickerError(message)
    selected = completed.stdout.strip()
    if not selected:
        raise FolderPickerError(cancel_message)
    return selected


def _run_tkinter_picker() -> str:
    script = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "path = filedialog.askdirectory(title='Choose a project folder for codex-fleet')\n"
        "print(path)\n"
    )
    return _run_picker(["python3", "-c", script], "Folder selection was cancelled.")
