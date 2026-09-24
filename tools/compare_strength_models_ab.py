from __future__ import annotations

import asyncio
import csv
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from agent.load_metrics import estimate_session_tss, extract_activity_duration_hours, resolve_hr_profile_values
from agent.mcp_client import call_tool, garmin_mcp_session

INPUT_CSV = ROOT / "docs" / "tss_independiente_junio_a_septiembre_hasta_2026-09-16.csv"
OUT_CSV = ROOT / "docs" / "strength_models_ab_comparison_2026-09-24.csv"
OUT_MD = ROOT / "docs" / "strength_models_ab_comparison_2026-09-24.md"

_MCP_CALL_TIMEOUT_S = 60.0
_HR_MAX_HARD_CEILING = 210.0
_HR_MAX_MIN_VALID = 120.0
_HR_MAX_ROBUST_PERCENTILE = 0.95


async def _call_tool_safe(session: Any, tool_name: str, args: dict[str, Any]) -> Any:
    try:
        return await asyncio.wait_for(call_tool(session, tool_name, args), timeout=_MCP_CALL_TIMEOUT_S)
    except TimeoutError:
        return f"Error executing tool {tool_name}: timeout after {_MCP_CALL_TIMEOUT_S:.0f}s"


def _safe_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        txt = value.strip()
        if txt.startswith("{") or txt.startswith("["):
            try:
                return json.loads(txt)
            except Exception:
                return value
    return value


def _looks_like_error(raw: Any) -> bool:
    if not isinstance(raw, str):
        return False
    txt = raw.strip().lower()
    return txt.startswith("error") or "error executing tool" in txt


def _normalize_activity_payload(raw: Any) -> dict[str, Any]:
    payload = _safe_json(raw)
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict) and isinstance(payload.get("activity"), dict):
        payload = payload["activity"]
    if not isinstance(payload, dict):
        raise RuntimeError("get_activity returned non-dict payload")
    return payload


def _extract_avg_hr(activity: dict[str, Any]) -> float | None:
    summary = activity.get("summaryDTO") if isinstance(activity.get("summaryDTO"), dict) else {}
    for key in ("averageHR", "avgHr", "avg_hr_bpm", "averageHeartRate"):
        raw = activity.get(key)
        if raw is None:
            raw = summary.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except Exception:
            continue
        if val > 0:
            return val
    return None


def _extract_activity_max_hr(activity: dict[str, Any]) -> float | None:
    summary = activity.get("summaryDTO") if isinstance(activity.get("summaryDTO"), dict) else {}
    for key in ("maxHR", "maxHr", "max_hr_bpm", "maxHeartRate"):
        raw = activity.get(key)
        if raw is None:
            raw = summary.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except Exception:
            continue
        if val > 0:
            return val
    return None


def _extract_lthr_bpm(profile: dict[str, Any]) -> float | None:
    user_data = profile.get("userData") if isinstance(profile.get("userData"), dict) else {}
    perf = profile.get("performance") if isinstance(profile.get("performance"), dict) else {}
    candidates = (
        perf.get("hr_threshold_bpm"),
        perf.get("lthr_bpm"),
        perf.get("lactate_threshold_hr_bpm"),
        perf.get("lactate_threshold_heart_rate"),
        user_data.get("lactateThresholdHeartRate"),
        user_data.get("thresholdHeartRate"),
        profile.get("lthr_bpm"),
    )
    for raw in candidates:
        try:
            v = float(raw)
        except Exception:
            continue
        if 120.0 <= v <= 230.0:
            return v
    return None


