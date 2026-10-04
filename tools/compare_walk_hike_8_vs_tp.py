from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from agent import load_metrics as lm
from agent.mcp_client import call_tool, garmin_mcp_session

REF_IDS = [
    23468464527,
    23478220005,
    23829149525,
    24013969366,
    24430006167,
    24484006590,
    24492874850,
    24502427862,
]

# Verified TP references from docs/proximos_pasos.md (verified_and_same_activity rows).
TP_DOCS_VERIFIED: dict[int, dict[str, float]] = {
    23468464527: {"tp_rTSS": 4.0, "tp_hrTSS": 84.0},
    23478220005: {"tp_rTSS": 4.0, "tp_hrTSS": 87.0},
    23829149525: {"tp_rTSS": 13.0, "tp_hrTSS": 154.0},
    24013969366: {"tp_rTSS": 7.0, "tp_hrTSS": 61.0},
    24430006167: {"tp_rTSS": 4.0, "tp_hrTSS": 25.0},
    24484006590: {"tp_rTSS": 10.0, "tp_hrTSS": 30.0},
    24492874850: {"tp_rTSS": 18.0, "tp_hrTSS": 103.0},
    24502427862: {"tp_rTSS": 3.0, "tp_hrTSS": 26.0},
}


def _safe_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return raw
    return raw


def _to_activity(payload: Any) -> dict:
    obj = _safe_json(payload)
    if isinstance(obj, list) and obj:
        obj = obj[0]
    if isinstance(obj, dict) and isinstance(obj.get("activity"), dict):
        obj = obj["activity"]
    return obj if isinstance(obj, dict) else {}


def _to_details_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


