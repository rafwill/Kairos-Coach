"""
agent/plans.py
Planes de entrenamiento: estado del plan y del objetivo, restricciones del atleta, generación del plan estructurado con el LLM, validación (ninguna semana > 10 % sobre lo sostenido) y presentación.
"""

import math
import re
from datetime import date, timedelta
from typing import Any


from agent import run_plan as _run_plan
from agent.hr_profile import _resolve_hr_zones, _resolve_running_threshold_pace_for_date
from agent.dates import (
    _format_iso_date_es,
    _safe_float,
)
from agent.daily_engine import (
    _get_planned_session_for_date,
    _get_planned_sessions_for_date,
    _session_type_label,
    _get_active_training_plan,
    _intensity_class_from_rpe,
    _normalize_intensity_class,
    _session_intensity_label,
    _structured_workout_duration_minutes,
    _zone_for_intensity_class,
)


_DAY_NAMES_ES = {1: "Lunes", 2: "Martes", 3: "Miércoles", 4: "Jueves", 5: "Viernes", 6: "Sábado", 7: "Domingo"}


def _day_slot(session: dict) -> tuple[int, int]:
    """Orden de las sesiones de una semana: día y, en un día doble (29/10), primero la principal."""
    return int(session.get("day_index") or 1), int(((session.get("structured_workout") or {}).get("slot")) or 1)


def _today_session_lines(plan: dict, profile: dict, day: date | None = None) -> list[str]:
    """La sesión (o sesiones, si es día doble) que toca hoy según el plan, para el arranque (29/10).

    Plan por bloques: título, duración, km, TSS y tramos, como en `/plan ver`; plan antiguo: tipo, duración e
    intensidad. Sin sesión hoy (antes o después del plan), lista vacía.
    """
    day = day or date.today()
    rows = _get_planned_sessions_for_date(plan, day.isoformat())
    lines: list[str] = []
    zones, _ = _resolve_hr_zones(profile or {}, "running")
    for i, row in enumerate(rows):
        if _is_block_plan(plan):
            sl = _run_plan.session_lines(row, day, zones)
            lines.append(f"- **{sl[0]}**" + (" (doble)" if i else ""))
            lines += [f"  - {x.strip()}" for x in sl[1:]]
        elif str(row.get("session_type") or "") == "rest":
            lines.append(f"- **{_DAY_NAMES_ES[day.isoweekday()]} {day.strftime('%d/%m/%Y')} — Descanso**")
        else:
            lines.append(f"- **{_DAY_NAMES_ES[day.isoweekday()]} {day.strftime('%d/%m/%Y')} — "
                         f"{_session_type_label(row.get('session_type'))}** · {int(row.get('duration_min') or 0)} min · "
                         f"{_session_intensity_label(row, profile)}")
    return lines


def _plan_week_lines(plan: dict, profile: dict, monday: date | None = None) -> list[str]:
    """Sesiones del plan de una semana de calendario (por defecto esta), una fila por día con su fecha (28/10).

    Antes "¿qué me toca esta semana?" solo decía que había un plan; el LLM, sin sesiones, contestaba que no podía
    leerlo. Un plan sin sesiones (creado antes del 28/10 solo con el formulario) lo dice y propone regenerarlo.
    """
    if not (plan or {}).get("sessions"):
        return ["", "## 🗓️ Esta semana", "El plan no tiene sesiones: créalo de nuevo con `/plan crear` para generarlas."]
    if _is_block_plan(plan) and _plan_start(plan):
        # Plan por bloques (28/10): cada día con fecha, sesión, duración, km, TSS y sus tramos.
        monday = monday or (date.today() - timedelta(days=date.today().weekday()))
        start = _plan_start(plan)
        week_index = (monday - start).days // 7 + 1
        rows = sorted([s for s in plan["sessions"] if int(s.get("week_index") or 0) == week_index], key=_day_slot)
        if not rows:
            return ["", "## 🗓️ Esta semana", "Esta semana queda fuera de las fechas del plan."]
        zones, _ = _resolve_hr_zones(profile or {}, "running")
        phases = (plan.get("plan_data") or {}).get("phases") or []
        phase = phases[week_index - 1] if week_index - 1 < len(phases) else ""
        deload = week_index in set((plan.get("plan_data") or {}).get("deload_weeks") or [])
        out = ["", "## 🗓️ Esta semana", _run_plan.week_header(week_index, start, phase, deload, rows), ""]
        for s in rows:
            day = monday + timedelta(days=int(s.get("day_index") or 1) - 1)
            sl = _run_plan.session_lines(s, day, zones)
            out.append(f"- **{sl[0]}{' (hoy)' if day == date.today() else ''}**")
            out += [f"  - {x.strip()}" for x in sl[1:]]
        return out
    monday = monday or (date.today() - timedelta(days=date.today().weekday()))
    rows = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        session = _get_planned_session_for_date(plan, day.isoformat())
        if session is None:
            continue
        kind = str(session.get("session_type") or "sesión")
        minutes = int(session.get("duration_min") or 0)
        if kind == "rest" or minutes <= 0:
            what, intensity = "Descanso", "—"
        else:
            what, intensity = f"{_session_type_label(kind)} · {minutes} min", _session_intensity_label(session, profile)
        today_mark = " (hoy)" if day == date.today() else ""
        rows.append(f"| {_DAY_NAMES_ES[day.isoweekday()]} {day.strftime('%d/%m')}{today_mark} | {what} | {intensity} |")
    if not rows:
        return ["", "## 🗓️ Esta semana", "Esta semana queda fuera de las fechas del plan."]
    return ["", "## 🗓️ Esta semana", "| Día | Sesión | Intensidad |", "|---|---|---|", *rows]


def _build_training_plan_status_markdown(profile: dict) -> str:
    """Construye respuesta clara y coherente para consultas de estado de plan."""
    plan = _get_active_training_plan(profile)
    goals = (profile or {}).get("goals", {}) if isinstance(profile, dict) else {}

    if not plan:
        lines = [
            "## 🧭 Resumen",
            "No tienes plan asignado ahora mismo.",
            "",
            "## 📊 Métricas clave",
            "| Métrica | Valor | Fuente |",
            "|---|---|---|",
            "| Plan activo | No | perfil/DB |",
        ]
        if _has_goal_in_profile(profile):
            race = goals.get("target_race") or "objetivo definido"
            race_date = _format_iso_date_es(goals.get("target_race_date")) or "fecha por definir"
            target_time = goals.get("target_time") or "tiempo por definir"
            weekly_hours = goals.get("weekly_training_hours") or "por definir"
            lines.extend([
                f"| Evento objetivo | {race} | perfil |",
                f"| Fecha objetivo | {race_date} | perfil |",
                f"| Tiempo objetivo | {target_time} | perfil |",
                f"| Horas/semana | {weekly_hours} | perfil |",
            ])
        lines.extend([
            "",
            "## ✅ Recomendación",
            "- Crear un plan activo alineado con tu objetivo guardado.",
            "",
            "## 🎯 Próximo paso",
            "- Si quieres, te preparo un plan activo a partir de ese objetivo.",
        ])
        return "\n".join(lines)

    title = str(plan.get("title") or plan.get("name") or "Plan activo").strip()
    today_focus = str(plan.get("today_focus") or plan.get("today_session") or "").strip()
    status = str(plan.get("status") or "active").strip()
    race = plan.get("target_race") or goals.get("target_race") or "objetivo definido"
    race_date = _format_iso_date_es(plan.get("target_race_date") or goals.get("target_race_date")) or "fecha por definir"

    lines = [
        "## 🧭 Resumen",
        f"Sí, tienes un plan activo: {title}.",
        "",
        "## 📊 Métricas clave",
        "| Métrica | Valor | Fuente |",
        "|---|---|---|",
        "| Plan activo | Sí | perfil/DB |",
        f"| Estado del plan | {status} | plan activo |",
        f"| Objetivo | {race} | plan/perfil |",
        f"| Fecha objetivo | {race_date} | plan/perfil |",
    ]
    if today_focus:
        lines.append(f"| Sesión sugerida hoy | {today_focus} | plan activo |")
    lines.extend(_plan_week_lines(plan, profile))
    lines.extend([
        "",
        "## ✅ Recomendación",
        "- Mantener el plan activo y ajustar la sesión según recuperación actual.",
        "",
        "## 🎯 Próximo paso",
        "- Si quieres, adapto la sesión de hoy según tu recuperación actual.",
    ])
    return "\n".join(lines)


def _has_goal_in_profile(profile: dict) -> bool:
    """Comprueba si el perfil ya contiene un objetivo útil para planificar."""
    goals = (profile or {}).get("goals", {})
    return bool(
        goals.get("target_race")
        or goals.get("target_race_date")
        or goals.get("target_time")
        or goals.get("weekly_training_hours")
    )


