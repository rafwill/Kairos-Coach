"""
Reusable load metrics calculations.

This module centralizes TSS estimation and ATL/CTL/TSB computations so it can
be imported by other projects without depending on chat or storage layers.
"""

from __future__ import annotations

import json
import logging
import os
import re
from statistics import median
from datetime import date, datetime, timedelta
from typing import Any

try:
    from agent.running_tss import procesar_actividad as _procesar_running_tss
except (ImportError, RuntimeError):
    _procesar_running_tss = None

log = logging.getLogger(__name__)


# Increase when TSS formula behavior changes.
TSS_FORMULA_VERSION = 22

# Running fallback v2: fixed HR guardrail ratio to avoid per-dataset re-tuning.
RUNNING_TSS_FALLBACK_HR_GUARDRAIL_RATIO = 0.88

# Fast trail threshold (< 6:00/km) where raw zone hrTSS is explicitly preferred.
TRAIL_FAST_PACE_RAW_ZONES_SEC_PER_KM = 6 * 60

# Non-fast trail smoothing for sustained very-low-intensity blocks.
TRAIL_NON_FAST_LOW_IF_THRESHOLD = 0.58
TRAIL_NON_FAST_LOW_IF_FLOOR = 0.58
TRAIL_NON_FAST_LOW_IF_MIN_BLOCK_SECONDS = 5 * 60
TRAIL_NON_FAST_LOW_IF_APPLY_RATIO = 0.18


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


def _resolve_activity_type_for_routing(activity: dict) -> Any:
    if not isinstance(activity, dict):
        return ""
    return (
        activity.get("type")
        or activity.get("activityType")
        or activity.get("activityTypeDTO")
        or activity.get("activityTypeDto")
        or activity.get("typeDTO")
        or ""
    )


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


def _extract_running_native_effective_pace_sec_per_km(activity: dict) -> float | None:
    """Return only native effective pace fields (no average-pace fallback)."""
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
    return None


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


def _decode_json_like(payload: Any) -> Any:
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return payload
    return payload


