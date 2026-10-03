from __future__ import annotations

import asyncio
import json
from typing import Any

from dotenv import load_dotenv

from agent.mcp_client import call_tool, garmin_mcp_session
from agent import load_metrics as lm

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


def _extract_walk_samples(raw_details: str) -> list[tuple[float, float, float, float | None]]:
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


def _walk_cost_minetti(i: float) -> float:
    return (280.5 * (i**5)) - (58.7 * (i**4)) - (76.8 * (i**3)) + (51.9 * (i**2)) + (19.6 * i) + 2.5


def _vo2_and_source(speed_mps: float, grade: float) -> tuple[float, str]:
    return lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, grade)


def _equiv_uncapped(vo2: float, source: str) -> float:
    if source == "minetti":
        c0 = _walk_cost_minetti(0.0)
        if c0 <= 0:
            return 0.0
        return max(0.0, (vo2 * lm.WALK_HIKE_J_PER_ML_O2) / c0)
    return max(0.0, (vo2 - lm.WALK_HIKE_ACSM_RESTING_VO2_MLKGMIN) / lm.WALK_HIKE_ACSM_SPEED_COEFF)


def _probe_points(speed_m_min: float) -> list[tuple[float, float, str]]:
    speed_mps = float(speed_m_min) / 60.0
    probes = [0.081, 0.099, 0.101]
    out: list[tuple[float, float, str]] = []
    for g in probes:
        vo2, src = _vo2_and_source(speed_mps, g)
        veq = lm._walk_equivalent_flat_speed_from_vo2_m_min(vo2, src, speed_mps=speed_mps, grade=g)
        out.append((g, veq, src))
    return out


def _monotonicity_sweep(speed_m_min: float = 80.0, step: float = 0.005) -> tuple[int, list[str], int, list[str]]:
    speed_mps = float(speed_m_min) / 60.0

    uphill_grades = []
    g = 0.0
    while g <= 0.45 + 1e-12:
        uphill_grades.append(round(g, 6))
        g += step

    full_grades = []
    g = -0.45
    while g <= 0.45 + 1e-12:
        full_grades.append(round(g, 6))
        g += step

    def _count_violations(grades: list[float]) -> tuple[int, list[str]]:
        prev = None
        violations = 0
        examples: list[str] = []
        for gg in grades:
            vo2, src = _vo2_and_source(speed_mps, gg)
            veq = lm._walk_equivalent_flat_speed_from_vo2_m_min(vo2, src, speed_mps=speed_mps, grade=gg)
            if prev is not None and (veq + 1e-9) < prev[1]:
                violations += 1
                if len(examples) < 5:
                    examples.append(f"g_prev={prev[0]:.3f} veq_prev={prev[1]:.3f} -> g={gg:.3f} veq={veq:.3f}")
            prev = (gg, veq)
        return violations, examples

    uphill_v, uphill_ex = _count_violations(uphill_grades)
    full_v, full_ex = _count_violations(full_grades)
    return uphill_v, uphill_ex, full_v, full_ex


def _analyze_samples(samples: list[tuple[float, float, float, float | None]]) -> dict[str, float]:
    cap = float(lm.WALK_HIKE_EQUIV_SPEED_M_MIN_MAX)

    uphill_total = 0
    uphill_capped = 0
    all_total = 0
    all_capped = 0

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

        vo2, src = _vo2_and_source(speed_mps, grade)
        veq_unc = _equiv_uncapped(vo2, src)
        capped = veq_unc >= cap

        all_total += 1
        if capped:
            all_capped += 1

        if grade > 0:
            uphill_total += 1
            if capped:
                uphill_capped += 1

        prev_dist = dist_cur if dist_cur is not None else prev_dist

    pct_uphill = (100.0 * uphill_capped / uphill_total) if uphill_total else 0.0
    pct_all = (100.0 * all_capped / all_total) if all_total else 0.0
    return {
        "uphill_total": float(uphill_total),
        "uphill_capped": float(uphill_capped),
        "uphill_capped_pct": pct_uphill,
        "all_total": float(all_total),
        "all_capped": float(all_capped),
        "all_capped_pct": pct_all,
    }


def _compute_obs_uncapped(samples: list[tuple[float, float, float, float | None]]) -> tuple[float, float]:
    # Returns (moving_seconds, normalized_equiv_speed_uncapped_m_min)
    series: list[float] = []
    moving = 0.0

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

        vo2, src = _vo2_and_source(speed_mps, grade)
        veq_unc = _equiv_uncapped(vo2, src)

        reps = max(1, int(round(dt)))
        series.extend([veq_unc] * reps)
        moving += dt
        prev_dist = dist_cur if dist_cur is not None else prev_dist

    if moving <= 0 or len(series) < 5:
        return 0.0, 0.0

    smoothed = lm._moving_average(series, lm.WALK_HIKE_EQUIV_SMOOTH_WINDOW_S)
    p4 = [max(0.0, v) ** 4 for v in smoothed if v > 0]
    if not p4:
        return moving, 0.0

    norm = (sum(p4) / len(p4)) ** 0.25
    return moving, float(norm)


