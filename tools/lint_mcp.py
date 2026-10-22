"""Pasa la guía de estilo MCP (apiaddicts mcps-style-guide) sobre las tools del MCP de Kairos.

Uso:
    python tools/lint_mcp.py

- Arranca el servidor MCP (python -m garmin_mcp) por stdio (como lo hace Kairos), pide tools/list y lo guarda en
  .tmp/mcp-tools.json. No hace login en Garmin.
- Ejecuta Spectral (npx, Node 18+) con tools/mcp_lint/.spectral.yaml: el ruleset de apiaddicts anclado
  a un commit, con las dos reglas no adoptadas desactivadas y su motivo.
- Sale con el código de Spectral: 0 si no hay errores ni avisos.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULESET = ROOT / "tools" / "mcp_lint" / ".spectral.yaml"
OUT = ROOT / ".tmp" / "mcp-tools.json"
SPECTRAL = "@stoplight/spectral-cli@6.17.0"


async def dump_tools_list(out: Path) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "garmin_mcp"], cwd=str(ROOT), env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
    data = json.loads(result.model_dump_json(by_alias=True, exclude_none=False))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"tools": data["tools"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(data["tools"])


def main() -> int:
    sys.path.insert(0, str(ROOT))
    n = asyncio.run(dump_tools_list(OUT))
    print(f"tools/list: {n} tools → {OUT.relative_to(ROOT)}")
    npx = shutil.which("npx")
    if not npx:
        print("Falta npx (Node 18+) para ejecutar Spectral.")
        return 2
    cmd = [npx, "-y", SPECTRAL, "lint", "-r", str(RULESET), str(OUT), "-F", "warn"]
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


if __name__ == "__main__":
    raise SystemExit(main())