def _build_goal_plan_fallback(profile: dict) -> str:
    """Genera una planificación base útil usando el objetivo guardado en el perfil."""
    goals = (profile or {}).get("goals", {})
    health = (profile or {}).get("health", {})

    race = goals.get("target_race") or "tu evento objetivo"
    race_date = goals.get("target_race_date") or "fecha por confirmar"
    target_time = goals.get("target_time") or "tiempo por definir"
    weekly_hours = goals.get("weekly_training_hours") or "8-10"
    conditions = [*(health.get("pathologies") or []), *(health.get("injuries") or [])]
    injuries = ", ".join(conditions) if conditions else "ninguna relevante"
    constraints = _resolve_training_constraints(goals if isinstance(goals, dict) else {}, health if isinstance(health, dict) else {}, "")
    available = constraints.get("available_days") or [1, 2, 3, 4, 5, 6, 7]
    unavailable = constraints.get("unavailable_days") or []
    day_names = {1: "Lunes", 2: "Martes", 3: "Miércoles", 4: "Jueves", 5: "Viernes", 6: "Sábado", 7: "Domingo"}
    available_label = ", ".join(day_names.get(int(d), str(d)) for d in available)
    unavailable_label = ", ".join(day_names.get(int(d), str(d)) for d in unavailable) if unavailable else "ninguno"

    return (
        "## Planificación Inicial para tu Objetivo\n\n"
        f"- Evento objetivo: {race}\n"
        f"- Fecha objetivo: {race_date}\n"
        f"- Tiempo objetivo: {target_time}\n"
        f"- Horas semanales estimadas: {weekly_hours}\n"
        f"- Días entrenables declarados: {available_label}\n"
        f"- Días no entrenables declarados: {unavailable_label}\n"
        f"- Condiciones de salud declaradas: {injuries}\n\n"
        "### Estructura semanal propuesta (base)\n"
        "- El microciclo se distribuye automáticamente según disponibilidad diaria real.\n"
        "- Prioriza: calidad separada, tirada larga, aeróbico suave y recuperación.\n"
        "- Incluye descansos obligatorios y límites de carga por día/semana.\n\n"
        "### Próximos pasos\n"
        "- En la siguiente interacción ajustaré paces, volúmenes y progresión según tus datos Garmin recientes "
        "(carga, HRV, sueño y entrenamientos)."
    )


_DAY_TOKEN_TO_INDEX = {
    "1": 1, "lunes": 1, "lun": 1, "monday": 1, "mon": 1,
    "2": 2, "martes": 2, "mar": 2, "tuesday": 2, "tue": 2,
    "3": 3, "miercoles": 3, "miércoles": 3, "mie": 3, "mié": 3, "wednesday": 3, "wed": 3,
    "4": 4, "jueves": 4, "jue": 4, "thursday": 4, "thu": 4,
    "5": 5, "viernes": 5, "vie": 5, "friday": 5, "fri": 5,
    "6": 6, "sabado": 6, "sábado": 6, "sab": 6, "saturday": 6, "sat": 6,
    "7": 7, "domingo": 7, "dom": 7, "sunday": 7, "sun": 7,
    # Iniciales del calendario español (/plan crear, 29/10): L M X J V S D.
    "l": 1, "m": 2, "x": 3, "j": 4, "v": 5, "s": 6, "d": 7,
}
_DAY_LETTERS = {1: "L", 2: "M", 3: "X", 4: "J", 5: "V", 6: "S", 7: "D"}


def _parse_day_list(text: str) -> list[int]:
    """"L,M,X,J,V,S,D", "lunes, miércoles", "L-V" o "todos" → días 1-7 (vacío si no se entiende)."""
    t = str(text or "").strip().lower()
    if t in ("", "todos", "todos los días", "toda la semana"):
        return list(range(1, 8)) if t else []
    days: set[int] = set()
    for part in re.split(r"[,;\s]+", t):
        if "-" in part:
            a, _, b = part.partition("-")
            lo, hi = _DAY_TOKEN_TO_INDEX.get(a.strip()), _DAY_TOKEN_TO_INDEX.get(b.strip())
            if lo and hi and lo <= hi:
                days.update(range(lo, hi + 1))
        elif part in _DAY_TOKEN_TO_INDEX:
            days.add(_DAY_TOKEN_TO_INDEX[part])
    return sorted(days)


def _parse_day_limits(text: str) -> dict[int, int]:
    """"L-V 75, S 180" → {1: 75, …, 5: 75, 6: 180}: minutos máximos por día."""
    out: dict[int, int] = {}
    for part in re.split(r"[,;]+", str(text or "")):
        m = re.match(r"^\s*([a-záéíóú\-\s]+?)\s+(\d{1,3})\s*(?:min)?\s*$", part.strip().lower())
        if m:
            for d in _parse_day_list(m.group(1).replace(" ", "")):
                out[d] = int(m.group(2))
    return out


def _format_days(days: list[int]) -> str:
    return ",".join(_DAY_LETTERS[d] for d in sorted(days)) if days and len(days) < 7 else "todos"


def _parse_day_indexes(value: Any) -> set[int]:
    out: set[int] = set()
    if value is None:
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    elif isinstance(value, str):
        items = re.split(r"[,;/\\|]+", value)
    else:
        items = [value]

    for raw in items:
        token = str(raw or "").strip().lower()
        if not token:
            continue
        if token in _DAY_TOKEN_TO_INDEX:
            out.add(_DAY_TOKEN_TO_INDEX[token])
            continue
        try:
            num = int(float(token))
        except (TypeError, ValueError):
            continue
        if 1 <= num <= 7:
            out.add(num)
    return out


def _extract_max_minutes_per_day(value: Any) -> dict[int, int]:
    out: dict[int, int] = {}
    if not isinstance(value, dict):
        return out
    for key, raw_val in value.items():
        day_idxs = _parse_day_indexes(key)
        if not day_idxs:
            continue
        try:
            minutes = int(round(float(raw_val)))
        except (TypeError, ValueError):
            continue
        if minutes < 0:
            continue
        for d in day_idxs:
            out[d] = minutes
    return out


def _resolve_training_constraints(goals: dict, health: dict, user_message: str = "") -> dict:
    """Resuelve restricciones generales de planificación desde perfil + mensaje."""
    availability = goals.get("availability") if isinstance(goals.get("availability"), dict) else {}
    all_days = {1, 2, 3, 4, 5, 6, 7}

    training_days = _parse_day_indexes(
        goals.get("training_days")
        or goals.get("availability_days")
        or availability.get("training_days")
        or availability.get("available_days")
    )
    unavailable_days = _parse_day_indexes(
        goals.get("unavailable_days")
        or goals.get("rest_days")
        or availability.get("unavailable_days")
        or availability.get("rest_days")
    )

    weekend_available = availability.get("weekend_available")
    if isinstance(weekend_available, bool) and not weekend_available:
        unavailable_days.update({6, 7})

    user_lower = str(user_message or "").lower()
    if any(k in user_lower for k in ("sin fin de semana", "no fin de semana", "solo entre semana", "entre semana")):
        unavailable_days.update({6, 7})

    if training_days:
        available_days = set(training_days)
    else:
        available_days = set(all_days)
    available_days -= unavailable_days
    if not available_days:
        available_days = set(all_days - unavailable_days) or set(all_days)

    max_minutes = _extract_max_minutes_per_day(
        goals.get("max_minutes_per_day") or availability.get("max_minutes_per_day")
    )
    max_session = goals.get("max_session_minutes") or availability.get("max_session_minutes")
    try:
        max_session_int = int(round(float(max_session))) if max_session is not None else None
    except (TypeError, ValueError):
        max_session_int = None
    if max_session_int is not None and max_session_int >= 0:
        for d in available_days:
            prev = max_minutes.get(d)
            max_minutes[d] = min(prev, max_session_int) if prev is not None else max_session_int

    for d in list(available_days):
        if max_minutes.get(d) == 0:
            available_days.discard(d)
            unavailable_days.add(d)

    if not available_days:
        available_days = set(all_days - unavailable_days) or set(all_days)

    min_rest = goals.get("min_rest_days") or availability.get("min_rest_days") or 1
    try:
        min_rest_days = max(1, min(4, int(round(float(min_rest)))))
    except (TypeError, ValueError):
        min_rest_days = 1

    max_quality = goals.get("max_quality_sessions_per_week") or availability.get("max_quality_sessions_per_week") or 2
    try:
        max_quality_sessions = max(1, min(3, int(round(float(max_quality)))))
    except (TypeError, ValueError):
        max_quality_sessions = 2

    health_constraints = health.get("training_constraints") if isinstance(health.get("training_constraints"), dict) else {}
    impact_level = str(
        health_constraints.get("impact_level")
        or health.get("impact_level")
        or ""
    ).strip().lower()

    conditions = health.get("conditions") if isinstance(health.get("conditions"), list) else []
    pathologies = health.get("pathologies") if isinstance(health.get("pathologies"), list) else []
    injuries = health.get("injuries") if isinstance(health.get("injuries"), list) else []
    has_health_flags = bool(conditions or pathologies or injuries)

    if impact_level not in {"low", "moderate", "high"}:
        impact_level = "moderate" if has_health_flags else "none"

    volume_factor_map = {"none": 1.00, "low": 0.95, "moderate": 0.88, "high": 0.78}
    intensity_factor_map = {"none": 1.00, "low": 0.95, "moderate": 0.86, "high": 0.74}
    volume_factor = volume_factor_map.get(impact_level, 1.00)
    intensity_factor = intensity_factor_map.get(impact_level, 1.00)

    long_pref = _parse_day_indexes(
        goals.get("long_run_days")
        or availability.get("long_run_days")
        or availability.get("long_session_days")
    )
    if not long_pref:
        weekend_pref = [d for d in (6, 7) if d in available_days]
        if weekend_pref:
            long_pref = set(weekend_pref)
        else:
            long_pref = set(sorted(available_days, reverse=True))

    return {
        "available_days": sorted(available_days),
        "unavailable_days": sorted(all_days - available_days),
        "max_minutes_per_day": {int(k): int(v) for k, v in max_minutes.items()},
        "min_rest_days": int(min_rest_days),
        "max_quality_sessions_per_week": int(max_quality_sessions),
        "long_day_preferences": sorted(long_pref),
        "health_impact_level": impact_level,
        "volume_factor": float(volume_factor),
        "intensity_factor": float(intensity_factor),
    }