def _extract_hr_samples_from_activity_details(
    activity_details_raw: Any,
    duration_seconds: float,
) -> list[tuple[float, float]]:
    data = _decode_json_like(activity_details_raw)
    if not isinstance(data, (dict, list)):
        return []

    samples: list[tuple[float, float]] = []

    def _to_float(raw: Any) -> float | None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _looks_like_hr(key: str) -> bool:
        key_norm = str(key or "").strip().lower()
        return key_norm in {
            "heartrate",
            "heart_rate",
            "heartRate",
            "hr",
            "hrbpm",
            "bpm",
            "directheartrate",
        } or ("heart" in key_norm and "rate" in key_norm)

    def _extract_time_seconds(sample: dict[str, Any]) -> float | None:
        for key in (
            "startTimeInSeconds",
            "timeInSeconds",
            "elapsedDuration",
            "elapsedDurationInSeconds",
            "timerDurationInSeconds",
            "offsetInSeconds",
            "seconds",
            "timeOffset",
            "sumDuration",
            "duration",
            "timestamp",
            "epochMs",
            "millis",
        ):
            if key not in sample:
                continue
            val = _to_float(sample.get(key))
            if val is None:
                continue
            # Epoch milliseconds.
            if val > 10_000_000_000:
                return val / 1000.0
            return val
        return None

    def _extract_hr_value(sample: dict[str, Any]) -> float | None:
        for key, raw in sample.items():
            if not _looks_like_hr(str(key)):
                continue
            val = _to_float(raw)
            if val is None:
                continue
            if 30.0 <= val <= 235.0:
                return val
        return None

    def _walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                _walk(item)
            return

        if not isinstance(node, dict):
            return

        hr_value = _extract_hr_value(node)
        t_seconds = _extract_time_seconds(node)
        if hr_value is not None and t_seconds is not None:
            samples.append((t_seconds, hr_value))

        # Some payloads expose only arrays of heart-rate values without explicit timestamps.
        for key, value in node.items():
            key_norm = str(key or "").strip().lower()
            if not isinstance(value, list):
                continue
            if not (_looks_like_hr(key_norm) or "heartrate" in key_norm):
                continue

            hr_values: list[float] = []
            for raw in value:
                val = _to_float(raw)
                if val is None:
                    continue
                if 30.0 <= val <= 235.0:
                    hr_values.append(val)

            if not hr_values:
                continue

            step = (duration_seconds / float(len(hr_values))) if duration_seconds > 0 else 1.0
            step = max(1.0, step)
            for idx, hr_val in enumerate(hr_values):
                samples.append((idx * step, hr_val))

        for nested in node.values():
            if isinstance(nested, (dict, list)):
                _walk(nested)

    def _extract_indexed_garmin_samples(root: dict[str, Any]) -> list[tuple[float, float]]:
        descriptors = root.get("metricDescriptors")
        rows = root.get("activityDetailMetrics")
        if not isinstance(descriptors, list) or not isinstance(rows, list):
            return []

        desc_by_key: dict[str, dict[str, Any]] = {}
        for d in descriptors:
            if not isinstance(d, dict):
                continue
            key = str(d.get("key") or "").strip()
            if not key:
                continue
            desc_by_key[key] = d

        hr_keys = ("directHeartRate", "heartRate", "hr", "bpm")
        time_keys = (
            "directTimestamp",
            "sumDuration",
            "sumElapsedDuration",
            "sumMovingDuration",
            "elapsedDuration",
            "timerDurationInSeconds",
        )

        hr_desc = next((desc_by_key.get(k) for k in hr_keys if desc_by_key.get(k) is not None), None)
        if not isinstance(hr_desc, dict):
            return []

        try:
            hr_idx = int(hr_desc.get("metricsIndex"))
        except (TypeError, ValueError):
            return []

        time_desc_pairs: list[tuple[str, dict[str, Any]]] = []
        for key in time_keys:
            td = desc_by_key.get(key)
            if isinstance(td, dict):
                time_desc_pairs.append((key, td))

        if not time_desc_pairs:
            return []

        extracted: list[tuple[float, float]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            metrics = row.get("metrics")
            if not isinstance(metrics, list):
                continue
            if hr_idx < 0 or hr_idx >= len(metrics):
                continue

            hr_val = _to_float(metrics[hr_idx])
            if hr_val is None or not (30.0 <= hr_val <= 235.0):
                continue

            time_seconds = None
            for key, td in time_desc_pairs:
                try:
                    t_idx = int(td.get("metricsIndex"))
                except (TypeError, ValueError):
                    continue
                if t_idx < 0 or t_idx >= len(metrics):
                    continue

                t_raw = _to_float(metrics[t_idx])
                if t_raw is None:
                    continue

                if key == "directTimestamp":
                    time_seconds = (t_raw / 1000.0) if t_raw > 10_000_000_000 else t_raw
                else:
                    unit = td.get("unit") if isinstance(td.get("unit"), dict) else {}
                    factor = _to_float(unit.get("factor")) if isinstance(unit, dict) else None
                    if factor and factor > 0:
                        time_seconds = t_raw / factor
                    else:
                        time_seconds = t_raw
                break

            if time_seconds is None:
                continue
            extracted.append((time_seconds, hr_val))

        return extracted

    if isinstance(data, dict):
        samples.extend(_extract_indexed_garmin_samples(data))

    _walk(data)

    if len(samples) < 5:
        return []

    samples.sort(key=lambda x: x[0])
    # Normalize absolute timestamps to relative seconds.
    if samples and samples[0][0] > 86_400 and (samples[-1][0] - samples[0][0]) > 0:
        base_t = samples[0][0]
        samples = [(t - base_t, hr) for t, hr in samples]

    dedup: dict[int, float] = {}
    for t, hr in samples:
        sec = int(round(max(0.0, t)))
        dedup[sec] = hr

    out = sorted((float(sec), hr) for sec, hr in dedup.items())
    return out


def _estimate_hr_tss_from_activity_details(
    activity: dict,
    hours: float,
    activity_details_raw: str | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
    min_coverage_ratio: float = 0.40,
) -> float | None:
    if hours <= 0:
        return None
    if not activity_details_raw:
        return None

    duration_seconds = hours * 3600.0
    samples = _extract_hr_samples_from_activity_details(activity_details_raw, duration_seconds)
    if len(samples) < 5:
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
    except (TypeError, ValueError):
        return None

    if hr_rest <= 0:
        hr_rest = 50.0
    if hr_max <= 0:
        hr_max = 185.0
    if avg_hr is not None:
        hr_max = max(hr_max, avg_hr + 5.0)
        hr_rest = min(hr_rest, avg_hr - 5.0)

    denom = max(1.0, hr_max - hr_rest)

    deltas = [
        samples[idx + 1][0] - samples[idx][0]
        for idx in range(len(samples) - 1)
        if (samples[idx + 1][0] - samples[idx][0]) > 0
    ]
    step_guess = float(median(deltas)) if deltas else 1.0
    step_guess = max(1.0, min(30.0, step_guess))

    tss_total = 0.0
    covered_seconds = 0.0
    for idx, (t_sec, hr) in enumerate(samples):
        if idx + 1 < len(samples):
            dt = samples[idx + 1][0] - t_sec
            if dt <= 0:
                dt = step_guess
        else:
            dt = step_guess

        dt = max(1.0, min(30.0, float(dt)))
        hrr = (float(hr) - hr_rest) / denom
        if_sec = max(0.0, min(1.10, hrr))
        tss_total += (dt / 3600.0) * (if_sec**2) * 100.0
        covered_seconds += dt

    coverage_ratio = (covered_seconds / duration_seconds) if duration_seconds > 0 else 0.0
    if coverage_ratio < max(0.0, float(min_coverage_ratio or 0.0)):
        return None

    return max(0.0, tss_total)


def _resolve_hr_threshold_bpm_for_activity(activity: dict, hr_threshold_bpm: float | None = None) -> float | None:
    def _coerce(raw: Any) -> float | None:
        if raw is None:
            return None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        if 120.0 <= v <= 230.0:
            return v
        return None

    resolved = _coerce(hr_threshold_bpm)
    if resolved is not None:
        return resolved

    if not isinstance(activity, dict):
        return None

    for key in (
        "hr_threshold_bpm",
        "lthr_bpm",
        "lthr",
        "lactate_threshold_hr_bpm",
        "lactate_threshold_heart_rate",
        "threshold_heart_rate",
        "hrAtLactateThreshold",
        "heart_rate_threshold",
    ):
        resolved = _coerce(activity.get(key))
        if resolved is not None:
            return resolved

    summary = activity.get("summaryDTO") if isinstance(activity.get("summaryDTO"), dict) else {}
    for key in (
        "hr_threshold_bpm",
        "lthr_bpm",
        "lthr",
        "lactate_threshold_hr_bpm",
        "lactate_threshold_heart_rate",
        "threshold_heart_rate",
        "hrAtLactateThreshold",
        "heart_rate_threshold",
    ):
        resolved = _coerce(summary.get(key))
        if resolved is not None:
            return resolved

    return None


def _resolve_trail_rest_hr_bpm(activity: dict, hr_rest_bpm: float | None = None) -> float:
    def _coerce(raw: Any) -> float | None:
        if raw is None:
            return None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        if 30.0 <= v <= 100.0:
            return v
        return None

    resolved = _coerce(hr_rest_bpm)
    if resolved is not None:
        return resolved

    if not isinstance(activity, dict):
        return 55.0

    for key in (
        "restingHeartRate",
        "resting_heart_rate",
        "restingHR",
        "rhr",
    ):
        resolved = _coerce(activity.get(key))
        if resolved is not None:
            return resolved

    summary = activity.get("summaryDTO") if isinstance(activity.get("summaryDTO"), dict) else {}
    for key in (
        "restingHeartRate",
        "resting_heart_rate",
        "restingHR",
        "rhr",
    ):
        resolved = _coerce(summary.get(key))
        if resolved is not None:
            return resolved

    return 55.0


def _estimate_hr_tss_from_activity_details_lthr(
    activity: dict,
    hours: float,
    activity_details_raw: str | None,
    hr_threshold_bpm: float | None,
    hr_rest_bpm: float | None = None,
    attenuate_sustained_low_if: bool = False,
    min_coverage_ratio: float = 0.40,
) -> float | None:
    if hours <= 0:
        return None
    if not activity_details_raw:
        return None

    lthr = _resolve_hr_threshold_bpm_for_activity(activity, hr_threshold_bpm)
    if lthr is None or lthr <= 0:
        return None
    hr_rest = _resolve_trail_rest_hr_bpm(activity, hr_rest_bpm)
    if hr_rest >= lthr - 10.0:
        hr_rest = max(30.0, lthr - 55.0)

    duration_seconds = hours * 3600.0
    samples = _extract_hr_samples_from_activity_details(activity_details_raw, duration_seconds)
    if len(samples) < 5:
        return None

    deltas = [
        samples[idx + 1][0] - samples[idx][0]
        for idx in range(len(samples) - 1)
        if (samples[idx + 1][0] - samples[idx][0]) > 0
    ]
    step_guess = float(median(deltas)) if deltas else 1.0
    step_guess = max(1.0, min(30.0, step_guess))

    tss_total = 0.0
    covered_seconds = 0.0
    segments: list[tuple[float, float]] = []
    for idx, (t_sec, hr) in enumerate(samples):
        if idx + 1 < len(samples):
            dt = samples[idx + 1][0] - t_sec
            if dt <= 0:
                dt = step_guess
        else:
            dt = step_guess

        dt = max(1.0, min(30.0, float(dt)))
        denom = max(1.0, float(lthr) - float(hr_rest))
        if_sec = max(0.40, min(1.15, (float(hr) - float(hr_rest)) / denom))
        segments.append((dt, if_sec))
        tss_total += (dt / 3600.0) * (if_sec**2) * 100.0
        covered_seconds += dt

    if attenuate_sustained_low_if and segments and covered_seconds > 0:
        low_threshold = float(TRAIL_NON_FAST_LOW_IF_THRESHOLD)
        low_floor = float(TRAIL_NON_FAST_LOW_IF_FLOOR)
        min_block = float(TRAIL_NON_FAST_LOW_IF_MIN_BLOCK_SECONDS)

        adjusted_segments: list[tuple[float, float]] = []
        low_total_seconds = 0.0
        i = 0
        while i < len(segments):
            dt_i, if_i = segments[i]
            if if_i < low_threshold:
                j = i
                block_seconds = 0.0
                while j < len(segments) and segments[j][1] < low_threshold:
                    block_seconds += segments[j][0]
                    j += 1

                if block_seconds >= min_block:
                    low_total_seconds += block_seconds
                    for k in range(i, j):
                        dt_k, if_k = segments[k]
                        adjusted_segments.append((dt_k, max(low_floor, if_k)))
                else:
                    adjusted_segments.extend(segments[i:j])
                i = j
                continue

            adjusted_segments.append((dt_i, if_i))
            i += 1

        low_ratio = low_total_seconds / covered_seconds
        if low_ratio >= float(TRAIL_NON_FAST_LOW_IF_APPLY_RATIO):
            tss_total = sum((dt / 3600.0) * (if_sec**2) * 100.0 for dt, if_sec in adjusted_segments)

    coverage_ratio = (covered_seconds / duration_seconds) if duration_seconds > 0 else 0.0
    if coverage_ratio < max(0.0, float(min_coverage_ratio or 0.0)):
        return None

    return max(0.0, tss_total)


def _extract_splits_list(payload: Any) -> list[dict]:
    data = _decode_json_like(payload)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]

    if isinstance(data, dict):
        for key in (
            "splits",
            "splitSummaries",
            "split_summary",
            "laps",
            "data",
            "items",
            "result",
            "lapDTOs",
            "lapSummaries",
        ):
            nested = data.get(key)
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, dict)]

        for value in data.values():
            if isinstance(value, (list, dict)):
                found = _extract_splits_list(value)
                if found:
                    return found

    return []


