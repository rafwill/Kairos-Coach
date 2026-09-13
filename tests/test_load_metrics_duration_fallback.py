from __future__ import annotations

import json

from agent.load_metrics import estimate_session_tss


def _hr_zones_payload_one_hour() -> str:
    zones = [
        {"zoneNumber": 1, "secsInZone": 900, "minHeartRateIn": 110, "maxHeartRateIn": 120},
        {"zoneNumber": 2, "secsInZone": 1200, "minHeartRateIn": 121, "maxHeartRateIn": 136},
        {"zoneNumber": 3, "secsInZone": 1500, "minHeartRateIn": 137, "maxHeartRateIn": 150},
    ]
    return json.dumps(zones)


def _activity_details_hr_indexed_payload(total_seconds: int = 900, hr_bpm: float = 150.0) -> str:
    descriptors = [
        {"key": "directTimestamp", "metricsIndex": 0},
        {"key": "directHeartRate", "metricsIndex": 1},
    ]
    rows = []
    for t in range(total_seconds + 1):
        rows.append({"metrics": [float(t), float(hr_bpm)]})
    payload = {"metricDescriptors": descriptors, "activityDetailMetrics": rows}
    return json.dumps(payload)


def test_trail_missing_duration_uses_zone_duration_before_native_tss():
    activity = {
        "activityType": "trail_running",
        "trainingStressScore": 300.0,
        "averageHR": 145.0,
        "maxHR": 176.0,
    }

    tss, label = estimate_session_tss(
        activity,
        ftp=None,
        running_threshold_pace_sec_per_km=330.0,
        hr_rest_bpm=50.0,
        hr_max_bpm=185.0,
        hr_zones_raw=_hr_zones_payload_one_hour(),
        activity_details_raw=None,
        use_trail_splits=False,
        hr_threshold_bpm=169.0,
    )

    assert label == "hrTSS"
    assert tss > 0
    assert abs(tss - 300.0) > 1e-6


def test_trail_missing_duration_uses_details_time_axis_before_native_tss():
    activity = {
        "activityType": "trail_running",
        "trainingStressScore": 250.0,
    }

    tss, label = estimate_session_tss(
        activity,
        ftp=None,
        running_threshold_pace_sec_per_km=330.0,
        hr_rest_bpm=50.0,
        hr_max_bpm=185.0,
        hr_zones_raw=None,
        activity_details_raw=_activity_details_hr_indexed_payload(total_seconds=900, hr_bpm=155.0),
        use_trail_splits=False,
        hr_threshold_bpm=169.0,
    )

    assert label == "hrTSS"
    assert tss > 0
    assert abs(tss - 250.0) > 1e-6
