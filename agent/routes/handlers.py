"""
agent/routes/handlers.py
Tabla de rutas del chat (pendiente 7, 28/10): cada ruta determinista o mixta es un método `_route_<ruta>` que
devuelve la respuesta (o la respuesta y otro nombre de ruta para agent.log, o None para seguir con el LLM).
`TrainerAgent._answer` la consulta con la ruta que da `ToolRouter`.
"""

import logging
import json
from datetime import timedelta


from agent.mcp.client import (
    call_tool,
)
from agent import run_plan as _run_plan
from agent import storage as _storage
from agent.hr_profile import _resolve_hr_zones
from agent.llm import LLMAuthError
from agent.llm_calls import _provider_error
from agent.tool_results import (
    _compact_tool_result,
    _is_no_data_result,
)
from agent.data_access import (
    _load_user_profile,
    _save_user_profile,
)
from agent.daily_engine import (
    _build_startup_plan_recommendation,
    _compute_daily_plan_adjustment,
    _get_active_training_plan,
    _normalize_storage_plan_row,
)
from agent.plans import (
    _build_goal_plan_fallback,
    _build_structured_plan_markdown,
    _build_training_plan_status_markdown,
    _generate_structured_plan_payload,
    _plan_start,
    _summarize_plan_changes,
    _validate_structured_plan,
    _wants_new_plan_intent,
)
from agent.routes.load import (
    _build_current_week_tss_markdown,
    _build_load_trend_markdown,
    _build_recent_tss_by_sport_markdown,
    _build_today_load_status_markdown,
    _build_week_activities_markdown,
)
from agent.routes.records import (
    _build_personal_records_markdown,
)
from agent.routes.profile import (
    _build_config_options_markdown,
    _build_goal_status_markdown,
    _build_hr_threshold_profile_markdown,
    _build_running_threshold_profile_markdown,
)
from agent.routes.period import (
    _build_period_activities_markdown,
)
from agent.routes.sleep import (
    _build_sleep_night_markdown,
    _build_sleep_trend_markdown,
    _resolve_sleep_night,
    _resolve_sleep_window,
)
from agent.startup_status import (
    _build_proactive_status_markdown,
)
from agent.router import (
    _detect_personal_records_sport_intent,
)
from agent.routes.day_data import (
    _build_mcp_factual_query_markdown,
)
from agent.routes.db_count import _build_db_count_markdown

log = logging.getLogger(__name__)

# Rutas que se comprueban antes que la de conteo de actividades: con ellas, una pregunta que además pida contar
# actividades sigue en su ruta (el orden de las comprobaciones de antes de la tabla).
FIRST_ROUTES = frozenset({'config_options', 'plan_status', 'goal_status', 'week_tss', 'week_activities', 'load_trend', 'hr_threshold', 'running_threshold', 'today_load_status', 'period_activities', 'sleep_night', 'sleep_trend', 'activity_details', 'recent_tss_by_sport'})


