from __future__ import annotations

import asyncio
import csv
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from agent.load_metrics import estimate_session_tss, extract_activity_duration_hours, resolve_hr_profile_values
from agent.mcp_client import garmin_mcp_session
from tools.compare_strength_models_ab import (
    OUT_CSV,
    OUT_MD,
    _build_markdown,
    _call_tool_safe,
    _derive_max_hr_from_activities_by_date_paginated,
    _derive_max_hr_from_get_activities_paginated,
    _derive_resting_hr_from_rhr_day,
    _estimate_strength_tss_lthr,
    _extract_avg_hr,
    _extract_lthr_bpm,
    _looks_like_error,
    _metrics,
    _normalize_activity_payload,
    _resolve_robust_hr_max,
    _safe_json,
    _strength_model_a_diagnostics,
)

RETRY_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 2.5


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    txt = str(value).strip()
    if not txt:
        return None
    try:
        return float(txt)
    except Exception:
        return None


async def _resolve_strength_hr_params(session: Any) -> tuple[float | None, str, float | None, str, float | None]:
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
        hr_rest_derived = await _derive_resting_hr_from_rhr_day(session, days=14)
        if hr_rest_derived is not None:
            hr_rest_used = float(hr_rest_derived)
            hr_rest_source = "derived_get_rhr_day_14d"
        else:
            hr_rest_source = "fallback_50"

    hr_max_used = hr_max_profile
    hr_max_source = "profile"
    if hr_max_used is None:
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
        robust_hr_max, robust_tag = _resolve_robust_hr_max(by_date_vals + paged_vals)
        if robust_hr_max is not None:
            hr_max_used = robust_hr_max
            hr_max_source = (
                "derived_maxHR_paginated_robust"
                f"({robust_tag}; by_date={by_date_n}/{by_date_n_hr}, get_activities={paged_n}/{paged_n_hr})"
            )
        else:
            hr_max_source = "fallback_185"

    return hr_rest_used, hr_rest_source, hr_max_used, hr_max_source, lthr_bpm


async def _fetch_activity_with_retries(session: Any, activity_id: int) -> tuple[dict[str, Any] | None, str]:
    last_error = ""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        raw_activity = await _call_tool_safe(session, "get_activity", {"activity_id": activity_id})
        if not _looks_like_error(raw_activity):
            try:
                return _normalize_activity_payload(raw_activity), ""
            except Exception as ex:
                last_error = str(ex)
        else:
            last_error = str(raw_activity).strip()

        if attempt < RETRY_ATTEMPTS:
            print(
                f"[RETRY] activity {activity_id} attempt {attempt}/{RETRY_ATTEMPTS} failed; sleeping {RETRY_PAUSE_SECONDS:.1f}s",
                flush=True,
            )
            await asyncio.sleep(RETRY_PAUSE_SECONDS)

    return None, last_error or "unknown_get_activity_error"


