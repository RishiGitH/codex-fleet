.PHONY: install test lint doctor budget smoke local-check full-local-check up docker-up docker-down

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
	python -m codex_fleet up --repo . --fake --once

up: install smoke

local-check: install lint test doctor budget

full-local-check: local-check smoke

docker-up:
	docker compose -f docker/compose.yml up --build

docker-down:
	docker compose -f docker/compose.yml down