def _split_first_float(split: dict, *keys: str) -> float | None:
    for key in keys:
        raw = split.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _extract_split_duration_seconds(split: dict) -> float | None:
    return _split_first_float(
        split,
        "duration",
        "durationInSeconds",
        "elapsedDuration",
        "movingDuration",
        "seconds",
        "splitDuration",
        "lapDuration",
        "total_time_seconds",
    )


def _extract_split_distance_meters(split: dict) -> float | None:
    # Explicit meter keys in Garmin payloads.
    for key in ("distanceInMeters", "distance_m"):
        if key in split:
            value = _split_first_float(split, key)
            if value is not None and value > 0:
                return value

    # Garmin lapDTOs commonly expose distance in meters under "distance".
    if "distance" in split:
        value = _split_first_float(split, "distance")
        if value is not None and value > 0:
            # Heuristic: large values are meters; tiny values are likely kilometers.
            if value >= 100.0:
                return value
            return value * 1000.0

    # Alternative keys that may be in kilometers.
    for key in ("splitDistance", "lapDistance"):
        if key in split:
            value = _split_first_float(split, key)
            if value is not None and value > 0:
                return value * 1000.0

    return None


def _extract_split_net_grade(split: dict, distance_m: float) -> float | None:
    if distance_m <= 0:
        return None

    grade_pct = _split_first_float(split, "avgGrade", "averageGrade", "grade", "gradePercent")
    if grade_pct is not None:
        return grade_pct / 100.0

    elev_gain = _split_first_float(split, "elevationGain", "elevation_gain", "totalAscent", "ascent", "gain")
    elev_loss = _split_first_float(split, "elevationLoss", "elevation_loss", "totalDescent", "descent", "loss")
    if elev_gain is None and elev_loss is None:
        start_ele = _split_first_float(split, "startElevation", "startElevationInMeters", "start_elevation")
        end_ele = _split_first_float(split, "endElevation", "endElevationInMeters", "end_elevation")
        if start_ele is not None and end_ele is not None:
            return (end_ele - start_ele) / distance_m
        return None

    gain = max(0.0, elev_gain or 0.0)
    loss = max(0.0, elev_loss or 0.0)
    return (gain - loss) / distance_m


def _running_energy_cost_ratio_for_grade(grade_decimal: float) -> float:
    s = max(-0.30, min(0.30, float(grade_decimal)))
    cost = (
        155.4 * (s**5)
        - 30.4 * (s**4)
        - 43.3 * (s**3)
        + 46.3 * (s**2)
        + 19.5 * s
        + 3.6
    )
    return max(0.70, min(1.60, cost / 3.6))


def _extract_trail_ngp_like_pace_sec_per_km(
    activity: dict,
    splits_raw: str | None = None,
) -> tuple[float | None, float]:
    splits_payload: Any = splits_raw
    if splits_payload is None and isinstance(activity, dict):
        splits_payload = (
            activity.get("_splits_raw")
            or activity.get("splits_raw")
            or activity.get("splitsRaw")
            or activity.get("splits")
        )

    splits = _extract_splits_list(splits_payload)
    if not splits:
        return None, 0.0

    total_weight = 0.0
    weighted_speed4 = 0.0
    covered_seconds = 0.0

    for split in splits:
        if not isinstance(split, dict):
            continue

        duration_s = _extract_split_duration_seconds(split)
        distance_m = _extract_split_distance_meters(split)
        if duration_s is None or distance_m is None:
            continue
        if duration_s <= 0 or distance_m <= 0:
            continue

        grade = _extract_split_net_grade(split, distance_m)
        if grade is None:
            continue

        speed_ms = distance_m / duration_s
        if speed_ms <= 0:
            continue

        cost_ratio = _running_energy_cost_ratio_for_grade(grade)
        eq_flat_speed = speed_ms * cost_ratio
        if eq_flat_speed <= 0:
            continue

        weight = max(1.0, duration_s)
        weighted_speed4 += weight * (eq_flat_speed**4)
        total_weight += weight
        covered_seconds += duration_s

    if total_weight <= 0:
        return None, 0.0

    ngp_like_speed = (weighted_speed4 / total_weight) ** 0.25
    if ngp_like_speed <= 0:
        return None, 0.0

    pace_sec_per_km = 1000.0 / ngp_like_speed
    activity_seconds = _extract_activity_duration_hours(activity) * 3600.0
    coverage = (covered_seconds / activity_seconds) if activity_seconds > 0 else 0.0
    return pace_sec_per_km, max(0.0, min(1.0, coverage))


