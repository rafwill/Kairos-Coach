from __future__ import annotations

import asyncio
import json
import math
from statistics import median
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


def _extract_walk_samples(raw_details: Any) -> list[tuple[float, float, float, float | None]]:
    data = _safe_json(raw_details)
    if not isinstance(data, dict):
        return []

    descriptors = data.get("metricDescriptors")
    rows = data.get("activityDetailMetrics")
    if not isinstance(descriptors, list) or not isinstance(rows, list):
        return []

    by_key: dict[str, int] = {}
    for d in descriptors:
        if not isinstance(d, dict):
            continue
        key = str(d.get("key") or "").strip()
        try:
            idx = int(d.get("metricsIndex"))
        except (TypeError, ValueError):
            continue
        if key:
            by_key[key] = idx

    t_idx = by_key.get("directTimestamp")
    v_idx = by_key.get("directSpeed")
    e_idx = by_key.get("directElevation")
    d_idx = by_key.get("sumDistance")
    if t_idx is None or v_idx is None or e_idx is None:
        return []

    out: list[tuple[float, float, float, float | None]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, list):
            continue
        if max(t_idx, v_idx, e_idx) >= len(metrics):
            continue

        try:
            t_raw = float(metrics[t_idx])
            v_raw = float(metrics[v_idx])
            e_raw = float(metrics[e_idx])
        except (TypeError, ValueError):
            continue

        d_val: float | None = None
        if d_idx is not None and 0 <= d_idx < len(metrics):
            try:
                d_val = float(metrics[d_idx])
            except (TypeError, ValueError):
                d_val = None

        t_sec = (t_raw / 1000.0) if t_raw > 10_000_000_000 else t_raw
        out.append((float(t_sec), max(0.0, float(v_raw)), float(e_raw), d_val))

    if len(out) < 5:
        return []

    out.sort(key=lambda x: x[0])
    if out[0][0] > 86_400 and (out[-1][0] - out[0][0]) > 0:
        base = out[0][0]
        out = [(t - base, v, e, d) for t, v, e, d in out]

    dedup: dict[int, tuple[float, float, float | None]] = {}
    for t, v, e, d in out:
        dedup[int(round(max(0.0, t)))] = (v, e, d)

    return sorted((float(sec), vals[0], vals[1], vals[2]) for sec, vals in dedup.items())


def _pre_cap_equiv(vo2: float, src: str, speed_mps: float, grade: float) -> float:
    if src == "minetti":
        c0 = lm._walk_coste_minetti_j_kg_m(0.0)
        pre = (vo2 * lm.WALK_HIKE_J_PER_ML_O2) / c0 if c0 > 0 else 0.0
        return max(0.0, pre)

    pre = (vo2 - lm.WALK_HIKE_ACSM_RESTING_VO2_MLKGMIN) / lm.WALK_HIKE_ACSM_SPEED_COEFF
    if src == "acsm" and grade > 0.0:
        ceiling = lm._walk_acsm_equiv_ceiling_from_minetti_cutoff(speed_mps)
        pre = min(pre, ceiling)
    return max(0.0, pre)


def _build_uncapped_series(samples: list[tuple[float, float, float, float | None]]) -> list[float]:
    series: list[float] = []
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

        vo2, src = lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, grade)
        veq = _pre_cap_equiv(vo2, src, speed_mps, grade)

        reps = max(1, int(round(dt)))
        series.extend([veq] * reps)
        prev_dist = dist_cur if dist_cur is not None else prev_dist

    return series


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if p <= 0:
        return min(values)
    if p >= 100:
        return max(values)

    arr = sorted(values)
    rank = (len(arr) - 1) * (p / 100.0)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return arr[lo]
    frac = rank - lo
    return arr[lo] + ((arr[hi] - arr[lo]) * frac)


def _normalized_speed(series: list[float]) -> float:
    if len(series) < 5:
        return 0.0
    smooth = lm._moving_average(series, lm.WALK_HIKE_EQUIV_SMOOTH_WINDOW_S)
    p4 = [max(0.0, v) ** 4 for v in smooth if v > 0]
    if not p4:
        return 0.0
    return (sum(p4) / len(p4)) ** 0.25


def _count_above(values: list[float], threshold: float) -> int:
    return sum(1 for v in values if v > threshold)


