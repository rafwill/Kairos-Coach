"""Reconstrucción única de la línea base de TSS (activity_tss + load_metrics_daily).

Ejecuta la ruta de producción compute_and_persist_load_metrics(rebuild_from=START) con
las escrituras interceptadas, valida el resultado y solo con --apply lo persiste.
A partir de la línea base, compute_load nunca recalcula TSS hacia atrás.

Uso:
    python -m tools.rebuild_tss_baseline --username USER --start 2026-02-22            # prueba, no escribe
    python -m tools.rebuild_tss_baseline --username USER --start 2026-02-22 --apply    # escribe

Validación (bloquea --apply):
  - get_activity_details devolvió error en alguna llamada.
  - Alguna sesión de running no-trail no usó el pipeline con detalles.
  - La tabla activity_tss no existe (falta ejecutar supabase/schema.sql).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CALL_DELAY_SECONDS = 0.25
RETRY_BACKOFF_SECONDS = (0.0, 5.0, 20.0, 60.0)  # intento inicial + 3 reintentos ante error transitorio


def _is_error_text(raw) -> bool:
    if not isinstance(raw, str):
        return False
    text = raw.strip().lower()
    if text.startswith(("{", "[")):
        return False
    return text.startswith("error") or "429" in text[:200] or "too many requests" in text[:200]


async def _compute(username: str, start: str) -> dict:
    from agent import storage as st
    from agent import trainer_agent as ta
    from agent.mcp.client import garmin_mcp_session

    row = st.find_user_by_username(username)
    if not row:
        raise SystemExit(f"Usuario no encontrado: {username}")
    st.set_active_user(row.get("id"), username)

    series: list[dict] = []
    rows: list[dict] = []
    tool_counts: Counter = Counter()
    tool_errors: Counter = Counter()
    tool_retries: Counter = Counter()
    original_call_tool = ta.call_tool

    async def _throttled_call_tool(session, tool_name, args):
        raw = None
        for attempt, backoff in enumerate(RETRY_BACKOFF_SECONDS):
            await asyncio.sleep(CALL_DELAY_SECONDS + backoff)
            try:
                raw = await original_call_tool(session, tool_name, args)
            except (TimeoutError, OSError, RuntimeError) as exc:
                raw = f"Error: {exc}"
            tool_counts[tool_name] += 1
            if not _is_error_text(raw):
                return raw
            if attempt + 1 < len(RETRY_BACKOFF_SECONDS):
                tool_retries[tool_name] += 1
        tool_errors[tool_name] += 1
        return raw

    async with garmin_mcp_session() as session:
        agent = ta.TrainerAgent(mcp_session=session, provider="nvidia")
        agent.user_profile = ta._load_user_profile()
        with patch.object(ta, "call_tool", side_effect=_throttled_call_tool), \
             patch.object(ta._storage, "upsert_load_metrics_series", side_effect=lambda r: series.extend(r)), \
             patch.object(ta._storage, "upsert_activity_tss_rows", side_effect=lambda r: rows.extend(r)), \
             patch.object(ta, "_save_user_profile"), \
             patch.object(ta.TrainerAgent, "_apply_series_to_profile"):
            await agent.compute_and_persist_load_metrics(force_full_recalc=True, rebuild_from=start)
        profile = agent.user_profile

    return {
        "series": series,
        "rows": rows,
        "profile": profile,
        "tool_counts": dict(tool_counts),
        "tool_errors": dict(tool_errors),
        "tool_retries": dict(tool_retries),
    }


def _validate(result: dict) -> list[str]:
    problems = []
    if result["tool_errors"].get("get_activity_details"):
        problems.append(f"get_activity_details con error en {result['tool_errors']['get_activity_details']} llamadas")
    running_without_details = [
        r["activity_id"] for r in result["rows"]
        if str(r.get("model") or "").startswith("running:") and r.get("model") != "running:pipeline_fisico"
    ]
    if running_without_details:
        problems.append(f"{len(running_without_details)} sesiones de running sin pipeline con detalles: {running_without_details[:10]}")
    if not result["series"]:
        problems.append("serie vacía")
    return problems


def _activity_tss_table_exists() -> bool:
    from agent import storage as st

    sb = st._supabase()
    if not sb:
        return False
    try:
        sb.table("activity_tss").select("activity_id").limit(1).execute()
        return True
    except Exception as exc:  # postgrest APIError
        if st._is_missing_table_error(exc, "activity_tss"):
            return False
        raise


def _write_report(result: dict, out_dir: Path, start: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    rows_path = out_dir / f"linea_base_actividades_{start}_{stamp}.csv"
    cols = ["start_date", "activity_id", "sport", "tss", "method", "model", "tss_source", "native_load",
            "hr_rest_used", "lthr_used", "ftp_used", "threshold_pace_used", "walk_threshold_used"]
    with rows_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in sorted(result["rows"], key=lambda x: (x["start_date"], x["activity_id"])):
            w.writerow({c: r.get(c) for c in cols})
    series_path = out_dir / f"linea_base_serie_{start}_{stamp}.csv"
    with series_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "tss", "atl", "ctl", "tsb", "activities_count"])
        w.writeheader()
        w.writerows(result["series"])
    return rows_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconstruye la línea base de TSS (una sola vez).")
    parser.add_argument("--username", required=True)
    parser.add_argument("--start", required=True, help="Fecha ISO de inicio de la línea base (el CTL arranca en 0 ese día).")
    parser.add_argument("--apply", action="store_true", help="Persistir en Supabase si la validación pasa.")
    parser.add_argument("--out-dir", default=str(ROOT / "tmp"), help="Carpeta del informe CSV.")
    args = parser.parse_args()
    date.fromisoformat(args.start)

    load_dotenv(ROOT / ".env", override=False)
    result = asyncio.run(_compute(args.username, args.start))
    report = _write_report(result, Path(args.out_dir), args.start)

    last = result["series"][-1] if result["series"] else {}
    by_model = Counter(r.get("model") for r in result["rows"])
    print(f"actividades={len(result['rows'])} dias={len(result['series'])} informe={report}")
    print(f"hoy {last.get('date')}: TSS={last.get('tss')} ATL={last.get('atl')} CTL={last.get('ctl')} TSB={last.get('tsb')}")
    print("modelos:", json.dumps(dict(by_model.most_common()), ensure_ascii=False))
    print(
        "llamadas:", json.dumps(result["tool_counts"]),
        "reintentos:", json.dumps(result["tool_retries"]),
        "errores:", json.dumps(result["tool_errors"]),
    )
    walk = (result["profile"].get("performance") or {}).get("walk_hike_threshold_speed_m_min")
    print("umbral marcha calibrado:", walk)

    problems = _validate(result)
    if problems:
        print("VALIDACION FALLIDA:\n  - " + "\n  - ".join(problems))
    else:
        print("VALIDACION OK")

    if not args.apply:
        print("Modo prueba: no se ha escrito nada.")
        return
    if problems:
        raise SystemExit("No se aplica: validación fallida.")
    if not _activity_tss_table_exists():
        raise SystemExit("No se aplica: la tabla activity_tss no existe (ejecuta supabase/schema.sql en el SQL Editor de Supabase).")

    from agent import storage as st
    from agent import trainer_agent as ta

    st.upsert_activity_tss_rows(result["rows"])
    st.upsert_load_metrics_series(result["series"])
    ta._save_user_profile(result["profile"])
    stored = st.get_activity_tss_map(args.start) or {}
    print(f"APLICADO: activity_tss={len(stored)}/{len(result['rows'])} filas, load_metrics_daily={len(result['series'])} días")


if __name__ == "__main__":
    main()
