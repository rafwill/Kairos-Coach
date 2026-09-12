from __future__ import annotations

import math

import pytest

from agent.running_tss import (
    calcular_hrtss,
    calcular_ngp,
    calcular_pendiente_por_muestra,
    calcular_rtss,
    coste_minetti,
    detectar_tramos_pausados,
    parsear_activity_details_garmin,
    procesar_actividad,
    remuestrear_1hz_lineal,
    velocidad_ajustada_por_pendiente,
)


def _build_activity_details_payload(
    total_s: int = 120,
    speed_ms: float = 3.0,
    hr_bpm: float = 160.0,
    with_pause: bool = False,
) -> dict:
    descriptors = [
        {"key": "directTimestamp", "metricsIndex": 0},
        {"key": "directSpeed", "metricsIndex": 1},
        {"key": "directHeartRate", "metricsIndex": 2},
        {"key": "directElevation", "metricsIndex": 3},
        {"key": "sumDistance", "metricsIndex": 4},
    ]

    rows = []
    distance = 0.0
    for t in range(0, total_s + 1):
        spd = speed_ms
        if with_pause and 40 <= t < 80:
            spd = 0.0
        distance += spd
        rows.append(
            {
                "metrics": [
                    float(t),
                    float(spd),
                    float(hr_bpm),
                    100.0,
                    distance,
                ]
            }
        )

    return {
        "metricDescriptors": descriptors,
        "activityDetailMetrics": rows,
    }


def test_coste_minetti_base_and_positive_grade_increase():
    assert math.isclose(coste_minetti(0.0), 3.6, rel_tol=0.0, abs_tol=1e-9)
    assert coste_minetti(0.05) > coste_minetti(0.0)
    assert coste_minetti(0.10) > coste_minetti(0.05)


def test_ngp_constant_flat_series_matches_constant_speed():
    speed = [3.2] * 600
    grade = [0.0] * 600
    adjusted = velocidad_ajustada_por_pendiente(speed, grade)
    ngp = calcular_ngp(adjusted, paused_mask=None, rolling_window_s=30)
    assert ngp is not None
    assert abs(ngp - 3.2) < 1e-6


def test_rtss_is_100_for_one_hour_at_threshold_speed():
    rtss, if_value = calcular_rtss(3600.0, ngp_ms=3.5, ftpace_ms=3.5)
    assert abs(if_value - 1.0) < 1e-12
    assert abs(rtss - 100.0) < 1e-9


def test_hrtss_is_100_for_one_hour_at_lthr():
    lthr = 170.0
    hr_series = [lthr] * 3600
    hrtss = calcular_hrtss(hr_series, hr_reposo=50.0, hr_max=190.0, lthr=lthr, sexo="male")
    assert hrtss is not None
    assert abs(hrtss - 100.0) < 1e-9


def test_pause_segment_exclusion_avoids_artificial_ngp_drop():
    moving_speed = [3.0] * 120
    with_pause = [3.0] * 40 + [0.0] * 40 + [3.0] * 40

    ngp_moving = calcular_ngp(moving_speed, paused_mask=None, rolling_window_s=30)
    mask = detectar_tramos_pausados(with_pause, min_speed_ms=0.5, min_duration_s=20)
    ngp_filtered = calcular_ngp(with_pause, paused_mask=mask, rolling_window_s=30)
    ngp_unfiltered = calcular_ngp(with_pause, paused_mask=None, rolling_window_s=30)

    assert ngp_moving is not None and ngp_filtered is not None and ngp_unfiltered is not None
    assert ngp_filtered > ngp_unfiltered
    assert abs(ngp_filtered - ngp_moving) < 0.2


def test_irregular_sampling_resamples_to_1hz_correctly():
    t = [0.0, 1.8, 4.2, 6.9]
    v = [2.0, 2.0, 4.0, 4.0]
    t1, v1 = remuestrear_1hz_lineal(t, v)

    assert len(t1) == len(v1)
    assert len(t1) == 7  # 0..6
    # En t=3s entre 1.8 (2.0) y 4.2 (4.0) debe interpolar en torno a 3.0
    assert abs(v1[3] - 3.0) < 0.2


def test_parser_raises_explicit_error_when_required_descriptor_missing():
    payload = _build_activity_details_payload(total_s=20)
    payload["metricDescriptors"] = [d for d in payload["metricDescriptors"] if d["key"] != "directHeartRate"]

    with pytest.raises(ValueError) as exc:
        parsear_activity_details_garmin(payload)

    assert "directHeartRate" in str(exc.value)


def test_pipeline_returns_rtss_hrtss_for_valid_payload():
    payload = _build_activity_details_payload(total_s=600, speed_ms=3.0, hr_bpm=165.0, with_pause=True)
    atleta = {
        "ftpace_ms": 3.0,
        "hr_reposo": 50.0,
        "hr_max": 190.0,
        "lthr": 165.0,
        "sexo": "male",
    }

    out = procesar_actividad(payload, atleta)

    assert out["duracion_seg"] > 0
    assert out["ngp_ms"] is not None
    assert out["IF"] > 0
    assert out["rTSS"] > 0
    assert out["hrTSS"] is not None


def test_grade_computation_clips_extreme_values():
    elev = [0.0, 50.0, 100.0]
    dist = [0.0, 1.0, 2.0]
    grade = calcular_pendiente_por_muestra(elev, dist, smooth_window_s=1, min_delta_dist_m=0.1, grade_clip=0.30)
    assert len(grade) == 3
    assert max(grade) <= 0.30
