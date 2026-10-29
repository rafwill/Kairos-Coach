"""
agent/daily_engine.py
Motor diario: plan activo y sesión de hoy, ajuste según HRV, body battery y sueño (_compute_daily_plan_adjustment), intensidad en zonas de Garmin y marco de la sesión de hoy para el LLM.
"""

import logging
import json
import re
from datetime import date
from typing import Any


from agent import storage as _storage
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _estimate_session_tss,
    _resolve_hr_profile_values,
    _resolve_running_threshold_pace_sec_per_km,
)
from agent.prompts import (
    _render_prompt,
)
from agent.dates import (
    _safe_float,
)
from agent.tool_results import (
    _activity_id_int,
)
from agent.data_access import (
    _load_stored_activity_tss,
)
from agent.hr_profile import (
    _extract_cycling_ftp_watts,
    _resolve_hr_threshold_bpm,
    _resolve_hr_zones,
    _resolve_running_threshold_pace_for_date,
)
from agent.wellness import (
    _extract_body_battery_level,
    _extract_hrv_inputs,
    _extract_sleep_inputs,
)

log = logging.getLogger(__name__)


def _normalize_storage_plan_row(row: dict) -> dict | None:
    """Normaliza una fila de training_plan (DB) al formato usado por el agente."""
    if not isinstance(row, dict):
        return None

    plan_data = row.get("plan_data")
    merged: dict = dict(plan_data) if isinstance(plan_data, dict) else {}
    status = str(row.get("status") or merged.get("status") or "").strip().lower()

    merged.update(
        {
            "id": row.get("id") or merged.get("id"),
            "title": row.get("title") or merged.get("title") or merged.get("name") or "Plan activo",
            "description": row.get("description") or merged.get("description") or "",
            "objective": row.get("objective") or merged.get("objective") or "",
            "difficulty": row.get("difficulty") or merged.get("difficulty") or "moderate",
            "duration_weeks": row.get("duration_weeks") if row.get("duration_weeks") is not None else merged.get("duration_weeks"),
            "status": status or "active",
            "source": row.get("source") or merged.get("source") or "agent",
            "active": (status == "active"),
            # Fecha de creación: antes de ella el atleta no seguía este plan (adherencia, 29/10).
            "created_at": row.get("created_at") or merged.get("created_at"),
        }
    )
    return merged


def _get_active_training_plan(profile: dict) -> dict | None:
    """Devuelve el plan activo del atleta; prioriza DB y usa perfil como fallback."""
    try:
        db_row = _storage.get_active_training_plan()
        db_plan = _normalize_storage_plan_row(db_row)
        if db_plan:
            plan_id = db_plan.get("id")
            if plan_id:
                try:
                    sessions = _storage.list_training_plan_sessions(str(plan_id))
                    if sessions:
                        db_plan["sessions"] = sessions
                except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
                    log.debug("No se pudieron cargar sesiones de plan %s: %s", plan_id, exc)
            return db_plan
    except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
        log.warning("No se pudo leer plan activo desde DB; usando fallback local: %s", exc)

    plan = (profile or {}).get("training_plan")
    if not isinstance(plan, dict):
        return None

    status = str(plan.get("status") or "").strip().lower()
    active_flag = plan.get("active")
    is_active = bool(active_flag) if isinstance(active_flag, bool) else status in {
        "active", "assigned", "current", "in_progress"
    }
    if not is_active:
        return None
    return plan


def _build_startup_plan_recommendation(plan: dict) -> str:
    """Construye la recomendación inicial cuando existe plan activo."""
    title = str(plan.get("title") or plan.get("name") or "plan activo").strip()
    today_focus = str(plan.get("today_focus") or plan.get("today_session") or "").strip()
    if today_focus:
        return f"Tienes plan activo ({title}). Sesión sugerida hoy: {today_focus}. ¿Quieres que la ajuste con tu estado actual?"
    return f"Tienes plan activo ({title}). ¿Quieres que adapte la sesión de hoy a ese plan?"


def _resolve_today_plan_session(plan: dict) -> str:
    """Obtiene una descripción breve de la sesión prevista hoy."""
    if not isinstance(plan, dict):
        return "sesión planificada"
    today_focus = str(plan.get("today_focus") or plan.get("today_session") or "").strip()
    if today_focus:
        return today_focus
    title = str(plan.get("title") or plan.get("name") or "sesión planificada").strip()
    return title or "sesión planificada"


_SESSION_TYPE_LABELS = {
    "strength": "Fuerza (gimnasio)", "strength_home": "Fuerza en casa",
    "running_quality": "Calidad de carrera", "running_z2": "Rodaje Z2", "long_run": "Tirada larga",
    "trail_z2": "Trail suave Z2", "trail_long": "Trail largo",
    "cycling_z2": "Bici Z2", "cycling_mtb": "Bici de montaña", "cycling_recovery": "Bici de recuperación",
    "recovery": "Recuperación", "rest": "Descanso",
}


def _session_type_label(session_type: str | None) -> str:
    """Nombre en español del tipo de sesión del motor de planes (28/10: antes se mostraba el código, "long_run")."""
    code = str(session_type or "").strip()
    return _SESSION_TYPE_LABELS.get(code) or (code.replace("_", " ").capitalize() if code else "Sesión")


