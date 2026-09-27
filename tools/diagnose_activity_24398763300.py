from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from agent.load_metrics import (
    estimate_session_tss,
    estimate_running_tss_tp_like_adjusted,
    estimate_running_tss_tp_like_v2,
    extract_activity_duration_hours,
    resolve_hr_profile_values,
    resolve_running_threshold_pace_sec_per_km,
)
from agent.mcp_client import call_tool, garmin_mcp_session
from agent.running_tss import procesar_actividad
from agent.running_tss import (
    calcular_ngp,
    calcular_pendiente_por_muestra,
    calcular_rtss,
    detectar_tramos_pausados,
    parsear_activity_details_garmin,
    remuestrear_1hz_lineal,
    velocidad_ajustada_por_pendiente,
)
from agent.trainer_agent import _resolve_hr_threshold_bpm

ACTIVITY_ID = 24398763300
CALL_TIMEOUT_S = 60.0


async def _call_tool_safe(session: Any, tool_name: str, args: dict[str, Any]) -> Any:
    try:
        return await asyncio.wait_for(call_tool(session, tool_name, args), timeout=CALL_TIMEOUT_S)
    except TimeoutError:
        return f"Error executing tool {tool_name}: timeout after {CALL_TIMEOUT_S:.0f}s"


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
    return isinstance(raw, str) and (
        raw.strip().lower().startswith("error") or "error executing tool" in raw.strip().lower()
    )


def _normalize_activity(raw: Any) -> dict[str, Any]:
    data = _safe_json(raw)
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict) and isinstance(data.get("activity"), dict):
        data = data["activity"]
    if not isinstance(data, dict):
        raise RuntimeError("activity payload is not dict")
    return data


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
            v = float(raw)
        except Exception:
            return None
        return v if v > 0 else None

    if isinstance(payload, (int, float, str)):
        return _as_pos(payload)

    if isinstance(payload, list):
        for item in payload:
            out = _extract_ftp(item)
            if out is not None:
                return out
        return None

    if isinstance(payload, dict):
        for k in keys:
            if k in payload:
                out = _as_pos(payload.get(k))
                if out is not None:
                    return out
        for nested in ("data", "result", "profile", "performance", "userData"):
            if nested in payload:
                out = _extract_ftp(payload.get(nested))
                if out is not None:
                    return out

    return None


def _activity_meta(activity: dict[str, Any]) -> dict[str, Any]:
    at = activity.get("activityType")
    if isinstance(at, dict):
        type_key = at.get("typeKey")
    else:
        type_key = at
    return {
        "activity_id": activity.get("activityId") or activity.get("activity_id"),
        "name": activity.get("activityName") or activity.get("name"),
        "date": str(activity.get("startTimeLocal") or activity.get("activityDate") or "")[:10],
        "type_key": type_key,
        "duration_s": activity.get("duration")
        or activity.get("durationInSeconds")
        or (activity.get("summaryDTO") or {}).get("duration"),
        "avg_hr": activity.get("averageHR") or (activity.get("summaryDTO") or {}).get("averageHR"),
        "max_hr": activity.get("maxHR") or (activity.get("summaryDTO") or {}).get("maxHR"),
        "avg_speed": activity.get("averageSpeed") or (activity.get("summaryDTO") or {}).get("averageSpeed"),
    }


