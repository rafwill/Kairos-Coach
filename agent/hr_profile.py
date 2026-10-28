"""
agent/hr_profile.py
Zonas y umbrales de FC del atleta: zonas de Garmin por deporte (fuente única _resolve_hr_zones), umbral de FC fisiológico y el de TrainingPeaks por fecha, ritmo umbral por fecha, FC de reposo y FTP.
"""

import logging
import json
import asyncio
from datetime import date
from typing import Any


from agent.mcp.client import (
    call_tool,
)
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _resolve_hr_profile_values,
    _try_parse_json,
)
from agent.dates import (
    _parse_iso_date_safe,
)
from agent.data_access import (
    _save_user_profile,
)
from agent.garmin_data import (
    HR_ZONE_NAMES_ES,
    _key_candidates,
)

log = logging.getLogger(__name__)


def _extract_cycling_ftp_watts(payload: Any) -> float | None:
    """Extrae FTP de ciclismo (vatios) desde respuestas MCP heterogéneas."""
    if payload is None:
        return None

    candidate_keys = (
        "cyclingFtp",
        "cycling_ftp",
        "ftp",
        "functionalThresholdPower",
        "functional_threshold_power",
        "functional_threshold_power_watts",
    )

    def _to_positive_float(raw: Any) -> float | None:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None
        if val <= 0:
            return None
        return round(val, 1)

    if isinstance(payload, (int, float, str)):
        return _to_positive_float(payload)

    if isinstance(payload, list):
        for item in payload:
            ftp = _extract_cycling_ftp_watts(item)
            if ftp:
                return ftp
        return None

    if isinstance(payload, dict):
        for key in candidate_keys:
            if key in payload:
                ftp = _to_positive_float(payload.get(key))
                if ftp:
                    return ftp
        # Respuestas anidadas comunes de MCP/API wrappers
        for nested_key in ("data", "result", "profile", "performance", "userData"):
            if nested_key in payload:
                ftp = _extract_cycling_ftp_watts(payload.get(nested_key))
                if ftp:
                    return ftp
    return None


def _resolve_hr_threshold_bpm(profile: dict | None) -> tuple[float | None, str, str]:
    """Extrae FC umbral desde perfil en distintas convenciones de clave."""
    profile = profile if isinstance(profile, dict) else {}
    perf = profile.get("performance") if isinstance(profile.get("performance"), dict) else {}
    user_data = profile.get("userData") if isinstance(profile.get("userData"), dict) else {}
    data_node = profile.get("data") if isinstance(profile.get("data"), dict) else {}
    data_user = data_node.get("userData") if isinstance(data_node.get("userData"), dict) else {}
    result_node = profile.get("result") if isinstance(profile.get("result"), dict) else {}
    result_user = result_node.get("userData") if isinstance(result_node.get("userData"), dict) else {}
    candidates = (
        perf.get("hr_threshold_bpm"),
        perf.get("lthr_bpm"),
        perf.get("lactate_threshold_hr_bpm"),
        perf.get("lactate_threshold_heart_rate"),
        perf.get("threshold_heart_rate"),
        perf.get("hrAtLactateThreshold"),
        perf.get("heart_rate_threshold"),
        user_data.get("lactateThresholdHeartRate"),
        user_data.get("lactate_threshold_heart_rate"),
        user_data.get("thresholdHeartRate"),
        user_data.get("threshold_heart_rate"),
        data_user.get("lactateThresholdHeartRate"),
        data_user.get("lactate_threshold_heart_rate"),
        data_user.get("thresholdHeartRate"),
        data_user.get("threshold_heart_rate"),
        result_user.get("lactateThresholdHeartRate"),
        result_user.get("lactate_threshold_heart_rate"),
        result_user.get("thresholdHeartRate"),
        result_user.get("threshold_heart_rate"),
        profile.get("hr_threshold_bpm"),
        profile.get("lthr_bpm"),
    )
    for raw in candidates:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if 120 <= v <= 230:
            date_value = str(
                perf.get("hr_threshold_date")
                or perf.get("lthr_date")
                or perf.get("lactate_threshold_hr_date")
                or perf.get("performance_params_updated_at")
                or ""
            ).strip() or "sin fecha"
            return round(v, 1), date_value, "perfil persistido"
    return None, "", ""


def _resolve_profile_history_value(
    profile: dict | None,
    history_key: str,
    value_key: str,
    d_iso: str,
    valid_range: tuple[float, float],
) -> float | None:
    """Valor vigente en la fecha de un historial del perfil: performance[history_key] = [{date, value_key}]."""
    perf = (profile or {}).get("performance") if isinstance(profile, dict) else None
    history = perf.get(history_key) if isinstance(perf, dict) else None
    if not isinstance(history, list):
        return None
    lo, hi = valid_range
    vigente: tuple[str, float] | None = None
    for entry in history:
        if not isinstance(entry, dict):
            continue
        try:
            since = str(entry.get("date") or "")
            value = float(entry.get(value_key))
        except (TypeError, ValueError):
            continue
        if since and since <= d_iso and lo <= value <= hi and (vigente is None or since > vigente[0]):
            vigente = (since, value)
    return vigente[1] if vigente else None