def _extract_activities_list(payload: Any) -> list[dict[str, Any]]:
    data = _safe_json(payload)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("activities", "activityList", "items", "data", "result"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _extract_first_float(node: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(node, dict):
        for key in keys:
            raw = node.get(key)
            if raw is None:
                continue
            try:
                return float(raw)
            except Exception:
                continue
        for val in node.values():
            out = _extract_first_float(val, keys)
            if out is not None:
                return out
    elif isinstance(node, list):
        for item in node:
            out = _extract_first_float(item, keys)
            if out is not None:
                return out
    return None


def _compute_quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if q <= 0:
        return min(values)
    if q >= 1:
        return max(values)

    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1.0 - w) + xs[hi] * w


def _resolve_robust_hr_max(values: list[float]) -> tuple[float | None, str]:
    cleaned = [v for v in values if _HR_MAX_MIN_VALID <= float(v) <= _HR_MAX_HARD_CEILING]
    if not cleaned:
        return None, "no_valid_hrmax_samples"
    robust = _compute_quantile(cleaned, _HR_MAX_ROBUST_PERCENTILE)
    if robust is None:
        return None, "quantile_failed"
    return float(robust), (
        f"robust_p{int(_HR_MAX_ROBUST_PERCENTILE * 100)}"
        f"_cap{int(_HR_MAX_HARD_CEILING)}"
        f"_n{len(cleaned)}"
    )


def _strength_model_a_diagnostics(
    avg_hr: float | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
) -> tuple[float | None, float | None, float | None]:
    if avg_hr is None:
        return None, None, None
    try:
        avg = float(avg_hr)
        hr_rest = float(hr_rest_bpm) if hr_rest_bpm is not None else 50.0
        hr_max = float(hr_max_bpm) if hr_max_bpm is not None else 185.0
        if hr_rest <= 0:
            hr_rest = 50.0
        if hr_max <= hr_rest + 5.0:
            hr_max = hr_rest + 5.0
        hrr_raw = (avg - hr_rest) / (hr_max - hr_rest)
        hrr_clamped = max(0.20, min(0.85, hrr_raw))
        if_strength = max(0.45, min(0.85, 0.50 + (hrr_clamped * 0.40)))
        return hrr_raw, hrr_clamped, if_strength
    except Exception:
        return None, None, None


async def _derive_resting_hr_from_rhr_day(session: Any, days: int = 14) -> float | None:
    today = date.today()
    values: list[float] = []
    for i in range(days):
        d = today - timedelta(days=i)
        raw = await _call_tool_safe(session, "get_rhr_day", {"date": d.isoformat()})
        if _looks_like_error(raw):
            continue
        payload = _safe_json(raw)
        rhr = _extract_first_float(
            payload,
            (
                "restingHeartRate",
                "resting_heart_rate",
                "restingHeartRateValue",
                "resting_hr",
                "rhr",
                "value",
            ),
        )
        if rhr is None:
            continue
        if 30.0 <= float(rhr) <= 100.0:
            values.append(float(rhr))
    if not values:
        return None
    return sum(values) / len(values)


async def _derive_max_hr_from_activities_by_date_paginated(
    session: Any,
    start_date_iso: str = "2000-01-01",
    end_date_iso: str | None = None,
    page_size: int = 100,
    max_pages: int = 12,
) -> tuple[list[float], int, int]:
    end_iso = end_date_iso or date.today().isoformat()
    max_values: list[float] = []
    activities_seen = 0
    unique_ids: set[int] = set()

    for page in range(max_pages):
        raw = await _call_tool_safe(
            session,
            "get_activities_by_date",
            {
                "start_date": start_date_iso,
                "end_date": end_iso,
                "page": page,
                "page_size": page_size,
            },
        )
        if _looks_like_error(raw):
            break

        payload = _safe_json(raw)
        acts = _extract_activities_list(payload)
        if not acts:
            break

        new_rows = 0
        for act in acts:
            act_id = act.get("activityId") or act.get("activity_id")
            try:
                act_id_int = int(act_id)
            except Exception:
                act_id_int = None
            if act_id_int is not None and act_id_int in unique_ids:
                continue
            if act_id_int is not None:
                unique_ids.add(act_id_int)
            new_rows += 1
            activities_seen += 1
            max_hr = _extract_activity_max_hr(act)
            if max_hr is not None:
                max_values.append(max_hr)

        if new_rows == 0:
            break

        if page % 2 == 0:
            print(f"[AB] by_date page={page} rows_seen={activities_seen}", flush=True)

        has_more = False
        if isinstance(payload, dict):
            has_more = bool(payload.get("has_more"))
            count_page = payload.get("count")
            if isinstance(count_page, int) and count_page < page_size:
                has_more = False
        if not has_more and len(acts) < page_size:
            break
        if not has_more and len(acts) >= page_size:
            # Defensive: continue if provider omits has_more but page is full.
            continue

    return max_values, activities_seen, len(max_values)


async def _derive_max_hr_from_get_activities_paginated(
    session: Any,
    limit: int = 100,
    max_pages: int = 10,
) -> tuple[list[float], int, int]:
    max_values: list[float] = []
    activities_seen = 0
    unique_ids: set[int] = set()

    for page in range(max_pages):
        raw = await _call_tool_safe(session, "get_activities", {"start": page * limit, "limit": limit})
        if _looks_like_error(raw):
            break

        acts = _extract_activities_list(raw)
        if not acts:
            break

        new_rows = 0
        for act in acts:
            act_id = act.get("activityId") or act.get("activity_id")
            try:
                act_id_int = int(act_id)
            except Exception:
                act_id_int = None
            if act_id_int is not None and act_id_int in unique_ids:
                continue
            if act_id_int is not None:
                unique_ids.add(act_id_int)
            new_rows += 1
            activities_seen += 1
            max_hr = _extract_activity_max_hr(act)
            if max_hr is not None:
                max_values.append(max_hr)

        if new_rows == 0 or len(acts) < limit:
            break

        if page % 2 == 0:
            print(f"[AB] get_activities page={page} rows_seen={activities_seen}", flush=True)

    return max_values, activities_seen, len(max_values)


def _estimate_strength_tss_lthr(
    activity: dict[str, Any],
    hr_rest_bpm: float | None,
    lthr_bpm: float | None,
) -> tuple[float | None, str]:
    hours = extract_activity_duration_hours(activity)
    if hours <= 0:
        return 0.0, "no_duration"

    hr_avg = _extract_avg_hr(activity)
    if hr_avg is None:
        return 0.0, "no_avg_hr"

    if lthr_bpm is None:
        return None, "no_lthr"

    hr_rest = float(hr_rest_bpm) if hr_rest_bpm is not None else 50.0
    lthr = float(lthr_bpm)
    if lthr <= hr_rest + 5.0:
        return None, "invalid_lthr"

    z = (float(hr_avg) - hr_rest) / max(1.0, (lthr - hr_rest))
    z_c = max(0.0, min(1.15, z))
    if_strength = max(0.45, min(0.80, 0.46 + (0.28 * z_c)))
    tss = max(0.0, hours * (if_strength**2) * 100.0)
    return tss, "lthr_model"


def _segment_strength(name: str) -> str:
    txt = (name or "").lower()
    if "neuromuscular" in txt:
        return "neuromuscular"
    if any(k in txt for k in ("movilidad", "activacion", "activation", "foam", "complemento", "estabilidad")):
        return "movilidad_activacion"
    return "other_strength"


def _metrics(rows: list[dict[str, Any]], pred_key: str) -> dict[str, float | int]:
    valid = [r for r in rows if r.get(pred_key) is not None]
    if not valid:
        return {"n": 0, "mae": 0.0, "bias": 0.0, "ratio_mean": 0.0}
    deltas = [float(r[pred_key]) - float(r["tp_local"]) for r in valid]
    ratios = [(float(r[pred_key]) / float(r["tp_local"])) for r in valid if float(r["tp_local"]) > 0]
    return {
        "n": len(valid),
        "mae": fmean(abs(x) for x in deltas),
        "bias": fmean(deltas),
        "ratio_mean": fmean(ratios) if ratios else 0.0,
    }


def _load_strength_reference_rows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[int] = set()

    with INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("modalidad") or "").strip().lower() != "strength_training":
                continue

            try:
                activity_id = int((row.get("activity_id") or "").strip())
                tp_local = float((row.get("tp_local") or "").strip())
            except Exception:
                continue

            if activity_id in seen:
                continue
            seen.add(activity_id)

            out.append(
                {
                    "fecha": (row.get("fecha") or "").strip(),
                    "activity_id": activity_id,
                    "tp_local": tp_local,
                    "tp_unit": (row.get("tp_unit") or "").strip(),
                    "actividad_ref": (row.get("actividad") or "").strip(),
                }
            )

    out.sort(key=lambda r: r["fecha"], reverse=True)
    return out


