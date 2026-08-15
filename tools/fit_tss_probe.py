from __future__ import annotations

import argparse
import math
from pathlib import Path
from statistics import mean, pstdev

from fitparse import FitFile


def _sec_to_mmss(sec: float) -> str:
    sec = max(0.0, float(sec))
    mm = int(sec // 60)
    ss = int(round(sec % 60))
    if ss == 60:
        mm += 1
        ss = 0
    return f"{mm:02d}:{ss:02d}"


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_session(fit: FitFile) -> dict:
    for msg in fit.get_messages("session"):
        d = {f.name: f.value for f in msg.fields}
        timer_s = _safe_float(d.get("total_timer_time"))
        elapsed_s = _safe_float(d.get("total_elapsed_time"))
        dist_m = _safe_float(d.get("total_distance"))
        enh_speed = _safe_float(d.get("enhanced_avg_speed"))
        return {
            "sport": d.get("sport"),
            "sub_sport": d.get("sub_sport"),
            "timer_s": timer_s,
            "elapsed_s": elapsed_s,
            "dist_m": dist_m,
            "enh_avg_speed_ms": enh_speed,
            "avg_hr": _safe_float(d.get("avg_heart_rate")),
            "max_hr": _safe_float(d.get("max_heart_rate")),
            "training_stress_score": _safe_float(d.get("training_stress_score")),
            "intensity_factor": _safe_float(d.get("intensity_factor")),
            "total_training_effect": _safe_float(d.get("total_training_effect")),
        }
    return {}


def _extract_records(fit: FitFile) -> list[dict]:
    out: list[dict] = []
    for msg in fit.get_messages("record"):
        d = {f.name: f.value for f in msg.fields}
        speed = _safe_float(d.get("enhanced_speed") or d.get("speed"))
        ts = d.get("timestamp")
        if speed is None or ts is None:
            continue
        out.append({
            "timestamp": ts,
            "speed_ms": speed,
            "hr": _safe_float(d.get("heart_rate")),
        })
    return out


def _tss_from_single_if(hours: float, intensity_factor: float) -> float:
    return max(0.0, hours * (intensity_factor ** 2) * 100.0)


def _tss_by_session_average(session: dict, threshold_pace_sec_km: float) -> tuple[float, float, float]:
    timer_s = session.get("timer_s") or 0.0
    dist_m = session.get("dist_m") or 0.0
    if timer_s <= 0 or dist_m <= 0 or threshold_pace_sec_km <= 0:
        return 0.0, 0.0, 0.0

    pace_s_km = timer_s / (dist_m / 1000.0)
    intensity_factor = threshold_pace_sec_km / pace_s_km
    intensity_factor = max(0.50, min(1.30, intensity_factor))
    tss = _tss_from_single_if(timer_s / 3600.0, intensity_factor)
    return tss, intensity_factor, pace_s_km


def _tss_by_point_series(records: list[dict], threshold_pace_sec_km: float) -> tuple[float, float, dict]:
    if len(records) < 2 or threshold_pace_sec_km <= 0:
        return 0.0, 0.0, {"transitions": 0, "cv_if": 0.0, "work_blocks": 0, "rest_blocks": 0, "if_mean": 0.0}

    total_tss = 0.0
    total_h = 0.0
    if_values: list[float] = []
    segment_durations_h: list[float] = []

    for i in range(1, len(records)):
        prev = records[i - 1]
        cur = records[i]
        dt_s = (cur["timestamp"] - prev["timestamp"]).total_seconds()
        if dt_s <= 0 or dt_s > 30:
            continue

        speed = prev.get("speed_ms")
        if speed is None or speed <= 0:
            continue

        pace_s_km = 1000.0 / speed
        intensity_factor = threshold_pace_sec_km / pace_s_km
        intensity_factor = max(0.30, min(1.60, intensity_factor))

        h = dt_s / 3600.0
        total_tss += h * (intensity_factor ** 2) * 100.0
        total_h += h
        if_values.append(intensity_factor)
        segment_durations_h.append(h)

    if total_h <= 0 or not if_values:
        return 0.0, 0.0, {"transitions": 0, "cv_if": 0.0, "work_blocks": 0, "rest_blocks": 0, "if_mean": 0.0}

    if_mean = mean(if_values)
    rel_delta = 0.05
    work_floor = max(0.80, if_mean + rel_delta)
    rest_ceiling = min(0.85, if_mean - rel_delta)

    work_blocks = 0
    rest_blocks = 0
    transitions = 0
    prev_state = None
    for intensity_factor in if_values:
        # Combine absolute and relative bands: catches quality sessions when
        # threshold-derived IF stays sub-0.95 but oscillates repeatedly.
        if intensity_factor >= 0.95 or intensity_factor >= work_floor:
            state = "work"
        elif intensity_factor <= 0.75 or intensity_factor <= rest_ceiling:
            state = "rest"
        else:
            state = "middle"

        if prev_state is None:
            prev_state = state
            if state == "work":
                work_blocks += 1
            elif state == "rest":
                rest_blocks += 1
            continue

        if state != prev_state:
            transitions += 1
            if state == "work":
                work_blocks += 1
            elif state == "rest":
                rest_blocks += 1
            prev_state = state

    np_if = math.sqrt(total_tss / (total_h * 100.0))
    cv_if = (pstdev(if_values) / mean(if_values)) if len(if_values) > 2 and mean(if_values) > 0 else 0.0
    details = {
        "transitions": transitions,
        "cv_if": cv_if,
        "work_blocks": work_blocks,
        "rest_blocks": rest_blocks,
        "if_mean": if_mean,
    }
    return total_tss, np_if, details


def _interval_likelihood(details: dict) -> tuple[str, str]:
    transitions = int(details.get("transitions") or 0)
    cv_if = float(details.get("cv_if") or 0.0)
    work_blocks = int(details.get("work_blocks") or 0)
    rest_blocks = int(details.get("rest_blocks") or 0)
    if_mean = float(details.get("if_mean") or 0.0)

    if transitions >= 18 and cv_if >= 0.14 and work_blocks >= 4 and rest_blocks >= 4:
        return "high", "Pattern strongly suggests interval work"
    if transitions >= 10 and cv_if >= 0.11 and work_blocks >= 2 and rest_blocks >= 2:
        return "medium", "Pattern suggests mixed/quality session"
    if transitions >= 8 and cv_if >= 0.10 and work_blocks >= 2 and if_mean <= 0.80:
        return "medium", "Pattern suggests under-threshold interval/fartlek session"
    return "low", "Pattern closer to steady/continuous run"


def analyze_fit(path: Path, threshold_pace_sec_km: float) -> None:
    fit = FitFile(str(path))
    session = _extract_session(fit)
    records = _extract_records(fit)

    print(f"File: {path}")
    if not session:
        print("No session message found.")
        return

    print("Session")
    print(f"  sport={session.get('sport')} sub_sport={session.get('sub_sport')}")
    print(f"  timer={session.get('timer_s')}s elapsed={session.get('elapsed_s')}s dist={session.get('dist_m')}m")
    print(f"  avg_hr={session.get('avg_hr')} max_hr={session.get('max_hr')}")
    print(f"  embedded_tss={session.get('training_stress_score')} embedded_if={session.get('intensity_factor')}")
    print(f"  records={len(records)}")

    avg_tss, avg_if, avg_pace = _tss_by_session_average(session, threshold_pace_sec_km)
    series_tss, series_if, details = _tss_by_point_series(records, threshold_pace_sec_km)

    print("\nComputed")
    print(f"  threshold={_sec_to_mmss(threshold_pace_sec_km)}/km ({threshold_pace_sec_km:.1f} s)")
    print(f"  avg_method:    pace={_sec_to_mmss(avg_pace)}/km ({avg_pace:.1f} s) IF={avg_if:.4f} TSS={avg_tss:.2f}")
    print(f"  series_method: IF={series_if:.4f} TSS={series_tss:.2f}")
    print(f"  delta(series-avg)={series_tss - avg_tss:+.2f}")

    likelihood, reason = _interval_likelihood(details)
    print("\nInterval detection")
    print(f"  likelihood={likelihood}")
    print(f"  transitions={details['transitions']} work_blocks={details['work_blocks']} rest_blocks={details['rest_blocks']} cv_if={details['cv_if']:.3f} if_mean={details['if_mean']:.3f}")
    print(f"  reason={reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe TSS from FIT files using average vs point-series methods.")
    parser.add_argument("fit_files", nargs="+", help="Path(s) to FIT activity file(s)")
    parser.add_argument("--threshold", type=float, required=True, help="Running threshold pace in min/km encoded as MMSS seconds (e.g. 245 for 4:05 min/km)")
    args = parser.parse_args()

    for item in args.fit_files:
        path = Path(item)
        if not path.exists():
            print(f"File not found: {path}")
            continue
        analyze_fit(path, args.threshold)
        print("\n" + "=" * 100 + "\n")


if __name__ == "__main__":
    main()
