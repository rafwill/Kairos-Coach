from __future__ import annotations

import argparse
import asyncio
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

from dotenv import load_dotenv

from agent import load_metrics as lm
from agent.mcp_client import call_tool, garmin_mcp_session


@dataclass
class SecPoint:
    t_sec: int
    speed_mps: float
    grade: float
    equiv_speed_m_min: float
    if_smoothed: float


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


def _parse_ids(raw_ids: str) -> list[int]:
    out: list[int] = []
    for part in raw_ids.split(","):
        token = part.strip()
        if not token:
            continue
        out.append(int(token))
    return out


def _extract_sec_points(activity_details_raw: str, threshold_m_min: float) -> list[SecPoint]:
    samples = lm._extract_walk_samples_from_activity_details(activity_details_raw)
    if len(samples) < 5:
        return []

    points_raw: list[tuple[int, float, float, float]] = []
    prev_dist: float | None = None

    for idx in range(1, len(samples)):
        t_prev, _, e_prev, _ = samples[idx - 1]
        t_cur, speed_mps, e_cur, dist_cur = samples[idx]

        dt = max(0.0, float(t_cur - t_prev))
        if dt <= 0:
            continue
        dt = min(dt, 30.0)

        if speed_mps < lm.WALK_HIKE_MIN_MOVING_SPEED_MS:
            prev_dist = dist_cur if dist_cur is not None else prev_dist
            continue

        delta_dist = speed_mps * dt
        if dist_cur is not None and prev_dist is not None:
            inferred = dist_cur - prev_dist
            if inferred > 0:
                delta_dist = inferred
        if delta_dist <= 0:
            prev_dist = dist_cur if dist_cur is not None else prev_dist
            continue

        grade = (float(e_cur) - float(e_prev)) / max(0.1, float(delta_dist))
        grade = max(lm.WALK_HIKE_MINETTI_GRADE_MIN, min(lm.WALK_HIKE_MINETTI_GRADE_MAX, grade))

        vo2, vo2_source = lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, grade)
        equiv = lm._walk_equivalent_flat_speed_from_vo2_m_min(
            vo2,
            vo2_source,
            speed_mps=speed_mps,
            grade=grade,
        )

        reps = max(1, int(round(dt)))
        t_start = int(round(max(0.0, t_cur - dt)))
        for offset in range(reps):
            points_raw.append((t_start + offset, float(speed_mps), float(grade), float(equiv)))

        prev_dist = dist_cur if dist_cur is not None else prev_dist

    if not points_raw:
        return []

    points_raw.sort(key=lambda x: x[0])
    smoothed = lm._moving_average([p[3] for p in points_raw], lm.WALK_HIKE_EQUIV_SMOOTH_WINDOW_S)

    out: list[SecPoint] = []
    for (t_sec, speed, grade, equiv), equiv_sm in zip(points_raw, smoothed):
        if_sm = max(0.0, float(equiv_sm) / threshold_m_min) if threshold_m_min > 0 else 0.0
        out.append(SecPoint(t_sec=t_sec, speed_mps=speed, grade=grade, equiv_speed_m_min=equiv, if_smoothed=if_sm))
    return out


