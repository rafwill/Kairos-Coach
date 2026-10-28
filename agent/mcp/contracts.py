"""
agent/mcp/contracts.py — capa del cliente MCP en el host (Kairos).

Contratos mínimos de argumentos por tool y normalización de invocaciones (fechas, alias).
"""

from __future__ import annotations

ADAPTER_CONTRACT_VERSION = "mcp-adapter-v1"


TOOL_CONTRACTS_V1: dict[str, dict] = {
    "get_user_profile": {},
    "get_activities": {},
    "get_activity": {"required_any": [("activity_id",)]},
    "get_activity_details": {"required_any": [("activity_id",)]},
    "get_activity_hr_in_timezones": {"required_any": [("activity_id",)]},
    "get_activities_by_date": {"required_any": [("start_date", "end_date"), ("startdate", "enddate")]},
    "get_activities_fordate": {"required_any": [("date",), ("startdate", "enddate")]},
    "get_activity_splits": {"required_any": [("activity_id",)]},
    "get_activity_exercise_sets": {"required_any": [("activity_id",)]},
    "get_activity_power_in_timezones": {"required_any": [("activity_id",)]},
    "get_stats": {},
    "get_sleep_summary": {"required_any": [("date",)]},
    "get_sleep_data": {"required_any": [("date",)]},
    "get_heart_rates_summary": {"required_any": [("date",)]},
    "get_stress_summary": {"required_any": [("date",)]},
    "get_respiration_summary": {"required_any": [("date",)]},
    "get_all_day_stress": {"required_any": [("date",)]},
    "get_all_day_events": {"required_any": [("date",)]},
    "get_body_battery": {"required_any": [("start_date", "end_date")]},
    "get_rhr_day": {"required_any": [("date",)]},
    "get_spo2_data": {"required_any": [("date",)]},
    "get_hrv_data": {"required_any": [("date",)]},
    "get_daily_steps": {"required_any": [("date",)]},
    "get_hydration_data": {"required_any": [("date",)]},
    "get_body_composition": {"required_any": [("start_date", "end_date")]},
    "get_training_readiness": {},
    "get_morning_training_readiness": {},
    "get_training_status": {},
    "get_training_load_trend": {"required_any": [("start_date", "end_date")]},
    "get_training_effect": {},
    "get_hrv_trend": {"required_any": [("start_date", "end_date")]},
    "get_rhr_trend": {"required_any": [("start_date", "end_date")]},
    "get_stress_trend": {"required_any": [("start_date", "end_date")]},
    "get_vo2max_trend": {"required_any": [("start_date", "end_date")]},
    "get_endurance_score": {},
    "get_fitnessage_data": {},
    "get_lactate_threshold": {},
    "get_heart_rate_zones": {},
    "get_cycling_ftp": {},
    "get_race_predictions": {},
    "get_personal_record": {},
    "get_weekly_steps": {},
    "get_weekly_intensity_minutes": {},
    "get_weekly_stress": {},
    "kairos_load_trends": {},
    "kairos_correlate": {},
    "kairos_weekly_sport_breakdown": {},
}


def normalize_tool_invocation(tool_name: str, arguments: dict) -> tuple[str, dict]:
    """Normaliza alias y shape mínimo de argumentos antes de llamar al MCP."""
    normalized_name = str(tool_name or "").strip()
    normalized_args = dict(arguments or {})

    if normalized_name == "get_personal_records":
        normalized_name = "get_personal_record"

    if normalized_name in {"get_body_battery", "get_body_composition"}:
        start = normalized_args.get("start_date")
        end = normalized_args.get("end_date")
        date_arg = normalized_args.get("date")

        if date_arg and not start and not end:
            normalized_args["start_date"] = date_arg
            normalized_args["end_date"] = date_arg
        elif start and not end:
            normalized_args["end_date"] = start
        elif end and not start:
            normalized_args["start_date"] = end

        normalized_args.pop("date", None)

    return normalized_name, normalized_args


def validate_min_input_contract(tool_name: str, arguments: dict) -> str | None:
    """Valida contratos mínimos en la frontera adapter->MCP."""
    contract = TOOL_CONTRACTS_V1.get(tool_name) or {}
    required_any = contract.get("required_any") or []

    for required_group in required_any:
        if all(arguments.get(key) not in (None, "") for key in required_group):
            return None

    if required_any:
        options = [" + ".join(group) for group in required_any]
        return (
            f"Error de contrato ({ADAPTER_CONTRACT_VERSION}) para '{tool_name}': "
            f"faltan campos requeridos. Aceptado: {' | '.join(options)}."
        )
    return None
