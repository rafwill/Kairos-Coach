from __future__ import annotations

import asyncio
import csv
import json
import re
import sys
import unicodedata
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

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

JUNE_CSV = ROOT / "docs" / "junio_tp_vs_garmin_2026-09-15_clean.csv"
JUL_SEP_CSV = ROOT / "docs" / "tss_comparativa_desde_2026-07-01.csv"
OUT_CSV = ROOT / "docs" / "tss_independiente_junio_a_septiembre_hasta_2026-09-16.csv"

DATE_FROM = date(2026, 6, 1)
DATE_TO = date(2026, 9, 30)
RUNNING_BIAS_ABS_LIMIT = 3.5


def _safe_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        txt = value.strip()
        if txt.startswith("{") or txt.startswith("["):
            try:
                return json.loads(txt)
            except Exception:
                return value
    return value


def _looks_like_error(raw: Any) -> bool:
    if not isinstance(raw, str):
        return False
    txt = raw.strip().lower()
    return txt.startswith("error") or "error executing tool" in txt


def _extract_ftp(payload: Any) -> float | None:
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

    def _as_pos(raw: Any) -> float | None:
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


def _parse_date(value: str) -> date | None:
    txt = (value or "").strip()
    if not txt:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


def _norm_text(value: str) -> str:
    txt = (value or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return txt


def _load_june_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with JUNE_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            dt = _parse_date(row.get("fecha", ""))
            if dt is None or not (DATE_FROM <= dt <= DATE_TO):
                continue
            if dt.month != 6:
                continue
            try:
                activity_id = int((row.get("activity_id") or "").strip())
                tp_val = float((row.get("tp_local") or "").strip())
            except Exception:
                continue
            rows.append(
                {
                    "fecha": dt.isoformat(),
                    "activity_id": activity_id,
                    "modalidad": (row.get("modalidad") or "").strip(),
                    "actividad": (row.get("actividad") or "").strip(),
                    "tp_local": tp_val,
                    "tp_unit": (row.get("tp_unit") or "").strip() or "TSS",
                    "match_reason": (row.get("match_reason") or "").strip() or "",
                    "source_dataset": "june_clean",
                }
            )
    return rows


def _parse_calc_number(raw: str) -> float | None:
    txt = (raw or "").strip()
    if not txt:
        return None
    match = re.search(r"-?\d+(?:[\.,]\d+)?", txt)
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def _load_jul_sep_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with JUL_SEP_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            dt = _parse_date(row.get("fecha", ""))
            if dt is None or not (DATE_FROM <= dt <= DATE_TO):
                continue
            if dt.month < 7:
                continue
            tp_raw = (row.get("tp_local") or "").strip()
            if not tp_raw:
                continue
            try:
                activity_id = int((row.get("activity_id") or "").strip())
                tp_val = float(tp_raw)
            except Exception:
                continue
            unidad = "TSS"
            modalidad = (row.get("modalidad") or "").strip()
            m = modalidad.lower()
            if m in {"trail_running", "hiking", "walking"}:
                unidad = "hrTSS"
            elif m in {"running", "treadmill_running"}:
                unidad = "rTSS"

            rows.append(
                {
                    "fecha": dt.isoformat(),
                    "activity_id": activity_id,
                    "modalidad": modalidad,
                    "actividad": (row.get("actividad") or "").strip(),
                    "tp_local": tp_val,
                    "tp_unit": unidad,
                    "match_reason": (
                        "single_candidate_fallback" if activity_id == 23455001968 else "exact_modality"
                    ),
                    "source_dataset": "tp_memory_jul_sep",
                }
            )
    return rows


def _merge_rows_once() -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for row in _load_june_rows():
        merged[row["activity_id"]] = row
    for row in _load_jul_sep_rows():
        merged[row["activity_id"]] = row
    out = list(merged.values())
    out.sort(key=lambda r: r["fecha"], reverse=True)
    return out


def _is_included_for_aggregates(row: dict[str, Any]) -> tuple[bool, str]:
    reason = str(row.get("match_reason") or "")
    activity_id = int(row.get("activity_id") or 0)
    if reason == "exact_modality":
        return True, "exact_modality"
    if reason == "single_candidate_fallback" and activity_id == 23455001968:
        return True, "manual_verified_fallback"
    return False, "excluded_match_reason"


def _metrics(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    if not rows:
        return {"n": 0, "mae": 0.0, "bias": 0.0, "ratio_mean": 0.0}
    deltas = [float(r["delta"]) for r in rows]
    ratios = [float(r["ratio"]) for r in rows]
    return {
        "n": len(rows),
        "mae": fmean(abs(x) for x in deltas),
        "bias": fmean(deltas),
        "ratio_mean": fmean(ratios),
    }


def _print_metric(name: str, payload: dict[str, float | int]) -> None:
    print(
        f"{name}: n={int(payload['n'])} "
        f"MAE={float(payload['mae']):.6f} "
        f"bias={float(payload['bias']):.6f} "
        f"ratio_mean={float(payload['ratio_mean']):.6f}"
    )


def _is_running(modalidad: str) -> bool:
    return (modalidad or "").strip().lower() in {"running", "treadmill_running"}


def _segment_running(activity_name: str) -> str:
    txt = _norm_text(activity_name)
    reps_patterns = (
        "x100",
        "x 100",
        "x45",
        "45\"",
        "45''",
    )
    if any(p in txt for p in reps_patterns):
        return "repeticiones_cortas"
    if "fartlek" in txt or "sostenido" in txt:
        return "fartlek_tempo_sostenido"
    return "rodaje_continuo"


def _segment_strength(activity_name: str) -> str:
    txt = _norm_text(activity_name)
    if "neuromuscular" in txt:
        return "neuromuscular"
    if any(k in txt for k in ("movilidad", "activacion", "activation", "mantenimiento muscular", "foam")):
        return "movilidad_activacion"
    return "other_strength"


async def main() -> None:
    load_dotenv(".env", override=False)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    base_rows = _merge_rows_once()
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

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
            activity_id = int(item["activity_id"])
            include_row, include_reason = _is_included_for_aggregates(item)
            out_row = {
                "generated_at": generated_at,
                "formula_version": TSS_FORMULA_VERSION,
                "fecha": item["fecha"],
                "activity_id": activity_id,
                "modalidad": item["modalidad"],
                "actividad": item["actividad"],
                "tp_local": item["tp_local"],
                "tp_unit": item["tp_unit"],
                "calc_tss": "",
                "calc_method": "",
                "delta": "",
                "ratio": "",
                "match_reason": item["match_reason"],
                "source_dataset": item["source_dataset"],
                "include_in_aggregates": include_row,
                "include_reason": include_reason,
                "calc_error": "",
            }
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

                tp_val = float(item["tp_local"])
                tss_val = float(tss_new or 0.0)
                out_row["calc_tss"] = round(tss_val, 6)
                out_row["calc_method"] = str(method_new or "")
                out_row["delta"] = round(tss_val - tp_val, 6)
                out_row["ratio"] = round((tss_val / tp_val), 6) if tp_val > 0 else ""
            except Exception as ex:
                out_row["calc_error"] = str(ex)
                out_row["include_in_aggregates"] = False
                out_row["include_reason"] = "calc_error"
                errors.append({"activity_id": activity_id, "error": str(ex)})

            results.append(out_row)

    results.sort(key=lambda r: r["fecha"], reverse=True)

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        fieldnames = list(results[0].keys()) if results else [
            "generated_at",
            "formula_version",
            "fecha",
            "activity_id",
            "modalidad",
            "actividad",
            "tp_local",
            "tp_unit",
            "calc_tss",
            "calc_method",
            "delta",
            "ratio",
            "match_reason",
            "source_dataset",
            "include_in_aggregates",
            "include_reason",
            "calc_error",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        if results:
            writer.writerows(results)

    included = [
        r
        for r in results
        if bool(r.get("include_in_aggregates"))
        and r.get("delta") != ""
        and r.get("ratio") != ""
    ]
    excluded = [r for r in results if not bool(r.get("include_in_aggregates"))]

    running = [r for r in included if _is_running(str(r.get("modalidad") or ""))]
    run_reps = [r for r in running if _segment_running(str(r.get("actividad") or "")) == "repeticiones_cortas"]
    run_fartlek = [r for r in running if _segment_running(str(r.get("actividad") or "")) == "fartlek_tempo_sostenido"]
    run_rodaje = [r for r in running if _segment_running(str(r.get("actividad") or "")) == "rodaje_continuo"]

    strength_rows = [r for r in included if str(r.get("modalidad") or "").lower() == "strength_training"]
    strength_neuro = [r for r in strength_rows if _segment_strength(str(r.get("actividad") or "")) == "neuromuscular"]
    strength_mov = [r for r in strength_rows if _segment_strength(str(r.get("actividad") or "")) == "movilidad_activacion"]

    trail_rows = [r for r in included if str(r.get("modalidad") or "").lower() == "trail_running"]

    m_running = _metrics(running)
    m_run_reps = _metrics(run_reps)
    m_run_fartlek = _metrics(run_fartlek)
    m_run_rodaje = _metrics(run_rodaje)
    m_strength_neuro = _metrics(strength_neuro)
    m_strength_mov = _metrics(strength_mov)
    m_trail = _metrics(trail_rows)

    running_pass_mae = float(m_running["mae"]) < 7.0
    running_pass_bias = abs(float(m_running["bias"])) <= RUNNING_BIAS_ABS_LIMIT

    def _segment_pass(m: dict[str, float | int]) -> bool:
        if int(m["n"]) == 0:
            return False
        ratio = float(m["ratio_mean"])
        return 0.88 <= ratio <= 1.12

    seg_reps_pass = _segment_pass(m_run_reps)
    seg_fartlek_pass = _segment_pass(m_run_fartlek)
    seg_rodaje_pass = _segment_pass(m_run_rodaje)
    running_all_pass = (
        running_pass_mae
        and running_pass_bias
        and seg_reps_pass
        and seg_fartlek_pass
        and seg_rodaje_pass
    )

    def _strength_status(m: dict[str, float | int]) -> str:
        if int(m["n"]) == 0:
            return "pendiente"
        ratio = float(m["ratio_mean"])
        bias = abs(float(m["bias"]))
        if 0.88 <= ratio <= 1.12 and bias <= 3.0:
            return "cerrado"
        return "pendiente"

    excluded_ids = [int(r["activity_id"]) for r in excluded]

    print(f"output={OUT_CSV.as_posix()}")
    print(f"formula_version={TSS_FORMULA_VERSION}")
    print(f"combined_rows_total={len(results)}")
    print(f"combined_rows_included={len(included)}")
    print(f"combined_rows_excluded={len(excluded)}")
    print("excluded_activity_ids=" + ",".join(str(x) for x in excluded_ids))

    print("\n== METRICS ==")
    _print_metric("running_global", m_running)
    _print_metric("running_segment_repeticiones_cortas", m_run_reps)
    _print_metric("running_segment_fartlek_tempo_sostenido", m_run_fartlek)
    _print_metric("running_segment_rodaje_continuo", m_run_rodaje)
    _print_metric("strength_neuromuscular", m_strength_neuro)
    _print_metric("strength_movilidad_activacion", m_strength_mov)
    _print_metric("trail", m_trail)

    print("\n== RUNNING CLOSURE CRITERIA ==")
    print(f"criterion_mae_lt_7={running_pass_mae}")
    print(f"criterion_bias_in_pm3_5={running_pass_bias}")
    print(f"criterion_segment_reps_ratio_in_0.88_1.12={seg_reps_pass}")
    print(f"criterion_segment_fartlek_ratio_in_0.88_1.12={seg_fartlek_pass}")
    print(f"criterion_segment_rodaje_ratio_in_0.88_1.12={seg_rodaje_pass}")
    print(f"running_verdict={'cerrado' if running_all_pass else 'pendiente'}")

    print("\n== STRENGTH SUMMARY ==")
    print(f"strength_neuromuscular_status={_strength_status(m_strength_neuro)}")
    print(f"strength_movilidad_activacion_status={_strength_status(m_strength_mov)}")

    if errors:
        print("\n== ERRORS ==")
        for err in errors:
            print(f"activity_id={err['activity_id']} error={err['error']}")


if __name__ == "__main__":
    asyncio.run(main())
