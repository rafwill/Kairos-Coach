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


def _as_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_reported_elevation_gain_m(activity: dict) -> float | None:
    summary = activity.get("summaryDTO") if isinstance(activity.get("summaryDTO"), dict) else {}
    for key in ("elevationGain", "elevation_gain", "totalAscent", "total_ascent", "elev_gain"):
        v = _as_float(activity.get(key))
        if v is None:
            v = _as_float(summary.get(key))
        if v is not None and v >= 0:
            return v
    return None


def _computed_elevation_gain_m(samples: list[tuple[float, float, float, float | None]]) -> float:
    if len(samples) < 2:
        return 0.0
    gain = 0.0
    for i in range(1, len(samples)):
        de = float(samples[i][2]) - float(samples[i - 1][2])
        if de > 0:
            gain += de
    return max(0.0, gain)


def _time_bucket_breakdown(samples: list[tuple[float, float, float, float | None]]) -> dict[str, float]:
    buckets = {
        "stop_exact_0": 0.0,
        "slow_0_0_1": 0.0,
        "slow_0_1_0_25": 0.0,
        "moving_ge_0_25": 0.0,
    }
    total = 0.0

    for i in range(1, len(samples)):
        t_prev = float(samples[i - 1][0])
        t_cur = float(samples[i][0])
        dt = t_cur - t_prev
        if dt <= 0:
            continue
        dt = min(30.0, dt)

        speed = max(0.0, float(samples[i][1]))
        total += dt

        if speed == 0.0:
            buckets["stop_exact_0"] += dt
        elif speed < 0.1:
            buckets["slow_0_0_1"] += dt
        elif speed < 0.25:
            buckets["slow_0_1_0_25"] += dt
        else:
            buckets["moving_ge_0_25"] += dt

    out = {"total_seconds": total}
    out.update(buckets)
    for k, v in buckets.items():
        out[f"{k}_pct"] = (100.0 * v / total) if total > 0 else 0.0
    out["non_moving_seconds"] = buckets["stop_exact_0"] + buckets["slow_0_0_1"] + buckets["slow_0_1_0_25"]
    out["non_moving_pct"] = (100.0 * out["non_moving_seconds"] / total) if total > 0 else 0.0
    return out


async def main() -> None:
    load_dotenv(".env", override=False)

    print("=== PLA_DE_BERET_ELEVATION ===")
    print("id=23478220005")

    async with garmin_mcp_session(essential_only=False) as session:
        rows: list[dict[str, Any]] = []

        for aid in REF_IDS:
            raw_a = await call_tool(session, "get_activity", {"activity_id": aid})
            raw_d = await call_tool(session, "get_activity_details", {"activity_id": aid})

            act = _to_activity(raw_a)
            name = str(act.get("name") or act.get("activityName") or "")
            samples = lm._extract_walk_samples_from_activity_details(raw_d if isinstance(raw_d, str) else json.dumps(raw_d, ensure_ascii=False))
            breakdown = _time_bucket_breakdown(samples)

            reported_gain = _extract_reported_elevation_gain_m(act)
            computed_gain = _computed_elevation_gain_m(samples)

            row = {
                "id": aid,
                "name": name,
                "reported_gain_m": reported_gain,
                "computed_gain_m": computed_gain,
                **breakdown,
            }
            rows.append(row)

            if aid == 23478220005:
                print(
                    f"reported_gain_m={reported_gain if reported_gain is not None else 'NA'}"
                    f" | computed_gain_m={computed_gain:.3f}"
                )

    print("\n=== TOURISM_STOP_VS_SLOW ===")
    print("fields=id|name|total_s|non_moving_s|non_moving_pct|stop_0_s|stop_0_pct|slow_0_0.1_s|slow_0_0.1_pct|slow_0.1_0.25_s|slow_0.1_0.25_pct|moving_ge_0.25_s|moving_ge_0.25_pct")

    for r in rows:
        if "turismo" not in str(r["name"]).lower():
            continue
        print(
            "|".join(
                [
                    str(r["id"]),
                    str(r["name"]).replace("|", "/"),
                    f"{r['total_seconds']:.1f}",
                    f"{r['non_moving_seconds']:.1f}",
                    f"{r['non_moving_pct']:.3f}",
                    f"{r['stop_exact_0']:.1f}",
                    f"{r['stop_exact_0_pct']:.3f}",
                    f"{r['slow_0_0_1']:.1f}",
                    f"{r['slow_0_0_1_pct']:.3f}",
                    f"{r['slow_0_1_0_25']:.1f}",
                    f"{r['slow_0_1_0_25_pct']:.3f}",
                    f"{r['moving_ge_0_25']:.1f}",
                    f"{r['moving_ge_0_25_pct']:.3f}",
                ]
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
