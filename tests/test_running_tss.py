from __future__ import annotations

import math

import pytest
import agent.running_tss as running_tss_module

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


def _build_activity_details_from_speed_series(speed_ms_series: list[float], hr_bpm: float = 160.0) -> dict:
    descriptors = [
        {"key": "directTimestamp", "metricsIndex": 0},
        {"key": "directSpeed", "metricsIndex": 1},
        {"key": "directHeartRate", "metricsIndex": 2},
        {"key": "directElevation", "metricsIndex": 3},
        {"key": "sumDistance", "metricsIndex": 4},
    ]

    rows = []
    distance = 0.0
    for t, spd in enumerate(speed_ms_series):
        spd_f = max(0.0, float(spd))
        distance += spd_f
        rows.append(
            {
                "metrics": [
                    float(t),
                    spd_f,
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


def test_variant_a_gap_tempo_sustained_fails_due_to_low_variability_signature():
    # Test de regresion post-fix: tempo sostenido debe activar inclusion de pausas.
    # Patrón tipo tempo: bloque único sostenido entre calentamiento/enfriamiento.
    speed = ([2.8] * 600) + ([3.25] * 1800) + ([2.8] * 600)
    payload = _build_activity_details_from_speed_series(speed, hr_bpm=156.0)
    atleta = {
        "ftpace_ms": 4.083000183105469,
        "hr_reposo": 50.0,
        "hr_max": 190.0,
        "lthr": 169.0,
        "sexo": "male",
    }

    out = procesar_actividad(payload, atleta)

    cv_if = float(out["interval_cv_if"])
    transitions_per_h = float(out["interval_transitions_per_h"])
    share_fast = float(out["interval_share_fast"])
    variant_a_triggered = (cv_if >= 0.27) or (
        (transitions_per_h <= 80.0) and (share_fast >= 0.09)
    )

    assert transitions_per_h <= 80.0
    assert share_fast < 0.09
    assert cv_if < 0.27
    assert variant_a_triggered is False
    assert out["legacy_variant_a_triggered"] is False
    assert out["tempo_detector_triggered"] is True
    assert out["short_reps_detector_triggered"] is False
    assert out["include_pauses_in_ngp"] is True
    assert out["tempo_detector_longest_block_s"] >= 540.0


def test_variant_a_gap_short_reps_fail_due_to_high_transitions_and_low_fast_share():
    # Test de regresion post-fix: repeticiones cortas deben activar inclusion de pausas.
    # Repeticiones cortas: alternancia alta, pero picos breves se diluyen en ventana móvil.
    speed = [2.8] * 300
    for _ in range(12):
        speed.extend([4.3] * 12)   # trabajo corto
        speed.extend([2.2] * 24)   # recuperación más marcada para romper bloque tempo
    speed.extend([2.8] * 300)

    payload = _build_activity_details_from_speed_series(speed, hr_bpm=154.0)
    atleta = {
        "ftpace_ms": 4.083000183105469,
        "hr_reposo": 50.0,
        "hr_max": 190.0,
        "lthr": 169.0,
        "sexo": "male",
    }

    out = procesar_actividad(payload, atleta)

    cv_if = float(out["interval_cv_if"])
    transitions_per_h = float(out["interval_transitions_per_h"])
    share_fast = float(out["interval_share_fast"])
    variant_a_triggered = (cv_if >= 0.27) or (
        (transitions_per_h <= 80.0) and (share_fast >= 0.09)
    )

    assert transitions_per_h > 80.0
    assert share_fast < 0.09
    assert cv_if < 0.27
    assert variant_a_triggered is False
    assert out["legacy_variant_a_triggered"] is False
    assert out["tempo_detector_triggered"] is False
    assert out["short_reps_detector_triggered"] is True
    assert out["include_pauses_in_ngp"] is True
    assert out["short_reps_detector_transitions_per_h"] >= 80.0


def test_short_reps_detector_no_false_positive_easy_rodaje():
    # Rodaje continuo con variabilidad natural y pequeñas aceleraciones sueltas.
    speed: list[float] = []
    total_s = 75 * 60
    for t in range(total_s):
        base = 2.85
        low_wave = 0.12 * math.sin((2.0 * math.pi * t) / 180.0)
        short_wave = 0.05 * math.sin((2.0 * math.pi * t) / 13.0)
        speed.append(base + low_wave + short_wave)

    # Tres aceleraciones aisladas de 60 s cada una (no patrón de repeticiones cortas).
    for start in (900, 2100, 3300):
        for i in range(start, start + 60):
            speed[i] = 3.9

    payload = _build_activity_details_from_speed_series(speed, hr_bpm=150.0)
    atleta = {
        "ftpace_ms": 4.083000183105469,
        "hr_reposo": 50.0,
        "hr_max": 190.0,
        "lthr": 169.0,
        "sexo": "male",
    }

    out = procesar_actividad(payload, atleta)

    assert out["tempo_detector_triggered"] is False
    assert out["short_reps_detector_triggered"] is False
    assert out["short_reps_detector_share_fast"] >= 0.03
    assert out["short_reps_detector_work_bouts_in_range"] < 4


def test_short_reps_detector_no_false_positive_isolated_short_surges():
    # Rodaje continuo urbano con 5 aceleraciones cortas aisladas (no periódicas).
    speed: list[float] = []
    total_s = 75 * 60
    for t in range(total_s):
        base = 2.82
        low_wave = 0.09 * math.sin((2.0 * math.pi * t) / 220.0)
        jitter = 0.03 * math.sin((2.0 * math.pi * t) / 11.0)
        speed.append(base + low_wave + jitter)

    # Cinco eventos sueltos de 30 s separados por varios minutos.
    starts = (420, 1080, 1860, 2970, 3810)
    for start in starts:
        for i in range(start, start + 30):
            speed[i] = 4.0

    payload = _build_activity_details_from_speed_series(speed, hr_bpm=151.0)
    atleta = {
        "ftpace_ms": 4.083000183105469,
        "hr_reposo": 50.0,
        "hr_max": 190.0,
        "lthr": 169.0,
        "sexo": "male",
    }

    out = procesar_actividad(payload, atleta)

    # Caso exigente: share_fast y bouts podrían parecer intervalados,
    # pero sin densidad de alternancia propia de series.
    assert out["short_reps_detector_share_fast"] >= 0.03
    assert out["short_reps_detector_work_bouts_in_range"] >= 4
    assert out["short_reps_detector_transitions_per_h"] < 80.0
    assert out["tempo_detector_triggered"] is False
    assert out["short_reps_detector_triggered"] is False


def test_short_reps_is_skipped_when_tempo_detector_triggers(monkeypatch):
    payload = _build_activity_details_payload(total_s=300, speed_ms=3.0, hr_bpm=155.0, with_pause=False)
    atleta = {
        "ftpace_ms": 4.083000183105469,
        "hr_reposo": 50.0,
        "hr_max": 190.0,
        "lthr": 169.0,
        "sexo": "male",
    }

    def _fake_tempo(*args, **kwargs):
        return {
            "tempo_detector_triggered": True,
            "tempo_detector_if_threshold": 0.8,
            "tempo_detector_longest_block_s": 600.0,
        }

    def _forbidden_short_reps(*args, **kwargs):
        raise AssertionError("short_reps detector should not be evaluated when tempo is already triggered")

    monkeypatch.setattr(running_tss_module, "_detect_sustained_tempo_block", _fake_tempo)
    monkeypatch.setattr(running_tss_module, "_detect_short_repetitions_pattern", _forbidden_short_reps)

    out = procesar_actividad(payload, atleta)

    assert out["tempo_detector_triggered"] is True
    assert out["short_reps_detector_triggered"] is False
    assert out["include_pauses_in_ngp"] is True
