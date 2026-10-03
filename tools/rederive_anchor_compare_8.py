from __future__ import annotations

import asyncio
import json
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

OLD_THRESHOLD = 116.827367
SUSPECT_IDS = {24430006167, 24484006590, 23478220005}


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


def _anchor_winner_info(activities: list[dict]) -> dict[str, Any] | None:
    preferred: list[tuple[float, float, Any, float, str]] = []
    fallback: list[tuple[float, float, Any, float, str]] = []

    for activity in activities:
        if not isinstance(activity, dict):
            continue
        act_type = lm._resolve_activity_type_for_routing(activity)
        if not lm._is_hike_walk_activity(act_type):
            continue

        details_raw = (
            activity.get("_activity_details_raw")
            or activity.get("activity_details_raw")
            or activity.get("activityDetailsRaw")
        )
        if not details_raw:
            continue

        obs = lm._compute_walk_hike_metabolic_observables(str(details_raw))
        if not obs:
            continue

        moving_seconds = float(obs.get("moving_seconds") or 0.0)
        normalized_equiv = float(obs.get("normalized_equiv_speed_m_min") or 0.0)
        if moving_seconds <= 0 or normalized_equiv <= 0:
            continue

        sustained_weight = min(1.0, moving_seconds / (60.0 * 60.0))
        demanding_score = normalized_equiv * (0.85 + (0.15 * sustained_weight))

        activity_id = activity.get("id") or activity.get("activityId")
        name = str(activity.get("name") or activity.get("activityName") or "")
        item = (demanding_score, normalized_equiv, activity_id, moving_seconds, name)
        fallback.append(item)
        if moving_seconds >= float(lm.WALK_HIKE_THRESHOLD_ANCHOR_MIN_MOVING_SECONDS):
            preferred.append(item)

    if preferred:
        best = max(preferred, key=lambda it: it[0])
        mode = "preferred>=90min"
    elif fallback:
        best = max(fallback, key=lambda it: it[0])
        mode = "fallback<90min"
    else:
        return None

    return {
        "mode": mode,
        "score": float(best[0]),
        "normalized_equiv": float(best[1]),
        "activity_id": best[2],
        "moving_seconds": float(best[3]),
        "name": best[4],
    }


async def main() -> None:
    load_dotenv(".env", override=False)

    activities: list[dict] = []

    async with garmin_mcp_session(essential_only=False) as session:
        for aid in REF_IDS:
            raw_a = await call_tool(session, "get_activity", {"activity_id": aid})
            raw_d = await call_tool(session, "get_activity_details", {"activity_id": aid})
            act = _to_activity(raw_a)
            act["_activity_details_raw"] = raw_d if isinstance(raw_d, str) else json.dumps(raw_d, ensure_ascii=False)
            activities.append(act)

    new_threshold = lm.derive_walk_hike_threshold_speed_m_min_from_activities(activities)
    if new_threshold is None:
        new_threshold = lm.WALK_HIKE_THRESHOLD_SPEED_M_MIN_DEFAULT

    winner = _anchor_winner_info(activities)

    print("=== ANCHOR_REDERIVATION ===")
    print(f"old_threshold_m_min={OLD_THRESHOLD:.6f}")
    print(f"new_threshold_m_min={new_threshold:.6f}")
    print(f"delta_m_min={new_threshold - OLD_THRESHOLD:.6f}")
    if winner:
        print(
            "winner_activity_id={activity_id} | mode={mode} | moving_seconds={moving_seconds:.1f}"
            " | normalized_equiv={normalized_equiv:.6f} | score={score:.6f} | name={name}".format(**winner)
        )

    print("\n=== TABLE_NEW_VS_OLD ===")
    print("fields=id|name|threshold_used|moving_seconds|normalized_speed_m_min|if_new|tss_new|tss_old|delta_tss")

    changes: dict[int, tuple[float, float]] = {}

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

        base = dict(act)
        base["_walk_hike_threshold_speed_m_min"] = float(new_threshold)
        tss_new, _ = lm.estimate_walk_hike_tss(
            base,
            hours=hours,
            hr_zones_raw=None,
            hr_rest_bpm=None,
            hr_max_bpm=None,
            activity_details_raw=details if isinstance(details, str) else None,
            hr_threshold_bpm=None,
            running_threshold_pace_sec_per_km=None,
        )

        old_base = dict(act)
        old_base["_walk_hike_threshold_speed_m_min"] = float(OLD_THRESHOLD)
        tss_old, _ = lm.estimate_walk_hike_tss(
            old_base,
            hours=hours,
            hr_zones_raw=None,
            hr_rest_bpm=None,
            hr_max_bpm=None,
            activity_details_raw=details if isinstance(details, str) else None,
            hr_threshold_bpm=None,
            running_threshold_pace_sec_per_km=None,
        )

        tss_new_v = float(tss_new or 0.0)
        tss_old_v = float(tss_old or 0.0)
        if_new = (norm_speed / float(new_threshold)) if new_threshold > 0 and norm_speed > 0 else 0.0

        name = str(act.get("name") or act.get("activityName") or "").replace("|", "/")
        print(
            "|".join(
                [
                    str(aid),
                    name,
                    f"{new_threshold:.6f}",
                    f"{moving_seconds:.1f}",
                    f"{norm_speed:.6f}",
                    f"{if_new:.6f}",
                    f"{tss_new_v:.6f}",
                    f"{tss_old_v:.6f}",
                    f"{(tss_new_v - tss_old_v):.6f}",
                ]
            )
        )

        if aid in SUSPECT_IDS:
            changes[aid] = (tss_old_v, tss_new_v)

    print("\n=== SUSPECT_CASES_DELTA ===")
    lowered_all = True
    for aid in sorted(SUSPECT_IDS):
        old_v, new_v = changes.get(aid, (0.0, 0.0))
        lowered = new_v < old_v
        lowered_all = lowered_all and lowered
        print(
            f"id={aid} | tss_old={old_v:.6f} | tss_new={new_v:.6f} | lowered={str(lowered).lower()}"
        )
    print(f"all_three_lowered={str(lowered_all).lower()}")


if __name__ == "__main__":
    asyncio.run(main())
