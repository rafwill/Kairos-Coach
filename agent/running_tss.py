"""Motor de cálculo de rTSS/hrTSS para running.

Este módulo implementa una aproximación documentada de la metodología de
TrainingPeaks para carrera, usando:

- rTSS por ritmo con NGP aproximado (ajuste por pendiente + normalización 4a potencia).
- hrTSS por TRIMP de Banister calibrado para que 60 min a LTHR = 100.

Importante:
- NGP exacto y tabla interna de multiplicadores de hrTSS de TrainingPeaks no son
  públicos; esta implementación es una aproximación reproducible y testeable.
"""

from __future__ import annotations

import json
import math
from statistics import fmean, pstdev
from typing import Any


_REQUIRED_DESCRIPTOR_KEYS = (
    "directTimestamp",
    "directSpeed",
    "directHeartRate",
    "directElevation",
    "sumDistance",
)

# Rasgos/umbrales fijados por separación entre sesiones intervaladas y rodajes
# (sin optimizar contra error TP):
# - cv_if >= 0.27: alta variabilidad interna de intensidad.
# - o transición por hora baja + share_fast mínimo: patrón de bloques largos.
_INTERVAL_CV_IF_THRESHOLD = 0.27
_INTERVAL_LOW_TRANSITIONS_PER_H_THRESHOLD = 80.0
_INTERVAL_LOW_TRANSITIONS_MIN_FAST_SHARE = 0.09


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _moving_average(values: list[float], window: int) -> list[float]:
    """Calcula media móvil causal simple.

    Para cada índice i:
        y[i] = media(values[max(0, i-window+1) : i+1])
    """
    if window <= 1 or not values:
        return list(values)

    out: list[float] = []
    acc = 0.0
    for idx, val in enumerate(values):
        acc += val
        if idx >= window:
            acc -= values[idx - window]
            out.append(acc / float(window))
        else:
            out.append(acc / float(idx + 1))
    return out


def parsear_activity_details_garmin(activity_details_raw: Any) -> dict[str, list[float]]:
    """Parsea `get_activity_details` y devuelve series base.

    Espera estructura con:
    - `metricDescriptors`: lista de descriptores con `key` y `metricsIndex`.
    - `activityDetailMetrics`: filas con array `metrics`.

    Devuelve diccionario con listas homogéneas:
    - `tiempo_s`, `velocidad_ms`, `hr_bpm`, `elevacion_m`, `distancia_m`.

    Lanza:
    - `ValueError` si faltan claves requeridas en `metricDescriptors`.
    """
    data = activity_details_raw
    if isinstance(data, str):
        text = data.strip()
        if text.startswith("{") or text.startswith("["):
            data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError("activity_details_raw debe ser un JSON objeto con metricDescriptors")

    descriptors = data.get("metricDescriptors")
    rows = data.get("activityDetailMetrics")
    if not isinstance(descriptors, list) or not isinstance(rows, list):
        raise ValueError("Faltan metricDescriptors/activityDetailMetrics en activity_details")

    by_key: dict[str, int] = {}
    for desc in descriptors:
        if not isinstance(desc, dict):
            continue
        key = str(desc.get("key") or "").strip()
        idx = _as_float(desc.get("metricsIndex"))
        if not key or idx is None:
            continue
        by_key[key] = int(idx)

    missing = [k for k in _REQUIRED_DESCRIPTOR_KEYS if k not in by_key]
    if missing:
        raise ValueError(f"Faltan metricDescriptors requeridos: {', '.join(missing)}")

    t_idx = by_key["directTimestamp"]
    v_idx = by_key["directSpeed"]
    h_idx = by_key["directHeartRate"]
    e_idx = by_key["directElevation"]
    d_idx = by_key["sumDistance"]

    tiempo_s: list[float] = []
    velocidad_ms: list[float] = []
    hr_bpm: list[float] = []
    elevacion_m: list[float] = []
    distancia_m: list[float] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, list):
            continue
        max_idx = max(t_idx, v_idx, h_idx, e_idx, d_idx)
        if len(metrics) <= max_idx:
            continue

        t_raw = _as_float(metrics[t_idx])
        v_raw = _as_float(metrics[v_idx])
        h_raw = _as_float(metrics[h_idx])
        e_raw = _as_float(metrics[e_idx])
        d_raw = _as_float(metrics[d_idx])
        if None in (t_raw, v_raw, h_raw, e_raw, d_raw):
            continue

        t_val = float(t_raw)
        if t_val > 10_000_000_000:
            t_val /= 1000.0

        tiempo_s.append(t_val)
        velocidad_ms.append(max(0.0, float(v_raw)))
        hr_bpm.append(max(0.0, float(h_raw)))
        elevacion_m.append(float(e_raw))
        distancia_m.append(max(0.0, float(d_raw)))

    if len(tiempo_s) < 3:
        raise ValueError("No hay suficientes muestras válidas en activityDetailMetrics")

    # Normaliza timestamps absolutos a relativos.
    if tiempo_s[0] > 86_400 and (tiempo_s[-1] - tiempo_s[0]) > 0:
        base = tiempo_s[0]
        tiempo_s = [t - base for t in tiempo_s]

    return {
        "tiempo_s": tiempo_s,
        "velocidad_ms": velocidad_ms,
        "hr_bpm": hr_bpm,
        "elevacion_m": elevacion_m,
        "distancia_m": distancia_m,
    }