def _wants_new_plan_intent(user_message: str) -> bool:
    text = (user_message or "").strip().lower()
    if not text:
        return False
    markers = [
        "nuevo plan",
        "nuevo ciclo",
        "desde cero",
        "plan nuevo",
        "crear otro plan",
    ]
    return any(marker in text for marker in markers)


def _wants_replan_intent(user_message: str) -> bool:
    """«Replanifica el plan» (30/10, pendiente 11 d2): rehacer desde el lunes que viene hasta la carrera, no solo
    hasta el final de la semana siguiente como "ajusta el plan"."""
    text = (user_message or "").strip().lower()
    return any(m in text for m in ("replanific", "rehaz el plan", "rehacer el plan", "rehaz mi plan", "rehacer mi plan"))


def _race_distance_km(race: str) -> float | None:
    """Distancia de la carrera objetivo a partir de su nombre (para derivar el ritmo objetivo)."""
    txt = str(race or "").lower()
    if any(k in txt for k in ("media marat", "medio marat", "half", "21k", "21 k", "21,1", "21.1")):
        return 21.0975
    if any(k in txt for k in ("maratón", "maraton", "marathon", "42k", "42,2", "42.2")):
        return 42.195
    m = re.search(r"\b(\d{1,2})\s?k(?:m)?\b", txt)
    if m and int(m.group(1)) in (5, 10, 15):
        return float(m.group(1))
    return None