def _estimate_trail_tss_high_precision(
    activity: dict,
    hours: float,
    running_threshold_pace_sec_per_km: float | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
    splits_raw: str | None = None,
) -> float | None:
    if hours <= 0:
        return None

    threshold_pace = _extract_threshold_pace_sec_per_km(activity, running_threshold_pace_sec_per_km)
    if not threshold_pace or threshold_pace <= 0:
        return None

    # TP-like requirement: use native effective pace signal (NGP/GAP/normalized pace).
    # Reconstructed NGP from coarse laps is too unstable and can overestimate heavily.
    pace_effective = None
    for key in (
        "normalizedPaceSecPerKm",
        "normalized_pace_sec_per_km",
        "normalizedPace",
        "normalized_pace",
        "gradeAdjustedPaceSecPerKm",
        "grade_adjusted_pace_sec_per_km",
        "gradeAdjustedPace",
        "grade_adjusted_pace",
    ):
        pace = _parse_pace_to_sec_per_km(activity.get(key))
        if pace and pace > 0:
            pace_effective = pace
            break

    if not pace_effective and isinstance(activity.get("summaryDTO"), dict):
        summary = activity.get("summaryDTO") or {}
        for key in (
            "normalizedPaceSecPerKm",
            "normalizedPace",
            "gradeAdjustedPaceSecPerKm",
            "gradeAdjustedPace",
        ):
            pace = _parse_pace_to_sec_per_km(summary.get(key))
            if pace and pace > 0:
                pace_effective = pace
                break

    if not pace_effective or pace_effective <= 0:
        return None

    # TP-like running stress core: IF from threshold pace vs effective (NGP-like) pace.
    if_pace = max(0.40, min(1.50, threshold_pace / pace_effective))
    return max(0.0, hours * (if_pace**2) * 100.0)


