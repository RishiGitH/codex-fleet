.PHONY: install test lint doctor budget smoke local-check

install:
	python -m pip install -e '.[dev]'

lint:
	ruff check .

test:
	pytest

doctor:
	python -m codex_fleet doctor --repo .

budget:
	python -m codex_fleet budget --repo .

smoke:
	python -m codex_fleet run-configured --repo . --fake

local-check: install lint test doctor budget