def remuestrear_1hz_lineal(tiempo_s: list[float], valores: list[float]) -> tuple[list[float], list[float]]:
    """Remuestrea una serie irregular a 1 Hz por interpolación lineal."""
    if len(tiempo_s) != len(valores) or len(tiempo_s) < 2:
        return [], []

    pairs = sorted((float(t), float(v)) for t, v in zip(tiempo_s, valores))
    t_sorted = [p[0] for p in pairs]
    v_sorted = [p[1] for p in pairs]

    t0 = int(math.floor(t_sorted[0]))
    t1 = int(math.floor(t_sorted[-1]))
    if t1 <= t0:
        return [], []

    out_t = list(range(t0, t1 + 1))
    out_v: list[float] = []

    j = 0
    n = len(t_sorted)
    for ts in out_t:
        while j + 1 < n and t_sorted[j + 1] < ts:
            j += 1

        if j + 1 >= n:
            out_v.append(v_sorted[-1])
            continue

        ta, tb = t_sorted[j], t_sorted[j + 1]
        va, vb = v_sorted[j], v_sorted[j + 1]

        if tb <= ta:
            out_v.append(va)
            continue

        ratio = (ts - ta) / (tb - ta)
        ratio = max(0.0, min(1.0, ratio))
        out_v.append(va + (vb - va) * ratio)

    # Rebase a 0..N para estabilidad posterior.
    base = float(out_t[0])
    out_t_rel = [float(t - base) for t in out_t]
    return out_t_rel, out_v


def detectar_tramos_pausados(
    velocidad_1hz_ms: list[float],
    min_speed_ms: float = 0.5,
    min_duration_s: int = 20,
) -> list[bool]:
    """Detecta tramos parados/pausados sostenidos según velocidad y duración mínima."""
    mask = [False] * len(velocidad_1hz_ms)
    if not velocidad_1hz_ms:
        return mask

    start = None
    for idx, spd in enumerate(velocidad_1hz_ms):
        if spd <= min_speed_ms:
            if start is None:
                start = idx
        else:
            if start is not None and (idx - start) >= min_duration_s:
                for k in range(start, idx):
                    mask[k] = True
            start = None

    if start is not None and (len(velocidad_1hz_ms) - start) >= min_duration_s:
        for k in range(start, len(velocidad_1hz_ms)):
            mask[k] = True

    return mask


def coste_minetti(pendiente: float) -> float:
    """Coste energético de Minetti et al. (2002).

    C(i) = 155.4 i^5 - 30.4 i^4 - 43.3 i^3 + 46.3 i^2 + 19.5 i + 3.6
    donde i es la pendiente en fracción (0.10 = 10%).
    """
    i = float(pendiente)
    return (
        155.4 * (i**5)
        - 30.4 * (i**4)
        - 43.3 * (i**3)
        + 46.3 * (i**2)
        + 19.5 * i
        + 3.6
    )


