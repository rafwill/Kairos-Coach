"""
Reusable load metrics calculations.

This module centralizes TSS estimation and ATL/CTL/TSB computations so it can
be imported by other projects without depending on chat or storage layers.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)


# Increase when TSS formula behavior changes.
TSS_FORMULA_VERSION = 16

# Fast trail threshold (< 6:00/km) where raw zone hrTSS is explicitly preferred.
TRAIL_FAST_PACE_RAW_ZONES_SEC_PER_KM = 6 * 60


def _is_cycling_activity(act_type: Any) -> bool:
    if isinstance(act_type, dict):
        act_type = str(act_type.get("typeKey") or act_type.get("typeName") or "")
    t = str(act_type or "").lower()
    return any(kw in t for kw in ("cycling", "biking", "bike", "virtual_ride", "bmx", "cicl"))


def _is_strength_activity(act_type: Any) -> bool:
    if isinstance(act_type, dict):
        act_type = str(act_type.get("typeKey") or act_type.get("typeName") or "")
    t = str(act_type or "").lower()
    return any(kw in t for kw in ("strength", "fuerza", "weight", "gym", "functional_strength"))


def _is_trail_hike_walk_activity(act_type: Any) -> bool:
    if isinstance(act_type, dict):
        act_type = str(act_type.get("typeKey") or act_type.get("typeName") or "")
    t = str(act_type or "").lower()
    return any(kw in t for kw in ("trail", "hike", "hiking", "sender", "trek", "walk", "camin"))


def _is_trail_activity(act_type: Any) -> bool:
    if isinstance(act_type, dict):
        act_type = str(act_type.get("typeKey") or act_type.get("typeName") or "")
    t = str(act_type or "").lower()
    return "trail" in t


def _is_hike_walk_activity(act_type: Any) -> bool:
    if _is_trail_activity(act_type):
        return False
    if isinstance(act_type, dict):
        act_type = str(act_type.get("typeKey") or act_type.get("typeName") or "")
    t = str(act_type or "").lower()
    return any(kw in t for kw in ("hike", "hiking", "sender", "trek", "walk", "camin"))


def _is_running_non_trail_activity(act_type: Any) -> bool:
    if _is_trail_hike_walk_activity(act_type):
        return False
    if isinstance(act_type, dict):
        act_type = str(act_type.get("typeKey") or act_type.get("typeName") or "")
    t = str(act_type or "").lower()
    return any(kw in t for kw in ("running", "run", "corr"))


def _to_iso_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    if "T" in text:
        text = text.split("T", 1)[0]

    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _extract_training_load_points(payload: Any) -> list[dict]:
    points: list[dict] = []

    def _walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                _walk(item)
            return

        if not isinstance(node, dict):
            return

        d_iso = _to_iso_date(
            node.get("date")
            or node.get("calendarDate")
            or node.get("day")
            or node.get("start_date")
        )
        load_value = (
            node.get("trainingLoad")
            or node.get("training_load")
            or node.get("load")
            or node.get("loadValue")
            or node.get("dailyLoad")
            or node.get("loadScore")
        )

        if d_iso and load_value is not None:
            try:
                load_float = max(0.0, float(load_value))
                points.append({"date": d_iso, "tss": load_float})
            except (TypeError, ValueError):
                log.debug("training_load point invalido para fecha %s: %r", d_iso, load_value)

        for value in node.values():
            if isinstance(value, (list, dict)):
                _walk(value)

    _walk(payload)
    return points


def _extract_activity_duration_hours(activity: dict) -> float:
    duration_seconds = (
        activity.get("duration_seconds")
        or activity.get("duration")
        or activity.get("durationInSeconds")
        or activity.get("elapsedDuration")
        or activity.get("movingDuration")
        or activity.get("moving_duration_seconds")
        or 0
    )
    try:
        return max(0.0, float(duration_seconds) / 3600.0)
    except (TypeError, ValueError):
        return 0.0


def _extract_activity_distance_km(activity: dict) -> float | None:
    for key, in_meters in (
        ("distance", True),
        ("distance_meters", True),
        ("distance_m", True),
        ("distanceInMeters", True),
        ("totalDistanceInMeters", True),
        ("distanceKm", False),
        ("distance_km", False),
    ):
        raw = activity.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val <= 0:
            continue
        return (val / 1000.0) if in_meters else val
    return None


def _parse_pace_to_sec_per_km(raw: Any) -> float | None:
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        v = float(raw)
        if v <= 0:
            return None
        if v < 20:
            return v * 60.0
        return v

    text = str(raw).strip().lower()
    if not text:
        return None

    mmss = re.search(r"(\d{1,2})\s*[:m]\s*(\d{1,2})", text)
    if mmss:
        mm = int(mmss.group(1))
        ss = int(mmss.group(2))
        if mm >= 0 and 0 <= ss < 60:
            return mm * 60.0 + ss

    number = re.search(r"(\d+(?:[\.,]\d+)?)", text)
    if not number:
        return None
    try:
        v = float(number.group(1).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None

    if "km/h" in text or "kph" in text:
        return 3600.0 / v
    if "m/s" in text:
        return 1000.0 / v
    if v < 20:
        return v * 60.0
    return v


def _speed_ms_to_pace_sec_per_km(raw_speed: Any) -> float | None:
    if raw_speed is None:
        return None
    try:
        speed_ms = float(raw_speed)
    except (TypeError, ValueError):
        return None
    if speed_ms <= 0:
        return None

    if 0.2 <= speed_ms <= 1.2:
        speed_ms *= 10.0

    if speed_ms < 1.5 or speed_ms > 8.5:
        return None
    return 1000.0 / speed_ms


def _extract_avg_pace_sec_per_km(activity: dict) -> float | None:
    for key in (
        "averagePaceSecPerKm",
        "average_pace_sec_per_km",
        "avgPaceSecPerKm",
        "averagePace",
        "avgPace",
        "pace",
    ):
        pace = _parse_pace_to_sec_per_km(activity.get(key))
        if pace and pace > 0:
            return pace

    distance_km = _extract_activity_distance_km(activity)
    hours = _extract_activity_duration_hours(activity)
    if distance_km and distance_km > 0 and hours > 0:
        return (hours * 3600.0) / distance_km
    return None


def _extract_running_effective_pace_sec_per_km(activity: dict) -> float | None:
    for key in (
        "normalizedPaceSecPerKm",
        "normalized_pace_sec_per_km",
        "normalizedPace",
        "normalized_pace",
        "gradeAdjustedPaceSecPerKm",
        "grade_adjusted_pace_sec_per_km",
        "gradeAdjustedPace",
        "grade_adjusted_pace",
        "movingPaceSecPerKm",
        "moving_pace_sec_per_km",
        "averageMovingPace",
        "avgMovingPace",
        "movingPace",
    ):
        pace = _parse_pace_to_sec_per_km(activity.get(key))
        if pace and pace > 0:
            return pace
    return _extract_avg_pace_sec_per_km(activity)


def _should_use_raw_hr_tss_for_fast_trail(activity: dict) -> bool:
    if not isinstance(activity, dict):
        return False

    for key in (
        "finalPaceSecPerKm",
        "final_pace_sec_per_km",
        "lastPaceSecPerKm",
        "last_pace_sec_per_km",
        "finalPace",
        "final_pace",
    ):
        pace = _parse_pace_to_sec_per_km(activity.get(key))
        if pace and pace > 0:
            return float(pace) < float(TRAIL_FAST_PACE_RAW_ZONES_SEC_PER_KM)

    pace_effective = _extract_running_effective_pace_sec_per_km(activity)
    if not pace_effective or pace_effective <= 0:
        return False
    return float(pace_effective) < float(TRAIL_FAST_PACE_RAW_ZONES_SEC_PER_KM)


def _extract_training_load_tss(activity: dict) -> float | None:
    for key in (
        "trainingStressScore",
        "trainingLoad",
        "training_load",
        "activityTrainingLoad",
        "loadValue",
    ):
        raw_load = activity.get(key)
        if raw_load is None:
            continue
        try:
            val = float(raw_load)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return max(0.0, val)
    return None


def _estimate_if_from_hr(
    activity: dict,
    cycling_formula: bool,
    hr_rest_bpm: float | None = None,
    hr_max_bpm: float | None = None,
) -> float | None:
    avg_hr_raw = (
        activity.get("averageHR")
        or activity.get("avgHr")
        or activity.get("avg_hr_bpm")
        or activity.get("averageHeartRate")
    )
    max_hr_raw = (
        activity.get("maxHR")
        or activity.get("maxHr")
        or activity.get("max_hr_bpm")
        or activity.get("maxHeartRate")
    )
    if avg_hr_raw is None:
        return None
    try:
        avg_hr = float(avg_hr_raw)
        hr_rest = float(hr_rest_bpm) if hr_rest_bpm else 50.0
        hr_max = float(max_hr_raw) if max_hr_raw else (float(hr_max_bpm) if hr_max_bpm else 185.0)
        if hr_rest <= 0:
            hr_rest = 50.0
        if hr_max <= 0:
            hr_max = 185.0
        hr_max = max(hr_max, avg_hr + 5.0)
        hr_rest = min(hr_rest, avg_hr - 5.0)

        hrr = (avg_hr - hr_rest) / (hr_max - hr_rest)
        hrr = max(0.30, min(1.00, hrr))

        if cycling_formula:
            return max(0.35, min(1.05, hrr))
        return max(0.50, min(1.05, 0.40 + hrr * 0.65))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _find_hr_zones_in_json(data: Any) -> list[dict] | None:
    if isinstance(data, list):
        zone_like = [
            x
            for x in data
            if isinstance(x, dict) and (x.get("zoneNumber") is not None or x.get("zone_number") is not None)
        ]
        if zone_like and len(zone_like) >= 3:
            return zone_like
        for item in data:
            result = _find_hr_zones_in_json(item)
            if result:
                return result

    elif isinstance(data, dict):
        for key in (
            "heartRateTimeInZone",
            "heartRateZones",
            "hrTimeInZones",
            "timeInHeartRateZones",
            "heartRateTimeInZones",
            "hrZones",
            "zones",
            "hr_zones",
            "timeInZone",
            "timeInZones",
        ):
            val = data.get(key)
            if isinstance(val, list) and len(val) >= 3:
                result = _find_hr_zones_in_json(val)
                if result:
                    return result
        for val in data.values():
            if isinstance(val, (dict, list)):
                result = _find_hr_zones_in_json(val)
                if result:
                    return result

    return None


def _parse_hr_zones_list(raw: str | None) -> list[dict] | None:
    if not raw or not raw.strip():
        return None
    stripped = raw.strip()
    if stripped in ("null", "[]", "{}", "(sin datos)"):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None

    zones_raw = _find_hr_zones_in_json(data)
    if not zones_raw:
        return None

    normalized: list[dict] = []
    for z in zones_raw:
        if not isinstance(z, dict):
            continue

        zone_num = z.get("zoneNumber") or z.get("zone_number") or z.get("zone") or z.get("zoneNum") or 0
        try:
            zone_num = int(zone_num)
        except (TypeError, ValueError):
            zone_num = 0

        secs = (
            z.get("secsInZone")
            or z.get("secs_in_zone")
            or z.get("timeInZone")
            or z.get("time_in_zone")
            or z.get("seconds")
            or z.get("durationSeconds")
            or 0
        )
        try:
            secs = float(secs)
        except (TypeError, ValueError):
            secs = 0.0

        pct_direct = z.get("percentInZone") or z.get("percent_in_zone") or z.get("percentage")
        try:
            pct_direct = float(pct_direct) if pct_direct is not None else None
        except (TypeError, ValueError):
            pct_direct = None

        lo = (
            z.get("minHeartRateIn")
            or z.get("min_heart_rate_in")
            or z.get("zoneLow")
            or z.get("zone_low")
            or z.get("zoneLowBoundary")
            or z.get("zone_low_boundary")
            or z.get("minHr")
            or "?"
        )
        hi = (
            z.get("maxHeartRateIn")
            or z.get("max_heart_rate_in")
            or z.get("zoneHigh")
            or z.get("zone_high")
            or z.get("maxHr")
            or "?"
        )

        zone_name = z.get("zoneName") or z.get("zone_name") or z.get("name") or f"Z{zone_num}"

        if secs > 0 or pct_direct is not None:
            normalized.append(
                {
                    "zoneNumber": zone_num,
                    "secsInZone": secs,
                    "pctDirect": pct_direct,
                    "minHeartRateIn": lo,
                    "maxHeartRateIn": hi,
                    "zoneName": zone_name,
                }
            )

    return normalized if normalized else None


def _estimate_hr_tss_from_zones(
    activity: dict,
    hours: float,
    hr_zones_raw: str | None = None,
    hr_rest_bpm: float | None = None,
    hr_max_bpm: float | None = None,
    apply_cap: bool = True,
    min_coverage_ratio: float = 0.0,
) -> float | None:
    if hours <= 0:
        return None

    zones = _parse_hr_zones_list(hr_zones_raw) if hr_zones_raw else None
    if not zones and isinstance(activity, dict):
        for key in (
            "heartRateZones",
            "hr_zones",
            "hrZones",
            "timeInHeartRateZones",
            "heartRateTimeInZones",
            "zones",
        ):
            raw_z = activity.get(key)
            if not raw_z:
                continue
            try:
                zones = _parse_hr_zones_list(json.dumps(raw_z, ensure_ascii=False))
            except (TypeError, ValueError, OverflowError):
                zones = None
            if zones:
                break

    if not zones:
        return None

    avg_hr_raw = (
        activity.get("averageHR")
        or activity.get("avgHr")
        or activity.get("avg_hr_bpm")
        or activity.get("averageHeartRate")
    )
    max_hr_raw = (
        activity.get("maxHR")
        or activity.get("maxHr")
        or activity.get("max_hr_bpm")
        or activity.get("maxHeartRate")
    )

    try:
        avg_hr = float(avg_hr_raw) if avg_hr_raw is not None else None
        hr_rest = float(hr_rest_bpm) if hr_rest_bpm else 50.0
        hr_max = float(max_hr_raw) if max_hr_raw is not None else (float(hr_max_bpm) if hr_max_bpm else 185.0)
        if hr_rest <= 0:
            hr_rest = 50.0
        if hr_max <= 0:
            hr_max = 185.0
        if avg_hr is not None:
            hr_max = max(hr_max, avg_hr + 5.0)
            hr_rest = min(hr_rest, avg_hr - 5.0)
    except (TypeError, ValueError):
        return None

    dur_s = hours * 3600.0
    denom = max(1.0, hr_max - hr_rest)
    total_secs = 0.0
    tss_total = 0.0

    for z in zones:
        if not isinstance(z, dict):
            continue

        secs = 0.0
        try:
            secs = float(z.get("secsInZone") or 0.0)
        except (TypeError, ValueError):
            secs = 0.0

        if secs <= 0:
            try:
                pct = z.get("pctDirect")
                if pct is not None:
                    secs = max(0.0, float(pct) / 100.0 * dur_s)
            except (TypeError, ValueError):
                secs = 0.0

        if secs <= 0:
            continue

        lo_raw = z.get("minHeartRateIn")
        hi_raw = z.get("maxHeartRateIn")
        lo = hi = None
        try:
            if lo_raw not in (None, "?"):
                lo = float(lo_raw)
        except (TypeError, ValueError):
            lo = None
        try:
            if hi_raw not in (None, "?"):
                hi = float(hi_raw)
        except (TypeError, ValueError):
            hi = None

        if lo is not None and hi is not None and hi < lo:
            lo, hi = hi, lo

        if lo is not None and hi is not None:
            hr_mid = (lo + hi) / 2.0
        elif lo is not None:
            hr_mid = lo + 5.0
        elif hi is not None:
            hr_mid = hi - 5.0
        else:
            continue

        hrr = (hr_mid - hr_rest) / denom
        hrr = max(0.30, min(1.00, hrr))
        if_zone = max(0.50, min(1.05, 0.40 + hrr * 0.65))

        h = secs / 3600.0
        tss_total += h * (if_zone**2) * 100.0
        total_secs += secs

    if total_secs <= 0:
        return None

    coverage_ratio = total_secs / dur_s if dur_s > 0 else 0.0
    if coverage_ratio < max(0.0, float(min_coverage_ratio or 0.0)):
        return None

    if apply_cap:
        return max(0.0, tss_total)
    return max(0.0, tss_total)


def _resolve_hr_profile_values(profile: dict | None) -> tuple[float | None, float | None]:
    if not isinstance(profile, dict):
        return None, None

    perf = profile.get("performance") if isinstance(profile.get("performance"), dict) else {}
    health = profile.get("health") if isinstance(profile.get("health"), dict) else {}

    hr_rest_candidates = [
        perf.get("resting_hr"),
        perf.get("restingHeartRate"),
        perf.get("resting_heart_rate"),
        health.get("resting_hr"),
        health.get("restingHeartRate"),
        health.get("resting_heart_rate"),
        profile.get("resting_hr"),
        profile.get("restingHeartRate"),
        profile.get("resting_heart_rate"),
        profile.get("rhr"),
    ]
    hr_max_candidates = [
        perf.get("max_hr"),
        perf.get("maxHeartRate"),
        perf.get("max_heart_rate"),
        health.get("max_hr"),
        health.get("maxHeartRate"),
        health.get("max_heart_rate"),
        profile.get("max_hr"),
        profile.get("maxHeartRate"),
        profile.get("max_heart_rate"),
    ]

    def _pick(candidates: list[Any], min_v: float, max_v: float) -> float | None:
        for raw in candidates:
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if min_v <= val <= max_v:
                return val
        return None

    return _pick(hr_rest_candidates, 30.0, 100.0), _pick(hr_max_candidates, 120.0, 240.0)


def _extract_threshold_pace_sec_per_km(
    activity: dict, running_threshold_pace_sec_per_km: float | None = None
) -> float | None:
    if running_threshold_pace_sec_per_km and running_threshold_pace_sec_per_km > 0:
        return float(running_threshold_pace_sec_per_km)

    for key in (
        "thresholdPaceSecPerKm",
        "threshold_pace_sec_per_km",
        "lactateThresholdPace",
        "lactate_threshold_pace",
        "thresholdPace",
        "threshold_pace",
        "paceAtLactateThreshold",
    ):
        pace = _parse_pace_to_sec_per_km(activity.get(key))
        if pace and pace > 0:
            return pace

    for speed_key in (
        "lactate_threshold_speed_mps",
        "lactateThresholdSpeed",
        "lactate_threshold_speed",
        "thresholdSpeed",
        "threshold_speed",
    ):
        pace = _speed_ms_to_pace_sec_per_km(activity.get(speed_key))
        if pace and pace > 0:
            return pace

    return None


def _estimate_if_from_rpe(activity: dict) -> float | None:
    raw = (
        activity.get("rpe")
        or activity.get("sessionRpe")
        or activity.get("session_rpe")
        or activity.get("perceivedExertion")
        or activity.get("perceived_exertion")
        or activity.get("effort")
    )
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        rpe = float(raw)
    else:
        raw_text = str(raw)
        fraction_match = re.search(r"(?<!\d)(\d+(?:[\.,]\d+)?)\s*/\s*10(?:[\.,]0+)?\b", raw_text)
        if fraction_match:
            rpe = float(fraction_match.group(1).replace(",", "."))
        else:
            nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[\.,]\d+)?", raw_text)]
            if not nums:
                return None
            rpe = sum(nums) / len(nums)

    if rpe <= 0:
        return None
    rpe = max(1.0, min(10.0, rpe))
    return max(0.45, min(1.05, 0.40 + (rpe / 10.0) * 0.60))


def _extract_strength_rpe_10(activity: dict) -> float | None:
    raw = (
        activity.get("rpe")
        or activity.get("sessionRpe")
        or activity.get("session_rpe")
        or activity.get("workout_rpe")
        or activity.get("workoutRpe")
        or activity.get("perceivedExertion")
        or activity.get("perceived_exertion")
    )
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        rpe = float(raw)
    else:
        raw_text = str(raw)
        fraction_match = re.search(r"(?<!\d)(\d+(?:[\.,]\d+)?)\s*/\s*10(?:[\.,]0+)?\b", raw_text)
        if fraction_match:
            rpe = float(fraction_match.group(1).replace(",", "."))
        else:
            nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[\.,]\d+)?", raw_text)]
            if not nums:
                return None
            rpe = sum(nums) / len(nums)

    if rpe > 10.0:
        rpe = rpe / 10.0

    if rpe <= 0:
        return None
    return max(1.0, min(10.0, rpe))


def _estimate_strength_if(activity: dict) -> float | None:
    for key in ("gym_if", "strength_if", "intensityFactor", "intensity_factor", "if"):
        raw = activity.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if 0.35 <= val <= 1.10:
            return val

    txt = " ".join(
        [
            str(activity.get("name") or ""),
            str(activity.get("activityName") or ""),
            str(activity.get("description") or ""),
            str(activity.get("notes") or ""),
        ]
    ).lower()

    light_keywords = (
        "movilidad",
        "mobility",
        "tonificacion",
        "tonificación",
        "acondicionamiento",
        "activation",
        "activacion",
        "activación",
        "core suave",
        "recovery",
        "recuperacion",
        "recuperación",
    )
    maintenance_keywords = ("mantenimiento", "maintain", "maintenance", "base")
    neuromuscular_keywords = ("neuromuscular",)
    general_keywords = (
        "fuerza general",
        "hipertrofia",
        "fuerza resistencia",
        "full tren inferior",
        "full body",
        "tren inferior",
        "tren superior",
        "gym",
        "gimnasio",
    )
    heavy_keywords = (
        "fuerza maxima",
        "fuerza máxima",
        "max strength",
        "power",
        "potencia",
        "heavy",
        "1rm",
        "one rep max",
        "haltero",
        "weightlifting",
        "olimpic",
        "olympic",
    )

    if any(k in txt for k in heavy_keywords):
        return 0.80
    if any(k in txt for k in light_keywords):
        return 0.50
    if any(k in txt for k in neuromuscular_keywords):
        return 0.57
    if any(k in txt for k in maintenance_keywords):
        return 0.55
    if any(k in txt for k in general_keywords):
        return 0.56

    rpe = _extract_strength_rpe_10(activity)
    if rpe is not None:
        if rpe <= 4.0:
            return 0.50
        if rpe <= 6.0:
            return 0.56
        return 0.80

    return 0.56


def _estimate_strength_tss_from_rpe_minutes(activity: dict, hours: float) -> float | None:
    if hours <= 0:
        return None
    rpe = _extract_strength_rpe_10(activity)
    if rpe is None:
        return None

    minutes = hours * 60.0
    if rpe <= 4.0:
        tss_per_min = 0.5
    elif rpe <= 6.0:
        tss_per_min = 1.0
    elif rpe <= 7.0:
        tss_per_min = 1.2
    elif rpe <= 8.0:
        tss_per_min = 1.35
    else:
        tss_per_min = 1.5

    return max(0.0, minutes * tss_per_min)


def _estimate_walk_hike_tss(
    activity: dict,
    hours: float,
    hr_zones_raw: str | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
) -> tuple[float | None, str | None]:
    if hours <= 0:
        return None, None

    txt = " ".join(
        [
            str(activity.get("name") or ""),
            str(activity.get("activityName") or ""),
            str(activity.get("description") or ""),
            str(activity.get("notes") or ""),
        ]
    ).lower()
    act_type = str(activity.get("type") or activity.get("activityType") or "").lower()

    def _first_float(*keys: str) -> float | None:
        for key in keys:
            raw = activity.get(key)
            if raw is None:
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    distance_m = _first_float("distance", "distance_m", "distanceMeters")
    elev_gain = _first_float("elevationGain", "elevation_gain", "totalAscent", "total_ascent", "elev_gain")
    speed_mps = None
    if distance_m and hours > 0:
        speed_mps = distance_m / (hours * 3600.0)
    kmh = speed_mps * 3.6 if speed_mps is not None else None

    heavy_kw = (
        "mochila",
        "backpack",
        "cuesta",
        "cuestas",
        "desnivel",
        "palos",
        "sender",
        "trek",
        "hiking",
        "mountain",
        "monta",
        "trail walk",
    )
    brisk_kw = ("power walking", "ritmo vivo", "vivo", "marcha", "brisk", "ligero rapido", "ligero rápido")

    is_heavy = (
        any(k in txt for k in heavy_kw)
        or "hiking" in act_type
        or (elev_gain is not None and elev_gain >= 250.0)
        or (
            elev_gain is not None
            and distance_m
            and distance_m > 0
            and (elev_gain / max(1.0, distance_m / 1000.0)) >= 35.0
        )
    )
    is_brisk = any(k in txt for k in brisk_kw) or (kmh is not None and kmh >= 5.8)

    if is_heavy:
        if_model = 0.71
        min_h, max_h = 40.0, None
    elif is_brisk:
        if_model = 0.57
        min_h, max_h = 25.0, 40.0
    else:
        if_model = 0.45
        min_h, max_h = 15.0, 25.0

    tss_model = max(0.0, hours * (if_model**2) * 100.0)

    tss_zones = _estimate_hr_tss_from_zones(
        activity,
        hours=hours,
        hr_zones_raw=hr_zones_raw,
        hr_rest_bpm=hr_rest_bpm,
        hr_max_bpm=hr_max_bpm,
        apply_cap=False,
        min_coverage_ratio=0.35,
    )

    if tss_zones is not None:
        blended = (0.70 * float(tss_zones)) + (0.30 * float(tss_model))
        tss = max(0.0, blended)
        tss_h = tss / hours if hours > 0 else 0.0
        if max_h is not None:
            tss_h = min(max_h, tss_h)
        tss_h = max(min_h, tss_h)
        return max(0.0, tss_h * hours), "hrTSS"

    if_hr = _estimate_if_from_hr(activity, cycling_formula=False, hr_rest_bpm=hr_rest_bpm, hr_max_bpm=hr_max_bpm)
    if if_hr is not None:
        return max(0.0, hours * (if_hr**2) * 100.0), "hrTSS"

    return tss_model, "TSS"


def _estimate_tss_from_power_ftp(activity: dict, ftp: float | None, hours: float) -> float | None:
    if hours <= 0 or not ftp or ftp <= 0:
        return None
    power_raw = (
        activity.get("normalizedPower")
        or activity.get("normalized_power_watts")
        or activity.get("avgPower")
        or activity.get("avg_power_watts")
        or activity.get("averagePower")
        or activity.get("average_power_watts")
    )
    if power_raw is None:
        return None
    try:
        power_w = float(power_raw)
    except (ValueError, TypeError):
        return None
    if power_w <= 0:
        return None

    if_pow = power_w / ftp
    return max(0.0, hours * (if_pow**2) * 100.0)


def _has_activity_power_data(activity: dict) -> bool:
    if not isinstance(activity, dict):
        return False
    for key in (
        "normalizedPower",
        "normalizedPowerWatts",
        "normalized_power_watts",
        "avgPower",
        "avg_power_watts",
        "averagePower",
        "average_power_watts",
    ):
        raw = activity.get(key)
        if raw is None:
            continue
        try:
            if float(raw) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _estimate_tss_from_threshold_pace(
    activity: dict,
    hours: float,
    running_threshold_pace_sec_per_km: float | None = None,
    prefer_effective_running_pace: bool = False,
    if_pace_ceiling: float = 1.20,
) -> float | None:
    if hours <= 0:
        return None
    threshold_pace = _extract_threshold_pace_sec_per_km(activity, running_threshold_pace_sec_per_km)
    avg_pace = (
        _extract_running_effective_pace_sec_per_km(activity)
        if prefer_effective_running_pace
        else _extract_avg_pace_sec_per_km(activity)
    )
    if not threshold_pace or not avg_pace or threshold_pace <= 0 or avg_pace <= 0:
        return None

    if_pace = max(0.50, min(float(if_pace_ceiling), threshold_pace / avg_pace))
    return max(0.0, hours * (if_pace**2) * 100.0)


def _extract_running_if_from_threshold_pace(
    activity: dict,
    running_threshold_pace_sec_per_km: float | None = None,
    prefer_effective_running_pace: bool = False,
    if_pace_ceiling: float = 1.20,
) -> float | None:
    threshold_pace = _extract_threshold_pace_sec_per_km(activity, running_threshold_pace_sec_per_km)
    avg_pace = (
        _extract_running_effective_pace_sec_per_km(activity)
        if prefer_effective_running_pace
        else _extract_avg_pace_sec_per_km(activity)
    )
    if not threshold_pace or not avg_pace or threshold_pace <= 0 or avg_pace <= 0:
        return None
    return max(0.50, min(float(if_pace_ceiling), threshold_pace / avg_pace))


def _extract_running_session_signals(activity: dict) -> dict[str, Any]:
    def _first_float(*keys: str) -> float | None:
        for key in keys:
            raw = activity.get(key)
            if raw is None:
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    avg_speed = _first_float("avg_speed_mps", "averageSpeedMps", "average_speed_mps")
    max_speed = _first_float("max_speed_mps", "maxSpeedMps", "max_speed_mps")
    speed_ratio = (max_speed / avg_speed) if avg_speed and avg_speed > 0 and max_speed and max_speed > 0 else None

    lap_count = _first_float("lap_count", "lapCount")
    vigorous_min = _first_float("vigorous_intensity_minutes", "vigorousIntensityMinutes")
    workout_rpe = _first_float("workout_rpe", "workoutRpe")
    if workout_rpe is None:
        generic_rpe = _estimate_if_from_rpe(activity)
        if generic_rpe is not None:
            workout_rpe = max(0.0, min(100.0, ((generic_rpe - 0.40) / 0.60) * 100.0))

    te_label = str(activity.get("training_effect_label") or activity.get("trainingEffectLabel") or "").strip().lower()

    txt = " ".join([str(activity.get("name") or ""), str(activity.get("description") or ""), str(activity.get("notes") or "")]).lower()
    interval_keyword = bool(
        re.search(r"(interval|series|fartlek|cuestas|repet|z4|z5|vo2|\b\d+\s*[xX]\s*\d+|\b\d+['’]\s*[xX])", txt)
    )
    series_keyword = bool(re.search(r"(interval|series|repet|cuestas|\b\d+\s*[xX]\s*\d+|\b\d+['’]\s*[xX])", txt))
    fartlek_keyword = bool(re.search(r"\bfartlek\b", txt))
    rodaje_keyword = bool(re.search(r"\brodaje\b|\bz1\b|\bz2\b|\brecuperaci[oó]n\b|\bsuave\b", txt))

    return {
        "speed_ratio": speed_ratio,
        "lap_count": int(lap_count) if lap_count is not None else 0,
        "vigorous_min": float(vigorous_min) if vigorous_min is not None else 0.0,
        "workout_rpe": float(workout_rpe) if workout_rpe is not None else None,
        "te_label": te_label,
        "interval_keyword": interval_keyword,
        "series_keyword": series_keyword,
        "fartlek_keyword": fartlek_keyword,
        "rodaje_keyword": rodaje_keyword,
    }


def _classify_running_session_with_confidence(activity: dict) -> dict[str, Any]:
    sig = _extract_running_session_signals(activity)
    speed_ratio = float(sig.get("speed_ratio") or 0.0)
    lap_count = int(sig.get("lap_count") or 0)
    vigorous_min = float(sig.get("vigorous_min") or 0.0)
    workout_rpe = sig.get("workout_rpe")
    workout_rpe = float(workout_rpe) if workout_rpe is not None else 0.0
    te_label = str(sig.get("te_label") or "")
    interval_keyword = bool(sig.get("interval_keyword"))
    series_keyword = bool(sig.get("series_keyword"))
    fartlek_keyword = bool(sig.get("fartlek_keyword"))
    rodaje_keyword = bool(sig.get("rodaje_keyword"))

    high_te = te_label in {"lactate_threshold", "vo2max", "anaerobic_capacity"}

    rodaje_score = 0
    if rodaje_keyword:
        rodaje_score += 2
    if speed_ratio > 0 and speed_ratio < 1.14:
        rodaje_score += 2
    if workout_rpe <= 45.0:
        rodaje_score += 1
    if te_label in {"aerobic_base", "recovery", ""}:
        rodaje_score += 1
    if vigorous_min <= 25.0:
        rodaje_score += 1

    fartlek_score = 0
    if fartlek_keyword:
        fartlek_score += 3
    if interval_keyword:
        fartlek_score += 1
    if speed_ratio >= 1.14:
        fartlek_score += 1
    if workout_rpe >= 60.0:
        fartlek_score += 1
    if high_te:
        fartlek_score += 1
    if lap_count >= 14:
        fartlek_score += 1

    series_score = 0
    if series_keyword:
        series_score += 2
    if interval_keyword and not fartlek_keyword:
        series_score += 1
    if speed_ratio >= 1.15:
        series_score += 1
    if lap_count >= 16:
        series_score += 1
    if workout_rpe >= 55.0:
        series_score += 1
    if vigorous_min >= 35.0:
        series_score += 1
    if high_te:
        series_score += 1

    calidad_score = 1
    if high_te:
        calidad_score += 1
    if workout_rpe >= 50.0:
        calidad_score += 1

    scores = {
        "rodaje": rodaje_score,
        "fartlek": fartlek_score,
        "series": series_score,
        "calidad": calidad_score,
    }

    sorted_scores = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_kind, top_score = sorted_scores[0]
    second_score = sorted_scores[1][1]
    margin = top_score - second_score

    if top_kind == "rodaje" and top_score < 4:
        session_kind = "calidad"
    elif top_kind in {"fartlek", "series"} and top_score < 4:
        session_kind = "calidad"
    else:
        session_kind = top_kind

    confidence = "low"
    if top_score >= 5 and margin >= 2:
        confidence = "high"
    elif top_score >= 4 and margin >= 1:
        confidence = "medium"

    return {"session_kind": session_kind, "confidence": confidence, "scores": scores}


def _estimate_running_tss_examined(
    activity: dict,
    hours: float,
    running_threshold_pace_sec_per_km: float | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
) -> float | None:
    if hours <= 0:
        return None

    base_if = _extract_running_if_from_threshold_pace(
        activity,
        running_threshold_pace_sec_per_km=running_threshold_pace_sec_per_km,
        prefer_effective_running_pace=True,
        if_pace_ceiling=1.30,
    )
    tss_pace_base = max(0.0, hours * (base_if**2) * 100.0) if base_if is not None else None

    if_hr = _estimate_if_from_hr(activity, cycling_formula=False, hr_rest_bpm=hr_rest_bpm, hr_max_bpm=hr_max_bpm)
    tss_hr = max(0.0, hours * (if_hr**2) * 100.0) if if_hr is not None else None

    if base_if is None:
        return tss_hr

    cls = _classify_running_session_with_confidence(activity)
    session_kind = str(cls.get("session_kind") or "calidad")
    confidence = str(cls.get("confidence") or "low")

    if session_kind in {"fartlek", "series"}:
        sig = _extract_running_session_signals(activity)
        speed_ratio = float(sig.get("speed_ratio") or 1.0)
        lap_count = int(sig.get("lap_count") or 0)
        workout_rpe = sig.get("workout_rpe")
        workout_rpe = float(workout_rpe) if workout_rpe is not None else 0.0
        te_label = str(sig.get("te_label") or "")
        interval_keyword = bool(sig.get("interval_keyword"))
        fartlek_keyword = bool(sig.get("fartlek_keyword"))

        confidence_factor = {"high": 1.0, "medium": 0.85, "low": 0.70}.get(confidence, 0.70)

        uplift = 0.0
        if session_kind == "fartlek" or fartlek_keyword:
            if speed_ratio > 1.10:
                uplift += min(0.014, (speed_ratio - 1.10) * 0.12)
            if lap_count >= 24:
                uplift += 0.006
            elif lap_count >= 16:
                uplift += 0.003
            if interval_keyword:
                uplift += 0.004
            if fartlek_keyword:
                uplift += 0.003
            if workout_rpe >= 80.0:
                uplift += 0.003
            elif workout_rpe >= 65.0:
                uplift += 0.002
            if te_label in {"lactate_threshold", "vo2max", "anaerobic_capacity"}:
                uplift += 0.002
            uplift *= confidence_factor
            uplift = min(0.018, uplift)
        else:
            if speed_ratio > 1.12:
                uplift += min(0.045, (speed_ratio - 1.12) * 0.20)
            if workout_rpe >= 70.0:
                uplift += 0.01
            elif workout_rpe >= 55.0:
                uplift += 0.005
            if te_label in {"lactate_threshold", "vo2max", "anaerobic_capacity"}:
                uplift += 0.008
            if interval_keyword:
                uplift += 0.008
            uplift *= confidence_factor
            uplift = min(0.07, uplift)

        interval_if = max(0.50, min(1.30, base_if + uplift))
        tss_interval = max(0.0, hours * (interval_if**2) * 100.0)
        if tss_pace_base is not None:
            return max(tss_interval, tss_pace_base)
        return tss_interval if tss_interval is not None else tss_hr

    if tss_pace_base is not None:
        return tss_pace_base
    return tss_hr


def _estimate_if_from_training_effect(activity: dict) -> float | None:
    effect = activity.get("activityTrainingEffect") or activity.get("trainingEffect") or activity.get("aerobicTrainingEffect")
    if effect is None:
        return None
    try:
        effect_norm = max(0.0, min(float(effect) / 5.0, 1.2))
        return max(0.50, min(1.05, 0.50 + (effect_norm * 0.45)))
    except (TypeError, ValueError):
        return None


def _estimate_session_tss(
    activity: dict,
    ftp: float | None = None,
    running_threshold_pace_sec_per_km: float | None = None,
    hr_rest_bpm: float | None = None,
    hr_max_bpm: float | None = None,
    hr_zones_raw: str | None = None,
) -> tuple[float, str]:
    if not isinstance(activity, dict):
        return 0.0, "hrTSS"

    act_type = activity.get("type") or activity.get("activityType") or ""
    is_cycling = _is_cycling_activity(act_type)
    is_strength = _is_strength_activity(act_type)
    is_trail_hike_walk = _is_trail_hike_walk_activity(act_type)
    is_trail = _is_trail_activity(act_type)
    is_hike_walk = _is_hike_walk_activity(act_type)
    is_running_non_trail = _is_running_non_trail_activity(act_type)
    tss_native = _extract_training_load_tss(activity)

    hours = _extract_activity_duration_hours(activity)
    if hours <= 0:
        if tss_native is not None:
            return tss_native, "TSS"
        return 0.0, "hrTSS"

    if is_cycling:
        tss_pow = _estimate_tss_from_power_ftp(activity, ftp=ftp, hours=hours)
        if tss_pow is not None:
            return tss_pow, "TSS"

        tss_hr_zones = _estimate_hr_tss_from_zones(
            activity,
            hours=hours,
            hr_zones_raw=hr_zones_raw,
            hr_rest_bpm=hr_rest_bpm,
            hr_max_bpm=hr_max_bpm,
        )
        if tss_hr_zones is not None:
            return tss_hr_zones, "hrTSS"

        if_hr = _estimate_if_from_hr(activity, cycling_formula=True, hr_rest_bpm=hr_rest_bpm, hr_max_bpm=hr_max_bpm)
        if if_hr is not None:
            return max(0.0, hours * (if_hr**2) * 100.0), "hrTSS"

    elif is_running_non_trail:
        tss_running = _estimate_running_tss_examined(
            activity,
            hours=hours,
            running_threshold_pace_sec_per_km=running_threshold_pace_sec_per_km,
            hr_rest_bpm=hr_rest_bpm,
            hr_max_bpm=hr_max_bpm,
        )
        if tss_running is not None:
            return tss_running, "TSS"

    elif is_trail_hike_walk:
        if is_trail:
            tss_hr_zones = _estimate_hr_tss_from_zones(
                activity,
                hours=hours,
                hr_zones_raw=hr_zones_raw,
                hr_rest_bpm=hr_rest_bpm,
                hr_max_bpm=hr_max_bpm,
                apply_cap=False,
            )
            if tss_hr_zones is not None:
                if _should_use_raw_hr_tss_for_fast_trail(activity):
                    return max(0.0, float(tss_hr_zones)), "hrTSS"
                return max(0.0, float(tss_hr_zones)), "hrTSS"

        if is_hike_walk:
            tss_walk, lbl_walk = _estimate_walk_hike_tss(
                activity,
                hours=hours,
                hr_zones_raw=hr_zones_raw,
                hr_rest_bpm=hr_rest_bpm,
                hr_max_bpm=hr_max_bpm,
            )
            if tss_walk is not None:
                return max(0.0, float(tss_walk)), str(lbl_walk or "TSS")

        if_hr = _estimate_if_from_hr(activity, cycling_formula=False, hr_rest_bpm=hr_rest_bpm, hr_max_bpm=hr_max_bpm)
        if if_hr is not None:
            return max(0.0, hours * (if_hr**2) * 100.0), "hrTSS"
        tss_pace = _estimate_tss_from_threshold_pace(
            activity,
            hours=hours,
            running_threshold_pace_sec_per_km=running_threshold_pace_sec_per_km,
        )
        if tss_pace is not None:
            return tss_pace, "TSS"
        if_rpe = _estimate_if_from_rpe(activity)
        if if_rpe is not None:
            return max(0.0, hours * (if_rpe**2) * 100.0), "hrTSS"

    elif is_strength:
        tss_hr_zones = _estimate_hr_tss_from_zones(
            activity,
            hours=hours,
            hr_zones_raw=hr_zones_raw,
            hr_rest_bpm=hr_rest_bpm,
            hr_max_bpm=hr_max_bpm,
            min_coverage_ratio=0.35,
        )
        if tss_hr_zones is not None:
            return tss_hr_zones, "hrTSS"

        if_strength = _estimate_strength_if(activity)
        if if_strength is not None:
            return max(0.0, hours * (if_strength**2) * 100.0), "TSS"

        tss_rpe_minutes = _estimate_strength_tss_from_rpe_minutes(activity, hours)
        if tss_rpe_minutes is not None:
            return tss_rpe_minutes, "TSS"

        if_hr = _estimate_if_from_hr(activity, cycling_formula=False, hr_rest_bpm=hr_rest_bpm, hr_max_bpm=hr_max_bpm)
        if if_hr is not None:
            return max(0.0, hours * (if_hr**2) * 100.0), "hrTSS"
        if_rpe = _estimate_if_from_rpe(activity)
        if if_rpe is not None:
            return max(0.0, hours * (if_rpe**2) * 100.0), "hrTSS"

    if tss_native is not None:
        return tss_native, "TSS"

    tss_hr_zones_generic = _estimate_hr_tss_from_zones(
        activity,
        hours=hours,
        hr_zones_raw=hr_zones_raw,
        hr_rest_bpm=hr_rest_bpm,
        hr_max_bpm=hr_max_bpm,
    )
    if tss_hr_zones_generic is not None:
        return tss_hr_zones_generic, "hrTSS"

    if_hr_fallback = _estimate_if_from_hr(activity, cycling_formula=is_cycling, hr_rest_bpm=hr_rest_bpm, hr_max_bpm=hr_max_bpm)
    if if_hr_fallback is not None:
        return max(0.0, hours * (if_hr_fallback**2) * 100.0), "hrTSS"

    if_te = _estimate_if_from_training_effect(activity)
    if if_te is not None:
        return max(0.0, hours * (if_te**2) * 100.0), "hrTSS"

    if_default = 0.60 if is_cycling else 0.68
    return max(0.0, hours * (if_default**2) * 100.0), "hrTSS"


def _infer_tss_source_tag(activity: dict, tss_label: str, ftp: float | None, hr_zones_raw: str | None) -> str:
    if not isinstance(activity, dict):
        return "unknown"

    act_type = activity.get("type") or activity.get("activityType") or ""
    is_cycling = _is_cycling_activity(act_type)

    if is_cycling:
        if tss_label == "TSS" and ftp and ftp > 0 and _has_activity_power_data(activity):
            return "power_ftp"
        if tss_label == "hrTSS" and hr_zones_raw:
            return "hr_zones"
        if tss_label == "hrTSS":
            return "hr_avg"
        native_tss = _extract_training_load_tss(activity)
        if native_tss is not None:
            return "native_tss"
        return "cycling_fallback"

    if tss_label == "TSS":
        native_tss = _extract_training_load_tss(activity)
        if native_tss is not None:
            return "native_tss"
        return "pace_or_model"
    if tss_label == "hrTSS" and hr_zones_raw:
        return "hr_zones"
    if tss_label == "hrTSS":
        return "hr_avg_or_rpe"
    return "unknown"


def _resolve_running_threshold_pace_sec_per_km(profile: dict | None) -> float | None:
    if not isinstance(profile, dict):
        return None

    perf = profile.get("performance") if isinstance(profile.get("performance"), dict) else {}
    candidates: list[Any] = [
        perf.get("running_threshold_pace_sec_per_km"),
        perf.get("running_threshold_pace"),
        perf.get("lactate_threshold_pace_sec_per_km"),
        perf.get("lactate_threshold_pace"),
        perf.get("pace_at_lactate_threshold"),
        perf.get("threshold_pace"),
        profile.get("running_threshold_pace_sec_per_km"),
        profile.get("running_threshold_pace"),
    ]
    for raw in candidates:
        pace = _parse_pace_to_sec_per_km(raw)
        if pace and pace > 0:
            return pace

    speed_candidates = [
        perf.get("lactate_threshold_speed_mps"),
        perf.get("lactate_threshold_speed"),
        perf.get("running_threshold_speed"),
    ]
    for raw_speed in speed_candidates:
        pace = _speed_ms_to_pace_sec_per_km(raw_speed)
        if pace and pace > 0:
            return pace

    return None


def _percentile(values: list[float], pct: float, default: float = 0.0) -> float:
    cleaned = sorted(float(v) for v in values if v is not None)
    if not cleaned:
        return float(default)
    p = max(0.0, min(float(pct), 1.0))
    idx = int(round((len(cleaned) - 1) * p))
    return cleaned[idx]


_SPORT_MODEL_DEFAULTS: dict[str, dict] = {
    "trail running": {
        "atl_tau_days": 8,
        "ctl_tau_days": 42,
        "tsb_low_pct": 0.15,
        "tsb_high_pct": 0.80,
        "atl_high_pct": 0.85,
        "weekly_target_pct": 0.55,
        "weekly_high_pct": 0.90,
        "tsb_abs_floor": -35.0,
    },
    "running": {
        "atl_tau_days": 7,
        "ctl_tau_days": 42,
        "tsb_low_pct": 0.20,
        "tsb_high_pct": 0.80,
        "atl_high_pct": 0.80,
        "weekly_target_pct": 0.55,
        "weekly_high_pct": 0.85,
        "tsb_abs_floor": -30.0,
    },
    "ciclismo": {
        "atl_tau_days": 7,
        "ctl_tau_days": 45,
        "tsb_low_pct": 0.20,
        "tsb_high_pct": 0.80,
        "atl_high_pct": 0.80,
        "weekly_target_pct": 0.55,
        "weekly_high_pct": 0.85,
        "tsb_abs_floor": -32.0,
    },
    "triatlón": {
        "atl_tau_days": 7,
        "ctl_tau_days": 45,
        "tsb_low_pct": 0.15,
        "tsb_high_pct": 0.80,
        "atl_high_pct": 0.85,
        "weekly_target_pct": 0.55,
        "weekly_high_pct": 0.90,
        "tsb_abs_floor": -35.0,
    },
    "otro": {
        "atl_tau_days": 7,
        "ctl_tau_days": 42,
        "tsb_low_pct": 0.20,
        "tsb_high_pct": 0.80,
        "atl_high_pct": 0.80,
        "weekly_target_pct": 0.55,
        "weekly_high_pct": 0.85,
        "tsb_abs_floor": -30.0,
    },
}
_SPORT_MODEL_DEFAULTS["triaton"] = _SPORT_MODEL_DEFAULTS["triatlón"]
_SPORT_MODEL_DEFAULTS["triatlon"] = _SPORT_MODEL_DEFAULTS["triatlón"]


def _resolve_sport_model_cfg(profile: dict | None) -> dict:
    p = profile or {}
    sport_raw = str((p.get("goals") or {}).get("primary") or "running").strip().lower()
    base = dict(_SPORT_MODEL_DEFAULTS.get(sport_raw) or _SPORT_MODEL_DEFAULTS["running"])

    saved_model = (p.get("load_metrics") or {}).get("model") or {}
    for key in (
        "atl_tau_days",
        "ctl_tau_days",
        "tsb_low_pct",
        "tsb_high_pct",
        "atl_high_pct",
        "weekly_target_pct",
        "weekly_high_pct",
    ):
        if key in saved_model:
            try:
                base[key] = float(saved_model[key])
            except (TypeError, ValueError):
                pass

    return base


def _compute_weekly_spike_signal(
    series: list[dict],
    reference_day: date | None = None,
    threshold_ratio: float = 0.20,
) -> dict[str, Any]:
    ref = reference_day or date.today()
    week_start = ref - timedelta(days=ref.weekday())
    prev_start = week_start - timedelta(days=7)
    prev_end = week_start - timedelta(days=1)

    current_tss = 0.0
    previous_tss = 0.0

    for row in series or []:
        if not isinstance(row, dict):
            continue
        d_iso = str(row.get("date") or "")
        try:
            d_obj = date.fromisoformat(d_iso)
        except ValueError:
            continue
        tss = max(0.0, float(row.get("tss") or 0.0))
        if week_start <= d_obj <= ref:
            current_tss += tss
        elif prev_start <= d_obj <= prev_end:
            previous_tss += tss

    available = previous_tss > 0.0
    delta_pct = None
    spike_alert = False
    if available:
        delta_pct = round(((current_tss - previous_tss) / previous_tss) * 100.0, 1)
        spike_alert = current_tss > (previous_tss * (1.0 + threshold_ratio))

    return {
        "current_tss": round(current_tss, 1),
        "previous_tss": round(previous_tss, 1),
        "delta_pct": delta_pct,
        "threshold_pct": round(threshold_ratio * 100.0, 1),
        "available": available,
        "spike_alert": spike_alert,
    }


def _compute_load_fatigue_metrics(
    activities: list[dict],
    trend_payload: Any,
    profile: dict | None = None,
    days_window: int = 56,
    reference_day: date | None = None,
) -> dict | None:
    today = reference_day or date.today()
    start_day = today - timedelta(days=max(14, days_window - 1))
    running_threshold_pace = _resolve_running_threshold_pace_sec_per_km(profile)
    hr_rest_bpm, hr_max_bpm = _resolve_hr_profile_values(profile)

    tss_by_day: dict[str, float] = {}

    for item in _extract_training_load_points(trend_payload):
        d_iso = item.get("date")
        if not d_iso:
            continue
        try:
            d_obj = date.fromisoformat(d_iso)
        except ValueError:
            continue
        if d_obj < start_day or d_obj > today:
            continue
        tss_by_day[d_iso] = max(tss_by_day.get(d_iso, 0.0), float(item.get("tss") or 0.0))

    for act in list(activities or []):
        if not isinstance(act, dict):
            continue
        d_iso = _to_iso_date(act.get("startTimeLocal") or act.get("startTimeGMT") or act.get("date") or act.get("calendarDate"))
        if not d_iso:
            continue
        try:
            d_obj = date.fromisoformat(d_iso)
        except ValueError:
            continue
        if d_obj < start_day or d_obj > today:
            continue
        tss, _ = _estimate_session_tss(
            act,
            running_threshold_pace_sec_per_km=running_threshold_pace,
            hr_rest_bpm=hr_rest_bpm,
            hr_max_bpm=hr_max_bpm,
            hr_zones_raw=(act.get("_hr_zones_raw") or act.get("hr_zones_raw") or act.get("hrZonesRaw")),
        )
        if tss > 0:
            tss_by_day[d_iso] = tss_by_day.get(d_iso, 0.0) + tss

    if not tss_by_day:
        return None

    model_cfg = _resolve_sport_model_cfg(profile)
    tau_atl = int(round(float(model_cfg.get("atl_tau_days") or 7)))
    tau_ctl = int(round(float(model_cfg.get("ctl_tau_days") or 42)))
    tau_atl = max(3, min(tau_atl, 14))
    tau_ctl = max(21, min(tau_ctl, 90))

    sport_raw = str(((profile or {}).get("goals") or {}).get("primary") or "running").strip().lower()

    saved_last = ((profile or {}).get("load_metrics") or {}).get("last") or {}
    atl_prev = max(0.0, float(saved_last.get("atl") or 0.0))
    ctl_prev = max(0.0, float(saved_last.get("ctl") or 0.0))
    seed_date_iso = _to_iso_date(saved_last.get("date"))
    if seed_date_iso:
        try:
            seed_date = date.fromisoformat(seed_date_iso)
            if seed_date < start_day:
                atl_prev = 0.0
                ctl_prev = 0.0
        except ValueError:
            pass

    alpha_atl = 1.0 / float(tau_atl)
    alpha_ctl = 1.0 / float(tau_ctl)

    series: list[dict] = []
    day_cursor = start_day
    while day_cursor <= today:
        d_iso = day_cursor.isoformat()
        tss = max(0.0, float(tss_by_day.get(d_iso, 0.0)))
        atl = atl_prev + (tss - atl_prev) * alpha_atl
        ctl = ctl_prev + (tss - ctl_prev) * alpha_ctl
        tsb = ctl - atl
        row = {
            "date": d_iso,
            "tss": round(tss, 1),
            "atl": round(atl, 1),
            "ctl": round(ctl, 1),
            "tsb": round(tsb, 1),
        }
        series.append(row)
        atl_prev = atl
        ctl_prev = ctl
        day_cursor += timedelta(days=1)

    latest = series[-1]
    last_28 = series[-28:] if len(series) >= 28 else series[:]
    last_42 = series[-42:] if len(series) >= 42 else series[:]
    atl_values = [float(x["atl"]) for x in last_28]
    tsb_values = [float(x["tsb"]) for x in last_28]

    weekly_tss_values: list[float] = []
    for idx in range(0, len(last_42), 7):
        chunk = last_42[idx : idx + 7]
        if chunk:
            weekly_tss_values.append(round(sum(float(x["tss"]) for x in chunk), 1))
    weekly_spike = _compute_weekly_spike_signal(series, reference_day=today, threshold_ratio=0.20)
    current_week_tss = float(weekly_spike.get("current_tss") or 0.0)

    tsb_low = round(_percentile(tsb_values, float(model_cfg.get("tsb_low_pct") or 0.20), default=-10.0), 1)
    tsb_high = round(_percentile(tsb_values, float(model_cfg.get("tsb_high_pct") or 0.80), default=5.0), 1)
    atl_high = round(
        _percentile(atl_values, float(model_cfg.get("atl_high_pct") or 0.80), default=max(50.0, float(latest["atl"]))),
        1,
    )
    weekly_target = round(
        _percentile(weekly_tss_values, float(model_cfg.get("weekly_target_pct") or 0.55), default=current_week_tss),
        1,
    )
    weekly_high = round(
        _percentile(
            weekly_tss_values,
            float(model_cfg.get("weekly_high_pct") or 0.85),
            default=max(current_week_tss, weekly_target * 1.15),
        ),
        1,
    )

    days_with_load = sum(1 for x in series if float(x.get("tss") or 0.0) > 0)
    min_days_for_reliable_ranges = 21
    warming_up = days_with_load < min_days_for_reliable_ranges
    warming_up_days_remaining = max(0, min_days_for_reliable_ranges - days_with_load)

    tsb_now = float(latest["tsb"])
    atl_now = float(latest["atl"])
    tsb_abs_floor = float(model_cfg.get("tsb_abs_floor") or -30.0)
    abs_overload = tsb_now <= tsb_abs_floor
    sustained_overload = len(series) >= 7 and all(float(x["tsb"]) <= tsb_low for x in series[-7:])
    fatigue_high = (tsb_now < tsb_low) or (atl_now > atl_high)
    available_for_quality = (tsb_now >= tsb_low) and (tsb_now <= max(tsb_high, tsb_low + 4.0)) and not fatigue_high
    weekly_spike_alert = bool(weekly_spike.get("spike_alert"))

    if abs_overload or sustained_overload or (current_week_tss > weekly_high and tsb_now < tsb_low):
        status = "overload"
        action = "sobrecarga sostenida"
        recommendation = "Activa semana de descarga (-30% a -40% de volumen) y elimina calidad intensa 3-5 dias."
    elif fatigue_high:
        status = "fatigue_high"
        action = "fatiga alta"
        recommendation = "Reduce intensidad/volumen hoy y prioriza recuperacion activa, sueño e hidratacion."
    elif available_for_quality:
        status = "ready"
        action = "buena disponibilidad"
        recommendation = "Puedes mantener sesion de calidad o progresion controlada segun plan."
    else:
        status = "neutral"
        action = "carga estable"
        recommendation = "Manten carga aerobica controlada y reevalua mañana con HRV/sueño/estres."

    if weekly_spike_alert:
        if status in {"ready", "neutral"}:
            action = "spike semanal >20%"
            recommendation = (
                "⚠️ Spike semanal >20% vs semana previa: reduce 15-25% la carga de los proximos 2-3 dias "
                "y prioriza recuperacion para consolidar adaptacion."
            )
        elif status == "fatigue_high":
            recommendation = recommendation + " Ademas, la carga semanal ya supera en >20% a la semana previa."

    return {
        "model": {
            "name": "tp-inspired-ewma",
            "sport": sport_raw,
            "atl_tau_days": tau_atl,
            "ctl_tau_days": tau_ctl,
            "tsb_low_pct": model_cfg.get("tsb_low_pct") or 0.20,
            "tsb_high_pct": model_cfg.get("tsb_high_pct") or 0.80,
            "atl_high_pct": model_cfg.get("atl_high_pct") or 0.80,
        },
        "latest": latest,
        "series": series[-120:],
        "weekly": {
            "current_tss": current_week_tss,
            "previous_tss": float(weekly_spike.get("previous_tss") or 0.0),
            "spike_delta_pct": weekly_spike.get("delta_pct"),
            "spike_threshold_pct": float(weekly_spike.get("threshold_pct") or 20.0),
            "spike_alert": weekly_spike_alert,
            "target_tss": weekly_target,
            "high_tss": weekly_high,
        },
        "ranges": {
            "tsb_low": tsb_low,
            "tsb_high": tsb_high,
            "atl_high": atl_high,
            "tsb_abs_floor": tsb_abs_floor,
        },
        "warming_up": warming_up,
        "warming_up_days_remaining": warming_up_days_remaining,
        "days_with_load": days_with_load,
        "flags": {
            "fatigue_high": fatigue_high,
            "sustained_overload": sustained_overload,
            "abs_overload": abs_overload,
            "available_for_quality": available_for_quality,
            "warming_up": warming_up,
            "weekly_spike_alert": weekly_spike_alert,
        },
        "status": status,
        "action": action,
        "recommendation": recommendation,
    }
