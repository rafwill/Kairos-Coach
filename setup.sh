#!/usr/bin/env bash
set -euo pipefail

SKIP_DEV="${SKIP_DEV:-0}"
VENV_PATH=".venv"

resolve_python() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return
  fi
  echo "No se encontró Python en PATH. Instala Python 3.12+ e inténtalo de nuevo." >&2
  exit 1
}

PYTHON_BIN="$(resolve_python)"
VENV_PY="$VENV_PATH/bin/python"

echo "[setup] Creando entorno virtual en $VENV_PATH..."
"$PYTHON_BIN" -m venv "$VENV_PATH"

echo "[setup] Actualizando pip..."
"$VENV_PY" -m pip install --upgrade pip

echo "[setup] Instalando dependencias runtime..."
"$VENV_PY" -m pip install -r requirements.txt

echo "[setup] Instalando Kairos en modo editable (agent y garmin_mcp importables desde cualquier carpeta)..."
"$VENV_PY" -m pip install -e . --no-deps

if [[ -d .git ]]; then
  echo "[setup] Activando el pre-commit del repo (.githooks: ruff y tests antes de cada commit)..."
  git config core.hooksPath .githooks
fi

if [[ "$SKIP_DEV" != "1" ]]; then
  echo "[setup] Instalando dependencias de desarrollo..."
  "$VENV_PY" -m pip install -r requirements-dev.txt
fi

if [[ ! -f .env ]]; then
  echo "[setup] Generando .env desde .env.example..."
  cp .env.example .env
fi

echo
echo "[setup] OK. Siguientes pasos:"
echo "  1) En .env, solo SUPABASE_URL y SUPABASE_ANON_KEY (te los da quien administra Kairos)"
echo "  2) Al entrar, Kairos te pide tu cuenta, Garmin y tu proveedor de IA y los guarda cifrados en Supabase Vault"
echo "  3) Ejecuta: .venv/bin/python -m agent.main"