def calcular_pendiente_por_muestra(
    elevacion_1hz_m: list[float],
    distancia_1hz_m: list[float],
    smooth_window_s: int = 7,
    min_delta_dist_m: float = 1.0,
    grade_clip: float = 0.30,
) -> list[float]:
    """Calcula pendiente por muestra como Δelev_suavizada/Δdist con clipping."""
    if not elevacion_1hz_m or len(elevacion_1hz_m) != len(distancia_1hz_m):
        return []

    elev_smooth = _moving_average(elevacion_1hz_m, max(1, int(smooth_window_s)))

    grades = [0.0] * len(elev_smooth)
    for i in range(1, len(elev_smooth)):
        de = elev_smooth[i] - elev_smooth[i - 1]
        dd = distancia_1hz_m[i] - distancia_1hz_m[i - 1]
        if dd < min_delta_dist_m:
            grades[i] = grades[i - 1]
            continue
        g = de / dd
        if g > grade_clip:
            g = grade_clip
        elif g < -grade_clip:
            g = -grade_clip
        grades[i] = g
    return grades


def velocidad_ajustada_por_pendiente(velocidad_ms: list[float], pendiente: list[float]) -> list[float]:
    """Ajusta velocidad por pendiente con factor C(i)/C(0)."""
    if len(velocidad_ms) != len(pendiente):
        return []

    c0 = coste_minetti(0.0)
    out: list[float] = []
    for v, g in zip(velocidad_ms, pendiente):
        factor = coste_minetti(g) / c0 if c0 > 0 else 1.0
        out.append(max(0.0, float(v) * factor))
    return out


def calcular_ngp(
    velocidad_ajustada_ms_1hz: list[float],
    paused_mask: list[bool] | None = None,
    rolling_window_s: int = 30,
) -> float | None:
    """Calcula NGP con media móvil de 30 s y potencia cuarta.

    NGP = ( media( media_movil_30s(v_ajustada)^4 ) )^(1/4)

    Si `paused_mask` se proporciona, excluye esos puntos del cálculo para evitar que
    tramos de parada sostenidos depriman artificialmente el NGP.
    """
    if not velocidad_ajustada_ms_1hz:
        return None

    if paused_mask is not None and len(paused_mask) == len(velocidad_ajustada_ms_1hz):
        filtered = [v for v, paused in zip(velocidad_ajustada_ms_1hz, paused_mask) if not paused]
    else:
        filtered = list(velocidad_ajustada_ms_1hz)

    if len(filtered) < 5:
        return None

    roll = _moving_average(filtered, max(1, int(rolling_window_s)))
    p4 = [v**4 for v in roll if v > 0]
    if not p4:
        return None

    return fmean(p4) ** 0.25


