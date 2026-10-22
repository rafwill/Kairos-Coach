.PHONY: setup setup-win login serve test lint lint-mcp health-mcp docs-mcp

VENV := .venv
ifeq ($(OS),Windows_NT)
	VENV_PY := $(VENV)/Scripts/python.exe
else
	VENV_PY := $(VENV)/bin/python
endif

setup:
	bash ./setup.sh

setup-win:
	powershell -ExecutionPolicy Bypass -File ./setup.ps1

login:
	$(VENV_PY) -m agent.main

serve:
	$(VENV_PY) -m agent.main

test:
	$(VENV_PY) -m pytest -q

lint:
	$(VENV_PY) -m ruff check agent garmin_mcp tests tools

lint-mcp:
	$(VENV_PY) tools/lint_mcp.py

health-mcp:
	$(VENV_PY) tools/healthcheck_mcp.py

docs-mcp:
	$(VENV_PY) tools/gen_mcp_docs.py
