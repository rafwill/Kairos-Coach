from __future__ import annotations

import asyncio
import csv
import json
import sys
from datetime import date, timedelta
from datetime import datetime
from pathlib import Path
from statistics import fmean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from agent.load_metrics import estimate_session_tss, resolve_hr_profile_values
from agent.mcp_client import call_tool, garmin_mcp_session

INPUT_CSV = ROOT / "docs" / "tss_independiente_junio_a_septiembre_hasta_2026-09-16.csv"
OUT_CSV = ROOT / "docs" / "strength_hr_only_audit_2026-09-23.csv"
OUT_MD = ROOT / "docs" / "strength_hr_only_audit_2026-09-23.md"


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


def _normalize_activity_payload(raw: Any) -> dict[str, Any]:
    payload = _safe_json(raw)
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict) and isinstance(payload.get("activity"), dict):
        payload = payload["activity"]
    if not isinstance(payload, dict):
        raise RuntimeError("get_activity returned non-dict payload")
    return payload


def _extract_avg_hr(activity: dict[str, Any]) -> float | None:
    summary = activity.get("summaryDTO") if isinstance(activity.get("summaryDTO"), dict) else {}
    for key in ("averageHR", "avgHr", "avg_hr_bpm", "averageHeartRate"):
        raw = activity.get(key)
        if raw is None:
            raw = summary.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except Exception:
            continue
        if val > 0:
            return val
    return None


def _extract_activity_meta(activity: dict[str, Any]) -> tuple[str, str]:
    modality_raw: Any = (
        activity.get("type")
        or activity.get("activityType")
        or activity.get("activityTypeDTO")
        or activity.get("activityTypeDto")
        or activity.get("activityTypeKey")
        or ""
    )
    if isinstance(modality_raw, dict):
        modality = str(modality_raw.get("typeKey") or modality_raw.get("typeName") or "").strip()
    else:
        modality = str(modality_raw or "").strip()
    name = str(activity.get("activityName") or activity.get("name") or "").strip()
    return modality, name


def _extract_activity_max_hr(activity: dict[str, Any]) -> float | None:
    summary = activity.get("summaryDTO") if isinstance(activity.get("summaryDTO"), dict) else {}
    for key in ("maxHR", "maxHr", "max_hr_bpm", "maxHeartRate"):
        raw = activity.get(key)
        if raw is None:
            raw = summary.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except Exception:
            continue
        if val > 0:
            return val
    return None


