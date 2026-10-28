"""
agent/wellness.py
Sueño, HRV, body battery, FC en reposo y estrés de un día: elegir el día en la respuesta de Garmin, formatearlo para el chat y extraer lo que usa el motor diario.
"""

import functools
from typing import Any


from agent.garmin_data import (
    _first_value,
)


def _pick_day_payload(payload: Any, target_date: str) -> dict | None:
    """Intenta extraer el bloque de datos del día objetivo desde payloads heterogéneos."""
    def _date_match(item: dict) -> bool:
        if not target_date:
            return False
        day = str(item.get("date") or item.get("calendarDate") or item.get("measurementDate") or "")
        return day == target_date

    def _walk(node: Any) -> dict | None:
        if isinstance(node, list):
            # 1) Priorizar coincidencia exacta por fecha.
            for item in node:
                if isinstance(item, dict) and _date_match(item):
                    return item
            # 2) Buscar recursivamente en elementos envueltos.
            for item in node:
                nested = _walk(item)
                if nested is not None:
                    return nested
            # 3) Fallback al primer dict disponible.
            for item in node:
                if isinstance(item, dict):
                    return item
            return None

        if isinstance(node, dict):
            if _date_match(node):
                return node

            # Claves contenedoras comunes en respuestas MCP/Garmin.
            for key in (
                "data",
                "result",
                "items",
                "values",
                "measurements",
                "records",
                "dailySummary",
                "summaries",
                "stressValues",
                "allDayStressValues",
            ):
                if key in node:
                    nested = _walk(node.get(key))
                    if nested is not None:
                        return nested

            # Último recurso: explorar recursivo cualquier valor dict/list.
            for value in node.values():
                if isinstance(value, (dict, list)):
                    nested = _walk(value)
                    if nested is not None:
                        return nested

            # Si no hay nada mejor, conservar dict raíz.
            return node

        return None

    return _walk(payload)


def _pick_body_battery_day_payload(payload: Any, target_date: str) -> dict | None:
    """Selecciona el registro de body battery más informativo del día objetivo."""
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, list):
        return None

    candidates: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or item.get("calendarDate") or "")
        if day == target_date:
            candidates.append(item)

    if not candidates:
        candidates = [item for item in payload if isinstance(item, dict)]
    if not candidates:
        return None

    def _score(day: dict) -> int:
        score = 0
        if (
            day.get("body_battery_level") is not None
            or day.get("bodyBatteryLevel") is not None
            or day.get("bodyBatteryMostRecentValue") is not None
            or day.get("bodyBatteryValue") is not None
            or day.get("current") is not None
            or day.get("value") is not None
        ):
            score += 100
        if day.get("highestBodyBattery") is not None or day.get("highest") is not None or day.get("body_battery_highest") is not None:
            score += 10
        if day.get("lowestBodyBattery") is not None or day.get("lowest") is not None or day.get("body_battery_lowest") is not None:
            score += 10
        if day.get("charged") is not None or day.get("body_battery_charged") is not None or day.get("bodyBatteryCharged") is not None:
            score += 1
        if day.get("drained") is not None or day.get("body_battery_drained") is not None or day.get("bodyBatteryDrained") is not None:
            score += 1
        return score

    return max(candidates, key=_score)


def _format_body_battery_day(payload: Any, target_date: str) -> str:
    """Formatea Body Battery con datos reales del día si están disponibles."""
    day = _pick_body_battery_day_payload(payload, target_date)
    if not day:
        return "sin datos"

    def _first_present(*keys: str) -> Any:
        # Sin `or`: un 0 (p. ej. drained=0 de madrugada) es un dato válido.
        return next((day.get(k) for k in keys if day.get(k) is not None), None)

    level = _first_present(
        "body_battery_level", "bodyBatteryLevel", "bodyBatteryMostRecentValue", "bodyBatteryValue", "current", "value"
    )
    values = day.get("bodyBatteryValuesArray")
    if level is None and isinstance(values, list):
        # Serie [timestamp, nivel]: el último nivel válido es el actual.
        for point in reversed(values):
            if isinstance(point, (list, tuple)) and len(point) >= 2 and point[1] is not None:
                level = point[1]
                break
    highest = _first_present("highestBodyBattery", "highest", "body_battery_highest")
    lowest = _first_present("lowestBodyBattery", "lowest", "body_battery_lowest")
    charged = _first_present("charged", "body_battery_charged")
    drained = _first_present("drained", "body_battery_drained")

    parts: list[str] = []
    if level is not None:
        parts.append(f"nivel {int(level)}")
    if highest is not None and lowest is not None:
        parts.append(f"max {int(highest)}/min {int(lowest)}")
    if charged is not None and drained is not None:
        parts.append(f"cargado/drenado +{int(charged)}/-{int(drained)}")

    if parts:
        return " · ".join(parts)
    # Registro del día sin valores (p. ej., el reloj aún no ha sincronizado la noche).
    return "sin datos"