def _resolve_tp_hr_threshold_for_date(profile: dict | None, d_iso: str) -> float | None:
    """Umbral de FC que tenía TrainingPeaks en la fecha (performance.hr_threshold_tp_history).

    Es la referencia con la que TP calcula su hrTSS; puede diferir del umbral fisiológico del perfil.
    """
    return _resolve_profile_history_value(profile, "hr_threshold_tp_history", "bpm", d_iso, (120, 230))


def _zones_from_floors(floors: list[int], top: int) -> list[tuple[str, int, int]]:
    """Zonas contiguas: cada una va de su suelo al de la siguiente menos 1; la última, hasta `top`."""
    uppers = [floors[i + 1] - 1 for i in range(len(floors) - 1)] + [top]
    return [(f"Z{i + 1} {HR_ZONE_NAMES_ES[i + 1]}", floors[i], uppers[i]) for i in range(len(floors))]


def _hr_zone_bounds_bpm(lthr: float) -> list[tuple[str, int, int]]:
    """Respaldo cuando no hay zonas de Garmin guardadas: % del LTHR como TrainingPeaks.

    Z1 < 85 % · Z2 85–89 % · Z3 90–94 % · Z4 95–99 % · Z5 ≥ 100 %, contiguas (antes 151 y 160 lpm
    no caían en ninguna zona por redondeo).
    """
    def _bpm(pct: float) -> int:
        return int(round(float(lthr) * pct / 100.0))

    return _zones_from_floors([0, _bpm(85), _bpm(90), _bpm(95), _bpm(100)], 250)


def _resolve_hr_zones(profile: dict | None, sport: str = "running") -> tuple[list[tuple[str, int, int]], str]:
    """Zonas de FC en lpm para el deporte y su fuente.

    Fuente de verdad: las zonas de Garmin Connect (las del reloj y TrainingPeaks), que Kairos lee con
    get_heart_rate_zones y guarda en performance.hr_zones_garmin. Sin ellas, % del LTHR del perfil.
    """
    perf = (profile or {}).get("performance") or {}
    stored = perf.get("hr_zones_garmin") or {}
    by_sport = {str(x.get("sport") or ""): x for x in stored.get("sports") or [] if isinstance(x, dict)}
    item = by_sport.get(sport) or by_sport.get("default")
    if item:
        zones = item.get("zones") or []
        floors = [int(z["min_bpm"]) for z in zones if z.get("min_bpm") is not None]
        top = item.get("max_hr") or (zones[-1].get("max_bpm") if zones else None) or 250
        if len(floors) == 5:
            method = {"HR_RESERVE": "reserva de FC", "LACTATE_THRESHOLD": "% del umbral",
                      "PERCENT_MAX_HR": "% de la FC máxima"}.get(str(item.get("method") or ""), str(item.get("method") or ""))
            return _zones_from_floors(floors, int(top)), f"Garmin ({method})" if method else "Garmin"
    lthr, _, _ = _resolve_hr_threshold_bpm(profile)
    if lthr:
        return _hr_zone_bounds_bpm(float(lthr)), "% del LTHR (sin zonas de Garmin guardadas)"
    return [], ""


def _normalize_garmin_hr_zones(payload: Any) -> dict | None:
    """Salida de get_heart_rate_zones → {"sports": [{sport, method, resting_hr, max_hr, lactate_threshold_hr, zones}]}."""
    data = _try_parse_json(payload) if isinstance(payload, str) else payload
    sports = data.get("sports") if isinstance(data, dict) else None
    clean = []
    for item in sports or []:
        zones = item.get("zones") if isinstance(item, dict) else None
        if not zones or len(zones) != 5 or any(z.get("min_bpm") is None for z in zones):
            continue
        clean.append({k: item.get(k) for k in ("sport", "method", "resting_hr", "max_hr", "lactate_threshold_hr", "zones")})
    return {"sports": sorted(clean, key=lambda x: str(x.get("sport") or ""))} if clean else None


def _resolve_running_threshold_pace_for_date(profile: dict | None, d_iso: str) -> float | None:
    """Ritmo umbral (s/km) vigente en la fecha (performance.running_threshold_pace_history)."""
    return _resolve_profile_history_value(profile, "running_threshold_pace_history", "sec_per_km", d_iso, (150, 600))


