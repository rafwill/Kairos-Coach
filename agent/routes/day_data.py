"""
agent/routes/day_data.py
Ruta de datos de un día (mcp_factual): sueño, HRV, body battery, FC en reposo, estrés, pasos y TSS de Kairos de una fecha, y respaldo de recuperación cuando Garmin no da disposición.
"""

import logging
import json
import asyncio
import re
from datetime import date, timedelta
from typing import Any

from mcp import ClientSession

from agent.mcp.client import (
    call_tool,
)
from agent import storage as _storage
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _extract_activity_native_load,
    _extract_training_load_points,
    _find_hr_zones_in_json,
    _parse_hr_zones_list,
    _resolve_hr_profile_values,
    _try_parse_json,
)
from agent.dates import (
    _extract_iso_date_from_text,
    _resolve_target_date_from_message,
)
from agent.tool_results import (
    _activity_id_int,
    _compact_tool_result,
    _extract_activities_list,
    _flatten_garmin_activity,
    _is_no_data_result,
)
from agent.data_access import (
    _load_metrics_series,
    _load_stored_activity_tss,
)
from agent.garmin_data import (
    _activity_matches_sport,
    _extract_activity_date_iso,
    _get_activity_name_es,
)
from agent.wellness import (
    _build_wellness_payloads_from_row,
    _format_all_day_events,
    _format_body_battery_day,
    _format_hrv_day,
    _format_rhr_day,
    _format_sleep_day,
    _format_stress_day,
    _format_training_effect,
)
from agent.activity_analysis import (
    _build_activity_analysis_block,
    _format_activity_analysis_for_markdown,
)
from agent.router import (
    _is_activity_details_query_intent,
)

log = logging.getLogger(__name__)