def _hrv_deviation_text(avg: Any, weekly: Any) -> str:
    """Diferencia de la HRV de la noche frente a su media de 7 días, ya calculada para el LLM (26/10: con "58 ms" y
    "media 59 ms" por separado escribía "> 10 % por debajo"; sensor S25)."""
    try:
        night, base = float(avg), float(weekly)
    except (TypeError, ValueError):
        return ""
    if base <= 0:
        return ""
    pct = round(100.0 * (night - base) / base)
    return f"{pct:+d} % frente a su media de 7 días" if pct else "igual que su media de 7 días"


def _format_hrv_day(payload: Any, target_date: str) -> str:
    """Formatea HRV con métricas relevantes (ms) del día."""
    day = _pick_day_payload(payload, target_date)
    if not day:
        return "sin datos"

    avg = (
        day.get("last_night_avg_hrv_ms")
        or day.get("lastNightAvg")
        or day.get("avgOvernightHrv")
        or day.get("avgHrv")
    )
    weekly = day.get("weekly_avg_hrv_ms") or day.get("weeklyAvg")
    status = day.get("status")

    parts: list[str] = []
    if avg is not None:
        parts.append(f"{float(avg):.1f} ms")
    if weekly is not None:
        parts.append(f"7d {float(weekly):.1f} ms")
    deviation = _hrv_deviation_text(avg, weekly)
    if deviation:
        parts.append(deviation)
    if status:
        parts.append(str(status))

    if parts:
        return " · ".join(parts)
    # Registro del día sin valores (p. ej., el reloj aún no ha sincronizado la noche).
    return "sin datos"


def _format_sleep_day(payload: Any, target_date: str) -> str:
    """Formatea sueño con horas y puntuación cuando exista."""
    day = _pick_day_payload(payload, target_date)
    if not day:
        return "sin datos"

    sleep_hours = day.get("sleep_hours")
    sleep_seconds = day.get("sleep_seconds") or day.get("sleepTimeSeconds")
    score = day.get("sleep_score") or day.get("sleepScore")

    if sleep_hours is None and sleep_seconds is not None:
        try:
            sleep_hours = round(float(sleep_seconds) / 3600, 2)
        except (TypeError, ValueError):
            sleep_hours = None

    parts: list[str] = []
    if sleep_hours is not None:
        total_min = int(round(float(sleep_hours) * 60))
        parts.append(f"{total_min // 60} h {total_min % 60:02d} min")
    if score is not None:
        parts.append(f"puntuación {int(score)}")

    if parts:
        return " · ".join(parts)
    # Registro del día sin valores (p. ej., el reloj aún no ha sincronizado la noche).
    return "sin datos"


def _format_rhr_day(payload: Any, target_date: str) -> str:
    """Formatea FC en reposo del día cuando exista."""
    day = _pick_day_payload(payload, target_date)
    if not day:
        return "sin datos"

    rhr = (
        day.get("restingHeartRate")
        or day.get("resting_heart_rate")
        or day.get("resting_hr")
        or day.get("rhr")
        or day.get("value")
    )
    if rhr is None:
        return "sin datos"

    try:
        return f"{int(float(rhr))} bpm"
    except (TypeError, ValueError):
        return f"{rhr}"


