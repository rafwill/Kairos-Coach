"""
agent/garmin_data.py
Lectura de las respuestas de Garmin: valores por clave, fecha y deporte de una actividad, nombres de actividades y de zonas en español.
"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any


def _is_activity_in_last_48h(activity: dict, now: datetime | None = None) -> bool:
    """Comprueba si una actividad cae en la ventana de últimas 48h."""
    now_day = now.date() if now is not None else datetime.now(tz=timezone.utc).date()
    act_iso = _extract_activity_date_iso(activity)
    if not act_iso:
        return False
    try:
        act_date = date.fromisoformat(act_iso)
    except ValueError:
        return False

    delta = now_day - act_date
    return timedelta(days=0) <= delta <= timedelta(days=2)


# Nombres de zona de FC: los de TrainingPeaks, en español. Los mismos en el contexto del LLM, en los
# análisis de actividad y en el juez (antes convivían los de Garmin, "Suave" para Z2, y los de Kairos).
HR_ZONE_NAMES_ES = {1: "Recuperación", 2: "Aeróbica", 3: "Tempo", 4: "Subumbral", 5: "Supraumbral"}


def _key_candidates(raw_key: Any) -> set[str]:
    """Formas de una clave para compararla sin mirar mayúsculas ni separadores: snake_case y todo junto."""
    key = str(raw_key or "").strip().lower()
    if not key:
        return set()
    snake = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    compact = re.sub(r"[^a-z0-9]", "", key)
    return {snake, compact}


def _first_value(source: dict, *keys: str) -> Any:
    """Valor de la primera clave que exista (no None) en `source`."""
    for key in keys:
        value = source.get(key)
        if value is not None:
            return value
    return None


# Familias de deporte para agrupar (por typeKey de Garmin) y lo que pide la pregunta.
def _activity_family(act: dict) -> str:
    key = str((act.get("activityType") or {}).get("typeKey") if isinstance(act.get("activityType"), dict)
              else act.get("activityType") or "").lower()
    if "trail" in key:
        return "Trail"
    if "run" in key:
        return "Carrera"
    if "cycl" in key or "bik" in key:
        return "Ciclismo"
    if "swim" in key:
        return "Natación"
    if "walk" in key or "hik" in key:
        return "Caminar / senderismo"
    if "strength" in key:
        return "Fuerza"
    if "elliptical" in key:
        return "Elíptica"
    return key.replace("_", " ").capitalize() or "Otra"


def _activity_matches_sport(act: dict, sport_filter: str) -> bool:
    """Actividad de Garmin del deporte pedido (trail, running sin trail, cycling)."""
    if not isinstance(act, dict):
        return False
    type_key = str(
        (act.get("activityTypeDTO") or {}).get("typeKey", "")
        or (act.get("activityType") or {}).get("typeKey", "")
        or act.get("type", "")
    ).lower()
    if sport_filter == "trail":
        return "trail" in type_key
    if sport_filter == "running":
        return "running" in type_key and "trail" not in type_key
    if sport_filter == "cycling":
        return "cycl" in type_key or "bike" in type_key or "biking" in type_key
    return True


def _extract_activity_date_iso(activity: dict) -> str | None:
    """Obtiene la fecha ISO de una actividad Garmin a partir de sus campos de inicio.
    Soporta:
    - ISO strings: '2026-07-02T08:30:00' o '2026-07-02 08:30:00'
    - Solo fecha: '2026-07-02'
    - Epoch en milisegundos (int o string): 1751414400000
    - Epoch en segundos (int o string): 1751414400
    """
    if not isinstance(activity, dict):
        return None

    for key in (
                "startTimeLocal", "startTimeGMT", "startTimeUTC",
                "start_time_local", "start_time_gmt", "start_time_utc",
                "startTime", "start_time",
                "calendarDate", "beginTimestamp", "activitySummary"):
        value = activity.get(key)
        if value is None:
            continue

        # String con fecha ISO o similar
        if isinstance(value, str):
            s = value.strip()
            if len(s) >= 10:
                date_str = s[:10].replace(" ", "-")  # '2026 07 02' -> '2026-07-02'
                try:
                    return date.fromisoformat(date_str).isoformat()
                except ValueError:
                    pass
            # Epoch como string
            if s.isdigit() and len(s) >= 10:
                try:
                    ts = int(s)
                    if ts > 10_000_000_000:   # milisegundos
                        ts //= 1000
                    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                except (ValueError, OSError, OverflowError):
                    pass

        # Epoch numérico
        if isinstance(value, (int, float)) and value > 0:
            try:
                ts = int(value)
                if ts > 10_000_000_000:   # milisegundos
                    ts //= 1000
                return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            except (ValueError, OSError, OverflowError):
                pass

    return None


# Nombres de las zonas en los análisis de actividad: los mismos que en el resto de Kairos.
_GARMIN_ZONE_NAMES_ES = HR_ZONE_NAMES_ES


# Mapeo de typeKey de actividades Garmin → nombre en español
_GARMIN_ACTIVITY_NAMES_ES: dict[str, str] = {
    "trail_running":           "Trail Running",
    "running":                 "Running",
    "indoor_running":          "Running Indoor (Cinta)",
    "road_biking":             "Ciclismo de Carretera",
    "mountain_biking":         "Ciclismo MTB",
    "cycling":                 "Ciclismo",
    "indoor_cycling":          "Ciclismo Indoor",
    "virtual_ride":            "Ciclismo Virtual",
    "gravel_cycling":          "Ciclismo Gravel",
    "e_bike_mountain":         "E-Bike MTB",
    "e_bike_fitness":          "E-Bike Carretera",
    "strength_training":       "Entrenamiento de Fuerza",
    "functional_strength_training": "Fuerza Funcional",
    "swimming":                "Natación",
    "lap_swimming":            "Natación en Piscina",
    "open_water_swimming":     "Natación Aguas Abiertas",
    "hiking":                  "Senderismo",
    "walking":                 "Caminata",
    "yoga":                    "Yoga",
    "pilates":                 "Pilates",
    "cardio_training":         "Cardio",
    "fitness_equipment":       "Máquinas Fitness",
    "elliptical":              "Elíptica",
    "stair_climbing":          "Escaleras/Step",
    "rowing":                  "Remo",
    "paddleboarding":          "Paddle Surf",
    "kayaking":                "Kayak",
    "skiing":                  "Esquí",
    "snowboarding":            "Snowboard",
    "cross_country_skiing":    "Esquí de Fondo",
    "triathlon":               "Triatlón",
    "multi_sport":             "Multideporte",
    "tennis":                  "Tenis",
    "soccer":                  "Fútbol",
    "basketball":              "Baloncesto",
    "golf":                    "Golf",
    "breathwork":              "Respiración/Mindfulness",
}


def _get_activity_name_es(act_type) -> str:
    """Devuelve el nombre en español del tipo de actividad Garmin."""
    if isinstance(act_type, dict):
        key = str(act_type.get("typeKey") or act_type.get("typeName") or "").lower()
    else:
        key = str(act_type or "").lower()
    # Buscar coincidencia exacta primero
    if key in _GARMIN_ACTIVITY_NAMES_ES:
        return _GARMIN_ACTIVITY_NAMES_ES[key]
    # Coincidencia parcial por palabras clave
    if "trail" in key:
        return "Trail Running"
    if "mountain_bik" in key or "mtb" in key:
        return "Ciclismo MTB"
    if "bik" in key or "cycl" in key or "cicl" in key:
        return "Ciclismo"
    if "run" in key or "corr" in key:
        return "Running"
    if "strength" in key or "fuerza" in key or "weight" in key:
        return "Entrenamiento de Fuerza"
    if "swim" in key or "natac" in key:
        return "Natación"
    if "hik" in key or "trek" in key:
        return "Senderismo"
    if "walk" in key:
        return "Caminata"
    if "yoga" in key:
        return "Yoga"
    # Fallback: formatear el typeKey si está disponible
    if key:
        return key.replace("_", " ").title()
    return ""
