.PHONY: setup setup-win serve test lint lint-informe hooks lint-mcp health-mcp docs-mcp check-rls

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

serve:
	$(VENV_PY) -m agent.main

test:
	$(VENV_PY) -m pytest -q

lint:
	$(VENV_PY) -m ruff check agent garmin_mcp tests tools

# Informe de estilo (no bloquea): familias fuera del lint obligatorio de pyproject.toml.
lint-informe:
	$(VENV_PY) -m ruff check agent garmin_mcp tests tools --select E,W,B,I,UP,SIM,DTZ,RUF --statistics --exit-zero

# Pre-commit versionado (.githooks/pre-commit): ruff y tests antes de cada commit.
hooks:
	git config core.hooksPath .githooks

lint-mcp:
	$(VENV_PY) tools/lint_mcp.py

health-mcp:
	$(VENV_PY) tools/healthcheck_mcp.py

docs-mcp:
	$(VENV_PY) tools/gen_mcp_docs.py

# Seguridad por fila de Supabase: qué ve la clave anon sin sesión y qué ve tu sesión (supabase/README.md).
check-rls:
	$(VENV_PY) tools/check_rls.py
