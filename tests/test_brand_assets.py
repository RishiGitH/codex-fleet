from pathlib import Path


def test_codex_fleet_logo_asset_exists() -> None:
    logo = Path("assets/brand/codex-fleet-logo.svg")

    assert logo.exists()
    assert "<svg" in logo.read_text()
    assert "codex-fleet logo" in logo.read_text()
