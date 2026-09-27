from __future__ import annotations

import asyncio
import csv
import json
import sys
from datetime import date
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

INPUT_CSV = ROOT / "docs" / "tss_independiente_junio_a_septiembre_hasta_2026-09-16.csv"
START_DATE = "2026-09-11"
END_DATE = date.today().isoformat()
PAGE_SIZE = 100
MAX_PAGES = 8
CALL_TIMEOUT_S = 60.0


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


async def _call_tool_with_retries(
    session: Any,
    tool_name: str,
    args: dict[str, Any],
    retries: int = 2,
    delay_s: float = 2.5,
) -> Any:
    raw = await _call_tool_safe(session, tool_name, args)
    if not _looks_like_error(raw):
        return raw

    raw_text = str(raw).lower()
    retryable = "504" in raw_text or "timeout" in raw_text or "retryable" in raw_text
    if not retryable:
        return raw

    for _ in range(retries):
        await asyncio.sleep(delay_s)
        raw = await _call_tool_safe(session, tool_name, args)
        if not _looks_like_error(raw):
            return raw
    return raw


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


def _normalize_activity_payload(raw: Any) -> dict[str, Any]:
    payload = _safe_json(raw)
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict) and isinstance(payload.get("activity"), dict):
        payload = payload["activity"]
    if not isinstance(payload, dict):
        raise RuntimeError("get_activity returned non-dict payload")
    return payload


def _activity_id_from_node(node: dict[str, Any]) -> int | None:
    raw = node.get("activityId")
    if raw is None:
        raw = node.get("activity_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _activity_date_from_node(node: dict[str, Any]) -> str:
    for key in (
        "activityDate",
        "startTimeLocal",
        "startTimeGMT",
        "startTime",
        "beginTimestamp",
    ):
        raw = node.get(key)
        if raw is None:
            continue
        txt = str(raw)
        if len(txt) >= 10:
            return txt[:10]
    return ""


def _activity_name(node: dict[str, Any]) -> str:
    for key in ("activityName", "name", "actividad"):
        raw = node.get(key)
        if raw:
            return str(raw)
    return ""


def _activity_modality(node: dict[str, Any]) -> str:
    for key in ("activityType", "typeKey", "modalidad"):
        raw = node.get(key)
        if raw:
            if isinstance(raw, dict):
                type_key = raw.get("typeKey")
                if type_key:
                    return str(type_key)
            return str(raw)
    at = node.get("activityTypeDTO")
    if isinstance(at, dict):
        for key in ("typeKey", "type", "parentTypeId"):
            raw = at.get(key)
            if raw:
                return str(raw)
    return ""


def _load_tp_lookup() -> dict[int, dict[str, Any]]:
    tp: dict[int, dict[str, Any]] = {}
    with INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw_id = row.get("activity_id")
            if not raw_id:
                continue
            try:
                act_id = int(raw_id)
            except Exception:
                continue
            tp_val: float | None
            try:
                tp_val = float(row.get("tp_local") or "")
            except Exception:
                tp_val = None
            tp[act_id] = {
                "tp_local": tp_val,
                "tp_unit": str(row.get("tp_unit") or ""),
                "modalidad_tp": str(row.get("modalidad") or ""),
                "actividad_tp": str(row.get("actividad") or ""),
                "fecha_tp": str(row.get("fecha") or ""),
            }
    return tp


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value)


async def _fetch_activities_in_range(session: Any) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for page in range(MAX_PAGES):
        raw = await _call_tool_safe(
            session,
            "get_activities_by_date",
            {
                "start_date": START_DATE,
                "end_date": END_DATE,
                "page": page,
                "page_size": PAGE_SIZE,
            },
        )
        if _looks_like_error(raw):
            print(f"[WARN] get_activities_by_date page={page} error={raw}", flush=True)
            break

        payload = _safe_json(raw)
        acts = _extract_activities_list(payload)
        if not acts:
            break

        new_rows = 0
        for act in acts:
            if not isinstance(act, dict):
                continue
            act_id = _activity_id_from_node(act)
            if act_id is None or act_id in seen_ids:
                continue
            seen_ids.add(act_id)
            new_rows += 1
            all_rows.append(act)

        print(f"[LIST] page={page} got={len(acts)} new={new_rows} total={len(all_rows)}", flush=True)

        has_more = False
        if isinstance(payload, dict):
            has_more = bool(payload.get("has_more"))
            count_page = payload.get("count")
            if isinstance(count_page, int) and count_page < PAGE_SIZE:
                has_more = False

        if not has_more and len(acts) < PAGE_SIZE:
            break

    return all_rows