def _estimate_trail_hr_tss_tp_like(
    activity: dict,
    hours: float,
    hr_zones_raw: str | None = None,
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

    # Fixed zone-based IF profile for trail hrTSS fallback (TP-like, no split heuristics).
    zone_if_map = {
        1: 0.52,
        2: 0.60,
        3: 0.68,
        4: 0.78,
        5: 0.88,
        6: 0.96,
        7: 1.02,
    }

    tss_total = 0.0
    total_secs = 0.0
    for z in zones:
        if not isinstance(z, dict):
            continue

        try:
            secs = float(z.get("secsInZone") or 0.0)
        except (TypeError, ValueError):
            secs = 0.0
        if secs <= 0:
            continue

        try:
            zone_num = int(z.get("zoneNumber") or 0)
        except (TypeError, ValueError):
            zone_num = 0

        if_zone = zone_if_map.get(zone_num)
        if if_zone is None:
            if zone_num > 0:
                if_zone = min(1.05, 0.52 + (zone_num - 1) * 0.10)
            else:
                if_zone = 0.60

        tss_total += (secs / 3600.0) * (if_zone**2) * 100.0
        total_secs += secs

    if total_secs <= 0:
        return None

    return max(0.0, tss_total)


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


def _estimate_hr_tss_from_zones_lthr(
    activity: dict,
    hours: float,
    hr_threshold_bpm: float | None,
    hr_rest_bpm: float | None = None,
    hr_zones_raw: str | None = None,
    min_coverage_ratio: float = 0.0,
) -> float | None:
    if hours <= 0:
        return None

    lthr = _resolve_hr_threshold_bpm_for_activity(activity, hr_threshold_bpm)
    if lthr is None or lthr <= 0:
        return None
    hr_rest = _resolve_trail_rest_hr_bpm(activity, hr_rest_bpm)
    if hr_rest >= lthr - 10.0:
        hr_rest = max(30.0, lthr - 55.0)

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

    dur_s = hours * 3600.0
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

        denom = max(1.0, float(lthr) - float(hr_rest))
        if_zone = max(0.40, min(1.15, (float(hr_mid) - float(hr_rest)) / denom))
        tss_total += (secs / 3600.0) * (if_zone**2) * 100.0
        total_secs += secs

    if total_secs <= 0:
        return None

    coverage_ratio = total_secs / dur_s if dur_s > 0 else 0.0
    if coverage_ratio < max(0.0, float(min_coverage_ratio or 0.0)):
        return None

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


def _resolve_running_tss_model(activity: dict | None = None) -> str:
    """Resolve running TSS model with activity override and env fallback."""
    model_raw = None
    if isinstance(activity, dict):
        model_raw = activity.get("running_tss_model") or activity.get("_running_tss_model")
    if model_raw is None:
        model_raw = os.environ.get("KAIROS_RUNNING_TSS_MODEL")

    model = str(model_raw or "tp_like").strip().lower()
    if model in {"legacy", "examined", "baseline"}:
        return "legacy"
    return "tp_like"


def _resolve_running_fallback_model(activity: dict | None = None) -> str:
    """Resolve running fallback model used when activity_details pipeline is unavailable.

    Defaults to legacy for backward compatibility. Enable simplified fallback with:
      - env: KAIROS_RUNNING_TSS_FALLBACK_MODEL=v2
      - activity override: running_tss_fallback_model='v2'
    """
    model_raw = None
    if isinstance(activity, dict):
        model_raw = activity.get("running_tss_fallback_model") or activity.get("_running_tss_fallback_model")
    if model_raw is None:
        model_raw = os.environ.get("KAIROS_RUNNING_TSS_FALLBACK_MODEL")

    model = str(model_raw or "legacy").strip().lower()
    if model in {"v2", "simple", "minimal"}:
        return "v2"
    return "legacy"


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


def _estimate_running_tss_tp_like_adjusted(
    activity: dict,
    hours: float,
    running_threshold_pace_sec_per_km: float | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
) -> float | None:
    """TP-like running model used as canonical default in running activities."""
    if hours <= 0:
        return None

    threshold_pace = _extract_threshold_pace_sec_per_km(activity, running_threshold_pace_sec_per_km)
    native_effective_pace = _extract_running_native_effective_pace_sec_per_km(activity)
    avg_pace = _extract_avg_pace_sec_per_km(activity)

    base_if: float | None = None
    if threshold_pace and threshold_pace > 0:
        if native_effective_pace and native_effective_pace > 0:
            base_if = max(0.50, min(1.35, threshold_pace / native_effective_pace))
        elif avg_pace and avg_pace > 0:
            if_avg = max(0.50, min(1.30, threshold_pace / avg_pace))
            if_hr = _estimate_if_from_hr(activity, cycling_formula=False, hr_rest_bpm=hr_rest_bpm, hr_max_bpm=hr_max_bpm)
            if_te = _estimate_if_from_training_effect(activity)
            fallback_if = None
            if if_hr is not None and if_te is not None:
                fallback_if = max(float(if_hr), float(if_te))
            elif if_hr is not None:
                fallback_if = float(if_hr)
            elif if_te is not None:
                fallback_if = float(if_te)

            if fallback_if is not None:
                # Blend pace with internal load proxy when NGP/GAP is missing.
                # Keep fallback influence moderate to avoid overestimating steady rodajes.
                base_if = max(0.50, min(1.30, (0.84 * if_avg) + (0.16 * fallback_if)))
            else:
                base_if = if_avg

    if_hr = _estimate_if_from_hr(activity, cycling_formula=False, hr_rest_bpm=hr_rest_bpm, hr_max_bpm=hr_max_bpm)
    tss_hr = max(0.0, hours * (if_hr**2) * 100.0) if if_hr is not None else None

    if base_if is None:
        return tss_hr

    cls = _classify_running_session_with_confidence(activity)
    session_kind = str(cls.get("session_kind") or "calidad")
    confidence = str(cls.get("confidence") or "low")
    sig = _extract_running_session_signals(activity)

    speed_ratio = float(sig.get("speed_ratio") or 1.0)
    lap_count = int(sig.get("lap_count") or 0)
    vigorous_min = float(sig.get("vigorous_min") or 0.0)
    workout_rpe = sig.get("workout_rpe")
    workout_rpe = float(workout_rpe) if workout_rpe is not None else 0.0
    te_label = str(sig.get("te_label") or "")
    interval_keyword = bool(sig.get("interval_keyword"))
    fartlek_keyword = bool(sig.get("fartlek_keyword"))

    confidence_factor = {"high": 1.0, "medium": 0.90, "low": 0.80}.get(confidence, 0.80)

    uplift = 0.0
    if session_kind == "series":
        if speed_ratio > 1.10:
            uplift += min(0.060, (speed_ratio - 1.10) * 0.24)
        if lap_count >= 16:
            uplift += 0.020
        elif lap_count >= 10:
            uplift += 0.010
        if workout_rpe >= 75.0:
            uplift += 0.014
        elif workout_rpe >= 60.0:
            uplift += 0.008
        if interval_keyword:
            uplift += 0.010
        if te_label in {"lactate_threshold", "vo2max", "anaerobic_capacity"}:
            uplift += 0.010
        if vigorous_min >= 30.0:
            uplift += 0.008
        uplift = min(0.110, uplift * confidence_factor)
    elif session_kind == "fartlek" or fartlek_keyword:
        if speed_ratio > 1.08:
            uplift += min(0.030, (speed_ratio - 1.08) * 0.16)
        if lap_count >= 14:
            uplift += 0.010
        if workout_rpe >= 70.0:
            uplift += 0.010
        elif workout_rpe >= 55.0:
            uplift += 0.006
        if interval_keyword:
            uplift += 0.008
        if te_label in {"lactate_threshold", "vo2max", "anaerobic_capacity"}:
            uplift += 0.006
        uplift = min(0.055, uplift * confidence_factor)
    elif session_kind == "rodaje":
        if hours >= 1.50:
            uplift += 0.010
        elif hours >= 1.00:
            uplift += 0.006
        if workout_rpe >= 50.0:
            uplift += 0.002
        if speed_ratio > 0 and speed_ratio < 1.14:
            uplift += 0.002
        uplift = min(0.014, uplift)
    else:
        if workout_rpe >= 60.0:
            uplift += 0.010
        if te_label in {"lactate_threshold", "vo2max", "anaerobic_capacity"}:
            uplift += 0.008
        uplift = min(0.025, uplift * confidence_factor)

    if_adjusted = max(0.50, min(1.38, base_if + uplift))
    tss = max(0.0, hours * (if_adjusted**2) * 100.0)

    # Session calibration to better track TP bias by workout type.
    session_gain = {
        "series": 1.10,
        "fartlek": 1.03,
        "rodaje": 0.99,
        "calidad": 1.01,
    }.get(session_kind, 1.01)
    tss *= session_gain

    if tss_hr is not None and session_kind in {"series", "fartlek", "calidad"}:
        tss = max(tss, 0.88 * tss_hr)

    return max(0.0, tss)


def _estimate_running_tss_tp_like_v2(
    activity: dict,
    hours: float,
    running_threshold_pace_sec_per_km: float | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
) -> float | None:
    """Simplified TP-like fallback for running when activity_details is unavailable.

    Design goals:
    - Keep this path easy to reason about (base pace IF + single explicit HR guardrail).
    - Avoid stacked heuristic layers from legacy fallback.
    - Keep `k` fixed by design (no dataset tuning loops).
    """
    if hours <= 0:
        return None

    threshold_pace = _extract_threshold_pace_sec_per_km(activity, running_threshold_pace_sec_per_km)
    native_effective_pace = _extract_running_native_effective_pace_sec_per_km(activity)
    avg_pace = _extract_avg_pace_sec_per_km(activity)

    base_if: float | None = None
    if threshold_pace and threshold_pace > 0:
        if native_effective_pace and native_effective_pace > 0:
            base_if = max(0.50, min(1.35, threshold_pace / native_effective_pace))
        elif avg_pace and avg_pace > 0:
            base_if = max(0.50, min(1.30, threshold_pace / avg_pace))

    if_hr = _estimate_if_from_hr(activity, cycling_formula=False, hr_rest_bpm=hr_rest_bpm, hr_max_bpm=hr_max_bpm)
    tss_hr = max(0.0, hours * (if_hr**2) * 100.0) if if_hr is not None else None

    if base_if is None:
        return tss_hr

    tss_pace = max(0.0, hours * (base_if**2) * 100.0)
    if tss_hr is None:
        return tss_pace

    # Single explicit guardrail against implausibly low pace-based fallback output.
    return max(tss_pace, RUNNING_TSS_FALLBACK_HR_GUARDRAIL_RATIO * tss_hr)


def _estimate_running_tss_from_activity_details_pipeline(
    activity: dict,
    activity_details_raw: Any,
    running_threshold_pace_sec_per_km: float | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
    hr_threshold_bpm: float | None,
) -> float | None:
    """Intenta calcular running TSS desde series de activity_details (NGP + TRIMP calibrado)."""
    if _procesar_running_tss is None:
        return None
    if not activity_details_raw:
        return None

    threshold_pace = _extract_threshold_pace_sec_per_km(activity, running_threshold_pace_sec_per_km)
    if threshold_pace is None or threshold_pace <= 0:
        return None
    ftpace_ms = 1000.0 / float(threshold_pace)
    if ftpace_ms <= 0:
        return None

    lthr = _resolve_hr_threshold_bpm_for_activity(activity, hr_threshold_bpm)
    sexo = (
        activity.get("sex")
        or activity.get("gender")
        or activity.get("sexo")
        or "male"
    )

    atleta = {
        "ftpace_ms": ftpace_ms,
        "hr_reposo": hr_rest_bpm,
        "hr_max": hr_max_bpm,
        "lthr": lthr,
        "sexo": sexo,
    }

    try:
        out = _procesar_running_tss(activity_details_raw, atleta)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None

    if not isinstance(out, dict):
        return None

    rtss = out.get("rTSS")
    hrtss = out.get("hrTSS")
    try:
        if rtss is not None and float(rtss) > 0:
            return float(rtss)
    except (TypeError, ValueError):
        pass
    try:
        if hrtss is not None and float(hrtss) > 0:
            return float(hrtss)
    except (TypeError, ValueError):
        pass

    return None


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
    splits_raw: str | None = None,
    activity_details_raw: str | None = None,
    use_trail_splits: bool = True,
    hr_threshold_bpm: float | None = None,
) -> tuple[float, str]:
    if not isinstance(activity, dict):
        return 0.0, "hrTSS"

    act_type = _resolve_activity_type_for_routing(activity)
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
        details_payload = (
            activity_details_raw
            or activity.get("_activity_details_raw")
            or activity.get("activity_details_raw")
            or activity.get("activityDetailsRaw")
        )
        running_model = _resolve_running_tss_model(activity)
        if running_model == "legacy":
            tss_running = _estimate_running_tss_examined(
                activity,
                hours=hours,
                running_threshold_pace_sec_per_km=running_threshold_pace_sec_per_km,
                hr_rest_bpm=hr_rest_bpm,
                hr_max_bpm=hr_max_bpm,
            )
        else:
            tss_running = _estimate_running_tss_from_activity_details_pipeline(
                activity,
                activity_details_raw=details_payload,
                running_threshold_pace_sec_per_km=running_threshold_pace_sec_per_km,
                hr_rest_bpm=hr_rest_bpm,
                hr_max_bpm=hr_max_bpm,
                hr_threshold_bpm=hr_threshold_bpm,
            )
            if tss_running is None:
                fallback_model = _resolve_running_fallback_model(activity)
                if fallback_model == "v2":
                    # Classification stays metadata-only in v2 fallback (no multipliers by session kind).
                    tss_running = _estimate_running_tss_tp_like_v2(
                        activity,
                        hours=hours,
                        running_threshold_pace_sec_per_km=running_threshold_pace_sec_per_km,
                        hr_rest_bpm=hr_rest_bpm,
                        hr_max_bpm=hr_max_bpm,
                    )
                else:
                    tss_running = _estimate_running_tss_tp_like_adjusted(
                        activity,
                        hours=hours,
                        running_threshold_pace_sec_per_km=running_threshold_pace_sec_per_km,
                        hr_rest_bpm=hr_rest_bpm,
                        hr_max_bpm=hr_max_bpm,
                    )
        if tss_running is not None:
            return tss_running, "TSS"

    elif is_trail_hike_walk:
        details_payload = (
            activity_details_raw
            or activity.get("_activity_details_raw")
            or activity.get("activity_details_raw")
            or activity.get("activityDetailsRaw")
        )
        if is_trail:
            if _should_use_raw_hr_tss_for_fast_trail(activity):
                # Keep fast-trail pattern intact.
                if use_trail_splits:
                    tss_trail_precise = _estimate_trail_tss_high_precision(
                        activity,
                        hours=hours,
                        running_threshold_pace_sec_per_km=running_threshold_pace_sec_per_km,
                        hr_rest_bpm=hr_rest_bpm,
                        hr_max_bpm=hr_max_bpm,
                        splits_raw=splits_raw,
                    )
                    if tss_trail_precise is not None:
                        return max(0.0, float(tss_trail_precise)), "TSS"

                tss_hr_stream = _estimate_hr_tss_from_activity_details(
                    activity,
                    hours=hours,
                    activity_details_raw=details_payload,
                    hr_rest_bpm=hr_rest_bpm,
                    hr_max_bpm=hr_max_bpm,
                    min_coverage_ratio=0.40,
                )
                if tss_hr_stream is not None:
                    return max(0.0, float(tss_hr_stream)), "hrTSS"

                tss_trail_tp_like = _estimate_trail_hr_tss_tp_like(
                    activity,
                    hours=hours,
                    hr_zones_raw=hr_zones_raw,
                )
                if tss_trail_tp_like is not None:
                    return max(0.0, float(tss_trail_tp_like)), "hrTSS"

                tss_hr_zones = _estimate_hr_tss_from_zones(
                    activity,
                    hours=hours,
                    hr_zones_raw=hr_zones_raw,
                    hr_rest_bpm=hr_rest_bpm,
                    hr_max_bpm=hr_max_bpm,
                    apply_cap=False,
                )
                if tss_hr_zones is not None:
                    return max(0.0, float(tss_hr_zones)), "hrTSS"
            else:
                # TP-like for non-fast trail: prefer LTHR-based hrTSS, then fallback to pace-based TSS.
                lthr = _resolve_hr_threshold_bpm_for_activity(activity, hr_threshold_bpm)
                if lthr is not None:
                    tss_hr_stream_lthr = _estimate_hr_tss_from_activity_details_lthr(
                        activity,
                        hours=hours,
                        activity_details_raw=details_payload,
                        hr_threshold_bpm=lthr,
                        hr_rest_bpm=hr_rest_bpm,
                        attenuate_sustained_low_if=True,
                        min_coverage_ratio=0.40,
                    )
                    if tss_hr_stream_lthr is not None:
                        return max(0.0, float(tss_hr_stream_lthr)), "hrTSS"

                    tss_hr_zones_lthr = _estimate_hr_tss_from_zones_lthr(
                        activity,
                        hours=hours,
                        hr_threshold_bpm=lthr,
                        hr_rest_bpm=hr_rest_bpm,
                        hr_zones_raw=hr_zones_raw,
                        min_coverage_ratio=0.35,
                    )
                    if tss_hr_zones_lthr is not None:
                        return max(0.0, float(tss_hr_zones_lthr)), "hrTSS"

                    tss_trail_pace = _estimate_tss_from_threshold_pace(
                        activity,
                        hours=hours,
                        running_threshold_pace_sec_per_km=running_threshold_pace_sec_per_km,
                        prefer_effective_running_pace=True,
                        if_pace_ceiling=1.50,
                    )
                    if tss_trail_pace is not None:
                        return max(0.0, float(tss_trail_pace)), "TSS"
                else:
                    # No LTHR available: keep legacy non-fast trail behavior.
                    if use_trail_splits:
                        tss_trail_precise = _estimate_trail_tss_high_precision(
                            activity,
                            hours=hours,
                            running_threshold_pace_sec_per_km=running_threshold_pace_sec_per_km,
                            hr_rest_bpm=hr_rest_bpm,
                            hr_max_bpm=hr_max_bpm,
                            splits_raw=splits_raw,
                        )
                        if tss_trail_precise is not None:
                            return max(0.0, float(tss_trail_precise)), "TSS"

                    tss_hr_stream = _estimate_hr_tss_from_activity_details(
                        activity,
                        hours=hours,
                        activity_details_raw=details_payload,
                        hr_rest_bpm=hr_rest_bpm,
                        hr_max_bpm=hr_max_bpm,
                        min_coverage_ratio=0.40,
                    )
                    if tss_hr_stream is not None:
                        return max(0.0, float(tss_hr_stream)), "hrTSS"

                    tss_trail_tp_like = _estimate_trail_hr_tss_tp_like(
                        activity,
                        hours=hours,
                        hr_zones_raw=hr_zones_raw,
                    )
                    if tss_trail_tp_like is not None:
                        return max(0.0, float(tss_trail_tp_like)), "hrTSS"

                    tss_hr_zones = _estimate_hr_tss_from_zones(
                        activity,
                        hours=hours,
                        hr_zones_raw=hr_zones_raw,
                        hr_rest_bpm=hr_rest_bpm,
                        hr_max_bpm=hr_max_bpm,
                        apply_cap=False,
                    )
                    if tss_hr_zones is not None:
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

    act_type = _resolve_activity_type_for_routing(activity)
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
    if tss_label == "hrTSS" and (
        activity.get("_activity_details_raw")
        or activity.get("activity_details_raw")
        or activity.get("activityDetailsRaw")
    ):
        return "hr_stream"

    if tss_label == "hrTSS" and hr_zones_raw:
        return "hr_zones"
    if tss_label == "hrTSS":
        return "hr_avg_or_rpe"
    return "unknown"


