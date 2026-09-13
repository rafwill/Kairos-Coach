from __future__ import annotations

import asyncio
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from agent.load_metrics import (
    TSS_FORMULA_VERSION,
    estimate_session_tss,
    resolve_hr_profile_values,
    resolve_running_threshold_pace_sec_per_km,
)
from agent.mcp_client import call_tool, garmin_mcp_session
from agent.trainer_agent import _resolve_hr_threshold_bpm

TP_MEMORY_CSV = "docs/tss_comparativa_desde_2026-07-01.csv"
DATE_FROM = date(2026, 7, 1)


def _safe_json(value):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except Exception:
                return value
    return value


def _looks_like_error(raw) -> bool:
    if not isinstance(raw, str):
        return False
    txt = raw.strip().lower()
    return txt.startswith("error") or "error executing tool" in txt


def _extract_ftp(payload):
    if payload is None:
        return None

    keys = (
        "cyclingFtp",
        "cycling_ftp",
        "ftp",
        "functionalThresholdPower",
        "functional_threshold_power",
        "functional_threshold_power_watts",
    )

    def _as_pos(raw):
        try:
            val = float(raw)
        except Exception:
            return None
        return val if val > 0 else None

    if isinstance(payload, (int, float, str)):
        return _as_pos(payload)

    if isinstance(payload, list):
        for item in payload:
            out = _extract_ftp(item)
            if out is not None:
                return out
        return None

    if isinstance(payload, dict):
        for key in keys:
            if key in payload:
                out = _as_pos(payload.get(key))
                if out is not None:
                    return out
        for nested in ("data", "result", "profile", "performance", "userData"):
            if nested in payload:
                out = _extract_ftp(payload.get(nested))
                if out is not None:
                    return out

    return None


def _parse_fecha(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _tp_unit_guess(modalidad: str) -> str:
    mode = (modalidad or "").strip().lower()
    if mode in {"trail_running", "hiking", "walking"}:
        return "hrTSS"
    if mode in {"running", "treadmill_running"}:
        return "rTSS"
    return "TSS"


def _read_tp_memory_rows() -> list[dict]:
    out = []
    with open(TP_MEMORY_CSV, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            tp_raw = (row.get("tp_local") or "").strip()
            if not tp_raw:
                continue

            fecha_dt = _parse_fecha((row.get("fecha") or "").strip())
            if fecha_dt is None:
                continue
            if fecha_dt.date() < DATE_FROM or fecha_dt.date() > date.today():
                continue

            try:
                activity_id = int((row.get("activity_id") or "").strip())
                tp_val = float(tp_raw)
            except Exception:
                continue

            out.append(
                {
                    "fecha": fecha_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "activity_id": activity_id,
                    "modalidad": (row.get("modalidad") or "").strip(),
                    "actividad": (row.get("actividad") or "").strip(),
                    "tp_memoria": tp_val,
                    "tp_memoria_unidad": _tp_unit_guess((row.get("modalidad") or "").strip()),
                }
            )

    out.sort(key=lambda r: r["fecha"], reverse=True)
    return out


async def main():
    load_dotenv(".env", override=False)

    base_rows = _read_tp_memory_rows()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out_path = f"docs/tss_independiente_desde_2026-07-01_hasta_{date.today().isoformat()}.csv"

    results = []
    errors = []

    async with garmin_mcp_session(essential_only=False) as session:
        raw_profile = await call_tool(session, "get_user_profile", {})
        profile = _safe_json(raw_profile)
        if not isinstance(profile, dict):
            profile = {}

        hr_rest, hr_max = resolve_hr_profile_values(profile)
        run_thr = resolve_running_threshold_pace_sec_per_km(profile)
        lthr_profile, _, _ = _resolve_hr_threshold_bpm(profile)
        ftp = _extract_ftp(profile)
        if ftp is None:
            raw_ftp = await call_tool(session, "get_cycling_ftp", {})
            ftp = _extract_ftp(_safe_json(raw_ftp))

        for item in base_rows:
            activity_id = item["activity_id"]
            try:
                raw_activity = await call_tool(session, "get_activity", {"activity_id": activity_id})
                if _looks_like_error(raw_activity):
                    raise RuntimeError(str(raw_activity).strip())

                activity = _safe_json(raw_activity)
                if isinstance(activity, list) and activity:
                    activity = activity[0]
                if isinstance(activity, dict) and isinstance(activity.get("activity"), dict):
                    activity = activity["activity"]
                if not isinstance(activity, dict):
                    raise RuntimeError("get_activity returned non-dict payload")

                raw_zones = await call_tool(session, "get_activity_hr_in_timezones", {"activity_id": activity_id})
                zones_raw = (
                    None
                    if _looks_like_error(raw_zones)
                    else (raw_zones if isinstance(raw_zones, str) and raw_zones.strip() else None)
                )

                raw_details = await call_tool(session, "get_activity_details", {"activity_id": activity_id})
                if _looks_like_error(raw_details):
                    raise RuntimeError(str(raw_details).strip())
                details_raw = raw_details if isinstance(raw_details, str) else json.dumps(raw_details, ensure_ascii=False)

                tss_new, method_new = estimate_session_tss(
                    activity,
                    ftp=ftp,
                    running_threshold_pace_sec_per_km=run_thr,
                    hr_rest_bpm=hr_rest,
                    hr_max_bpm=hr_max,
                    hr_zones_raw=zones_raw,
                    activity_details_raw=details_raw,
                    use_trail_splits=False,
                    hr_threshold_bpm=lthr_profile,
                )

                tp_val = float(item["tp_memoria"])
                tss_val = float(tss_new or 0.0)

                results.append(
                    {
                        "generated_at": generated_at,
                        "formula_version": TSS_FORMULA_VERSION,
                        "fecha": item["fecha"],
                        "activity_id": activity_id,
                        "modalidad": item["modalidad"],
                        "actividad": item["actividad"],
                        "nueva_formula_valor_metodo": f"{tss_val:.1f} {method_new}",
                        "nueva_formula_valor": round(tss_val, 1),
                        "nueva_formula_metodo": method_new,
                        "tp_memoria_valor_unidad": f"{tp_val:.1f} {item['tp_memoria_unidad']}",
                        "tp_memoria_valor": tp_val,
                        "tp_memoria_unidad": item["tp_memoria_unidad"],
                        "delta_nueva_vs_tp": round(tss_val - tp_val, 1),
                        "ratio_nueva_vs_tp": round((tss_val / tp_val), 3) if tp_val > 0 else "",
                    }
                )
            except Exception as ex:
                errors.append({"activity_id": activity_id, "error": str(ex)})

    results.sort(key=lambda r: r["fecha"], reverse=True)

    if results:
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)

    print("script=tools/generate_independent_tp_table.py")
    print(f"formula_version={TSS_FORMULA_VERSION}")
    print(f"date_from={DATE_FROM.isoformat()}")
    print(f"date_to={date.today().isoformat()}")
    print(f"rows_with_tp_memory={len(base_rows)}")
    print(f"processed={len(results)}")
    print(f"errors={len(errors)}")
    print(f"output={out_path}")
    for item in errors:
        print(f"error_activity_id={item['activity_id']} error={item['error']}")


if __name__ == "__main__":
    asyncio.run(main())