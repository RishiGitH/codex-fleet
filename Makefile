.PHONY: install test lint doctor budget bootstrap smoke local-check full-local-check up plane-up docker-up docker-down

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

bootstrap:
	python -m codex_fleet bootstrap --repo .

smoke:
	python -m codex_fleet up --repo . --fake --once

up: install smoke

plane-up:
	bash scripts/plane-up

local-check: install lint test doctor budget

full-local-check: local-check smoke

docker-up:
	docker compose -f docker/compose.yml up --build

docker-down:
	docker compose -f docker/compose.yml down