def _build_markdown(
    rows: list[dict[str, Any]],
    hr_rest_used: float | None,
    hr_rest_source: str,
    hr_max_used: float | None,
    hr_max_source: str,
    lthr_bpm: float | None,
    m_a: dict[str, float | int],
    m_b: dict[str, float | int],
) -> str:
    lines: list[str] = []
    lines.append("# Strength Model A/B Comparison")
    lines.append("")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("Sample note: n is small (18 sessions); result is directional, not final calibration.")
    lines.append("Coefficients for model B were fixed before this comparison (no post-hoc tuning in this run).")
    lines.append("")
    lines.append("## Inputs")
    lines.append(f"- hr_rest_used: {round(hr_rest_used, 3) if hr_rest_used is not None else 'n/d'} ({hr_rest_source})")
    lines.append(f"- hr_max_used: {round(hr_max_used, 3) if hr_max_used is not None else 'n/d'} ({hr_max_source})")
    lines.append(f"- lthr_bpm: {round(lthr_bpm, 3) if lthr_bpm is not None else 'n/d'}")
    lines.append("")
    lines.append("## Aggregate")
    lines.append("| Model | n | MAE | Bias | Ratio mean |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(
        f"| A (HR reserve) | {int(m_a['n'])} | {float(m_a['mae']):.6f} | {float(m_a['bias']):.6f} | {float(m_a['ratio_mean']):.6f} |"
    )
    lines.append(
        f"| B (LTHR anchored) | {int(m_b['n'])} | {float(m_b['mae']):.6f} | {float(m_b['bias']):.6f} | {float(m_b['ratio_mean']):.6f} |"
    )
    lines.append("")
    lines.append("## Per-session")
    lines.append("| Date | Activity ID | Segment | TP | A | B | dA | dB | ratioA | ratioB | B source |")
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in rows:
        tp = float(r["tp_local"])
        a = r.get("tss_model_a")
        b = r.get("tss_model_b")
        da = (float(a) - tp) if a is not None else None
        db = (float(b) - tp) if b is not None else None
        ra = (float(a) / tp) if (a is not None and tp > 0) else None
        rb = (float(b) / tp) if (b is not None and tp > 0) else None
        lines.append(
            "| {date} | {aid} | {seg} | {tp:.3f} | {a} | {b} | {da} | {db} | {ra} | {rb} | {src} |".format(
                date=r.get("fecha") or "",
                aid=int(r.get("activity_id") or 0),
                seg=r.get("segment") or "",
                tp=tp,
                a=(f"{float(a):.3f}" if a is not None else "n/d"),
                b=(f"{float(b):.3f}" if b is not None else "n/d"),
                da=(f"{da:.3f}" if da is not None else "n/d"),
                db=(f"{db:.3f}" if db is not None else "n/d"),
                ra=(f"{ra:.3f}" if ra is not None else "n/d"),
                rb=(f"{rb:.3f}" if rb is not None else "n/d"),
                src=str(r.get("model_b_source") or ""),
            )
        )

    return "\n".join(lines) + "\n"