def _find_streaks(points: list[SecPoint], threshold_if: float) -> list[dict[str, Any]]:
    streaks: list[dict[str, Any]] = []
    i = 0
    n = len(points)
    while i < n:
        if points[i].if_smoothed < threshold_if:
            i += 1
            continue

        j = i
        while j + 1 < n and points[j + 1].if_smoothed >= threshold_if and points[j + 1].t_sec == points[j].t_sec + 1:
            j += 1

        seg = points[i : j + 1]
        speeds = [p.speed_mps for p in seg]
        ifs = [p.if_smoothed for p in seg]
        ds = [abs(speeds[k + 1] - speeds[k]) for k in range(len(speeds) - 1)]
        streaks.append(
            {
                "start_sec": seg[0].t_sec,
                "end_sec": seg[-1].t_sec,
                "duration_sec": len(seg),
                "if_smoothed_max": max(ifs),
                "if_smoothed_mean": fmean(ifs),
                "speed_mean_mps": fmean(speeds),
                "speed_std_mps": pstdev(speeds) if len(speeds) > 1 else 0.0,
                "speed_delta_abs_mean_mps": fmean(ds) if ds else 0.0,
                "speed_delta_abs_max_mps": max(ds) if ds else 0.0,
                "points": seg,
            }
        )
        i = j + 1

    streaks.sort(
        key=lambda s: (float(s["if_smoothed_max"]), float(s["if_smoothed_mean"]), int(s["duration_sec"])),
        reverse=True,
    )
    return streaks


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
    parser = argparse.ArgumentParser(description="Extract second-by-second speed traces for top smoothed IF streaks.")
    parser.add_argument("--target-ids", type=str, required=True, help="Comma-separated activity IDs")
    parser.add_argument("--if-threshold", type=float, default=1.0)
    parser.add_argument("--top-n", type=int, default=3)
    args = parser.parse_args()

    target_ids = _parse_ids(args.target_ids)

    load_dotenv(".env", override=False)

    activities: dict[int, dict] = {}
    details: dict[int, str] = {}

    async with garmin_mcp_session(essential_only=False) as session:
        for aid in target_ids:
            raw_a = await call_tool(session, "get_activity", {"activity_id": aid})
            raw_d = await call_tool(session, "get_activity_details", {"activity_id": aid})
            activities[aid] = _to_activity(raw_a)
            details[aid] = _to_details_text(raw_d)

    derivation_activities: list[dict] = []
    for aid in target_ids:
        act = dict(activities[aid])
        act["_activity_details_raw"] = details[aid]
        derivation_activities.append(act)
    threshold = lm.derive_walk_hike_threshold_speed_m_min_from_activities(derivation_activities)
    if threshold is None:
        threshold = lm.WALK_HIKE_THRESHOLD_SPEED_M_MIN_DEFAULT

    out_dir = Path("docs") / "walk_hike_temporal_profile"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=== TOP_SMOOTHED_STREAK_SPEEDS ===")
    print(f"threshold_m_min={threshold:.6f}")
    print(f"if_threshold={args.if_threshold:.3f} | top_n={args.top_n}")

    for aid in target_ids:
        act = activities[aid]
        name = str(act.get("name") or act.get("activityName") or "")
        points = _extract_sec_points(details[aid], threshold)
        streaks = _find_streaks(points, args.if_threshold)
        top = streaks[: args.top_n]

        print(f"id={aid} | name={name} | streaks_found={len(streaks)}")

        summary_rows: list[dict[str, Any]] = []
        second_rows: list[dict[str, Any]] = []
        for rank, s in enumerate(top, start=1):
            summary_rows.append(
                {
                    "rank": rank,
                    "start_sec": s["start_sec"],
                    "end_sec": s["end_sec"],
                    "duration_sec": s["duration_sec"],
                    "if_smoothed_max": s["if_smoothed_max"],
                    "if_smoothed_mean": s["if_smoothed_mean"],
                    "speed_mean_mps": s["speed_mean_mps"],
                    "speed_std_mps": s["speed_std_mps"],
                    "speed_delta_abs_mean_mps": s["speed_delta_abs_mean_mps"],
                    "speed_delta_abs_max_mps": s["speed_delta_abs_max_mps"],
                }
            )

            print(
                f"top{rank} start={s['start_sec']} end={s['end_sec']} dur={s['duration_sec']}"
                f" | if_max={s['if_smoothed_max']:.4f} if_mean={s['if_smoothed_mean']:.4f}"
                f" | speed_mean={s['speed_mean_mps']:.4f} speed_std={s['speed_std_mps']:.4f}"
                f" | dV_mean={s['speed_delta_abs_mean_mps']:.4f} dV_max={s['speed_delta_abs_max_mps']:.4f}"
            )

            for p in s["points"]:
                second_rows.append(
                    {
                        "rank": rank,
                        "t_sec": p.t_sec,
                        "speed_mps": p.speed_mps,
                        "grade": p.grade,
                        "equiv_speed_m_min_raw": p.equiv_speed_m_min,
                        "if_smoothed": p.if_smoothed,
                    }
                )

        _write_csv(out_dir / f"smoothed_streak_summary_{aid}.csv", summary_rows)
        _write_csv(out_dir / f"smoothed_streak_seconds_{aid}.csv", second_rows)

    print(f"artifacts_dir={out_dir.as_posix()}")


if __name__ == "__main__":
    asyncio.run(main())
