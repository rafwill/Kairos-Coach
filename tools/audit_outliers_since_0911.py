from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from agent.load_metrics import (
    estimate_session_tss,
    extract_activity_duration_hours,
    resolve_hr_profile_values,
    resolve_running_threshold_pace_sec_per_km,
)
from agent.mcp_client import call_tool, garmin_mcp_session
from agent.running_tss import procesar_actividad
from agent.trainer_agent import _resolve_hr_threshold_bpm

# User-provided TP entries (manual input, pending source verification)
TP_MAP: dict[int, float] = {
    24509729642: 112.0,
    24502427862: 3.0,
    24496338934: 92.0,
    24492874850: 18.0,
    24484006590: 10.0,
    24478612377: 33.0,
    24471246072: 65.0,
    24455637560: 105.0,
    24437481035: 91.0,
    24430006167: 15.0,
    24407524068: 175.0,
    24398763300: 54.0,
    24383422318: 21.0,
    24383043078: 42.0,
    24368377888: 76.0,
    24342674498: 135.0,
    24322094785: 261.7,
}

RATIO_LOW = 0.75
RATIO_HIGH = 1.35
CALL_TIMEOUT_S = 60.0
RETRY_DELAY_S = 2.5
RETRY_ATTEMPTS = 2


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


async def _call_tool_safe(session: Any, tool_name: str, args: dict[str, Any]) -> Any:
    try:
        return await asyncio.wait_for(call_tool(session, tool_name, args), timeout=CALL_TIMEOUT_S)
    except TimeoutError:
        return f"Error executing tool {tool_name}: timeout after {CALL_TIMEOUT_S:.0f}s"


async def _call_tool_with_retries(session: Any, tool_name: str, args: dict[str, Any]) -> Any:
    raw = await _call_tool_safe(session, tool_name, args)
    if not _looks_like_error(raw):
        return raw

    text = str(raw).lower()
    retryable = "504" in text or "timeout" in text or "retryable" in text
    if not retryable:
        return raw

    for _ in range(RETRY_ATTEMPTS):
        await asyncio.sleep(RETRY_DELAY_S)
        raw = await _call_tool_safe(session, tool_name, args)
        if not _looks_like_error(raw):
            return raw
    return raw


def _normalize_activity_payload(raw: Any) -> dict[str, Any]:
    payload = _safe_json(raw)
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict) and isinstance(payload.get("activity"), dict):
        payload = payload["activity"]
    if not isinstance(payload, dict):
        raise RuntimeError("get_activity returned non-dict payload")
    return payload


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


