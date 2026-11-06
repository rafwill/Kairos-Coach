"""Prueba de concepto de la web (fase 0, docs/web.md): ¿Garmin deja trabajar a Kairos desde un servidor en la nube?

Uso (en la máquina virtual, con GARMIN_EMAIL/GARMIN_PASSWORD en .env):
    python tools/poc_garmin_cloud.py --primer-login   # una vez, en una terminal: login con MFA si Garmin lo pide
    python tools/poc_garmin_cloud.py                  # una pasada (lo lanza cron cada 3 h)
    python tools/poc_garmin_cloud.py --informe        # resumen de todas las pasadas y veredicto

- Cada pasada arranca el servidor MCP como Kairos (python -m garmin_mcp por stdio) y hace las 7 llamadas del
  arranque diario (Body Battery, HRV y sueño de hoy y de ayer, actividades de 48 h): lo que haría la web cada día.
  No llama a las 44 tools (eso es tools/healthcheck_mcp.py) para no provocar el 429 que se quiere medir.
- Clasifica cada llamada con `classify` de healthcheck_mcp (OK, VACÍA, ERROR, ENTORNO = 429 o autenticación) y
  apunta si los tokens de Garmin se reutilizaron o hubo un login nuevo (fecha del fichero de tokens).
- Guarda una línea por pasada en .tmp/poc_garmin/pasadas.jsonl (datos reales: ignorado por git).
- Criterio de éxito fijado antes de ver resultados (docs/web.md, fase 0): ver `verdict`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", encoding="utf-8")

from tools.healthcheck_mcp import classify  # noqa: E402

LOG = ROOT / ".tmp" / "poc_garmin" / "pasadas.jsonl"
MIN_HOURS = 72  # duración mínima de la prueba
MIN_RUNS = 20  # pasadas mínimas (cada 3 h: 8 al día)
MIN_OK_SHARE = 0.95  # pasadas sin ningún fallo de entorno ni de la tool


def startup_calls(today: date) -> list[tuple[str, dict]]:
    """Las llamadas a Garmin del arranque de Kairos (StartupMixin.collect_startup_snapshot_48h)."""
    t, y = today.isoformat(), (today - timedelta(days=1)).isoformat()
    return [
        ("get_body_battery", {"start_date": t, "end_date": t}),
        ("get_body_battery", {"start_date": y, "end_date": y}),
        ("get_hrv_data", {"date": t}),
        ("get_hrv_data", {"date": y}),
        ("get_sleep_summary", {"date": t}),
        ("get_sleep_summary", {"date": y}),
        ("get_activities_by_date", {"start_date": (today - timedelta(days=2)).isoformat(), "end_date": t,
                                    "page": 0, "page_size": 100}),
    ]


def token_file() -> Path | None:
    from garmin_mcp.server import _tokenstore_dir

    email = (os.environ.get("GARMIN_EMAIL") or "").strip()
    files = sorted(_tokenstore_dir(email).glob("*.json")) if email else []
    return files[0] if files else None


async def one_pass(pause_s: float) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    before = token_file()
    before_mtime = before.stat().st_mtime if before else None
    params = StdioServerParameters(command=sys.executable, args=["-m", "garmin_mcp"], cwd=str(ROOT),
                                   env=dict(os.environ))
    calls = []
    t_start = time.monotonic()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Los logs del servidor, a un fichero: con cron no deben ir a la salida.
        with (LOG.parent / "garmin_mcp.log").open("a", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for tool, args in startup_calls(date.today()):
                        t0 = time.monotonic()
                        res = await session.call_tool(tool, args)
                        text = "".join(getattr(c, "text", "") for c in res.content)
                        state = classify(bool(res.isError), text)
                        calls.append({"tool": tool, "args": args, "estado": state,
                                      "segundos": round(time.monotonic() - t0, 2), "bytes": len(text),
                                      "detalle": text[:160].replace("\n", " ")
                                      if state in ("ERROR", "ENTORNO") else ""})
                        await asyncio.sleep(pause_s)
        crash = ""
    except Exception as exc:  # la prueba registra cualquier fallo del arranque del servidor o del login
        crash = f"{type(exc).__name__}: {exc}"[:300]
    after = token_file()
    after_mtime = after.stat().st_mtime if after else None
    return {
        "cuando": datetime.now().astimezone().isoformat(timespec="seconds"),
        "maquina": socket.gethostname(),
        "segundos": round(time.monotonic() - t_start, 1),
        "tokens_antes": before_mtime is not None,
        "tokens_renovados": before_mtime is not None and after_mtime is not None and after_mtime != before_mtime,
        "login_nuevo": before_mtime is None and after_mtime is not None,
        "fallo": crash,
        "llamadas": calls,
    }


def pass_ok(p: dict) -> bool:
    return not p["fallo"] and bool(p["llamadas"]) and all(c["estado"] in ("OK", "VACÍA") for c in p["llamadas"])


def verdict(passes: list[dict]) -> tuple[str, list[str]]:
    """Criterio fijado antes de ver resultados: ≥ 72 h, ≥ 20 pasadas, ≥ 95 % sin fallos y, después del primer
    login, ningún login nuevo con contraseña (los tokens se reutilizan; renovarlos solos es normal)."""
    if not passes:
        return "SIN DATOS", ["No hay pasadas: lanza una con `python tools/poc_garmin_cloud.py`."]
    first = datetime.fromisoformat(passes[0]["cuando"])
    last = datetime.fromisoformat(passes[-1]["cuando"])
    hours = (last - first).total_seconds() / 3600
    ok = sum(1 for p in passes if pass_ok(p))
    relogins = sum(1 for p in passes[1:] if p["login_nuevo"])
    env = sum(1 for p in passes for c in p["llamadas"] if c["estado"] == "ENTORNO")
    notes = [f"{len(passes)} pasadas en {hours:.0f} h; {ok} sin fallos ({ok / len(passes):.0%}); "
             f"{env} llamadas con 429 o autenticación; {relogins} logins nuevos tras el primero."]
    if relogins or ok / len(passes) < MIN_OK_SHARE:
        return "NO VALE", notes + ["Garmin no deja trabajar de forma estable desde esta máquina: plan B (docs/web.md)."]
    if hours < MIN_HOURS or len(passes) < MIN_RUNS:
        return "EN CURSO", notes + [f"Faltan datos: al menos {MIN_HOURS} h y {MIN_RUNS} pasadas."]
    return "VALE", notes + ["Garmin funciona desde la nube con los tokens reutilizados: se puede seguir a la fase 1."]


def report() -> int:
    passes = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if LOG.exists() else []
    state, notes = verdict(passes)
    print(f"Veredicto: {state}")
    for n in notes:
        print(f"- {n}")
    for p in passes[-10:]:
        marks = "".join({"OK": "✅", "VACÍA": "⚪", "ERROR": "❌", "ENTORNO": "⚠️"}[c["estado"]] for c in p["llamadas"])
        extra = " login nuevo" if p["login_nuevo"] else (" tokens renovados" if p["tokens_renovados"] else "")
        print(f"  {p['cuando']}  {p['segundos']:5.1f}s  {marks or '—'}{extra}{'  ' + p['fallo'] if p['fallo'] else ''}")
    return {"VALE": 0, "EN CURSO": 0, "SIN DATOS": 0}.get(state, 1)


def first_login() -> int:
    """Login interactivo (con el código MFA si Garmin lo pide) que deja los tokens donde los busca el servidor."""
    from garminconnect import Garmin

    from garmin_mcp.server import _tokenstore_dir

    email, password = (os.environ.get(k, "").strip() for k in ("GARMIN_EMAIL", "GARMIN_PASSWORD"))
    if not (email and password):
        print("Faltan GARMIN_EMAIL/GARMIN_PASSWORD en .env")
        return 1
    client = Garmin(email=email, password=password, prompt_mfa=lambda: input("Código MFA de Garmin: ").strip())
    client.login(str(_tokenstore_dir(email)))
    print(f"Login correcto; tokens en {_tokenstore_dir(email)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--informe", action="store_true", help="resumen de las pasadas y veredicto")
    ap.add_argument("--primer-login", action="store_true", help="login interactivo (MFA) para guardar los tokens")
    ap.add_argument("--pausa", type=float, default=0.5, help="segundos entre llamadas")
    args = ap.parse_args()
    if args.informe:
        return report()
    if args.primer_login:
        return first_login()
    result = asyncio.run(one_pass(args.pausa))
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, ensure_ascii=False) + "\n")
    marks = "".join({"OK": "✅", "VACÍA": "⚪", "ERROR": "❌", "ENTORNO": "⚠️"}[c["estado"]] for c in result["llamadas"])
    print(f"{result['cuando']}  {result['segundos']}s  {marks or '—'}  {result['fallo']}")
    return 0 if pass_ok(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