def _resolve_running_threshold_pace_sec_per_km(profile: dict | None) -> float | None:
    if not isinstance(profile, dict):
        return None

    perf = profile.get("performance") if isinstance(profile.get("performance"), dict) else {}
    user_data = profile.get("userData") if isinstance(profile.get("userData"), dict) else {}
    candidates: list[Any] = [
        perf.get("running_threshold_pace_sec_per_km"),
        perf.get("running_threshold_pace"),
        perf.get("lactate_threshold_pace_sec_per_km"),
        perf.get("lactate_threshold_pace"),
        perf.get("pace_at_lactate_threshold"),
        perf.get("threshold_pace"),
        user_data.get("runningThresholdPaceSecPerKm"),
        user_data.get("runningThresholdPace"),
        user_data.get("lactateThresholdPaceSecPerKm"),
        user_data.get("lactateThresholdPace"),
        user_data.get("paceAtLactateThreshold"),
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
        user_data.get("lactateThresholdSpeedMps"),
        user_data.get("lactateThresholdSpeed"),
        user_data.get("runningThresholdSpeedMps"),
        user_data.get("runningThresholdSpeed"),
    ]
    for raw_speed in speed_candidates:
        pace = _speed_ms_to_pace_sec_per_km(raw_speed)
        if pace and pace > 0:
            return pace

    # Garmin profile payloads can nest threshold pace/speed under zone settings.
    pace_keys = {
        "running_threshold_pace_sec_per_km",
        "running_threshold_pace",
        "threshold_pace_sec_per_km",
        "threshold_pace",
        "runningThresholdPaceSecPerKm",
        "runningThresholdPace",
        "thresholdPaceSecPerKm",
        "thresholdPace",
        "paceAtLactateThreshold",
    }
    speed_keys = {
        "running_threshold_speed_mps",
        "running_threshold_speed",
        "threshold_speed_mps",
        "threshold_speed",
        "runningThresholdSpeedMps",
        "runningThresholdSpeed",
        "lactateThresholdSpeedMps",
        "lactateThresholdSpeed",
        "thresholdSpeedMps",
        "thresholdSpeed",
    }

    def _walk(node: Any) -> float | None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in pace_keys:
                    pace = _parse_pace_to_sec_per_km(value)
                    if pace and pace > 0:
                        return pace
                if key in speed_keys:
                    pace = _speed_ms_to_pace_sec_per_km(value)
                    if pace and pace > 0:
                        return pace

            for value in node.values():
                found = _walk(value)
                if found and found > 0:
                    return found

        elif isinstance(node, list):
            for item in node:
                found = _walk(item)
                if found and found > 0:
                    return found

        return None

    nested = _walk(profile)
    if nested and nested > 0:
        return nested

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


