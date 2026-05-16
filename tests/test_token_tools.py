from codex_fleet.token_tools import (
    capabilities_payload,
    native_compress_output,
    tool_commands_from_config,
)


class DummyConfig:
    rtk_command = "definitely-missing-rtk"
    caveman_command = "definitely-missing-caveman"
    repomix_command = "definitely-missing-repomix"
    graphify_command = "definitely-missing-graphify"


def test_capabilities_payload_reports_missing_optional_tools() -> None:
    payload = capabilities_payload(tool_commands_from_config(DummyConfig()))

    assert payload["rtk"]["available"] is False
    assert "optional" in str(payload["rtk"]["recommendation"])


def test_native_compress_output_keeps_errors_and_edges() -> None:
    raw = "\n".join(["start", *[f"progress {index}" for index in range(200)], "Traceback: boom", "end"])

    compressed = native_compress_output(raw, max_lines=20)

    assert "start" in compressed
    assert "Traceback: boom" in compressed
    assert "end" in compressed
    assert len(compressed.splitlines()) <= 21