def _extract_activities_list(payload: Any) -> list[dict[str, Any]]:
    data = _safe_json(payload)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("activities", "activityList", "items", "data", "result"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _extract_first_float(node: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(node, dict):
        for key in keys:
            raw = node.get(key)
            if raw is None:
                continue
            try:
                return float(raw)
            except Exception:
                continue
        for val in node.values():
            out = _extract_first_float(val, keys)
            if out is not None:
                return out
    elif isinstance(node, list):
        for item in node:
            out = _extract_first_float(item, keys)
            if out is not None:
                return out
    return None


def _extract_lthr_bpm(profile: dict[str, Any]) -> float | None:
    user_data = profile.get("userData") if isinstance(profile.get("userData"), dict) else {}
    perf = profile.get("performance") if isinstance(profile.get("performance"), dict) else {}
    candidates = (
        perf.get("hr_threshold_bpm"),
        perf.get("lthr_bpm"),
        perf.get("lactate_threshold_hr_bpm"),
        perf.get("lactate_threshold_heart_rate"),
        user_data.get("lactateThresholdHeartRate"),
        user_data.get("thresholdHeartRate"),
        profile.get("lthr_bpm"),
    )
    for raw in candidates:
        try:
            v = float(raw)
        except Exception:
            continue
        if 120.0 <= v <= 230.0:
            return v
    return None


async def _derive_resting_hr_from_rhr_day(session: Any, days: int = 14) -> float | None:
    today = date.today()
    values: list[float] = []
    for i in range(days):
        d = today - timedelta(days=i)
        raw = await call_tool(session, "get_rhr_day", {"date": d.isoformat()})
        if _looks_like_error(raw):
            continue
        payload = _safe_json(raw)
        rhr = _extract_first_float(
            payload,
            (
                "restingHeartRate",
                "resting_heart_rate",
                "restingHeartRateValue",
                "resting_hr",
                "rhr",
                "value",
            ),
        )
        if rhr is None:
            continue
        if 30.0 <= float(rhr) <= 100.0:
            values.append(float(rhr))
    if not values:
        return None
    return sum(values) / len(values)


async def _derive_max_hr_from_activities(session: Any, days: int = 365) -> float | None:
    today = date.today()
    raw = await call_tool(
        session,
        "get_activities_by_date",
        {
            "start_date": (today - timedelta(days=days)).isoformat(),
            "end_date": today.isoformat(),
        },
    )
    if _looks_like_error(raw):
        return None
    acts = _extract_activities_list(raw)
    max_values: list[float] = []
    for act in acts:
        max_hr = _extract_activity_max_hr(act)
        if max_hr is None:
            continue
        if 120.0 <= float(max_hr) <= 240.0:
            max_values.append(float(max_hr))
    if not max_values:
        return None
    return max(max_values)


def _strength_if_hr(
    avg_hr: float | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
) -> tuple[float | None, float | None]:
    if avg_hr is None:
        return None, None

    hr_rest = float(hr_rest_bpm) if hr_rest_bpm else 50.0
    hr_max = float(hr_max_bpm) if hr_max_bpm else 185.0

    if hr_rest <= 0:
        hr_rest = 50.0
    if hr_max <= hr_rest + 5.0:
        hr_max = hr_rest + 5.0

    hrr_raw = (float(avg_hr) - hr_rest) / (hr_max - hr_rest)
    hrr = max(0.20, min(0.85, hrr_raw))
    if_strength = 0.50 + (hrr * 0.40)
    if_strength = max(0.45, min(0.85, if_strength))
    return if_strength, hrr


def _load_strength_reference_rows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[int] = set()

    with INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("modalidad") or "").strip().lower() != "strength_training":
                continue

            try:
                activity_id = int((row.get("activity_id") or "").strip())
                tp_local = float((row.get("tp_local") or "").strip())
            except Exception:
                continue

            if activity_id in seen:
                continue
            seen.add(activity_id)

            out.append(
                {
                    "fecha": (row.get("fecha") or "").strip(),
                    "activity_id": activity_id,
                    "tp_local": tp_local,
                    "tp_unit": (row.get("tp_unit") or "").strip(),
                    "actividad_ref": (row.get("actividad") or "").strip(),
                }
            )

    out.sort(key=lambda r: r["fecha"], reverse=True)
    return out


def _write_markdown(
    rows: list[dict[str, Any]],
    hr_rest: float | None,
    hr_max: float | None,
    hr_rest_source: str,
    hr_max_source: str,
    lthr_bpm: float | None,
) -> None:
    valid = [r for r in rows if r.get("calc_error") == "" and r.get("tss_hr_only") is not None]
    missing_hr = [r for r in valid if not bool(r.get("has_average_hr"))]

    if valid:
        deltas = [float(r["delta_vs_tp"]) for r in valid]
        ratios = [float(r["ratio_vs_tp"]) for r in valid if float(r["tp_local"]) > 0]
        mae = fmean(abs(x) for x in deltas)
        bias = fmean(deltas)
        ratio_mean = fmean(ratios) if ratios else 0.0
    else:
        mae = bias = ratio_mean = 0.0

    lines = [
        "# Auditoria fuerza HR-only (codigo actual)",
        "",
        f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Referencia TP (rows strength): {len(rows)}",
        f"FC reposo de perfil usada: {hr_rest if hr_rest is not None else 'n/d'}",
        f"FC max de perfil usada: {hr_max if hr_max is not None else 'n/d'}",
        f"LTHR de referencia: {lthr_bpm if lthr_bpm is not None else 'n/d'}",
        f"Fuente FC reposo efectiva: {hr_rest_source}",
        f"Fuente FC max efectiva: {hr_max_source}",
        "",
        "## Resumen",
        f"- sesiones auditadas: {len(valid)}",
        f"- sesiones sin averageHR usable: {len(missing_hr)}",
        f"- MAE vs TP: {mae:.6f}",
        f"- bias vs TP: {bias:.6f}",
        f"- ratio medio calc/TP: {ratio_mean:.6f}",
        "",
        "## CSV",
        f"- {OUT_CSV.as_posix()}",
    ]

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    load_dotenv(".env", override=False)
    ref_rows = _load_strength_reference_rows()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not ref_rows:
        raise RuntimeError("No strength reference rows found in TP source CSV")

    results: list[dict[str, Any]] = []

    async with garmin_mcp_session(essential_only=False) as session:
        raw_profile = await call_tool(session, "get_user_profile", {})
        profile = _safe_json(raw_profile)
        if not isinstance(profile, dict):
            profile = {}
        hr_rest, hr_max = resolve_hr_profile_values(profile)
        lthr_bpm = _extract_lthr_bpm(profile)
        hr_rest_source = "profile"
        hr_max_source = "profile"

        if hr_rest is None:
            hr_rest_derived = await _derive_resting_hr_from_rhr_day(session, days=14)
            if hr_rest_derived is not None:
                hr_rest = float(hr_rest_derived)
                hr_rest_source = "derived_get_rhr_day_14d"
            else:
                hr_rest_source = "fallback_50"

        if hr_max is None:
            hr_max_derived = await _derive_max_hr_from_activities(session, days=365)
            if hr_max_derived is not None:
                hr_max = float(hr_max_derived)
                hr_max_source = "derived_activities_maxHR_365d"
            else:
                hr_max_source = "fallback_185"

        # Physiological guardrail for offline audit only:
        # if derived/loaded HRmax is implausibly close to LTHR, lift HRmax from LTHR.
        if lthr_bpm is not None and hr_max is not None:
            lthr_guard = float(lthr_bpm) / 0.90
            if hr_max <= float(lthr_bpm) + 5.0 and lthr_guard > hr_max:
                hr_max = lthr_guard
                hr_max_source = f"lthr_guardrail_90pct_from_{round(float(lthr_bpm),1)}"

        for ref in ref_rows:
            activity_id = int(ref["activity_id"])
            row: dict[str, Any] = {
                "generated_at": generated_at,
                "activity_id": activity_id,
                "fecha": ref["fecha"],
                "actividad_ref": ref["actividad_ref"],
                "tp_local": float(ref["tp_local"]),
                "tp_unit": ref["tp_unit"],
                "activity_type": "",
                "activity_name": "",
                "average_hr": "",
                "has_average_hr": False,
                "hr_rest_profile": hr_rest if hr_rest is not None else "",
                "hr_max_profile": hr_max if hr_max is not None else "",
                "hr_rest_effective": "",
                "hr_max_effective": "",
                "hr_profile_source": "",
                "hrr_clamped": "",
                "if_hr_strength": "",
                "tss_hr_only": "",
                "method_label": "",
                "delta_vs_tp": "",
                "ratio_vs_tp": "",
                "calc_error": "",
            }

            try:
                raw_activity = await call_tool(session, "get_activity", {"activity_id": activity_id})
                if _looks_like_error(raw_activity):
                    raise RuntimeError(str(raw_activity).strip())

                activity = _normalize_activity_payload(raw_activity)
                act_type, act_name = _extract_activity_meta(activity)
                avg_hr = _extract_avg_hr(activity)
                if_strength, hrr = _strength_if_hr(avg_hr, hr_rest, hr_max)
                hr_rest_eff = float(hr_rest) if hr_rest is not None else 50.0
                hr_max_eff = float(hr_max) if hr_max is not None else 185.0

                tss, label = estimate_session_tss(
                    activity,
                    hr_rest_bpm=hr_rest,
                    hr_max_bpm=hr_max,
                )

                tp = float(ref["tp_local"])
                tss_val = float(tss or 0.0)

                row["activity_type"] = act_type
                row["activity_name"] = act_name
                row["average_hr"] = "" if avg_hr is None else round(float(avg_hr), 6)
                row["has_average_hr"] = avg_hr is not None
                row["hr_rest_effective"] = round(hr_rest_eff, 6)
                row["hr_max_effective"] = round(hr_max_eff, 6)
                if hr_rest is not None and hr_max is not None:
                    row["hr_profile_source"] = f"rest:{hr_rest_source}|max:{hr_max_source}"
                elif hr_rest is not None:
                    row["hr_profile_source"] = f"rest:{hr_rest_source}|max:fallback_185"
                elif hr_max is not None:
                    row["hr_profile_source"] = f"rest:fallback_50|max:{hr_max_source}"
                else:
                    row["hr_profile_source"] = "fallback_50_185"
                row["hrr_clamped"] = "" if hrr is None else round(float(hrr), 6)
                row["if_hr_strength"] = "" if if_strength is None else round(float(if_strength), 6)
                row["tss_hr_only"] = round(tss_val, 6)
                row["method_label"] = str(label or "")
                row["delta_vs_tp"] = round(tss_val - tp, 6)
                row["ratio_vs_tp"] = round((tss_val / tp), 6) if tp > 0 else ""
            except Exception as ex:
                row["calc_error"] = str(ex)

            results.append(row)

    results.sort(key=lambda r: str(r.get("fecha") or ""), reverse=True)

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        fieldnames = list(results[0].keys()) if results else []
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        if results:
            writer.writerows(results)

    _write_markdown(results, hr_rest, hr_max, hr_rest_source, hr_max_source, lthr_bpm)

    ok = [r for r in results if r.get("calc_error") == ""]
    print(f"output_csv={OUT_CSV.as_posix()}")
    print(f"output_md={OUT_MD.as_posix()}")
    print(f"rows_strength_reference={len(ref_rows)}")
    print(f"rows_audited_ok={len(ok)}")


if __name__ == "__main__":
    asyncio.run(main())
