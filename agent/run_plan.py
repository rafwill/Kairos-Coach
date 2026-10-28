"""
agent/run_plan.py
Plan de carrera en asfalto (5K, 10K, media, maratón) por bloques con descarga (28/10, decisión del usuario).

- Ritmos: zonas de ritmo de Joe Friel (las de TrainingPeaks) sobre el ritmo umbral vigente del atleta, más el
  ritmo objetivo de la carrera (tiempo objetivo / distancia). Cada tramo lleva su rango de ritmo y, como
  referencia, la zona de FC de Garmin.
- Fases: base → construcción → específico → afinamiento (2 semanas), con una semana de descarga (−25 %) cada
  4.ª fuera del afinamiento. El volumen parte del que el atleta sostiene y no sube más de un 10 % por semana.
- Sesiones que progresan por fase. Los pasos son la fuente única: la descripción se escribe a partir de ellos
  (no pueden contradecirse) y el TSS planificado sale de ellos con la fórmula de rTSS de TrainingPeaks
  (horas × IF² × 100, IF = ritmo umbral / ritmo del tramo).

Sin dependencias del chat: lo usa `plans._generate_structured_plan_payload` para objetivos de carrera en asfalto.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

# Zonas de ritmo de Friel como múltiplos del ritmo umbral (s/km): (nombre, lento, rápido, zona de FC de Garmin,
# clase de intensidad del motor diario). Z1 no tiene límite lento en Friel; se muestra hasta 145 %.
PACE_ZONES: dict[str, tuple[str, float, float, int, str]] = {
    "Z1": ("Recuperación", 1.45, 1.29, 1, "recovery"),
    "Z2": ("Aeróbico", 1.29, 1.14, 2, "endurance"),
    "Z3": ("Tempo", 1.13, 1.06, 3, "tempo"),
    "Z4": ("Subumbral", 1.05, 0.99, 4, "threshold"),
    "Z5a": ("Supraumbral", 1.00, 0.97, 5, "threshold"),
    "Z5b": ("VO2máx", 0.96, 0.90, 5, "vo2"),
    "Z5c": ("Velocidad", 0.90, 0.84, 5, "vo2"),
}
ZONE_ORDER = ["Z1", "Z2", "Z3", "Z4", "Z5a", "Z5b", "Z5c"]
RACE_DISTANCES_KM = {"5K": 5.0, "10K": 10.0, "Media maratón": 21.0975, "Maratón": 42.195}
# Sin tiempo objetivo: ritmo de carrera aproximado respecto al umbral (5K más rápido, maratón más lento).
_RACE_PACE_FROM_THRESHOLD = {5.0: 0.95, 10.0: 0.99, 21.0975: 1.02, 42.195: 1.07}
_DAY_NAMES = {1: "Lunes", 2: "Martes", 3: "Miércoles", 4: "Jueves", 5: "Viernes", 6: "Sábado", 7: "Domingo"}


def race_distance_km(race: str) -> float | None:
    """Distancia de la carrera objetivo por su nombre (media, maratón, 5K, 10K…)."""
    txt = str(race or "").lower()
    if any(k in txt for k in ("media marat", "medio marat", "half", "21k", "21 k", "21,1", "21.1")):
        return 21.0975
    if any(k in txt for k in ("maratón", "maraton", "marathon", "42k", "42,2", "42.2")):
        return 42.195
    m = re.search(r"\b(\d{1,2})\s?k(?:m)?\b", txt)
    if m and int(m.group(1)) in (5, 10, 15):
        return float(m.group(1))
    return None


def parse_target_time(text: str) -> int | None:
    """Tiempo objetivo "h:mm:ss" o "mm:ss" en segundos."""
    m = re.match(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m:
        return None
    h, mm, ss = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    total = h * 3600 + mm * 60 + ss
    return total if total > 0 else None


def fmt_pace(sec_per_km: float) -> str:
    sec = int(round(sec_per_km))
    return f"{sec // 60}:{sec % 60:02d}"


def fmt_duration(minutes: float) -> str:
    total = int(round(minutes * 60))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


# ─── Pasos ────────────────────────────────────────────────────────────────────

class Paces:
    """Rangos de ritmo (s/km, [rápido, lento]) del atleta: zonas sobre el umbral y el ritmo de carrera."""

    def __init__(self, threshold: float, race_pace: float):
        self.threshold = float(threshold)
        self.race = float(race_pace)

    def zone(self, key: str) -> tuple[float, float]:
        _, slow, fast, _, _ = PACE_ZONES[key]
        return round(self.threshold * fast), round(self.threshold * slow)

    def race_range(self) -> tuple[float, float]:
        return round(self.race - 3), round(self.race + 3)

    def zone_of(self, pace: float) -> str:
        """Zona de ritmo en la que cae un ritmo (para la FC de referencia del ritmo de carrera)."""
        ratio = pace / self.threshold
        for key in ZONE_ORDER:
            _, slow, fast, _, _ = PACE_ZONES[key]
            if fast <= ratio <= slow:
                return key
        return "Z1" if ratio > 1.29 else "Z5c"


def _target(paces: Paces, zone: str) -> dict:
    if zone == "RP":
        fast, slow = paces.race_range()
        ref = paces.zone_of(paces.race)
        return {"metric": "pace", "range": [fast, slow], "pace_zone": "RP", "zone": PACE_ZONES[ref][3],
                "intensityClass": PACE_ZONES[ref][4]}
    fast, slow = paces.zone(zone)
    return {"metric": "pace", "range": [fast, slow], "pace_zone": zone, "zone": PACE_ZONES[zone][3],
            "intensityClass": PACE_ZONES[zone][4]}


def step(paces: Paces, kind: str, zone: str, minutes: float | None = None, meters: float | None = None,
         name: str | None = None, note: str = "") -> dict:
    """Un tramo: tiempo o distancia, con su rango de ritmo. Con distancia, el tiempo se estima con el ritmo medio."""
    target = _target(paces, zone)
    mid = sum(target["range"]) / 2
    if meters is not None:
        minutes = meters / 1000 * mid / 60
    names = {"warmup": "Calentamiento", "cooldown": "Vuelta a la calma", "work": "Trabajo", "recovery": "Recuperación",
             "steady": "Continuo"}
    out = {"name": name or names.get(kind, kind), "type": kind, "duration_min": round(float(minutes or 0), 2), "reps": 1,
           "intensityClass": target["intensityClass"], "target": target}
    if meters is not None:
        out["distance_m"] = int(meters)
    if note:
        out["note"] = note
    return out


def block(reps: int, *steps: dict, name: str = "Series") -> dict:
    return {"name": name, "type": "interval_block", "reps": int(reps), "steps": list(steps)}


def _leaf_steps(steps: list[dict], times: int = 1):
    for s in steps:
        if s.get("steps"):
            yield from _leaf_steps(s["steps"], times * int(s.get("reps") or 1))
        else:
            yield s, times * int(s.get("reps") or 1)


def workout_totals(steps: list[dict], threshold: float) -> tuple[float, float, float]:
    """(minutos, km, TSS planificado). rTSS de TrainingPeaks por tramo: horas × (umbral / ritmo)² × 100."""
    minutes = km = tss = 0.0
    for s, times in _leaf_steps(steps):
        dur = float(s.get("duration_min") or 0) * times
        rng = (s.get("target") or {}).get("range") or [0, 0]
        mid = (rng[0] + rng[1]) / 2 if rng[0] else 0
        minutes += dur
        if mid:
            km += dur * 60 / mid
            tss += dur / 60 * (threshold / mid) ** 2 * 100
    return round(minutes, 1), round(km, 1), round(tss, 1)


def _step_text(s: dict, hr_zones: list | None) -> str:
    target = s.get("target") or {}
    rng = target.get("range")
    amount = f"{int(s['distance_m'])} m" if s.get("distance_m") else _fmt_minutes(float(s.get("duration_min") or 0))
    zone = target.get("pace_zone")
    zone_label = "ritmo de carrera" if zone == "RP" else zone
    hr = ""
    hz = int(target.get("zone") or 0)
    if hr_zones and 1 <= hz <= len(hr_zones):
        _, lo, hi = hr_zones[hz - 1]
        hr = f" · FC Z{hz} {lo}–{hi}"
    text = f"{amount} @ {fmt_pace(rng[0])}–{fmt_pace(rng[1])}/km ({zone_label}{hr})" if rng else amount
    if s.get("note"):
        text += f", {s['note']}"
    return text


def _fmt_minutes(minutes: float) -> str:
    if minutes < 1:
        return f"{int(round(minutes * 60))} s"
    whole = int(minutes)
    secs = int(round((minutes - whole) * 60))
    return f"{whole} min" + (f" {secs} s" if secs else "")


def describe_steps(steps: list[dict], hr_zones: list | None = None) -> list[str]:
    """Una línea por tramo, como los detalles de entrenamiento de TrainingPeaks."""
    lines = []
    for s in steps:
        if s.get("steps"):
            inner = " + ".join(_step_text(c, hr_zones) for c in s["steps"])
            lines.append(f"{s.get('name') or 'Series'}: {int(s.get('reps') or 1)} × [{inner}]")
        else:
            label = s.get("name") or ""
            lines.append(f"{label}: {_step_text(s, hr_zones)}" if label else _step_text(s, hr_zones))
    return lines


# ─── Sesiones ─────────────────────────────────────────────────────────────────

def _workout(paces: Paces, title: str, session_type: str, steps: list[dict], main_zone: str, rpe: str) -> dict:
    minutes, km, tss = workout_totals(steps, paces.threshold)
    main = _target(paces, main_zone)
    return {
        "schema": "kairos-workout-v1",
        "sessionType": session_type,
        "title": title,
        "summary": {"duration_min": minutes, "distance_km": km, "tss": tss, "intensityClass": main["intensityClass"]},
        "targets": {"intensityClass": main["intensityClass"], "primary": main, "secondary": {"metric": "rpe", "label": rpe}},
        "threshold_pace": paces.threshold,
        "steps": steps,
        "notes": " · ".join(describe_steps(steps)),
    }


def _wu(p: Paces, minutes: float = 15) -> dict:
    return step(p, "warmup", "Z2", minutes)


def _cd(p: Paces, minutes: float = 10) -> dict:
    return step(p, "cooldown", "Z1", minutes)


def easy_run(p: Paces, minutes: float, strides: bool = False) -> dict:
    core = float(max(10, round(minutes - 15 - (8 if strides else 0))))
    steps = [step(p, "warmup", "Z1", 10), step(p, "steady", "Z2", core, name="Rodaje")]
    if strides:
        steps.append(block(6, step(p, "work", "Z5c", 20 / 60, name="Recta"), step(p, "recovery", "Z1", 1), name="Rectas"))
    steps.append(_cd(p, 5))
    title = "Rodaje suave" + (" + rectas" if strides else "")
    return _workout(p, title, "running_z2", steps, "Z2", "RPE 3-4")


def recovery_run(p: Paces, minutes: float) -> dict:
    steps = [step(p, "warmup", "Z1", 5), step(p, "steady", "Z1", max(10.0, minutes - 10), name="Regenerativo"), _cd(p, 5)]
    return _workout(p, "Rodaje regenerativo", "recovery", steps, "Z1", "RPE 2-3")


def long_run(p: Paces, minutes: float, phase: str, k: int, deload: bool) -> dict:
    """Tirada larga: aeróbica en base; final progresivo en construcción; tramos a ritmo de carrera en específico."""
    minutes = float(max(45, round(minutes)))
    if deload or phase == "base" and k == 0:
        steps = [step(p, "warmup", "Z1", 10), step(p, "steady", "Z2", minutes - 15, name="Continuo"), _cd(p, 5)]
        return _workout(p, "Tirada larga aeróbica", "long_run", steps, "Z2", "RPE 3-4")
    if phase in ("base", "build"):
        fast_min = min(25, 10 + 5 * k)
        steps = [step(p, "warmup", "Z1", 10), step(p, "steady", "Z2", minutes - 15 - fast_min, name="Continuo"),
                 step(p, "work", "Z3", fast_min, name="Final progresivo"), _cd(p, 5)]
        return _workout(p, f"Tirada larga con final progresivo ({fast_min}' en Z3)", "long_run", steps, "Z2", "RPE 4-5")
    if phase == "specific":
        reps, rp_min = [(2, 12), (3, 10), (2, 15), (3, 12), (1, 30)][min(k, 4)]
        rp_total = reps * rp_min
        easy = max(20.0, minutes - 15 - rp_total - (reps * 5 if reps > 1 else 0))
        if reps == 1:
            steps = [step(p, "warmup", "Z1", 10), step(p, "steady", "Z2", easy, name="Continuo"),
                     step(p, "work", "RP", rp_min, name="Final a ritmo de carrera"), _cd(p, 5)]
        else:
            steps = [step(p, "warmup", "Z1", 10), step(p, "steady", "Z2", easy, name="Continuo"),
                     block(reps, step(p, "work", "RP", rp_min, name="Ritmo de carrera"), step(p, "recovery", "Z2", 5),
                           name="Bloques"), _cd(p, 5)]
        return _workout(p, f"Tirada larga con {reps}×{rp_min}' a ritmo de carrera", "long_run", steps, "Z2", "RPE 5-6")
    steps = [step(p, "warmup", "Z1", 10), step(p, "steady", "Z2", max(20.0, minutes - 30), name="Continuo"),
             step(p, "work", "RP", 15, name="Ritmo de carrera"), _cd(p, 5)]
    return _workout(p, "Tirada larga de afinamiento con 15' a ritmo de carrera", "long_run", steps, "Z2", "RPE 4-5")


def quality_session(p: Paces, phase: str, slot: int, k: int, deload: bool) -> dict:
    """Calidad 1 (slot 1) y 2 (slot 2) de la semana según la fase y la semana dentro de ella (k)."""
    if deload:
        if slot == 1:
            steps = [_wu(p), block(6, step(p, "work", "Z4", 1, name="Fuerte"), step(p, "recovery", "Z2", 1)), _cd(p)]
            return _workout(p, "Fartlek de descarga 6×(1'/1')", "running_quality", steps, "Z4", "RPE 6")
        return easy_run(p, 45, strides=True)
    if phase == "base":
        if slot == 1:
            if k % 2 == 0:
                reps = min(12, 8 + 2 * (k // 2))
                steps = [_wu(p, 20), block(reps, step(p, "work", "Z5b", 45 / 60, name="Cuesta", note="cuesta del 6-8 %, por esfuerzo"),
                                           step(p, "recovery", "Z1", 1.5, note="bajando al trote")), _cd(p)]
                return _workout(p, f"Cuestas cortas {reps}×45\"", "running_quality", steps, "Z5b", "RPE 8")
            reps = min(6, 3 + k // 2)
            steps = [_wu(p, 20), block(reps, step(p, "work", "Z4", 15 / 60, name="Paso 1"), step(p, "work", "Z5a", 15 / 60, name="Paso 2"),
                                       step(p, "work", "Z5b", 15 / 60, name="Paso 3"), step(p, "work", "Z5c", 15 / 60, name="Paso 4"),
                                       step(p, "recovery", "Z2", 1), name="Reducción gradual en 5 pasos"), _cd(p)]
            return _workout(p, f"Reducción gradual {reps}×(4×15\" + 1')", "running_quality", steps, "Z5b", "RPE 7-8")
        work, rec, reps = [(1, 1, 8), (2, 1, 6), (3, 1.5, 5), (4, 1.5, 4)][min(k, 3)]
        steps = [_wu(p), block(reps, step(p, "work", "Z4", work, name="Fuerte"), step(p, "recovery", "Z2", rec)), _cd(p)]
        return _workout(p, f"Fartlek {reps}×({work}'/{rec:g}')", "running_quality", steps, "Z4", "RPE 7")
    if phase == "build":
        if slot == 1:
            reps, meters, rec = [(6, 800, 2), (5, 1000, 2), (4, 1200, 2.5), (5, 1200, 2.5)][min(k, 3)]
            steps = [_wu(p, 20), block(reps, step(p, "work", "Z5b", meters=meters, name=f"{meters} m"),
                                       step(p, "recovery", "Z1", rec, note="trote")), _cd(p)]
            return _workout(p, f"Series VO2máx {reps}×{meters} m", "running_quality", steps, "Z5b", "RPE 8-9")
        reps, minutes, rec = [(3, 10, 2), (3, 12, 2), (2, 15, 3), (1, 25, 0)][min(k, 3)]
        work = step(p, "work", "Z4", minutes, name="Umbral")
        main = work if reps == 1 else block(reps, work, step(p, "recovery", "Z2", rec), name="Bloques")
        title = f"Umbral {reps}×{minutes}'" if reps > 1 else f"Umbral {minutes}' continuo"
        return _workout(p, title, "running_quality", [_wu(p), main, _cd(p)], "Z4", "RPE 7-8")
    if phase == "specific":
        if slot == 1:
            reps, meters, rec = [(3, 3000, 3), (4, 2500, 2), (2, 5000, 4), (3, 4000, 3)][min(k, 3)]
            steps = [_wu(p, 20), block(reps, step(p, "work", "RP", meters=meters, name=f"{meters / 1000:g} km"),
                                       step(p, "recovery", "Z2", rec)), _cd(p)]
            return _workout(p, f"Específico {reps}×{meters / 1000:g} km a ritmo de carrera", "running_quality", steps, "RP", "RPE 7-8")
        if k % 2 == 0:
            steps = [_wu(p, 20), block(6, step(p, "work", "Z5a", meters=1000, name="1000 m"), step(p, "recovery", "Z1", 1.5)), _cd(p)]
            return _workout(p, "Series 6×1000 m a supraumbral", "running_quality", steps, "Z5a", "RPE 8")
        steps = [_wu(p), step(p, "work", "Z4", 20, name="Umbral"), step(p, "recovery", "Z2", 5),
                 step(p, "work", "RP", 10, name="Ritmo de carrera"), _cd(p)]
        return _workout(p, "Umbral 20' + 10' a ritmo de carrera", "running_quality", steps, "Z4", "RPE 7-8")
    # Afinamiento
    if slot == 1:
        steps = [_wu(p), block(4, step(p, "work", "RP", meters=2000, name="2 km"), step(p, "recovery", "Z2", 2)), _cd(p)]
        return _workout(p, "Afinamiento 4×2 km a ritmo de carrera", "running_quality", steps, "RP", "RPE 7")
    steps = [_wu(p), block(5, step(p, "work", "Z5a", meters=1000, name="1000 m"), step(p, "recovery", "Z1", 2)), _cd(p)]
    return _workout(p, "Afinamiento 5×1000 m", "running_quality", steps, "Z5a", "RPE 7-8")


def race_week_activation(p: Paces) -> dict:
    steps = [_wu(p), block(3, step(p, "work", "RP", meters=1000, name="1 km"), step(p, "recovery", "Z2", 2)), _cd(p)]
    return _workout(p, "Activación 3×1 km a ritmo de carrera", "running_quality", steps, "RP", "RPE 6-7")


def race_session(p: Paces, race: str, distance_km: float) -> dict:
    steps = [step(p, "warmup", "Z1", 12), block(3, step(p, "work", "Z4", 20 / 60, name="Progresiva"), step(p, "recovery", "Z1", 1),
                                                   name="Activación"),
             step(p, "work", "RP", meters=distance_km * 1000, name="Carrera", note="primer km sin pasarte del ritmo"),
             step(p, "cooldown", "Z1", 10)]
    return _workout(p, f"Carrera: {race}", "race_run", steps, "RP", "RPE 9-10")


def strength_session(minutes: float = 40) -> dict:
    steps = [{"name": "Calentamiento", "type": "warmup", "duration_min": 10, "reps": 1, "intensityClass": "endurance",
              "target": {"metric": "rpe", "range": [3, 4]}},
             {"name": "Fuerza", "type": "steady", "duration_min": max(10, minutes - 15), "reps": 1, "intensityClass": "tempo",
              "target": {"metric": "rpe", "range": [6, 7]},
              "note": "sentadilla búlgara, peso muerto rumano, gemelo y sóleo, core antiextensión, 3×6-10"},
             {"name": "Movilidad", "type": "cooldown", "duration_min": 5, "reps": 1, "intensityClass": "recovery",
              "target": {"metric": "rpe", "range": [2, 3]}}]
    return {"schema": "kairos-workout-v1", "sessionType": "strength", "title": "Fuerza para corredores",
            "summary": {"duration_min": float(minutes), "distance_km": 0.0, "tss": round(minutes / 60 * 35, 1),
                        "intensityClass": "tempo"},
            "targets": {"intensityClass": "tempo", "primary": {"metric": "rpe", "range": [6, 7]}},
            "steps": steps,
            "notes": "Calentamiento 10 min · Fuerza: sentadilla búlgara, peso muerto rumano, gemelo y sóleo, core "
                     "antiextensión, 3×6-10 (RPE 6-7) · Movilidad 5 min"}


def rest_session() -> dict:
    return {"schema": "kairos-workout-v1", "sessionType": "rest", "title": "Descanso",
            "summary": {"duration_min": 0, "distance_km": 0.0, "tss": 0.0, "intensityClass": "recovery"},
            "steps": [{"name": "Descanso", "type": "rest", "duration_min": 0, "reps": 1, "intensityClass": "recovery"}],
            "notes": "Descanso"}


# ─── Plan ─────────────────────────────────────────────────────────────────────

def phases_for(weeks: int) -> list[str]:
    """Fase de cada semana: afinamiento 2 (1 si el plan es corto), específico ≈ 30 %, construcción ≈ 30 %, base el resto."""
    taper = 2 if weeks >= 8 else 1
    rest = weeks - taper
    specific = max(1, round(rest * 0.33)) if rest >= 3 else 0
    build = max(1, round(rest * 0.33)) if rest >= 2 else 0
    base = max(0, rest - specific - build)
    return ["base"] * base + ["build"] * build + ["specific"] * specific + ["taper"] * taper


def deload_weeks(phases: list[str]) -> set[int]:
    """Semana de descarga (1-based) cada 4.ª fuera del afinamiento, sin caer en la última antes de él."""
    load_weeks = [i + 1 for i, ph in enumerate(phases) if ph != "taper"]
    return {w for w in load_weeks if w % 4 == 0 and w != load_weeks[-1]}


def build_run_plan(*, race: str, race_date: date, target_time: str, threshold_pace: float, start: date,
                   weekly_minutes_goal: float, recent_weekly_minutes: float | None, longest_recent_min: float | None,
                   long_day: int = 7, quality_days: tuple[int, int] = (2, 4), rest_days: tuple[int, ...] = (5,),
                   strength_day: int | None = 1, hr_zones: list | None = None) -> tuple[dict, list[dict]]:
    """Plan desde el lunes `start` hasta la semana de la carrera. Devuelve (datos del plan, sesiones)."""
    distance = race_distance_km(race) or 10.0
    target_s = parse_target_time(target_time)
    race_pace = target_s / distance if target_s else threshold_pace * _RACE_PACE_FROM_THRESHOLD.get(distance, 1.0)
    p = Paces(threshold_pace, race_pace)

    race_monday = race_date - timedelta(days=race_date.weekday())
    weeks = max(1, min(24, (race_monday - start).days // 7 + 1))
    phases = phases_for(weeks)
    deloads = deload_weeks(phases)

    start_min = recent_weekly_minutes if recent_weekly_minutes and recent_weekly_minutes > 0 else weekly_minutes_goal * 0.85
    goal = max(start_min, weekly_minutes_goal)
    long_cap = (longest_recent_min or 75.0) * 1.10
    long_max = {5.0: 90.0, 10.0: 100.0, 21.0975: 130.0, 42.195: 180.0}.get(distance, 120.0)

    sessions: list[dict] = []
    week_minutes: list[float] = []
    # Volumen real (suma de las sesiones) de la última semana de carga y su tirada: de ahí sale la siguiente (+8 %,
    # nunca más del 10 %), la descarga (−25 %) y el afinamiento (−20 % y la tirada −25 %).
    last_load = start_min
    peak_load = start_min
    last_long = min(long_cap, long_max)
    phase_counter: dict[str, int] = {}
    for wi in range(1, weeks + 1):
        phase = phases[wi - 1]
        deload = wi in deloads
        k = phase_counter.get(phase, 0)
        if not deload:
            phase_counter[phase] = k + 1
        if phase == "taper":
            target_min = peak_load * 0.80
        elif deload:
            target_min = last_load * 0.75
        else:
            target_min = min(goal, last_load * 1.08)
        is_race_week = wi == weeks
        days: dict[int, dict] = {}

        if is_race_week:
            race_day = race_date.isoweekday()
            for d in range(1, 8):
                if d == race_day:
                    days[d] = race_session(p, race, distance)
                elif d > race_day:
                    days[d] = rest_session()
                elif d == race_day - 1:
                    days[d] = easy_run(p, 25, strides=True)
                elif d == race_day - 3 and d >= 1:
                    days[d] = race_week_activation(p)
                elif d in rest_days or d == race_day - 2:
                    days[d] = rest_session()
                else:
                    days[d] = easy_run(p, 35)
        else:
            q1 = quality_session(p, phase, 1, k, deload)
            q2 = quality_session(p, phase, 2, k, deload)
            fixed = q1["summary"]["duration_min"] + q2["summary"]["duration_min"]
            strength = strength_session() if strength_day else None
            if strength:
                fixed += strength["summary"]["duration_min"]
            if phase == "taper":
                long_min = max(60.0, last_long * 0.75)
            elif deload:
                long_min = max(60.0, last_long * 0.80)
            else:
                long_min = min(long_max, long_cap, max(60.0, target_min * 0.30))
                long_cap = min(long_max, long_cap + 10)
            easy_days = [d for d in range(1, 8) if d not in (long_day, *quality_days, *rest_days) and d != strength_day]
            remaining = max(0.0, target_min - fixed - long_min)
            per_easy = float(round(min(75.0, max(30.0, remaining / max(1, len(easy_days)))))) if easy_days else 0
            days[quality_days[0]] = q1
            days[quality_days[1]] = q2
            days[long_day] = long_run(p, long_min, phase, k, deload)
            for d in rest_days:
                days[d] = rest_session()
            if strength_day:
                days[strength_day] = strength
            for i, d in enumerate(easy_days):
                days[d] = easy_run(p, per_easy, strides=(i == len(easy_days) - 1 and phase != "taper"))

        total = 0.0
        for d in sorted(days):
            wk = days[d]
            minutes = float(wk["summary"]["duration_min"])
            total += minutes
            rpe = (wk.get("targets") or {}).get("secondary", {}).get("label") or ""
            sessions.append({
                "week_index": wi,
                "day_index": d,
                "session_type": wk["sessionType"],
                "duration_min": int(round(minutes)),
                "intensity": rpe,
                "exercises": [wk["title"]],
                "notes": wk["notes"],
                "structured_workout": wk,
            })
        week_minutes.append(round(total, 1))
        if not deload and phase != "taper" and not is_race_week:
            last_load = total
            peak_load = max(peak_load, total)
            last_long = float(days[long_day]["summary"]["duration_min"])

    plan_data = {
        "method": "bloques_con_descarga",
        "race_distance_km": distance,
        "race_pace_sec_per_km": round(race_pace, 1),
        "threshold_pace_sec_per_km": round(threshold_pace, 1),
        "phases": phases,
        "deload_weeks": sorted(deloads),
        "week_minutes": week_minutes,
        "phase_weeks": {ph: phases.count(ph) for ph in ("base", "build", "specific", "taper")},
    }
    return plan_data, sessions


def week_header(week_index: int, start: date, phase: str, deload: bool, sessions: list[dict]) -> str:
    monday = start + timedelta(weeks=week_index - 1)
    names = {"base": "Base", "build": "Construcción", "specific": "Específico", "taper": "Afinamiento"}
    minutes = sum(float(((s.get("structured_workout") or {}).get("summary") or {}).get("duration_min") or 0) for s in sessions)
    km = sum(float(((s.get("structured_workout") or {}).get("summary") or {}).get("distance_km") or 0) for s in sessions)
    tss = sum(float(((s.get("structured_workout") or {}).get("summary") or {}).get("tss") or 0) for s in sessions)
    label = names.get(phase, phase) + (" · descarga" if deload else "")
    return (f"Semana {week_index} · {monday.strftime('%d/%m')}–{(monday + timedelta(days=6)).strftime('%d/%m/%Y')} · {label} · "
            f"{fmt_duration(minutes)} · ~{km:.0f} km · {tss:.0f} TSS")


def session_lines(session: dict, day: date, hr_zones: list | None = None) -> list[str]:
    """Cabecera del día (fecha, sesión, duración, km, TSS) y sus tramos, como un día de TrainingPeaks."""
    wk = session.get("structured_workout") or {}
    summary = wk.get("summary") or {}
    title = wk.get("title") or str(session.get("session_type") or "Sesión")
    head = f"{_DAY_NAMES[day.isoweekday()]} {day.strftime('%d/%m/%Y')} — {title}"
    if (session.get("session_type") or "") == "rest":
        return [head]
    km = float(summary.get("distance_km") or 0)
    head += f" · {fmt_duration(float(summary.get('duration_min') or session.get('duration_min') or 0))}"
    if km:
        head += f" · ~{km:.1f} km"
    if summary.get("tss"):
        head += f" · {float(summary['tss']):.0f} TSS"
    steps = wk.get("steps") or []
    detail = describe_steps(steps, hr_zones) if any((s.get("target") or {}).get("metric") == "pace" for s in steps) else \
        [str(wk.get("notes") or session.get("notes") or "")]
    return [head, *[f"  {line}" for line in detail if line]]
