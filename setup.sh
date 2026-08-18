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
  echo "No se encontró Python en PATH. Instala Python 3.10+ e inténtalo de nuevo." >&2
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

if [[ "$SKIP_DEV" != "1" ]]; then
  echo "[setup] Instalando dependencias de desarrollo..."
  "$VENV_PY" -m pip install -r requirements-dev.txt
fi

if [[ ! -f .env ]]; then
  echo "[setup] Generando .env desde .env.example..."
  cp .env.example .env
fi

if ! grep -q '^ENCRYPTION_KEY=' .env; then
  echo "[setup] Generando ENCRYPTION_KEY y añadiéndola a .env..."
  KEY="$($VENV_PY -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  {
    echo
    echo "# Clave de cifrado local (generada automáticamente)"
    echo "ENCRYPTION_KEY=$KEY"
  } >> .env
fi

echo
echo "[setup] OK. Siguientes pasos:"
echo "  1) Completa GARMIN_EMAIL/GARMIN_PASSWORD y claves LLM en .env"
echo "  2) Configura SUPABASE_URL y SUPABASE_ANON_KEY"
echo "  3) Ejecuta: .venv/bin/python -m agent.main"
