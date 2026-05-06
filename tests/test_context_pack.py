import json
from pathlib import Path

from codex_fleet.context_pack import write_context_pack


def test_context_pack_excludes_generated_and_private_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n")
    for dirname in [".git", ".venv", ".codex-fleet", ".mypy_cache", ".ruff_cache"]:
        (tmp_path / dirname).mkdir()
        (tmp_path / dirname / "secret.txt").write_text("do not include\n")

    out_dir = tmp_path / ".codex-fleet" / "context"
    result = write_context_pack(tmp_path, out_dir)
    tree = (out_dir / "tree.md").read_text()
    metadata = json.loads((out_dir / "metadata.json").read_text())

    assert result.file_count >= 2
    assert "src/app.py" in tree
    assert ".git/secret.txt" not in tree
    assert ".venv/secret.txt" not in tree
    assert ".codex-fleet/secret.txt" not in tree
    assert ".mypy_cache/secret.txt" not in tree
    assert ".ruff_cache/secret.txt" not in tree
    assert ".git" in metadata["exclusions"]
    assert ".mypy_cache" in metadata["exclusions"]
    assert metadata["estimated_tokens"] > 0
