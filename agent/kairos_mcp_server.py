"""
kairos_mcp_server.py
Servidor MCP propio de Kairos para consultas Garmin (sin dependencia de runtime en upstream).
"""

from __future__ import annotations

from datetime import date
from datetime import timedelta
from typing import Any

from garminconnect import Garmin
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("kairos-garmin-mcp", instructions="Kairos local Garmin MCP server")

_client: Garmin | None = None
_client_identity: tuple[str, str] | None = None

PASSTHROUGH_TOOLS: dict[str, str] = {
    "get_user_profile": "get_user_profile",
    "get_activities": "get_activities",
    "get_activity": "get_activity",
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

    client = Garmin(email=email, password=password)
    client.login()
    _client = client
    _client_identity = identity
    return client


def _invoke_passthrough(tool_name: str, args: dict[str, Any]) -> Any:
    client = _garmin_client()
    method_name = PASSTHROUGH_TOOLS[tool_name]
    method = getattr(client, method_name)

    if tool_name == "get_activities":
        return method(_as_int(args.get("start", 0), 0), _as_int(args.get("limit", 50), 50))

    if tool_name == "get_activities_by_date":
        start = _as_iso_day(args.get("start_date") or args.get("startdate"))
        end = _as_iso_day(args.get("end_date") or args.get("enddate") or start, default=start)
        page = _as_int(args.get("page", 0), 0)
        page_size = _as_int(args.get("page_size", 100), 100)
        data = method(start, end)
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
        "get_activity_hr_in_timezones",
        "get_activity_splits",
        "get_activity_exercise_sets",
        "get_activity_power_in_timezones",
    }:
        activity_id = args.get("activity_id")
        if activity_id in (None, ""):
            raise RuntimeError(f"{tool_name} requiere activity_id")
        return method(int(activity_id))

    if tool_name == "get_stats":
        return method(_as_iso_day(args.get("date")))

    if tool_name in {
        "get_sleep_data",
        "get_all_day_stress",
        "get_all_day_events",
        "get_rhr_day",
        "get_spo2_data",
        "get_hrv_data",
        "get_daily_steps",
        "get_hydration_data",
    }:
        return method(_as_iso_day(args.get("date")))

    if tool_name in {"get_body_battery", "get_body_composition"}:
        start = _as_iso_day(args.get("start_date") or args.get("date"))
        end = _as_iso_day(args.get("end_date") or args.get("date") or start, default=start)
        return method(start, end)

    if tool_name in {"get_training_readiness", "get_morning_training_readiness"}:
        return method(_as_iso_day(args.get("date")))

    cleaned = {k: v for k, v in args.items() if v not in (None, "")}
    try:
        return method(**cleaned)
    except TypeError:
        return method()


def _training_load_trend(args: dict[str, Any]) -> dict[str, Any]:
    client = _garmin_client()
    start = _as_iso_day(args.get("start_date") or args.get("date"))
    end = _as_iso_day(args.get("end_date") or args.get("date") or start, default=start)

    data = client.get_activities_by_date(start, end)
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
    client = _garmin_client()
    start = _as_iso_day(args.get("start_date") or args.get("date"))
    end = _as_iso_day(args.get("end_date") or args.get("date") or start, default=start)

    trend: list[dict[str, Any]] = []
    for day in _iso_date_iter(start, end):
        try:
            payload = client.get_hrv_data(day)
        except Exception:
            continue

        value = None
        if isinstance(payload, dict):
            for key in ("last_night_avg_hrv_ms", "lastNightAvg", "avgOvernightHrv", "averageHrv"):
                raw = payload.get(key)
                if raw not in (None, ""):
                    value = raw
                    break
        trend.append({"date": day, "hrv": value})

    return {
        "start_date": start,
        "end_date": end,
        "days_with_data": sum(1 for row in trend if row.get("hrv") not in (None, "")),
        "trend": trend,
    }


def _vo2max_trend(args: dict[str, Any]) -> dict[str, Any]:
    client = _garmin_client()
    start = _as_iso_day(args.get("start_date") or args.get("date"))
    end = _as_iso_day(args.get("end_date") or args.get("date") or start, default=start)
    payload = client.get_max_metrics(start, end)
    return {"start_date": start, "end_date": end, "metrics": payload}