# Public API (stable surface for reuse in other projects)
SPORT_MODEL_DEFAULTS = _SPORT_MODEL_DEFAULTS


def to_iso_date(value: Any) -> str | None:
    return _to_iso_date(value)


def extract_training_load_points(payload: Any) -> list[dict]:
    return _extract_training_load_points(payload)


def extract_activity_duration_hours(activity: dict) -> float:
    return _extract_activity_duration_hours(activity)


def extract_activity_distance_km(activity: dict) -> float | None:
    return _extract_activity_distance_km(activity)


def parse_pace_to_sec_per_km(raw: Any) -> float | None:
    return _parse_pace_to_sec_per_km(raw)


def speed_ms_to_pace_sec_per_km(raw_speed: Any) -> float | None:
    return _speed_ms_to_pace_sec_per_km(raw_speed)


def extract_avg_pace_sec_per_km(activity: dict) -> float | None:
    return _extract_avg_pace_sec_per_km(activity)


def extract_running_effective_pace_sec_per_km(activity: dict) -> float | None:
    return _extract_running_effective_pace_sec_per_km(activity)


def should_use_raw_hr_tss_for_fast_trail(activity: dict) -> bool:
    return _should_use_raw_hr_tss_for_fast_trail(activity)


def extract_training_load_tss(activity: dict) -> float | None:
    return _extract_training_load_tss(activity)


def estimate_if_from_hr(
    activity: dict,
    cycling_formula: bool,
    hr_rest_bpm: float | None = None,
    hr_max_bpm: float | None = None,
) -> float | None:
    return _estimate_if_from_hr(
        activity,
        cycling_formula=cycling_formula,
        hr_rest_bpm=hr_rest_bpm,
        hr_max_bpm=hr_max_bpm,
    )


def estimate_hr_tss_from_zones(
    activity: dict,
    hours: float,
    hr_zones_raw: str | None = None,
    hr_rest_bpm: float | None = None,
    hr_max_bpm: float | None = None,
    apply_cap: bool = True,
    min_coverage_ratio: float = 0.0,
) -> float | None:
    return _estimate_hr_tss_from_zones(
        activity,
        hours=hours,
        hr_zones_raw=hr_zones_raw,
        hr_rest_bpm=hr_rest_bpm,
        hr_max_bpm=hr_max_bpm,
        apply_cap=apply_cap,
        min_coverage_ratio=min_coverage_ratio,
    )


def resolve_hr_profile_values(profile: dict | None) -> tuple[float | None, float | None]:
    return _resolve_hr_profile_values(profile)


def extract_threshold_pace_sec_per_km(
    activity: dict,
    running_threshold_pace_sec_per_km: float | None = None,
) -> float | None:
    return _extract_threshold_pace_sec_per_km(activity, running_threshold_pace_sec_per_km)


def estimate_if_from_rpe(activity: dict) -> float | None:
    return _estimate_if_from_rpe(activity)


def extract_strength_rpe_10(activity: dict) -> float | None:
    return _extract_strength_rpe_10(activity)


def estimate_strength_if(activity: dict) -> float | None:
    return _estimate_strength_if(activity)


def estimate_strength_tss_from_rpe_minutes(activity: dict, hours: float) -> float | None:
    return _estimate_strength_tss_from_rpe_minutes(activity, hours)