async def main() -> None:
    load_dotenv(".env", override=False)

    activities: list[dict[str, Any]] = []

    async with garmin_mcp_session(essential_only=False) as session:
        for aid in REF_IDS:
            raw_a = await call_tool(session, "get_activity", {"activity_id": aid})
            raw_d = await call_tool(session, "get_activity_details", {"activity_id": aid})
            act = _to_activity(raw_a)
            act["_activity_details_raw"] = _to_details_text(raw_d)
            activities.append(act)

    threshold = lm.derive_walk_hike_threshold_speed_m_min_from_activities(activities)
    if threshold is None:
        threshold = lm.WALK_HIKE_THRESHOLD_SPEED_M_MIN_DEFAULT

    rows: list[dict[str, Any]] = []
    abs_errors_r: list[float] = []
    ratios_r: list[float] = []
    abs_errors_hr: list[float] = []
    ratios_hr: list[float] = []

    print("=== WALK_HIKE_8_VS_TP ===")
    print(f"derived_threshold_m_min={threshold:.6f}")

    for aid, act in zip(REF_IDS, activities):
        details = act.get("_activity_details_raw")
        obs = lm._compute_walk_hike_metabolic_observables(details) if details else None
        moving_seconds = float(obs.get("moving_seconds") or 0.0) if obs else 0.0
        norm_speed = float(obs.get("normalized_equiv_speed_m_min") or 0.0) if obs else 0.0

        hours = lm._resolve_activity_duration_hours(
            act,
            hr_zones_raw=None,
            activity_details_raw=details if isinstance(details, str) else None,
        )

        act_for_tss = dict(act)
        act_for_tss["_walk_hike_threshold_speed_m_min"] = float(threshold)
        model_tss, label = lm.estimate_walk_hike_tss(
            act_for_tss,
            hours=hours,
            hr_zones_raw=None,
            hr_rest_bpm=None,
            hr_max_bpm=None,
            activity_details_raw=details if isinstance(details, str) else None,
            hr_threshold_bpm=None,
            running_threshold_pace_sec_per_km=None,
        )

        tp_tss_raw = lm._extract_training_load_tss(act)
        tp_doc = TP_DOCS_VERIFIED.get(aid, {})
        tp_rtss = float(tp_doc["tp_rTSS"]) if "tp_rTSS" in tp_doc else None
        tp_hrtss = float(tp_doc["tp_hrTSS"]) if "tp_hrTSS" in tp_doc else None

        tp_payload = float(tp_tss_raw) if tp_tss_raw is not None else None
        tp_source = "activity_payload" if tp_payload is not None else "docs_verified"

        model_tss_v = float(model_tss or 0.0)
        delta_r = model_tss_v - float(tp_rtss) if tp_rtss is not None else None
        ratio_r = (model_tss_v / float(tp_rtss)) if tp_rtss not in (None, 0) else None
        delta_hr = model_tss_v - float(tp_hrtss) if tp_hrtss is not None else None
        ratio_hr = (model_tss_v / float(tp_hrtss)) if tp_hrtss not in (None, 0) else None

        if delta_r is not None:
            abs_errors_r.append(abs(float(delta_r)))
        if ratio_r is not None:
            ratios_r.append(float(ratio_r))
        if delta_hr is not None:
            abs_errors_hr.append(abs(float(delta_hr)))
        if ratio_hr is not None:
            ratios_hr.append(float(ratio_hr))

        if tp_rtss is not None and tp_hrtss is not None:
            lo = min(tp_rtss, tp_hrtss)
            hi = max(tp_rtss, tp_hrtss)
            if model_tss_v < lo:
                bracket_position = "below_both"
            elif model_tss_v > hi:
                bracket_position = "above_both"
            elif model_tss_v == lo:
                bracket_position = "at_lower"
            elif model_tss_v == hi:
                bracket_position = "at_upper"
            else:
                bracket_position = "between_rtss_hrtss"
        else:
            bracket_position = "unknown"

        name = str(act.get("name") or act.get("activityName") or "").replace("|", "/")
        act_type = str(act.get("type") or act.get("activityType") or "")
        if_val = (norm_speed / threshold) if threshold > 0 and norm_speed > 0 else 0.0

        row = {
            "activity_id": aid,
            "type": act_type,
            "name": name,
            "moving_seconds": moving_seconds,
            "normalized_equiv_speed_m_min": norm_speed,
            "if_model": if_val,
            "tss_model": model_tss_v,
            "tp_rTSS_docs": tp_rtss if tp_rtss is not None else "",
            "tp_hrTSS_docs": tp_hrtss if tp_hrtss is not None else "",
            "tp_payload_raw": tp_payload if tp_payload is not None else "",
            "tp_source": tp_source,
            "delta_model_minus_rTSS": float(delta_r) if delta_r is not None else "",
            "ratio_model_over_rTSS": float(ratio_r) if ratio_r is not None else "",
            "delta_model_minus_hrTSS": float(delta_hr) if delta_hr is not None else "",
            "ratio_model_over_hrTSS": float(ratio_hr) if ratio_hr is not None else "",
            "bracket_position_vs_tp": bracket_position,
            "label": str(label or ""),
        }
        rows.append(row)

        print(
            f"id={aid} | type={act_type} | tss_model={model_tss_v:.3f}"
            f" | tp_rTSS={(float(tp_rtss) if tp_rtss is not None else 'NA')}"
            f" | tp_hrTSS={(float(tp_hrtss) if tp_hrtss is not None else 'NA')}"
            f" | delta_r={(float(delta_r) if delta_r is not None else 'NA')}"
            f" | delta_hr={(float(delta_hr) if delta_hr is not None else 'NA')}"
            f" | bracket={bracket_position}"
        )

    mae_r = (sum(abs_errors_r) / len(abs_errors_r)) if abs_errors_r else 0.0
    mean_ratio_r = (sum(ratios_r) / len(ratios_r)) if ratios_r else 0.0
    mae_hr = (sum(abs_errors_hr) / len(abs_errors_hr)) if abs_errors_hr else 0.0
    mean_ratio_hr = (sum(ratios_hr) / len(ratios_hr)) if ratios_hr else 0.0
    print(
        "summary:"
        f" compared_r={len(abs_errors_r)} | mae_r={mae_r:.4f} | mean_ratio_r={mean_ratio_r:.4f}"
        f" | compared_hr={len(abs_errors_hr)} | mae_hr={mae_hr:.4f} | mean_ratio_hr={mean_ratio_hr:.4f}"
    )

    out_dir = Path("docs") / "walk_hike_temporal_profile"
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "tp_comparison_8_activities.csv", rows)
    _write_csv(
        out_dir / "tp_comparison_8_summary.csv",
        [
            {
                "derived_threshold_m_min": float(threshold),
                "compared_count_rtss": len(abs_errors_r),
                "mae_rtss": float(mae_r),
                "mean_ratio_rtss": float(mean_ratio_r),
                "compared_count_hrtss": len(abs_errors_hr),
                "mae_hrtss": float(mae_hr),
                "mean_ratio_hrtss": float(mean_ratio_hr),
            }
        ],
    )
    print(f"artifacts_dir={out_dir.as_posix()}")


if __name__ == "__main__":
    asyncio.run(main())