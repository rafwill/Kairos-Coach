"""
agent/startup.py
Arranque de la sesión: tools del MCP, zonas de FC de Garmin, datos personales, foto de las últimas 48 h y resumen inicial; preguntas de la sesión anterior y su resumen diario.
"""

import time
import logging
import json
import asyncio
from datetime import date, timedelta
from typing import Any, Awaitable


from agent.mcp.client import (
    list_available_tools,
    call_tool,
)
from agent import storage as _storage
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _try_parse_json,
    TSS_FORMULA_VERSION,
)
from agent.tool_results import (
    _build_tools_schema,
    _compact_tool_result,
    _extract_activities_list,
)
from agent.data_access import (
    _load_metrics_series,
    _load_metrics_view,
    _load_session_context,
    _save_user_profile,
    _session_memory_enabled,
)
from agent.garmin_data import (
    _extract_activity_date_iso,
    _is_activity_in_last_48h,
)
from agent.hr_profile import (
    _normalize_garmin_hr_zones,
)
from agent.wellness import (
    _extract_body_battery_snapshot,
    _extract_hrv_snapshot,
    _format_body_battery_day,
    _format_hrv_day,
    _format_sleep_day,
)
from agent.kairos_tools import (
    _KAIROS_INTERNAL_TOOLS,
)
from agent import plan_drift as _plan_drift
from agent import startup_cache as _startup_cache
from agent import weather as _weather
from agent.plans import _plan_field, _plan_start, _today_session_lines
from agent.startup_status import SOURCE_RULES, SOURCE_RULES_AND_COACH
from agent.daily_engine import (
    _build_startup_plan_recommendation,
    _compute_daily_plan_adjustment,
    _compute_plan_execution_feedback,
    _get_active_training_plan,
)
from agent.routes.load import (
    _compute_effective_week_tss_from_series_and_activities,
)
from agent.startup_status import (
    _build_proactive_status_markdown,
)

log = logging.getLogger(__name__)


async def _call_json(session, tool_name: str, args: dict) -> Any:
    """Llama a una tool de Garmin con 8 s de tiempo y devuelve el JSON; None si falla."""
    try:
        raw = await asyncio.wait_for(call_tool(session, tool_name, args), timeout=8.0)
    except (TimeoutError, OSError) as exc:
        log.debug("startup: fallo red en %s: %s", tool_name, exc)
        return None
    except RuntimeError as exc:
        log.debug("startup: fallo runtime en %s: %s", tool_name, exc)
        return None
    except asyncio.TimeoutError:
        log.debug("startup: timeout en %s", tool_name)
        return None
    parsed_raw = _try_parse_json(raw)
    if parsed_raw is not None:
        return parsed_raw
    compact = _compact_tool_result(raw, tool_name)
    parsed = _try_parse_json(compact)
    return parsed if parsed is not None else compact


def _recent_training_ids(activities: list[dict]) -> list[str]:
    """Ids de las actividades de las últimas 48 h (hasta 5), como las lista el snapshot del arranque."""
    ids = [a.get("activityId") or a.get("id") for a in activities if _is_activity_in_last_48h(a)]
    return [str(i) for i in ids if i is not None][:5]


def _plan_drift_lines(plan: dict, today: date | None = None) -> list[str]:
    """Bloque «🔄 Tu plan ya no encaja» del arranque (30/10, pendiente 11 d1); vacío si el plan sigue valiendo.

    Solo juzga los días desde la creación del plan y su última revisión (`revised_on`): lo anterior ya se tuvo en
    cuenta al crearlo o revisarlo. Lee activity_tss y wellness_daily; un fallo de lectura deja el bloque vacío.
    """
    start, sessions = _plan_start(plan), plan.get("sessions") or []
    if not start or not sessions:
        return []
    today = today or date.today()
    marks = [str(plan.get("created_at") or "")[:10], str(_plan_field(plan, "revised_on") or "")[:10]]
    since = max((date.fromisoformat(m) for m in marks if len(m) == 10), default=None)
    days = _plan_drift.window(today, start, since)
    if not days:
        return []
    acts = _storage.get_activity_tss_map((days[0] - timedelta(days=1)).isoformat())  # el día de antes: sesión movida
    if acts is None:
        return []
    hrv = _storage.get_wellness_daily_range(days[0].isoformat(), today.isoformat())
    signals = _plan_drift.detect(sessions, start, list(acts.values()), hrv, today, since)
    if signals:
        log.info("[PLAN] desvíos: %s", " | ".join(s.code for s in signals))
    return _plan_drift.markdown(signals)


