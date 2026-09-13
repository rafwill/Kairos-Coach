from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime
from statistics import median

from dotenv import load_dotenv

from agent.mcp_client import call_tool, garmin_mcp_session
from agent.trainer_agent import _resolve_hr_threshold_bpm
from agent.load_metrics import (
    TSS_FORMULA_VERSION,
    estimate_session_tss,
    resolve_hr_profile_values,
    resolve_running_threshold_pace_sec_per_km,
    _resolve_activity_duration_hours,
    _resolve_hr_threshold_bpm_for_activity,
    _extract_hr_samples_from_activity_details,
    _should_use_raw_hr_tss_for_fast_trail,
    _estimate_hr_tss_from_activity_details,
    _estimate_hr_tss_from_activity_details_lthr,
    _estimate_hr_tss_from_zones_lthr,
    _estimate_trail_hr_tss_tp_like,
    _estimate_hr_tss_from_zones,
    _estimate_tss_from_threshold_pace,
)

SRC = "docs/tss_comparativa_desde_2026-07-01.csv"
OUT = "docs/tss_trail_metodo_desde_2026-07-01.csv"

ANCHOR_EXPECTED = {
    23455001968: 1.000,
    24107904670: 1.023,
    23852863121: 1.001,
}
ANCHOR_TOLERANCE = 0.02


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * pct
    floor_idx = int(k)
    ceil_idx = min(floor_idx + 1, len(ordered) - 1)
    if floor_idx == ceil_idx:
        return float(ordered[floor_idx])
    frac = k - floor_idx
    return float(ordered[floor_idx] + (ordered[ceil_idx] - ordered[floor_idx]) * frac)


def _compute_hr_coverage_ratio(samples: list[tuple[float, float]], duration_seconds: float) -> float:
    if len(samples) < 5 or duration_seconds <= 0:
        return 0.0

    deltas = [
        samples[i + 1][0] - samples[i][0]
        for i in range(len(samples) - 1)
        if (samples[i + 1][0] - samples[i][0]) > 0
    ]
    step_guess = float(median(deltas)) if deltas else 1.0
    step_guess = max(1.0, min(30.0, step_guess))

    covered = 0.0
    for idx, (t_sec, _hr) in enumerate(samples):
        if idx + 1 < len(samples):
            dt = samples[idx + 1][0] - t_sec
            if dt <= 0:
                dt = step_guess
        else:
            dt = step_guess
        dt = max(1.0, min(30.0, float(dt)))
        covered += dt

    return covered / duration_seconds


def _derive_grade_diagnostics(details_raw: str) -> tuple[str, float | None, float | None]:
    payload = _safe_json(details_raw)
    if not isinstance(payload, dict):
        return "unavailable", None, None

    descriptors = payload.get("metricDescriptors")
    rows = payload.get("activityDetailMetrics")
    if not isinstance(descriptors, list) or not isinstance(rows, list):
        return "unavailable", None, None

    by_key: dict[str, dict] = {}
    for item in descriptors:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if isinstance(key, str) and key.strip():
            by_key[key] = item

    grades: list[float] = []
    explicit_key = None
    for key in by_key.keys():
        key_norm = key.lower()
        if ("grade" in key_norm or "slope" in key_norm or "incline" in key_norm) and "speed" not in key_norm:
            explicit_key = key
            break

    if explicit_key is not None:
        try:
            idx = int(by_key[explicit_key].get("metricsIndex"))
        except (TypeError, ValueError):
            idx = -1
        if idx >= 0:
            for row in rows:
                metrics = row.get("metrics") if isinstance(row, dict) else None
                if not isinstance(metrics, list) or idx >= len(metrics):
                    continue
                try:
                    value = float(metrics[idx])
                except (TypeError, ValueError):
                    continue
                if -2.0 <= value <= 2.0:
                    value *= 100.0
                if -60.0 <= value <= 60.0:
                    grades.append(value)

    grade_source = "explicit"
    if not grades:
        if "directElevation" not in by_key or "sumDistance" not in by_key:
            return "unavailable", None, None

        try:
            elev_idx = int(by_key["directElevation"].get("metricsIndex"))
            dist_idx = int(by_key["sumDistance"].get("metricsIndex"))
        except (TypeError, ValueError):
            return "unavailable", None, None

        prev_alt = None
        prev_dist = None
        for row in rows:
            metrics = row.get("metrics") if isinstance(row, dict) else None
            if not isinstance(metrics, list) or elev_idx >= len(metrics) or dist_idx >= len(metrics):
                continue
            try:
                alt = float(metrics[elev_idx])
                dist = float(metrics[dist_idx])
            except (TypeError, ValueError):
                continue

            if prev_alt is not None and prev_dist is not None:
                delta_dist = dist - prev_dist
                if delta_dist > 1.0:
                    grade = 100.0 * (alt - prev_alt) / delta_dist
                    if -60.0 <= grade <= 60.0:
                        grades.append(grade)
            prev_alt = alt
            prev_dist = dist

        grade_source = "derived"

    if not grades:
        return "unavailable", None, None

    p50 = _percentile(grades, 0.50)
    steep_up_share = sum(1 for grade in grades if grade > 10.0) / float(len(grades))
    return grade_source, p50, steep_up_share