def _generate_structured_plan_payload(
    profile: dict,
    user_message: str,
    base_plan: dict | None = None,
    load_context: dict | None = None,
) -> tuple[dict, list[dict]]:
    """Genera un plan estructurado progresivo y sesiones listas para persistir.

    `load_context` (opcional) aporta el estado real del atleta: volumen semanal reciente
    (`recent_weekly_minutes`), sesión más larga reciente (`longest_recent_min`), CTL/ATL/TSB y
    `status` del modelo de carga. Con él, el plan parte del volumen que el atleta sostiene de verdad
    (+10 % por semana como máximo hasta las horas declaradas) y suaviza el inicio si hay fatiga.
    """
    goals = (profile or {}).get("goals", {})
    health = (profile or {}).get("health", {})
    load_context = load_context if isinstance(load_context, dict) else {}

    race = str(goals.get("target_race") or "objetivo de rendimiento").strip()
    race_date = str(goals.get("target_race_date") or "").strip()
    target_time = str(goals.get("target_time") or "").strip()
    weekly_hours = _safe_float(goals.get("weekly_training_hours"), 8.0)
    weekly_hours = min(24.0, max(3.0, weekly_hours))
    constraints = _resolve_training_constraints(
        goals if isinstance(goals, dict) else {},
        health if isinstance(health, dict) else {},
        user_message,
    )

    duration_weeks = 8
    past_race_note = ""
    if race_date:
        try:
            days_to_race = (date.fromisoformat(race_date) - date.today()).days
            if days_to_race < 0:
                # Objetivo ya pasado: no se planifica hacia él.
                past_race_note = f"El objetivo guardado ({race}, {race_date}) ya pasó; plan genérico de 8 semanas."
                race, race_date, target_time = "objetivo de rendimiento", "", ""
            else:
                duration_weeks = min(16, max(4, int(days_to_race / 7)))
        except (TypeError, ValueError):
            duration_weeks = 8

    injuries = list((health or {}).get("injuries") or [])
    has_injuries = bool(injuries)
    injuries_label = ", ".join(injuries[:2]) if injuries else ""

    user_lower = str(user_message or "").lower()
    primary = str((goals or {}).get("primary") or "running").strip().lower()
    wants_gym = any(k in user_lower for k in ("gim", "fuerza", "pesas", "strength"))
    wants_road_cycling = any(k in user_lower for k in ("carretera", "road", "ciclismo"))
    wants_mtb = any(k in user_lower for k in ("montaña", "mtb", "mountain"))
    wants_trail = ("trail" in user_lower) or ("trail" in primary)

    if not any((wants_gym, wants_road_cycling, wants_mtb, wants_trail)):
        if "trail" in primary:
            wants_trail = True
            wants_gym = True
        elif any(k in primary for k in ("tri", "multi")):
            wants_gym = True
            wants_road_cycling = True

    def _parse_target_10k_pace() -> float | None:
        """Ritmo objetivo de carrera (s/km) = tiempo objetivo / distancia de la carrera."""
        txt = str(target_time or "").strip()
        if not txt:
            return None
        m = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", txt)
        if not m:
            return None
        h, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
        total = h * 3600 + mm * 60 + ss
        distance = _race_distance_km(race)
        if not distance or total <= 0:
            return None
        return total / distance

    def _fmt_pace(sec_per_km: float) -> str:
        sec = max(0, int(round(sec_per_km)))
        return f"{sec // 60}:{sec % 60:02d} min/km"

    target_10k_pace = _parse_target_10k_pace()

    impact_level = str(constraints.get("health_impact_level") or "none")
    difficulty = "moderate"
    if impact_level in {"high", "moderate"}:
        difficulty = "easy"
    elif weekly_hours >= 10:
        difficulty = "hard"

    if impact_level == "high":
        difficulty_reason = "Dificultad reducida a 'easy' por restricciones de salud de alto impacto."
    elif impact_level == "moderate":
        difficulty_reason = "Dificultad reducida a 'easy' por restricciones de salud de impacto moderado."
    elif has_injuries:
        difficulty_reason = f"Dificultad reducida a 'easy' por lesión activa: {injuries_label}."
    elif weekly_hours >= 10:
        difficulty_reason = f"Dificultad 'hard' por disponibilidad semanal alta ({weekly_hours}h)."
    else:
        difficulty_reason = "Dificultad 'moderate' estándar."

    if injuries:
        detail = ", ".join(str(x) for x in injuries[:2] if str(x).strip())
        if detail and detail.lower() not in difficulty_reason.lower():
            difficulty_reason = f"{difficulty_reason} Contexto reportado: lesión/condición activa ({detail})."

    weekly_minutes = int(round(weekly_hours * 60))

    # Punto de partida real (load_context): volumen sostenido, sesión larga y estado de carga.
    recent_weekly_min = _safe_float(load_context.get("recent_weekly_minutes"), 0.0)
    longest_recent_min = _safe_float(load_context.get("longest_recent_min"), 0.0)
    load_status = str(load_context.get("status") or "").strip().lower()
    fatigued_start = load_status in {"overload", "fatigue_high"}
    load_notes: list[str] = []
    if recent_weekly_min > 0:
        load_notes.append(
            f"Punto de partida: {int(round(recent_weekly_min))} min/semana reales (últimas 4 semanas); "
            "el volumen sube como máximo un 10 % sobre lo ya sostenido."
        )
    if load_context.get("ctl") is not None:
        load_notes.append(
            f"Carga actual: Estado físico (CTL) {float(load_context['ctl']):.1f}, "
            f"Fatiga (ATL) {float(load_context.get('atl') or 0):.1f}, Forma (TSB) {float(load_context.get('tsb') or 0):+.1f}"
            + (f" ({load_status})." if load_status else ".")
        )
    if fatigued_start:
        load_notes.append("Inicio con fatiga: primera semana al 80 % y calidad a RPE 5-6.")
    if past_race_note:
        load_notes.append(past_race_note)

    # Carrera en asfalto con ritmo umbral conocido: plan por bloques con descarga, ritmos de Friel sobre el umbral y
    # tramos detallados (agent/run_plan.py, 28/10). Trail, bici y triatlón siguen con el motor de abajo.
    run_threshold = _resolve_running_threshold_pace_for_date(profile, date.today().isoformat())
    # Manda la carrera objetivo, no el deporte principal del perfil (un atleta de trail puede preparar una media).
    explicit_other = any(k in user_lower for k in ("trail", "montaña", "mtb", "bici", "ciclismo", "triatl"))
    road_race = _run_plan.race_distance_km(race) is not None and not any(
        k in race.lower() for k in ("trail", "ultra", "montaña", "monte"))
    runner_without_goal = not race_date and not any(k in primary for k in ("trail", "tri", "multi", "cycl", "bici"))
    if run_threshold and not explicit_other and (road_race or runner_without_goal):
        return _generate_run_plan_payload(
            profile, race, race_date, target_time, run_threshold, weekly_hours, constraints, load_context,
            difficulty, difficulty_reason, load_notes, injuries, user_message, base_plan, wants_gym,
        )

    taper_weeks = 2 if duration_weeks >= 10 else 1
    peak_weeks = max(1, int(round(duration_weeks * 0.2)))
    base_weeks = max(2, int(round(duration_weeks * 0.4)))
    build_weeks = max(1, duration_weeks - taper_weeks - peak_weeks - base_weeks)

    def _phase_for_week(wi: int) -> str:
        if wi <= base_weeks:
            return "base"
        if wi <= base_weeks + build_weeks:
            return "build"
        if wi <= base_weeks + build_weeks + peak_weeks:
            return "peak"
        return "taper"

    def _volume_multiplier(wi: int, phase: str) -> float:
        if phase == "taper":
            return 0.72 if (duration_weeks - wi) >= 1 else 0.58
        cycle = (wi - 1) % 4
        if cycle == 3:
            return 0.82
        base_mul = {"base": 0.95, "build": 1.05, "peak": 1.12}.get(phase, 1.0)
        progressive = [0.96, 1.0, 1.06][cycle]
        return base_mul * progressive

    def _split_weekly_minutes(total_min: int) -> dict[str, int]:
        block = {
            "strength": int(round(total_min * 0.12)),
            "quality_1": int(round(total_min * 0.18)),
            "easy": int(round(total_min * 0.14)),
            "quality_2": int(round(total_min * 0.16)),
            "long": int(round(total_min * 0.30)),
            "recovery": int(round(total_min * 0.10)),
        }
        for k in list(block.keys()):
            if k == "long":
                block[k] = max(70, block[k])
            elif k == "strength":
                block[k] = max(30, block[k])
            else:
                block[k] = max(25, block[k])
        return block

    def _quality_workout(phase: str, wi: int, slot: int) -> tuple[str, str, str, list[str]]:
        if target_10k_pace is not None:
            pace_mod = [10, 6, 3, 0, -2]
            p = target_10k_pace + pace_mod[min(max(wi - 1, 0), len(pace_mod) - 1)]
            pace_label = _fmt_pace(p)
        else:
            pace_label = "ritmo de trabajo por sensaciones"

        if slot == 1:
            if wants_trail:
                notes = (
                    "Calentamiento 15'. Parte principal: bloques de subida a umbral (ej. 4-6x6') con bajada técnica controlada como recuperación. "
                    "Enfriamiento 10-12'."
                )
                return ("trail_tempo", "RPE 6-8", notes, ["subida sostenida", "descenso técnico", "economía en montaña"])
            if phase == "base":
                main = ["6x400m @ controlado", "4x1000m @ umbral"][(wi + 1) % 2]
                notes = (
                    f"Calentamiento 15' + técnica. Parte principal: {main} ({pace_label}) con recuperación 200m trote. "
                    "Enfriamiento 10'."
                )
                return ("running_quality", "RPE 6-7", notes, ["series cortas", "técnica de carrera", "economía"])
            if phase == "build":
                main = ["5x1000m", "3x2000m", "8x500m"][(wi + slot) % 3]
                notes = (
                    f"Calentamiento 20'. Parte principal: {main} ({pace_label}) con recuperaciones controladas (90-150''). "
                    "Enfriamiento 10-15'."
                )
                return ("running_quality", "RPE 7-8", notes, ["series medias", "umbral", "cadencia"])
            if phase == "peak":
                main = ["4x1200m", "10x300m"][(wi + slot) % 2]
                notes = (
                    f"Calentamiento 20'. Parte principal: {main} ({pace_label}) con recuperación incompleta. "
                    "Enfriamiento 12'."
                )
                return ("running_quality", "RPE 8-9", notes, ["VO2 controlado", "economía a ritmo objetivo"])

            notes = (
                f"Calentamiento 15'. Activación pre-competición: 6x200m ({pace_label}) con recuperación completa. "
                "Enfriamiento 10'."
            )
            return ("running_quality", "RPE 6-7", notes, ["afinado neuromuscular", "recordatorio de ritmo"])

        if wants_trail:
            notes = (
                "Calentamiento 15'. Parte principal: cuestas técnicas (8-12 repeticiones de 60-90'') + bajadas con foco en cadencia. "
                "Enfriamiento 10'."
            )
            return ("trail_hills", "RPE 6-8", notes, ["subidas técnicas", "bajadas", "uso de bastones si aplica"])

        if phase == "base":
            return ("running_quality", "RPE 6-7", "Calentamiento 15'. Parte principal: fartlek 10x(1' fuerte/1' suave). Enfriamiento 10'.", ["fartlek", "control de esfuerzo"])
        if phase == "build":
            return ("running_quality", "RPE 7-8", "Calentamiento 15'. Parte principal: tempo 3x10' (RPE 7) con 3' suaves. Enfriamiento 10'.", ["tempo", "umbral sostenible"])
        if phase == "peak":
            return ("running_quality", "RPE 7-8", "Calentamiento 15'. Parte principal: fartlek piramidal 1'-2'-3'-4'-3'-2'-1'. Enfriamiento 10'.", ["fartlek avanzado", "cambio de ritmo"])
        return ("running_quality", "RPE 6-7", "Calentamiento 12'. Parte principal: 20' ritmo controlado + 4 rectas. Enfriamiento 10'.", ["tempo corto", "activación"])

    available_days = set(int(x) for x in (constraints.get("available_days") or [])) or {1, 2, 3, 4, 5, 6, 7}
    unavailable_days = set(int(x) for x in (constraints.get("unavailable_days") or []))
    day_caps = {
        int(k): int(v)
        for k, v in (constraints.get("max_minutes_per_day") or {}).items()
        if 1 <= int(k) <= 7 and int(v) >= 0
    }
    min_rest_days = max(1, min(4, int(constraints.get("min_rest_days") or 1)))
    max_quality_sessions = max(1, min(3, int(constraints.get("max_quality_sessions_per_week") or 2)))
    long_day_pref = [int(d) for d in (constraints.get("long_day_preferences") or []) if 1 <= int(d) <= 7]

    def _cap_duration(day_idx: int, duration: int) -> int:
        cap = day_caps.get(day_idx)
        if cap is None:
            return max(0, int(duration))
        return max(0, min(int(duration), int(cap)))

    def _structured_target_for_session(session_type: str, intensity_text: str) -> dict:
        st = str(session_type or "").lower()
        intensity_class = _intensity_class_from_rpe(intensity_text)

        # hr_zone = zona de Garmin (la del reloj y TP), decisión del usuario del 23/10; antes hr_pct
        # (% del LTHR, zonas de Friel), que no casaba con las de Garmin.
        hr_zone = {"metric": "hr_zone", "zone": _zone_for_intensity_class(intensity_class)}
        ftp_ranges = {
            "recovery": [45, 55],
            "endurance": [56, 75],
            "tempo": [76, 90],
            "threshold": [91, 105],
            "vo2": [106, 120],
        }

        if "cycling" in st or "bike" in st:
            return {
                "intensityClass": intensity_class,
                "primary": {"metric": "ftp_pct", "range": ftp_ranges.get(intensity_class, [56, 75])},
                "secondary": hr_zone,
            }
        if "strength" in st:
            return {
                "intensityClass": intensity_class,
                "primary": {"metric": "rpe", "range": [4, 6] if intensity_class in {"recovery", "endurance"} else [6, 8]},
            }
        return {
            "intensityClass": intensity_class,
            "primary": hr_zone,
            "secondary": {"metric": "rpe", "range": [3, 5] if intensity_class in {"recovery", "endurance"} else [6, 8]},
        }

    def _build_structured_workout(session_type: str, duration_min: int, intensity_text: str, notes_text: str) -> dict:
        dur = max(0, int(duration_min or 0))
        target = _structured_target_for_session(session_type, intensity_text)
        intensity_class = str(target.get("intensityClass") or "endurance")

        if str(session_type or "").lower() == "rest" or dur <= 0:
            return {
                "schema": "kairos-workout-v1",
                "sessionType": str(session_type or "rest"),
                "steps": [
                    {
                        "name": "Rest",
                        "type": "rest",
                        "duration_min": 0,
                        "reps": 1,
                        "intensityClass": "recovery",
                    }
                ],
                "notes": str(notes_text or ""),
            }

        warmup = max(8, min(20, int(round(dur * 0.18))))
        cooldown = max(6, min(15, int(round(dur * 0.14))))
        main_dur = max(5, dur - warmup - cooldown)
        is_quality = any(k in str(session_type or "").lower() for k in ("quality", "tempo", "hills", "fartlek", "interval"))

        if is_quality and main_dur >= 18:
            reps = max(3, min(10, int(round(main_dur / 5.0))))
            work_dur = max(2, int(round((main_dur * 0.65) / reps)))
            rec_dur = max(1, int(round((main_dur * 0.35) / reps)))
            main_step = {
                "name": "Main Intervals",
                "type": "interval_block",
                "reps": reps,
                "steps": [
                    {
                        "name": "Work",
                        "type": "work",
                        "duration_min": work_dur,
                        "intensityClass": intensity_class,
                        "target": target.get("primary"),
                    },
                    {
                        "name": "Recovery",
                        "type": "recovery",
                        "duration_min": rec_dur,
                        "intensityClass": "recovery",
                        "target": {"metric": "hr_zone", "zone": 1},
                    },
                ],
            }
        else:
            main_step = {
                "name": "Main",
                "type": "steady",
                "duration_min": main_dur,
                "reps": 1,
                "intensityClass": intensity_class,
                "target": target.get("primary"),
            }

        return {
            "schema": "kairos-workout-v1",
            "sessionType": str(session_type or "session"),
            "summary": {
                "duration_min": dur,
                "intensityClass": intensity_class,
            },
            "targets": target,
            "steps": [
                {
                    "name": "Warm-up",
                    "type": "warmup",
                    "duration_min": warmup,
                    "reps": 1,
                    "intensityClass": "endurance",
                    "target": {"metric": "hr_zone", "zone": 1, "zone_to": 2},
                },
                main_step,
                {
                    "name": "Cool-down",
                    "type": "cooldown",
                    "duration_min": cooldown,
                    "reps": 1,
                    "intensityClass": "recovery",
                    "target": {"metric": "hr_zone", "zone": 1},
                },
            ],
            "notes": str(notes_text or ""),
        }

    def _make_session(
        week_index: int,
        day_index: int,
        session_type: str,
        duration_min: int,
        intensity: str,
        exercises: list[str],
        notes: str,
    ) -> dict:
        duration_int = int(duration_min or 0)
        return {
            "week_index": week_index,
            "day_index": day_index,
            "session_type": session_type,
            "duration_min": duration_int,
            "intensity": intensity,
            "exercises": exercises,
            "notes": notes,
            "structured_workout": _build_structured_workout(session_type, duration_int, intensity, notes),
        }

    sessions: list[dict] = []
    # Máximo semanal sostenido hasta ahora (real al inicio); ninguna semana lo supera en más de un 10 %.
    sustained_max_min = recent_weekly_min if recent_weekly_min > 0 else None
    long_cap_min = longest_recent_min * 1.10 if longest_recent_min > 0 else None
    for wi in range(1, duration_weeks + 1):
        phase = _phase_for_week(wi)
        volume_mul = _volume_multiplier(wi, phase)
        week_min = int(round(weekly_minutes * volume_mul * float(constraints.get("volume_factor") or 1.0)))
        if sustained_max_min is not None:
            week_min = min(week_min, int(round(sustained_max_min * 1.10)))
            if volume_mul >= 1.0:
                sustained_max_min = max(sustained_max_min, float(week_min))
        if fatigued_start and wi == 1:
            week_min = int(round(week_min * 0.80))
        split = _split_weekly_minutes(week_min)
        if long_cap_min is not None and phase != "taper":
            split["long"] = min(split["long"], int(round(long_cap_min)))
            long_cap_min *= 1.10
        q1_type, q1_intensity, q1_notes, q1_ex = _quality_workout(phase, wi, slot=1)
        q2_type, q2_intensity, q2_notes, q2_ex = _quality_workout(phase, wi, slot=2)

        intensity_factor = float(constraints.get("intensity_factor") or 1.0)
        if fatigued_start and wi == 1:
            q1_intensity = "RPE 5-6"
            q2_intensity = "RPE 5-6"
        elif intensity_factor < 0.90:
            q1_intensity = "RPE 5-6"
            q2_intensity = "RPE 5-6"
        elif intensity_factor < 1.0:
            q1_intensity = "RPE 6-7"
            q2_intensity = "RPE 6-7"

        if wants_mtb and wi % 2 == 0:
            easy_type = "cycling_mtb"
            easy_ex = ["cadencia en subida", "tracción", "técnica en sendero"]
            easy_notes = "Calentamiento 10'. Parte principal en Z2 por terreno variable. Enfriamiento 10'."
        elif wants_road_cycling and wi % 2 == 1:
            easy_type = "cycling_z2"
            easy_ex = ["rodaje aeróbico", "cadencia 85-95 rpm", "control de potencia/RPE"]
            easy_notes = "Calentamiento 10'. Parte principal continua en Z2. Enfriamiento 10'."
        else:
            easy_type = "trail_z2" if wants_trail else "running_z2"
            easy_ex = ["rodaje continuo", "economía de carrera"]
            easy_notes = "Calentamiento 10'. Parte principal en Z2 conversacional. Enfriamiento 8-10'."

        recovery_type = "recovery"
        recovery_ex = ["rodaje suave", "movilidad", "descarga miofascial"]
        if wants_road_cycling and wi % 3 == 0:
            recovery_type = "cycling_recovery"
            recovery_ex = ["rodillo/carretera suave", "cadencia alta sin carga"]

        strength_type = "strength" if wants_gym else "strength_home"
        strength_notes = (
            "Calentamiento 10'. Parte principal de fuerza compensatoria (core, glúteo medio, sóleo, isquios). "
            "Enfriamiento 5-10'."
            if wants_gym
            else "Calentamiento 10'. Circuito funcional en casa (core, cadera, tobillo, estabilidad). Enfriamiento 5-10'."
        )

        long_type = "trail_long" if wants_trail else "long_run"
        long_notes = (
            "Calentamiento 12'. Parte principal en montaña con desnivel progresivo y control técnico en bajadas. "
            "Nutrición objetivo: 40-70 g CH/h e hidratación 500-800 ml/h. Enfriamiento 10'."
            if wants_trail
            else "Calentamiento 12'. Parte principal continua en Z2. Nutrición objetivo: 30-60 g CH/h e hidratación 500-800 ml/h. Enfriamiento 10'."
        )

        if has_injuries or float(constraints.get("volume_factor") or 1.0) < 1.0:
            split["quality_1"] = int(round(split["quality_1"] * 0.85))
            split["quality_2"] = int(round(split["quality_2"] * 0.80))
            split["long"] = int(round(split["long"] * 0.85))

        blocked_days = set(unavailable_days)
        extra_rest: set[int] = set()
        if len(blocked_days) < min_rest_days:
            for pref_day in (5, 1, 3, 7, 2, 4, 6):
                if pref_day in available_days and pref_day not in blocked_days:
                    extra_rest.add(pref_day)
                    if len(blocked_days) + len(extra_rest) >= min_rest_days:
                        break

        training_slots = sorted([d for d in available_days if d not in extra_rest])
        if not training_slots:
            training_slots = [2, 4, 6]

        if len(training_slots) >= 6:
            role_plan = ["strength", "quality_1", "easy", "quality_2", "long", "recovery"]
        elif len(training_slots) == 5:
            role_plan = ["strength", "quality_1", "easy", "quality_2", "long"]
        elif len(training_slots) == 4:
            role_plan = ["quality_1", "easy", "quality_2", "long"]
        elif len(training_slots) == 3:
            role_plan = ["quality_1", "easy", "long"]
        elif len(training_slots) == 2:
            role_plan = ["quality_1", "long"]
        else:
            role_plan = ["easy"]

        day_to_role: dict[int, str] = {}
        free_days = set(training_slots)

        if "long" in role_plan:
            long_candidates = [d for d in long_day_pref if d in free_days]
            if not long_candidates:
                long_candidates = sorted(free_days, key=lambda d: day_caps.get(d, 10_000), reverse=True)
            if long_candidates:
                day = long_candidates[0]
                day_to_role[day] = "long"
                free_days.discard(day)

        quality_roles = [r for r in role_plan if r.startswith("quality")][:max_quality_sessions]
        quality_pref = [2, 4, 3, 5, 1, 6, 7]
        prev_q_day: int | None = None
        for role_name in quality_roles:
            candidates = [d for d in quality_pref if d in free_days]
            if prev_q_day is not None:
                spaced = [d for d in candidates if abs(d - prev_q_day) >= 2]
                if spaced:
                    candidates = spaced
            if "long" in day_to_role.values():
                long_day = next((k for k, v in day_to_role.items() if v == "long"), None)
                if long_day is not None:
                    away_from_long = [d for d in candidates if abs(d - long_day) >= 2]
                    if away_from_long:
                        candidates = away_from_long
            if candidates:
                day = candidates[0]
                day_to_role[day] = role_name
                free_days.discard(day)
                prev_q_day = day

        for role_name in role_plan:
            if role_name in day_to_role.values() or role_name.startswith("quality"):
                continue
            if not free_days:
                break
            pref = [1, 3, 5, 7, 2, 4, 6]
            candidates = [d for d in pref if d in free_days]
            day = candidates[0] if candidates else sorted(free_days)[0]
            day_to_role[day] = role_name
            free_days.discard(day)

        for day_idx in range(1, 8):
            role_name = day_to_role.get(day_idx)
            if day_idx in blocked_days or day_idx in extra_rest or role_name is None:
                sessions.append(_make_session(
                    wi,
                    day_idx,
                    "rest",
                    0,
                    "RPE 1-2",
                    ["descanso activo opcional"],
                    "Día de recuperación/descanso según restricciones y disponibilidad.",
                ))
                continue

            if role_name == "strength":
                sessions.append(_make_session(
                    wi,
                    day_idx,
                    strength_type,
                    _cap_duration(day_idx, split["strength"]),
                    "RPE 4-5",
                    ["movilidad tobillo/cadera", "fuerza general", "core"],
                    strength_notes,
                ))
            elif role_name == "quality_1":
                sessions.append(_make_session(
                    wi,
                    day_idx,
                    q1_type,
                    _cap_duration(day_idx, split["quality_1"]),
                    q1_intensity,
                    q1_ex,
                    q1_notes,
                ))
            elif role_name == "quality_2":
                sessions.append(_make_session(
                    wi,
                    day_idx,
                    q2_type,
                    _cap_duration(day_idx, split["quality_2"]),
                    q2_intensity,
                    q2_ex,
                    q2_notes,
                ))
            elif role_name == "easy":
                sessions.append(_make_session(
                    wi,
                    day_idx,
                    easy_type,
                    _cap_duration(day_idx, split["easy"]),
                    "RPE 3-4",
                    easy_ex,
                    easy_notes,
                ))
            elif role_name == "long":
                sessions.append(_make_session(
                    wi,
                    day_idx,
                    long_type,
                    _cap_duration(day_idx, split["long"]),
                    "RPE 4-5" if not has_injuries else "RPE 3-4",
                    ["tirada larga progresiva", "estrategia de nutrición/hidratación"],
                    long_notes,
                ))
            else:
                sessions.append(_make_session(
                    wi,
                    day_idx,
                    recovery_type,
                    _cap_duration(day_idx, split["recovery"]),
                    "RPE 2-3",
                    recovery_ex,
                    "Recuperación activa y descarga muscular. Prioriza sueño y rehidratación.",
                ))

    objective_text = f"Preparación para {race}"
    if target_time:
        objective_text += f" con objetivo de {target_time}"

    base_description = "Plan estructurado generado por el coach a partir de objetivos y perfil del atleta."
    plan_description = f"{base_description} {difficulty_reason}" if difficulty_reason else base_description
    if load_notes:
        plan_description = f"{plan_description} {' '.join(load_notes)}"

    plan = {
        "title": f"Plan hacia {race}",
        "description": plan_description,
        "objective": objective_text,
        "difficulty": difficulty,
        "duration_weeks": duration_weeks,
        "status": "active",
        "source": "agent_structured_plan",
        "plan_data": {
            "target_race": race,
            "target_race_date": race_date,
            "target_time": target_time,
            "weekly_training_hours": weekly_hours,
            "injuries": injuries,
            "difficulty_reason": difficulty_reason,
            # Lunes de esta semana: las semanas del plan son las de calendario (antes empezaba hoy y la semana 1
            # iba de miércoles a martes; 28/10). Las sesiones de días ya pasados de esta semana quedan atrás.
            "start_date": (date.today() - timedelta(days=date.today().weekday())).isoformat(),
            "phase_weeks": {
                "base": base_weeks,
                "build": build_weeks,
                "peak": peak_weeks,
                "taper": taper_weeks,
            },
            "sports_mix": {
                "gym": wants_gym,
                "cycling_road": wants_road_cycling,
                "cycling_mtb": wants_mtb,
                "trail": wants_trail,
            },
            "constraints": constraints,
            "today_focus": "Sesión de calidad o ajuste por recuperación",
            "generation_note": (user_message or "")[:240],
            "base_plan_id": (base_plan or {}).get("id"),
            "load_context": {
                k: load_context.get(k)
                for k in ("recent_weekly_minutes", "longest_recent_min", "ctl", "atl", "tsb", "status")
                if load_context.get(k) is not None
            },
            "target_pace_sec_per_km": round(target_10k_pace, 1) if target_10k_pace else None,
        },
    }
    return plan, sessions


