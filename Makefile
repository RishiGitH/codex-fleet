.PHONY: install test lint doctor budget bootstrap smoke local-check full-local-check up demo down plane-up plane-status plane-bootstrap plane-source plane-verify plane-fork-preview plane-fork-prepare plane-fork-clone docker-up docker-down

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PYTEST ?= $(if $(wildcard .venv/bin/pytest),.venv/bin/pytest,pytest)
RUFF ?= $(if $(wildcard .venv/bin/ruff),.venv/bin/ruff,ruff)

install:
	$(PYTHON) -c "import codex_fleet" || $(PYTHON) -m pip install -e '.[dev]'

lint:
	$(RUFF) check .

test:
	$(PYTEST)

doctor:
	$(PYTHON) -m codex_fleet doctor --repo .

budget:
	$(PYTHON) -m codex_fleet budget --repo .

bootstrap:
	$(PYTHON) -m codex_fleet bootstrap --repo .

smoke:
	$(PYTEST) tests/test_daemon.py tests/test_orchestrator.py

up: install
	PYTHONUNBUFFERED=1 $(PYTHON) -m codex_fleet up --repo . --verbose

demo: install
	PYTHONUNBUFFERED=1 $(PYTHON) -m codex_fleet up --repo . --fake --verbose

down:
	$(PYTHON) -m codex_fleet down --repo .

plane-up:
	$(PYTHON) -m codex_fleet plane-up --repo .

plane-status:
	$(PYTHON) -m codex_fleet plane-status --repo .

plane-bootstrap:
	$(PYTHON) -m codex_fleet plane-bootstrap --repo .

plane-source:
	$(PYTHON) -m codex_fleet plane-source --repo .

plane-verify:
	$(PYTHON) -m codex_fleet plane-verify --repo .

plane-fork-preview:
	$(PYTHON) -m codex_fleet plane-fork-preview --repo .

plane-fork-prepare:
	$(PYTHON) -m codex_fleet plane-fork-preview --repo . --prepare-only

plane-fork-clone:
	scripts/plane-fork-clone

local-check: install lint test doctor budget

full-local-check: local-check smoke

docker-up:
	docker compose -f docker/compose.yml up --build

docker-down:
	docker compose -f docker/compose.yml down
