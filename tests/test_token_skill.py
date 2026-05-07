from pathlib import Path

import yaml


def test_token_budget_review_skill_has_valid_frontmatter() -> None:
    path = Path(".agents/skills/token-budget-review/SKILL.md")
    text = path.read_text()
    assert text.startswith("---\n")
    frontmatter = text.split("---", 2)[1]
    data = yaml.safe_load(frontmatter)

    assert data["name"] == "token-budget-review"
    description = data["description"]
    assert "AGENTS.md" in description
    assert "WORKFLOW.md" in description
    assert "skills" in description
    assert "runner logs/events" in description
    assert "context packing" in description