def _run_plan_layout(constraints: dict) -> dict:
    """Días de la semana del plan de carrera según la disponibilidad del atleta (descanso, calidad, tirada, fuerza)."""
    available = set(int(d) for d in (constraints.get("available_days") or [])) or set(range(1, 8))
    unavailable = set(range(1, 8)) - available
    long_pref = [int(d) for d in (constraints.get("long_day_preferences") or []) if int(d) in available]
    long_day = long_pref[0] if long_pref else (7 if 7 in available else max(available))
    rest_days = set(unavailable)
    for d in (5, 1, 3):
        if len(rest_days) >= max(1, int(constraints.get("min_rest_days") or 1)):
            break
        if d != long_day:
            rest_days.add(d)
    free = [d for d in range(1, 8) if d not in rest_days and d != long_day]
    quality = []
    for d in [2, 4, 3, 1, 6, 5]:
        if d in free and all(abs(d - q) >= 2 for q in quality) and abs(d - long_day) >= 2 and len(quality) < 2:
            quality.append(d)
    while len(quality) < 2 and free:
        extra = next((d for d in free if d not in quality), None)
        if extra is None:
            break
        quality.append(extra)
    quality = sorted(quality[:2]) if len(quality) >= 2 else [2, 4]
    strength = next((d for d in (1, 3, 6) if d in free and d not in quality), None)
    return {"long_day": long_day, "quality_days": tuple(quality), "rest_days": tuple(sorted(rest_days)),
            "strength_day": strength}


