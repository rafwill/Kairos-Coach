from __future__ import annotations

import asyncio
import json
from collections import defaultdict
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

CAPS = [140.0, 160.0, 180.0, 200.0]


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


def _grade_bin(grade: float) -> str:
    pct = grade * 100.0
    if pct < 5:
        return "0-5%"
    if pct < 10:
        return "5-10%"
    if pct < 15:
        return "10-15%"
    if pct < 20:
        return "15-20%"
    if pct < 25:
        return "20-25%"
    if pct < 30:
        return "25-30%"
    return ">=30%"


def _manual_points() -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    speeds_m_min = [80.0, 90.0, 100.0]
    grades = [0.15, 0.18]

    for spd in speeds_m_min:
        speed_mps = spd / 60.0
        for g in grades:
            vo2, src = lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, g)
            pre = _pre_cap_equiv(vo2, src, speed_mps, g)
            points.append(
                {
                    "speed_m_min": spd,
                    "grade_pct": g * 100.0,
                    "src": 1.0 if src == "minetti" else 0.0,
                    "vo2": vo2,
                    "equiv_uncapped_m_min": pre,
                    "equiv_uncapped_kmh": pre * 0.06,
                }
            )

    return points


async def main() -> None:
    load_dotenv(".env", override=False)

    stats: dict[float, dict[str, Any]] = {
        cap: {
            "uphill_total": 0,
            "uphill_capped": 0,
            "all_total": 0,
            "all_capped": 0,
            "bins": defaultdict(int),
        }
        for cap in CAPS
    }

    async with garmin_mcp_session(essential_only=False) as session:
        for aid in REF_IDS:
            raw_d = await call_tool(session, "get_activity_details", {"activity_id": aid})
            samples = _extract_walk_samples(raw_d)

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
                equiv_pre = _pre_cap_equiv(vo2, src, speed_mps, grade)

                for cap in CAPS:
                    st = stats[cap]
                    st["all_total"] += 1
                    capped = equiv_pre > cap + 1e-9
                    if capped:
                        st["all_capped"] += 1

                    if grade > 0:
                        st["uphill_total"] += 1
                        if capped:
                            st["uphill_capped"] += 1
                            st["bins"][_grade_bin(grade)] += 1

                prev_dist = dist_cur if dist_cur is not None else prev_dist

    print("=== CAP_ORIGIN ===")
    print("WALK_HIKE_EQUIV_SPEED_M_MIN_MAX was introduced as a hard physical safeguard during stabilization; no external citation in-repo.")
    print(f"current_cap_m_min={lm.WALK_HIKE_EQUIV_SPEED_M_MIN_MAX}")

    print("\n=== MINETTI_MANUAL_POINTS ===")
    for p in _manual_points():
        src_name = "minetti" if p["src"] == 1.0 else "acsm"
        print(
            f"speed={p['speed_m_min']:.0f} m/min | grade={p['grade_pct']:.0f}% | src={src_name}"
            f" | vo2={p['vo2']:.2f} | equiv_uncapped={p['equiv_uncapped_m_min']:.2f} m/min"
            f" ({p['equiv_uncapped_kmh']:.2f} km/h)"
        )

    print("\n=== CAP_SENSITIVITY ===")
    for cap in CAPS:
        st = stats[cap]
        up_total = st["uphill_total"]
        up_cap = st["uphill_capped"]
        all_total = st["all_total"]
        all_cap = st["all_capped"]
        up_pct = (100.0 * up_cap / up_total) if up_total else 0.0
        all_pct = (100.0 * all_cap / all_total) if all_total else 0.0
        print(
            f"cap={cap:.0f} | uphill={up_cap}/{up_total} ({up_pct:.2f}%)"
            f" | all={all_cap}/{all_total} ({all_pct:.2f}%)"
        )

    print("\n=== CAP_SENSITIVITY_BY_BIN ===")
    bins_order = ["0-5%", "5-10%", "10-15%", "15-20%", "20-25%", "25-30%", ">=30%"]
    for cap in CAPS:
        st = stats[cap]
        print(f"cap={cap:.0f}")
        total = st["uphill_capped"]
        for b in bins_order:
            c = st["bins"].get(b, 0)
            share = (100.0 * c / total) if total else 0.0
            print(f"  {b}: {c} ({share:.2f}%)")


if __name__ == "__main__":
    asyncio.run(main())
