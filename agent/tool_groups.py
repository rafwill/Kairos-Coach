"""
agent/tool_groups.py — metatooling: qué tools de Garmin recibe el LLM según la pregunta (26/10).

Antes iban las 45 en cada llamada (≈ 6.000 tokens por vuelta del bucle). Ahora las tools se agrupan por tema
y la pregunta decide, con reglas (sin LLM), qué grupos van. Las de Kairos (`kairos_*`) van siempre, junto con
`kairos_pedir_herramientas`, con la que el LLM pide otro grupo si le falta algo. Si ninguna regla encaja, van
todas, como antes: así nunca se queda sin la tool que necesita.

Sin dependencias del resto de `agent/`.
"""

from __future__ import annotations

import re
import unicodedata

GROUPS: dict[str, tuple[str, ...]] = {
    "actividades": (
        "get_activities", "get_activity", "get_activity_details", "get_activity_hr_in_timezones",
        "get_activities_by_date", "get_activities_fordate", "get_activity_splits", "get_activity_exercise_sets",
        "get_activity_power_in_timezones", "get_training_effect",
    ),
    "sueno_recuperacion": (
        "get_sleep_data", "get_sleep_summary", "get_hrv_data", "get_hrv_trend", "get_body_battery", "get_rhr_day",
        "get_training_readiness", "get_morning_training_readiness",
    ),
    "dia_a_dia": (
        "get_stats", "get_heart_rates_summary", "get_stress_summary", "get_all_day_stress", "get_all_day_events",
        "get_respiration_summary", "get_spo2_data", "get_daily_steps", "get_hydration_data",
    ),
    "rendimiento": (
        "get_training_status", "get_training_load_trend", "get_vo2max_trend", "get_endurance_score",
        "get_fitnessage_data", "get_heart_rate_zones", "get_lactate_threshold", "get_cycling_ftp",
        "get_race_predictions", "get_personal_record",
    ),
    "semanal_cuerpo": (
        "get_weekly_steps", "get_weekly_intensity_minutes", "get_weekly_stress", "get_body_composition",
        "get_user_profile",
    ),
}
GROUP_OF = {tool: group for group, tools in GROUPS.items() for tool in tools}
REQUEST_TOOL = "kairos_pedir_herramientas"

# Reglas sobre la pregunta sin tildes y en minúsculas. Una pregunta puede activar varios grupos.
_RULES: dict[str, re.Pattern] = {
    "actividades": re.compile(
        r"actividad|carrera\b|rodaje|tirada|sesion|series|intervalo|vuelta|split|parcial|\bkm\b|ritmo|bici|"
        r"ciclismo|trail|nadar|natacion|fuerza|gimnasio|entreno\b|entrenamiento de\b|efecto de entrenamiento|"
        r"zona(s)? de (fc|frecuencia)|analiza"
    ),
    "sueno_recuperacion": re.compile(
        r"dorm|sueno|descans|\bhrv\b|variabilidad|body battery|bateria|recuper|reposo|readiness|disposicion|"
        r"preparad|como estoy|forma de hoy|puedo entrenar"
    ),
    "dia_a_dia": re.compile(
        r"estres|respira|spo2|saturacion|oxigeno|pasos|hidrata|agua|bebid|caloria|frecuencia cardiaca|pulso"
    ),
    "rendimiento": re.compile(
        r"vo2|estado de entrenamiento|training status|resistencia|endurance|edad (fisica|de fitness)|fitness|"
        r"zona|umbral|lactato|\bftp\b|predicc|record|marca personal|\bpb\b|carga garmin"
    ),
    "semanal_cuerpo": re.compile(
        r"semana|semanal|minutos de intensidad|peso|composicion|grasa|\bimc\b|musculo|perfil|altura"
    ),
}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def groups_for(question: str) -> set[str]:
    """Grupos que pide la pregunta; vacío si ninguna regla encaja (entonces van todas las tools)."""
    q = _fold(question)
    return {group for group, rule in _RULES.items() if rule.search(q)}


def request_tool_schema() -> dict:
    """Meta-tool para que el LLM pida otro grupo de tools en la vuelta siguiente."""
    return {
        "type": "function",
        "function": {
            "name": REQUEST_TOOL,
            "description": (
                "Si para responder necesitas datos de Garmin cuyas herramientas no tienes ahora, pide su grupo y "
                "las tendrás en la siguiente llamada. actividades: lista, detalle, vueltas, zonas, potencia, "
                "efecto de entrenamiento. sueno_recuperacion: sueño, HRV, body battery, FC en reposo, disposición. "
                "dia_a_dia: resumen del día, estrés, FC, respiración, SpO2, pasos, hidratación. rendimiento: estado "
                "de entrenamiento, carga Garmin, VO2máx, resistencia, zonas, umbral, FTP, predicciones, récords. "
                "semanal_cuerpo: pasos, minutos de intensidad y estrés semanales, composición corporal, perfil."
            ),
            "parameters": {
                "type": "object",
                "properties": {"grupo": {"type": "string", "enum": sorted(GROUPS)}},
                "required": ["grupo"],
            },
        },
    }


def select(schema: list[dict], groups: set[str]) -> list[dict]:
    """Las tools de esos grupos más las de Kairos y la meta-tool; sin grupos, todas (como antes del 26/10)."""
    if not groups:
        return list(schema)
    wanted = {tool for group in groups for tool in GROUPS.get(group, ())}
    out = [t for t in schema if _name(t).startswith("kairos_") or _name(t) in wanted or _name(t) not in GROUP_OF]
    missing = set(GROUPS) - groups
    if missing:
        out.append(request_tool_schema())
    return out


def _name(tool: dict) -> str:
    return str(((tool or {}).get("function") or {}).get("name") or "")