def _get_planned_sessions_for_date(plan: dict, target_date_iso: str) -> list[dict]:
    """Todas las sesiones de una fecha: con días dobles (29/10) hay dos; la primera es la principal."""
    first = _get_planned_session_for_date(plan, target_date_iso)
    if not first:
        return []
    same = [s for s in list(plan.get("sessions") or []) if isinstance(s, dict)
            and int(s.get("week_index") or 1) == int(first.get("week_index") or 1)
            and int(s.get("day_index") or 0) == int(first.get("day_index") or 0)]
    same.sort(key=lambda s: int(((s.get("structured_workout") or {}).get("slot")) or 1))
    return same or [first]


def _get_planned_session_for_date(plan: dict, target_date_iso: str) -> dict | None:
    """Obtiene la sesión planificada para una fecha.

    Si el plan tiene sesiones por `week_index`, calcula la semana relativa desde
    `plan_data.start_date` (o `created_at` como fallback) y selecciona primero
    esa semana; si no existe, usa la primera semana disponible como fallback.
    """
    if not isinstance(plan, dict):
        return None
    sessions = list(plan.get("sessions") or [])
    if not sessions:
        return None
    try:
        day_index = date.fromisoformat(target_date_iso).isoweekday()  # lunes=1..domingo=7
    except (TypeError, ValueError):
        return None

    target_week_index = None
    start_known = False
    try:
        plan_data = plan.get("plan_data") if isinstance(plan.get("plan_data"), dict) else plan
        start_iso = str((plan_data or {}).get("start_date") or (plan_data or {}).get("created_at") or "").strip()
        if start_iso:
            start_known = True
            start_d = date.fromisoformat(start_iso[:10])
            target_d = date.fromisoformat(target_date_iso[:10])
            delta_days = (target_d - start_d).days
            if delta_days >= 0:
                target_week_index = int(delta_days // 7) + 1
    except (TypeError, ValueError):
        target_week_index = None

    candidates: list[dict] = []
    for session in sessions:
        if not isinstance(session, dict):
            continue
        try:
            if int(session.get("day_index") or 0) == day_index:
                candidates.append(session)
        except (TypeError, ValueError):
            continue

    if not candidates:
        return None

    if target_week_index is not None:
        same_week = [
            c for c in candidates
            if int(c.get("week_index") or 1) == target_week_index
        ]
        if same_week:
            same_week.sort(key=lambda x: int(x.get("day_index") or 1))
            return same_week[0]
        # Con fecha de inicio, un día fuera de las semanas del plan no tiene sesión (28/10: antes devolvía la del
        # mismo día de la semana 1, y tras acabar el plan seguía proponiendo su primera semana).
        return None
    if start_known:
        return None  # antes del inicio del plan

    candidates.sort(key=lambda x: int(x.get("week_index") or 1))
    return candidates[0]


def _extract_activity_duration_minutes(activity: dict) -> float:
    """Normaliza duración de actividad (min) soportando claves camel/snake."""
    if not isinstance(activity, dict):
        return 0.0
    raw = (
        activity.get("duration_seconds")
        or activity.get("duration")
        or activity.get("moving_duration_seconds")
        or activity.get("movingDuration")
    )
    try:
        return max(0.0, float(raw) / 60.0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_activity_group(activity: dict) -> str:
    """Mapea una actividad real a grupo de modalidad para matching con plan."""
    if not isinstance(activity, dict):
        return "other"
    act_type = str(
        activity.get("type")
        or activity.get("activityType")
        or activity.get("activity_type")
        or activity.get("name")
        or ""
    ).strip().lower()

    if any(k in act_type for k in ("cycl", "bike", "bici")):
        return "cycling"
    if any(k in act_type for k in ("swim", "nat")):
        return "swimming"
    if any(k in act_type for k in ("strength", "gym", "weight", "fuerza", "pesas")):
        return "strength"
    if any(k in act_type for k in ("run", "trail", "carrera", "treadmill", "hike", "walk", "sender")):
        return "running"
    return "other"


def _normalize_session_group(session_type: str) -> str:
    """Mapea session_type del plan a grupo de modalidad para matching."""
    st = str(session_type or "").strip().lower()
    if st == "rest":
        return "rest"
    if "strength" in st or "fuerza" in st:
        return "strength"
    if "cycl" in st or "bike" in st or "bici" in st:
        return "cycling"
    if "swim" in st or "nat" in st:
        return "swimming"
    if any(k in st for k in ("run", "trail", "recovery", "long", "tempo", "z2", "quality", "hills")):
        return "running"
    return "other"


def _extract_rpe_mid(intensity: str | None) -> float:
    """Extrae RPE medio desde textos como 'RPE 7-8'."""
    text = str(intensity or "").strip().lower().replace(",", ".")
    m = re.search(r"rpe\s*(\d+(?:\.\d+)?)\s*(?:[-/]\s*(\d+(?:\.\d+)?))?", text)
    if not m:
        return 5.0
    low = _safe_float(m.group(1), 5.0)
    high = _safe_float(m.group(2), low)
    return max(1.0, min(10.0, (low + high) / 2.0))


def _normalize_intensity_class(value: Any) -> str:
    txt = str(value or "").strip().lower()
    aliases = {
        "z1": "recovery",
        "z2": "endurance",
        "z3": "tempo",
        "z4": "threshold",
        "z5": "vo2",
    }
    return aliases.get(txt, txt)


def _structured_workout_duration_minutes(workout: dict) -> float:
    """Suma duración total de un structured_workout en minutos."""
    if not isinstance(workout, dict):
        return 0.0

    def _sum_step(step: dict) -> float:
        if not isinstance(step, dict):
            return 0.0
        reps = max(1, int(_safe_float(step.get("reps"), 1.0)))
        nested = step.get("steps")
        if isinstance(nested, list) and nested:
            nested_total = sum(_sum_step(ch) for ch in nested)
            return reps * nested_total
        duration = max(0.0, _safe_float(step.get("duration_min"), 0.0))
        return reps * duration

    steps = workout.get("steps")
    if not isinstance(steps, list):
        return 0.0
    return round(sum(_sum_step(s) for s in steps), 1)


_INTENSITY_CLASS_ORDER = ["recovery", "endurance", "tempo", "threshold", "vo2"]


def _downgrade_intensity_class(value: str, levels: int = 1) -> str:
    current = _normalize_intensity_class(value)
    if current not in _INTENSITY_CLASS_ORDER:
        return current
    idx = _INTENSITY_CLASS_ORDER.index(current)
    return _INTENSITY_CLASS_ORDER[max(0, idx - max(1, int(levels)))]


def _estimate_tss_hour_for_intensity_class(value: str) -> float:
    ic = _normalize_intensity_class(value)
    mapping = {
        "recovery": 35.0,
        "endurance": 50.0,
        "tempo": 65.0,
        "threshold": 82.0,
        "vo2": 95.0,
    }
    return mapping.get(ic, 55.0)


def _extract_primary_intensity_class(step: dict) -> str:
    if not isinstance(step, dict):
        return "endurance"
    own = _normalize_intensity_class(step.get("intensityClass") or "")
    if own:
        return own
    nested = step.get("steps")
    if isinstance(nested, list):
        for ch in nested:
            ic = _extract_primary_intensity_class(ch)
            if ic:
                return ic
    return "endurance"


def _copy_structured_workout(workout: dict | None) -> dict | None:
    if not isinstance(workout, dict):
        return None
    try:
        return json.loads(json.dumps(workout))
    except (TypeError, ValueError, OverflowError):
        return dict(workout)


def _apply_structured_workout_adjustment(session: dict | None, decision: str) -> tuple[dict | None, list[str]]:
    """Ajusta el JSON structured_workout y devuelve trazabilidad del ajuste."""
    if not isinstance(session, dict):
        return None, []
    sw = _copy_structured_workout(session.get("structured_workout"))
    if not isinstance(sw, dict):
        return None, []

    decision_key = str(decision or "").strip().lower()
    if decision_key in {"maintain", "rest"}:
        return sw, ["sin cambios estructurales"] if decision_key == "maintain" else ["sesión convertida a descanso"]

    volume_factor = 0.70 if decision_key == "easy" else 0.80
    intensity_levels = 1
    trace: list[str] = []

    def _is_main_work_step(step_type: str) -> bool:
        st = str(step_type or "").strip().lower()
        return st not in {"warmup", "cooldown", "rest", "recovery"}

    def _adjust_step(step: dict) -> None:
        if not isinstance(step, dict):
            return
        stype = str(step.get("type") or "").strip().lower()

        if _is_main_work_step(stype):
            old_ic = _normalize_intensity_class(step.get("intensityClass") or "")
            if old_ic:
                new_ic = _downgrade_intensity_class(old_ic, levels=intensity_levels)
                if new_ic != old_ic:
                    step["intensityClass"] = new_ic
                    trace.append(f"intensidad {old_ic}->{new_ic} en {stype}")

            # La intensidad baja una zona de Garmin. Antes se multiplicaba target.range por el factor de
            # volumen (85-89 % del LTHR pasaba a 59,5-62,3 %).
            target = step.get("target")
            if isinstance(target, dict) and str(target.get("metric") or "") == "pace":
                # Plan por bloques (28/10): la zona de ritmo baja un nivel y el rango se recalcula con el umbral.
                from agent import run_plan as _run_plan

                threshold = _safe_float(sw.get("threshold_pace"), 0.0)
                key = str(target.get("pace_zone") or "")
                if key == "RP" and threshold:
                    key = _run_plan.Paces(threshold, threshold).zone_of(sum(target.get("range") or [0, 0]) / 2)
                if threshold and key in _run_plan.ZONE_ORDER:
                    lower = _run_plan.ZONE_ORDER[max(0, _run_plan.ZONE_ORDER.index(key) - intensity_levels)]
                    if lower != key:
                        new = _run_plan._target(_run_plan.Paces(threshold, threshold), lower)
                        target.update({k: new[k] for k in ("range", "pace_zone", "zone", "intensityClass")})
                        target.pop("pace_fixed", None)  # un ritmo fijado (4:00/km) pasa al rango de la zona
                        step["intensityClass"] = new["intensityClass"]
                        trace.append(f"ritmo {key}->{lower} en {stype}")
            elif isinstance(target, dict) and str(target.get("metric") or "") == "hr_zone":
                old_zone = int(_safe_float(target.get("zone"), 2.0))
                new_zone = max(1, old_zone - intensity_levels)
                if new_zone != old_zone:
                    target["zone"] = new_zone
                    if "zone_to" in target:
                        target["zone_to"] = max(new_zone, int(_safe_float(target.get("zone_to"), old_zone)) - intensity_levels)
                    trace.append(f"zona Z{old_zone}->Z{new_zone} en {stype}")

            if "duration_min" in step:
                old_dur = max(0.0, _safe_float(step.get("duration_min"), 0.0))
                if old_dur > 0:
                    new_dur = max(1.0, round(old_dur * volume_factor, 1))
                    if new_dur != old_dur:
                        step["duration_min"] = new_dur
                        trace.append(f"duración {old_dur}->{new_dur} en {stype}")

        reps = int(_safe_float(step.get("reps"), 1.0))
        if reps > 1 and (_is_main_work_step(stype) or stype == "interval_block"):
            new_reps = max(1, int(round(reps * volume_factor)))
            if new_reps != reps:
                step["reps"] = new_reps
                trace.append(f"reps {reps}->{new_reps} en {stype}")

        nested = step.get("steps")
        if isinstance(nested, list):
            for ch in nested:
                _adjust_step(ch)

    steps = sw.get("steps")
    if isinstance(steps, list):
        for item in steps:
            _adjust_step(item)

    total_after = _structured_workout_duration_minutes(sw)
    summary = sw.get("summary")
    if isinstance(summary, dict):
        summary["duration_min"] = int(round(total_after))

    if not trace:
        trace.append("sin cambios estructurales")
    return sw, trace


def _compute_structured_block_feedback(planned_session: dict, actual_duration_min: float, actual_tss: float) -> dict:
    """Compara planificado vs ejecutado por bloques del structured_workout."""
    sw = (planned_session or {}).get("structured_workout") if isinstance(planned_session, dict) else None
    if not isinstance(sw, dict):
        return {}

    steps = sw.get("steps")
    if not isinstance(steps, list) or not steps:
        return {}

    blocks: list[dict] = []
    remaining = max(0.0, float(actual_duration_min or 0.0))
    actual_tss_per_hour = (float(actual_tss or 0.0) * 60.0 / max(1.0, actual_duration_min)) if actual_duration_min > 0 else 0.0

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        planned_block_min = _structured_workout_duration_minutes({"steps": [step]})
        if planned_block_min <= 0:
            continue
        completed_min = min(planned_block_min, remaining)
        remaining = max(0.0, remaining - planned_block_min)
        completion_pct = round(completed_min / max(1.0, planned_block_min), 2)
        if completion_pct >= 0.95:
            status = "completed"
        elif completion_pct >= 0.25:
            status = "partial"
        else:
            status = "missed"

        ic = _extract_primary_intensity_class(step)
        planned_tss_hour = _estimate_tss_hour_for_intensity_class(ic)
        intensity_deviation = round(actual_tss_per_hour - planned_tss_hour, 1)

        blocks.append(
            {
                "index": idx,
                "name": str(step.get("name") or f"block_{idx}"),
                "type": str(step.get("type") or "block"),
                "planned_min": round(planned_block_min, 1),
                "completed_min": round(completed_min, 1),
                "completion_pct": completion_pct,
                "status": status,
                "planned_intensity_class": ic,
                "intensity_deviation_tss_h": intensity_deviation,
            }
        )

    if not blocks:
        return {}

    completed_blocks = sum(1 for b in blocks if b.get("status") == "completed")
    partial_blocks = sum(1 for b in blocks if b.get("status") == "partial")
    missed_blocks = sum(1 for b in blocks if b.get("status") == "missed")
    total_planned = sum(float(b.get("planned_min") or 0.0) for b in blocks)
    total_done = sum(float(b.get("completed_min") or 0.0) for b in blocks)
    time_deviation_pct = round((total_done - total_planned) / max(1.0, total_planned), 2)

    return {
        "blocks": blocks,
        "summary": {
            "completed_blocks": completed_blocks,
            "partial_blocks": partial_blocks,
            "missed_blocks": missed_blocks,
            "total_blocks": len(blocks),
            "time_deviation_pct": time_deviation_pct,
        },
    }


def _intensity_class_from_rpe(intensity_text: str) -> str:
    """Clase de intensidad del motor de planes a partir del texto "RPE x-y" de una sesión."""
    txt = str(intensity_text or "").lower()
    m = re.search(r"rpe\s*(\d+)(?:\s*[-–]\s*(\d+))?", txt)
    if not m:
        return "endurance"
    lo = int(m.group(1))
    hi = int(m.group(2) or lo)
    rpe_avg = (lo + hi) / 2.0
    if rpe_avg <= 2.5:
        return "recovery"
    if rpe_avg <= 4.5:
        return "endurance"
    if rpe_avg <= 6.5:
        return "tempo"
    if rpe_avg <= 8.0:
        return "threshold"
    return "vo2"


# Zona de Garmin de cada clase de intensidad del motor de planes (decisión del usuario, 23/10: la FC en
# zonas de Garmin es la referencia principal y el RPE el complemento; antes, % del LTHR de Friel).
_INTENSITY_CLASS_ZONE = {"recovery": 1, "endurance": 2, "tempo": 3, "threshold": 4, "vo2": 5}


_GARMIN_ZONE_NAMES = {1: "Z1 Recuperación", 2: "Z2 Aeróbica", 3: "Z3 Tempo", 4: "Z4 Subumbral", 5: "Z5 Supraumbral"}


def _zone_for_intensity_class(intensity_class: str) -> int:
    return _INTENSITY_CLASS_ZONE.get(_normalize_intensity_class(intensity_class or "") or "endurance", 2)


def _session_intensity_class(session: dict) -> str:
    """Clase de intensidad de una sesión: la de su structured_workout o, si no la tiene, la de su RPE."""
    sw = (session or {}).get("structured_workout") or {}
    ic = ((sw.get("summary") or {}).get("intensityClass") or (sw.get("targets") or {}).get("intensityClass")) if isinstance(sw, dict) else ""
    return _normalize_intensity_class(ic or "") or _intensity_class_from_rpe(str((session or {}).get("intensity") or ""))


def _session_intensity_label(session: dict, profile: dict | None) -> str:
    """Intensidad de una sesión para mostrar: zona de Garmin (nombre y lpm) y el RPE del plan como complemento.

    Se calcula al mostrar, así vale para los planes ya guardados (con RPE y % del LTHR) y sigue a las zonas
    actuales de Garmin. En fuerza manda el RPE; el descanso se queda como está.
    """
    stype = str((session or {}).get("session_type") or "").lower()
    rpe = str((session or {}).get("intensity") or "").strip()
    if stype == "rest" or int(_safe_float((session or {}).get("duration_min"), 0.0)) <= 0:
        return "descanso"
    if "strength" in stype:
        return rpe or "RPE n/d"
    zone = _zone_for_intensity_class(_session_intensity_class(session))
    sport = "cycling" if ("cycling" in stype or "bike" in stype) else "running"
    zones, _ = _resolve_hr_zones(profile or {}, sport)
    label = _GARMIN_ZONE_NAMES[zone]
    if zones and len(zones) >= zone:
        name, lo, hi = zones[zone - 1]
        label = f"{name} ({lo}–{hi} lpm)"
    return f"{label} · {rpe}" if rpe else label


_DECISION_LABELS = {
    "maintain": "mantener la sesión prevista",
    "reduce": "reducir (sin bloques intensos)",
    "easy": "sesión suave",
    "rest": "descanso",
}


def _build_today_session_frame(daily_decision: dict | None, profile: dict | None) -> str:
    """Marco de la sesión de hoy (docs/Harness.md, G12): lo decide el motor diario y el LLM solo lo redacta.

    Tipo de sesión, zonas de Garmin permitidas (con lpm) y duración máxima. Vacío si no hay decisión.
    """
    decision = str((daily_decision or {}).get("decision") or "").strip().lower()
    if decision not in _DECISION_LABELS:
        return ""
    zones, _ = _resolve_hr_zones(profile or {}, "running")

    def lpm(n: int, end: int | None = None) -> str:
        if len(zones) < max(n, end or n):
            return ""
        return f" ({zones[n - 1][1]}–{zones[(end or n) - 1][2]} lpm)"

    def top(n: int) -> str:
        return f"{zones[n - 1][2]} lpm" if len(zones) >= n else f"el techo de Z{n}"

    resulting = str((daily_decision or {}).get("resulting_session") or "").strip()
    adjusted = (daily_decision or {}).get("adjusted_structured_workout")
    if decision == "rest":
        allowed = "ninguna: descanso; si te mueves, movilidad suave en Z1" + lpm(1)
    elif decision == "easy":
        allowed = f"Z1–Z2{lpm(1, 2)}, techo {top(2)}; nada de Z3 o superior"
    elif decision == "reduce":
        allowed = f"hasta Z3{lpm(1, 3)}, techo {top(3)}; sin bloques en Z4–Z5"
    else:
        main_ic = ""
        if isinstance(adjusted, dict):
            main_ic = str((adjusted.get("summary") or {}).get("intensityClass") or "")
        if main_ic:
            zone = _zone_for_intensity_class(main_ic)
            allowed = f"las de la sesión prevista: parte principal en {_GARMIN_ZONE_NAMES[zone]}{lpm(zone)}, calentamiento en Z1–Z2 y enfriamiento en Z1"
        else:
            allowed = f"parte principal hasta Z4{lpm(4)}, calentamiento en Z1–Z2 y enfriamiento en Z1"

    minutes = _structured_workout_duration_minutes(adjusted) if isinstance(adjusted, dict) and decision != "rest" else 0.0
    if minutes > 0:
        duration = f"{int(round(minutes))} min"
    else:
        spans = [int(hi) for _, hi in re.findall(r"(\d+)\s*-\s*(\d+)\s*min", resulting)]
        duration = f"{max(spans)} min" if spans else "la de la sesión prevista"
    return _render_prompt(
        "coaching/session_frame",
        decision=_DECISION_LABELS[decision],
        sesion=resulting or "—",
        zonas=allowed,
        duracion=duration,
    )


def _structured_workout_summary(session: dict) -> str:
    """Devuelve un resumen corto del bloque principal para utilidad diaria."""
    if not isinstance(session, dict):
        return ""
    sw = session.get("structured_workout")
    if not isinstance(sw, dict):
        return ""
    steps = sw.get("steps")
    if not isinstance(steps, list) or not steps:
        return ""

    main_step = None
    for step in steps:
        st = str((step or {}).get("type") or "").strip().lower()
        if st not in {"warmup", "cooldown", "rest"}:
            main_step = step
            break
    if not isinstance(main_step, dict):
        return ""

    stype = str(main_step.get("type") or "").strip().lower()
    # Al usuario, la zona de Garmin ("Z2 Aeróbica"), no la clase interna ("endurance").
    intensity = _GARMIN_ZONE_NAMES[_zone_for_intensity_class(main_step.get("intensityClass") or "")]

    if stype == "interval_block":
        reps = max(1, int(_safe_float(main_step.get("reps"), 1.0)))
        work_min = 0.0
        rec_min = 0.0
        for ch in list(main_step.get("steps") or []):
            ch_type = str((ch or {}).get("type") or "").strip().lower()
            dur = max(0.0, _safe_float((ch or {}).get("duration_min"), 0.0))
            if ch_type == "work":
                work_min = dur
            elif ch_type == "recovery":
                rec_min = dur
        if work_min > 0 and rec_min > 0:
            return f"bloque principal: {reps}x{int(round(work_min))}' + {int(round(rec_min))}' rec ({intensity})"
        if work_min > 0:
            return f"bloque principal: {reps}x{int(round(work_min))}' ({intensity})"
        return f"bloque principal: intervalos x{reps} ({intensity})"

    dur = max(0.0, _safe_float(main_step.get("duration_min"), 0.0))
    if dur > 0:
        return f"bloque principal: {int(round(dur))}' ({intensity})"
    return ""


def _build_adjusted_structured_session_text(planned_session: str, planned_row: dict | None, decision: str) -> str:
    """Construye mensaje accionable de sesión resultante usando structured_workout."""
    base = str(planned_session or "sesión planificada").strip() or "sesión planificada"
    if decision in {"rest", "maintain"} or not isinstance(planned_row, dict):
        return base

    summary = _structured_workout_summary(planned_row)
    if not summary:
        if decision == "easy":
            return f"{base} → versión suave (Z1-Z2, sin bloques intensos)"
        return f"{base} → reducir volumen 20-30% y bajar 1 zona de intensidad"

    if decision == "easy":
        return f"{base} → versión suave: {summary}; bajar 1 nivel de intensidad y priorizar continuidad"
    if decision == "reduce":
        return f"{base} → reducir 20-30%: {summary}; recorta repeticiones o minutos de bloque principal"
    return base


def _estimate_planned_session_tss(session: dict) -> float:
    """Estimación determinista de TSS planificado por sesión (cuando no hay potencia/HR real)."""
    if not isinstance(session, dict):
        return 0.0
    duration_min = max(0.0, _safe_float(session.get("duration_min"), 0.0))
    session_type = str(session.get("session_type") or "").strip().lower()
    if session_type == "rest" or duration_min <= 0:
        return 0.0

    rpe_mid = _extract_rpe_mid(str(session.get("intensity") or ""))
    # Aproximación conservadora: TSS/h ~ RPE*10 (RPE 5 => 50 TSS/h).
    tss_hour = max(30.0, min(95.0, rpe_mid * 10.0))
    return round((duration_min / 60.0) * tss_hour, 1)


def _compute_plan_execution_feedback(
    plan: dict,
    activities_for_day: list[dict],
    target_date_iso: str,
    profile: dict,
) -> dict | None:
    """Compara planificado vs ejecutado en un día y devuelve adherencia + desviación.

    Un día anterior a la creación del plan no se juzga (29/10: un plan creado hoy que empieza el lunes "tenía"
    sesiones ayer, y no hacerlas bajaba la decisión de hoy).
    """
    if str((plan or {}).get("created_at") or "")[:10] > str(target_date_iso)[:10]:
        return None
    planned = _get_planned_session_for_date(plan, target_date_iso)
    if not planned:
        return None

    planned_type = str(planned.get("session_type") or "session").strip().lower()
    planned_group = _normalize_session_group(planned_type)
    planned_duration = max(0.0, _safe_float(planned.get("duration_min"), 0.0))
    planned_tss = _estimate_planned_session_tss(planned)

    executed = [a for a in list(activities_for_day or []) if isinstance(a, dict)]
    actual_duration = round(sum(_extract_activity_duration_minutes(a) for a in executed), 1)
    if actual_duration <= 0.0 and executed and planned_duration > 0.0:
        # Algunos payloads omiten duration/duration_seconds; evitamos penalizar adherencia por dato faltante.
        actual_duration = round(planned_duration, 1)
    actual_tss_acc = 0.0
    running_threshold_pace = (
        _resolve_running_threshold_pace_for_date(profile, target_date_iso)
        or _resolve_running_threshold_pace_sec_per_km(profile)
    )
    hr_rest_bpm, hr_max_bpm = _resolve_hr_profile_values(profile)
    hr_threshold_bpm, _, _ = _resolve_hr_threshold_bpm(profile)
    cycling_ftp = _extract_cycling_ftp_watts(profile)
    stored_tss = _load_stored_activity_tss(target_date_iso) if executed else {}
    for a in executed:
        stored_row = stored_tss.get(_activity_id_int(a))
        if stored_row is not None:
            actual_tss_acc += max(0.0, float(stored_row.get("tss") or 0.0))
            continue
        # Provisional solo si la actividad aún no tiene TSS guardado (evita penalizar adherencia).
        try:
            tss_val, _ = _estimate_session_tss(
                a,
                ftp=cycling_ftp,
                running_threshold_pace_sec_per_km=running_threshold_pace,
                hr_rest_bpm=hr_rest_bpm,
                hr_max_bpm=hr_max_bpm,
                hr_threshold_bpm=hr_threshold_bpm,
            )
        except (TypeError, ValueError, KeyError):
            tss_val = 0.0
        actual_tss_acc += max(0.0, float(tss_val or 0.0))
    actual_tss = round(actual_tss_acc, 1)

    if planned_group == "rest":
        type_score = 1.0 if actual_duration <= 15.0 else 0.0
        duration_score = 1.0 if actual_duration <= 15.0 else max(0.0, 1.0 - ((actual_duration - 15.0) / 60.0))
    else:
        if executed:
            main_exec = max(executed, key=_extract_activity_duration_minutes)
            actual_group = _normalize_activity_group(main_exec)
        else:
            actual_group = "none"

        if actual_group == planned_group:
            type_score = 1.0
        elif planned_group == "running" and actual_group in {"other", "none"}:
            type_score = 0.2
        elif planned_group == "running":
            type_score = 0.7
        else:
            type_score = 0.2

        if planned_duration <= 0:
            duration_score = 0.0
        else:
            duration_diff = abs(actual_duration - planned_duration) / max(planned_duration, 30.0)
            duration_score = max(0.0, 1.0 - duration_diff)

    adherence_score = round((0.6 * type_score) + (0.4 * duration_score), 2)
    if adherence_score >= 0.75:
        adherence_label = "adherente"
    elif adherence_score >= 0.40:
        adherence_label = "parcial"
    else:
        adherence_label = "baja"

    load_deviation_pct = round(
        ((actual_tss - planned_tss) / max(planned_tss, 1.0)),
        2,
    )
    if load_deviation_pct > 0.25:
        next_adjustment = "reducir siguiente sesión"
    elif load_deviation_pct < -0.25:
        next_adjustment = "progresar ligeramente siguiente sesión"
    else:
        next_adjustment = "mantener siguiente sesión"

    structured_summary = _structured_workout_summary(planned)
    block_feedback = _compute_structured_block_feedback(planned, actual_duration, actual_tss)

    return {
        "date": target_date_iso,
        "planned": {
            "session_type": planned_type,
            "duration_min": round(planned_duration, 1),
            "intensity": str(planned.get("intensity") or ""),
            "tss_est": planned_tss,
            "structured_summary": structured_summary,
        },
        "executed": {
            "activities": len(executed),
            "duration_min": actual_duration,
            "tss": actual_tss,
        },
        "block_feedback": block_feedback,
        "adherence_score": adherence_score,
        "adherence_label": adherence_label,
        "load_deviation_pct": load_deviation_pct,
        "next_session_adjustment": next_adjustment,
    }


def _compute_daily_plan_adjustment(snapshot: dict, plan: dict | None) -> dict | None:
    """Motor determinista de ajuste diario del plan.

    Entrada: TSB, ATL, TSS semanal, sueño, HRV, body battery y sesión planificada.
    Salida: maintain, reduce, easy o rest.
    """
    has_plan = isinstance(plan, dict)
    if not has_plan:
        plan = {}

    dates = snapshot.get("dates") or {}
    today_iso = str(dates.get("today") or date.today().isoformat())
    try:
        today_idx = date.fromisoformat(today_iso).isoweekday()
    except (TypeError, ValueError):
        today_idx = date.today().isoweekday()

    plan_data = plan.get("plan_data") if isinstance(plan.get("plan_data"), dict) else {}
    plan_constraints = plan_data.get("constraints") if isinstance(plan_data.get("constraints"), dict) else {}
    unavailable_days = set(int(d) for d in (plan_constraints.get("unavailable_days") or []))
    max_minutes_per_day = {
        int(k): int(v)
        for k, v in (plan_constraints.get("max_minutes_per_day") or {}).items()
        if 1 <= int(k) <= 7 and int(v) >= 0
    }

    planned_session = _resolve_today_plan_session(plan) if has_plan else "sin plan activo"
    planned_row = _get_planned_session_for_date(plan, today_iso) if has_plan else None
    planned_duration_min = int((planned_row or {}).get("duration_min") or 0)

    if today_idx in unavailable_days:
        return {
            "rule": "availability",
            "decision": "rest",
            "reason": "día no entrenable según disponibilidad declarada",
            "adherence_adjustment": "none",
            "planned_session": planned_session,
            "resulting_session": "descanso + movilidad ligera (20-30 min)",
            "inputs": {
                "status": "neutral",
                "today_day_index": today_idx,
                "planned_duration_min": planned_duration_min,
                "stress_flags": None,
            },
        }

    load_fatigue = snapshot.get("load_fatigue") or {}
    latest = load_fatigue.get("latest") or {}
    weekly = load_fatigue.get("weekly") or {}
    ranges = load_fatigue.get("ranges") or {}

    status = str(load_fatigue.get("status") or "neutral").strip().lower() or "neutral"
    tsb = _safe_float(latest.get("tsb"), 0.0)
    atl = _safe_float(latest.get("atl"), 0.0)
    weekly_tss = _safe_float(weekly.get("current_tss"), 0.0)
    atl_high = _safe_float(ranges.get("atl_high"), 0.0)

    body_today_payload = (snapshot.get("body_battery") or {}).get("today")
    hrv_today_payload = (snapshot.get("hrv") or {}).get("today")
    sleep_today_payload = (snapshot.get("sleep") or {}).get("today")

    bb_level = _extract_body_battery_level(body_today_payload, today_iso)
    sleep_hours, sleep_score = _extract_sleep_inputs(sleep_today_payload, today_iso)
    hrv_avg, hrv_weekly, hrv_status = _extract_hrv_inputs(hrv_today_payload, today_iso)
    hrv_ratio = (hrv_avg / hrv_weekly) if (hrv_avg and hrv_weekly and hrv_weekly > 0) else None

    low_bb = bb_level is not None and bb_level < 35.0
    poor_sleep = (
        (sleep_hours is not None and sleep_hours < 6.0)
        or (sleep_score is not None and sleep_score < 60.0)
    )
    low_hrv = (
        (hrv_ratio is not None and hrv_ratio < 0.90)
        or (hrv_status in {"low", "unbalanced", "poor"})
    )
    high_weekly = (
        _safe_float(weekly.get("high_tss"), 0.0) > 0
        and weekly_tss >= _safe_float(weekly.get("high_tss"), 0.0)
    )
    weekly_spike_alert = bool((load_fatigue.get("flags") or {}).get("weekly_spike_alert"))

    stress_flags = int(low_bb) + int(poor_sleep) + int(low_hrv) + int(high_weekly) + int(weekly_spike_alert)

    # Reglas explícitas por estado.
    if status == "overload":
        decision = "rest"
        rule = "overload"
        reason = "TSB (Forma)/estado en sobrecarga: activar descarga obligatoria"
    elif status == "fatigue_high":
        if stress_flags >= 2:
            decision = "rest"
            reason = "fatiga alta + señales de recuperación alteradas"
        else:
            decision = "easy"
            reason = "fatiga alta: convertir sesión a suave"
        rule = "fatigue_high"
    elif status == "ready":
        if stress_flags == 0:
            decision = "maintain"
            reason = "disponibilidad alta y recuperación estable"
        elif stress_flags == 1:
            decision = "reduce"
            reason = "ready pero con una señal de riesgo"
        else:
            decision = "easy"
            reason = "ready con múltiples señales de riesgo"
        rule = "ready"
    else:
        if stress_flags >= 2:
            decision = "easy"
            reason = "estado neutral con recuperación comprometida"
        elif stress_flags == 1:
            decision = "reduce"
            reason = "estado neutral con una señal de riesgo"
        else:
            decision = "maintain"
            reason = "estado neutral sin alertas relevantes"
        rule = "neutral"

    # Ajuste fase 3: planificado vs ejecutado (día N-1) modifica la decisión base.
    feedback = snapshot.get("plan_execution_feedback") or {}
    adherence_score = _safe_float(feedback.get("adherence_score"), -1.0)
    load_dev = _safe_float(feedback.get("load_deviation_pct"), 0.0)
    adherence_adjustment = "none"

    order = ["rest", "easy", "reduce", "maintain"]
    rank = {name: idx for idx, name in enumerate(order)}

    def _downgrade(current: str) -> str:
        return order[max(0, rank.get(current, 2) - 1)]

    def _upgrade(current: str) -> str:
        return order[min(len(order) - 1, rank.get(current, 2) + 1)]

    executed_yday = int(((feedback.get("executed") or {}).get("activities")) or 0)
    if decision != "rest" and adherence_score >= 0.0:
        # Se baja si ayer hizo más carga de la prevista o una actividad distinta; no por no entrenar (29/10: saltarse
        # una sesión deja al atleta más fresco, y antes bajaba la de hoy igual que un exceso).
        if load_dev > 0.25 or (adherence_score < 0.40 and executed_yday > 0):
            new_decision = _downgrade(decision)
            if new_decision != decision:
                decision = new_decision
                adherence_adjustment = "down"
                why = ("ayer hiciste más carga de la prevista" if load_dev > 0.25
                       else "ayer hiciste una sesión distinta de la prevista")
                reason = f"{reason}; ajuste porque {why}"
        elif (
            load_dev < -0.30
            and adherence_score >= 0.75
            and stress_flags <= 1
            and status in {"ready", "neutral"}
        ):
            new_decision = _upgrade(decision)
            if new_decision != decision:
                decision = new_decision
                adherence_adjustment = "up"
                reason = f"{reason}; ajuste por infra-carga adherente en dia previo"

    day_cap = max_minutes_per_day.get(today_idx)
    if day_cap is not None and planned_duration_min > day_cap and decision != "rest":
        decision = "reduce" if day_cap > 0 else "rest"
        reason = f"{reason}; sesión ajustada por límite diario ({day_cap} min)"

    adjusted_structured_workout = None
    adjustment_trace: list[str] = []
    if has_plan:
        if decision in {"easy", "reduce", "maintain"}:
            adjusted_structured_workout, adjustment_trace = _apply_structured_workout_adjustment(planned_row, decision)
        elif decision == "rest":
            adjusted_structured_workout = {
                "schema": "kairos-workout-v1",
                "sessionType": "rest",
                "steps": [
                    {
                        "name": "Rest",
                        "type": "rest",
                        "duration_min": 0,
                        "reps": 1,
                        "intensityClass": "recovery",
                    }
                ],
            }
            adjustment_trace = ["sesión convertida a descanso"]

    if decision == "rest":
        resulting_session = "descanso + movilidad ligera (20-30 min)"
    elif decision == "easy":
        resulting_session = (
            _build_adjusted_structured_session_text(planned_session, planned_row, "easy")
            if has_plan
            else "rodaje suave 30-50 min en Z1-Z2 + movilidad"
        )
    elif decision == "reduce":
        resulting_session = (
            _build_adjusted_structured_session_text(planned_session, planned_row, "reduce")
            if has_plan
            else "rodaje controlado 40-60 min (sin bloques intensos)"
        )
    else:
        resulting_session = (
            _build_adjusted_structured_session_text(planned_session, planned_row, "maintain")
            if has_plan
            else "sesión de calidad controlada (siempre con calentamiento y enfriamiento)"
        )

    return {
        "rule": rule,
        "decision": decision,
        "reason": reason,
        "adherence_adjustment": adherence_adjustment,
        "planned_session": planned_session,
        "resulting_session": resulting_session,
        "adjusted_structured_workout": adjusted_structured_workout,
        "adjustment_trace": adjustment_trace,
        "inputs": {
            "status": status,
            "tsb": tsb,
            "atl": atl,
            "atl_high": atl_high,
            "weekly_tss": weekly_tss,
            "body_battery": bb_level,
            "sleep_hours": sleep_hours,
            "sleep_score": sleep_score,
            "hrv_avg": hrv_avg,
            "hrv_weekly": hrv_weekly,
            "hrv_ratio": hrv_ratio,
            "hrv_status": hrv_status,
            "stress_flags": stress_flags,
            "adherence_score": adherence_score if adherence_score >= 0.0 else None,
            "load_deviation_pct": load_dev if adherence_score >= 0.0 else None,
            "today_day_index": today_idx,
            "planned_duration_min": planned_duration_min,
            "day_cap_min": day_cap,
        },
    }
