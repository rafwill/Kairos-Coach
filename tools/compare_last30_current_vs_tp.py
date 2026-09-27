from __future__ import annotations

import asyncio
import csv
import json
import sys
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
LAST_N = 30
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


def _load_last_n() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                tp = float(row.get("tp_local") or "")
                act_id = int(row.get("activity_id") or "")
            except Exception:
                continue
            rows.append(
                {
                    "fecha": str(row.get("fecha") or ""),
                    "activity_id": act_id,
                    "modalidad": str(row.get("modalidad") or ""),
                    "actividad": str(row.get("actividad") or ""),
                    "tp_local": tp,
                    "tp_unit": str(row.get("tp_unit") or ""),
                }
            )

    rows.sort(key=lambda r: r["fecha"], reverse=True)
    return rows[:LAST_N]


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value)


async def main() -> None:
    load_dotenv(".env", override=False)
    rows = _load_last_n()
    if not rows:
        raise RuntimeError("No rows loaded from TP reference CSV")

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

        for idx, item in enumerate(rows, start=1):
            activity_id = int(item["activity_id"])
            print(f"[{idx}/{len(rows)}] activity_id={activity_id}", flush=True)
            out = dict(item)
            out["kairos_actual"] = None
            out["calc_method"] = ""
            out["delta"] = None
            out["ratio"] = None
            out["error"] = ""

            try:
                raw_activity = await _call_tool_safe(session, "get_activity", {"activity_id": activity_id})
                if _looks_like_error(raw_activity):
                    raise RuntimeError(str(raw_activity).strip())

                activity = _safe_json(raw_activity)
                if isinstance(activity, list) and activity:
                    activity = activity[0]
                if isinstance(activity, dict) and isinstance(activity.get("activity"), dict):
                    activity = activity["activity"]
                if not isinstance(activity, dict):
                    raise RuntimeError("get_activity returned non-dict payload")

                raw_zones = await _call_tool_safe(session, "get_activity_hr_in_timezones", {"activity_id": activity_id})
                zones_raw = (
                    None
                    if _looks_like_error(raw_zones)
                    else (raw_zones if isinstance(raw_zones, str) and raw_zones.strip() else None)
                )

                raw_details = await _call_tool_safe(session, "get_activity_details", {"activity_id": activity_id})
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
                tp = float(out["tp_local"])
                out["kairos_actual"] = round(tss_val, 6)
                out["calc_method"] = str(method_new or "")
                out["delta"] = round(tss_val - tp, 6)
                out["ratio"] = round((tss_val / tp), 6) if tp > 0 else None
            except Exception as ex:
                out["error"] = str(ex)

            out_rows.append(out)

    ok_rows = [r for r in out_rows if not r["error"] and r["delta"] is not None]
    mae = fmean(abs(float(r["delta"])) for r in ok_rows) if ok_rows else None
    bias = fmean(float(r["delta"]) for r in ok_rows) if ok_rows else None
    ratio_mean = fmean(float(r["ratio"]) for r in ok_rows if r["ratio"] is not None) if ok_rows else None

    print("\n=== RESUMEN ===")
    print(f"formula_version={TSS_FORMULA_VERSION}")
    print(f"rows_total={len(out_rows)}")
    print(f"rows_ok={len(ok_rows)}")
    print(f"rows_error={len(out_rows)-len(ok_rows)}")
    if mae is not None:
        print(f"mae={mae:.6f}")
        print(f"bias={bias:.6f}")
        print(f"ratio_mean={ratio_mean:.6f}")

    print("\n=== TABLA (ULTIMAS 30) ===")
    print("| fecha | activity_id | modalidad | TP | Kairos_actual | metodo | delta | ratio | error |")
    print("|---|---:|---|---:|---:|---|---:|---:|---|")
    for r in out_rows:
        print(
            "| {fecha} | {activity_id} | {modalidad} | {tp} | {kairos} | {metodo} | {delta} | {ratio} | {error} |".format(
                fecha=r["fecha"],
                activity_id=r["activity_id"],
                modalidad=r["modalidad"],
                tp=_fmt(float(r["tp_local"]), 3),
                kairos=_fmt(r["kairos_actual"], 3),
                metodo=str(r["calc_method"] or ""),
                delta=_fmt(r["delta"], 3),
                ratio=_fmt(r["ratio"], 3),
                error=str(r["error"] or ""),
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