async def main() -> None:
    load_dotenv(".env", override=False)

    acts: list[dict] = []
    per_activity: list[dict[str, Any]] = []

    async with garmin_mcp_session(essential_only=False) as session:
        for aid in REF_IDS:
            raw_a = await call_tool(session, "get_activity", {"activity_id": aid})
            raw_d = await call_tool(session, "get_activity_details", {"activity_id": aid})

            act = _to_activity(raw_a)
            act["_activity_details_raw"] = raw_d
            acts.append(act)

            samples = _extract_walk_samples(raw_d if isinstance(raw_d, str) else json.dumps(raw_d, ensure_ascii=False))
            an = _analyze_samples(samples)

            moving_s, norm_unc = _compute_obs_uncapped(samples)
            obs_capped = lm._compute_walk_hike_metabolic_observables(raw_d)
            norm_capped = float(obs_capped.get("normalized_equiv_speed_m_min")) if obs_capped else 0.0

            per_activity.append(
                {
                    "activity_id": aid,
                    "type": str(act.get("type") or act.get("activityType") or ""),
                    "name": str(act.get("name") or act.get("activityName") or "")[:80],
                    **an,
                    "moving_seconds": moving_s,
                    "norm_equiv_uncapped": norm_unc,
                    "norm_equiv_capped": norm_capped,
                }
            )

    # Option A threshold reconstructed with same selection logic, uncapped and capped variants.
    candidates_unc: list[tuple[float, float, int]] = []
    candidates_cap: list[tuple[float, float, int]] = []
    for row in per_activity:
        moving_seconds = float(row.get("moving_seconds") or 0.0)
        norm_unc = float(row.get("norm_equiv_uncapped") or 0.0)
        norm_cap = float(row.get("norm_equiv_capped") or 0.0)
        if moving_seconds < (30.0 * 60.0):
            continue

        sustained = min(1.0, moving_seconds / (60.0 * 60.0))
        weight = 0.85 + (0.15 * sustained)
        score_unc = norm_unc * weight
        score_cap = norm_cap * weight
        candidates_unc.append((score_unc, norm_unc, int(row["activity_id"])))
        candidates_cap.append((score_cap, norm_cap, int(row["activity_id"])))

    derived_capped = lm.derive_walk_hike_threshold_speed_m_min_from_activities(acts)

    print("=== CONSTANTS ===")
    print(f"eq_speed_cap_m_min={lm.WALK_HIKE_EQUIV_SPEED_M_MIN_MAX}")
    print(f"default_threshold_m_min={lm.WALK_HIKE_THRESHOLD_SPEED_M_MIN_DEFAULT}")
    print(f"acsm_positive_grade_max={lm.WALK_HIKE_ACSM_POSITIVE_GRADE_MAX}")
    print(f"same_constant={(lm.WALK_HIKE_EQUIV_SPEED_M_MIN_MAX == lm.WALK_HIKE_THRESHOLD_SPEED_M_MIN_DEFAULT)}")

    print("\n=== THREE_POINT_PROBE_V80 ===")
    for g, veq, src in _probe_points(80.0):
        print(f"grade={g:.3f} | veq_uncapped_m_min={veq:.3f} | src={src}")

    uphill_v, uphill_ex, full_v, full_ex = _monotonicity_sweep(speed_m_min=80.0, step=0.005)
    print("\n=== MONOTONICITY_SWEEP_V80 ===")
    print(f"uphill_violations_count={uphill_v}")
    print(f"full_range_violations_count={full_v}")
    if uphill_ex:
        print("uphill_examples=")
        for ex in uphill_ex:
            print(f"  {ex}")
    if full_ex:
        print("full_range_examples=")
        for ex in full_ex:
            print(f"  {ex}")

    print("\n=== PER_ACTIVITY_CLIP ===")
    for row in per_activity:
        print(
            " | ".join(
                [
                    f"id={row['activity_id']}",
                    f"type={row['type']}",
                    f"uphill_capped={int(row['uphill_capped'])}/{int(row['uphill_total'])}",
                    f"uphill_pct={row['uphill_capped_pct']:.2f}%",
                    f"all_pct={row['all_capped_pct']:.2f}%",
                    f"norm_unc={row['norm_equiv_uncapped']:.2f}",
                    f"norm_cap={row['norm_equiv_capped']:.2f}",
                ]
            )
        )

    sum_up_total = sum(int(r["uphill_total"]) for r in per_activity)
    sum_up_cap = sum(int(r["uphill_capped"]) for r in per_activity)
    sum_all_total = sum(int(r["all_total"]) for r in per_activity)
    sum_all_cap = sum(int(r["all_capped"]) for r in per_activity)

    print("\n=== AGGREGATE_CLIP ===")
    print(f"uphill_capped={sum_up_cap}/{sum_up_total} ({(100.0*sum_up_cap/sum_up_total) if sum_up_total else 0.0:.2f}%)")
    print(f"all_capped={sum_all_cap}/{sum_all_total} ({(100.0*sum_all_cap/sum_all_total) if sum_all_total else 0.0:.2f}%)")

    print("\n=== OPTION_A_TRACE ===")
    if candidates_unc:
        best_unc = max(candidates_unc, key=lambda x: x[0])
        print(
            f"best_uncapped_activity_id={best_unc[2]} raw_norm_equiv_uncapped_m_min={best_unc[1]:.4f} score_uncapped={best_unc[0]:.4f}"
        )
    else:
        print("best_uncapped_activity_id=NONE")

    if candidates_cap:
        best_cap = max(candidates_cap, key=lambda x: x[0])
        print(
            f"best_capped_activity_id={best_cap[2]} raw_norm_equiv_capped_m_min={best_cap[1]:.4f} score_capped={best_cap[0]:.4f}"
        )
    else:
        print("best_capped_activity_id=NONE")

    print(f"derived_threshold_current_impl_m_min={derived_capped}")


if __name__ == "__main__":
    asyncio.run(main())