def _activity_id(act: dict[str, Any]) -> int | None:
    raw = act.get("activityId") or act.get("activity_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _activity_type_key(act: dict[str, Any]) -> str:
    at = act.get("activityType") or act.get("activityTypeDTO") or act.get("activityTypeDto") or act.get("type")
    if isinstance(at, dict):
        tk = at.get("typeKey") or at.get("typeName") or ""
        return str(tk)
    return str(at or "")


def _activity_date_iso(act: dict[str, Any]) -> str:
    for key in ("startTimeLocal", "activityDate", "startTimeGMT", "startTime"):
        raw = act.get(key)
        if raw is None:
            continue
        txt = str(raw)
        if len(txt) >= 10:
            return txt[:10]
    return ""


def _activity_name(act: dict[str, Any]) -> str:
    return str(act.get("activityName") or act.get("name") or "")


def _avg_speed(act: dict[str, Any]) -> float | None:
    summary = act.get("summaryDTO") if isinstance(act.get("summaryDTO"), dict) else {}
    raw = act.get("averageSpeed")
    if raw is None:
        raw = summary.get("averageSpeed")
    try:
        val = float(raw)
    except Exception:
        return None
    return val if val > 0 else None


def _pace_min_km(speed_ms: float | None) -> str:
    if not speed_ms or speed_ms <= 0:
        return ""
    sec = 1000.0 / speed_ms
    m = int(sec // 60)
    s = sec % 60
    return f"{m}:{s:05.2f}"


async def main() -> None:
    load_dotenv(".env", override=False)

    async with garmin_mcp_session(essential_only=False) as session:
        raw_profile = await _call_tool_with_retries(session, "get_user_profile", {})
        if _looks_like_error(raw_profile):
            raise RuntimeError(str(raw_profile))
        profile = _safe_json(raw_profile)
        if not isinstance(profile, dict):
            profile = {}

        hr_rest, hr_max = resolve_hr_profile_values(profile)
        run_thr = resolve_running_threshold_pace_sec_per_km(profile)
        lthr_profile, _, _ = _resolve_hr_threshold_bpm(profile)
        ftp = _extract_ftp(profile)
        if ftp is None:
            raw_ftp = await _call_tool_with_retries(session, "get_cycling_ftp", {})
            if not _looks_like_error(raw_ftp):
                ftp = _extract_ftp(_safe_json(raw_ftp))

        rows: list[dict[str, Any]] = []

        # Build a recent-activities index to fill missing date/type metadata.
        activity_index: dict[int, dict[str, Any]] = {}
        day_raw = await _call_tool_with_retries(
            session,
            "get_activities_by_date",
            {
                "start_date": "2026-09-11",
                "end_date": date.today().isoformat(),
                "page": 0,
                "page_size": 200,
            },
        )
        if not _looks_like_error(day_raw):
            for a in _extract_activities_list(day_raw):
                act_id = _activity_id(a)
                if act_id is not None:
                    activity_index[act_id] = a

        for idx, (activity_id, tp) in enumerate(TP_MAP.items(), start=1):
            print(f"[{idx}/{len(TP_MAP)}] activity_id={activity_id}", flush=True)
            row: dict[str, Any] = {
                "activity_id": activity_id,
                "tp": tp,
                "tp_source_status": "manual_unverified",
                "protocol_outcome": "pending_tp_source_evidence",
                "evidence_next": "trainingpeaks_screenshot_or_export_required",
                "error": "",
            }
            try:
                raw_activity = await _call_tool_with_retries(session, "get_activity", {"activity_id": activity_id})
                if _looks_like_error(raw_activity):
                    raise RuntimeError(str(raw_activity))
                activity = _normalize_activity_payload(raw_activity)

                row["fecha"] = _activity_date_iso(activity)
                row["modalidad"] = _activity_type_key(activity)
                row["actividad"] = _activity_name(activity)

                # Fill missing metadata from list endpoint if detail payload is sparse.
                idx_row = activity_index.get(activity_id)
                if idx_row:
                    if not row["fecha"]:
                        row["fecha"] = _activity_date_iso(idx_row)
                    if not row["modalidad"]:
                        row["modalidad"] = _activity_type_key(idx_row)
                    if not row["actividad"]:
                        row["actividad"] = _activity_name(idx_row)

                row["dur_h"] = round(extract_activity_duration_hours(activity), 3)

                spd = _avg_speed(activity)
                row["avg_speed_ms"] = round(spd, 3) if spd else None
                row["avg_pace_min_km"] = _pace_min_km(spd)

                raw_zones = await _call_tool_with_retries(session, "get_activity_hr_in_timezones", {"activity_id": activity_id})
                zones_raw = None if _looks_like_error(raw_zones) else (raw_zones if isinstance(raw_zones, str) else json.dumps(raw_zones))

                raw_details = await _call_tool_with_retries(session, "get_activity_details", {"activity_id": activity_id})
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

                kairos = float(tss)
                ratio = kairos / float(tp) if tp > 0 else None
                row["kairos"] = round(kairos, 3)
                row["method"] = str(method)
                row["delta"] = round(kairos - float(tp), 3)
                row["ratio"] = round(ratio, 3) if ratio is not None else None
                row["is_outlier"] = bool(ratio is not None and (ratio < RATIO_LOW or ratio > RATIO_HIGH))

                if row["modalidad"].lower().find("running") >= 0 and run_thr and run_thr > 0:
                    atleta = {
                        "ftpace_ms": 1000.0 / run_thr,
                        "hr_reposo": hr_rest,
                        "hr_max": hr_max,
                        "lthr": lthr_profile,
                        "sexo": profile.get("userData", {}).get("gender") or profile.get("sex") or "male",
                    }
                    run_out = procesar_actividad(details_raw, atleta)
                    row["run_if"] = round(float(run_out.get("IF") or 0.0), 3)
                    row["run_ngp_ms"] = round(float(run_out.get("ngp_ms") or 0.0), 3)
                    row["run_paused_s"] = int(run_out.get("paused_seconds") or 0)
                    row["run_tempo_det"] = bool(run_out.get("tempo_detector_triggered"))
                    row["run_short_reps_det"] = bool(run_out.get("short_reps_detector_triggered"))

                # Same-day ambiguity check to catch possible cross-activity TP transcription.
                d = row.get("fecha") or ""
                if d:
                    day_raw = await _call_tool_with_retries(
                        session,
                        "get_activities_by_date",
                        {"start_date": d, "end_date": d, "page": 0, "page_size": 100},
                    )
                    same_day_total = 0
                    same_day_same_modality = 0
                    if not _looks_like_error(day_raw):
                        acts = _extract_activities_list(day_raw)
                        same_day_total = len(acts)
                        mod = str(row.get("modalidad") or "").lower()
                        for a in acts:
                            tk = _activity_type_key(a).lower()
                            if tk == mod:
                                same_day_same_modality += 1
                    row["same_day_total"] = same_day_total
                    row["same_day_same_modality"] = same_day_same_modality
                    row["tp_wrong_activity_risk"] = (
                        "high" if same_day_same_modality >= 2 else ("medium" if same_day_total >= 2 else "low")
                    )
            except Exception as ex:
                row["error"] = str(ex)

            rows.append(row)

    outliers = [r for r in rows if r.get("is_outlier")]

    print("\n=== PROTOCOL UPDATE ===")
    print("verify_tp_source outcomes:")
    print("1) verified_and_same_activity -> proceed to code audit only if discrepancy remains")
    print("2) verified_but_wrong_activity_or_transcribed -> fix TP reference, close without code changes")
    print("3) source_unverified -> pending evidence, do not assign blame")

    print("\n=== OUTLIERS (ratio outside [0.75, 1.35]) ===")
    print("| activity_id | fecha | modalidad | TP | Kairos | ratio | delta | dur_h | avg_pace | run_if | tempo_det | short_reps_det | same_day_total | same_day_same_modality | tp_wrong_activity_risk | tp_source_status | protocol_outcome |")
    print("|---:|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|---|---|")
    for r in sorted(outliers, key=lambda x: abs(float(x.get("delta") or 0.0)), reverse=True):
        print(
            "| {activity_id} | {fecha} | {modalidad} | {tp:.3f} | {kairos} | {ratio} | {delta} | {dur_h} | {avg_pace} | {run_if} | {tempo} | {short} | {day_total} | {day_mod} | {risk} | {src} | {outcome} |".format(
                activity_id=r.get("activity_id"),
                fecha=r.get("fecha") or "",
                modalidad=str(r.get("modalidad") or ""),
                tp=float(r.get("tp") or 0.0),
                kairos=("" if r.get("kairos") is None else f"{float(r['kairos']):.3f}"),
                ratio=("" if r.get("ratio") is None else f"{float(r['ratio']):.3f}"),
                delta=("" if r.get("delta") is None else f"{float(r['delta']):.3f}"),
                dur_h=("" if r.get("dur_h") is None else f"{float(r['dur_h']):.3f}"),
                avg_pace=str(r.get("avg_pace_min_km") or ""),
                run_if=("" if r.get("run_if") is None else f"{float(r['run_if']):.3f}"),
                tempo=("" if r.get("run_tempo_det") is None else ("1" if r.get("run_tempo_det") else "0")),
                short=("" if r.get("run_short_reps_det") is None else ("1" if r.get("run_short_reps_det") else "0")),
                day_total=("" if r.get("same_day_total") is None else int(r.get("same_day_total") or 0)),
                day_mod=("" if r.get("same_day_same_modality") is None else int(r.get("same_day_same_modality") or 0)),
                risk=str(r.get("tp_wrong_activity_risk") or ""),
                src=str(r.get("tp_source_status") or ""),
                outcome=str(r.get("protocol_outcome") or ""),
            )
        )

    print("\n=== ALL ENTRIES STATUS ===")
    pending = sum(1 for r in rows if r.get("protocol_outcome") == "pending_tp_source_evidence")
    print(f"rows_total={len(rows)}")
    print(f"outliers={len(outliers)}")
    print(f"pending_tp_source_evidence={pending}")


if __name__ == "__main__":
    asyncio.run(main())
