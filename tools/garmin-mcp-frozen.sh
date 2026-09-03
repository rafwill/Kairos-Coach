#!/usr/bin/env bash
set -euo pipefail

# Frozen MCP launcher (Unix): arranca el MCP propio de Kairos.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PY="$PROJECT_ROOT/.venv/bin/python"

if [ -x "$VENV_PY" ]; then
  exec "$VENV_PY" -m agent.kairos_mcp_server "$@"
fi

if ! command -v python >/dev/null 2>&1; then
  echo "[garmin-mcp-frozen] ERROR: 'python' no esta en PATH." >&2
  echo "Instala dependencias con: python -m pip install -r requirements.txt" >&2
  exit 1
fi

exec python -m agent.kairos_mcp_server "$@"
