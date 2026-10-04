from __future__ import annotations

import asyncio
import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
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

DEFAULT_TARGET_IDS = [23478220005, 24502427862]
BLOCK_SECONDS = 60
TOP_WINDOW_SECONDS = 300


@dataclass
class SecPoint:
    t_sec: int
    speed_mps: float
    grade: float
    equiv_speed_m_min: float
    if_inst: float
    p4: float
    vo2_source: str
    equiv_speed_m_min_smoothed: float = 0.0
    if_inst_smoothed: float = 0.0
    p4_smoothed: float = 0.0


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


def _parse_target_ids(raw_ids: str | None) -> list[int]:
    if not raw_ids:
        return list(DEFAULT_TARGET_IDS)
    out: list[int] = []
    for part in raw_ids.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            continue
    return out if out else list(DEFAULT_TARGET_IDS)


def _total_elevation_gain_m(activity_details_raw: str) -> float:
    samples = lm._extract_walk_samples_from_activity_details(activity_details_raw)
    if len(samples) < 2:
        return 0.0
    gain = 0.0
    for idx in range(1, len(samples)):
        e_prev = float(samples[idx - 1][2])
        e_cur = float(samples[idx][2])
        delta = e_cur - e_prev
        if delta > 0:
            gain += delta
    return float(gain)


def _summarize_elevation_jumps(activity_details_raw: str) -> tuple[dict[str, float], list[dict[str, float]]]:
    samples = lm._extract_walk_samples_from_activity_details(activity_details_raw)
    if len(samples) < 2:
        return {}, []

    pos_jumps: list[tuple[float, float]] = []  # (jump_m, dt_s)
    for idx in range(1, len(samples)):
        t_prev, _, e_prev, _ = samples[idx - 1]
        t_cur, _, e_cur, _ = samples[idx]
        dt = max(1e-9, float(t_cur - t_prev))
        jump = float(e_cur) - float(e_prev)
        if jump > 0:
            pos_jumps.append((jump, dt))

    if not pos_jumps:
        return {}, []

    total_gain = float(sum(j for j, _ in pos_jumps))
    sorted_jumps = sorted((j for j, _ in pos_jumps), reverse=True)
    top3_gain = float(sum(sorted_jumps[:3]))
    top5_gain = float(sum(sorted_jumps[:5]))

    bins: list[tuple[str, float, float]] = [
        ("0_to_0_5", 0.0, 0.5),
        ("0_5_to_1_0", 0.5, 1.0),
        ("1_0_to_2_0", 1.0, 2.0),
        ("2_0_to_3_0", 2.0, 3.0),
        ("3_0_to_5_0", 3.0, 5.0),
        ("gt_5_0", 5.0, float("inf")),
    ]
    bin_rows: list[dict[str, float]] = []
    for name, lo, hi in bins:
        selected = [(j, dt) for j, dt in pos_jumps if (j >= lo and (j < hi if hi != float("inf") else True))]
        gain = float(sum(j for j, _ in selected))
        count = len(selected)
        bin_rows.append(
            {
                "bin": name,
                "count": float(count),
                "gain_m": gain,
                "gain_pct": (gain / total_gain * 100.0) if total_gain > 0 else 0.0,
            }
        )

    suspicious_rate_threshold_mps = 1.0
    suspicious_count = 0
    suspicious_gain = 0.0
    max_jump = 0.0
    max_rate = 0.0
    for jump, dt in pos_jumps:
        rate = jump / dt
        max_jump = max(max_jump, jump)
        max_rate = max(max_rate, rate)
        if rate > suspicious_rate_threshold_mps and jump >= 1.0:
            suspicious_count += 1
            suspicious_gain += jump

    summary = {
        "jump_count": float(len(pos_jumps)),
        "gain_total_m": total_gain,
        "top3_gain_m": top3_gain,
        "top3_gain_pct": (top3_gain / total_gain * 100.0) if total_gain > 0 else 0.0,
        "top5_gain_m": top5_gain,
        "top5_gain_pct": (top5_gain / total_gain * 100.0) if total_gain > 0 else 0.0,
        "max_jump_m": max_jump,
        "max_jump_rate_mps": max_rate,
        "suspicious_rate_threshold_mps": suspicious_rate_threshold_mps,
        "suspicious_jump_count": float(suspicious_count),
        "suspicious_gain_m": float(suspicious_gain),
        "suspicious_gain_pct": (float(suspicious_gain) / total_gain * 100.0) if total_gain > 0 else 0.0,
    }
    return summary, bin_rows


