"""
mcp_adapter.py
Capa de adaptación estable entre Kairos y el servidor MCP.
"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from datetime import date
from hashlib import sha256

from agent import storage as _storage
from agent import load_metrics as _load_metrics

ADAPTER_CONTRACT_VERSION = "mcp-adapter-v1"


TOOL_CONTRACTS_V1: dict[str, dict] = {
    "get_user_profile": {},
    "get_activities": {},
    "get_activity": {"required_any": [("activity_id",)]},
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
    "get_vo2max_trend": {"required_any": [("start_date", "end_date")]},
    "get_endurance_score": {},
    "get_fitnessage_data": {},
    "get_lactate_threshold": {},
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

CRITICAL_CACHEABLE_TOOLS: set[str] = {
    "get_user_profile",
    "get_activities",
    "get_activities_by_date",
    "get_activity",
    "get_activity_hr_in_timezones",
    "get_body_battery",
    "get_hrv_data",
    "get_sleep_summary",
    "get_training_readiness",
    "get_morning_training_readiness",
    "get_training_load_trend",
    "get_personal_record",
}


def get_contract_registry() -> dict[str, dict]:
    """Devuelve copia del registro de contratos v1 por tool."""
    return dict(TOOL_CONTRACTS_V1)


def _build_cache_key(tool_name: str, arguments: dict) -> str:
    payload = {
        "tool": str(tool_name or "").strip(),
        "args": dict(arguments or {}),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _load_adapter_cache_from_profile() -> dict:
    try:
        profile = _storage.load_user_profile() or {}
    except Exception:
        return {}
    root = profile.get("mcp_adapter_cache") or {}
    versioned = root.get(ADAPTER_CONTRACT_VERSION) or {}
    return versioned if isinstance(versioned, dict) else {}


def _save_adapter_cache_to_profile(cache_payload: dict) -> None:
    try:
        profile = _storage.load_user_profile() or {}
        root = profile.get("mcp_adapter_cache") or {}
        root[ADAPTER_CONTRACT_VERSION] = dict(cache_payload or {})
        profile["mcp_adapter_cache"] = root
        _storage.save_user_profile(profile)
    except Exception:
        return


def cache_tool_response(tool_name: str, arguments: dict, response_text: str) -> None:
    """Persiste respuesta MCP en caché local por usuario para fallback frozen."""
    name = str(tool_name or "").strip()
    if name not in CRITICAL_CACHEABLE_TOOLS:
        return
    payload = str(response_text or "").strip()
    if not payload:
        return
    if payload.startswith("Error ") or "Error executing tool" in payload:
        return

    cache_key = _build_cache_key(name, arguments)
    cache = _load_adapter_cache_from_profile()
    tools_bucket = cache.get("tools") or {}
    per_tool = tools_bucket.get(name) or {}
    per_tool[cache_key] = {
        "response": payload,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }

    # Evita crecimiento indefinido: conserva solo las últimas 10 entradas por tool.
    if len(per_tool) > 10:
        ordered_items = sorted(
            per_tool.items(),
            key=lambda item: str((item[1] or {}).get("updated_at") or ""),
        )
        per_tool = dict(ordered_items[-10:])

    tools_bucket[name] = per_tool
    cache["tools"] = tools_bucket
    _save_adapter_cache_to_profile(cache)


def resolve_cached_tool_response(tool_name: str, arguments: dict, backend_effective: str | None) -> str | None:
    """Devuelve payload cacheado para tools críticas cuando el backend efectivo es frozen."""
    if str(backend_effective or "").strip().lower() != "frozen":
        return None

    name = str(tool_name or "").strip()
    if name not in CRITICAL_CACHEABLE_TOOLS:
        return None

    cache = _load_adapter_cache_from_profile()
    tools_bucket = cache.get("tools") or {}
    per_tool = tools_bucket.get(name) or {}
    entry = per_tool.get(_build_cache_key(name, arguments)) or {}
    payload = entry.get("response")
    text = str(payload) if payload else ""
    if not text:
        return None
    if text.startswith("Error ") or "Error executing tool" in text:
        return None
    return text


def _parse_iso_date(raw: object) -> date | None:
    iso = _load_metrics.to_iso_date(raw)
    if not iso:
        return None
    try:
        return date.fromisoformat(iso)
    except ValueError:
        return None


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


def resolve_local_fastpath_response(tool_name: str, arguments: dict, backend_effective: str | None) -> str | None:
    """Devuelve respuesta local para tools críticas en backend frozen, o None para usar MCP."""
    if str(backend_effective or "").strip().lower() != "frozen":
        return None

    if tool_name != "get_training_load_trend":
        return resolve_cached_tool_response(tool_name, arguments, backend_effective)

    start_d = _parse_iso_date(arguments.get("start_date"))
    end_d = _parse_iso_date(arguments.get("end_date"))
    if not start_d or not end_d:
        return resolve_cached_tool_response(tool_name, arguments, backend_effective)

    if end_d < start_d:
        start_d, end_d = end_d, start_d

    window_days = max(14, (end_d - start_d).days + 7)

    try:
        series = _storage.get_load_metrics_series(days=window_days)
    except Exception:
        return resolve_cached_tool_response(tool_name, arguments, backend_effective)

    if not isinstance(series, list):
        return resolve_cached_tool_response(tool_name, arguments, backend_effective)

    rows: list[dict] = []
    for row in series:
        d = _parse_iso_date((row or {}).get("date"))
        if not d or d < start_d or d > end_d:
            continue
        rows.append(
            {
                "date": d.isoformat(),
                "trainingLoad": float((row or {}).get("tss") or 0.0),
            }
        )

    rows.sort(key=lambda x: x["date"])
    if rows:
        return json.dumps(rows, ensure_ascii=False)

    return resolve_cached_tool_response(tool_name, arguments, backend_effective)