def _activities_fordate(args: dict[str, Any]) -> Any:
    client = _garmin_client()
    if args.get("date"):
        return client.get_activities_fordate(_as_iso_day(args.get("date")))

    start = _as_iso_day(args.get("startdate") or args.get("start_date"))
    end = _as_iso_day(args.get("enddate") or args.get("end_date") or start, default=start)
    return client.get_activities_by_date(start, end)


def _heart_rates_summary(args: dict[str, Any]) -> dict[str, Any]:
    client = _garmin_client()
    d = _as_iso_day(args.get("date"))
    payload = client.get_heart_rates(d)

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
    client = _garmin_client()
    d = _as_iso_day(args.get("date"))
    return {"date": d, "stress": client.get_stress_data(d)}


def _respiration_summary(args: dict[str, Any]) -> dict[str, Any]:
    client = _garmin_client()
    d = _as_iso_day(args.get("date"))
    return {"date": d, "respiration": client.get_respiration_data(d)}


def _training_effect(args: dict[str, Any]) -> dict[str, Any]:
    client = _garmin_client()
    activity_id = args.get("activity_id")
    if activity_id in (None, ""):
        return {"message": "activity_id requerido para get_training_effect"}

    payload = client.get_activity(int(activity_id))
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


@mcp.tool(name="get_user_profile")
def get_user_profile():
    return _dispatch("get_user_profile", {})


@mcp.tool(name="get_activities")
def get_activities(start: int = 0, limit: int = 50):
    return _dispatch("get_activities", {"start": start, "limit": limit})


@mcp.tool(name="get_activity")
def get_activity(activity_id: int):
    return _dispatch("get_activity", {"activity_id": activity_id})


@mcp.tool(name="get_activity_hr_in_timezones")
def get_activity_hr_in_timezones(activity_id: int):
    return _dispatch("get_activity_hr_in_timezones", {"activity_id": activity_id})