async def main() -> None:
    load_dotenv(".env", override=False)

    if not OUT_CSV.exists():
        raise RuntimeError(f"CSV not found: {OUT_CSV}")

    with OUT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise RuntimeError("A/B CSV is empty")

    failed_idx = [i for i, r in enumerate(rows) if str(r.get("calc_error") or "").strip()]
    print(f"[RETRY] failed_rows_before={len(failed_idx)}", flush=True)

    if not failed_idx:
        print("[RETRY] nothing to retry")
        return

    hr_rest_used_for_report: float | None = None
    hr_rest_source_for_report = "unknown"
    hr_max_used_for_report: float | None = None
    hr_max_source_for_report = "unknown"
    lthr_bpm_for_report: float | None = None

    async with garmin_mcp_session(essential_only=False) as session:
        hr_rest_used, hr_rest_source, hr_max_used, hr_max_source, lthr_bpm = await _resolve_strength_hr_params(session)
        hr_rest_used_for_report = hr_rest_used
        hr_rest_source_for_report = hr_rest_source
        hr_max_used_for_report = hr_max_used
        hr_max_source_for_report = hr_max_source
        lthr_bpm_for_report = lthr_bpm
        print(
            f"[RETRY] params hr_rest={hr_rest_used} ({hr_rest_source}) hr_max={hr_max_used} ({hr_max_source}) lthr={lthr_bpm}",
            flush=True,
        )

        recovered = 0
        still_failed = 0

        for idx in failed_idx:
            row = rows[idx]
            activity_id = int(float(str(row.get("activity_id") or "0")))
            print(f"[RETRY] activity {activity_id}", flush=True)

            activity, err = await _fetch_activity_with_retries(session, activity_id)
            if activity is None:
                row["calc_error"] = err
                still_failed += 1
                continue

            try:
                hr_avg = _extract_avg_hr(activity)
                duration_h = extract_activity_duration_hours(activity)

                tss_a, label_a = estimate_session_tss(
                    activity,
                    hr_rest_bpm=hr_rest_used,
                    hr_max_bpm=hr_max_used,
                )
                tss_b, src_b = _estimate_strength_tss_lthr(
                    activity,
                    hr_rest_bpm=hr_rest_used,
                    lthr_bpm=lthr_bpm,
                )
                if tss_b is None:
                    tss_b = float(tss_a or 0.0)
                    src_b = f"fallback_model_a:{src_b}"

                hrr_raw_a, hrr_clamped_a, if_eff_a = _strength_model_a_diagnostics(
                    hr_avg,
                    hr_rest_bpm=hr_rest_used,
                    hr_max_bpm=hr_max_used,
                )

                row["average_hr"] = "" if hr_avg is None else str(float(hr_avg))
                row["duration_h"] = str(float(duration_h))
                row["tss_model_a"] = str(float(tss_a or 0.0))
                row["tss_model_b"] = str(float(tss_b or 0.0))
                row["model_a_label"] = str(label_a or "")
                row["model_b_source"] = str(src_b)
                row["hrr_model_a_raw"] = "" if hrr_raw_a is None else str(float(hrr_raw_a))
                row["hrr_model_a_clamped"] = "" if hrr_clamped_a is None else str(float(hrr_clamped_a))
                row["if_model_a_effective"] = "" if if_eff_a is None else str(float(if_eff_a))
                row["calc_error"] = ""
                recovered += 1
            except Exception as ex:
                row["calc_error"] = str(ex)
                still_failed += 1

    fieldnames = list(rows[0].keys())
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    ok_rows: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("calc_error") or "").strip():
            continue
        item = {
            "fecha": row.get("fecha") or "",
            "activity_id": int(float(str(row.get("activity_id") or "0"))),
            "segment": row.get("segment") or "",
            "tp_local": _to_float(row.get("tp_local")) or 0.0,
            "tss_model_a": _to_float(row.get("tss_model_a")),
            "tss_model_b": _to_float(row.get("tss_model_b")),
            "model_b_source": row.get("model_b_source") or "",
        }
        ok_rows.append(item)

    ok_rows.sort(key=lambda r: str(r.get("fecha") or ""), reverse=True)

    m_a = _metrics(ok_rows, "tss_model_a")
    m_b = _metrics(ok_rows, "tss_model_b")

    out_md = _build_markdown(
        rows=ok_rows,
        hr_rest_used=hr_rest_used_for_report,
        hr_rest_source=hr_rest_source_for_report,
        hr_max_used=hr_max_used_for_report,
        hr_max_source=hr_max_source_for_report,
        lthr_bpm=lthr_bpm_for_report,
        m_a=m_a,
        m_b=m_b,
    )
    OUT_MD.write_text(out_md, encoding="utf-8")

    print(f"[RETRY] recovered={recovered}")
    print(f"[RETRY] still_failed={still_failed}")
    print(f"[RETRY] rows_ok={len(ok_rows)}")
    print(f"[RETRY] model_a_mae={float(m_a['mae']):.6f}")
    print(f"[RETRY] model_b_mae={float(m_b['mae']):.6f}")
    print(f"output_csv={OUT_CSV.as_posix()}")
    print(f"output_md={OUT_MD.as_posix()}")


if __name__ == "__main__":
    asyncio.run(main())
