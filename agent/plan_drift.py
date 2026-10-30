"""
agent/plan_drift.py — ¿sigue valiendo el plan? (30/10, pendiente 11 d1).

Compara los últimos días del plan activo con lo que hizo el atleta y su HRV, sin LLM, y dice si conviene
replanificar. Criterios fijados antes de programarlo (docs/proximos_pasos.md, 11 d1). Lo hecho sale de
activity_tss (fecha, deporte y TSS), lo previsto de las sesiones del plan y la HRV de wellness_daily: sin Garmin.
No cambia el plan: el atleta decide si pide «replanifica el plan».
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

WINDOW_DAYS = 7
KEY_TYPES = {"running_quality", "long_run", "race_run"}  # calidad, tirada larga y carrera
RUN_TYPES = {"running_z2", "recovery", "long_run", "running_quality", "race_run"}
MIN_MISSED_KEY = 2
MOVED_SHARE = 0.80  # TSS de más el día de antes o de después que cuenta como sesión movida
MIN_INACTIVE_STREAK = 3
HRV_LOW_RATIO = 0.90  # el umbral del motor diario (_compute_daily_plan_adjustment)
MIN_HRV_STREAK = 3
MIN_OTHER_SPORT_DAYS = 2
LOAD_LOW, LOAD_HIGH = 0.50, 1.30
LOAD_MIN_DAYS, LOAD_MIN_PLANNED_TSS = 5, 150.0

_DAY_NAMES = {1: "lunes", 2: "martes", 3: "miércoles", 4: "jueves", 5: "viernes", 6: "sábado", 7: "domingo"}


@dataclass(frozen=True)
class Signal:
    code: str  # missed_key, inactive, hrv_low, other_sport, load_low, load_high
    text: str


def _day(d: date) -> str:
    return f"{_DAY_NAMES[d.isoweekday()]} {d.strftime('%d/%m')}"


def _planned_tss(session: dict) -> float:
    summary = (session.get("structured_workout") or {}).get("summary") or {}
    try:
        return max(0.0, float(summary.get("tss") or 0))
    except (TypeError, ValueError):
        return 0.0


def window(today: date, start: date, since: date | None) -> list[date]:
    """Días que se juzgan: de ayer hacia atrás, como mucho WINDOW_DAYS, ninguno antes de `start` ni de `since`."""
    first = max(today - timedelta(days=WINDOW_DAYS), start, since or start)
    return [first + timedelta(days=i) for i in range((today - first).days)]


def detect(sessions: list[dict], start: date, activities: list[dict], hrv_rows: list[dict], today: date,
           since: date | None = None) -> list[Signal]:
    """Señales de que el plan ya no encaja con lo que pasa de verdad.

    sessions: las del plan (week_index, day_index, session_type, structured_workout.summary.tss).
    activities: filas de activity_tss (start_date ISO, sport, tss). hrv_rows: filas de wellness_daily
    (metric_date, hrv_last_night_avg_ms, hrv_weekly_avg_ms). since: creación del plan o última revisión.
    """
    days = window(today, start, since)
    if not days:
        return []
    planned: dict[date, list[dict]] = {}
    for s in sessions or []:
        w, d = int(s.get("week_index") or 0), int(s.get("day_index") or 0)
        if w >= 1 and 1 <= d <= 7:
            planned.setdefault(start + timedelta(weeks=w - 1, days=d - 1), []).append(s)
    done: dict[date, list[dict]] = {}
    for a in activities or []:
        try:
            when = date.fromisoformat(str(a.get("start_date") or "")[:10])
        except ValueError:
            continue
        if float(a.get("tss") or 0) > 0:
            done.setdefault(when, []).append(a)

    def tss_planned(d: date) -> float:
        return sum(_planned_tss(s) for s in planned.get(d, []))

    def tss_done(d: date) -> float:
        return sum(float(a.get("tss") or 0) for a in done.get(d, []))

    def trains(d: date) -> bool:
        return any(str(s.get("session_type") or "") != "rest" for s in planned.get(d, []))

    signals: list[Signal] = []

    # Sesiones clave sin hacer (y no movidas al día de antes o al de después).
    missed = []
    for d in days:
        key = [s for s in planned.get(d, []) if str(s.get("session_type") or "") in KEY_TYPES]
        if not key or done.get(d):
            continue
        need = MOVED_SHARE * sum(_planned_tss(s) for s in key)
        moved = any(n < today and tss_done(n) - tss_planned(n) >= need > 0
                    for n in (d - timedelta(days=1), d + timedelta(days=1)))
        if not moved:
            title = (key[0].get("structured_workout") or {}).get("title") or key[0].get("session_type")
            missed.append(f"{_day(d)} ({title})")
    if len(missed) >= MIN_MISSED_KEY:
        signals.append(Signal("missed_key", f"{len(missed)} sesiones clave sin hacer: {', '.join(missed)}."))

    # Días seguidos con entrenamiento previsto y sin ninguna actividad.
    streak: list[date] = []
    longest: list[date] = []
    for d in days:
        if trains(d) and not done.get(d):
            streak.append(d)
        elif not trains(d) and not done.get(d) and streak:
            continue  # un descanso previsto no rompe ni alarga la racha
        else:
            streak = []
        if len(streak) > len(longest):
            longest = list(streak)
    if len(longest) >= MIN_INACTIVE_STREAK:
        signals.append(Signal("inactive", f"{len(longest)} días de entrenamiento seguidos sin actividad "
                                          f"(del {_day(longest[0])} al {_day(longest[-1])})."))

    # HRV de la noche por debajo del 90 % de su media semanal varios días seguidos (hasta hoy incluido).
    hrv: dict[date, tuple[float, float]] = {}
    for r in hrv_rows or []:
        try:
            when = date.fromisoformat(str(r.get("metric_date") or "")[:10])
            night, week = float(r.get("hrv_last_night_avg_ms") or 0), float(r.get("hrv_weekly_avg_ms") or 0)
        except (TypeError, ValueError):
            continue
        if night > 0 and week > 0:
            hrv[when] = (night, week)
    run, best = [], []
    for d in days + [today]:
        if d in hrv and hrv[d][0] < HRV_LOW_RATIO * hrv[d][1]:
            run.append(d)
        else:
            run = []
        if len(run) > len(best):
            best = list(run)
    if len(best) >= MIN_HRV_STREAK:
        signals.append(Signal("hrv_low", f"HRV por debajo del 90 % de su media semanal {len(best)} noches seguidas "
                                         f"(del {_day(best[0])} al {_day(best[-1])})."))

    # Carrera prevista y solo otro deporte hecho.
    other = [d for d in days
             if any(str(s.get("session_type") or "") in RUN_TYPES for s in planned.get(d, []))
             and done.get(d) and not any("running" in str(a.get("sport") or "") for a in done[d])]
    if len(other) >= MIN_OTHER_SPORT_DAYS:
        sports = sorted({str(a.get("sport")) for d in other for a in done[d]})
        signals.append(Signal("other_sport", f"{len(other)} días con carrera prevista y otro deporte hecho "
                                             f"({', '.join(sports)}): {', '.join(_day(d) for d in other)}."))

    # Carga de la ventana frente a la prevista.
    p, x = sum(tss_planned(d) for d in days), sum(tss_done(d) for d in days)
    if len(days) >= LOAD_MIN_DAYS and p >= LOAD_MIN_PLANNED_TSS:
        ratio = x / p
        if ratio < LOAD_LOW or ratio > LOAD_HIGH:
            code = "load_low" if ratio < LOAD_LOW else "load_high"
            signals.append(Signal(code, f"Carga de los últimos {len(days)} días: {x:.0f} TSS hechos frente a {p:.0f} "
                                        f"previstos ({ratio * 100:.0f} %)."))
    return signals


def markdown(signals: list[Signal]) -> list[str]:
    """Bloque del arranque con las señales y la propuesta; vacío si no hay ninguna."""
    if not signals:
        return []
    return ["", "## 🔄 Tu plan ya no encaja", *[f"- {s.text}" for s in signals],
            "- Si quieres, pídeme «replanifica el plan» y tu entrenador lo ajusta desde hoy con lo que has hecho de "
            "verdad. Hasta entonces, el plan sigue como está."]