def _extract_sec_points(activity_details_raw: str, threshold_m_min: float) -> tuple[list[SecPoint], dict[str, float]]:
    samples = lm._extract_walk_samples_from_activity_details(activity_details_raw)
    if len(samples) < 5:
        return [], {}

    sec_points: list[SecPoint] = []
    moving_seconds = 0.0
    prev_dist: float | None = None
    source_counts: dict[str, int] = defaultdict(int)

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
        v_equiv_m_min = lm._walk_equivalent_flat_speed_from_vo2_m_min(
            vo2,
            vo2_source,
            speed_mps=speed_mps,
            grade=grade,
        )
        if_inst = max(0.0, v_equiv_m_min / threshold_m_min) if threshold_m_min > 0 else 0.0
        p4 = if_inst**4

        reps = max(1, int(round(dt)))
        t_start = int(round(max(0.0, t_cur - dt)))
        for offset in range(reps):
            sec_points.append(
                SecPoint(
                    t_sec=t_start + offset,
                    speed_mps=float(speed_mps),
                    grade=float(grade),
                    equiv_speed_m_min=float(v_equiv_m_min),
                    if_inst=float(if_inst),
                    p4=float(p4),
                    vo2_source=vo2_source,
                )
            )

        source_counts[vo2_source] += reps
        moving_seconds += dt
        prev_dist = dist_cur if dist_cur is not None else prev_dist

    if not sec_points:
        return [], {}

    sec_points.sort(key=lambda p: p.t_sec)

    # Apply the same production smoothing window used by normalized equivalent speed.
    smoothed_equiv = lm._moving_average(
        [p.equiv_speed_m_min for p in sec_points],
        lm.WALK_HIKE_EQUIV_SMOOTH_WINDOW_S,
    )
    for p, v_sm in zip(sec_points, smoothed_equiv):
        p.equiv_speed_m_min_smoothed = float(v_sm)
        p.if_inst_smoothed = max(0.0, float(v_sm) / threshold_m_min) if threshold_m_min > 0 else 0.0
        p.p4_smoothed = p.if_inst_smoothed**4

    prod_obs = lm._compute_walk_hike_metabolic_observables(activity_details_raw)
    normalized_equiv_prod = float((prod_obs or {}).get("normalized_equiv_speed_m_min") or 0.0)
    normalized_equiv_diag = lm._compute_normalized_walk_equivalent_speed_m_min([p.equiv_speed_m_min for p in sec_points])
    normalized_equiv_diag = float(normalized_equiv_diag or 0.0)

    validation = {
        "moving_seconds": float(moving_seconds),
        "normalized_equiv_prod": normalized_equiv_prod,
        "normalized_equiv_diag": normalized_equiv_diag,
        "normalized_equiv_delta": normalized_equiv_diag - normalized_equiv_prod,
        "source_acsm_ratio": float(source_counts.get("acsm", 0)) / float(len(sec_points)),
        "source_minetti_ratio": float(source_counts.get("minetti", 0)) / float(len(sec_points)),
    }
    return sec_points, validation