def _format_stress_day(summary_payload: Any, all_day_payload: Any, target_date: str) -> str:
    """Formatea estrés diario priorizando summary y usando all-day como fallback."""
    day = _pick_day_payload(summary_payload, target_date)
    if day:
        avg = (
            day.get("average_stress_level")
            or day.get("avgStressLevel")
            or day.get("averageStressLevel")
            or day.get("avgStress")
        )
        highest = day.get("max_stress_level") or day.get("maxStressLevel") or day.get("highestStressLevel")
        restful = day.get("rest_stress_duration") or day.get("restStressDuration")
        parts: list[str] = []
        if avg is not None:
            try:
                parts.append(f"avg {int(float(avg))}")
            except (TypeError, ValueError):
                parts.append(f"avg {avg}")
        if highest is not None:
            try:
                parts.append(f"max {int(float(highest))}")
            except (TypeError, ValueError):
                parts.append(f"max {highest}")
        if restful is not None:
            parts.append(f"reposo {restful}")
        if parts:
            return " · ".join(parts)

    # Fallback: payload crudo all-day stress
    day_all = _pick_day_payload(all_day_payload, target_date)
    if day_all:
        sample_size = None
        if isinstance(day_all, dict):
            for key in ("values", "stressValuesArray", "allDayStressValues", "stressValues"):
                arr = day_all.get(key)
                if isinstance(arr, list):
                    sample_size = len(arr)
                    break
        if sample_size is not None:
            return f"datos all-day ({sample_size} muestras)"
        return "datos all-day disponibles"
    return "sin datos"


def _format_all_day_events(payload: Any, target_date: str) -> str:
    """Resume cantidad de eventos diarios cuando el endpoint los devuelve."""
    day = _pick_day_payload(payload, target_date)
    if not day:
        return "sin datos"
    if isinstance(day, list):
        return f"{len(day)} eventos"
    if isinstance(day, dict):
        for key in ("events", "eventList", "dailyEvents", "wellnessEvents"):
            events = day.get(key)
            if isinstance(events, list):
                return f"{len(events)} eventos"
        return "eventos disponibles"
    return "sin datos"


def _format_training_effect(payload: Any, activity_payload: Any) -> tuple[str, str]:
    """Formatea Training Effect y devuelve (valor, fuente).

    get_training_effect puede devolver todos los campos a null; entonces se usa la actividad.
    """
    for candidate, source in ((payload, "Garmin get_training_effect"), (activity_payload, "Garmin get_activity")):
        if candidate is None:
            continue
        # Primero el propio diccionario (p. ej. la actividad ya aplanada); _pick_day_payload puede
        # devolver otro sub-diccionario (activityTypeDTO) sin Training Effect.
        for src in (candidate, _pick_day_payload(candidate, "")):
            if isinstance(src, dict):
                value = _format_training_effect_fields(src)
                if value:
                    return value, source
    return "sin datos", "no disponible"


def _format_training_effect_fields(src: dict) -> str:
    te = src.get("training_effect") or src.get("trainingEffect") or src.get("aerobicTrainingEffect") or src.get("aerobic_effect")
    an = src.get("anaerobic_effect") or src.get("anaerobicTrainingEffect")
    label = src.get("training_effect_label") or src.get("trainingEffectLabel")
    recovery_h = src.get("recovery_time_hours") or src.get("recoveryTimeHours")

    parts: list[str] = []
    if te is not None:
        try:
            parts.append(f"aeróbico {float(te):.1f}")
        except (TypeError, ValueError):
            parts.append(f"aeróbico {te}")
    if an is not None:
        try:
            parts.append(f"anaeróbico {float(an):.1f}")
        except (TypeError, ValueError):
            parts.append(f"anaeróbico {an}")
    if label:
        parts.append(str(label))
    if recovery_h is not None:
        try:
            parts.append(f"recuperación {float(recovery_h):.1f} h")
        except (TypeError, ValueError):
            parts.append(f"recuperación {recovery_h}")

    return " · ".join(parts)


def _extract_body_battery_level(payload: Any, target_date: str) -> float | None:
    """Extrae nivel de Body Battery del día objetivo."""
    day = _pick_body_battery_day_payload(payload, target_date)
    if not isinstance(day, dict):
        return None
    value = (
        day.get("body_battery_level")
        or day.get("bodyBatteryLevel")
        or day.get("bodyBatteryMostRecentValue")
        or day.get("bodyBatteryValue")
        or day.get("current")
        or day.get("value")
    )
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_body_battery_snapshot(payload: Any, target_date: str) -> dict | None:
    """Extrae campos normalizados de Body Battery para persistencia diaria."""
    day = _pick_body_battery_day_payload(payload, target_date)
    if not isinstance(day, dict):
        return None

    _first = functools.partial(_first_value, day)

    snapshot = {
        "level": _first("body_battery_level", "bodyBatteryLevel", "bodyBatteryMostRecentValue", "bodyBatteryValue", "current", "value"),
        "charged": _first("charged", "body_battery_charged", "bodyBatteryCharged"),
        "drained": _first("drained", "body_battery_drained", "bodyBatteryDrained"),
        "highest": _first("highestBodyBattery", "highest", "body_battery_highest"),
        "lowest": _first("lowestBodyBattery", "lowest", "body_battery_lowest"),
    }
    if all(value is None for value in snapshot.values()):
        return None
    return snapshot


