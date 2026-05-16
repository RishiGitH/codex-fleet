from __future__ import annotations

from codex_fleet.budget import estimate_tokens
from codex_fleet.models import WorkItem


def build_runner_prompt(
    item: WorkItem,
    *,
    max_protocol_tokens: int = 800,
    skill_policy: str = "minimal",
) -> str:
    description = item.description or "No description provided."
    protocol = (
        "You are running inside codex-fleet. Do not call Plane directly; the daemon parses your final answer "
        "and updates Plane. Keep Plane-facing summaries concise and do not dump raw logs, secrets, or large diffs.\n\n"
        "Make the smallest safe change, run relevant tests, and summarize changed files plus verification.\n\n"
        "If you are blocked on user input, include exactly one fenced block named `codex-fleet-needs-input` "
        "containing JSON with `question`, optional `needed_to_continue`, and optional `suggested_state`.\n\n"
        "If separate follow-up work should become Plane tasks, include one fenced block named "
        "`codex-fleet-proposed-tasks` containing a JSON array of objects with `title`, optional `description`, "
        "optional `role`, optional `depends_on`, optional `suggested_state`, and optional `labels`. Do not include secrets."
    )
    if skill_policy != "minimal":
        protocol += (
            "\n\nUse repo-local skills only when their trigger matches the task. Prefer targeted file reads over broad context dumps."
        )
    protocol = _cap_protocol(protocol, max_protocol_tokens)
    return (
        f"Work item {item.identifier}: {item.title}\n\n"
        f"Description:\n{description}\n\n"
        f"{protocol}"
    )


def _cap_protocol(protocol: str, max_protocol_tokens: int) -> str:
    if max_protocol_tokens <= 0 or estimate_tokens(protocol) <= max_protocol_tokens:
        return protocol
    lines: list[str] = []
    for line in protocol.splitlines():
        candidate = "\n".join([*lines, line]).strip()
        if estimate_tokens(candidate) > max_protocol_tokens:
            break
        lines.append(line)
    return "\n".join(lines).strip()