def _generate_run_plan_payload(profile, race, race_date, target_time, threshold, weekly_hours, constraints, load_context,
                               difficulty, difficulty_reason, load_notes, injuries, user_message, base_plan, wants_gym):
    """Plan de carrera en asfalto por bloques (agent/run_plan.py) con el formato de plan y sesiones de siempre."""
    today = date.today()
    start = today - timedelta(days=today.weekday())
    if race_date:
        race_day = date.fromisoformat(race_date)
        race_label = race
    else:
        # Sin objetivo: bloque de 8 semanas que acaba con un test de 10K en domingo.
        race_day = start + timedelta(weeks=7, days=6)
        race_label = "Test de 10K"
        target_time = ""
    layout = _run_plan_layout(constraints)
    if race_day.isoweekday() in layout["quality_days"] or race_day.isoweekday() == layout["long_day"]:
        pass  # la semana de la carrera tiene su propio reparto
    zones, _ = _resolve_hr_zones(profile, "running")
    plan_data_run, sessions = _run_plan.build_run_plan(
        race=race_label, race_date=race_day, target_time=target_time, threshold_pace=threshold, start=start,
        weekly_minutes_goal=weekly_hours * 60, recent_weekly_minutes=load_context.get("recent_weekly_minutes"),
        longest_recent_min=load_context.get("longest_recent_min"), long_day=layout["long_day"],
        quality_days=layout["quality_days"], rest_days=layout["rest_days"], strength_day=layout["strength_day"],
        hr_zones=zones,
    )
    weeks = max(s["week_index"] for s in sessions)
    objective = f"Preparación para {race_label}" + (f" con objetivo de {target_time}" if target_time else "")
    description = ("Plan por bloques con descarga (base → construcción → específico → afinamiento). Ritmos sobre el "
                   f"umbral vigente ({_run_plan.fmt_pace(threshold)}/km) y el ritmo de carrera "
                   f"({_run_plan.fmt_pace(plan_data_run['race_pace_sec_per_km'])}/km); FC de Garmin como referencia. "
                   f"{difficulty_reason} {' '.join(load_notes)}").strip()
    plan = {
        "title": f"Plan hacia {race_label}",
        "description": description,
        "objective": objective,
        "difficulty": difficulty,
        "duration_weeks": weeks,
        "status": "active",
        "source": "agent_structured_plan",
        "plan_data": {
            **plan_data_run,
            "target_race": race_label,
            "target_race_date": race_day.isoformat(),
            "target_time": target_time,
            "weekly_training_hours": weekly_hours,
            "injuries": injuries,
            "difficulty_reason": difficulty_reason,
            "start_date": start.isoformat(),
            "constraints": constraints,
            "layout": {k: list(v) if isinstance(v, tuple) else v for k, v in layout.items()},
            "generation_note": (user_message or "")[:240],
            "base_plan_id": (base_plan or {}).get("id"),
            "load_context": {k: load_context.get(k) for k in ("recent_weekly_minutes", "longest_recent_min", "ctl", "atl",
                                                               "tsb", "status") if load_context.get(k) is not None},
            "target_pace_sec_per_km": plan_data_run["race_pace_sec_per_km"],
        },
    }
    return plan, sessions


def _plan_field(plan: dict | None, key: str):
    """Un dato de plan_data: dentro de `plan_data` (plan recién generado) o arriba (plan normalizado desde la base de
    datos, `_normalize_storage_plan_row`, que funde plan_data con la fila; 29/10: el arranque no lo encontraba)."""
    plan = plan or {}
    return (plan.get("plan_data") or {}).get(key) or plan.get(key)


def _is_block_plan(plan: dict | None) -> bool:
    return str(_plan_field(plan, "method") or "") == "bloques_con_descarga"


def _plan_start(plan: dict) -> date | None:
    try:
        return date.fromisoformat(str(_plan_field(plan, "start_date") or "")[:10])
    except ValueError:
        return None


