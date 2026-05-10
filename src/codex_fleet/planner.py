from __future__ import annotations

from dataclasses import dataclass
from json import loads
from typing import Any

PLANNER_TASK_ROLES = frozenset({"code_scout", "implementer", "quality_reviewer", "security_reviewer", "test_reviewer"})
PLANNER_PRIORITIES = frozenset({"high", "medium", "low"})
REVIEWER_ROLES = frozenset({"quality_reviewer", "security_reviewer", "test_reviewer"})


class PlannerContractError(ValueError):
    pass


@dataclass(frozen=True)
class PlannerTask:
    title: str
    description: str
    role: str
    priority: str
    depends_on: tuple[str, ...]
    workflow_mode: str


@dataclass(frozen=True)
class PlannerOutput:
    summary: str
    tasks: tuple[PlannerTask, ...]
    reviewers: tuple[str, ...]


def parse_planner_output(text: str) -> PlannerOutput:
    try:
        raw = loads(text)
    except ValueError as exc:
        raise PlannerContractError("Planner output must be a single JSON object.") from exc
    if not isinstance(raw, dict):
        raise PlannerContractError("Planner output must be a JSON object.")
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise PlannerContractError("Planner output needs a non-empty summary.")
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, list):
        raise PlannerContractError("Planner output needs a tasks array.")
    if not raw_tasks:
        raise PlannerContractError("Planner output needs at least one child task.")
    tasks = tuple(_planner_task(entry) for entry in raw_tasks)
    raw_reviewers = raw.get("reviewers", [])
    if not isinstance(raw_reviewers, list):
        raise PlannerContractError("Planner reviewers must be an array.")
    reviewers = tuple(_reviewer(value) for value in raw_reviewers)
    return PlannerOutput(summary=summary.strip(), tasks=tasks, reviewers=reviewers)


def _planner_task(raw: Any) -> PlannerTask:
    if not isinstance(raw, dict):
        raise PlannerContractError("Each planner task must be an object.")
    title = _required_text(raw, "title")
    description = _planner_description(raw)
    role = _normalize_planner_role(_required_text(raw, "role"))
    if role not in PLANNER_TASK_ROLES:
        raise PlannerContractError(f"Unsupported planner task role: {role}")
    priority = _optional_text(raw, "priority") or "medium"
    if priority not in PLANNER_PRIORITIES:
        raise PlannerContractError(f"Unsupported planner task priority: {priority}")
    depends_on_raw = raw.get("depends_on", raw.get("dependencies", []))
    if not isinstance(depends_on_raw, list):
        raise PlannerContractError("Planner task depends_on must be an array.")
    depends_on = tuple(str(item).strip() for item in depends_on_raw if isinstance(item, str) and item.strip())
    workflow_mode = _optional_text(raw, "workflow_mode") or "execute_only"
    if workflow_mode != "execute_only":
        raise PlannerContractError("Planner child tasks must use workflow_mode execute_only.")
    return PlannerTask(
        title=title,
        description=description,
        role=role,
        priority=priority,
        depends_on=depends_on,
        workflow_mode=workflow_mode,
    )


def _reviewer(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("role") or value.get("name")
    if not isinstance(value, str) or not value.strip():
        raise PlannerContractError("Planner reviewers must be non-empty strings.")
    reviewer = _normalize_reviewer_role(value.strip())
    if reviewer not in REVIEWER_ROLES:
        raise PlannerContractError(f"Unsupported reviewer: {reviewer}")
    return reviewer


def _planner_description(raw: dict[str, Any]) -> str:
    description = _optional_text(raw, "description")
    if description:
        return description
    instructions = _optional_text(raw, "instructions")
    criteria = raw.get("acceptance_criteria")
    parts: list[str] = []
    if instructions:
        parts.append(instructions)
    if isinstance(criteria, list):
        clean = [str(item).strip() for item in criteria if str(item).strip()]
        if clean:
            parts.append("Acceptance criteria:\n" + "\n".join(f"- {item}" for item in clean))
    description = "\n\n".join(parts).strip()
    if not description:
        raise PlannerContractError("Planner task needs a non-empty description.")
    return description


def _normalize_planner_role(role: str) -> str:
    normalized = role.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "planner": "code_scout",
        "scout": "code_scout",
        "researcher": "code_scout",
        "auditor": "code_scout",
        "frontend": "implementer",
        "frontend_engineer": "implementer",
        "ui": "implementer",
        "designer": "implementer",
        "design": "implementer",
        "content": "implementer",
        "copywriter": "implementer",
        "qa": "test_reviewer",
        "tester": "test_reviewer",
        "test_agent": "test_reviewer",
        "reviewer": "quality_reviewer",
        "harness_reviewer": "quality_reviewer",
        "token_reviewer": "quality_reviewer",
        "frontend_reviewer": "quality_reviewer",
        "design_reviewer": "quality_reviewer",
        "product_reviewer": "quality_reviewer",
        "accessibility_reviewer": "quality_reviewer",
        "qa_reviewer": "test_reviewer",
    }
    return aliases.get(normalized, normalized)


def _normalize_reviewer_role(reviewer: str) -> str:
    normalized = reviewer.strip().lower().replace("-", "_").replace(" ", "_")
    if "security" in normalized:
        return "security_reviewer"
    if "test" in normalized or normalized in {"qa", "tester"}:
        return "test_reviewer"
    if "token" in normalized or "context" in normalized or "harness" in normalized or "quality" in normalized:
        return "quality_reviewer"
    if normalized in REVIEWER_ROLES:
        return normalized
    if normalized.endswith("_reviewer") or normalized == "reviewer":
        return "quality_reviewer"
    return normalized


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PlannerContractError(f"Planner task needs a non-empty {key}.")
    return value.strip()


def _optional_text(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()