async def main() -> None:
    load_dotenv(".env", override=False)
    tp_lookup = _load_tp_lookup()
    out_rows: list[dict[str, Any]] = []

    async with garmin_mcp_session(essential_only=False) as session:
        raw_profile = await _call_tool_safe(session, "get_user_profile", {})
        if _looks_like_error(raw_profile):
            raise RuntimeError(str(raw_profile).strip())

        profile = _safe_json(raw_profile)
        if not isinstance(profile, dict):
            profile = {}

        hr_rest, hr_max = resolve_hr_profile_values(profile)
        run_thr = resolve_running_threshold_pace_sec_per_km(profile)
        lthr_profile, _, _ = _resolve_hr_threshold_bpm(profile)

        ftp = _extract_ftp(profile)
        if ftp is None:
            raw_ftp = await _call_tool_safe(session, "get_cycling_ftp", {})
            if not _looks_like_error(raw_ftp):
                ftp = _extract_ftp(_safe_json(raw_ftp))

        activities = await _fetch_activities_in_range(session)
        # Sort by date descending (and ID descending as tiebreaker)
        activities.sort(key=lambda a: (_activity_date_from_node(a), _activity_id_from_node(a) or 0), reverse=True)

        for idx, act_ref in enumerate(activities, start=1):
            act_id = _activity_id_from_node(act_ref)
            if act_id is None:
                continue

            out: dict[str, Any] = {
                "fecha": _activity_date_from_node(act_ref),
                "activity_id": act_id,
                "modalidad": _activity_modality(act_ref),
                "actividad": _activity_name(act_ref),
                "tp_local": None,
                "tp_unit": "",
                "kairos_actual": None,
                "calc_method": "",
                "delta": None,
                "ratio": None,
                "error": "",
                "tp_missing": True,
            }

            tp_row = tp_lookup.get(act_id)
            if tp_row:
                out["tp_local"] = tp_row.get("tp_local")
                out["tp_unit"] = str(tp_row.get("tp_unit") or "")
                out["tp_missing"] = tp_row.get("tp_local") is None

            print(f"[{idx}/{len(activities)}] activity_id={act_id}", flush=True)

            try:
                raw_activity = await _call_tool_safe(session, "get_activity", {"activity_id": act_id})
                if _looks_like_error(raw_activity):
                    raise RuntimeError(str(raw_activity).strip())
                activity = _normalize_activity_payload(raw_activity)

                if not out["modalidad"]:
                    out["modalidad"] = _activity_modality(activity)
                if not out["actividad"]:
                    out["actividad"] = _activity_name(activity)
                if not out["fecha"]:
                    out["fecha"] = _activity_date_from_node(activity)

                raw_zones = await _call_tool_safe(session, "get_activity_hr_in_timezones", {"activity_id": act_id})
                zones_raw = (
                    None
                    if _looks_like_error(raw_zones)
                    else (raw_zones if isinstance(raw_zones, str) and raw_zones.strip() else None)
                )

                raw_details = await _call_tool_with_retries(
                    session,
                    "get_activity_details",
                    {"activity_id": act_id},
                )
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

                tss_val = float(tss_new or 0.0)
                out["kairos_actual"] = round(tss_val, 6)
                out["calc_method"] = str(method_new or "")
                if out["tp_local"] is not None:
                    tp = float(out["tp_local"])
                    out["delta"] = round(tss_val - tp, 6)
                    out["ratio"] = round((tss_val / tp), 6) if tp > 0 else None
            except Exception as ex:
                out["error"] = str(ex)

            out_rows.append(out)

    ok_with_tp = [r for r in out_rows if not r["error"] and r["delta"] is not None]
    mae = fmean(abs(float(r["delta"])) for r in ok_with_tp) if ok_with_tp else None
    bias = fmean(float(r["delta"]) for r in ok_with_tp) if ok_with_tp else None
    ratio_mean = fmean(float(r["ratio"]) for r in ok_with_tp if r["ratio"] is not None) if ok_with_tp else None

    missing_tp = sum(1 for r in out_rows if r["tp_local"] is None)
    calc_errors = sum(1 for r in out_rows if r["error"])

    print("\n=== RESUMEN ===")
    print(f"range={START_DATE}..{END_DATE}")
    print(f"formula_version={TSS_FORMULA_VERSION}")
    print(f"rows_total={len(out_rows)}")
    print(f"rows_with_tp={len(out_rows) - missing_tp}")
    print(f"rows_missing_tp={missing_tp}")
    print(f"rows_calc_error={calc_errors}")
    if mae is not None:
        print(f"mae_tp_rows={mae:.6f}")
        print(f"bias_tp_rows={bias:.6f}")
        print(f"ratio_mean_tp_rows={ratio_mean:.6f}")

    print("\n=== TABLA (DESDE 2026-09-11) ===")
    print("| fecha | activity_id | modalidad | actividad | TP | Kairos_actual | metodo | delta | ratio | tp_falta | error |")
    print("|---|---:|---|---|---:|---:|---|---:|---:|---:|---|")
    for r in out_rows:
        print(
            "| {fecha} | {activity_id} | {modalidad} | {actividad} | {tp} | {kairos} | {metodo} | {delta} | {ratio} | {tp_falta} | {error} |".format(
                fecha=r["fecha"],
                activity_id=r["activity_id"],
                modalidad=str(r["modalidad"] or ""),
                actividad=str(r["actividad"] or "").replace("|", "/"),
                tp=_fmt(r["tp_local"], 3),
                kairos=_fmt(r["kairos_actual"], 3),
                metodo=str(r["calc_method"] or ""),
                delta=_fmt(r["delta"], 3),
                ratio=_fmt(r["ratio"], 3),
                tp_falta="1" if r["tp_local"] is None else "0",
                error=str(r["error"] or "").replace("|", "/"),
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