def _spike_attenuation_probe(series: list[float], spike_value: float) -> dict[str, float]:
    if len(series) < 120:
        return {
            "baseline_median": 0.0,
            "raw_delta": 0.0,
            "smooth_delta": 0.0,
            "attenuation_ratio": 0.0,
            "norm_before": 0.0,
            "norm_after": 0.0,
            "norm_delta_pct": 0.0,
        }

    baseline = float(median(series))
    idx = len(series) // 2
    mutated = list(series)
    mutated[idx] = max(mutated[idx], spike_value)

    smooth_before = lm._moving_average(series, lm.WALK_HIKE_EQUIV_SMOOTH_WINDOW_S)
    smooth_after = lm._moving_average(mutated, lm.WALK_HIKE_EQUIV_SMOOTH_WINDOW_S)

    raw_delta = max(0.0, float(mutated[idx] - series[idx]))
    smooth_delta = max(0.0, float(smooth_after[idx] - smooth_before[idx]))
    attenuation = (smooth_delta / raw_delta) if raw_delta > 0 else 0.0

    norm_before = _normalized_speed(series)
    norm_after = _normalized_speed(mutated)
    norm_delta_pct = ((norm_after - norm_before) / norm_before * 100.0) if norm_before > 0 else 0.0

    return {
        "baseline_median": baseline,
        "raw_delta": raw_delta,
        "smooth_delta": smooth_delta,
        "attenuation_ratio": attenuation,
        "norm_before": norm_before,
        "norm_after": norm_after,
        "norm_delta_pct": norm_delta_pct,
    }


async def main() -> None:
    load_dotenv(".env", override=False)

    all_series: list[float] = []
    per_activity_stats: list[dict[str, float]] = []
    series_by_activity: dict[int, list[float]] = {}

    async with garmin_mcp_session(essential_only=False) as session:
        for aid in REF_IDS:
            raw_d = await call_tool(session, "get_activity_details", {"activity_id": aid})
            samples = _extract_walk_samples(raw_d)
            series = _build_uncapped_series(samples)
            if not series:
                continue

            series_by_activity[aid] = series
            all_series.extend(series)
            p99 = _percentile(series, 99.0)
            p999 = _percentile(series, 99.9)
            mx = max(series)
            norm = _normalized_speed(series)
            per_activity_stats.append(
                {
                    "id": float(aid),
                    "n": float(len(series)),
                    "p99": p99,
                    "p999": p999,
                    "max": mx,
                    "norm": norm,
                }
            )

    print("=== DISTRIBUTION_UNCAPPED_PRE_SMOOTHING ===")
    print(f"samples_total={len(all_series)}")
    for p in (50, 75, 90, 95, 97, 99, 99.5, 99.9):
        print(f"p{p}={_percentile(all_series, float(p)):.3f}")
    print(f"max={max(all_series) if all_series else 0.0:.3f}")

    print("\n=== ABOVE_THRESHOLDS ===")
    for thr in (140.0, 160.0, 180.0, 200.0, 250.0, 300.0, 350.0):
        c = _count_above(all_series, thr)
        pct = (100.0 * c / len(all_series)) if all_series else 0.0
        print(f">{thr:.0f} = {c} ({pct:.3f}%)")

    print("\n=== PER_ACTIVITY_UNCAPPED ===")
    for row in per_activity_stats:
        print(
            f"id={int(row['id'])} | n={int(row['n'])} | p99={row['p99']:.3f}"
            f" | p99.9={row['p999']:.3f} | max={row['max']:.3f} | norm={row['norm']:.3f}"
        )

    print("\n=== SPIKE_ATTENUATION_30S ===")
    # Probe each activity with one-sample spike to 350 m/min.
    ratios = []
    norm_deltas = []
    for row in per_activity_stats:
        aid = int(row["id"])
        series = series_by_activity.get(aid, [])
        probe = _spike_attenuation_probe(series, spike_value=350.0)
        ratios.append(probe["attenuation_ratio"])
        norm_deltas.append(probe["norm_delta_pct"])
        print(
            f"id={aid} | raw_delta={probe['raw_delta']:.3f} | smooth_delta={probe['smooth_delta']:.3f}"
            f" | attenuation_ratio={probe['attenuation_ratio']:.4f} | norm_delta_pct={probe['norm_delta_pct']:.4f}%"
        )

    if ratios:
        print(
            f"attenuation_ratio_median={median(ratios):.4f}"
            f" | attenuation_ratio_max={max(ratios):.4f}"
            f" | attenuation_ratio_theoretical~={1.0/30.0:.4f}"
        )
    if norm_deltas:
        print(
            f"norm_delta_pct_median={median(norm_deltas):.4f}%"
            f" | norm_delta_pct_max={max(norm_deltas):.4f}%"
        )


if __name__ == "__main__":
    asyncio.run(main())