class StartupMixin:
    """Arranque de la sesión: tools del MCP, zonas de FC de Garmin, datos personales, foto de las últimas 48 h y resumen inicial; preguntas de la sesión anterior y su resumen diario."""

    async def initialize(self) -> None:
        """Carga las herramientas disponibles del MCP y restaura el historial reciente."""
        tools = await list_available_tools(self.mcp_session)
        self.tools_schema = _build_tools_schema(tools)
        # Añadir herramientas internas Kairos (no requieren MCP)
        self.tools_schema.extend(_KAIROS_INTERNAL_TOOLS)

        self._restore_previous_session()
        await self._refresh_hr_zones_from_garmin()

    async def _refresh_hr_zones_from_garmin(self) -> None:
        """Lee las zonas de FC de Garmin y las guarda en el perfil solo si cambian.

        Fuente de verdad de las zonas (reloj y TrainingPeaks). Una llamada por arranque; si Garmin falla,
        se siguen usando las guardadas. La versión anterior pasa a hr_zones_garmin_history con su fecha.
        """
        try:
            raw = await call_tool(self.mcp_session, "get_heart_rate_zones", {})
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError) as exc:
            log.info("Zonas de FC: Garmin no responde, se usan las guardadas (%s)", exc)
            return
        live = _normalize_garmin_hr_zones(raw)
        if not live or not isinstance(self.user_profile, dict):
            log.info("Zonas de FC: Garmin no devolvió zonas válidas; se usan las guardadas")
            return
        perf = self.user_profile.setdefault("performance", {})
        stored = perf.get("hr_zones_garmin") or {}
        if stored.get("sports") == live["sports"]:
            return
        today_iso = date.today().isoformat()
        if stored.get("sports"):
            history = perf.setdefault("hr_zones_garmin_history", [])
            history.append({"valid_from": stored.get("updated_at"), "valid_until": today_iso, "sports": stored["sports"]})
        perf["hr_zones_garmin"] = {"updated_at": today_iso, **live}
        try:
            _save_user_profile(self.user_profile)
            log.info("Zonas de FC: actualizadas desde Garmin (%s)", today_iso)
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            log.warning("Zonas de FC: no se pudieron guardar en el perfil: %s", exc)

    def _restore_previous_session(self) -> None:
        """De la sesión anterior solo se recuperan las preguntas del usuario (temas), no las respuestas:
        con ellas en el contexto el LLM repetía cifras viejas o erróneas sin volver a consultar las tools."""
        if not _session_memory_enabled():
            self.previous_session_questions = ()
            return
        ctx = _load_session_context()
        self.previous_session_questions = tuple(
            str(entry.get("content") or "").strip()
            for entry in ctx.get("history", [])[-6:]
            if entry.get("role") == "user" and str(entry.get("content") or "").strip()
        )

    async def fetch_garmin_personal_data(self, profile_raw: str | None = None) -> dict:
        """
        Obtiene datos personales del usuario directamente desde Garmin Connect.
        Estructura real de get_user_profile:
          { "userData": { "gender", "weight"(g), "height"(cm), "birthDate" }, ... }
        El nombre no está disponible en este endpoint.
        profile_raw: respuesta de get_user_profile ya pedida (el arranque la usa también para comprobar el acceso).
        """
        result = {}
        today = date.today().isoformat()

        # --- get_user_profile ---
        try:
            raw = profile_raw if profile_raw is not None else await call_tool(self.mcp_session, "get_user_profile", {})
            data = json.loads(raw) if raw and raw.strip().startswith("{") else {}
            if isinstance(data, dict):
                ud = data.get("userData", {})

                # Edad calculada desde birthDate (YYYY-MM-DD)
                birth = ud.get("birthDate") or data.get("birthDate")
                if birth:
                    try:
                        born = date.fromisoformat(str(birth))
                        today_d = date.today()
                        age = today_d.year - born.year - (
                            (today_d.month, today_d.day) < (born.month, born.day)
                        )
                        if 5 < age < 120:
                            result["age"] = age
                    except (ValueError, TypeError):
                        pass

                # Género
                gender = ud.get("gender") or data.get("gender", "")
                if gender:
                    result["gender"] = "hombre" if "MALE" in str(gender).upper() else "mujer"

                # Altura en cm
                height = ud.get("height") or data.get("height")
                if height:
                    try:
                        h = float(height)
                        if h > 50:
                            result["height_cm"] = int(round(h))
                    except (ValueError, TypeError):
                        pass

                # Peso: Garmin lo devuelve en gramos (ej: 67000.0 = 67 kg)
                weight = ud.get("weight") or data.get("weight")
                if weight:
                    try:
                        w = float(weight)
                        result["weight_kg"] = round(w / 1000, 1) if w > 300 else round(w, 1)
                    except (ValueError, TypeError):
                        pass
        except (TimeoutError, OSError, RuntimeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log.debug("fetch_garmin_personal_data: get_user_profile falló: %s", exc)

        # --- get_body_composition (peso más reciente si get_user_profile no lo devolvió) ---
        if "weight_kg" not in result:
            try:
                raw = await call_tool(
                    self.mcp_session,
                    "get_body_composition",
                    {"start_date": today, "end_date": today},
                )
                data = json.loads(raw) if raw and raw.strip().startswith(("{", "[")) else {}
                if isinstance(data, list) and data:
                    data = data[0]
                if isinstance(data, dict):
                    weight = data.get("weight") or data.get("weightKg") or data.get("value")
                    if weight:
                        try:
                            w = float(weight)
                            result["weight_kg"] = round(w / 1000, 1) if w > 300 else round(w, 1)
                        except (ValueError, TypeError):
                            pass
            except (TimeoutError, OSError, RuntimeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                log.debug("fetch_garmin_personal_data: get_body_composition falló: %s", exc)

        return result

    async def _today_weather(self) -> dict | None:
        """Tiempo de hoy donde entrena el atleta (30/10, decisión del usuario); None sin lugar o si falla.

        Las coordenadas del lugar se buscan una vez y se guardan en el perfil con el nombre del que salen.
        """
        goals = (getattr(self, "user_profile", None) or {}).get("goals") or {}
        location = str(goals.get("training_location") or "")
        if not location:
            return None
        weather, coords = await _weather.today_weather(location, goals.get("training_location_coords"), date.today())
        if coords:
            self.user_profile.setdefault("goals", {})["training_location_coords"] = coords
            try:
                _save_user_profile(self.user_profile)
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                log.info("[TIEMPO] no se pudieron guardar las coordenadas: %s", exc)
        return weather

    async def collect_startup_snapshot_48h(self, prefetch_today: dict | None = None,
                                           load_ready: "asyncio.Future | None" = None) -> dict:
        """Recoge un snapshot operativo de 48h para briefing de arranque.

        load_ready: el cálculo de la carga del arranque, que corre a la vez (28/10, pendiente 8c). Las llamadas a
        Garmin no lo esperan; la lectura de la serie de carga, sí, para no leerla antes de que se guarde.
        """
        today_iso = date.today().isoformat()
        yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
        two_days_ago_iso = (date.today() - timedelta(days=2)).isoformat()
        _pf = prefetch_today or {}
        _t_snap = time.perf_counter()
        # El tiempo de hoy (Open-Meteo, 30/10) va a la vez que las llamadas a Garmin.
        weather_task = asyncio.create_task(self._today_weather())
        if not hasattr(self, "_session_wellness_cache") or not isinstance(getattr(self, "_session_wellness_cache", None), dict):
            self._session_wellness_cache = {}
        _session_wellness_cache: dict[str, dict[str, Any]] = self._session_wellness_cache

        async def _tool_json(tool_name: str, args: dict) -> Any:
            return await _call_json(self.mcp_session, tool_name, args)

        async def _cached_or_fetch(cache_key: str, tool_name: str, args: dict) -> Any:
            if cache_key in _pf:
                raw = _pf[cache_key]
                if raw is None:
                    return None
                parsed = _try_parse_json(raw)
                if parsed is not None:
                    return parsed
                compact = _compact_tool_result(raw, tool_name)
                return _try_parse_json(compact) or compact
            return await _tool_json(tool_name, args)

        def _safe_upsert_wellness(
            metric_date: str,
            *,
            body_payload: Any = None,
            hrv_payload: Any = None,
            source_payload: dict | None = None,
        ) -> None:
            body = _extract_body_battery_snapshot(body_payload, metric_date)
            hrv = _extract_hrv_snapshot(hrv_payload, metric_date)
            if not body and not hrv:
                return
            try:
                _storage.upsert_wellness_daily(
                    metric_date,
                    body_battery=body,
                    hrv=hrv,
                    source_payload=source_payload,
                )
            except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
                log.debug("collect_startup_snapshot_48h: wellness DB upsert fallo (%s): %s", metric_date, exc)

        async def _return(value: Any) -> Any:
            return value

        def _session_get(metric_date: str, metric_key: str) -> Any:
            day_bucket = _session_wellness_cache.get(metric_date) or {}
            return day_bucket.get(metric_key)

        def _session_set(metric_date: str, metric_key: str, payload: Any) -> None:
            day_bucket = _session_wellness_cache.setdefault(metric_date, {})
            day_bucket[metric_key] = payload

        # Los datos de hoy se piden siempre a Garmin (cambian durante el día); los de ayer, de la caché de la sesión.
        use_session_body_yday = (
            ("body_yday" not in _pf)
            and _session_get(yesterday_iso, "body") is not None
        )
        use_session_hrv_yday = (
            ("hrv_yday" not in _pf)
            and _session_get(yesterday_iso, "hrv") is not None
        )

        body_today_task: Awaitable[Any]
        if "body_today" in _pf:
            body_today_task = _cached_or_fetch("body_today", "get_body_battery", {"start_date": today_iso, "end_date": today_iso})
        else:
            body_today_task = _tool_json("get_body_battery", {"start_date": today_iso, "end_date": today_iso})

        body_yday_task = (
            _return(_session_get(yesterday_iso, "body"))
            if use_session_body_yday
            else _tool_json("get_body_battery", {"start_date": yesterday_iso, "end_date": yesterday_iso})
        )

        hrv_today_task: Awaitable[Any]
        if "hrv_today" in _pf:
            hrv_today_task = _cached_or_fetch("hrv_today", "get_hrv_data", {"date": today_iso})
        else:
            hrv_today_task = _tool_json("get_hrv_data", {"date": today_iso})

        hrv_yday_task = (
            _return(_session_get(yesterday_iso, "hrv"))
            if use_session_hrv_yday
            else _tool_json("get_hrv_data", {"date": yesterday_iso})
        )

        body_today, body_yday, hrv_today, hrv_yday, sleep_today, sleep_yday, activities_raw = (
            await asyncio.gather(
                body_today_task,
                body_yday_task,
                hrv_today_task,
                hrv_yday_task,
                _tool_json("get_sleep_summary", {"date": today_iso}),
                _tool_json("get_sleep_summary", {"date": yesterday_iso}),
                _tool_json(
                    "get_activities_by_date",
                    {
                        "start_date": two_days_ago_iso,
                        "end_date": today_iso,
                        "page": 0,
                        "page_size": 100,
                    },
                ),
            )
        )

        # Política por sesión: si MCP trae dato válido, persistirlo y cachearlo en memoria.
        # Si falla MCP, no usar DB histórica para cálculos actuales (evita reutilizar datos viejos).
        body_today_from_mcp = _extract_body_battery_snapshot(body_today, today_iso)
        hrv_today_from_mcp = _extract_hrv_snapshot(hrv_today, today_iso)
        body_yday_from_mcp = _extract_body_battery_snapshot(body_yday, yesterday_iso)
        hrv_yday_from_mcp = _extract_hrv_snapshot(hrv_yday, yesterday_iso)

        if body_today_from_mcp is not None:
            _session_set(today_iso, "body", body_today)
        if hrv_today_from_mcp is not None:
            _session_set(today_iso, "hrv", hrv_today)
        if body_yday_from_mcp is not None:
            _session_set(yesterday_iso, "body", body_yday)
        if hrv_yday_from_mcp is not None:
            _session_set(yesterday_iso, "hrv", hrv_yday)

        wellness_sources = {
            "body_today": "mcp" if body_today_from_mcp is not None else "mcp_unavailable",
            "hrv_today": "mcp" if hrv_today_from_mcp is not None else "mcp_unavailable",
            "body_yday": "session" if use_session_body_yday else "mcp",
            "hrv_yday": "session" if use_session_hrv_yday else "mcp",
        }
        if (not use_session_body_yday) and (body_yday_from_mcp is None):
            wellness_sources["body_yday"] = "mcp_unavailable"
        if (not use_session_hrv_yday) and (hrv_yday_from_mcp is None):
            wellness_sources["hrv_yday"] = "mcp_unavailable"

        # Persistir/actualizar wellness diario con datos nuevos de MCP o prefetch.
        if body_today_from_mcp is not None:
            _safe_upsert_wellness(
                today_iso,
                body_payload=body_today,
                hrv_payload=hrv_today if hrv_today_from_mcp is not None else None,
                source_payload={
                    "body": body_today,
                    "hrv": hrv_today if hrv_today_from_mcp is not None else None,
                },
            )
        elif hrv_today_from_mcp is not None:
            _safe_upsert_wellness(
                today_iso,
                hrv_payload=hrv_today,
                source_payload={"hrv": hrv_today},
            )

        if ((not use_session_body_yday and body_yday_from_mcp is not None) or
            (not use_session_hrv_yday and hrv_yday_from_mcp is not None)):
            _safe_upsert_wellness(
                yesterday_iso,
                body_payload=body_yday if (not use_session_body_yday and body_yday_from_mcp is not None) else None,
                hrv_payload=hrv_yday if (not use_session_hrv_yday and hrv_yday_from_mcp is not None) else None,
                source_payload={
                    "body": body_yday if (not use_session_body_yday and body_yday_from_mcp is not None) else None,
                    "hrv": hrv_yday if (not use_session_hrv_yday and hrv_yday_from_mcp is not None) else None,
                },
            )

        log.info("[SNAPSHOT] ⏱ gather_7_calls=%.1fs (cached: body=%s hrv=%s)",
                 time.perf_counter() - _t_snap,
                 "body_today" in _pf, "hrv_today" in _pf)
        activities_recent = _extract_activities_list(activities_raw)
        if not activities_recent:
            retry_recent_raw = await _tool_json(
                "get_activities_by_date",
                {
                    "start_date": yesterday_iso,
                    "end_date": today_iso,
                    "page": 0,
                    "page_size": 100,
                },
            )
            activities_recent = _extract_activities_list(retry_recent_raw)

        if not activities_recent:
            # Fallback para backends que no responden bien al filtro por fecha.
            fallback_recent_raw = await _tool_json("get_activities", {"start": "0", "limit": "20"})
            activities_recent = _extract_activities_list(fallback_recent_raw)

        recent_trainings: list[dict] = []
        for activity in activities_recent:
            if not _is_activity_in_last_48h(activity):
                continue
            day = _extract_activity_date_iso(activity) or ""
            recent_trainings.append(
                {
                    "date": day,
                    "name": activity.get("name") or activity.get("activityName") or activity.get("activityType") or "Actividad",
                    "activity_id": activity.get("activityId") or activity.get("id"),
                }
            )

        # Fase 3: cierre del bucle planificado vs ejecutado (dia N-1).
        plan_execution_feedback: dict = {}
        active_plan = _get_active_training_plan(getattr(self, "user_profile", {}) or {})
        if active_plan:
            yday_activities: list[dict] = []
            yday_raw = await _tool_json(
                "get_activities_by_date",
                {
                    "start_date": yesterday_iso,
                    "end_date": yesterday_iso,
                    "page": 0,
                    "page_size": 100,
                },
            )
            if isinstance(yday_raw, dict):
                yday_activities = _extract_activities_list(yday_raw.get("activities") or yday_raw)
            elif isinstance(yday_raw, list):
                yday_activities = _extract_activities_list(yday_raw)

            # Fallback local: filtrar el lote reciente por fecha exacta de ayer.
            if not yday_activities:
                for _act in activities_recent:
                    if _extract_activity_date_iso(_act) == yesterday_iso:
                        yday_activities.append(_act)

            feedback = _compute_plan_execution_feedback(
                plan=active_plan,
                activities_for_day=yday_activities,
                target_date_iso=yesterday_iso,
                profile=getattr(self, "user_profile", {}) if hasattr(self, "user_profile") else {},
            )
            if isinstance(feedback, dict):
                plan_execution_feedback = feedback

        # Carga: solo la serie de load_metrics_daily. Sin ella no hay estado de carga (27/10: antes se recalculaba
        # el TSS en vivo, sin el detalle de las actividades, o se usaba una copia del perfil que podía ser vieja).
        _load_debug: str = "sin serie en load_metrics_daily"
        load_fatigue = None
        if load_ready is not None:
            await asyncio.wait([load_ready])  # sin propagar su error: el arranque ya lo avisa por su cuenta
        canonical_series = _load_metrics_series()
        if canonical_series:
            load_fatigue = _load_metrics_view(getattr(self, "user_profile", {}), canonical_series) or None
            if load_fatigue:
                _load_debug = f"usando serie canónica de DB ({len(canonical_series)} días)"
                try:
                    today_d = date.today()
                    week_start = today_d - timedelta(days=today_d.weekday())
                    week_dates = [week_start + timedelta(days=i) for i in range((today_d - week_start).days + 1)]
                    week_activities_raw = await _tool_json(
                        "get_activities_by_date",
                        {
                            "start_date": week_start.isoformat(),
                            "end_date": today_iso,
                            "page": 0,
                            "page_size": 200,
                        },
                    )
                    week_activities = _extract_activities_list(week_activities_raw)
                    effective_tss, used_fallback = _compute_effective_week_tss_from_series_and_activities(
                        series=canonical_series,
                        activities=week_activities,
                        week_dates=week_dates,
                    )
                    load_fatigue.setdefault("weekly", {})
                    load_fatigue["weekly"]["current_tss_effective"] = effective_tss
                    load_fatigue["weekly"]["current_tss_effective_source"] = (
                        "load_metrics_daily+activity_fallback" if used_fallback else "load_metrics_daily"
                    )
                except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as _week_exc:
                    log.debug("collect_startup: no se pudo calcular current_tss_effective: %s", _week_exc)

        body_summary = (
            f"hoy={_format_body_battery_day(body_today, today_iso)} · "
            f"ayer={_format_body_battery_day(body_yday, yesterday_iso)}"
        )
        hrv_summary = (
            f"hoy={_format_hrv_day(hrv_today, today_iso)} · "
            f"ayer={_format_hrv_day(hrv_yday, yesterday_iso)}"
        )
        sleep_summary = (
            f"hoy={_format_sleep_day(sleep_today, today_iso)} · "
            f"ayer={_format_sleep_day(sleep_yday, yesterday_iso)}"
        )

        return {
            "window_hours": 48,
            "dates": {"today": today_iso, "yesterday": yesterday_iso},
            "wellness_sources": wellness_sources,
            "body_battery": {"today": body_today, "yesterday": body_yday, "summary": body_summary},
            "hrv": {"today": hrv_today, "yesterday": hrv_yday, "summary": hrv_summary},
            "sleep": {"today": sleep_today, "yesterday": sleep_yday, "summary": sleep_summary},
            "load_fatigue": load_fatigue or {},
            "load_debug": _load_debug,
            "plan_execution_feedback": plan_execution_feedback,
            "trainings": recent_trainings[:5],
            "weather": await weather_task,
        }

    async def build_startup_status_markdown(self, profile_changes: list[str] | None = None,
                                            load_ready: "asyncio.Future | None" = None) -> str:
        """Construye el mensaje proactivo mostrado al arrancar la sesion (load_ready: ver collect_startup_snapshot_48h)."""
        _t0 = time.perf_counter()
        log.info("[STARTUP] ▶ build_startup_status_markdown inicio")
        snapshot = await self.collect_startup_snapshot_48h(load_ready=load_ready)
        log.info("[STARTUP] ⏱ collect_snapshot=%.1fs", time.perf_counter() - _t0)
        snapshot["profile_changes"] = profile_changes or []
        active_plan = _get_active_training_plan(self.user_profile)
        snapshot["plan_assigned"] = bool(active_plan)
        if active_plan:
            snapshot["plan_recommendation"] = _build_startup_plan_recommendation(active_plan)
            snapshot["daily_plan_decision"] = _compute_daily_plan_adjustment(snapshot, active_plan) or {}
            snapshot["today_session_lines"] = _today_session_lines(active_plan, self.user_profile)
            snapshot["plan_drift_lines"] = _plan_drift_lines(active_plan)
        md = _build_proactive_status_markdown(snapshot)
        needs_coach = bool(active_plan and snapshot["daily_plan_decision"].get("decision"))
        if needs_coach:
            md = await self._startup_coaching(md, snapshot["daily_plan_decision"])
        self._save_startup_summary(snapshot, profile_changes, md, needs_coach)
        log.info("[STARTUP] ✓ total=%.1fs", time.perf_counter() - _t0)
        return md

    async def cached_startup_summary(self, profile_changes: list[str] | None = None) -> str | None:
        """El resumen del arranque guardado, con su nota de cuándo es, si tiene menos de 1 h y nada ha cambiado
        (31/10, petición del usuario); None si hay que rehacerlo. Solo hace 2 llamadas a Garmin (actividades y sueño
        de hoy) y 1 a Supabase para el plan, y se salta el cálculo de carga, el snapshot y el LLM."""
        try:
            row = _storage.get_startup_summary()
            if not _startup_cache.is_recent(row):
                return None
            today_iso = date.today().isoformat()
            acts_raw, sleep_today = await asyncio.gather(
                _call_json(self.mcp_session, "get_activities_by_date", {
                    "start_date": (date.today() - timedelta(days=2)).isoformat(), "end_date": today_iso,
                    "page": 0, "page_size": 100}),
                _call_json(self.mcp_session, "get_sleep_summary", {"date": today_iso}),
            )
            if acts_raw is None:
                return None  # Garmin no responde: no se puede asegurar que no haya nada nuevo
            current = _startup_cache.fingerprint(
                today=today_iso, activity_ids=_recent_training_ids(_extract_activities_list(acts_raw)),
                sleep=_format_sleep_day(sleep_today, today_iso), plan_row=_storage.get_active_training_plan(),
                formula_version=TSS_FORMULA_VERSION, profile_changes=profile_changes)
            if not _startup_cache.matches(row, current):
                return None
            log.info("[STARTUP] resumen reutilizado (huella igual, menos de 1 h)")
            return _startup_cache.with_note(row)
        except Exception as exc:  # noqa: BLE001 — la caché es opcional: ante cualquier fallo se rehace el resumen
            log.warning("[STARTUP] caché del resumen no disponible: %s", exc)
            return None

    def _save_startup_summary(self, snapshot: dict, profile_changes: list[str] | None, md: str,
                              needs_coach: bool) -> None:
        """Guarda el resumen con su huella para reutilizarlo. No se guarda si Garmin no respondió nada ni si faltó el
        texto del entrenador (el LLM falló): así el siguiente arranque lo reintenta."""
        sleep_today = (snapshot.get("sleep") or {}).get("today")
        if all(x is None for x in ((snapshot.get("body_battery") or {}).get("today"),
                                   (snapshot.get("hrv") or {}).get("today"), sleep_today)):
            return
        if needs_coach and "🧠 Tu entrenador" not in md:
            return
        try:
            today_iso = str((snapshot.get("dates") or {}).get("today") or date.today().isoformat())
            _storage.save_startup_summary(_startup_cache.fingerprint(
                today=today_iso, activity_ids=[t.get("activity_id") for t in snapshot.get("trainings") or []],
                sleep=_format_sleep_day(sleep_today, today_iso), plan_row=_storage.get_active_training_plan(),
                formula_version=TSS_FORMULA_VERSION, profile_changes=profile_changes), md)
        except Exception as exc:  # noqa: BLE001 — la caché es opcional
            log.warning("[STARTUP] no se pudo guardar el resumen: %s", exc)

    async def _startup_coaching(self, md: str, decision: dict) -> str:
        """Recomendación del arranque con plan (29/10, decisión del usuario): la decisión es la del motor diario y el
        LLM la explica y concreta la sesión dentro de su marco (G12, S15). Su texto va dentro de "Recomendación",
        antes del próximo paso; si el LLM falla, queda solo la parte determinista."""
        if not getattr(self, "client", None):
            return md
        t0 = time.perf_counter()
        augmented = await self._augment_with_llm_coaching_if_needed(
            "¿Qué me recomiendas hoy con lo que me toca en el plan?", md, route="daily_readiness",
            daily_plan_decision=decision, always=True, model=self.model)  # el principal, no el del chat (30/10)
        log.info("[STARTUP] ⏱ coaching=%.1fs", time.perf_counter() - t0)
        extra = augmented[len(md):] if augmented.startswith(md) else ""
        if "_Fase coaching por LLM sobre datos deterministas" not in extra:
            return md  # el LLM no respondió: sin la nota de error en el arranque
        text = extra.split("## 🧠 Interpretación de coaching", 1)[-1]
        text = text.split("\n\n_Fase coaching por LLM", 1)[0].strip()
        # El texto del entrenador cierra la recomendación, justo antes del pie, que pasa a decir de dónde sale cada parte.
        marker = "\n\n" + SOURCE_RULES
        block = "\n\n### 🧠 Tu entrenador\n" + text
        if marker in md:
            return md.replace(marker, block + "\n\n" + SOURCE_RULES_AND_COACH, 1)
        return md + block

    async def build_onboarding_mcp_enrichment(self) -> dict:
        """Obtiene datos MCP utiles para enriquecer la base inicial del atleta."""
        personal = await self.fetch_garmin_personal_data()
        startup = await self.collect_startup_snapshot_48h()
        return {
            "personal": personal,
            "startup_48h": startup,
        }

    def today_ia_tokens(self) -> int:
        """Tokens consumidos hoy con el proveedor de IA del atleta (ia_provider); 0 si no se pueden leer."""
        try:
            row = _storage.get_ia_provider() or {}
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            log.debug("No se pudo leer el consumo de tokens: %s", exc)
            return 0
        return int((row.get("daily_tokens") or {}).get(date.today().isoformat(), 0) or 0)

    def generate_session_summary_checkpoint(self) -> str:
        """Genera un resumen local y ligero para checkpoints frecuentes.

        No llama al LLM: evita latencia de red durante la sesión y en el cierre.
        """
        if not self.conversation_history:
            return ""

        # Solo las preguntas: las respuestas traen cifras que caducan (como en _restore_previous_session).
        user_msgs = [
            str(msg.get("content") or "").strip()
            for msg in self.conversation_history
            if msg.get("role") == "user" and str(msg.get("content") or "").strip()
        ]

        if not user_msgs:
            return ""

        return f"Temas recientes: {' | '.join(user_msgs[-4:])}."

    def save_session_summary_checkpoint(self) -> None:
        """Persiste un checkpoint diario del resumen de sesión (upsert por fecha)."""
        summary = self.generate_session_summary_checkpoint()
        if not summary or not _session_memory_enabled():
            return
        try:
            _storage.persist_session_summary_daily(summary)
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            log.debug("No se pudo persistir checkpoint diario de sesión: %s", exc)
