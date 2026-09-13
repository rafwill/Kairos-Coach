from __future__ import annotations

import json

from agent.load_metrics import (
    _extract_duration_seconds_from_activity_details_payload,
    _resolve_activity_duration_hours,
)


def test_details_duration_prefers_activity_level_summary_over_nested_laps():
    payload = {
        "summaryDTO": {"elapsedDuration": 36612},  # 10.170 h
        "laps": [
            {"duration": 2400},
            {"duration": 3000},
            {"duration": 4000},
        ],
        "segments": [
            {"meta": {"duration": 99999}},
        ],
    }

    secs = _extract_duration_seconds_from_activity_details_payload(json.dumps(payload))
    assert secs is not None
    assert abs(secs - 36612.0) < 1e-6


def test_details_duration_prefers_root_activity_field_over_nested_values():
    payload = {
        "durationInSeconds": 1800,
        "laps": [{"duration": 7200}],
    }

    secs = _extract_duration_seconds_from_activity_details_payload(json.dumps(payload))
    assert secs is not None
    assert abs(secs - 1800.0) < 1e-6


def test_resolve_duration_uses_hr_zones_when_activity_and_details_missing_duration():
    activity = {"activityType": "trail_running"}
    zones = json.dumps(
        [
            {"zoneNumber": 1, "secsInZone": 600, "minHeartRateIn": 110, "maxHeartRateIn": 120},
            {"zoneNumber": 2, "secsInZone": 1200, "minHeartRateIn": 121, "maxHeartRateIn": 136},
            {"zoneNumber": 3, "secsInZone": 300, "minHeartRateIn": 137, "maxHeartRateIn": 150},
        ]
    )

    hours = _resolve_activity_duration_hours(activity, hr_zones_raw=zones, activity_details_raw=None)
    assert abs(hours - (2100.0 / 3600.0)) < 1e-9


def test_resolve_duration_uses_last_hr_sample_for_garmin_indexed_details_payload():
    activity = {"activityType": "trail_running"}
    payload = {
        "metricDescriptors": [
            {"key": "directTimestamp", "metricsIndex": 0},
            {"key": "directHeartRate", "metricsIndex": 1},
        ],
        "activityDetailMetrics": [
            {"metrics": [0.0, 140.0]},
            {"metrics": [300.0, 143.0]},
            {"metrics": [600.0, 145.0]},
            {"metrics": [900.0, 146.0]},
            {"metrics": [1200.0, 147.0]},
        ],
    }

    hours = _resolve_activity_duration_hours(
        activity,
        hr_zones_raw=None,
        activity_details_raw=json.dumps(payload),
    )
    assert abs(hours - (1200.0 / 3600.0)) < 1e-9
