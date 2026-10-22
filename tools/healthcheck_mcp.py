"""Healthcheck en vivo del MCP de Kairos: llama a todas las tools contra Garmin y dice cuáles funcionan.

Uso:
    python tools/healthcheck_mcp.py [--pausa 0.3]

- Arranca agent.kairos_mcp_server por stdio (como Kairos), con GARMIN_EMAIL/GARMIN_PASSWORD del .env.
- Llama a cada tool con argumentos normales (ayer, las dos últimas semanas, la actividad más reciente).
  Todas son de solo lectura.
- Clasifica cada tool: OK (con datos), VACÍA (sin datos: aviso, puede ser normal), ERROR (fallo de la tool)
  o ENTORNO (Garmin bloquea con 429 o la autenticación falla: no es un fallo de Kairos).
- Complementa al test de contrato sin red (tests/test_kairos_mcp_server.py): ese detecta que llamamos mal a
  garminconnect; este, que Garmin ha cambiado algo por su lado. Lanzarlo al actualizar garminconnect, antes de
  una batería o si algo parece raro. No en el arranque de Kairos (41 llamadas a Garmin).
- Guarda el informe en .tmp/healthcheck/ (datos reales: ignorado por git). Sale con 1 si alguna tool falla,
  2 si solo hay fallos de entorno, 0 si todo va bien.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", encoding="utf-8")

ENV_ERROR = re.compile(r"(?i)\b(429|too many requests|rate limit|401|403|unauthori[sz]ed|authentication|forbidden)\b")
EMPTY_TEXTS = {"", "null", "none", "[]", "{}"}


def classify(is_error: bool, text: str) -> str:
    """OK / VACÍA / ERROR / ENTORNO a partir de la respuesta de una tool."""
    if is_error:
        return "ENTORNO" if ENV_ERROR.search(text or "") else "ERROR"
    stripped = (text or "").strip()
    if stripped.lower() in EMPTY_TEXTS:
        return "VACÍA"
    try:
        parsed = json.loads(stripped)
    except ValueError:
        return "OK"
    if parsed in (None, [], {}) or (isinstance(parsed, dict) and all(v in (None, [], {}, "") for v in parsed.values())):
        return "VACÍA"
    return "OK"


def build_args(tool, activity_id: int | None) -> dict:
    props = tool.inputSchema.get("properties") or {}
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    two_weeks = (date.today() - timedelta(days=14)).isoformat()
    args: dict = {}
    if "activity_id" in props and activity_id is not None:
        args["activity_id"] = activity_id
    if "start_date" in props:
        args.update({"start_date": two_weeks, "end_date": yesterday})
    elif "date" in props:
        args["date"] = yesterday
    if tool.name == "get_activities":
        args = {"start": 0, "limit": 3}
    return args


async def run(pause_s: float) -> list[dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "agent.kairos_mcp_server"], cwd=str(ROOT), env=dict(os.environ)
    )
    results: list[dict] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            latest = await session.call_tool("get_activities", {"start": 0, "limit": 1})
            activity_id = None
            try:
                first = json.loads("".join(getattr(c, "text", "") for c in latest.content))
                first = first[0] if isinstance(first, list) else first
                activity_id = int(first.get("activityId"))
            except (ValueError, TypeError, AttributeError, IndexError):
                pass
            for tool in tools:
                args = build_args(tool, activity_id)
                t0 = time.monotonic()
                res = await session.call_tool(tool.name, args)
                elapsed = time.monotonic() - t0
                text = "".join(getattr(c, "text", "") for c in res.content)
                status = classify(bool(res.isError), text)
                results.append({
                    "tool": tool.name, "estado": status, "segundos": round(elapsed, 2), "bytes": len(text),
                    "args": args, "detalle": text[:200].replace("\n", " ") if status != "OK" else "",
                })
                mark = {"OK": "✅", "VACÍA": "⚪", "ERROR": "❌", "ENTORNO": "⚠️"}[status]
                print(f"{mark} {tool.name:34s} {elapsed:5.1f}s  {len(text):7d} bytes" + (f"  → {results[-1]['detalle'][:90]}" if status != "OK" else ""), flush=True)
                await asyncio.sleep(pause_s)
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pausa", type=float, default=0.3, help="segundos entre llamadas (para no provocar 429)")
    args = ap.parse_args()
    if not (os.environ.get("GARMIN_EMAIL") and os.environ.get("GARMIN_PASSWORD")):
        ap.error("faltan GARMIN_EMAIL/GARMIN_PASSWORD en .env")

    results = asyncio.run(run(args.pausa))
    counts = {k: sum(1 for r in results if r["estado"] == k) for k in ("OK", "VACÍA", "ERROR", "ENTORNO")}
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    out = ROOT / ".tmp" / "healthcheck" / f"mcp_{stamp}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps({"resumen": counts, "tools": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# Healthcheck MCP — {stamp}", "", " · ".join(f"{k}: {v}" for k, v in counts.items()), "",
             "| Tool | Estado | s | bytes | Detalle |", "|---|---|---:|---:|---|"]
    lines += [f"| {r['tool']} | {r['estado']} | {r['segundos']} | {r['bytes']} | {r['detalle'] or '—'} |" for r in results]
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{len(results)} tools · " + " · ".join(f"{k} {v}" for k, v in counts.items()) + f" · informe: {out.with_suffix('.md').relative_to(ROOT)}")
    if counts["ERROR"]:
        return 1
    return 2 if counts["ENTORNO"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
