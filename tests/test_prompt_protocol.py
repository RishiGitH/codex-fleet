from codex_fleet.models import WorkItem
from codex_fleet.prompt_protocol import build_runner_prompt


def test_runner_prompt_keeps_codex_fleet_protocol_bounded() -> None:
    item = WorkItem(id="1", identifier="CF-1", title="Do work", description="Make the change.", state="Ready")

    prompt = build_runner_prompt(item, max_protocol_tokens=80)

    assert "Work item CF-1" in prompt
    assert "Description:" in prompt
    assert "codex-fleet" in prompt
    assert len(prompt) < 1200
