"""
garmin_mcp/server.py — servidor MCP de Garmin Connect para Kairos (solo lectura, stdio).

Independiente del host: no importa nada de `agent/`. Arranque: `python -m garmin_mcp`.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import date
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

from garminconnect import Garmin
from garminconnect.exceptions import GarminConnectAuthenticationError
from garminconnect.exceptions import GarminConnectConnectionError
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

mcp = FastMCP("kairos-garmin-mcp", instructions="Kairos local Garmin MCP server")

_client: Garmin | None = None
_client_identity: tuple[str, str] | None = None
_AUTH_LIKE_HTTP_CODES = {"401", "403"}

PASSTHROUGH_TOOLS: dict[str, str] = {
    "get_user_profile": "get_user_profile",
    "get_activities": "get_activities",
    "get_activity": "get_activity",
    "get_activity_details": "get_activity_details",
    "get_activity_hr_in_timezones": "get_activity_hr_in_timezones",
    "get_activities_by_date": "get_activities_by_date",
    "get_activity_splits": "get_activity_splits",
    "get_activity_exercise_sets": "get_activity_exercise_sets",
    "get_activity_power_in_timezones": "get_activity_power_in_timezones",
    "get_stats": "get_stats",
    "get_sleep_data": "get_sleep_data",
    "get_all_day_stress": "get_all_day_stress",
    "get_all_day_events": "get_all_day_events",
    "get_body_battery": "get_body_battery",
    "get_rhr_day": "get_rhr_day",
    "get_spo2_data": "get_spo2_data",
    "get_hrv_data": "get_hrv_data",
    "get_daily_steps": "get_daily_steps",
    "get_hydration_data": "get_hydration_data",
    "get_body_composition": "get_body_composition",
    "get_training_readiness": "get_training_readiness",
    "get_morning_training_readiness": "get_morning_training_readiness",
    "get_training_status": "get_training_status",
    "get_endurance_score": "get_endurance_score",
    "get_fitnessage_data": "get_fitnessage_data",
    "get_lactate_threshold": "get_lactate_threshold",
    "get_cycling_ftp": "get_cycling_ftp",
    "get_race_predictions": "get_race_predictions",
    "get_personal_record": "get_personal_record",
    "get_weekly_steps": "get_weekly_steps",
    "get_weekly_intensity_minutes": "get_weekly_intensity_minutes",
    "get_weekly_stress": "get_weekly_stress",
}


def _today_iso() -> str:
    return date.today().isoformat()


def _as_iso_day(raw: Any, default: str | None = None) -> str:
    if raw is None or raw == "":
        return default or _today_iso()
    try:
        return date.fromisoformat(str(raw)).isoformat()
    except ValueError:
        return default or _today_iso()


def _as_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def _iso_date_iter(start_iso: str, end_iso: str) -> list[str]:
    start_d = date.fromisoformat(start_iso)
    end_d = date.fromisoformat(end_iso)
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    days = min((end_d - start_d).days, 90)
    return [(start_d + timedelta(days=i)).isoformat() for i in range(days + 1)]


def _weekly_range(args: dict[str, Any]) -> tuple[str, str, int]:
    """Rango para las tools semanales: fin (por defecto hoy), inicio (por defecto 4 semanas antes) y nº de semanas."""
    end = _as_iso_day(args.get("end_date"))
    start = _as_iso_day(args.get("start_date"), default=(date.fromisoformat(end) - timedelta(days=27)).isoformat())
    if start > end:
        start, end = end, start
    weeks = max(1, ((date.fromisoformat(end) - date.fromisoformat(start)).days // 7) + 1)
    return start, end, weeks


def _load_value(payload: Any) -> float:
    if not isinstance(payload, dict):
        return 0.0
    for key in ("trainingLoad", "activityTrainingLoad", "tss", "trainingStressScore", "activityTss"):
        raw = payload.get(key)
        if raw not in (None, ""):
            try:
                return float(raw)
            except (ValueError, TypeError):
                continue
    return 0.0


def _activity_iso_day(activity: dict) -> str | None:
    if not isinstance(activity, dict):
        return None
    for key in ("startTimeLocal", "startTimeGMT", "startDate", "date"):
        raw = activity.get(key)
        if not raw:
            continue
        day = str(raw)[:10]
        try:
            return date.fromisoformat(day).isoformat()
        except ValueError:
            continue
    return None


def _garmin_client() -> Garmin:
    global _client
    global _client_identity

    import os

    email = (os.environ.get("GARMIN_EMAIL") or "").strip()
    password = (os.environ.get("GARMIN_PASSWORD") or "").strip()

    if not email or not password:
        raise RuntimeError("GARMIN_EMAIL/GARMIN_PASSWORD no configurados")

    identity = (email, password)
    if _client is not None and _client_identity == identity:
        return _client

    # Con tokenstore, garminconnect reutiliza los tokens guardados (y los refresca si caducan);
    # solo hace login con contraseña si faltan o no valen, y entonces los guarda para el siguiente
    # arranque. Sin él, cada proceso hacía un login completo y Garmin acaba devolviendo 429.
    client = Garmin(email=email, password=password)
    try:
        client.login(str(_tokenstore_dir(email)))
    except GarminConnectAuthenticationError:
        # Tokens guardados que Garmin ya no acepta (p. ej., tras cambiar la contraseña):
        # login() los carga y falla sin probar la contraseña. Se borran y se entra con ella.
        shutil.rmtree(_tokenstore_dir(email), ignore_errors=True)
        client = Garmin(email=email, password=password)
        client.login(str(_tokenstore_dir(email)))
    _client = client
    _client_identity = identity
    return client


def _tokenstore_dir(email: str) -> Path:
    """Carpeta de tokens de Garmin por usuario (GARMINTOKENS la sustituye si está definida)."""
    import os

    override = (os.environ.get("GARMINTOKENS") or "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        user_key = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]
        path = Path.home() / ".garminconnect" / "kairos" / user_key
    path.mkdir(parents=True, exist_ok=True)
    return path


def _invalidate_client_cache(clear_tokens: bool = False) -> None:
    """Olvida el cliente; con clear_tokens borra los tokens guardados para forzar un login nuevo."""
    global _client
    global _client_identity
    if clear_tokens and _client_identity is not None:
        shutil.rmtree(_tokenstore_dir(_client_identity[0]), ignore_errors=True)
    _client = None
    _client_identity = None


def _is_auth_like_connection_error(exc: Exception) -> bool:
    """True only when the error text explicitly reports API Error 401/403."""
    text = str(exc)
    match = re.search(r"API Error (\d{3})", text)
    if not match:
        return False
    code = match.group(1)
    if code == "429":
        return False
    return code in _AUTH_LIKE_HTTP_CODES


def _invoke_with_auth_retry(method_name: str, *args: Any, **kwargs: Any) -> Any:
    """Invoke a Garmin method and retry once only for auth-like failures."""
    client = _garmin_client()
    method = getattr(client, method_name)

    try:
        return method(*args, **kwargs)
    except GarminConnectAuthenticationError:
        # Tokens guardados que ya no valen: se borran para que el reintento haga login de verdad.
        _invalidate_client_cache(clear_tokens=True)
        client = _garmin_client()
        method = getattr(client, method_name)
        return method(*args, **kwargs)
    except GarminConnectConnectionError as exc:
        if not _is_auth_like_connection_error(exc):
            raise
        _invalidate_client_cache(clear_tokens=True)
        client = _garmin_client()
        method = getattr(client, method_name)
        return method(*args, **kwargs)


def _invoke_passthrough(tool_name: str, args: dict[str, Any]) -> Any:
    method_name = PASSTHROUGH_TOOLS[tool_name]

    if tool_name == "get_activities":
        return _invoke_with_auth_retry(method_name, _as_int(args.get("start", 0), 0), _as_int(args.get("limit", 50), 50))

    if tool_name == "get_activities_by_date":
        start = _as_iso_day(args.get("start_date") or args.get("startdate"))
        end = _as_iso_day(args.get("end_date") or args.get("enddate") or start, default=start)
        page = _as_int(args.get("page", 0), 0)
        page_size = _as_int(args.get("page_size", 100), 100)
        data = _invoke_with_auth_retry(method_name, start, end)
        if not isinstance(data, list):
            return data
        begin = max(0, page * page_size)
        end_idx = begin + page_size
        page_data = data[begin:end_idx]
        return {
            "count": len(page_data),
            "page": page,
            "page_size": page_size,
            "has_more": end_idx < len(data),
            "date_range": {"start": start, "end": end},
            "activities": page_data,
        }

    if tool_name in {
        "get_activity",
        "get_activity_details",
        "get_activity_hr_in_timezones",
        "get_activity_splits",
        "get_activity_exercise_sets",
        "get_activity_power_in_timezones",
    }:
        activity_id = args.get("activity_id")
        if activity_id in (None, ""):
            raise RuntimeError(f"{tool_name} requiere activity_id")
        return _invoke_with_auth_retry(method_name, int(activity_id))

    if tool_name == "get_stats":
        return _invoke_with_auth_retry(method_name, _as_iso_day(args.get("date")))

    if tool_name in {
        "get_sleep_data",
        "get_all_day_stress",
        "get_all_day_events",
        "get_rhr_day",
        "get_spo2_data",
        "get_hrv_data",
        "get_hydration_data",
    }:
        return _invoke_with_auth_retry(method_name, _as_iso_day(args.get("date")))

    if tool_name in {"get_body_battery", "get_body_composition"}:
        start = _as_iso_day(args.get("start_date") or args.get("date"))
        end = _as_iso_day(args.get("end_date") or args.get("date") or start, default=start)
        return _invoke_with_auth_retry(method_name, start, end)

    if tool_name in {"get_training_readiness", "get_morning_training_readiness"}:
        return _invoke_with_auth_retry(method_name, _as_iso_day(args.get("date")))

    # Firmas de garminconnect 0.3.2 (verificadas el 22/10/2026; antes estas tools fallaban siempre).
    if tool_name == "get_daily_steps":  # (start, end)
        day = _as_iso_day(args.get("date"))
        return _invoke_with_auth_retry(method_name, day, day)

    if tool_name in {"get_training_status", "get_fitnessage_data"}:  # (cdate)
        return _invoke_with_auth_retry(method_name, _as_iso_day(args.get("date")))

    if tool_name == "get_endurance_score":  # (startdate, enddate=None)
        start = _as_iso_day(args.get("start_date"))
        end = _as_iso_day(args.get("end_date") or start, default=start)
        return _invoke_with_auth_retry(method_name, start, end)

    if tool_name in {"get_weekly_steps", "get_weekly_stress"}:  # (end, weeks)
        start, end, weeks = _weekly_range(args)
        return _invoke_with_auth_retry(method_name, end, weeks)

    if tool_name == "get_weekly_intensity_minutes":  # (start, end)
        start, end, _weeks = _weekly_range(args)
        return _invoke_with_auth_retry(method_name, start, end)

    cleaned = {k: v for k, v in args.items() if v not in (None, "")}
    try:
        return _invoke_with_auth_retry(method_name, **cleaned)
    except TypeError:
        return _invoke_with_auth_retry(method_name)


def _training_load_trend(args: dict[str, Any]) -> dict[str, Any]:
    start = _as_iso_day(args.get("start_date") or args.get("date"))
    end = _as_iso_day(args.get("end_date") or args.get("date") or start, default=start)

    data = _invoke_with_auth_retry("get_activities_by_date", start, end)
    if not isinstance(data, list):
        return {"start_date": start, "end_date": end, "days_with_data": 0, "trend": []}

    totals: dict[str, float] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        day = _activity_iso_day(item)
        if not day:
            continue
        totals[day] = totals.get(day, 0.0) + _load_value(item)

    trend = [{"date": k, "trainingLoad": round(v, 2)} for k, v in sorted(totals.items())]
    return {"start_date": start, "end_date": end, "days_with_data": len(trend), "trend": trend}


def _hrv_trend(args: dict[str, Any]) -> dict[str, Any]:
    start = _as_iso_day(args.get("start_date") or args.get("date"))
    end = _as_iso_day(args.get("end_date") or args.get("date") or start, default=start)

    trend: list[dict[str, Any]] = []
    for day in _iso_date_iter(start, end):
        try:
            payload = _invoke_with_auth_retry("get_hrv_data", day)
        except Exception:
            continue

        # Garmin devuelve los valores del día dentro de hrvSummary (la raíz solo trae lecturas y
        # marcas de tiempo). Se acepta también la raíz por si algún día llega aplanado.
        summary: dict[str, Any] = {}
        if isinstance(payload, dict):
            nested = payload.get("hrvSummary")
            summary = nested if isinstance(nested, dict) else payload
        value = None
        for key in ("lastNightAvg", "last_night_avg_hrv_ms", "avgOvernightHrv", "averageHrv"):
            raw = summary.get(key)
            if raw not in (None, ""):
                value = raw
                break
        trend.append({
            "date": day,
            "hrv": value,
            "weekly_avg": summary.get("weeklyAvg"),
            "status": summary.get("status"),
        })

    return {
        "start_date": start,
        "end_date": end,
        "days_with_data": sum(1 for row in trend if row.get("hrv") not in (None, "")),
        "trend": trend,
    }


def _vo2max_trend(args: dict[str, Any]) -> dict[str, Any]:
    start = _as_iso_day(args.get("start_date") or args.get("date"))
    end = _as_iso_day(args.get("end_date") or args.get("date") or start, default=start)
    days = _iso_date_iter(start, end)
    sample = sorted(set(days[::7]) | {days[-1]})  # el VO2máx cambia despacio: una consulta por semana
    metrics: list[dict[str, Any]] = []
    for day in sample:
        try:
            metrics.append({"date": day, "metrics": _invoke_with_auth_retry("get_max_metrics", day)})
        except GarminConnectAuthenticationError:
            raise
        except Exception as exc:  # un día sin datos no invalida el resto del rango
            metrics.append({"date": day, "error": str(exc)[:120]})
    return {"start_date": start, "end_date": end, "sampled_every_days": 7, "metrics": metrics}


def _activities_fordate(args: dict[str, Any]) -> Any:
    if args.get("date"):
        return _invoke_with_auth_retry("get_activities_fordate", _as_iso_day(args.get("date")))

    start = _as_iso_day(args.get("startdate") or args.get("start_date"))
    end = _as_iso_day(args.get("enddate") or args.get("end_date") or start, default=start)
    return _invoke_with_auth_retry("get_activities_by_date", start, end)


def _heart_rates_summary(args: dict[str, Any]) -> dict[str, Any]:
    d = _as_iso_day(args.get("date"))
    payload = _invoke_with_auth_retry("get_heart_rates", d)

    out = {"date": d, "restingHeartRate": None, "min": None, "max": None, "avg": None}
    if isinstance(payload, dict):
        for src, dst in (
            ("restingHeartRate", "restingHeartRate"),
            ("restingHr", "restingHeartRate"),
            ("minHeartRate", "min"),
            ("maxHeartRate", "max"),
            ("averageHeartRate", "avg"),
        ):
            if payload.get(src) is not None:
                out[dst] = payload.get(src)
    return out


def _stress_summary(args: dict[str, Any]) -> dict[str, Any]:
    d = _as_iso_day(args.get("date"))
    return {"date": d, "stress": _invoke_with_auth_retry("get_stress_data", d)}


def _respiration_summary(args: dict[str, Any]) -> dict[str, Any]:
    d = _as_iso_day(args.get("date"))
    return {"date": d, "respiration": _invoke_with_auth_retry("get_respiration_data", d)}


def _training_effect(args: dict[str, Any]) -> dict[str, Any]:
    activity_id = args.get("activity_id")
    if activity_id in (None, ""):
        return {"message": "activity_id requerido para get_training_effect"}

    payload = _invoke_with_auth_retry("get_activity", int(activity_id))
    if not isinstance(payload, dict):
        return {"activity_id": int(activity_id), "training_effect": None}

    return {
        "activity_id": int(activity_id),
        "aerobicTrainingEffect": payload.get("aerobicTrainingEffect"),
        "anaerobicTrainingEffect": payload.get("anaerobicTrainingEffect"),
        "trainingEffectLabel": payload.get("trainingEffectLabel"),
    }


def _dispatch(tool_name: str, args: dict[str, Any]) -> Any:
    if tool_name == "get_training_load_trend":
        return _training_load_trend(args)
    if tool_name == "get_hrv_trend":
        return _hrv_trend(args)
    if tool_name == "get_vo2max_trend":
        return _vo2max_trend(args)
    if tool_name == "get_activities_fordate":
        return _activities_fordate(args)
    if tool_name == "get_heart_rates_summary":
        return _heart_rates_summary(args)
    if tool_name == "get_stress_summary":
        return _stress_summary(args)
    if tool_name == "get_respiration_summary":
        return _respiration_summary(args)
    if tool_name == "get_training_effect":
        return _training_effect(args)
    return _invoke_passthrough(tool_name, args)


# ─── Definición de las tools (MCP) ─────────────────────────────────────────────
# Todas son de solo lectura sobre Garmin Connect. Título, descripción y descripción de cada
# parámetro siguen la guía de estilo MCP de apiaddicts (mcps-style-guide); ver tools/lint_mcp.py.
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

Day = Annotated[str | None, Field(description="Día en formato AAAA-MM-DD. Por defecto, hoy.")]
StartDate = Annotated[str | None, Field(description="Inicio del rango, AAAA-MM-DD (incluido).")]
EndDate = Annotated[str | None, Field(description="Fin del rango, AAAA-MM-DD (incluido). Por defecto, igual que el inicio.")]
SingleDay = Annotated[str | None, Field(description="Alternativa a start_date/end_date para un solo día (AAAA-MM-DD).")]
ActivityId = Annotated[int, Field(description="ID de la actividad en Garmin (activityId), p. ej. de get_activities_by_date.")]


def _tool(name: str, title: str, description: str):
    return mcp.tool(name=name, title=title, description=description, annotations=_READ_ONLY)


@_tool("get_user_profile", "Perfil del usuario en Garmin",
       "Perfil del usuario en Garmin Connect: sexo, peso, altura, fecha de nacimiento y datos fisiológicos básicos.")
def get_user_profile():
    return _dispatch("get_user_profile", {})


@_tool("get_activities", "Últimas actividades",
       "Lista de actividades más recientes, de la más nueva a la más antigua (resumen de cada una: tipo, fecha, "
       "duración, distancia, FC). Para un rango de fechas usa get_activities_by_date.")
def get_activities(
    start: Annotated[int, Field(description="Posición desde la que empezar (0 = la más reciente).")] = 0,
    limit: Annotated[int, Field(description="Número máximo de actividades a devolver.")] = 50,
):
    return _dispatch("get_activities", {"start": start, "limit": limit})


@_tool("get_activity", "Resumen de una actividad",
       "Resumen completo de una actividad: duración, distancia, ritmo, FC, desnivel, potencia, calorías y "
       "Training Effect (las métricas vienen en summaryDTO).")
def get_activity(activity_id: ActivityId):
    return _dispatch("get_activity", {"activity_id": activity_id})


@_tool("get_activity_details", "Detalle segundo a segundo",
       "Series temporales de una actividad (velocidad, FC, altitud, distancia, potencia…) a lo largo de la sesión. "
       "Respuesta grande: úsala solo si necesitas el detalle por tramos.")
def get_activity_details(activity_id: ActivityId):
    return _dispatch("get_activity_details", {"activity_id": activity_id})


@_tool("get_activity_hr_in_timezones", "Tiempo por zonas de FC",
       "Tiempo pasado en cada zona de frecuencia cardiaca durante una actividad, con los límites de zona de Garmin.")
def get_activity_hr_in_timezones(activity_id: ActivityId):
    return _dispatch("get_activity_hr_in_timezones", {"activity_id": activity_id})


@_tool("get_activities_by_date", "Actividades de un rango de fechas",
       "Actividades entre dos fechas, paginadas (count, page, has_more, activities). Es la forma recomendada de "
       "localizar actividades por fecha.")
def get_activities_by_date(
    start_date: StartDate = None,
    end_date: EndDate = None,
    startdate: Annotated[str | None, Field(description="Alias de start_date (compatibilidad).")] = None,
    enddate: Annotated[str | None, Field(description="Alias de end_date (compatibilidad).")] = None,
    page: Annotated[int, Field(description="Página a devolver, empezando en 0.")] = 0,
    page_size: Annotated[int, Field(description="Actividades por página.")] = 100,
):
    return _dispatch(
        "get_activities_by_date",
        {
            "start_date": start_date,
            "end_date": end_date,
            "startdate": startdate,
            "enddate": enddate,
            "page": page,
            "page_size": page_size,
        },
    )


@_tool("get_activities_fordate", "Actividades de un día",
       "Actividades de un día concreto (con date) o de un rango (con startdate/enddate).")
def get_activities_fordate(
    date: Day = None,
    startdate: Annotated[str | None, Field(description="Inicio del rango, AAAA-MM-DD (si no se da date).")] = None,
    enddate: Annotated[str | None, Field(description="Fin del rango, AAAA-MM-DD (si no se da date).")] = None,
):
    return _dispatch("get_activities_fordate", {"date": date, "startdate": startdate, "enddate": enddate})


@_tool("get_activity_splits", "Parciales (laps) de una actividad",
       "Parciales o vueltas de una actividad: distancia, tiempo, ritmo, FC y desnivel de cada lap.")
def get_activity_splits(activity_id: ActivityId):
    return _dispatch("get_activity_splits", {"activity_id": activity_id})


@_tool("get_activity_exercise_sets", "Series de una sesión de fuerza",
       "Ejercicios, series, repeticiones y pesos registrados en una actividad de fuerza.")
def get_activity_exercise_sets(activity_id: ActivityId):
    return _dispatch("get_activity_exercise_sets", {"activity_id": activity_id})


@_tool("get_activity_power_in_timezones", "Tiempo por zonas de potencia",
       "Tiempo pasado en cada zona de potencia durante una actividad (solo con potenciómetro).")
def get_activity_power_in_timezones(activity_id: ActivityId):
    return _dispatch("get_activity_power_in_timezones", {"activity_id": activity_id})


@_tool("get_stats", "Resumen diario",
       "Resumen del día: pasos, calorías, FC en reposo, estrés medio, minutos de intensidad y Body Battery.")
def get_stats(date: Day = None):
    return _dispatch("get_stats", {"date": date})


@_tool("get_sleep_data", "Sueño (completo)",
       "Datos completos del sueño de la noche que termina en la fecha: fases, puntuación, respiración y SpO2. "
       "Para un resumen compacto usa get_sleep_summary.")
def get_sleep_data(date: Day = None):
    return _dispatch("get_sleep_data", {"date": date})


@_tool("get_heart_rates_summary", "Resumen de FC del día",
       "FC en reposo, mínima, máxima y media del día.")
def get_heart_rates_summary(date: Day = None):
    return _dispatch("get_heart_rates_summary", {"date": date})


@_tool("get_stress_summary", "Estrés del día",
       "Estrés del día según Garmin (media, máximo y reparto por niveles).")
def get_stress_summary(date: Day = None):
    return _dispatch("get_stress_summary", {"date": date})


@_tool("get_respiration_summary", "Respiración del día",
       "Frecuencia respiratoria del día y de la noche (media, mínima y máxima).")
def get_respiration_summary(date: Day = None):
    return _dispatch("get_respiration_summary", {"date": date})


@_tool("get_all_day_stress", "Estrés a lo largo del día",
       "Serie de estrés y Body Battery a lo largo de todo el día.")
def get_all_day_stress(date: Day = None):
    return _dispatch("get_all_day_stress", {"date": date})


@_tool("get_all_day_events", "Eventos del día",
       "Eventos registrados durante el día (actividades, siestas, periodos de estrés o de recuperación).")
def get_all_day_events(date: Day = None):
    return _dispatch("get_all_day_events", {"date": date})


@_tool("get_body_battery", "Body Battery",
       "Body Battery por día: nivel, cargado, drenado y serie a lo largo del día.")
def get_body_battery(start_date: StartDate = None, end_date: EndDate = None, date: SingleDay = None):
    return _dispatch("get_body_battery", {"start_date": start_date, "end_date": end_date, "date": date})


@_tool("get_rhr_day", "FC en reposo",
       "Frecuencia cardiaca en reposo del día.")
def get_rhr_day(date: Day = None):
    return _dispatch("get_rhr_day", {"date": date})


@_tool("get_spo2_data", "SpO2",
       "Saturación de oxígeno en sangre (SpO2) del día y de la noche.")
def get_spo2_data(date: Day = None):
    return _dispatch("get_spo2_data", {"date": date})


@_tool("get_hrv_data", "HRV de la noche",
       "Variabilidad de la frecuencia cardiaca (HRV) de la noche que termina en la fecha: media nocturna, media "
       "semanal, estado (BALANCED, UNBALANCED…) y línea base.")
def get_hrv_data(date: Day = None):
    return _dispatch("get_hrv_data", {"date": date})


@_tool("get_daily_steps", "Pasos del día",
       "Pasos del día y objetivo de pasos.")
def get_daily_steps(date: Day = None):
    return _dispatch("get_daily_steps", {"date": date})


@_tool("get_hydration_data", "Hidratación",
       "Hidratación registrada en el día (consumo y objetivo).")
def get_hydration_data(date: Day = None):
    return _dispatch("get_hydration_data", {"date": date})


@_tool("get_body_composition", "Composición corporal",
       "Peso y composición corporal (grasa, masa muscular, IMC) registrados en el rango.")
def get_body_composition(start_date: StartDate = None, end_date: EndDate = None, date: SingleDay = None):
    return _dispatch("get_body_composition", {"start_date": start_date, "end_date": end_date, "date": date})


@_tool("get_training_readiness", "Training Readiness",
       "Disposición para entrenar de Garmin (0–100) y sus factores (sueño, recuperación, HRV, carga aguda). "
       "Indicador de Garmin: complementa, no sustituye, el estado de carga de Kairos.")
def get_training_readiness(date: Day = None):
    return _dispatch("get_training_readiness", {"date": date})


@_tool("get_morning_training_readiness", "Training Readiness al despertar",
       "Training Readiness calculado al despertar (el valor de la mañana, sin la evolución del día).")
def get_morning_training_readiness(date: Day = None):
    return _dispatch("get_morning_training_readiness", {"date": date})


@_tool("get_training_status", "Estado de entrenamiento de Garmin",
       "Estado de entrenamiento según Garmin (productivo, mantenimiento, sobrecarga…), VO2máx y carga aguda de "
       "Garmin. Es la valoración de Garmin, no el estado de carga de Kairos.")
def get_training_status(date: Day = None):
    return _dispatch("get_training_status", {"date": date})


@_tool("get_training_load_trend", "Carga Garmin por día (no TSS)",
       "Carga nativa de Garmin por día (suma de activityTrainingLoad de sus actividades). NO es TSS y no sirve "
       "para CTL/ATL/TSB: está en otra escala (×0.07 a ×2 frente al TSS). Para el TSS y la serie CTL/ATL/TSB de "
       "Kairos usa kairos_load_trends. Si la citas, llámala 'Carga Garmin'.")
def get_training_load_trend(start_date: StartDate = None, end_date: EndDate = None, date: SingleDay = None):
    return _dispatch("get_training_load_trend", {"start_date": start_date, "end_date": end_date, "date": date})


@_tool("get_training_effect", "Training Effect de una actividad",
       "Training Effect aeróbico y anaeróbico de una actividad (0–5) y su etiqueta (AEROBIC_BASE, TEMPO, VO2MAX…).")
def get_training_effect(
    activity_id: Annotated[int | None, Field(description="ID de la actividad en Garmin (activityId).")] = None,
):
    return _dispatch("get_training_effect", {"activity_id": activity_id})


@_tool("get_hrv_trend", "Tendencia de HRV",
       "HRV nocturna día a día en un rango (máximo 90 días).")
def get_hrv_trend(start_date: StartDate = None, end_date: EndDate = None, date: SingleDay = None):
    return _dispatch("get_hrv_trend", {"start_date": start_date, "end_date": end_date, "date": date})


@_tool("get_vo2max_trend", "Tendencia de VO2máx",
       "VO2máx estimado por Garmin (carrera y ciclismo) en un rango de fechas, muestreado cada 7 días más el "
       "último día del rango (cambia despacio).")
def get_vo2max_trend(start_date: StartDate = None, end_date: EndDate = None, date: SingleDay = None):
    return _dispatch("get_vo2max_trend", {"start_date": start_date, "end_date": end_date, "date": date})


@_tool("get_endurance_score", "Endurance Score",
       "Puntuación de resistencia de Garmin (Endurance Score) por día en el rango, y su clasificación.")
def get_endurance_score(start_date: StartDate = None, end_date: EndDate = None):
    return _dispatch("get_endurance_score", {"start_date": start_date, "end_date": end_date})


@_tool("get_fitnessage_data", "Edad física",
       "Edad física (Fitness Age) estimada por Garmin y los factores que la determinan.")
def get_fitnessage_data(date: Day = None):
    return _dispatch("get_fitnessage_data", {"date": date})


@_tool("get_lactate_threshold", "Umbral de lactato de Garmin",
       "Umbral de lactato estimado por Garmin (velocidad y FC). Solo informativo: el ritmo y la FC umbral que "
       "usa Kairos para zonas, TSS y planes son los del perfil de Kairos.")
def get_lactate_threshold():
    return _dispatch("get_lactate_threshold", {})


@_tool("get_cycling_ftp", "FTP de ciclismo en Garmin",
       "FTP de ciclismo registrado en Garmin (vatios).")
def get_cycling_ftp():
    return _dispatch("get_cycling_ftp", {})


@_tool("get_race_predictions", "Predicciones de carrera",
       "Tiempos previstos por Garmin para 5K, 10K, media maratón y maratón.")
def get_race_predictions():
    return _dispatch("get_race_predictions", {})


@_tool("get_personal_record", "Récords personales",
       "Récords personales del usuario en Garmin (distancias de carrera, ciclismo, natación…), con su fecha.")
def get_personal_record():
    return _dispatch("get_personal_record", {})


@_tool("get_weekly_steps", "Pasos por semana",
       "Pasos acumulados por semana en el rango. Por defecto, las 4 últimas semanas.")
def get_weekly_steps(start_date: StartDate = None, end_date: EndDate = None):
    return _dispatch("get_weekly_steps", {"start_date": start_date, "end_date": end_date})


@_tool("get_weekly_intensity_minutes", "Minutos de intensidad por semana",
       "Minutos de intensidad moderada y vigorosa por semana en el rango, con el objetivo semanal. Por defecto, "
       "las 4 últimas semanas.")
def get_weekly_intensity_minutes(start_date: StartDate = None, end_date: EndDate = None):
    return _dispatch("get_weekly_intensity_minutes", {"start_date": start_date, "end_date": end_date})


@_tool("get_weekly_stress", "Estrés por semana",
       "Estrés medio por semana en el rango. Por defecto, las 4 últimas semanas.")
def get_weekly_stress(start_date: StartDate = None, end_date: EndDate = None):
    return _dispatch("get_weekly_stress", {"start_date": start_date, "end_date": end_date})


@_tool("get_sleep_summary", "Sueño (resumen)",
       "Resumen del sueño de la noche que termina en la fecha: duración total, puntuación y segundos en sueño "
       "profundo, ligero, REM y despierto.")
def get_sleep_summary(date: Day = None):
    d = _as_iso_day(date)
    payload = _invoke_with_auth_retry("get_sleep_data", d)
    if not isinstance(payload, dict):
        return {"date": d, "sleep": payload}

    dto = payload.get("dailySleepDTO") or payload
    score_nested = (dto.get("sleepScores") or {}).get("overall") or {}
    sleep_seconds = dto.get("sleepTimeSeconds") or dto.get("sleep_time_seconds")
    score = dto.get("sleepScore") or dto.get("sleepScoreValue") or score_nested.get("value")

    return {
        "date": d,
        "sleepTimeSeconds": sleep_seconds,
        "sleepScore": score,
        "deepSleepSeconds": dto.get("deepSleepSeconds") or dto.get("deep_sleep_seconds"),
        "lightSleepSeconds": dto.get("lightSleepSeconds") or dto.get("light_sleep_seconds"),
        "remSleepSeconds": dto.get("remSleepSeconds") or dto.get("rem_sleep_seconds"),
        "awakeSleepSeconds": dto.get("awakeSleepSeconds") or dto.get("wakeSeconds"),
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