async def main() -> None:
    load_dotenv(".env", override=False)

    async with garmin_mcp_session(essential_only=False) as session:
        raw_profile = await _call_tool_safe(session, "get_user_profile", {})
        if _looks_like_error(raw_profile):
            raise RuntimeError(str(raw_profile))
        profile = _safe_json(raw_profile)
        if not isinstance(profile, dict):
            profile = {}

        hr_rest, hr_max = resolve_hr_profile_values(profile)
        run_thr = resolve_running_threshold_pace_sec_per_km(profile)
        lthr_profile, _, _ = _resolve_hr_threshold_bpm(profile)
        ftp = _extract_ftp(profile)

        raw_activity = await _call_tool_safe(session, "get_activity", {"activity_id": ACTIVITY_ID})
        if _looks_like_error(raw_activity):
            raise RuntimeError(str(raw_activity))
        activity = _normalize_activity(raw_activity)

        raw_zones = await _call_tool_safe(session, "get_activity_hr_in_timezones", {"activity_id": ACTIVITY_ID})
        zones_raw = None if _looks_like_error(raw_zones) else (raw_zones if isinstance(raw_zones, str) else json.dumps(raw_zones))

        raw_details = await _call_tool_safe(session, "get_activity_details", {"activity_id": ACTIVITY_ID})
        if _looks_like_error(raw_details):
            raise RuntimeError(str(raw_details))
        details_raw = raw_details if isinstance(raw_details, str) else json.dumps(raw_details, ensure_ascii=False)

        tss, method = estimate_session_tss(
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

        atleta = {
            "ftpace_ms": (1000.0 / run_thr) if run_thr and run_thr > 0 else None,
            "hr_reposo": hr_rest,
            "hr_max": hr_max,
            "lthr": lthr_profile,
            "sexo": profile.get("userData", {}).get("gender") or profile.get("sex") or "male",
        }
        run_pipeline = procesar_actividad(details_raw, atleta)

        # Sensitivity check: when paused_seconds=0, include/exclude paused mask should be identical.
        parsed = parsear_activity_details_garmin(details_raw)
        t_raw = parsed["tiempo_s"]
        v_raw = parsed["velocidad_ms"]
        el_raw = parsed["elevacion_m"]
        d_raw = parsed["distancia_m"]
        _, v_1hz = remuestrear_1hz_lineal(t_raw, v_raw)
        _, el_1hz = remuestrear_1hz_lineal(t_raw, el_raw)
        _, d_1hz = remuestrear_1hz_lineal(t_raw, d_raw)
        n = min(len(v_1hz), len(el_1hz), len(d_1hz))
        v_1hz = v_1hz[:n]
        el_1hz = el_1hz[:n]
        d_1hz = d_1hz[:n]
        paused = detectar_tramos_pausados(v_1hz)
        grade = calcular_pendiente_por_muestra(el_1hz, d_1hz)
        v_adj = velocidad_ajustada_por_pendiente(v_1hz, grade)

        ngp_with_pauses = calcular_ngp(v_adj, paused_mask=None, rolling_window_s=30)
        ngp_without_pauses = calcular_ngp(v_adj, paused_mask=paused, rolling_window_s=30)
        ftpace_ms = (1000.0 / run_thr) if run_thr and run_thr > 0 else 0.0
        rtss_with_pauses = None
        rtss_without_pauses = None
        if ngp_with_pauses is not None and ftpace_ms > 0:
            rtss_with_pauses, _ = calcular_rtss(float(n), ngp_with_pauses, ftpace_ms)
        if ngp_without_pauses is not None and ftpace_ms > 0:
            rtss_without_pauses, _ = calcular_rtss(float(n), ngp_without_pauses, ftpace_ms)

        hours = extract_activity_duration_hours(activity)
        fallback_v2 = estimate_running_tss_tp_like_v2(
            activity,
            hours=hours,
            running_threshold_pace_sec_per_km=run_thr,
            hr_rest_bpm=hr_rest,
            hr_max_bpm=hr_max,
        )
        fallback_adjusted = estimate_running_tss_tp_like_adjusted(
            activity,
            hours=hours,
            running_threshold_pace_sec_per_km=run_thr,
            hr_rest_bpm=hr_rest,
            hr_max_bpm=hr_max,
        )

        out = {
            "activity_meta": _activity_meta(activity),
            "profile_inputs": {
                "hr_rest": hr_rest,
                "hr_max": hr_max,
                "run_threshold_pace_sec_per_km": run_thr,
                "lthr_profile": lthr_profile,
            },
            "estimate_session_tss": {
                "tss": tss,
                "method": method,
            },
            "running_pipeline": {
                "rTSS": run_pipeline.get("rTSS"),
                "hrTSS": run_pipeline.get("hrTSS"),
                "IF": run_pipeline.get("IF"),
                "ngp_ms": run_pipeline.get("ngp_ms"),
                "paused_seconds": run_pipeline.get("paused_seconds"),
                "samples_1hz": run_pipeline.get("samples_1hz"),
                "include_pauses_in_ngp": run_pipeline.get("include_pauses_in_ngp"),
                "legacy_variant_a_triggered": run_pipeline.get("legacy_variant_a_triggered"),
                "tempo_detector_triggered": run_pipeline.get("tempo_detector_triggered"),
                "tempo_detector_if_threshold": run_pipeline.get("tempo_detector_if_threshold"),
                "tempo_detector_longest_block_s": run_pipeline.get("tempo_detector_longest_block_s"),
                "short_reps_detector_triggered": run_pipeline.get("short_reps_detector_triggered"),
                "short_reps_detector_transitions_per_h": run_pipeline.get("short_reps_detector_transitions_per_h"),
                "short_reps_detector_share_fast": run_pipeline.get("short_reps_detector_share_fast"),
                "short_reps_detector_work_bouts_in_range": run_pipeline.get("short_reps_detector_work_bouts_in_range"),
                "short_reps_detector_work_bout_cv": run_pipeline.get("short_reps_detector_work_bout_cv"),
                "short_reps_detector_recovery_bout_cv": run_pipeline.get("short_reps_detector_recovery_bout_cv"),
            },
            "pause_sensitivity": {
                "paused_seconds_recomputed": int(sum(1 for x in paused if x)),
                "ngp_with_pauses": ngp_with_pauses,
                "ngp_without_pauses": ngp_without_pauses,
                "rtss_with_pauses": rtss_with_pauses,
                "rtss_without_pauses": rtss_without_pauses,
            },
            "fallback_comparison": {
                "running_tss_tp_like_v2": fallback_v2,
                "running_tss_tp_like_adjusted": fallback_adjusted,
            },
        }

        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