def _safe_json(value):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except Exception:
                return value
    return value


def _looks_like_error(raw) -> bool:
    if not isinstance(raw, str):
        return False
    txt = raw.strip().lower()
    return txt.startswith("error") or "error executing tool" in txt


def _extract_ftp(payload):
    if payload is None:
        return None

    keys = (
        "cyclingFtp",
        "cycling_ftp",
        "ftp",
        "functionalThresholdPower",
        "functional_threshold_power",
        "functional_threshold_power_watts",
    )

    def _as_pos(raw):
        try:
            val = float(raw)
        except Exception:
            return None
        return val if val > 0 else None

    if isinstance(payload, (int, float, str)):
        return _as_pos(payload)

    if isinstance(payload, list):
        for item in payload:
            out = _extract_ftp(item)
            if out is not None:
                return out
        return None

    if isinstance(payload, dict):
        for key in keys:
            if key in payload:
                out = _as_pos(payload.get(key))
                if out is not None:
                    return out
        for nested in ("data", "result", "profile", "performance", "userData"):
            if nested in payload:
                out = _extract_ftp(payload.get(nested))
                if out is not None:
                    return out

    return None


def _trail_rows():
    rows = []
    with open(SRC, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("modalidad") or "").strip().lower() != "trail_running":
                continue

            tp_raw = (row.get("tp_local") or "").strip()
            old_calc = (row.get("nuevo_calculo") or "").strip()
            if not tp_raw or not old_calc:
                continue

            try:
                activity_id = int((row.get("activity_id") or "").strip())
                tp_local = float(tp_raw)
                old_tss = float(old_calc.split(" ", 1)[0])
            except Exception:
                continue

            rows.append(
                {
                    "activity_id": activity_id,
                    "fecha": (row.get("fecha") or "").strip(),
                    "actividad": (row.get("actividad") or "").strip(),
                    "tp_local": tp_local,
                    "tss_source": old_tss,
                }
            )
    return rows