async def _build_mcp_factual_query_markdown(mcp_session, profile: dict, user_message: str) -> str:
    """Resuelve consultas factuales diarias con MCP como fuente principal."""
    target_d = _resolve_target_date_from_message(user_message)
    msg_low = (user_message or "").lower()
    has_explicit_date = _extract_iso_date_from_text(user_message) is not None
    is_last_activity_query = bool(re.search(
        r"\b(última|ultima|último|ultimo|reciente|mas reciente|más reciente)\s*(actividad|entreno|entrenamiento|salida|sesión|sesion|trail|carrera|running|bici)\b",
        msg_low,
    ))
    sport_filter: str | None = None
    if is_last_activity_query:
        if re.search(r"\btrail\b", msg_low):
            sport_filter = "trail"
        elif re.search(r"\brunning\b|\bcarrera\b", msg_low):
            sport_filter = "running"
        elif re.search(r"\bbici\b|\bciclismo\b|\bcycling\b", msg_low):
            sport_filter = "cycling"

    # Si el usuario pide "última actividad" sin fecha explícita,
    # resolvemos la fecha real desde el listado reciente de Garmin.
    if is_last_activity_query and not has_explicit_date:
        recent_acts: list[dict] = []
        end_recent = date.today().isoformat()
        start_recent = (date.today() - timedelta(days=30)).isoformat()
        attempts = (
            (
                "get_activities_by_date",
                {
                    "start_date": start_recent,
                    "end_date": end_recent,
                    "page": 0,
                    "page_size": 200,
                },
            ),
            ("get_activities", {"start": "0", "limit": "60"}),
        )
        for tool_name, args in attempts:
            try:
                raw_recent = await call_tool(mcp_session, tool_name, args)
            except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, asyncio.TimeoutError, json.JSONDecodeError, KeyError):
                continue
            recent_acts = _extract_activities_list(_try_parse_json(raw_recent) or raw_recent)
            # Con filtro de deporte, solo vale si hay alguna de ese deporte; si no, se prueba el siguiente
            # intento (24/10: la última bici, de hace 73 días, quedaba fuera de los 30 días del primero).
            if recent_acts and (not sport_filter or any(_activity_matches_sport(a, sport_filter) for a in recent_acts)):
                break

        latest_candidate_iso: str | None = None
        for act in recent_acts:
            if not isinstance(act, dict):
                continue
            if sport_filter and not _activity_matches_sport(act, sport_filter):
                continue
            iso = _extract_activity_date_iso(act)
            if not iso:
                continue
            if latest_candidate_iso is None or iso > latest_candidate_iso:
                latest_candidate_iso = iso

        # Fallback robusto para consultas "última ..." con filtro de deporte:
        # escanea días recientes hasta encontrar una actividad válida.
        if latest_candidate_iso is None and sport_filter:
            for offset in range(0, 45):
                probe_d = date.today() - timedelta(days=offset)
                probe_iso = probe_d.isoformat()
                try:
                    raw_probe = await call_tool(mcp_session, "get_activities_fordate", {"date": probe_iso})
                except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, asyncio.TimeoutError, json.JSONDecodeError, KeyError):
                    continue
                probe_acts = _extract_activities_list(_try_parse_json(raw_probe) or raw_probe)
                if not probe_acts:
                    continue
                for act in probe_acts:
                    if not isinstance(act, dict):
                        continue
                    type_key = str(
                        (act.get("activityTypeDTO") or {}).get("typeKey", "")
                        or (act.get("activityType") or {}).get("typeKey", "")
                        or act.get("type", "")
                    ).lower()
                    if sport_filter == "trail" and "trail" not in type_key:
                        continue
                    if sport_filter == "running" and ("running" not in type_key or "trail" in type_key):
                        continue
                    if sport_filter == "cycling" and "cycl" not in type_key and "bike" not in type_key:
                        continue
                    latest_candidate_iso = probe_iso
                    break
                if latest_candidate_iso:
                    break

        if latest_candidate_iso:
            try:
                target_d = date.fromisoformat(latest_candidate_iso)
            except ValueError:
                pass

    target_iso = target_d.isoformat()
    wants_activity_details = _is_activity_details_query_intent(user_message)

    async def _tool_json(tool_name: str, args: dict) -> Any:
        try:
            raw = await call_tool(mcp_session, tool_name, args)
        except (TimeoutError, OSError) as exc:
            log.debug("_build_mcp_factual_query_markdown: fallo red en %s: %s", tool_name, exc)
            return None
        except RuntimeError as exc:
            log.debug("_build_mcp_factual_query_markdown: fallo runtime en %s: %s", tool_name, exc)
            return None
        parsed_raw = _try_parse_json(raw)
        if parsed_raw is not None:
            return parsed_raw
        compact = _compact_tool_result(raw, tool_name)
        parsed = _try_parse_json(compact)
        return parsed if parsed is not None else compact

    activities: list[dict] = []
    activities_source_tool = "Garmin get_activities_by_date"
    for tool_name, args in (
        ("get_activities_fordate", {"date": target_iso}),
        ("get_activities_by_date", {"start_date": target_iso, "end_date": target_iso, "page": 0, "page_size": 100}),
        ("get_activities_by_date", {"startdate": target_iso, "enddate": target_iso}),
    ):
        payload = await _tool_json(tool_name, args)
        acts = _extract_activities_list(payload)
        if acts:
            activities = acts
            activities_source_tool = f"Garmin {tool_name}"
            break

    # Fallback factual del TSS diario con los TSS guardados de las actividades del día
    # cuando load_metrics/trend aún no reflejan la sesión.
    stored_tss = _load_stored_activity_tss(target_d.isoformat())
    activity_tss_day = 0.0
    for act in activities:
        if not isinstance(act, dict):
            continue
        stored_row = stored_tss.get(_activity_id_int(act))
        if stored_row is not None and float(stored_row.get("tss") or 0.0) > 0:
            activity_tss_day += float(stored_row["tss"])
    activity_tss_day = round(activity_tss_day, 1)

    if wants_activity_details:
        if not activities:
            return "\n".join([
                "## 🧭 Resumen",
                "No se encontraron actividades para el día consultado.",
                "",
                "## 📊 Métricas clave",
                "| Métrica | Valor | Fuente |",
                "|---|---|---|",
                f"| Fecha consultada | {target_d.strftime('%d/%m/%Y')} | consulta factual MCP |",
                f"| Actividades detectadas | 0 | {activities_source_tool} |",
                "",
                "## ✅ Recomendación",
                "- No aplica en esta consulta.",
                "",
                "## 🎯 Próximo paso",
                "- Verifica si la actividad está sincronizada en Garmin Connect y vuelve a consultar.",
                "- Fuente: respuesta determinista (datos factuales MCP, sin inferencias numéricas del LLM).",
            ])

        def _activity_id(act: dict) -> int | None:
            raw = act.get("id") or act.get("activityId") or act.get("activity_id")
            try:
                return int(raw) if raw is not None else None
            except (TypeError, ValueError):
                return None

        def _activity_duration_seconds(act: dict) -> float:
            raw = act.get("duration") or act.get("duration_seconds") or act.get("movingDuration") or 0
            try:
                return float(raw)
            except (TypeError, ValueError):
                return 0.0

        primary = max(activities, key=_activity_duration_seconds)
        primary_id = _activity_id(primary)

        raw_activity = None
        if primary_id is not None:
            try:
                raw_activity = await call_tool(mcp_session, "get_activity", {"activity_id": primary_id})
            except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, asyncio.TimeoutError, json.JSONDecodeError, KeyError):
                raw_activity = None

        raw_training_effect = None
        if primary_id is not None:
            try:
                raw_training_effect = await call_tool(mcp_session, "get_training_effect", {"activity_id": primary_id})
            except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, asyncio.TimeoutError, json.JSONDecodeError, KeyError):
                raw_training_effect = None

        act_payload = _try_parse_json(raw_activity) if raw_activity else None
        if not isinstance(act_payload, dict):
            act_payload = dict(primary)
            raw_activity = json.dumps(act_payload, ensure_ascii=False)
        act_payload = _flatten_garmin_activity(act_payload)

        te_payload = _try_parse_json(raw_training_effect) if raw_training_effect else None
        te_value, te_source = _format_training_effect(te_payload, act_payload)

        # TSS de la actividad principal: el guardado en activity_tss. La carga de Garmin
        # se muestra aparte y nunca sustituye al TSS.
        # Carga Garmin = activityTrainingLoad (activity_tss.native_load); en bici con potencia
        # extract_training_load_tss devolvería el TSS de Garmin en su lugar.
        primary_stored = stored_tss.get(_activity_id_int(primary)) if primary_id is not None else None
        primary_garmin_load = (primary_stored or {}).get("native_load")
        if primary_garmin_load is None:
            primary_garmin_load = _extract_activity_native_load(act_payload)
        if primary_garmin_load is None:
            primary_garmin_load = _extract_activity_native_load(primary)

        primary_tss_value: float | None = None
        primary_tss_source = "pendiente de cálculo"
        if primary_stored is not None:
            primary_tss_value = float(primary_stored.get("tss") or 0.0)
            lbl = str(primary_stored.get("method") or "").strip() or "TSS"
            primary_tss_source = f"Kairos ({lbl}, guardado)"
        tss_display = f"{float(primary_tss_value):.1f}" if primary_tss_value is not None else "pendiente de cálculo"

        raw_hr_zones = None
        embedded = _find_hr_zones_in_json(act_payload)
        if embedded:
            raw_hr_zones = json.dumps(embedded, ensure_ascii=False)

        if not raw_hr_zones and primary_id is not None:
            for tool_name in ("get_activity_hr_in_timezones",):
                for param in ({"activity_id": primary_id}, {"activityId": primary_id}):
                    try:
                        _raw = await call_tool(mcp_session, tool_name, param)
                    except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, asyncio.TimeoutError, json.JSONDecodeError, KeyError):
                        continue
                    if not _raw:
                        continue
                    low = _raw.lower()
                    if "unknown tool" in low:
                        break
                    if _parse_hr_zones_list(_raw):
                        raw_hr_zones = _raw
                        break
                if raw_hr_zones:
                    break

        analysis = _build_activity_analysis_block(
            activity_raw=raw_activity or "{}",
            hr_zones_raw=raw_hr_zones,
            stored_tss_row=primary_stored,
        )
        analysis_md = _format_activity_analysis_for_markdown(analysis)

        metrics_lines = [
            "## 🧭 Resumen",
            "Detalle de entrenamiento resuelto con datos factuales de MCP.",
            "",
            "## 📊 Métricas clave",
            "| Métrica | Valor | Fuente |",
            "|---|---|---|",
            f"| Fecha consultada | {target_d.strftime('%d/%m/%Y')} | consulta factual MCP |",
            f"| Actividades detectadas | {len(activities)} | {activities_source_tool} |",
            f"| TSS (actividad) | {tss_display} | {primary_tss_source} |",
            f"| Training Effect | {te_value} | {te_source} |",
        ]
        if primary_garmin_load is not None:
            metrics_lines.append(
                f"| Carga Garmin (no TSS) | {float(primary_garmin_load):.1f} | activityTrainingLoad/trainingLoad |"
            )

        return "\n".join(
            metrics_lines
            + [
                "",
                analysis_md,
                "",
                "## ✅ Recomendación",
                "- Usa este análisis para ajustar intensidad de la próxima sesión según carga real.",
                "",
                "## 🎯 Próximo paso",
                "- Si quieres, convierto este análisis en una sesión concreta para mañana.",
                "- Fuente: respuesta determinista (datos factuales MCP, sin inferencias numéricas del LLM).",
            ]
        )

    body_payload, hrv_payload, sleep_payload, rhr_payload, stress_summary_payload, all_day_stress_payload, all_day_events_payload, trend_payload = await asyncio.gather(
        _tool_json("get_body_battery", {"start_date": target_iso, "end_date": target_iso}),
        _tool_json("get_hrv_data", {"date": target_iso}),
        _tool_json("get_sleep_summary", {"date": target_iso}),
        _tool_json("get_rhr_day", {"date": target_iso}),
        _tool_json("get_stress_summary", {"date": target_iso}),
        _tool_json("get_all_day_stress", {"date": target_iso}),
        _tool_json("get_all_day_events", {"date": target_iso}),
        _tool_json(
            "get_training_load_trend",
            {
                "start_date": (target_d - timedelta(days=7)).isoformat(),
                "end_date": target_iso,
            },
        ),
    )

    # Fallback DB-first para wellness diario (Body Battery/HRV) cuando MCP no trae detalle útil.
    body_fmt_probe = _format_body_battery_day(body_payload, target_iso)
    hrv_fmt_probe = _format_hrv_day(hrv_payload, target_iso)
    if body_fmt_probe in {"sin datos", "datos disponibles"} or hrv_fmt_probe in {"sin datos", "datos disponibles"}:
        try:
            _wellness_row = _storage.get_wellness_daily(target_iso)
        except (RuntimeError, ValueError, TypeError, OSError, KeyError):
            _wellness_row = None
        db_body_payload, db_hrv_payload = _build_wellness_payloads_from_row(_wellness_row, target_iso)
        if body_fmt_probe in {"sin datos", "datos disponibles"} and db_body_payload is not None:
            body_payload = db_body_payload
        if hrv_fmt_probe in {"sin datos", "datos disponibles"} and db_hrv_payload is not None:
            hrv_payload = db_hrv_payload

    # TSS del día: solo el de Kairos (serie diaria; si no, suma de activity_tss). La tendencia
    # de Garmin (get_training_load_trend) es su carga nativa y va aparte, nunca como TSS.
    garmin_load_day: float | None = None
    for row in _extract_training_load_points(trend_payload):
        if str(row.get("date") or "") == target_iso:
            try:
                garmin_load_day = float(row.get("tss") or 0.0)
            except (TypeError, ValueError):
                garmin_load_day = None
            break

    tss_day: float | None = None
    tss_source = "no disponible"
    series = _load_metrics_series()
    for row in series:
        if str(row.get("date") or "") == target_iso:
            try:
                tss_day = float(row.get("tss") or 0.0)
                tss_source = "load_metrics(series)"
            except (TypeError, ValueError):
                tss_day = None
            break

    if activity_tss_day > 0 and (tss_day is None or float(tss_day) <= 0.0):
        tss_day = activity_tss_day
        tss_source = "activity_tss (suma de actividades del día)"

    rhr_display = _format_rhr_day(rhr_payload, target_iso)
    if rhr_display == "sin datos":
        profile_rhr, _ = _resolve_hr_profile_values(profile)
        if profile_rhr is not None:
            try:
                rhr_display = f"{int(round(float(profile_rhr)))} bpm"
            except (TypeError, ValueError):
                pass

    def _duration_min(activity: dict) -> int | None:
        dur = activity.get("duration") or activity.get("duration_seconds") or activity.get("movingDuration")
        if dur is None:
            return None
        try:
            return int(round(float(dur) / 60.0))
        except (TypeError, ValueError):
            return None

    lines = [
        "## 🧭 Resumen",
        "Consulta factual diaria resuelta con datos reales de Garmin/MCP.",
        "",
        "## 📊 Métricas clave",
        "| Métrica | Valor | Fuente |",
        "|---|---|---|",
        f"| Fecha consultada | {target_d.strftime('%d/%m/%Y')} | consulta factual MCP |",
        f"| TSS del día | {f'{tss_day:.1f}' if tss_day is not None else 'sin datos'} | {tss_source if tss_day is not None else 'no disponible'} |",
        f"| Carga Garmin del día (no TSS) | {f'{garmin_load_day:.1f}' if garmin_load_day is not None else 'sin datos'} | get_training_load_trend |",
        f"| FC en reposo | {rhr_display} | Garmin RHR |",
        f"| Sueño | {_format_sleep_day(sleep_payload, target_iso)} | Garmin sleep |",
        f"| Estrés | {_format_stress_day(stress_summary_payload, all_day_stress_payload, target_iso)} | Garmin stress_summary/all_day_stress |",
        f"| Eventos diarios | {_format_all_day_events(all_day_events_payload, target_iso)} | Garmin all_day_events |",
        f"| Body Battery | {_format_body_battery_day(body_payload, target_iso)} | Garmin body_battery |",
        f"| HRV | {_format_hrv_day(hrv_payload, target_iso)} | Garmin HRV |",
    ]

    if activities:
        lines.append("")
        lines.append("Actividades del día (Garmin):")
        for act in activities[:6]:
            name = str(act.get("name") or act.get("activityName") or "Actividad").strip() or "Actividad"
            sport = _get_activity_name_es(act.get("type") or act.get("activityType") or "") or "Actividad"
            dur_m = _duration_min(act)
            if dur_m is not None:
                lines.append(f"- {sport} — {name} ({dur_m} min)")
            else:
                lines.append(f"- {sport} — {name}")
    else:
        lines.append("")
        lines.append("- Actividades del día (Garmin): sin datos")

    lines.append("")
    lines.append("## ✅ Recomendación")
    if tss_day is not None:
        lines.append("- Usa este estado diario para decidir si mantienes, reduces o subes carga hoy.")
    else:
        lines.append("- No aplica en esta consulta.")
    lines.append("")
    lines.append("## 🎯 Próximo paso")
    lines.append("- Si quieres, traduzco estas métricas en una recomendación concreta de sesión para hoy.")
    lines.append("- Fuente: respuesta determinista (datos factuales MCP, sin inferencias numéricas del LLM).")
    return "\n".join(lines)