def _infer_interval_session_traits(
    velocidad_ajustada_ms_1hz: list[float],
    paused_mask: list[bool],
    ftpace_ms: float,
    rolling_window_s: int = 20,
) -> dict[str, float]:
    """Extrae rasgos de sesión intervalada sin usar TP.

    Rasgos:
    - share_fast: fracción de segundos con IF_20s >= 0.95.
    - cv_if: coef. de variación de IF_20s.
    - transitions_per_h: cambios de estado work/rest/mid por hora.
    """
    n = len(velocidad_ajustada_ms_1hz)
    if n < 60 or len(paused_mask) != n or ftpace_ms <= 0:
        return {
            "share_fast": 0.0,
            "cv_if": 0.0,
            "transitions_per_h": 0.0,
        }

    roll: list[float] = []
    acc = 0.0
    w = max(1, int(rolling_window_s))
    for i, val in enumerate(velocidad_ajustada_ms_1hz):
        x = 0.0 if paused_mask[i] else float(val)
        acc += x
        if i >= w:
            old = 0.0 if paused_mask[i - w] else float(velocidad_ajustada_ms_1hz[i - w])
            acc -= old
            m = acc / float(w)
        else:
            m = acc / float(i + 1)
        roll.append(max(0.0, m))

    if_values = [v / ftpace_ms for v in roll]
    share_fast = sum(1 for x in if_values if x >= 0.95) / float(n)

    if_mean = fmean(if_values) if if_values else 0.0
    cv_if = (pstdev(if_values) / if_mean) if len(if_values) > 2 and if_mean > 0 else 0.0

    work_floor = max(0.80, if_mean + 0.05)
    rest_ceiling = min(0.85, if_mean - 0.05)

    transitions = 0
    prev_state = None
    for x in if_values:
        if x >= 0.95 or x >= work_floor:
            state = "work"
        elif x <= 0.75 or x <= rest_ceiling:
            state = "rest"
        else:
            state = "mid"

        if prev_state is None:
            prev_state = state
            continue
        if state != prev_state:
            transitions += 1
            prev_state = state

    duration_h = n / 3600.0
    transitions_per_h = transitions / duration_h if duration_h > 0 else 0.0

    return {
        "share_fast": float(share_fast),
        "cv_if": float(cv_if),
        "transitions_per_h": float(transitions_per_h),
    }


def _should_include_pauses_in_ngp(interval_traits: dict[str, float]) -> bool:
    """Decide si incluir pausas en NGP para sesiones intervaladas/mixtas.

    Regla fija basada en separación de rasgos (sin tuning por error TP):
    - `cv_if >= 0.27`, o
    - `transitions_per_h <= 80` y `share_fast >= 0.09`.
    """
    cv_if = float(interval_traits.get("cv_if") or 0.0)
    transitions_per_h = float(interval_traits.get("transitions_per_h") or 0.0)
    share_fast = float(interval_traits.get("share_fast") or 0.0)

    return (cv_if >= _INTERVAL_CV_IF_THRESHOLD) or (
        transitions_per_h <= _INTERVAL_LOW_TRANSITIONS_PER_H_THRESHOLD
        and share_fast >= _INTERVAL_LOW_TRANSITIONS_MIN_FAST_SHARE
    )


def calcular_rtss(duracion_seg: float, ngp_ms: float, ftpace_ms: float) -> tuple[float, float]:
    """Calcula rTSS e IF.

    IF = NGP / FTPace
    rTSS = (t × NGP × IF) / (FTPace × 3600) × 100
    """
    if duracion_seg <= 0 or ngp_ms <= 0 or ftpace_ms <= 0:
        return 0.0, 0.0

    if_val = ngp_ms / ftpace_ms
    rtss = (float(duracion_seg) * ngp_ms * if_val) / (ftpace_ms * 3600.0) * 100.0
    return max(0.0, rtss), max(0.0, if_val)


def trimp_banister(
    hr_media: float,
    hr_reposo: float,
    hr_max: float,
    minutos: float,
    sexo: str,
) -> float:
    """Calcula TRIMP de Banister por intervalo.

    TRIMP = minutos × frac_reserva × 0.64 × exp(k × frac_reserva)
    frac_reserva = (FC_media - FC_reposo) / (FC_max - FC_reposo)

    k = 1.92 (hombre) / 1.67 (mujer)
    """
    if minutos <= 0:
        return 0.0

    denom = max(1e-9, float(hr_max) - float(hr_reposo))
    frac = (float(hr_media) - float(hr_reposo)) / denom
    frac = max(0.0, min(1.2, frac))

    sexo_norm = str(sexo or "").strip().lower()
    k = 1.67 if sexo_norm in {"f", "female", "mujer"} else 1.92

    return float(minutos) * frac * 0.64 * math.exp(k * frac)


