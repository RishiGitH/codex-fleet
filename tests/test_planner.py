import json

import pytest

from codex_fleet.planner import PlannerContractError, parse_planner_output
from codex_fleet.runner import parse_planner_contract_failure, parse_planner_tasks


def test_parse_planner_output_accepts_canonical_contract() -> None:
    parsed = parse_planner_output(
        json.dumps(
            {
                "summary": "Split the work into implementation and review.",
                "tasks": [
                    {
                        "title": "Implement settings endpoint",
                        "description": "Add the endpoint and tests.",
                        "role": "implementer",
                        "priority": "high",
                        "depends_on": [],
                        "workflow_mode": "execute_only",
                    }
                ],
                "reviewers": ["harness_reviewer"],
            }
        )
    )

    assert parsed.summary.startswith("Split")
    assert parsed.tasks[0].role == "implementer"
    assert parsed.reviewers == ("quality_reviewer",)


def test_parse_planner_output_defaults_missing_child_workflow_mode() -> None:
    parsed = parse_planner_output(
        json.dumps(
            {
                "summary": "Split the work into implementation and review.",
                "tasks": [
                    {
                        "title": "Implement settings endpoint",
                        "description": "Add the endpoint and tests.",
                        "role": "implementer",
                        "priority": "high",
                        "depends_on": [],
                    }
                ],
                "reviewers": [],
            }
        )
    )

    assert parsed.tasks[0].workflow_mode == "execute_only"


def test_parse_planner_output_rejects_invalid_role() -> None:
    with pytest.raises(PlannerContractError, match="Unsupported planner task role"):
        parse_planner_output(
            json.dumps(
                {
                    "summary": "bad",
                    "tasks": [
                        {
                            "title": "Do everything",
                            "description": "One-shot the project.",
                            "role": "orchestrator",
                            "priority": "high",
                            "depends_on": [],
                            "workflow_mode": "execute_only",
                        }
                    ],
                    "reviewers": [],
                }
            )
        )


def test_runner_maps_planner_output_to_plane_child_tasks() -> None:
    output = """```codex-fleet-planner-output
{"summary":"plan","tasks":[{"title":"Scout files","description":"Map relevant files.","role":"code_scout","priority":"medium","depends_on":[],"workflow_mode":"execute_only"}],"reviewers":["harness_reviewer"]}
```"""

    tasks = parse_planner_tasks(output)

    assert [task.role for task in tasks] == ["code_scout", "quality_reviewer"]
    assert parse_planner_contract_failure(output) is None


def test_planner_contract_failure_when_required_output_missing() -> None:
    failure = parse_planner_contract_failure("I will plan this in prose.", require_output=True)

    assert failure is not None
    assert "missing codex-fleet-planner-output" in failure.question


def test_planner_contract_failure_when_required_tasks_empty() -> None:
    output = """```codex-fleet-planner-output
{"summary":"plan","tasks":[],"reviewers":[]}
```"""

    failure = parse_planner_contract_failure(output, require_output=True)

    assert failure is not None
    assert "at least one child task" in failure.question


def test_runner_recovers_unfenced_planner_json() -> None:
    output = (
        '{"summary":"plan","tasks":[{"title":"Build UI","description":"Implement the landing page.",'
        '"role":"implementer","priority":"high","depends_on":[],"workflow_mode":"execute_only"}],"reviewers":[]}'
    )

    tasks = parse_planner_tasks(output)

    assert [task.title for task in tasks] == ["Build UI", "Quality review implementation", "Test implementation and record proof"]


def test_parse_planner_output_normalizes_common_app_server_shape() -> None:
    parsed = parse_planner_output(
        json.dumps(
            {
                "summary": "Build the landing page.",
                "tasks": [
                    {
                        "title": "Audit app",
                        "role": "planner",
                        "instructions": "Inspect structure before implementation.",
                        "dependencies": [],
                        "acceptance_criteria": ["Route identified."],
                        "reviewers": ["frontend-reviewer"],
                    },
                    {
                        "title": "Implement page",
                        "role": "frontend",
                        "instructions": "Build the landing page.",
                        "dependencies": ["Audit app"],
                    },
                    {
                        "title": "Verify page",
                        "role": "qa",
                        "instructions": "Run checks and inspect layout.",
                        "dependencies": ["Implement page"],
                    },
                ],
                "reviewers": [
                    {"name": "frontend-reviewer", "role": "reviewer"},
                    {"name": "security-reviewer", "role": "security-reviewer"},
                ],
            }
        )
    )

    assert [task.role for task in parsed.tasks] == ["code_scout", "implementer", "test_reviewer"]
    assert parsed.tasks[0].description.startswith("Inspect structure")
    assert parsed.tasks[0].depends_on == ()
    assert parsed.tasks[1].depends_on == ("Audit app",)
    assert parsed.reviewers == ("quality_reviewer", "security_reviewer")
