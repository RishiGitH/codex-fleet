.PHONY: install test lint doctor budget bootstrap smoke local-check full-local-check up demo down plane-up plane-status plane-bootstrap plane-source plane-verify plane-fork-preview plane-fork-prepare plane-fork-clone docker-up docker-down

SYSTEM_PYTHON ?= python3
PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
RUFF ?= .venv/bin/ruff

.venv/bin/python:
	$(SYSTEM_PYTHON) -m venv .venv

.venv/.installed: pyproject.toml .venv/bin/python
	$(PYTHON) -m pip install -e '.[dev]'
	touch .venv/.installed

install: .venv/.installed

lint: install
	$(RUFF) check .

test: install
	$(PYTEST)

doctor: install
	$(PYTHON) -m codex_fleet doctor --repo .

budget: install
	$(PYTHON) -m codex_fleet budget --repo .

bootstrap: install
	$(PYTHON) -m codex_fleet bootstrap --repo .

smoke: install
	$(PYTEST) tests/test_daemon.py tests/test_orchestrator.py

up: install
	PYTHONUNBUFFERED=1 $(PYTHON) -m codex_fleet up --repo . --verbose

demo: install
	PYTHONUNBUFFERED=1 $(PYTHON) -m codex_fleet up --repo . --fake --verbose

down: install
	$(PYTHON) -m codex_fleet down --repo .

plane-up: install
	$(PYTHON) -m codex_fleet plane-up --repo .

plane-status: install
	$(PYTHON) -m codex_fleet plane-status --repo .

plane-bootstrap: install
	$(PYTHON) -m codex_fleet plane-bootstrap --repo .

plane-source: install
	$(PYTHON) -m codex_fleet plane-source --repo .

plane-verify: install
	$(PYTHON) -m codex_fleet plane-verify --repo .

plane-fork-preview: install
	$(PYTHON) -m codex_fleet plane-fork-preview --repo .

plane-fork-prepare: install
	$(PYTHON) -m codex_fleet plane-fork-preview --repo . --prepare-only

plane-fork-clone:
	scripts/plane-fork-clone

local-check: install lint test doctor budget

full-local-check: local-check smoke

docker-up:
	docker compose -f docker/compose.yml up --build

docker-down:
	docker compose -f docker/compose.yml down
