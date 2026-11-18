"""
agent/intensity.py — cuánto de lo previsto se hizo de verdad (11/11, petición del usuario).

Dos piezas, funciones puras:
- `distribution`: al calcular el TSS de una actividad (con su detalle segundo a segundo, el mismo que usa el rTSS),
  los segundos que pasó a cada ritmo con pendiente (GAP de Minetti, como el NGP) y a cada pulsación, sin las pausas.
  Se guarda una vez en `activity_tss.intensity`; no se vuelve a pedir el detalle a Garmin.
- `completion`: reparte esos segundos entre los tramos del plan de ese día, sin contar dos veces el mismo segundo:
  primero la calidad (Z3 o más, RP, FC3 o más), de la más exigente a la menos, con lo hecho en su zona o más rápido
  (decisión del usuario); después lo suave (calentamiento, rodaje Z1-Z2, recuperaciones, vuelta a la calma), solo con
  lo hecho dentro de su zona (decisión del usuario: ir más rápido en un rodaje Z2 no lo completa).
No se comparan vueltas: los planes de Kairos no se cargan en el reloj y las vueltas no coinciden con los bloques.

Parámetros fijados antes de ver resultados (11/11; no se ajustan mirando sesiones): mediana móvil centrada de
`SMOOTH_S` segundos sobre el GAP (el GPS segundo a segundo salta varios s/km; la mediana no difumina los cambios de
ritmo, la media sí: con ella una sesión sintética hecha tal cual salía al 96 %), ritmo en tramos de 1 s/km, FC en lpm,
pausas = las del rTSS (`detectar_tramos_pausados`) y los huecos de más de `MAX_SAMPLE_GAP_S` entre muestras (el reloj
parado: Garmin manda una muestra cada 2-4 s y en una pausa ninguna; al pasar a 1 Hz se rellenaban con ritmos
inventados, 12' de más el 8/11). Ritmos más lentos que `SLOWEST_PACE_S` (andar) van juntos en ese.
"""

from __future__ import annotations

import bisect
from typing import Any

from agent.run_plan import easy_hr_zone
from agent.running_tss import (
    calcular_pendiente_por_muestra,
    detectar_tramos_pausados,
    parsear_activity_details_garmin,
    remuestrear_1hz_lineal,
    velocidad_ajustada_por_pendiente,
)

VERSION = 1
SMOOTH_S = 15
SLOWEST_PACE_S = 900  # 15:00/km
MAX_SAMPLE_GAP_S = 10
EASY_ZONE_MAX = 2  # zona 1-2 (de ritmo o de FC) = suave; 3 o más = calidad