def _extract_hrv_snapshot(payload: Any, target_date: str) -> dict | None:
    """Extrae campos normalizados de HRV para persistencia diaria."""
    day = _pick_day_payload(payload, target_date)
    if not isinstance(day, dict):
        return None

    _first = functools.partial(_first_value, day)

    snapshot = {
        "last_night_avg_ms": _first("last_night_avg_hrv_ms", "lastNightAvg", "avgOvernightHrv", "avgHrv"),
        "weekly_avg_ms": _first("weekly_avg_hrv_ms", "weeklyAvg"),
        "status": _first("status"),
    }
    if all(value is None for value in snapshot.values()):
        return None
    return snapshot


def _build_wellness_payloads_from_row(row: dict | None, metric_date: str) -> tuple[dict | None, dict | None]:
    """Convierte una fila de wellness_daily en payloads compatibles con los formateadores."""
    if not isinstance(row, dict):
        return (None, None)

    body_payload = {
        "date": metric_date,
        "body_battery_level": row.get("body_battery_level"),
        "body_battery_charged": row.get("bb_charged"),
        "body_battery_drained": row.get("bb_drained"),
        "body_battery_highest": row.get("bb_highest"),
        "body_battery_lowest": row.get("bb_lowest"),
    }
    hrv_payload = {
        "date": metric_date,
        "last_night_avg_hrv_ms": row.get("hrv_last_night_avg_ms"),
        "weekly_avg_hrv_ms": row.get("hrv_weekly_avg_ms"),
        "status": row.get("hrv_status"),
    }

    has_body = any(v is not None for k, v in body_payload.items() if k != "date")
    has_hrv = any(v is not None for k, v in hrv_payload.items() if k != "date")
    return (body_payload if has_body else None, hrv_payload if has_hrv else None)


def _extract_sleep_inputs(payload: Any, target_date: str) -> tuple[float | None, float | None]:
    """Extrae horas de sueño y score del día objetivo."""
    day = _pick_day_payload(payload, target_date)
    if not isinstance(day, dict):
        return (None, None)

    sleep_hours = day.get("sleep_hours")
    sleep_seconds = day.get("sleep_seconds") or day.get("sleepTimeSeconds")
    if sleep_hours is None and sleep_seconds is not None:
        try:
            sleep_hours = float(sleep_seconds) / 3600.0
        except (TypeError, ValueError):
            sleep_hours = None

    score = day.get("sleep_score") or day.get("sleepScore")
    try:
        sleep_score = float(score) if score is not None else None
    except (TypeError, ValueError):
        sleep_score = None

    try:
        sleep_hours_f = float(sleep_hours) if sleep_hours is not None else None
    except (TypeError, ValueError):
        sleep_hours_f = None

    return (sleep_hours_f, sleep_score)


def _extract_hrv_inputs(payload: Any, target_date: str) -> tuple[float | None, float | None, str]:
    """Extrae HRV media nocturna, media 7d y estado textual."""
    day = _pick_day_payload(payload, target_date)
    if not isinstance(day, dict):
        return (None, None, "")

    avg = (
        day.get("last_night_avg_hrv_ms")
        or day.get("lastNightAvg")
        or day.get("avgOvernightHrv")
        or day.get("avgHrv")
    )
    weekly = day.get("weekly_avg_hrv_ms") or day.get("weeklyAvg")
    status = str(day.get("status") or "").strip().lower()

    try:
        avg_f = float(avg) if avg is not None else None
    except (TypeError, ValueError):
        avg_f = None
    try:
        weekly_f = float(weekly) if weekly is not None else None
    except (TypeError, ValueError):
        weekly_f = None

    return (avg_f, weekly_f, status)