def calcular_hrtss(
    hr_serie_1hz: list[float],
    hr_reposo: float,
    hr_max: float,
    lthr: float,
    sexo: str,
) -> float | None:
    """Calcula hrTSS calibrado contra 1h continua a LTHR.

    hrTSS = 100 × TRIMP_entreno / TRIMP_1h_a_LTHR
    """
    if not hr_serie_1hz:
        return None
    if hr_max <= hr_reposo or lthr <= 0:
        return None

    dur_min = len(hr_serie_1hz) / 60.0
    hr_mean = fmean([max(0.0, float(v)) for v in hr_serie_1hz])

    trimp_session = trimp_banister(hr_mean, hr_reposo, hr_max, dur_min, sexo)
    trimp_ref = trimp_banister(float(lthr), hr_reposo, hr_max, 60.0, sexo)
    if trimp_ref <= 0:
        return None

    return max(0.0, 100.0 * (trimp_session / trimp_ref))


def procesar_actividad(activity_details_json: Any, atleta: dict[str, Any]) -> dict[str, Any]:
    """Orquesta el pipeline completo de rTSS/hrTSS para running.

    `atleta` debe incluir:
    - `ftpace_ms`: velocidad umbral de carrera (m/s)
    - `hr_reposo`, `hr_max`, `lthr`, `sexo`

    Devuelve:
    - `duracion_seg`, `ngp_ms`, `IF`, `rTSS`, `hrTSS`
    y metadatos de depuración.
    """
    parsed = parsear_activity_details_garmin(activity_details_json)

    t_raw = parsed["tiempo_s"]
    v_raw = parsed["velocidad_ms"]
    hr_raw = parsed["hr_bpm"]
    el_raw = parsed["elevacion_m"]
    d_raw = parsed["distancia_m"]

    t_1hz, v_1hz = remuestrear_1hz_lineal(t_raw, v_raw)
    _, hr_1hz = remuestrear_1hz_lineal(t_raw, hr_raw)
    _, el_1hz = remuestrear_1hz_lineal(t_raw, el_raw)
    _, d_1hz = remuestrear_1hz_lineal(t_raw, d_raw)

    n = min(len(t_1hz), len(v_1hz), len(hr_1hz), len(el_1hz), len(d_1hz))
    if n < 5:
        raise ValueError("No hay suficientes datos tras remuestreo 1 Hz")

    t_1hz = t_1hz[:n]
    v_1hz = v_1hz[:n]
    hr_1hz = hr_1hz[:n]
    el_1hz = el_1hz[:n]
    d_1hz = d_1hz[:n]

    paused = detectar_tramos_pausados(v_1hz)
    grade = calcular_pendiente_por_muestra(el_1hz, d_1hz)
    v_adj = velocidad_ajustada_por_pendiente(v_1hz, grade)

    duration_s = float(n)
    ftpace_ms = _as_float(atleta.get("ftpace_ms")) or 0.0

    interval_traits = _infer_interval_session_traits(v_adj, paused, ftpace_ms)
    include_pauses_in_ngp = _should_include_pauses_in_ngp(interval_traits)
    ngp = calcular_ngp(
        v_adj,
        paused_mask=(None if include_pauses_in_ngp else paused),
        rolling_window_s=30,
    )

    rtss, if_val = calcular_rtss(duration_s, ngp or 0.0, ftpace_ms)

    hr_reposo = _as_float(atleta.get("hr_reposo"))
    hr_max = _as_float(atleta.get("hr_max"))
    lthr = _as_float(atleta.get("lthr"))
    sexo = str(atleta.get("sexo") or "male")

    hrtss = None
    if hr_reposo is not None and hr_max is not None and lthr is not None:
        hrtss = calcular_hrtss(hr_1hz, hr_reposo, hr_max, lthr, sexo)

    return {
        "duracion_seg": duration_s,
        "ngp_ms": ngp,
        "IF": if_val,
        "rTSS": rtss,
        "hrTSS": hrtss,
        "paused_seconds": int(sum(1 for x in paused if x)),
        "samples_1hz": n,
        "include_pauses_in_ngp": include_pauses_in_ngp,
        "interval_share_fast": interval_traits.get("share_fast", 0.0),
        "interval_cv_if": interval_traits.get("cv_if", 0.0),
        "interval_transitions_per_h": interval_traits.get("transitions_per_h", 0.0),
    }