def _plan_detail_markdown(plan: dict, sessions: list[dict], profile: dict | None, weeks: list[int] | None = None) -> str:
    """Plan de carrera por semanas: tabla de todas y el detalle de los días de `weeks` (por defecto esta y la siguiente)."""
    start = _plan_start(plan) or date.today()
    pd = plan.get("plan_data") or {}
    phases = pd.get("phases") or []
    deloads = set(pd.get("deload_weeks") or [])
    by_week: dict[int, list[dict]] = {}
    for s in sessions:
        by_week.setdefault(int(s.get("week_index") or 1), []).append(s)
    current = max(1, (date.today() - start).days // 7 + 1)
    weeks = weeks or [w for w in (current, current + 1) if w in by_week]
    zones, _ = _resolve_hr_zones(profile or {}, "running")
    head = str(plan.get("objective") or "")
    if pd.get("threshold_pace_sec_per_km"):
        head += f" · ritmo umbral {_run_plan.fmt_pace(float(pd['threshold_pace_sec_per_km']))}/km"
    if pd.get("race_pace_sec_per_km"):
        head += f" · ritmo de carrera {_run_plan.fmt_pace(float(pd['race_pace_sec_per_km']))}/km"
    focus = list(pd.get("focus") or [])  # plan del LLM: enfoque de cada semana
    lines = [f"## 🗓️ {plan.get('title') or 'Plan'}", head, ""]
    if pd.get("rationale"):
        lines += [str(pd["rationale"]), ""]
    lines += ["| Semana | Fechas | Fase | Tiempo | km | TSS |" + (" Enfoque |" if focus else ""),
              "|---|---|---|---|---:|---:|" + ("---|" if focus else "")]
    names = {"base": "Base", "build": "Construcción", "specific": "Específico", "taper": "Afinamiento", "race": "Carrera"}
    for wi in sorted(by_week):
        rows = by_week[wi]
        monday = start + timedelta(weeks=wi - 1)
        summ = [((r.get("structured_workout") or {}).get("summary") or {}) for r in rows]
        phase = phases[wi - 1] if wi - 1 < len(phases) else ""
        label = names.get(phase, phase) + (" · descarga" if wi in deloads else "")
        lines.append(f"| {wi}{' (actual)' if wi == current else ''} | {monday.strftime('%d/%m')}–{(monday + timedelta(days=6)).strftime('%d/%m')} | "
                     f"{label} | {_run_plan.fmt_duration(sum(float(x.get('duration_min') or 0) for x in summ))} | "
                     f"{sum(float(x.get('distance_km') or 0) for x in summ):.0f} | {sum(float(x.get('tss') or 0) for x in summ):.0f} |"
                     + (f" {focus[wi - 1] if wi - 1 < len(focus) else ''} |" if focus else ""))
    for wi in weeks:
        rows = sorted(by_week.get(wi, []), key=_day_slot)
        if not rows:
            continue
        phase = phases[wi - 1] if wi - 1 < len(phases) else ""
        lines += ["", "### " + _run_plan.week_header(wi, start, phase, wi in deloads, rows)]
        monday = start + timedelta(weeks=wi - 1)
        for r in rows:
            day = monday + timedelta(days=int(r.get("day_index") or 1) - 1)
            sl = _run_plan.session_lines(r, day, zones)
            lines.append(f"- **{sl[0]}**")
            lines += [f"  - {x.strip()}" for x in sl[1:]]
    lines += ["", "Otra semana: `/plan ver <nº> semana <n>`; todas: `/plan ver <nº> todo`."]
    return "\n".join(lines)


def _validate_structured_plan(plan: dict, sessions: list[dict], profile: dict) -> list[str]:
    """Valida la coherencia básica del plan estructurado antes de persistir."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["Plan inválido: formato no soportado."]

    if not str(plan.get("title") or "").strip():
        errors.append("El plan no tiene título.")
    if not str(plan.get("objective") or "").strip():
        errors.append("El plan no tiene objetivo definido.")
    if int(plan.get("duration_weeks") or 0) <= 0:
        errors.append("La duración del plan debe ser mayor que 0 semanas.")
    if not sessions:
        errors.append("El plan no contiene sesiones.")

    by_week: dict[int, list[dict]] = {}
    allowed_intensity_classes = {"recovery", "endurance", "tempo", "threshold", "vo2"}
    allowed_target_metrics = {"hr_zone", "ftp_pct", "rpe", "pace_pct", "pace"}

    def _validate_structured_workout(session: dict) -> list[str]:
        local_errors: list[str] = []
        stype = str(session.get("session_type") or "").strip().lower()
        duration = max(0.0, _safe_float(session.get("duration_min"), 0.0))
        sw = session.get("structured_workout")

        if stype != "rest" and not isinstance(sw, dict):
            return ["Hay sesiones activas sin structured_workout válido."]
        if stype == "rest":
            if isinstance(sw, dict):
                rest_steps = [
                    s for s in list(sw.get("steps") or [])
                    if isinstance(s, dict) and str(s.get("type") or "").strip().lower() == "rest"
                ]
                if not rest_steps:
                    local_errors.append("Las sesiones rest deben incluir un step explícito de tipo rest.")
            return local_errors
        if not isinstance(sw, dict):
            return local_errors

        if str(sw.get("schema") or "").strip() != "kairos-workout-v1":
            local_errors.append("structured_workout.schema debe ser kairos-workout-v1.")

        steps = sw.get("steps")
        if not isinstance(steps, list) or not steps:
            local_errors.append("structured_workout debe incluir steps no vacíos.")
            return local_errors

        types = [str((s or {}).get("type") or "").strip().lower() for s in steps if isinstance(s, dict)]
        if "warmup" not in types or "cooldown" not in types:
            local_errors.append("Sesión estructurada inválida: falta warmup o cooldown.")
        else:
            warmup_idx = types.index("warmup")
            cooldown_idx = len(types) - 1 - types[::-1].index("cooldown")
            if not (warmup_idx == 0 and cooldown_idx == len(types) - 1 and warmup_idx < cooldown_idx):
                local_errors.append("Sesión estructurada inválida: orden debe ser warmup -> main -> cooldown.")

        def _validate_step(step: dict) -> None:
            if not isinstance(step, dict):
                local_errors.append("step inválido en structured_workout.")
                return
            name = str(step.get("name") or "").strip()
            st = str(step.get("type") or "").strip().lower()
            if not name or not st:
                local_errors.append("Cada step requiere name y type.")
                return

            ic = _normalize_intensity_class(step.get("intensityClass") or "")
            if ic and ic not in allowed_intensity_classes:
                local_errors.append(f"intensityClass no soportada: {ic}.")

            target = step.get("target")
            if isinstance(target, dict):
                metric = str(target.get("metric") or "").strip().lower()
                rng = target.get("range")
                if metric and metric not in allowed_target_metrics:
                    local_errors.append(f"target.metric no soportado: {metric}.")
                if metric == "hr_zone":
                    z = int(_safe_float(target.get("zone"), 0.0))
                    z_to = int(_safe_float(target.get("zone_to"), z))
                    if not (1 <= z <= 5 and z <= z_to <= 5):
                        local_errors.append("target.zone debe ser una zona de Garmin (1-5) y zone_to >= zone.")
                if rng is not None:
                    if not (isinstance(rng, list) and len(rng) == 2):
                        local_errors.append("target.range debe tener formato [min,max].")
                    else:
                        a = _safe_float(rng[0], float("nan"))
                        b = _safe_float(rng[1], float("nan"))
                        if math.isnan(a) or math.isnan(b) or a > b:
                            local_errors.append("target.range inválido (min/max).")

            nested = step.get("steps")
            if isinstance(nested, list) and nested:
                reps = int(_safe_float(step.get("reps"), 1.0))
                if reps < 1:
                    local_errors.append("Bloques anidados requieren reps >= 1.")
                for ch in nested:
                    _validate_step(ch)
                return

            duration_min = _safe_float(step.get("duration_min"), -1.0)
            if duration_min < 0:
                local_errors.append("Cada step sin anidación requiere duration_min >= 0.")
            reps = int(_safe_float(step.get("reps"), 1.0))
            if reps < 1:
                local_errors.append("Cada step requiere reps >= 1.")

        for s in steps:
            _validate_step(s)

        sw_minutes = _structured_workout_duration_minutes(sw)
        if duration > 0 and sw_minutes > 0:
            mismatch = abs(sw_minutes - duration) / max(duration, 1.0)
            if mismatch > 0.45:
                local_errors.append("Duración de structured_workout desalineada con duration_min de la sesión.")

        if any(k in stype for k in ("quality", "tempo", "hills", "interval")):
            def _count_hard_steps(nodes: list[dict]) -> int:
                count = 0
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    ic = _normalize_intensity_class(node.get("intensityClass") or "")
                    if ic in {"tempo", "threshold", "vo2"}:
                        count += 1
                    nested = node.get("steps")
                    if isinstance(nested, list) and nested:
                        count += _count_hard_steps(nested)
                return count

            hard_steps = _count_hard_steps(steps)
            if hard_steps == 0:
                local_errors.append("Sesión de calidad sin bloque principal con intensidad tempo/threshold/vo2.")

        return local_errors

    for session in sessions:
        week_idx = int(session.get("week_index") or 1)
        by_week.setdefault(week_idx, []).append(session)

        day_index = int(session.get("day_index") or 0)
        if day_index < 1 or day_index > 7:
            errors.append("Hay sesiones con día fuera de rango (1-7).")
            break

        duration = int(session.get("duration_min") or 0)
        session_type = str(session.get("session_type") or "").strip().lower()
        if session_type != "rest" and duration <= 0:
            errors.append("Hay sesiones activas con duración no válida.")
            break
        sw_errors = _validate_structured_workout(session)
        if sw_errors:
            errors.extend(sw_errors)
            break
    duration_weeks = int(plan.get("duration_weeks") or 0)
    if duration_weeks > 0 and len(by_week) < max(1, duration_weeks - 1):
        errors.append("El plan no cubre suficientes semanas para la duración definida.")

    goals = (profile or {}).get("goals", {})
    health = (profile or {}).get("health", {})
    plan_data = plan.get("plan_data") if isinstance(plan.get("plan_data"), dict) else {}
    constraints = plan_data.get("constraints") if isinstance(plan_data.get("constraints"), dict) else None
    if not isinstance(constraints, dict):
        constraints = _resolve_training_constraints(goals if isinstance(goals, dict) else {}, health if isinstance(health, dict) else {}, "")

    unavailable_days = set(int(d) for d in (constraints.get("unavailable_days") or []))
    max_minutes_per_day = {
        int(k): int(v)
        for k, v in (constraints.get("max_minutes_per_day") or {}).items()
        if 1 <= int(k) <= 7 and int(v) >= 0
    }
    min_rest_days = max(1, min(4, int(constraints.get("min_rest_days") or 1)))
    max_quality_sessions = max(1, min(3, int(constraints.get("max_quality_sessions_per_week") or 2)))

    weekly_totals: list[int] = []
    quality_signatures: set[str] = set()
    for wi in sorted(by_week.keys()):
        week_rows = by_week[wi]
        days = {int(s.get("day_index") or 0) for s in week_rows}
        if len(days) < 7:
            errors.append(f"La semana {wi} no contiene los 7 días planificados.")
            break

        rest_count = sum(1 for s in week_rows if str(s.get("session_type") or "").strip().lower() == "rest")
        if rest_count < min_rest_days:
            errors.append(f"La semana {wi} debe tener al menos {min_rest_days} día(s) de descanso.")
            break

        for s in week_rows:
            d = int(s.get("day_index") or 0)
            st = str(s.get("session_type") or "").strip().lower()
            dur = int(s.get("duration_min") or 0)
            if d in unavailable_days and st != "rest":
                errors.append(f"La semana {wi} incumple disponibilidad: día {d} debe ser descanso.")
                break
            day_cap = max_minutes_per_day.get(d)
            if day_cap is not None and dur > day_cap:
                errors.append(f"La semana {wi} excede minutos máximos en día {d}.")
                break
        if errors:
            break

        quality_days = sorted(
            int(s.get("day_index") or 0)
            for s in week_rows
            if any(k in str(s.get("session_type") or "").lower() for k in ("quality", "tempo", "hills"))
        )
        min_quality_sessions = min(2, max_quality_sessions)
        if _is_block_plan(plan) and wi == max(by_week):
            min_quality_sessions = 0  # semana de carrera: la carrera es la sesión clave
        elif _is_block_plan(plan) and wi in set(_plan_field(plan, "deload_weeks") or []):
            # descarga: una sola sesión de calidad (el LLM puede quitarla del todo)
            min_quality_sessions = 0 if (plan.get("plan_data") or {}).get("author") == "llm" else 1
        elif (plan.get("plan_data") or {}).get("author") == "llm":
            min_quality_sessions = 1  # plan del LLM: el reparto de la calidad lo decide el entrenador
        if len(quality_days) > max_quality_sessions:
            errors.append(f"La semana {wi} excede el máximo de sesiones de calidad ({max_quality_sessions}).")
            break
        if len(quality_days) < min_quality_sessions:
            errors.append(f"La semana {wi} necesita al menos {min_quality_sessions} sesión(es) de calidad específicas.")
            break
        if len(quality_days) >= 2 and abs(quality_days[0] - quality_days[1]) < 2:
            errors.append(f"La semana {wi} concentra sesiones de calidad demasiado juntas.")
            break

        week_total = sum(max(0, int(s.get("duration_min") or 0)) for s in week_rows)
        weekly_totals.append(week_total)

        for s in week_rows:
            st = str(s.get("session_type") or "").lower()
            if any(k in st for k in ("quality", "tempo", "hills")):
                quality_signatures.add(f"{st}|{str(s.get('notes') or '').strip()[:80]}")

    expected_weekly_hours = _safe_float(goals.get("weekly_training_hours"), 8.0)
    expected_weekly_min = int(max(120, expected_weekly_hours * 60))
    if weekly_totals:
        avg_week = sum(weekly_totals) / max(1, len(weekly_totals))
        if avg_week > int(expected_weekly_min * 1.35):
            errors.append("La carga semanal propuesta excede claramente las horas semanales objetivo.")

        if len(set(weekly_totals)) <= 2 and len(weekly_totals) >= 6:
            errors.append("El plan es demasiado plano: falta progresión/descarga semanal visible.")

    if len(quality_signatures) < min(4, max(2, duration_weeks // 3)):
        errors.append("Las sesiones de calidad se repiten demasiado; falta variedad específica por fases.")

    return errors


def _plan_volume(sessions: list[dict] | None) -> tuple[float, float, int]:
    """Minutos y TSS planificados del plan entero y su número de semanas (al menos 1).

    Duración y TSS del resumen de la sesión estructurada (`structured_workout.summary`); la duración, si falta,
    de `duration_min`. Sin TSS planificado (planes antiguos), 0.
    """
    minutes = tss = 0.0
    weeks: set[int] = set()
    for s in sessions or []:
        s = s or {}
        summary = (s.get("structured_workout") or {}).get("summary") or {}
        minutes += _safe_float(summary.get("duration_min") or s.get("duration_min"), 0.0)
        tss += _safe_float(summary.get("tss"), 0.0)
        weeks.add(int(s.get("week_index") or 1))
    return minutes, tss, max(1, len(weeks))


def _fmt_hours(minutes: float) -> str:
    """Minutos en "h:mm h" (5159 → "85:59 h")."""
    total = int(round(minutes))
    return f"{total // 60}:{total % 60:02d} h"


def _summarize_plan_changes(
    previous_plan: dict | None,
    new_plan: dict,
    previous_sessions: list[dict] | None,
    new_sessions: list[dict],
) -> str:
    """Resume diferencias entre plan previo y nuevo para trazabilidad funcional."""
    if not previous_plan:
        return "Se creó un plan nuevo y se activó como plan principal."

    changes: list[str] = []
    if (previous_plan.get("duration_weeks") or 0) != (new_plan.get("duration_weeks") or 0):
        changes.append(
            f"Duración: {previous_plan.get('duration_weeks', 0)} -> {new_plan.get('duration_weeks', 0)} semanas"
        )
    if str(previous_plan.get("difficulty") or "") != str(new_plan.get("difficulty") or ""):
        changes.append(f"Dificultad: {previous_plan.get('difficulty', 'n/d')} -> {new_plan.get('difficulty', 'n/d')}")

    prev_sessions_count = len(previous_sessions or [])
    new_sessions_count = len(new_sessions or [])
    if prev_sessions_count != new_sessions_count:
        changes.append(f"Sesiones del plan: {prev_sessions_count} -> {new_sessions_count}")

    # Antes (hasta el 30/10) sumaba los minutos del plan entero y los llamaba "volumen semanal" ("5159 -> 5181 min").
    prev_min, prev_tss, prev_weeks = _plan_volume(previous_sessions)
    new_min, new_tss, new_weeks = _plan_volume(new_sessions)
    if round(prev_min) != round(new_min):
        changes.append(f"Volumen medio por semana: {_fmt_hours(prev_min / prev_weeks)} -> {_fmt_hours(new_min / new_weeks)} "
                       f"(total del plan: {_fmt_hours(prev_min)} -> {_fmt_hours(new_min)})")
    if (prev_tss or new_tss) and round(prev_tss) != round(new_tss):
        changes.append(f"TSS medio por semana: {prev_tss / prev_weeks:.0f} -> {new_tss / new_weeks:.0f} "
                       f"(total del plan: {prev_tss:.0f} -> {new_tss:.0f})")

    if not changes:
        return "Se registró una nueva versión sin cambios estructurales relevantes."
    return "\n".join(f"- {item}" for item in changes)


def _build_structured_plan_markdown(
    plan: dict,
    sessions: list[dict],
    change_summary: str,
    profile: dict | None = None,
) -> str:
    """Construye respuesta funcional del plan con estructura accionable."""
    if _is_block_plan(plan):
        return _plan_detail_markdown(plan, sessions, profile) + "\n\n## 🎯 Próximo paso\n" + str(change_summary or "")
    title = str(plan.get("title") or "Plan de entrenamiento").strip()
    objective = str(plan.get("objective") or "Objetivo no especificado").strip()
    difficulty = str(plan.get("difficulty") or "moderate").strip()
    duration_weeks = int(plan.get("duration_weeks") or 0)
    plan_id = str(plan.get("id") or "").strip()

    day_names = {
        1: "Lunes", 2: "Martes", 3: "Miércoles", 4: "Jueves", 5: "Viernes", 6: "Sábado", 7: "Domingo"
    }

    lines = [
        "## 🧭 Resumen",
        f"Plan activo: {title}",
        f"Objetivo: {objective}",
        f"Duración: {duration_weeks} semanas · Dificultad: {difficulty}",
        "",
        "## 📊 Métricas clave",
        "| Métrica | Valor | Fuente |",
        "|---|---|---|",
        f"| Objetivo | {objective} | plan |",
        f"| Duración | {duration_weeks} semanas | plan |",
        f"| Dificultad | {difficulty} | plan |",
    ]

    if plan_id:
        lines.append(f"| ID del plan | {plan_id} | plan |")

    by_week: dict[int, list[dict]] = {}
    for s in sessions:
        wi = int((s or {}).get("week_index") or 1)
        by_week.setdefault(wi, []).append(s)

    phase_weeks = (plan.get("plan_data") or {}).get("phase_weeks") if isinstance(plan.get("plan_data"), dict) else None
    if isinstance(phase_weeks, dict):
        lines.extend([
            f"| Fase base | {int(phase_weeks.get('base') or 0)} semanas | plan_data |",
            f"| Fase construcción | {int(phase_weeks.get('build') or 0)} semanas | plan_data |",
            f"| Fase pico | {int(phase_weeks.get('peak') or 0)} semanas | plan_data |",
            f"| Fase taper | {int(phase_weeks.get('taper') or 0)} semanas | plan_data |",
        ])

    lines.extend([
        "",
        "## ✅ Recomendación",
        "- Sigue la estructura de las primeras semanas y ajusta carga según sensaciones y recuperación.",
        "- Estructura semanal (primeras 3 semanas):",
    ])

    show_weeks = sorted(by_week.keys())[:3]
    for wi in show_weeks:
        week_rows = sorted(by_week[wi], key=lambda x: int(x.get("day_index") or 1))
        week_total = sum(int((r or {}).get("duration_min") or 0) for r in week_rows)
        lines.append(f"- Semana {wi}: ~{week_total} min")
        for s in week_rows:
            day = day_names.get(int(s.get("day_index") or 0), f"Día {s.get('day_index', '?')}")
            session_type = _session_type_label(s.get("session_type"))
            duration = int(s.get("duration_min") or 0)
            intensity = _session_intensity_label(s, profile)
            lines.append(f"  - {day}: {session_type} · {duration} min · {intensity}")

    lines.extend([
        "",
        "## 🎯 Próximo paso",
        "- Cambios de versión:",
        change_summary,
        "- Usa `/plan listar` para revisar planes, `/plan ver <plan_id>` para detalle y `/plan activar <plan_id>` para cambiar el activo.",
    ])

    return "\n".join(lines)