async def main() -> None:
    load_dotenv(".env", override=False)
    ref_rows = _load_strength_reference_rows()
    if not ref_rows:
        raise RuntimeError("No strength reference rows found")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: list[dict[str, Any]] = []

    async with garmin_mcp_session(essential_only=False) as session:
        print("[AB] fetching profile...", flush=True)
        raw_profile = await _call_tool_safe(session, "get_user_profile", {})
        if _looks_like_error(raw_profile):
            raise RuntimeError(str(raw_profile).strip())
        profile = _safe_json(raw_profile)
        if not isinstance(profile, dict):
            profile = {}

        hr_rest_profile, hr_max_profile = resolve_hr_profile_values(profile)
        lthr_bpm = _extract_lthr_bpm(profile)

        hr_rest_used = hr_rest_profile
        hr_rest_source = "profile"
        if hr_rest_used is None:
            print("[AB] deriving hr_rest from get_rhr_day...", flush=True)
            hr_rest_derived = await _derive_resting_hr_from_rhr_day(session, days=14)
            if hr_rest_derived is not None:
                hr_rest_used = float(hr_rest_derived)
                hr_rest_source = "derived_get_rhr_day_14d"
            else:
                hr_rest_source = "fallback_50"

        hr_max_used = hr_max_profile
        hr_max_source = "profile"
        if hr_max_used is None:
            print("[AB] deriving hr_max with paginated sources...", flush=True)
            by_date_vals, by_date_n, by_date_n_hr = await _derive_max_hr_from_activities_by_date_paginated(
                session,
                start_date_iso="2000-01-01",
                end_date_iso=date.today().isoformat(),
                page_size=100,
                max_pages=12,
            )
            paged_vals, paged_n, paged_n_hr = await _derive_max_hr_from_get_activities_paginated(
                session,
                limit=100,
                max_pages=10,
            )
            all_vals = by_date_vals + paged_vals
            robust_hr_max, robust_tag = _resolve_robust_hr_max(all_vals)
            if robust_hr_max is not None:
                hr_max_used = robust_hr_max
                hr_max_source = (
                    "derived_maxHR_paginated_robust"
                    f"({robust_tag}; by_date={by_date_n}/{by_date_n_hr}, get_activities={paged_n}/{paged_n_hr})"
                )
            else:
                hr_max_source = "fallback_185"

        for ref in ref_rows:
            activity_id = int(ref["activity_id"])
            row: dict[str, Any] = {
                "generated_at": generated_at,
                "activity_id": activity_id,
                "fecha": ref["fecha"],
                "actividad_ref": ref["actividad_ref"],
                "tp_local": float(ref["tp_local"]),
                "tp_unit": ref["tp_unit"],
                "average_hr": None,
                "duration_h": None,
                "segment": _segment_strength(str(ref.get("actividad_ref") or "")),
                "tss_model_a": None,
                "tss_model_b": None,
                "model_a_label": "",
                "model_b_source": "",
                "hrr_model_a_raw": None,
                "hrr_model_a_clamped": None,
                "if_model_a_effective": None,
                "calc_error": "",
            }

            try:
                print(f"[AB] activity {activity_id}...", flush=True)
                raw_activity = await _call_tool_safe(session, "get_activity", {"activity_id": activity_id})
                if _looks_like_error(raw_activity):
                    raise RuntimeError(str(raw_activity).strip())

                activity = _normalize_activity_payload(raw_activity)
                hr_avg = _extract_avg_hr(activity)
                duration_h = extract_activity_duration_hours(activity)

                tss_a, label_a = estimate_session_tss(
                    activity,
                    hr_rest_bpm=hr_rest_used,
                    hr_max_bpm=hr_max_used,
                )

                hrr_raw_a, hrr_clamped_a, if_eff_a = _strength_model_a_diagnostics(
                    hr_avg,
                    hr_rest_bpm=hr_rest_used,
                    hr_max_bpm=hr_max_used,
                )

                tss_b, src_b = _estimate_strength_tss_lthr(
                    activity,
                    hr_rest_bpm=hr_rest_used,
                    lthr_bpm=lthr_bpm,
                )
                if tss_b is None:
                    # Explicit fallback to model A when LTHR route is not usable.
                    tss_b = float(tss_a or 0.0)
                    src_b = f"fallback_model_a:{src_b}"

                row["average_hr"] = hr_avg
                row["duration_h"] = duration_h
                row["tss_model_a"] = float(tss_a or 0.0)
                row["tss_model_b"] = float(tss_b or 0.0)
                row["model_a_label"] = str(label_a or "")
                row["model_b_source"] = str(src_b)
                row["hrr_model_a_raw"] = hrr_raw_a
                row["hrr_model_a_clamped"] = hrr_clamped_a
                row["if_model_a_effective"] = if_eff_a
            except Exception as ex:
                row["calc_error"] = str(ex)

            rows.append(row)

    rows.sort(key=lambda r: str(r.get("fecha") or ""), reverse=True)

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        fieldnames = list(rows[0].keys()) if rows else []
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        if rows:
            writer.writerows(rows)

    ok_rows = [r for r in rows if not r.get("calc_error")]
    m_a = _metrics(ok_rows, "tss_model_a")
    m_b = _metrics(ok_rows, "tss_model_b")

    out_md = _build_markdown(
        rows=ok_rows,
        hr_rest_used=hr_rest_used,
        hr_rest_source=hr_rest_source,
        hr_max_used=hr_max_used,
        hr_max_source=hr_max_source,
        lthr_bpm=lthr_bpm,
        m_a=m_a,
        m_b=m_b,
    )
    OUT_MD.write_text(out_md, encoding="utf-8")

    print(f"output_csv={OUT_CSV.as_posix()}")
    print(f"output_md={OUT_MD.as_posix()}")
    print(f"rows_reference={len(ref_rows)}")
    print(f"rows_ok={len(ok_rows)}")
    print(f"model_a_mae={float(m_a['mae']):.6f}")
    print(f"model_b_mae={float(m_b['mae']):.6f}")


if __name__ == "__main__":
    asyncio.run(main())