def _infer_branch(activity, hours, details_raw, hr_zones_raw, run_thr, hr_rest, hr_max, lthr_profile):
    is_fast = bool(_should_use_raw_hr_tss_for_fast_trail(activity))
    lthr = _resolve_hr_threshold_bpm_for_activity(activity, lthr_profile)

    if is_fast:
        v = _estimate_hr_tss_from_activity_details(
            activity,
            hours=hours,
            activity_details_raw=details_raw,
            hr_rest_bpm=hr_rest,
            hr_max_bpm=hr_max,
            min_coverage_ratio=0.40,
        )
        if v is not None:
            return "fast_trail:hr_stream_hrr", is_fast, lthr

        v = _estimate_trail_hr_tss_tp_like(activity, hours=hours, hr_zones_raw=hr_zones_raw)
        if v is not None:
            return "fast_trail:tp_like", is_fast, lthr

        v = _estimate_hr_tss_from_zones(
            activity,
            hours=hours,
            hr_zones_raw=hr_zones_raw,
            hr_rest_bpm=hr_rest,
            hr_max_bpm=hr_max,
            apply_cap=False,
        )
        if v is not None:
            return "fast_trail:zones", is_fast, lthr

        return "fast_trail:none", is_fast, lthr

    if lthr is not None:
        v = _estimate_hr_tss_from_activity_details_lthr(
            activity,
            hours=hours,
            activity_details_raw=details_raw,
            hr_threshold_bpm=lthr,
            hr_rest_bpm=hr_rest,
            attenuate_sustained_low_if=True,
            min_coverage_ratio=0.40,
        )
        if v is not None:
            return "non_fast:lthr_details", is_fast, lthr

        v = _estimate_hr_tss_from_zones_lthr(
            activity,
            hours=hours,
            hr_threshold_bpm=lthr,
            hr_rest_bpm=hr_rest,
            hr_zones_raw=hr_zones_raw,
            min_coverage_ratio=0.35,
        )
        if v is not None:
            return "non_fast:lthr_zones", is_fast, lthr

        v = _estimate_tss_from_threshold_pace(
            activity,
            hours=hours,
            running_threshold_pace_sec_per_km=run_thr,
            prefer_effective_running_pace=True,
            if_pace_ceiling=1.50,
        )
        if v is not None:
            return "non_fast:lthr_pace", is_fast, lthr

        return "non_fast:lthr_none", is_fast, lthr

    v = _estimate_hr_tss_from_activity_details(
        activity,
        hours=hours,
        activity_details_raw=details_raw,
        hr_rest_bpm=hr_rest,
        hr_max_bpm=hr_max,
        min_coverage_ratio=0.40,
    )
    if v is not None:
        return "non_fast_no_lthr:hr_stream_hrr", is_fast, lthr

    v = _estimate_trail_hr_tss_tp_like(activity, hours=hours, hr_zones_raw=hr_zones_raw)
    if v is not None:
        return "non_fast_no_lthr:tp_like", is_fast, lthr

    v = _estimate_hr_tss_from_zones(
        activity,
        hours=hours,
        hr_zones_raw=hr_zones_raw,
        hr_rest_bpm=hr_rest,
        hr_max_bpm=hr_max,
        apply_cap=False,
    )
    if v is not None:
        return "non_fast_no_lthr:zones", is_fast, lthr

    return "non_fast_no_lthr:none", is_fast, lthr