def _extract_hr_threshold_from_payload(payload: Any) -> float | None:
    """Busca FC umbral (LTHR) en payloads JSON heterogéneos."""
    target_keys = {
        "hr_threshold_bpm",
        "lthr_bpm",
        "lactate_threshold_hr_bpm",
        "lactate_threshold_heart_rate",
        "threshold_heart_rate",
        "hratlactatethreshold",
        "heartratethreshold",
        "hrthreshold",
        "lthr",
        "lactatethresholdheartrate",
        "lactatethresholdheart_rate",
        "user_lactate_threshold_heart_rate",
    }

    def _walk(node: Any) -> float | None:
        if isinstance(node, dict):
            for key, value in node.items():
                if _key_candidates(key) & target_keys:
                    try:
                        val = float(value)
                    except (TypeError, ValueError):
                        val = None
                    if val is not None and 120 <= val <= 230:
                        return round(val, 1)
                nested = _walk(value)
                if nested is not None:
                    return nested
            return None
        if isinstance(node, list):
            for item in node:
                nested = _walk(item)
                if nested is not None:
                    return nested
        return None

    return _walk(payload)


def _extract_resting_hr_from_payload(payload: Any) -> float | None:
    """Busca FC en reposo en payloads JSON heterogéneos."""
    target_keys = {
        "restingheartrate",
        "resting_heart_rate",
        "resting_hr",
        "hrrestingvalue",
        "wellness_resting_heart_rate",
    }

    def _coerce(raw: Any) -> float | None:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None
        if 30.0 <= val <= 100.0:
            return round(val, 1)
        return None

    def _walk(node: Any) -> float | None:
        if isinstance(node, dict):
            for key, value in node.items():
                if _key_candidates(key) & target_keys:
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                out = _coerce(item.get("value"))
                                if out is not None:
                                    return out
                        out = _coerce(value[0] if value else None)
                        if out is not None:
                            return out
                    elif isinstance(value, dict):
                        out = _coerce(value.get("value"))
                        if out is not None:
                            return out
                    else:
                        out = _coerce(value)
                        if out is not None:
                            return out
                nested = _walk(value)
                if nested is not None:
                    return nested
        elif isinstance(node, list):
            for item in node:
                nested = _walk(item)
                if nested is not None:
                    return nested
        return None

    return _walk(payload)


async def _hydrate_profile_hr_anchors_if_missing(mcp_session, profile: dict | None) -> tuple[float | None, float | None, float | None]:
    """Hidrata hr_rest/lthr desde MCP cuando faltan en el perfil persistido."""
    profile = profile if isinstance(profile, dict) else {}
    hr_rest_bpm, hr_max_bpm = _resolve_hr_profile_values(profile)
    hr_threshold_bpm, _, _ = _resolve_hr_threshold_bpm(profile)

    needs_rest = hr_rest_bpm is None
    needs_lthr = hr_threshold_bpm is None
    if not (needs_rest or needs_lthr):
        return hr_rest_bpm, hr_max_bpm, hr_threshold_bpm

    perf = profile.setdefault("performance", {}) if isinstance(profile, dict) else {}
    updated = False
    today_iso = date.today().isoformat()

    if needs_lthr:
        try:
            raw_lthr = await call_tool(mcp_session, "get_lactate_threshold", {})
            parsed_lthr = _try_parse_json(raw_lthr)
            payload_lthr = parsed_lthr if parsed_lthr is not None else raw_lthr
            live_lthr = _extract_hr_threshold_from_payload(payload_lthr)
        except (TimeoutError, asyncio.TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, KeyError, AssertionError):
            live_lthr = None

        if live_lthr is not None:
            perf["hr_threshold_bpm"] = int(round(float(live_lthr)))
            if not _parse_iso_date_safe(perf.get("hr_threshold_date")):
                perf["hr_threshold_date"] = today_iso
            perf["performance_params_updated_at"] = today_iso
            updated = True

    if needs_rest:
        try:
            raw_rhr = await call_tool(mcp_session, "get_rhr_day", {"date": today_iso})
            parsed_rhr = _try_parse_json(raw_rhr)
            payload_rhr = parsed_rhr if parsed_rhr is not None else raw_rhr
            live_rhr = _extract_resting_hr_from_payload(payload_rhr)
        except (TimeoutError, asyncio.TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, KeyError, AssertionError):
            live_rhr = None

        if live_rhr is not None:
            perf["hr_rest_bpm"] = float(live_rhr)
            perf["performance_params_updated_at"] = today_iso
            updated = True

    if updated:
        profile["performance"] = perf
        try:
            _save_user_profile(profile)
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            log.debug("No se pudo persistir anclajes HR en perfil: %s", exc)

    hr_rest_bpm, hr_max_bpm = _resolve_hr_profile_values(profile)
    hr_threshold_bpm, _, _ = _resolve_hr_threshold_bpm(profile)
    return hr_rest_bpm, hr_max_bpm, hr_threshold_bpm