@mcp.tool(name="get_activities_by_date")
def get_activities_by_date(
    start_date: str | None = None,
    end_date: str | None = None,
    startdate: str | None = None,
    enddate: str | None = None,
    page: int = 0,
    page_size: int = 100,
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


@mcp.tool(name="get_activities_fordate")
def get_activities_fordate(date: str | None = None, startdate: str | None = None, enddate: str | None = None):
    return _dispatch("get_activities_fordate", {"date": date, "startdate": startdate, "enddate": enddate})


@mcp.tool(name="get_activity_splits")
def get_activity_splits(activity_id: int):
    return _dispatch("get_activity_splits", {"activity_id": activity_id})


@mcp.tool(name="get_activity_exercise_sets")
def get_activity_exercise_sets(activity_id: int):
    return _dispatch("get_activity_exercise_sets", {"activity_id": activity_id})


@mcp.tool(name="get_activity_power_in_timezones")
def get_activity_power_in_timezones(activity_id: int):
    return _dispatch("get_activity_power_in_timezones", {"activity_id": activity_id})


@mcp.tool(name="get_stats")
def get_stats(date: str | None = None):
    return _dispatch("get_stats", {"date": date})


@mcp.tool(name="get_sleep_data")
def get_sleep_data(date: str | None = None):
    return _dispatch("get_sleep_data", {"date": date})


@mcp.tool(name="get_heart_rates_summary")
def get_heart_rates_summary(date: str | None = None):
    return _dispatch("get_heart_rates_summary", {"date": date})


@mcp.tool(name="get_stress_summary")
def get_stress_summary(date: str | None = None):
    return _dispatch("get_stress_summary", {"date": date})


@mcp.tool(name="get_respiration_summary")
def get_respiration_summary(date: str | None = None):
    return _dispatch("get_respiration_summary", {"date": date})


@mcp.tool(name="get_all_day_stress")
def get_all_day_stress(date: str | None = None):
    return _dispatch("get_all_day_stress", {"date": date})


@mcp.tool(name="get_all_day_events")
def get_all_day_events(date: str | None = None):
    return _dispatch("get_all_day_events", {"date": date})


@mcp.tool(name="get_body_battery")
def get_body_battery(start_date: str | None = None, end_date: str | None = None, date: str | None = None):
    return _dispatch("get_body_battery", {"start_date": start_date, "end_date": end_date, "date": date})


@mcp.tool(name="get_rhr_day")
def get_rhr_day(date: str | None = None):
    return _dispatch("get_rhr_day", {"date": date})


@mcp.tool(name="get_spo2_data")
def get_spo2_data(date: str | None = None):
    return _dispatch("get_spo2_data", {"date": date})


@mcp.tool(name="get_hrv_data")
def get_hrv_data(date: str | None = None):
    return _dispatch("get_hrv_data", {"date": date})


@mcp.tool(name="get_daily_steps")
def get_daily_steps(date: str | None = None):
    return _dispatch("get_daily_steps", {"date": date})


@mcp.tool(name="get_hydration_data")
def get_hydration_data(date: str | None = None):
    return _dispatch("get_hydration_data", {"date": date})


@mcp.tool(name="get_body_composition")
def get_body_composition(start_date: str | None = None, end_date: str | None = None, date: str | None = None):
    return _dispatch("get_body_composition", {"start_date": start_date, "end_date": end_date, "date": date})


@mcp.tool(name="get_training_readiness")
def get_training_readiness(date: str | None = None):
    return _dispatch("get_training_readiness", {"date": date})


@mcp.tool(name="get_morning_training_readiness")
def get_morning_training_readiness(date: str | None = None):
    return _dispatch("get_morning_training_readiness", {"date": date})


@mcp.tool(name="get_training_status")
def get_training_status():
    return _dispatch("get_training_status", {})


@mcp.tool(name="get_training_load_trend")
def get_training_load_trend(start_date: str | None = None, end_date: str | None = None, date: str | None = None):
    return _dispatch("get_training_load_trend", {"start_date": start_date, "end_date": end_date, "date": date})


@mcp.tool(name="get_training_effect")
def get_training_effect(activity_id: int | None = None):
    return _dispatch("get_training_effect", {"activity_id": activity_id})


@mcp.tool(name="get_hrv_trend")
def get_hrv_trend(start_date: str | None = None, end_date: str | None = None, date: str | None = None):
    return _dispatch("get_hrv_trend", {"start_date": start_date, "end_date": end_date, "date": date})


@mcp.tool(name="get_vo2max_trend")
def get_vo2max_trend(start_date: str | None = None, end_date: str | None = None, date: str | None = None):
    return _dispatch("get_vo2max_trend", {"start_date": start_date, "end_date": end_date, "date": date})


@mcp.tool(name="get_endurance_score")
def get_endurance_score():
    return _dispatch("get_endurance_score", {})


@mcp.tool(name="get_fitnessage_data")
def get_fitnessage_data():
    return _dispatch("get_fitnessage_data", {})


@mcp.tool(name="get_lactate_threshold")
def get_lactate_threshold():
    return _dispatch("get_lactate_threshold", {})


@mcp.tool(name="get_cycling_ftp")
def get_cycling_ftp():
    return _dispatch("get_cycling_ftp", {})


@mcp.tool(name="get_race_predictions")
def get_race_predictions():
    return _dispatch("get_race_predictions", {})


@mcp.tool(name="get_personal_record")
def get_personal_record():
    return _dispatch("get_personal_record", {})


@mcp.tool(name="get_weekly_steps")
def get_weekly_steps(start_date: str | None = None, end_date: str | None = None):
    return _dispatch("get_weekly_steps", {"start_date": start_date, "end_date": end_date})


@mcp.tool(name="get_weekly_intensity_minutes")
def get_weekly_intensity_minutes(start_date: str | None = None, end_date: str | None = None):
    return _dispatch("get_weekly_intensity_minutes", {"start_date": start_date, "end_date": end_date})


@mcp.tool(name="get_weekly_stress")
def get_weekly_stress(start_date: str | None = None, end_date: str | None = None):
    return _dispatch("get_weekly_stress", {"start_date": start_date, "end_date": end_date})


@mcp.tool(name="get_sleep_summary")
def get_sleep_summary(date: str | None = None):
    d = _as_iso_day(date)
    payload = _garmin_client().get_sleep_data(d)
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
