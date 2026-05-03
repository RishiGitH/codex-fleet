from pathlib import Path


def file_size(path: Path) -> int:
    if not path.exists():
        return 0
    return path.stat().st_size