def _centered_median(values: list[float], window: int) -> list[float]:
    """Mediana móvil centrada: quita los saltos del GPS sin difuminar los cambios de ritmo (una media móvil de 15 s
    perdía unos 6 s a cada lado de cada serie: una sesión hecha tal cual salía al 96 %, 11/11)."""
    if window <= 1 or not values:
        return list(values)
    half = window // 2
    out = []
    win: list[float] = []
    lo = hi = 0  # ventana [lo, hi)
    for i in range(len(values)):
        while hi < min(len(values), i + half + 1):
            bisect.insort(win, values[hi])
            hi += 1
        while lo < max(0, i - half):
            win.pop(bisect.bisect_left(win, values[lo]))
            lo += 1
        m = len(win)
        out.append(win[m // 2] if m % 2 else (win[m // 2 - 1] + win[m // 2]) / 2)
    return out


def distribution(details_raw: Any, *, with_pace: bool) -> dict | None:
    """Segundos a cada ritmo (s/km, con pendiente) y a cada FC (lpm) en movimiento; None si el detalle no sirve.

    with_pace: solo en carrera (en bici o elíptica el ritmo no dice nada)."""
    try:
        parsed = parsear_activity_details_garmin(details_raw)
    except (ValueError, KeyError, TypeError):
        return None
    t_raw = parsed.get("tiempo_s") or []
    _, v = remuestrear_1hz_lineal(t_raw, parsed.get("velocidad_ms") or [])
    _, hr = remuestrear_1hz_lineal(t_raw, parsed.get("hr_bpm") or []) if parsed.get("hr_bpm") else ([], [])
    if len(v) < 60:
        return None
    paused = detectar_tramos_pausados(v)
    # Reloj parado: los segundos dentro de un hueco largo entre muestras no son de verdad.
    raw = sorted(float(x) for x in t_raw)
    base = int(raw[0] // 1)
    for ta, tb in zip(raw, raw[1:]):
        if tb - ta > MAX_SAMPLE_GAP_S:
            for k in range(int(ta - base) + 1, min(len(paused), int(tb - base))):
                paused[k] = True
    pace_hist: dict[str, int] = {}
    if with_pace:
        _, el = remuestrear_1hz_lineal(t_raw, parsed.get("elevacion_m") or [])
        _, dist = remuestrear_1hz_lineal(t_raw, parsed.get("distancia_m") or [])
        n = min(len(v), len(el), len(dist))
        gap = velocidad_ajustada_por_pendiente(v[:n], calcular_pendiente_por_muestra(el[:n], dist[:n])) if n == len(v) \
            else list(v)
        gap = _centered_median(gap or list(v), SMOOTH_S)
        for i, speed in enumerate(gap):
            if paused[i]:
                continue
            pace = SLOWEST_PACE_S if speed <= 1000 / SLOWEST_PACE_S else min(SLOWEST_PACE_S, round(1000 / speed))
            pace_hist[str(pace)] = pace_hist.get(str(pace), 0) + 1
    hr_hist: dict[str, int] = {}
    for i, bpm in enumerate(hr[:len(v)]):
        if paused[i] or not bpm or bpm <= 0:
            continue
        hr_hist[str(round(bpm))] = hr_hist.get(str(round(bpm)), 0) + 1
    moving = sum(1 for p in paused if not p)
    if not pace_hist and not hr_hist:
        return None
    return {"v": VERSION, "smooth_s": SMOOTH_S, "moving_s": moving, "pace": pace_hist, "hr": hr_hist}


def merge(dists: list[dict]) -> dict | None:
    """Varias actividades del mismo día (día doble, o el reloj partió la sesión) en una sola distribución."""
    dists = [d for d in dists if isinstance(d, dict)]
    if not dists:
        return None
    out: dict = {"moving_s": 0, "pace": {}, "hr": {}}
    for d in dists:
        out["moving_s"] += int(d.get("moving_s") or 0)
        for key in ("pace", "hr"):
            for k, s in (d.get(key) or {}).items():
                out[key][k] = out[key].get(k, 0) + int(s)
    return out


def _leaves(steps: list, times: int = 1, top: bool = True):
    """(tramo, veces, fuera de las series)."""
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        if s.get("steps"):
            yield from _leaves(s["steps"], times * int(s.get("reps") or 1), top=False)
        else:
            yield s, times * int(s.get("reps") or 1), top


def planned_parts(sessions: list[dict], hr_zones: list[tuple[str, int, int]]) -> list[dict]:
    """Tramos del plan con su objetivo medible: {metric pace|hr, lo, hi, quality, planned_s}. Los de RPE (fuerza) o
    sin objetivo no se miden."""
    parts = []
    for session in sessions or []:
        sw = (session or {}).get("structured_workout") or {}
        for step, times, top in _leaves(sw.get("steps") or []):
            seconds = float(step.get("duration_min") or 0) * 60 * times
            target = step.get("target") or {}
            if seconds <= 0:
                continue
            # Lo suave de un plan guardado por ritmo se mide por su FC, como se muestra (18/11).
            as_hr = easy_hr_zone(step) if top and hr_zones else None
            if as_hr:
                target = {"metric": "hr_zone", "zone": as_hr, "zone_to": as_hr}
            if target.get("metric") == "pace" and len(target.get("range") or []) == 2:
                fast, slow = sorted(float(x) for x in target["range"])
                parts.append({"metric": "pace", "lo": fast, "hi": slow, "planned_s": seconds,
                              "quality": int(target.get("zone") or 0) > EASY_ZONE_MAX})
            elif target.get("metric") == "hr_zone" and hr_zones:
                n = int(target.get("zone") or 0)
                n_to = int(target.get("zone_to") or n)
                if not 1 <= n <= len(hr_zones):
                    continue
                lo, hi = hr_zones[n - 1][1], hr_zones[min(n_to, len(hr_zones)) - 1][2]
                parts.append({"metric": "hr", "lo": float(lo), "hi": float(hi), "planned_s": seconds,
                              "quality": n > EASY_ZONE_MAX})
    return parts


def completion(parts: list[dict], dist: dict | None) -> dict | None:
    """Lo hecho de lo previsto: {pct, quality: {done_s, planned_s}, easy: {...}, easy_above_s, easy_below_s,
    easy_by_hr}; None sin datos.

    Cada segundo cuenta una sola vez. Calidad: en su zona o más rápido (más FC), de la más exigente a la menos, y
    dentro de una zona primero lo más cercano a ella. Suave: solo dentro de su zona. `easy_above_s`/`easy_below_s`:
    de lo suave que falta, lo que se hizo más intenso (más rápido o más FC) o más suave (más lento o menos FC) que su
    zona."""
    if not parts or not dist:
        return None
    remaining = {m: {float(k): int(s) for k, s in (dist.get(m) or {}).items()} for m in ("pace", "hr")}
    if not any(remaining[p["metric"]] for p in parts):
        return None

    def take(metric: str, keys: list[float], want: float) -> float:
        got = 0.0
        for k in keys:
            if got >= want:
                break
            use = min(remaining[metric][k], want - got)
            remaining[metric][k] -= use
            got += use
        return got

    # Calidad: ritmo de la zona más rápida a la más lenta (FC, de la más alta a la más baja); dentro de cada una, lo
    # más cercano a su zona primero, para no gastar en ella lo que necesita una zona más exigente.
    quality = sorted((p for p in parts if p["quality"]), key=lambda p: p["hi"] if p["metric"] == "pace" else -p["lo"])
    for p in quality:
        bins = remaining[p["metric"]]
        if p["metric"] == "pace":
            keys = sorted((k for k in bins if k <= p["hi"]), reverse=True)
        else:
            keys = sorted(k for k in bins if k >= p["lo"])
        p["done_s"] = take(p["metric"], keys, p["planned_s"])
    for p in parts:
        if p["quality"]:
            continue
        bins = remaining[p["metric"]]
        if p["metric"] == "pace":
            keys = [k for k in bins if p["lo"] <= k <= p["hi"]]
        else:
            keys = [k for k in bins if p["lo"] <= k < p["hi"]]
        p["done_s"] = take(p["metric"], keys, p["planned_s"])

    def total(sel, key):
        return sum(p[key] for p in parts if p["quality"] is sel)

    # Lo suave que falta, explicado: de lo que sobró, lo que fue más intenso que su zona (más rápido, o más FC) y lo
    # que fue más suave (más lento, o menos FC), hasta lo que falta en cada tramo y sin repetir segundos.
    above = below = 0.0
    for p in parts:
        missing = p["planned_s"] - p["done_s"]
        if p["quality"] or missing <= 0:
            continue
        bins = remaining[p["metric"]]
        if p["metric"] == "pace":
            harder = sorted((k for k in bins if k < p["lo"]), reverse=True)
            softer = sorted(k for k in bins if k > p["hi"])
        else:
            harder = sorted(k for k in bins if k >= p["hi"])
            softer = sorted((k for k in bins if k < p["lo"]), reverse=True)
        got = take(p["metric"], harder, missing)
        above += got
        below += take(p["metric"], softer, missing - got)
    planned = sum(p["planned_s"] for p in parts)
    done = sum(p["done_s"] for p in parts)
    return {"pct": round(100 * done / planned) if planned else None,
            "quality": {"done_s": round(total(True, "done_s")), "planned_s": round(total(True, "planned_s"))},
            "easy": {"done_s": round(total(False, "done_s")), "planned_s": round(total(False, "planned_s"))},
            "easy_above_s": round(above), "easy_below_s": round(below),
            "easy_by_hr": any(p["metric"] == "hr" for p in parts if not p["quality"])}