async def _build_recovery_fallback_snapshot(
    mcp_session: ClientSession,
    preferred_date_iso: str | None,
) -> str | None:
    """Construye un snapshot de recuperación cuando no hay training_readiness.

    Intenta primero la fecha solicitada, y si no hay datos, prueba hoy y ayer.
    """
    dates_to_try: list[str] = []
    if preferred_date_iso:
        dates_to_try.append(preferred_date_iso)
    today_iso = date.today().isoformat()
    yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
    for candidate in (today_iso, yesterday_iso):
        if candidate not in dates_to_try:
            dates_to_try.append(candidate)

    tools = [
        "get_body_battery",
        "get_hrv_data",
        "get_sleep_summary",
        "get_stress_summary",
        "get_all_day_stress",
        "get_all_day_events",
        "get_rhr_day",
    ]

    snapshot: dict[str, dict] = {}
    for tool_name in tools:
        for date_iso in dates_to_try:
            try:
                args = (
                    {"start_date": date_iso, "end_date": date_iso}
                    if tool_name == "get_body_battery"
                    else {"date": date_iso}
                )
                raw = await call_tool(mcp_session, tool_name, args)
            except (TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, KeyError):
                continue

            if _is_no_data_result(raw):
                continue

            compact = _compact_tool_result(raw, tool_name)
            try:
                parsed_data = json.loads(compact)
            except (TypeError, json.JSONDecodeError):
                # Algunos endpoints devuelven texto plano; guardarlo también es útil
                # para que el LLM no pierda contexto y evitar romper el flujo.
                parsed_data = {"raw": compact}

            snapshot[tool_name] = {
                "date": date_iso,
                "data": parsed_data,
            }
            break

    if not snapshot:
        return None

    payload = {
        "fallback_reason": "training_readiness_unavailable",
        "summary": "Se usa un snapshot alternativo de recuperación (body battery, HRV, sueño, estrés, RHR).",
        "snapshot": snapshot,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