def _summarize_blocks(sec_points: list[SecPoint], block_seconds: int) -> list[dict[str, float]]:
    by_block: dict[int, list[SecPoint]] = defaultdict(list)
    for p in sec_points:
        by_block[p.t_sec // block_seconds].append(p)

    rows: list[dict[str, float]] = []
    for bidx in sorted(by_block):
        vals = by_block[bidx]
        rows.append(
            {
                "block_index": float(bidx),
                "start_sec": float(bidx * block_seconds),
                "end_sec": float((bidx + 1) * block_seconds),
                "seconds": float(len(vals)),
                "speed_mps_avg": fmean(v.speed_mps for v in vals),
                "grade_avg": fmean(v.grade for v in vals),
                "equiv_speed_m_min_avg": fmean(v.equiv_speed_m_min for v in vals),
                "equiv_speed_m_min_smoothed_avg": fmean(v.equiv_speed_m_min_smoothed for v in vals),
                "if_avg": fmean(v.if_inst for v in vals),
                "if_smoothed_avg": fmean(v.if_inst_smoothed for v in vals),
                "p4_avg": fmean(v.p4 for v in vals),
                "p4_sum": float(sum(v.p4 for v in vals)),
                "p4_smoothed_avg": fmean(v.p4_smoothed for v in vals),
                "p4_smoothed_sum": float(sum(v.p4_smoothed for v in vals)),
            }
        )
    return rows


def _summarize_zones(sec_points: list[SecPoint], use_smoothed: bool = False) -> list[dict[str, float]]:
    bands = [
        ("lt_0_8", None, 0.8),
        ("0_8_to_0_9", 0.8, 0.9),
        ("0_9_to_1_0", 0.9, 1.0),
        ("1_0_to_1_1", 1.0, 1.1),
        ("gt_1_1", 1.1, None),
    ]
    total_secs = float(len(sec_points))
    total_p4 = float(sum((p.p4_smoothed if use_smoothed else p.p4) for p in sec_points))

    out: list[dict[str, float]] = []
    for name, lo, hi in bands:
        selected: list[SecPoint] = []
        for p in sec_points:
            if_val = p.if_inst_smoothed if use_smoothed else p.if_inst
            p4_val = p.p4_smoothed if use_smoothed else p.p4
            if lo is not None and if_val < lo:
                continue
            if hi is not None and if_val >= hi:
                continue
            selected.append(SecPoint(
                t_sec=p.t_sec,
                speed_mps=p.speed_mps,
                grade=p.grade,
                equiv_speed_m_min=p.equiv_speed_m_min,
                if_inst=if_val,
                p4=p4_val,
                vo2_source=p.vo2_source,
            ))
        secs = float(len(selected))
        p4_sum = float(sum(p.p4 for p in selected))
        out.append(
            {
                "zone": name,
                "mode": "smoothed" if use_smoothed else "raw",
                "seconds": secs,
                "time_pct": (secs / total_secs * 100.0) if total_secs > 0 else 0.0,
                "p4_sum": p4_sum,
                "p4_pct": (p4_sum / total_p4 * 100.0) if total_p4 > 0 else 0.0,
            }
        )
    return out


def _summarize_streaks(sec_points: list[SecPoint], threshold_if: float) -> dict[str, float]:
    max_streak = 0
    current_streak = 0
    streak_count = 0

    for p in sec_points:
        if p.if_inst >= threshold_if:
            if current_streak == 0:
                streak_count += 1
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    return {
        "threshold_if": threshold_if,
        "streak_count": float(streak_count),
        "max_streak_seconds": float(max_streak),
    }


def _summarize_streaks_smoothed(sec_points: list[SecPoint], threshold_if: float) -> dict[str, float]:
    max_streak = 0
    current_streak = 0
    streak_count = 0

    for p in sec_points:
        if p.if_inst_smoothed >= threshold_if:
            if current_streak == 0:
                streak_count += 1
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    return {
        "threshold_if": threshold_if,
        "streak_count": float(streak_count),
        "max_streak_seconds": float(max_streak),
    }


def _top_windows(sec_points: list[SecPoint], window_seconds: int, top_n: int) -> list[dict[str, float]]:
    if len(sec_points) < window_seconds:
        return []

    ranked: list[dict[str, float]] = []
    for i in range(0, len(sec_points) - window_seconds + 1):
        win = sec_points[i : i + window_seconds]
        ranked.append(
            {
                "start_sec": float(win[0].t_sec),
                "end_sec": float(win[-1].t_sec + 1),
                "seconds": float(window_seconds),
                "if_avg": fmean(p.if_inst for p in win),
                "p4_sum": float(sum(p.p4 for p in win)),
                "speed_mps_avg": fmean(p.speed_mps for p in win),
                "grade_avg": fmean(p.grade for p in win),
                "equiv_speed_m_min_avg": fmean(p.equiv_speed_m_min for p in win),
            }
        )

    ranked.sort(key=lambda r: (r["if_avg"], r["p4_sum"]), reverse=True)

    chosen: list[dict[str, float]] = []
    for cand in ranked:
        overlaps = False
        for c in chosen:
            if not (cand["end_sec"] <= c["start_sec"] or cand["start_sec"] >= c["end_sec"]):
                overlaps = True
                break
        if not overlaps:
            chosen.append(cand)
        if len(chosen) >= top_n:
            break
    return chosen


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _pct(value: float) -> str:
    return f"{value:.2f}%"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze walk/hike temporal intensity profiles.")
    parser.add_argument(
        "--target-ids",
        type=str,
        default=None,
        help="Comma-separated activity IDs to analyze.",
    )
    args = parser.parse_args()
    target_ids = _parse_target_ids(args.target_ids)

    load_dotenv(".env", override=False)

    activities: dict[int, dict] = {}
    details: dict[int, str] = {}

    async with garmin_mcp_session(essential_only=False) as session:
        for aid in REF_IDS:
            raw_a = await call_tool(session, "get_activity", {"activity_id": aid})
            raw_d = await call_tool(session, "get_activity_details", {"activity_id": aid})
            activities[aid] = _to_activity(raw_a)
            details[aid] = _to_details_text(raw_d)

    derivation_activities: list[dict] = []
    for aid in REF_IDS:
        act = dict(activities[aid])
        act["_activity_details_raw"] = details[aid]
        derivation_activities.append(act)
    threshold = lm.derive_walk_hike_threshold_speed_m_min_from_activities(derivation_activities)
    if threshold is None:
        threshold = lm.WALK_HIKE_THRESHOLD_SPEED_M_MIN_DEFAULT

    out_dir = Path("docs") / "walk_hike_temporal_profile"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=== WALK_HIKE_TEMPORAL_PROFILE ===")
    print(f"threshold_m_min={threshold:.6f}")
    print("analysis_ids=" + ",".join(str(x) for x in target_ids))

    comparison_rows: list[dict[str, Any]] = []

    for aid in target_ids:
        act = activities[aid]
        name = str(act.get("name") or act.get("activityName") or "")
        sec_points, validation = _extract_sec_points(details[aid], threshold)
        if not sec_points:
            print(f"id={aid} | name={name} | ERROR=no_moving_points")
            continue

        blocks = _summarize_blocks(sec_points, BLOCK_SECONDS)
        zones = _summarize_zones(sec_points, use_smoothed=False)
        zones_smoothed = _summarize_zones(sec_points, use_smoothed=True)
        streak_095 = _summarize_streaks(sec_points, 0.95)
        streak_100 = _summarize_streaks(sec_points, 1.00)
        streak_095_sm = _summarize_streaks_smoothed(sec_points, 0.95)
        streak_100_sm = _summarize_streaks_smoothed(sec_points, 1.00)
        top5 = _top_windows(sec_points, TOP_WINDOW_SECONDS, 5)

        total_p4 = float(sum(p.p4 for p in sec_points))
        top5_p4 = float(sum(w["p4_sum"] for w in top5))
        top5_p4_pct = (top5_p4 / total_p4 * 100.0) if total_p4 > 0 else 0.0
        elevation_gain_total_m = _total_elevation_gain_m(details[aid])
        jump_summary, jump_bins = _summarize_elevation_jumps(details[aid])

        _write_csv(out_dir / f"blocks_60s_{aid}.csv", blocks)
        _write_csv(out_dir / f"zones_{aid}.csv", zones)
        _write_csv(out_dir / f"zones_smoothed_30s_{aid}.csv", zones_smoothed)
        _write_csv(out_dir / f"top5_windows_5min_{aid}.csv", top5)
        _write_csv(out_dir / f"elevation_jumps_bins_{aid}.csv", jump_bins)

        avg_speed = fmean(p.speed_mps for p in sec_points)
        avg_grade = fmean(p.grade for p in sec_points)
        avg_equiv = fmean(p.equiv_speed_m_min for p in sec_points)
        avg_if = fmean(p.if_inst for p in sec_points)

        print(
            f"id={aid} | name={name} | moving_sec={len(sec_points)}"
            f" | avg_speed_mps={avg_speed:.4f} | avg_grade={avg_grade:.4f}"
            f" | avg_equiv_m_min={avg_equiv:.4f} | avg_if={avg_if:.4f}"
            f" | elev_gain_total_m={elevation_gain_total_m:.2f}"
        )
        print(
            f"validation: normalized_prod={validation.get('normalized_equiv_prod', 0.0):.6f}"
            f" | normalized_diag={validation.get('normalized_equiv_diag', 0.0):.6f}"
            f" | delta={validation.get('normalized_equiv_delta', 0.0):.9f}"
            f" | acsm_ratio={validation.get('source_acsm_ratio', 0.0):.4f}"
            f" | minetti_ratio={validation.get('source_minetti_ratio', 0.0):.4f}"
        )
        print(
            f"streaks: IF>=0.95 count={int(streak_095['streak_count'])} max_sec={int(streak_095['max_streak_seconds'])}"
            f" | IF>=1.00 count={int(streak_100['streak_count'])} max_sec={int(streak_100['max_streak_seconds'])}"
        )
        print(
            f"streaks_smoothed_30s: IF>=0.95 count={int(streak_095_sm['streak_count'])} max_sec={int(streak_095_sm['max_streak_seconds'])}"
            f" | IF>=1.00 count={int(streak_100_sm['streak_count'])} max_sec={int(streak_100_sm['max_streak_seconds'])}"
        )
        print(f"top5_windows_p4_pct={top5_p4_pct:.2f}%")
        if jump_summary:
            print(
                "elev_jumps:"
                f" count={int(jump_summary['jump_count'])}"
                f" | gain_total_m={jump_summary['gain_total_m']:.2f}"
                f" | top3_gain_pct={jump_summary['top3_gain_pct']:.2f}%"
                f" | top5_gain_pct={jump_summary['top5_gain_pct']:.2f}%"
                f" | max_jump_m={jump_summary['max_jump_m']:.2f}"
                f" | max_rate_mps={jump_summary['max_jump_rate_mps']:.2f}"
                f" | suspicious_count={int(jump_summary['suspicious_jump_count'])}"
                f" | suspicious_gain_pct={jump_summary['suspicious_gain_pct']:.2f}%"
            )
            for jb in jump_bins:
                print(
                    f"jump_bin={jb['bin']}"
                    f" | count={int(jb['count'])}"
                    f" | gain_pct={jb['gain_pct']:.2f}%"
                )

        for z in zones:
            print(
                f"zone_raw={z['zone']} | time_pct={_pct(float(z['time_pct']))}"
                f" | p4_pct={_pct(float(z['p4_pct']))}"
            )
        for z in zones_smoothed:
            print(
                f"zone_smoothed_30s={z['zone']} | time_pct={_pct(float(z['time_pct']))}"
                f" | p4_pct={_pct(float(z['p4_pct']))}"
            )

        for idx, w in enumerate(top5, start=1):
            print(
                f"top{idx} start={int(w['start_sec'])} end={int(w['end_sec'])}"
                f" | if_avg={w['if_avg']:.4f} | speed_mps_avg={w['speed_mps_avg']:.4f}"
                f" | grade_avg={w['grade_avg']:.4f} | equiv_m_min_avg={w['equiv_speed_m_min_avg']:.4f}"
                f" | p4_sum={w['p4_sum']:.4f}"
            )

        print("---")

        comparison_rows.append(
            {
                "activity_id": aid,
                "name": name,
                "moving_seconds": len(sec_points),
                "avg_speed_mps": avg_speed,
                "avg_grade": avg_grade,
                "avg_equiv_speed_m_min": avg_equiv,
                "avg_if": avg_if,
                "elevation_gain_total_m": elevation_gain_total_m,
                "elev_jump_count": jump_summary.get("jump_count", 0.0) if jump_summary else 0.0,
                "elev_jump_top3_gain_pct": jump_summary.get("top3_gain_pct", 0.0) if jump_summary else 0.0,
                "elev_jump_top5_gain_pct": jump_summary.get("top5_gain_pct", 0.0) if jump_summary else 0.0,
                "elev_jump_max_m": jump_summary.get("max_jump_m", 0.0) if jump_summary else 0.0,
                "elev_jump_max_rate_mps": jump_summary.get("max_jump_rate_mps", 0.0) if jump_summary else 0.0,
                "elev_jump_suspicious_count": jump_summary.get("suspicious_jump_count", 0.0) if jump_summary else 0.0,
                "elev_jump_suspicious_gain_pct": jump_summary.get("suspicious_gain_pct", 0.0) if jump_summary else 0.0,
                "top5_windows_p4_pct": top5_p4_pct,
                "streak_095_count": int(streak_095["streak_count"]),
                "streak_095_max_sec": int(streak_095["max_streak_seconds"]),
                "streak_100_count": int(streak_100["streak_count"]),
                "streak_100_max_sec": int(streak_100["max_streak_seconds"]),
                "acsm_ratio": validation.get("source_acsm_ratio", 0.0),
                "minetti_ratio": validation.get("source_minetti_ratio", 0.0),
                "normalized_equiv_prod": validation.get("normalized_equiv_prod", 0.0),
                "normalized_equiv_diag": validation.get("normalized_equiv_diag", 0.0),
                "normalized_equiv_delta": validation.get("normalized_equiv_delta", 0.0),
            }
        )

    _write_csv(out_dir / "comparison_summary.csv", comparison_rows)
    print(f"artifacts_dir={out_dir.as_posix()}")


if __name__ == "__main__":
    asyncio.run(main())