def estimate_walk_hike_tss(
    activity: dict,
    hours: float,
    hr_zones_raw: str | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
) -> tuple[float | None, str | None]:
    return _estimate_walk_hike_tss(activity, hours, hr_zones_raw, hr_rest_bpm, hr_max_bpm)


def estimate_tss_from_power_ftp(activity: dict, ftp: float | None, hours: float) -> float | None:
    return _estimate_tss_from_power_ftp(activity, ftp, hours)


def has_activity_power_data(activity: dict) -> bool:
    return _has_activity_power_data(activity)


def estimate_tss_from_threshold_pace(
    activity: dict,
    hours: float,
    running_threshold_pace_sec_per_km: float | None = None,
    prefer_effective_running_pace: bool = False,
    if_pace_ceiling: float = 1.20,
) -> float | None:
    return _estimate_tss_from_threshold_pace(
        activity,
        hours,
        running_threshold_pace_sec_per_km,
        prefer_effective_running_pace,
        if_pace_ceiling,
    )


def extract_running_if_from_threshold_pace(
    activity: dict,
    running_threshold_pace_sec_per_km: float | None = None,
    prefer_effective_running_pace: bool = False,
    if_pace_ceiling: float = 1.20,
) -> float | None:
    return _extract_running_if_from_threshold_pace(
        activity,
        running_threshold_pace_sec_per_km,
        prefer_effective_running_pace,
        if_pace_ceiling,
    )


def extract_running_session_signals(activity: dict) -> dict[str, Any]:
    return _extract_running_session_signals(activity)


def classify_running_session_with_confidence(activity: dict) -> dict[str, Any]:
    return _classify_running_session_with_confidence(activity)


def classify_running_session(activity: dict) -> str:
    cls = _classify_running_session_with_confidence(activity)
    return str(cls.get("session_kind") or "calidad")


def estimate_running_tss_examined(
    activity: dict,
    hours: float,
    running_threshold_pace_sec_per_km: float | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
) -> float | None:
    return _estimate_running_tss_examined(
        activity,
        hours,
        running_threshold_pace_sec_per_km,
        hr_rest_bpm,
        hr_max_bpm,
    )


def estimate_running_tss_tp_like_adjusted(
    activity: dict,
    hours: float,
    running_threshold_pace_sec_per_km: float | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
) -> float | None:
    return _estimate_running_tss_tp_like_adjusted(
        activity,
        hours,
        running_threshold_pace_sec_per_km,
        hr_rest_bpm,
        hr_max_bpm,
    )


def estimate_running_tss_tp_like_v2(
    activity: dict,
    hours: float,
    running_threshold_pace_sec_per_km: float | None,
    hr_rest_bpm: float | None,
    hr_max_bpm: float | None,
) -> float | None:
    return _estimate_running_tss_tp_like_v2(
        activity,
        hours,
        running_threshold_pace_sec_per_km,
        hr_rest_bpm,
        hr_max_bpm,
    )


def estimate_if_from_training_effect(activity: dict) -> float | None:
    return _estimate_if_from_training_effect(activity)


def estimate_session_tss(
    activity: dict,
    ftp: float | None = None,
    running_threshold_pace_sec_per_km: float | None = None,
    hr_rest_bpm: float | None = None,
    hr_max_bpm: float | None = None,
    hr_zones_raw: str | None = None,
    splits_raw: str | None = None,
    activity_details_raw: str | None = None,
    use_trail_splits: bool = True,
    hr_threshold_bpm: float | None = None,
) -> tuple[float, str]:
    return _estimate_session_tss(
        activity=activity,
        ftp=ftp,
        running_threshold_pace_sec_per_km=running_threshold_pace_sec_per_km,
        hr_rest_bpm=hr_rest_bpm,
        hr_max_bpm=hr_max_bpm,
        hr_zones_raw=hr_zones_raw,
        splits_raw=splits_raw,
        activity_details_raw=activity_details_raw,
        use_trail_splits=use_trail_splits,
        hr_threshold_bpm=hr_threshold_bpm,
    )


def infer_tss_source_tag(activity: dict, tss_label: str, ftp: float | None, hr_zones_raw: str | None) -> str:
    return _infer_tss_source_tag(activity, tss_label, ftp, hr_zones_raw)


def resolve_running_threshold_pace_sec_per_km(profile: dict | None) -> float | None:
    return _resolve_running_threshold_pace_sec_per_km(profile)


def percentile(values: list[float], pct: float, default: float = 0.0) -> float:
    return _percentile(values, pct, default)


def resolve_sport_model_cfg(profile: dict | None) -> dict:
    return _resolve_sport_model_cfg(profile)


def compute_weekly_spike_signal(
    series: list[dict],
    reference_day: date | None = None,
    threshold_ratio: float = 0.20,
) -> dict[str, Any]:
    return _compute_weekly_spike_signal(series, reference_day, threshold_ratio)


def compute_load_fatigue_metrics(
    activities: list[dict],
    trend_payload: Any,
    profile: dict | None = None,
    days_window: int = 56,
    reference_day: date | None = None,
) -> dict | None:
    return _compute_load_fatigue_metrics(
        activities,
        trend_payload,
        profile,
        days_window,
        reference_day,
    )


__all__ = [
    "TSS_FORMULA_VERSION",
    "RUNNING_TSS_FALLBACK_HR_GUARDRAIL_RATIO",
    "TRAIL_FAST_PACE_RAW_ZONES_SEC_PER_KM",
    "SPORT_MODEL_DEFAULTS",
    "to_iso_date",
    "extract_training_load_points",
    "extract_activity_duration_hours",
    "extract_activity_distance_km",
    "parse_pace_to_sec_per_km",
    "speed_ms_to_pace_sec_per_km",
    "extract_avg_pace_sec_per_km",
    "extract_running_effective_pace_sec_per_km",
    "should_use_raw_hr_tss_for_fast_trail",
    "extract_training_load_tss",
    "estimate_if_from_hr",
    "estimate_hr_tss_from_zones",
    "resolve_hr_profile_values",
    "extract_threshold_pace_sec_per_km",
    "estimate_if_from_rpe",
    "extract_strength_rpe_10",
    "estimate_strength_if",
    "estimate_strength_tss_from_rpe_minutes",
    "estimate_walk_hike_tss",
    "estimate_tss_from_power_ftp",
    "has_activity_power_data",
    "estimate_tss_from_threshold_pace",
    "extract_running_if_from_threshold_pace",
    "extract_running_session_signals",
    "classify_running_session_with_confidence",
    "classify_running_session",
    "estimate_running_tss_examined",
    "estimate_running_tss_tp_like_adjusted",
    "estimate_running_tss_tp_like_v2",
    "estimate_if_from_training_effect",
    "estimate_session_tss",
    "infer_tss_source_tag",
    "resolve_running_threshold_pace_sec_per_km",
    "percentile",
    "resolve_sport_model_cfg",
    "compute_weekly_spike_signal",
    "compute_load_fatigue_metrics",
]
