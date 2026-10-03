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


def _pre_cap_equiv(vo2: float, src: str, speed_mps: float, grade: float) -> tuple[float, bool]:
    if src == "minetti":
        c0 = lm._walk_coste_minetti_j_kg_m(0.0)
        pre = (vo2 * lm.WALK_HIKE_J_PER_ML_O2) / c0 if c0 > 0 else 0.0
        return max(0.0, pre), False

    # ACSM branch
    pre = (vo2 - lm.WALK_HIKE_ACSM_RESTING_VO2_MLKGMIN) / lm.WALK_HIKE_ACSM_SPEED_COEFF
    ceiling_applied = False
    if src == "acsm" and grade > 0.0:
        ceiling = lm._walk_acsm_equiv_ceiling_from_minetti_cutoff(speed_mps)
        if pre > ceiling:
            pre = ceiling
            ceiling_applied = True
    return max(0.0, pre), ceiling_applied


def _grade_bin_label(grade: float) -> str:
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


def _monotonicity_violations(speed_m_min: float = 80.0, step: float = 0.005) -> list[dict[str, Any]]:
    speed_mps = speed_m_min / 60.0
    grades: list[float] = []
    g = -0.45
    while g <= 0.45 + 1e-12:
        grades.append(round(g, 6))
        g += step

    violations: list[dict[str, Any]] = []
    prev_g = None
    prev_v = None
    prev_src = None
    for gg in grades:
        vo2, src = lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, gg)
        veq = lm._walk_equivalent_flat_speed_from_vo2_m_min(vo2, src, speed_mps=speed_mps, grade=gg)
        if prev_v is not None and veq + 1e-9 < prev_v:
            violations.append(
                {
                    "g_prev": prev_g,
                    "v_prev": prev_v,
                    "src_prev": prev_src,
                    "g": gg,
                    "v": veq,
                    "src": src,
                }
            )
        prev_g = gg
        prev_v = veq
        prev_src = src

    return violations


async def main() -> None:
    load_dotenv(".env", override=False)

    cap = float(lm.WALK_HIKE_EQUIV_SPEED_M_MIN_MAX)

    all_uphill_capped = 0
    all_uphill = 0
    capped_grade_bins = defaultdict(int)
    capped_src = defaultdict(int)
    acsm_samples = 0
    acsm_ceiling_applied = 0
    acsm_capped = 0
    minetti_capped = 0

    async with garmin_mcp_session(essential_only=False) as session:
        for aid in REF_IDS:
            raw_a = await call_tool(session, "get_activity", {"activity_id": aid})
            raw_d = await call_tool(session, "get_activity_details", {"activity_id": aid})
            _ = _to_activity(raw_a)
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
                pre_cap, ceiling_applied = _pre_cap_equiv(vo2, src, speed_mps, grade)
                is_capped = pre_cap > cap + 1e-9

                if src == "acsm":
                    acsm_samples += 1
                    if ceiling_applied:
                        acsm_ceiling_applied += 1

                if grade > 0:
                    all_uphill += 1
                    if is_capped:
                        all_uphill_capped += 1
                        capped_grade_bins[_grade_bin_label(grade)] += 1
                        capped_src[src] += 1
                        if src == "acsm":
                            acsm_capped += 1
                        elif src == "minetti":
                            minetti_capped += 1

                prev_dist = dist_cur if dist_cur is not None else prev_dist

    violations = _monotonicity_violations(speed_m_min=80.0, step=0.005)
    neg = [v for v in violations if float(v["g"]) < 0]
    near_zero = [v for v in violations if -0.01 <= float(v["g"]) <= 0.03]
    above_cut = [v for v in violations if float(v["g"]) > 0.03]

    def _range_text(vs: list[dict[str, Any]]) -> str:
        if not vs:
            return "none"
        gs = [float(v["g"]) for v in vs]
        return f"{min(gs):.3f}..{max(gs):.3f}"

    print("=== VIOLATIONS_LOCATION ===")
    print(f"total_violations={len(violations)}")
    print(f"negative_grade_violations={len(neg)} grade_range={_range_text(neg)}")
    print(f"near_zero_to_cut_violations={len(near_zero)} grade_range={_range_text(near_zero)}")
    print(f"above_cut_violations={len(above_cut)} grade_range={_range_text(above_cut)}")
    print("sample_violations_first5=")
    for row in violations[:5]:
        print(
            f"g_prev={row['g_prev']:.3f} v_prev={row['v_prev']:.3f} src_prev={row['src_prev']}"
            f" -> g={row['g']:.3f} v={row['v']:.3f} src={row['src']}"
        )

    print("\n=== UPHILL_CLIPPING_DISTRIBUTION ===")
    pct = (100.0 * all_uphill_capped / all_uphill) if all_uphill else 0.0
    print(f"uphill_capped={all_uphill_capped}/{all_uphill} ({pct:.2f}%)")
    for k in ["0-5%", "5-10%", "10-15%", "15-20%", "20-25%", "25-30%", ">=30%"]:
        n = capped_grade_bins.get(k, 0)
        share = (100.0 * n / all_uphill_capped) if all_uphill_capped else 0.0
        print(f"bin={k} count={n} share_of_capped={share:.2f}%")

    print("\n=== ACSM_SAFEGUARD_IMPACT ===")
    print(f"acsm_samples_total={acsm_samples}")
    print(f"acsm_ceiling_applied={acsm_ceiling_applied}")
    print(f"acsm_capped_uphill={acsm_capped}")
    print(f"minetti_capped_uphill={minetti_capped}")
    print(f"capped_source_breakdown={dict(capped_src)}")


if __name__ == "__main__":
    asyncio.run(main())