async def main():
    load_dotenv(".env", override=False)
    trail_rows = _trail_rows()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    results = []
    errors = []

    async with garmin_mcp_session(essential_only=False) as session:
        raw_profile = await call_tool(session, "get_user_profile", {})
        profile = _safe_json(raw_profile)
        if not isinstance(profile, dict):
            profile = {}

        hr_rest, hr_max = resolve_hr_profile_values(profile)
        run_thr = resolve_running_threshold_pace_sec_per_km(profile)
        lthr_profile, _, _ = _resolve_hr_threshold_bpm(profile)
        ftp = _extract_ftp(profile)
        if ftp is None:
            raw_ftp = await call_tool(session, "get_cycling_ftp", {})
            ftp = _extract_ftp(_safe_json(raw_ftp))

        for row in trail_rows:
            activity_id = row["activity_id"]
            try:
                raw_activity = await call_tool(session, "get_activity", {"activity_id": activity_id})
                if _looks_like_error(raw_activity):
                    raise RuntimeError(str(raw_activity).strip())

                activity = _safe_json(raw_activity)
                if isinstance(activity, list) and activity:
                    activity = activity[0]
                if isinstance(activity, dict) and isinstance(activity.get("activity"), dict):
                    activity = activity["activity"]
                if not isinstance(activity, dict):
                    raise RuntimeError("get_activity returned non-dict payload")

                if not activity.get("name") and activity.get("activityName"):
                    activity["name"] = activity["activityName"]

                raw_zones = await call_tool(session, "get_activity_hr_in_timezones", {"activity_id": activity_id})
                zones_raw = None if _looks_like_error(raw_zones) else (raw_zones if isinstance(raw_zones, str) and raw_zones.strip() else None)

                raw_details = await call_tool(session, "get_activity_details", {"activity_id": activity_id})
                if _looks_like_error(raw_details):
                    raise RuntimeError(str(raw_details).strip())
                details_raw = raw_details if isinstance(raw_details, str) else json.dumps(raw_details, ensure_ascii=False)

                tss_live, public_method = estimate_session_tss(
                    activity,
                    ftp=ftp,
                    running_threshold_pace_sec_per_km=run_thr,
                    hr_rest_bpm=hr_rest,
                    hr_max_bpm=hr_max,
                    hr_zones_raw=zones_raw,
                    activity_details_raw=details_raw,
                    use_trail_splits=False,
                    hr_threshold_bpm=lthr_profile,
                )

                duration_hours = float(
                    _resolve_activity_duration_hours(
                        activity,
                        hr_zones_raw=zones_raw,
                        activity_details_raw=details_raw,
                    )
                    or 0.0
                )
                duration_seconds = duration_hours * 3600.0
                hr_samples = _extract_hr_samples_from_activity_details(details_raw, duration_seconds)
                hr_coverage_ratio = _compute_hr_coverage_ratio(hr_samples, duration_seconds)
                grade_source, grade_p50, grade_steep_up_share = _derive_grade_diagnostics(details_raw)

                branch, is_fast, lthr_used = _infer_branch(
                    activity,
                    duration_hours,
                    details_raw,
                    zones_raw,
                    run_thr,
                    hr_rest,
                    hr_max,
                    lthr_profile,
                )

                old_ratio = (row["tss_source"] / row["tp_local"]) if row["tp_local"] > 0 else 0.0
                new_ratio = (float(tss_live or 0.0) / row["tp_local"]) if row["tp_local"] > 0 else 0.0
                new_delta = float(tss_live or 0.0) - row["tp_local"]

                results.append(
                    {
                        "generated_at": generated_at,
                        "formula_version": TSS_FORMULA_VERSION,
                        "activity_id": activity_id,
                        "fecha": row["fecha"],
                        "actividad": row["actividad"],
                        "tp_local": row["tp_local"],
                        "tss_prod_corregido": row["tss_source"],
                        "ratio_tss_tp": round(old_ratio, 3),
                        "tss_recomputed_live": round(float(tss_live or 0.0), 1),
                        "recompute_delta_vs_source": round(float(tss_live or 0.0) - row["tss_source"], 1),
                        "ratio_recomputed_vs_tp": round(new_ratio, 3),
                        "delta_recomputed_vs_tp": round(new_delta, 1),
                        "duration_hours_resolved": round(duration_hours, 3),
                        "hr_coverage_ratio": round(hr_coverage_ratio, 3),
                        "grade_source": grade_source,
                        "grade_p50": round(float(grade_p50), 2) if grade_p50 is not None else "",
                        "grade_steep_up_share": round(float(grade_steep_up_share), 3) if grade_steep_up_share is not None else "",
                        "metodo_publico": public_method,
                        "metodo_calculo": branch,
                        "is_fast_trail": bool(is_fast),
                        "lthr_available": lthr_used is not None,
                        "lthr_bpm_used": round(float(lthr_used), 1) if lthr_used is not None else "",
                        "lthr_profile_resolved": round(float(lthr_profile), 1) if lthr_profile is not None else "",
                    }
                )
            except Exception as ex:
                errors.append({"activity_id": activity_id, "error": str(ex)})

    results.sort(key=lambda r: r["fecha"], reverse=True)

    if results:
        with open(OUT, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)

    print(f"script=tools/regenerate_trail_tss_table.py")
    print(f"formula_version={TSS_FORMULA_VERSION}")
    print(f"processed={len(results)}")
    print(f"errors={len(errors)}")
    for item in errors:
        print(f"error_activity_id={item['activity_id']} error={item['error']}")

    by_id = {r["activity_id"]: r for r in results}
    anchor_ok = True
    for aid, ref in ANCHOR_EXPECTED.items():
        row = by_id.get(aid)
        if not row:
            print(f"anchor_missing={aid}")
            anchor_ok = False
            continue
        ratio = float(row["ratio_recomputed_vs_tp"])
        diff = abs(ratio - ref)
        print(f"anchor={aid} ratio={ratio:.3f} target={ref:.3f} diff={diff:.3f}")
        if diff > ANCHOR_TOLERANCE:
            anchor_ok = False

    print(f"anchor_validation={'PASS' if anchor_ok else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