class RoutesMixin:
    """Respuestas de las rutas deterministas y mixtas."""

    async def _route_db_count(self, user_message: str):
        """Conteo de actividades de la base de datos (activity_tss), sin LLM."""
        return _build_db_count_markdown()

    async def _route_config_options(self, user_message: str):
        """Ruta determinista para estado de plan: evita alucinaciones del LLM cuando la pregunta es "¿tengo plan?" o "¿cuál es mi plan?"."""
        assistant_reply = _build_config_options_markdown()
        return assistant_reply
    async def _route_plan_status(self, user_message: str):
        """Ruta determinista para estado de plan: evita alucinaciones del LLM cuando la pregunta es "¿tengo plan?" o "¿cuál es mi plan?"."""
        assistant_reply = _build_training_plan_status_markdown(self.user_profile)
        return assistant_reply
    async def _route_goal_status(self, user_message: str):
        """Ruta goal_status."""
        assistant_reply = _build_goal_status_markdown(self.user_profile)
        return assistant_reply
    async def _route_week_tss(self, user_message: str):
        """Ruta determinista para TSS semanal: evita ambigüedades del LLM y usa semana natural solicitada (o actual) con actividades reales de Garmin."""
        assistant_reply = await _build_current_week_tss_markdown(self.mcp_session, self.user_profile, user_message)
        assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="week_tss")
        return assistant_reply
    async def _route_week_activities(self, user_message: str):
        """Ruta determinista para actividades semanales: evita formatos inconsistentes y fuerza salida homogénea user-friendly."""
        assistant_reply = await _build_week_activities_markdown(self.mcp_session, user_message)
        assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="week_activities")
        return assistant_reply
    async def _route_load_trend(self, user_message: str):
        """Ruta determinista para tendencia de carga/fatiga (ATL/CTL/TSB). Evita timeouts del LLM en consultas de tendencia histórica."""
        assistant_reply = _build_load_trend_markdown(self.user_profile, user_message)
        assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="load_trend")
        return assistant_reply
    async def _route_hr_threshold(self, user_message: str):
        """Ruta determinista para FC umbral (LTHR): perfil primero y fallback MCP rápido."""
        latest_profile = _load_user_profile()
        if isinstance(latest_profile, dict) and latest_profile:
            self.user_profile = latest_profile
        assistant_reply = await _build_hr_threshold_profile_markdown(self.mcp_session, self.user_profile)
        return assistant_reply
    async def _route_running_threshold(self, user_message: str):
        """Ruta determinista para ritmo umbral de running. Carga el perfil más reciente para evitar responder con valores obsoletos."""
        latest_profile = _load_user_profile()
        if isinstance(latest_profile, dict) and latest_profile:
            self.user_profile = latest_profile
        assistant_reply = _build_running_threshold_profile_markdown(self.user_profile)
        return assistant_reply
    async def _route_today_load_status(self, user_message: str):
        """Ruta determinista para estado de carga/fatiga de hoy con regla aplicada. Evita que consultas de TSS/ATL/CTL/TSB caigan en factual diario genérico."""
        assistant_reply = _build_today_load_status_markdown(self.user_profile)
        assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="today_load_status")
        return assistant_reply
    async def _route_period_activities(self, user_message: str):
        """Ruta period_activities."""
        assistant_reply = await _build_period_activities_markdown(self.mcp_session, user_message)
        return assistant_reply
    async def _route_sleep_night(self, user_message: str):
        """Ruta sleep_night."""
        assistant_reply = await _build_sleep_night_markdown(self.mcp_session, _resolve_sleep_night(user_message))
        return assistant_reply
    async def _route_sleep_trend(self, user_message: str):
        """Tendencia de sueño: ruta mixta (tabla calculada en Python y, siempre, la lectura del LLM sobre ella)."""
        window = _resolve_sleep_window(user_message) or 7
        assistant_reply = await _build_sleep_trend_markdown(self.mcp_session, window)
        assistant_reply = await self._augment_with_llm_coaching_if_needed(
            user_message, assistant_reply, route="sleep_trend", always=True, prompt="coaching/sleep_trend",
            horizon=f"las últimas {window} noches frente a las {window} anteriores",
        )
        return assistant_reply
    async def _route_activity_details(self, user_message: str):
        """Ruta determinista para detalle de entrenamiento por día/fecha. Evita timeouts del LLM en preguntas factuales como "entrenamiento del martes"."""
        assistant_reply = await _build_mcp_factual_query_markdown(
            self.mcp_session,
            self.user_profile,
            user_message,
        )
        assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="activity_details")
        return assistant_reply
    async def _route_recent_tss_by_sport(self, user_message: str):
        """Ruta recent_tss_by_sport."""
        assistant_reply = await _build_recent_tss_by_sport_markdown(
            self.mcp_session,
            user_message,
        )
        return assistant_reply
    async def _route_mcp_factual(self, user_message: str):
        """Ruta MCP-first para consultas factuales de métricas diarias. Evita pasar por LLM cuando la pregunta se puede responder con datos directos de Garmin Connect."""
        assistant_reply = await _build_mcp_factual_query_markdown(
            self.mcp_session,
            self.user_profile,
            user_message,
        )
        assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="mcp_factual")
        return assistant_reply
    async def _route_daily_readiness(self, user_message: str):
        """Ruta determinista para readiness diario: evita respuestas del LLM con métricas inventadas y fuerza snapshot real de Garmin."""
        snapshot = await self.collect_startup_snapshot_48h()
        snapshot["profile_changes"] = []
        active_plan = _get_active_training_plan(self.user_profile)
        snapshot["plan_assigned"] = bool(active_plan)
        snapshot["daily_plan_decision"] = _compute_daily_plan_adjustment(snapshot, active_plan) or {}
        if active_plan:
            snapshot["plan_recommendation"] = _build_startup_plan_recommendation(active_plan)
        assistant_reply = _build_proactive_status_markdown(snapshot)
        assistant_reply = await self._augment_with_llm_coaching_if_needed(
            user_message, assistant_reply, route="daily_readiness",
            daily_plan_decision=snapshot.get("daily_plan_decision") or {},
        )
        return assistant_reply
    async def _route_planning(self, user_message: str, force_new: bool = False):
        """Planificación: el LLM construye el plan nuevo (agent/plan_llm.py) y el código lo comprueba; si no lo consigue, el
        motor de Kairos hace uno de respaldo y se avisa. Se guarda versionado en la base de datos."""
        try:
            previous_plan_row = None
            previous_plan = None
            previous_sessions: list[dict] = []
            try:
                previous_plan_row = _storage.get_active_training_plan()
                previous_plan = _normalize_storage_plan_row(previous_plan_row)
                if previous_plan and previous_plan.get("id"):
                    previous_sessions = _storage.list_training_plan_sessions(str(previous_plan.get("id")))
            except (RuntimeError, ValueError, TypeError, OSError, KeyError):
                previous_plan = _get_active_training_plan(self.user_profile)
                previous_sessions = []

            load_context = await self._build_plan_load_context()
            # force_new: `/plan crear` (28/10) siempre crea uno nuevo; en el chat, solo si se pide.
            wants_new = force_new or _wants_new_plan_intent(user_message)
            new_plan = None
            notice = ""
            if not wants_new and previous_plan and previous_plan.get("id") and \
                    (previous_plan.get("plan_data") or {}).get("author") == "llm":
                return await self._route_plan_revision(user_message, previous_plan, previous_sessions, load_context)
            if wants_new or not (previous_plan and previous_plan.get("id")):
                # El plan lo construye el LLM del atleta (28/10); el código lo comprueba y calcula ritmos, km y TSS.
                try:
                    new_plan, new_sessions, llm_errors = await self._build_plan_with_llm(user_message, load_context)
                except LLMAuthError:
                    raise
                except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, KeyError, _provider_error()) as exc:
                    new_plan, llm_errors = None, [f"{type(exc).__name__}: {exc}"]
                if new_plan is None:
                    log.warning("[PLAN] el LLM no consiguió un plan válido: %s", " | ".join(llm_errors[:6]))
                    notice = (f"> ⚠️ Tu modelo ({getattr(self, 'model', '')}) no consiguió un plan que cumpla las reglas "
                              f"de Kairos ({len(llm_errors)} fallo(s); el primero: {llm_errors[0] if llm_errors else 'n/d'}). "
                              "Este es el plan de respaldo del motor de Kairos.\n\n")
            if new_plan is None:
                new_plan, new_sessions = _generate_structured_plan_payload(
                    self.user_profile,
                    user_message,
                    base_plan=previous_plan,
                    load_context=load_context,
                )
            validation_errors = _validate_structured_plan(new_plan, new_sessions, self.user_profile)
            if validation_errors:
                assistant_reply = (
                    "## ⚠️ No pude persistir el plan propuesto\n\n"
                    + "\n".join(f"- {err}" for err in validation_errors)
                    + "\n\nAjusta perfil/objetivo con `/perfil editar objetivo` y lo regenero."
                )
            else:
                if previous_plan and previous_plan.get("id") and not wants_new:
                    persisted = _storage.update_training_plan(
                        str(previous_plan.get("id")),
                        {
                            "title": new_plan.get("title"),
                            "description": new_plan.get("description"),
                            "objective": new_plan.get("objective"),
                            "difficulty": new_plan.get("difficulty"),
                            "duration_weeks": new_plan.get("duration_weeks"),
                            "status": "active",
                            "source": "agent_structured_plan",
                            "plan_data": dict(new_plan.get("plan_data") or {}),
                        },
                        sessions=new_sessions,
                        change_reason="agent_structured_adjustment",
                    )
                    persisted_plan = _normalize_storage_plan_row(persisted) or new_plan
                else:
                    persisted = _storage.create_training_plan(
                        new_plan,
                        sessions=new_sessions,
                        change_reason="agent_structured_creation",
                    )
                    persisted_plan = _normalize_storage_plan_row(persisted) or new_plan

                change_summary = _summarize_plan_changes(
                    previous_plan,
                    persisted_plan,
                    previous_sessions,
                    new_sessions,
                )
                assistant_reply = notice + _build_structured_plan_markdown(
                    persisted_plan,
                    new_sessions,
                    change_summary,
                    profile=self.user_profile,
                )

                # Espejo backward-compatible en perfil.
                persisted_plan.setdefault("target_race", (new_plan.get("plan_data") or {}).get("target_race"))
                persisted_plan.setdefault("target_race_date", (new_plan.get("plan_data") or {}).get("target_race_date"))
                self.user_profile["training_plan"] = persisted_plan
                _save_user_profile(self.user_profile)
                self.__dict__.pop("_active_plan_cache", None)  # el bloque "Plan activo" se recarga

            return assistant_reply
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            log.warning("structured planning route failed, using goal fallback: %s", exc)
            self._log_chat_error(
                stage="planning_route",
                error=exc,
                extra={"user_message": user_message},
            )
            assistant_reply = _build_goal_plan_fallback(self.user_profile)
            return assistant_reply, "planning_fallback"
    async def _route_plan_revision(self, user_message: str, plan: dict, sessions: list[dict], load_context: dict) -> str:
        """Revisión del plan del LLM con lo que cuenta el atleta (28/10): cambia días desde hoy hasta el final de la
        semana siguiente, el código los comprueba y se guarda una versión nueva. Si no pasa las reglas, no se toca."""
        try:
            new_sessions, reply, errors, messages = await self._revise_plan_with_llm(
                user_message, plan, sessions, load_context)
        except LLMAuthError:
            raise
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, KeyError, _provider_error()) as exc:
            new_sessions, reply, errors, messages = None, "", [f"{type(exc).__name__}: {exc}"], []
        if errors:
            log.warning("[PLAN] revisión sin plan válido: %s", " | ".join(errors[:6]))
            return ("## ⚠️ No pude revisar el plan\n\n" + "\n".join(f"- {e}" for e in errors[:8])
                    + "\n\nEl plan sigue como estaba.")
        if reply:
            reply = await self._apply_output_sensors(reply, messages, "plan_revision")
        if new_sessions is None:
            return reply or "No hace falta cambiar el plan."
        old = {(int(s.get("week_index") or 0), int(s.get("day_index") or 0)):
               (s.get("structured_workout") or {}).get("plan_day") for s in sessions}
        changed = [s for s in new_sessions
                   if old.get((s["week_index"], s["day_index"])) != (s.get("structured_workout") or {}).get("plan_day")]
        pd = dict(plan.get("plan_data") or {})
        weeks = int(plan.get("duration_weeks") or 0)
        pd["week_minutes"] = [round(sum(float(s.get("duration_min") or 0) for s in new_sessions
                                        if int(s.get("week_index") or 0) == w), 1) for w in range(1, weeks + 1)]
        persisted = _storage.update_training_plan(
            str(plan["id"]),
            {k: plan.get(k) for k in ("title", "description", "objective", "difficulty", "duration_weeks")}
            | {"status": "active", "source": "agent_llm_plan", "plan_data": pd},
            sessions=new_sessions,
            change_reason="llm_revision",
        )
        persisted_plan = _normalize_storage_plan_row(persisted) or {**plan, "plan_data": pd}
        self.user_profile["training_plan"] = persisted_plan
        _save_user_profile(self.user_profile)
        self.__dict__.pop("_active_plan_cache", None)  # el bloque "Plan activo" se recarga
        start = _plan_start(persisted_plan) or _plan_start(plan)
        zones, _ = _resolve_hr_zones(self.user_profile, "running")
        lines = []
        for s in changed:
            day = start + timedelta(weeks=s["week_index"] - 1, days=s["day_index"] - 1)
            sl = _run_plan.session_lines(s, day, zones)
            lines.append(f"- **{sl[0]}**")
            lines += [f"  - {x.strip()}" for x in sl[1:]]
        return (reply + "\n\n" if reply else "") + "## 🗓️ Cambios en el plan\n" + "\n".join(lines) + \
            "\n\nGuardado como una versión nueva del plan (`/plan ver` para verlo entero)."

    async def _route_personal_records(self, user_message: str):
        """Récords personales: distancia y marca desde la primera respuesta; si Garmin no los da, sigue el LLM."""
        try:
            available_tool_names = {
                (item.get("function") or {}).get("name")
                for item in (self.tools_schema or [])
                if isinstance(item, dict)
            }
            records_tool = None
            if "get_personal_record" in available_tool_names:
                records_tool = "get_personal_record"
            elif "get_personal_records" in available_tool_names:
                records_tool = "get_personal_records"

            if records_tool:
                records_raw = await call_tool(self.mcp_session, records_tool, {})
                records_compact = _compact_tool_result(records_raw, records_tool)
                if records_compact and records_compact != "(sin datos)" and not _is_no_data_result(records_raw):
                    records_sport = _detect_personal_records_sport_intent(user_message, self.conversation_history)
                    assistant_reply = _build_personal_records_markdown(records_compact, preferred_sport=records_sport)
                    return assistant_reply
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            log.debug("personal records route falló: %s", exc)
            self._log_chat_error(
                stage="personal_records_route",
                error=exc,
                extra={"user_message": user_message},
            )
        return None

ROUTE_HANDLERS: dict[str, str] = {
    "db_count": "_route_db_count",
    "config_options": "_route_config_options",
    "plan_status": "_route_plan_status",
    "goal_status": "_route_goal_status",
    "week_tss": "_route_week_tss",
    "week_activities": "_route_week_activities",
    "load_trend": "_route_load_trend",
    "hr_threshold": "_route_hr_threshold",
    "running_threshold": "_route_running_threshold",
    "today_load_status": "_route_today_load_status",
    "period_activities": "_route_period_activities",
    "sleep_night": "_route_sleep_night",
    "sleep_trend": "_route_sleep_trend",
    "activity_details": "_route_activity_details",
    "recent_tss_by_sport": "_route_recent_tss_by_sport",
    "mcp_factual": "_route_mcp_factual",
    "daily_readiness": "_route_daily_readiness",
    "planning": "_route_planning",
    "personal_records": "_route_personal_records",
}
