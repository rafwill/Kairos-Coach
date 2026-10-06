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

# Operational safeguard: when this workflow captures fresh fixtures in
# fixtures/real_payloads, stage and commit those payload files in the same
# commit as the dependent analysis/docs changes (do not leave them only on disk).


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


async def main() -> None:
    load_dotenv(".env", override=False)

    activities: list[dict] = []

    async with garmin_mcp_session(essential_only=False) as session:
        for aid in REF_IDS:
            raw_a = await call_tool(session, "get_activity", {"activity_id": aid})
            raw_d = await call_tool(session, "get_activity_details", {"activity_id": aid})
            act = _to_activity(raw_a)
            act["_activity_details_raw"] = raw_d
            activities.append(act)

    threshold = lm.derive_walk_hike_threshold_speed_m_min_from_activities(activities)
    if threshold is None:
        threshold = lm.WALK_HIKE_THRESHOLD_SPEED_M_MIN_DEFAULT

    print("=== WALK_HIKE_8_VALIDATION ===")
    print(f"derived_threshold_m_min={threshold:.6f}")
    print("fields=id|type|name|threshold_m_min|moving_seconds|normalized_speed_m_min|IF|TSS|label")

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
        act_for_tss["_walk_hike_threshold_speed_m_min"] = threshold

        tss, label = lm.estimate_walk_hike_tss(
            act_for_tss,
            hours=hours,
            hr_zones_raw=None,
            hr_rest_bpm=None,
            hr_max_bpm=None,
            activity_details_raw=details if isinstance(details, str) else None,
            hr_threshold_bpm=None,
            running_threshold_pace_sec_per_km=None,
        )

        if_val = (norm_speed / threshold) if threshold > 0 and norm_speed > 0 else 0.0
        act_type = str(act.get("type") or act.get("activityType") or "")
        name = str(act.get("name") or act.get("activityName") or "")

        print(
            "|".join(
                [
                    str(aid),
                    act_type,
                    name.replace("|", "/"),
                    f"{threshold:.6f}",
                    f"{moving_seconds:.1f}",
                    f"{norm_speed:.6f}",
                    f"{if_val:.6f}",
                    f"{float(tss or 0.0):.6f}",
                    str(label or ""),
                ]
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
