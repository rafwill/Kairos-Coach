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

THRESHOLDS = [1.5, 2.0]
STRONG_POS_GRADE = 0.10
EPS = 1e-9


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


def _compute_equiv_and_grade(speed_mps: float, grade: float) -> tuple[float, float]:
    grade_clamped = max(lm.WALK_HIKE_MINETTI_GRADE_MIN, min(lm.WALK_HIKE_MINETTI_GRADE_MAX, grade))
    vo2, source = lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, grade_clamped)
    equiv = lm._walk_equivalent_flat_speed_from_vo2_m_min(
        vo2,
        source,
        speed_mps=speed_mps,
        grade=grade_clamped,
    )
    return float(equiv), float(grade_clamped)


def _rows_for_activity(activity_id: int, name: str, details_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    samples = lm._extract_walk_samples_from_activity_details(details_text)
    if len(samples) < 2:
        return [], []

    transition_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    # Build the same second-resolution stream used by temporal diagnostics.
    sec_points: list[dict[str, float]] = []
    prev_dist: float | None = None
    for idx in range(1, len(samples)):
        t_prev, _, elev_prev, _ = samples[idx - 1]
        t_cur, speed_cur, elev_cur, dist_cur = samples[idx]

        dt = max(0.0, float(t_cur - t_prev))
        if dt <= 0:
            continue
        dt = min(dt, 30.0)

        delta_dist = float(speed_cur) * dt
        if dist_cur is not None and prev_dist is not None:
            inferred = float(dist_cur) - float(prev_dist)
            if inferred > 0:
                delta_dist = inferred
        if delta_dist <= 0:
            prev_dist = dist_cur if dist_cur is not None else prev_dist
            continue

        grade_raw = (float(elev_cur) - float(elev_prev)) / max(0.1, float(delta_dist))
        equiv_cur, grade_clamped = _compute_equiv_and_grade(float(speed_cur), grade_raw)

        reps = max(1, int(round(dt)))
        t_start = int(round(max(0.0, float(t_cur) - dt)))
        for off in range(reps):
            sec_points.append(
                {
                    "t_sec": float(t_start + off),
                    "speed_mps": float(speed_cur),
                    "grade": float(grade_clamped),
                    "equiv_m_min": float(equiv_cur),
                }
            )

        prev_dist = dist_cur if dist_cur is not None else prev_dist

    if len(sec_points) < 2:
        return [], []

    sec_points.sort(key=lambda p: p["t_sec"])

    # Precompute per-transition metrics once over second-by-second points.
    transitions: list[dict[str, Any]] = []
    for idx in range(1, len(sec_points)):
        p_prev = sec_points[idx - 1]
        p_cur = sec_points[idx]
        t_prev = float(p_prev["t_sec"])
        t_cur = float(p_cur["t_sec"])
        dt = t_cur - t_prev
        if dt <= 0:
            continue

        speed_prev = float(p_prev["speed_mps"])
        speed_cur = float(p_cur["speed_mps"])
        accel = (speed_cur - speed_prev) / dt
        accel_abs = abs(accel)

        grade_clamped = float(p_cur["grade"])
        equiv_cur = float(p_cur["equiv_m_min"])
        equiv_prev = float(p_prev["equiv_m_min"])
        cap_hit = (equiv_cur >= lm.WALK_HIKE_EQUIV_SPEED_M_MIN_MAX - EPS) or (
            equiv_prev >= lm.WALK_HIKE_EQUIV_SPEED_M_MIN_MAX - EPS
        )

        transitions.append(
            {
                "t_prev": float(t_prev),
                "t_cur": float(t_cur),
                "dt": dt,
                "speed_prev_mps": float(speed_prev),
                "speed_cur_mps": float(speed_cur),
                "accel_mps2": float(accel),
                "accel_abs_mps2": float(accel_abs),
                "grade": float(grade_clamped),
                "grade_strong_positive": bool(grade_clamped > STRONG_POS_GRADE),
                "cap350_hit": bool(cap_hit),
                "equiv_prev_m_min": float(equiv_prev),
                "equiv_cur_m_min": float(equiv_cur),
            }
        )

    total = len(transitions)
    for thr in THRESHOLDS:
        flagged = [t for t in transitions if float(t["accel_abs_mps2"]) > thr]
        flagged_n = len(flagged)
        strong_pos = [t for t in flagged if bool(t["grade_strong_positive"])]
        moderate_or_flat = flagged_n - len(strong_pos)
        cap_hits = [t for t in flagged if bool(t["cap350_hit"])]

        summary_rows.append(
            {
                "activity_id": activity_id,
                "name": name,
                "threshold_mps2": thr,
                "total_transitions": total,
                "flagged_count": flagged_n,
                "flagged_pct": (100.0 * flagged_n / total) if total > 0 else 0.0,
                "strong_pos_grade_count": len(strong_pos),
                "strong_pos_grade_pct_of_flagged": (100.0 * len(strong_pos) / flagged_n) if flagged_n > 0 else 0.0,
                "moderate_or_flat_count": moderate_or_flat,
                "moderate_or_flat_pct_of_flagged": (100.0 * moderate_or_flat / flagged_n) if flagged_n > 0 else 0.0,
                "cap350_overlap_count": len(cap_hits),
                "cap350_overlap_pct_of_flagged": (100.0 * len(cap_hits) / flagged_n) if flagged_n > 0 else 0.0,
            }
        )

        for t in flagged:
            transition_rows.append(
                {
                    "activity_id": activity_id,
                    "name": name,
                    "threshold_mps2": thr,
                    "t_prev": t["t_prev"],
                    "t_cur": t["t_cur"],
                    "dt": t["dt"],
                    "speed_prev_mps": t["speed_prev_mps"],
                    "speed_cur_mps": t["speed_cur_mps"],
                    "accel_mps2": t["accel_mps2"],
                    "accel_abs_mps2": t["accel_abs_mps2"],
                    "grade": t["grade"],
                    "grade_strong_positive": t["grade_strong_positive"],
                    "cap350_hit": t["cap350_hit"],
                    "equiv_prev_m_min": t["equiv_prev_m_min"],
                    "equiv_cur_m_min": t["equiv_cur_m_min"],
                }
            )

    return summary_rows, transition_rows


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

    activities: dict[int, dict] = {}
    details: dict[int, str] = {}

    async with garmin_mcp_session(essential_only=False) as session:
        for aid in REF_IDS:
            raw_a = await call_tool(session, "get_activity", {"activity_id": aid})
            raw_d = await call_tool(session, "get_activity_details", {"activity_id": aid})
            activities[aid] = _to_activity(raw_a)
            details[aid] = _to_details_text(raw_d)

    all_summary: list[dict[str, Any]] = []
    all_flagged: list[dict[str, Any]] = []

    print("=== WALK_HIKE_ACCEL_REACH ===")
    print("fields=id|thr|flagged_pct|flagged_count|strong_pos_pct|moderate_flat_pct|cap350_overlap_pct")

    for aid in REF_IDS:
        act = activities[aid]
        name = str(act.get("name") or act.get("activityName") or "")
        summary_rows, flagged_rows = _rows_for_activity(aid, name, details[aid])
        all_summary.extend(summary_rows)
        all_flagged.extend(flagged_rows)

        for row in summary_rows:
            print(
                f"{aid}|{row['threshold_mps2']:.1f}|{row['flagged_pct']:.3f}|{row['flagged_count']}"
                f"|{row['strong_pos_grade_pct_of_flagged']:.3f}|{row['moderate_or_flat_pct_of_flagged']:.3f}"
                f"|{row['cap350_overlap_pct_of_flagged']:.3f}"
            )

    out_dir = Path("docs") / "walk_hike_temporal_profile"
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "accel_reach_summary_8_activities.csv", all_summary)
    _write_csv(out_dir / "accel_reach_flagged_transitions_8_activities.csv", all_flagged)
    print(f"artifacts_dir={out_dir.as_posix()}")


if __name__ == "__main__":
    asyncio.run(main())
