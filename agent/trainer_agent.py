"""
trainer_agent.py
Agente entrenador personal que combina OpenAI con las herramientas
de Garmin Connect a través del servidor MCP.
"""

import os
import time
import logging
import json
import asyncio
import re
from datetime import date, timedelta
from typing import Any

from mcp import ClientSession

from agent import tool_groups as _tool_groups
from agent.mcp.client import (
    call_tool,
    reset_tool_transparency_events,
    consume_tool_transparency_events,
    record_tool_failure,
)
from agent import storage as _storage
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _find_hr_zones_in_json,
    _parse_hr_zones_list,
)
from agent.prompts import (
    _load_coaching_rules,
    _load_system_prompt,
    _render_prompt,
)
from agent.llm import (
    _llm_timeout_seconds,
    _tool_timeout_seconds,
)
from agent.dates import (
    _extract_iso_date_from_text,
    _find_impossible_date,
    _normalize_date_args,
    _normalize_trend_date_range,
    _parse_iso_date_safe,
)
from agent.knowledge import (
    _load_athlete_knowledge_chunks,
)
from agent.tool_results import (
    _activity_id_int,
    _compact_tool_result,
    _extract_activities_list,
    _is_no_data_result,
)
from agent.data_access import (
    _add_ia_tokens,
    _load_stored_activity_tss,
    _load_user_profile,
    _save_history_entries,
    _save_user_profile,
)
from agent.garmin_data import (
    _GARMIN_ZONE_NAMES_ES,
    _get_activity_name_es,
)
from agent.kairos_tools import (
    _kairos_correlate,
    _kairos_load_trends,
    _kairos_weekly_sport_breakdown,
)
from agent.daily_engine import (
    _build_startup_plan_recommendation,
    _build_today_session_frame,
    _compute_daily_plan_adjustment,
    _get_active_training_plan,
    _normalize_storage_plan_row,
)
from agent.plans import (
    _build_goal_plan_fallback,
    _build_structured_plan_markdown,
    _build_training_plan_status_markdown,
    _generate_structured_plan_payload,
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
    _format_load_fatigue_summary,
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
from agent.activity_analysis import (
    _build_activity_analysis_block,
    _build_activity_candidates_payload,
    _build_post_activity_section_spec,
    _find_activity_id_by_date,
    _normalize_get_activity_args,
    _resolve_activity_id_from_query,
    _resolve_last_activity,
)
from agent.startup_status import (
    _build_proactive_status_markdown,
)
from agent.replies import (
    _build_mcp_error_warning_markdown,
    _build_mcp_transparency_markdown,
    _is_generic_needs_more_info_reply,
    _is_no_access_reply,
)
from agent.router import (
    ToolRouter,
    _detect_personal_records_sport_intent,
    _is_personal_records_followup_intent,
    _is_personal_records_intent,
    _is_planning_intent,
    _is_post_activity_feedback_intent,
    _is_today_training_question,
    _is_tomorrow_workout_intent,
)
from agent.routes.day_data import (
    _build_mcp_factual_query_markdown,
    _build_recovery_fallback_snapshot,
)
from agent.coaching import (
    _calendar_block,
    _coaching_horizon,
)
from agent.load_compute import LoadMixin
from agent.startup import StartupMixin
from agent.context import ContextMixin
from agent.llm_calls import LLMMixin

log = logging.getLogger(__name__)


# ─── Funciones de persistencia ───────────────────────────────────────────────
# Thin wrappers sobre agent.storage para mantener compatibilidad con imports
# existentes en main.py y los tests. Toda la lógica de persistencia
# vive en agent/storage.py.


# ─── Rutas factuales por periodo y del sueño (punto 3 de producción, 25/10) ─────
# Preguntas que el LLM resolvía leyendo series y en las que se equivocaba (exploración del 24/10):
# kilómetros de un periodo, la actividad más larga de un mes y el sueño de anoche. Las responde Python.


# ─── Proveedores de LLM y su clave ──────────────────────────────────────────


# ─── Herramientas internas Kairos (kairos_*) ─────────────────────────────────
# Estas tools operan sobre datos ya almacenados en el perfil (load_metrics.series)
# o sobre actividades Garmin MCP, y se procesan en Python puro sin llamar al LLM.


# ─── Helpers para cálculo incremental de carga/fatiga ─────────────────────────


class TrainerAgent(LoadMixin, StartupMixin, ContextMixin, LLMMixin):
    """
    Agente entrenador personal que usa OpenAI + Garmin MCP.
    Mantiene historial de conversación y llama herramientas de Garmin
    automáticamente según lo que necesite para responder al usuario.
    """

    # Preguntas del usuario en la sesión anterior (se rellena en initialize); tupla: no se comparte mutable.
    previous_session_questions: tuple[str, ...] = ()

    def __init__(self, mcp_session: ClientSession, provider: dict | None = None):
        self.mcp_session = mcp_session
        # Proveedor de IA del atleta (fila de ia_provider); sin él, el de la base de datos.
        self.set_provider(provider or _storage.get_ia_provider() or {})
        self.system_prompt = _load_system_prompt()
        self.user_profile = _load_user_profile()
        self.conversation_history: list[dict] = []
        self.tools_schema: list[dict] = []
        self.knowledge_chunks, self.knowledge_sources = _load_athlete_knowledge_chunks(
            os.environ.get("ATHLETE_KB_PATHS", "")
        )
        stored_kb = (_storage.load_athlete_knowledge() or "").strip()
        if stored_kb:
            self.knowledge_chunks.append({"source": "db:athlete_knowledge", "text": stored_kb[:4000]})
            if "db:athlete_knowledge" not in self.knowledge_sources:
                self.knowledge_sources.append("db:athlete_knowledge")
        
        # Variables para tracking de tokens de la sesión
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.tool_router = ToolRouter()

    async def _handle_internal_tool(self, tool_name: str, arguments: dict) -> str:
        """Despacha herramientas internas kairos_* sin llamar al servidor MCP."""
        args = arguments if isinstance(arguments, dict) else {}
        if tool_name == _tool_groups.REQUEST_TOOL:
            group = str(args.get("grupo") or "")
            if group not in _tool_groups.GROUPS:
                return json.dumps({"error": f"Grupo '{group}' no existe.", "grupos": sorted(_tool_groups.GROUPS)},
                                  ensure_ascii=False)
            groups = getattr(self, "_turn_tool_groups", None)
            if groups is not None and groups:
                groups.add(group)
            log.info("[TOOL] el LLM pide el grupo %s", group)
            return json.dumps({"ok": True, "grupo": group, "herramientas": list(_tool_groups.GROUPS[group]),
                               "nota": "Ya las tienes disponibles: llámalas en tu siguiente paso."},
                              ensure_ascii=False)
        if tool_name == "kairos_load_trends":
            return _kairos_load_trends(self.user_profile, weeks_back=int(args.get("weeks_back") or 8))
        elif tool_name == "kairos_correlate":
            return _kairos_correlate(
                self.user_profile,
                metric_a=str(args.get("metric_a") or "tss"),
                metric_b=str(args.get("metric_b") or "tsb"),
                weeks_back=int(args.get("weeks_back") or 8),
            )
        elif tool_name == "kairos_weekly_sport_breakdown":
            return await _kairos_weekly_sport_breakdown(
                self.mcp_session,
                weeks_back=int(args.get("weeks_back") or 4),
                sport_type=str(args.get("sport_type") or ""),
            )
        else:
            return json.dumps({"error": f"Herramienta interna '{tool_name}' no reconocida."}, ensure_ascii=False)

    async def _finalize_chat_reply(
        self,
        user_message: str,
        assistant_reply: str,
        route: str,
    ) -> str:
        """Persiste el turno y emite hook post-mensaje de forma uniforme."""
        transparency_events = consume_tool_transparency_events()
        if transparency_events:
            assistant_reply = (
                _build_mcp_error_warning_markdown(transparency_events)
                + assistant_reply
                + _build_mcp_transparency_markdown(transparency_events)
            )

        self.conversation_history.append({"role": "user", "content": user_message})
        self.conversation_history.append({"role": "assistant", "content": assistant_reply})
        _save_history_entries([("user", user_message), ("assistant", assistant_reply)])
        self._log_turn_stats(route)
        return assistant_reply

    def _log_turn_stats(self, route: str) -> None:
        """Una línea por pregunta en agent.log: tiempo total y consultas a Supabase (28/10, pendiente 8)."""
        db = _storage.db_stats()
        started = getattr(self, "_turn_started", None)
        total = f"{time.perf_counter() - started:.1f}s" if started else "?"
        detail = ", ".join(f"{name}×{n}" for name, n in sorted(db["by_function"].items(), key=lambda kv: -kv[1]))
        log.info("[TURNO] ruta=%s total=%s supabase=%d consultas %.2fs, %d desde la caché (%s)", route, total, db["calls"],
                 db["seconds"], db["cache_hits"], detail)

    async def _today_frame_for_loop(self, prefetch_today: dict | None = None) -> tuple[str, str | None]:
        """Marco de la sesión de hoy y decisión del motor para el bucle con tools (G15).

        Los mismos datos y el mismo motor que la ruta de estado del día. Si algo falla, el bucle sigue sin marco.
        """
        try:
            snapshot = await self.collect_startup_snapshot_48h(prefetch_today=prefetch_today)
            decision = _compute_daily_plan_adjustment(snapshot, _get_active_training_plan(self.user_profile)) or {}
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, KeyError) as exc:
            self._log_chat_error(stage="today_frame_for_loop", error=exc)
            return "", None
        frame = _build_today_session_frame(decision, self.user_profile)
        log.info("[G15] marco de hoy para el bucle: decision=%s", decision.get("decision") or "—")
        return frame, (decision.get("decision") if frame else None)

    async def chat(self, user_message: str) -> str:
        """
        Procesa un mensaje del usuario y devuelve la respuesta del agente.
        Gestiona automáticamente las llamadas a herramientas de Garmin.

        Las lecturas de Supabase de la pregunta pasan por una caché que se abre aquí y se cierra al terminar,
        también si la pregunta falla (pendiente 8b, 28/10).
        """
        self._turn_started = time.perf_counter()
        _storage.reset_db_stats()
        _storage.open_read_cache()
        try:
            return await self._answer(user_message)
        finally:
            _storage.close_read_cache()

    async def _answer(self, user_message: str) -> str:
        """Responde una pregunta: rutas deterministas, rutas mixtas o bucle con tools (lo llama `chat`)."""
        if not hasattr(self, "tool_router") or not isinstance(getattr(self, "tool_router", None), ToolRouter):
            self.tool_router = ToolRouter()

        impossible = _find_impossible_date(user_message)
        if impossible:
            return await self._finalize_chat_reply(
                user_message,
                f"La fecha {impossible} no existe en el calendario. ¿Qué día querías consultar? (formato DD/MM/AAAA)",
                route="invalid_date",
            )

        reset_tool_transparency_events()
        _prefetch_cache: dict = {}  # poblado por el pre-fetch si user_date == hoy
        route_key = self.tool_router.route_key(
            user_message,
            self.conversation_history,
            self.user_profile,
        )

        # Ruta determinista para estado de plan: evita alucinaciones del LLM
        # cuando la pregunta es "¿tengo plan?" o "¿cuál es mi plan?".
        if route_key == "config_options":
            assistant_reply = _build_config_options_markdown()
            return await self._finalize_chat_reply(user_message, assistant_reply, route="config_options")

        # Ruta determinista para estado de plan: evita alucinaciones del LLM
        # cuando la pregunta es "¿tengo plan?" o "¿cuál es mi plan?".
        if route_key == "plan_status":
            assistant_reply = _build_training_plan_status_markdown(self.user_profile)
            return await self._finalize_chat_reply(user_message, assistant_reply, route="plan_status")

        if route_key == "goal_status":
            assistant_reply = _build_goal_status_markdown(self.user_profile)
            return await self._finalize_chat_reply(user_message, assistant_reply, route="goal_status")

        # Ruta determinista para TSS semanal: evita ambigüedades del LLM
        # y usa semana natural solicitada (o actual) con actividades reales de Garmin.
        if route_key == "week_tss":
            assistant_reply = await _build_current_week_tss_markdown(self.mcp_session, self.user_profile, user_message)
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="week_tss")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="week_tss")

        # Ruta determinista para actividades semanales: evita formatos inconsistentes
        # y fuerza salida homogénea user-friendly.
        if route_key == "week_activities":
            assistant_reply = await _build_week_activities_markdown(self.mcp_session, user_message)
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="week_activities")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="week_activities")

        # Ruta determinista para tendencia de carga/fatiga (ATL/CTL/TSB).
        # Evita timeouts del LLM en consultas de tendencia histórica.
        if route_key == "load_trend":
            assistant_reply = _build_load_trend_markdown(self.user_profile, user_message)
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="load_trend")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="load_trend")

        # Ruta determinista para FC umbral (LTHR): perfil primero y fallback MCP rápido.
        if route_key == "hr_threshold":
            latest_profile = _load_user_profile()
            if isinstance(latest_profile, dict) and latest_profile:
                self.user_profile = latest_profile
            assistant_reply = await _build_hr_threshold_profile_markdown(self.mcp_session, self.user_profile)
            return await self._finalize_chat_reply(user_message, assistant_reply, route="hr_threshold")

        # Ruta determinista para ritmo umbral de running.
        # Carga el perfil más reciente para evitar responder con valores obsoletos.
        if route_key == "running_threshold":
            latest_profile = _load_user_profile()
            if isinstance(latest_profile, dict) and latest_profile:
                self.user_profile = latest_profile
            assistant_reply = _build_running_threshold_profile_markdown(self.user_profile)
            return await self._finalize_chat_reply(user_message, assistant_reply, route="running_threshold")

        # Ruta determinista para estado de carga/fatiga de hoy con regla aplicada.
        # Evita que consultas de TSS/ATL/CTL/TSB caigan en factual diario genérico.
        if route_key == "today_load_status":
            assistant_reply = _build_today_load_status_markdown(self.user_profile)
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="today_load_status")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="today_load_status")

        if route_key == "period_activities":
            assistant_reply = await _build_period_activities_markdown(self.mcp_session, user_message)
            return await self._finalize_chat_reply(user_message, assistant_reply, route="period_activities")

        if route_key == "sleep_night":
            assistant_reply = await _build_sleep_night_markdown(self.mcp_session, _resolve_sleep_night(user_message))
            return await self._finalize_chat_reply(user_message, assistant_reply, route="sleep_night")

        # Tendencia de sueño: ruta mixta (tabla calculada en Python y, siempre, la lectura del LLM sobre ella).
        if route_key == "sleep_trend":
            window = _resolve_sleep_window(user_message) or 7
            assistant_reply = await _build_sleep_trend_markdown(self.mcp_session, window)
            assistant_reply = await self._augment_with_llm_coaching_if_needed(
                user_message, assistant_reply, route="sleep_trend", always=True, prompt="coaching/sleep_trend",
                horizon=f"las últimas {window} noches frente a las {window} anteriores",
            )
            return await self._finalize_chat_reply(user_message, assistant_reply, route="sleep_trend")

        # Ruta determinista para detalle de entrenamiento por día/fecha.
        # Evita timeouts del LLM en preguntas factuales como "entrenamiento del martes".
        if route_key == "activity_details":
            assistant_reply = await _build_mcp_factual_query_markdown(
                self.mcp_session,
                self.user_profile,
                user_message,
            )
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="activity_details")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="activity_details")

        if route_key == "recent_tss_by_sport":
            assistant_reply = await _build_recent_tss_by_sport_markdown(
                self.mcp_session,
                user_message,
            )
            return await self._finalize_chat_reply(user_message, assistant_reply, route="recent_tss_by_sport")

        # Ruta determinista para consultas de conteo de actividades en DB
        _message_lc = (user_message or "").lower()
        _count_query = bool(re.search(
            r'\b(cu[aá]ntas?\s+actividades|total\s+actividades|actividades\s+registradas|actividades\s+en\s+(la\s+)?(base|bbdd|db)|cuantos\s+entrenos)\b',
            _message_lc
        ))
        if not _count_query and "actividades" in _message_lc and (
            "base de datos" in _message_lc or "db" in _message_lc or "bbdd" in _message_lc
        ):
            _count_query = True
        if _count_query:
            # Fuente: activity_tss (una fila por actividad con su TSS), no la serie diaria de 120 días.
            _rows = list(_load_stored_activity_tss("2000-01-01").values())
            _by_sport: dict[str, int] = {}
            for _r in _rows:
                _label = _get_activity_name_es(str(_r.get("sport") or "")) or str(_r.get("sport") or "otro")
                _by_sport[_label] = _by_sport.get(_label, 0) + 1
            _dates = sorted(str(_r.get("start_date") or "") for _r in _rows if _r.get("start_date"))

            def _dmy(iso: str) -> str:
                _d = _parse_iso_date_safe(iso)
                return _d.strftime("%d/%m/%Y") if _d else iso

            _range = f"{_dmy(_dates[0])} → {_dmy(_dates[-1])}" if _dates else "sin datos"
            assistant_reply = "\n".join([
                "## 🧭 Resumen",
                f"Tengo registradas **{len(_rows)} actividades** con su TSS en la base de datos de Kairos.",
                "",
                "## 📊 Métricas clave",
                "| Dato | Valor |",
                "|---|---:|",
                f"| Actividades con TSS | {len(_rows)} |",
                f"| Días con entrenamiento | {len(set(_dates))} |",
                f"| Rango cubierto | {_range} |",
                *[f"| {sport} | {n} |" for sport, n in sorted(_by_sport.items(), key=lambda kv: -kv[1])],
                "",
                "## ✅ Recomendación",
                "- Es el histórico con el que Kairos calcula tu Estado físico, Fatiga y Forma (línea base desde la fecha inicial).",
                "",
                "## 🎯 Próximo paso",
                "- Si quieres, te listo los TSS de las últimas actividades de un deporte.",
                "- Fuente: respuesta determinista (activity_tss, sin LLM).",
            ])
            return await self._finalize_chat_reply(user_message, assistant_reply, route="db_count")

        # Ruta MCP-first para consultas factuales de métricas diarias.
        # Evita pasar por LLM cuando la pregunta se puede responder con
        # datos directos de Garmin Connect.
        if route_key == "mcp_factual":
            assistant_reply = await _build_mcp_factual_query_markdown(
                self.mcp_session,
                self.user_profile,
                user_message,
            )
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="mcp_factual")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="mcp_factual")

        # Ruta determinista para readiness diario: evita respuestas del LLM
        # con métricas inventadas y fuerza snapshot real de Garmin.
        if route_key == "daily_readiness":
            snapshot = await self.collect_startup_snapshot_48h(prefetch_today=_prefetch_cache)
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
            return await self._finalize_chat_reply(user_message, assistant_reply, route="daily_readiness")

        # Ruta funcional de planificación: generación/actualización estructurada,
        # persistida y versionada en DB sin depender del LLM.
        if route_key == "planning":
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

                new_plan, new_sessions = _generate_structured_plan_payload(
                    self.user_profile,
                    user_message,
                    base_plan=previous_plan,
                    load_context=await self._build_plan_load_context(),
                )
                validation_errors = _validate_structured_plan(new_plan, new_sessions, self.user_profile)
                if validation_errors:
                    assistant_reply = (
                        "## ⚠️ No pude persistir el plan propuesto\n\n"
                        + "\n".join(f"- {err}" for err in validation_errors)
                        + "\n\nAjusta perfil/objetivo con `/perfil editar objetivo` y lo regenero."
                    )
                else:
                    wants_new = _wants_new_plan_intent(user_message)
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
                    assistant_reply = _build_structured_plan_markdown(
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

                return await self._finalize_chat_reply(user_message, assistant_reply, route="planning")
            except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                log.warning("structured planning route failed, using goal fallback: %s", exc)
                self._log_chat_error(
                    stage="planning_route",
                    error=exc,
                    extra={"user_message": user_message},
                )
                assistant_reply = _build_goal_plan_fallback(self.user_profile)
                return await self._finalize_chat_reply(user_message, assistant_reply, route="planning_fallback")

        # Ruta directa para récords personales: evita respuestas de "sin acceso"
        # y asegura que se entreguen distancia + marca desde la primera respuesta.
        force_personal_records = route_key == "personal_records"
        if force_personal_records:
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
                        return await self._finalize_chat_reply(user_message, assistant_reply, route="personal_records")
            except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                log.debug("personal records route falló: %s", exc)
                self._log_chat_error(
                    stage="personal_records_route",
                    error=exc,
                    extra={"user_message": user_message},
                )

        # Mensajes del LLM con su prompt de sistema (perfil, carga, plan…): solo las preguntas que llegan aquí los
        # usan; antes se construían también para las rutas deterministas, con una lectura de Supabase de más (8b).
        messages = self._build_messages(user_message)

        # Pre-fetch proactivo: si el usuario menciona una fecha explícita,
        # resolver y cargar la actividad + contexto completo ANTES del bucle LLM.
        analysis_block_for_rescue = ""
        prefetch_date_for_rescue = ""
        user_date = _extract_iso_date_from_text(user_message)
        is_week_range_query = bool(re.search(r"\b(semana|week)\b", (user_message or "").lower()))

        # "Mi última actividad / mi último trail…" sin fecha: la actividad concreta (28/10, `_resolve_last_activity`).
        last_activity_id = None
        if not user_date and not is_week_range_query:
            user_date, last_activity_id = await _resolve_last_activity(self.mcp_session, user_message)

        if user_date and not is_week_range_query:
            # Intento 1: get_activities_fordate para la fecha exacta
            pre_id = last_activity_id
            if pre_id is None:
                try:
                    _raw_date_acts = await call_tool(
                        self.mcp_session, "get_activities_fordate",
                        {"date": user_date},
                    )
                    _acts_day = _extract_activities_list(_raw_date_acts)
                    if _acts_day:
                        _first = _acts_day[0]
                        pre_id = (_first.get("id") or _first.get("activityId")
                                  or _first.get("activity_id"))
                        if pre_id:
                            pre_id = int(pre_id)
                except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as _e:
                    log.debug("pre_fetch get_activities_fordate fallback: %s", _e)

            # Intento 2: paginación por fecha si el anterior falló
            if pre_id is None:
                pre_id = await _find_activity_id_by_date(self.mcp_session, user_date)

            log.info("pre_fetch: user_date=%s pre_id=%s", user_date, pre_id)
            if pre_id is not None:
                raw_pre = await call_tool(self.mcp_session, "get_activity", {"activity_id": pre_id})
                pre_data = _compact_tool_result(raw_pre, "get_activity")
                # Duración total de la actividad — denominador correcto para % zonas (igual que Garmin Connect)
                _act_dur_s = None
                try:
                    _act_raw_j = json.loads(raw_pre) if raw_pre else {}
                    _dur_raw = (_act_raw_j.get("duration") or _act_raw_j.get("movingDuration")
                                or _act_raw_j.get("duration_seconds"))
                    if _dur_raw is not None:
                        _act_dur_s = float(_dur_raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
                context_parts = [f"ACTIVIDAD (activityId={pre_id}, fecha={user_date}):\n{pre_data}"]

                # Fetch paralelo: body_battery, sleep (2 fechas), hrv, training_load
                _night_before = (date.fromisoformat(user_date) - timedelta(days=1)).isoformat()
                _tl_end = date.today().isoformat()
                _tl_start = (date.today() - timedelta(weeks=4)).isoformat()

                async def _safe_prefetch(tool_name: str, args: dict) -> str | None:
                    try:
                        return await call_tool(self.mcp_session, tool_name, args)
                    except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError):
                        return None

                raw_bb, raw_sleep, raw_sleep_nb, raw_hrv, raw_tl = await asyncio.gather(
                    _safe_prefetch("get_body_battery", {"start_date": user_date, "end_date": user_date}),
                    _safe_prefetch("get_sleep_data", {"date": user_date}),
                    _safe_prefetch("get_sleep_data", {"date": _night_before}),
                    _safe_prefetch("get_hrv_data", {"date": user_date}),
                    _safe_prefetch("get_training_load_trend", {"start_date": _tl_start, "end_date": _tl_end}),
                )

                # body_battery
                if raw_bb is not None:
                    log.debug(f"body_battery raw({user_date}): {(raw_bb or '')[:200]}")
                    bb_data = _compact_tool_result(raw_bb, "get_body_battery")
                    log.debug(f"body_battery compact({user_date}): {bb_data[:120] if bb_data else 'None'}")
                    if bb_data and bb_data != "(sin datos)":
                        context_parts.append(f"BODY BATTERY del {user_date}:\n{bb_data}")

                # sleep: preferir user_date, fallback night_before (ambas ya disponibles)
                _sleep_added = False
                for _sleep_date, _raw_s in ((user_date, raw_sleep), (_night_before, raw_sleep_nb)):
                    if _raw_s is None:
                        continue
                    _sd = _compact_tool_result(_raw_s, "get_sleep_data")
                    log.info(f"sleep({_sleep_date}): {_sd[:120] if _sd else 'None'}")
                    if _sd and _sd != "(sin datos)":
                        context_parts.append(f"SUENO noche previa ({_sleep_date}):\n{_sd}")
                        _sleep_added = True
                        break
                if not _sleep_added:
                    log.info("sleep: no se encontraron datos en ninguna fecha")

                # hrv
                if raw_hrv is not None:
                    hrv_data = _compact_tool_result(raw_hrv, "get_hrv_data")
                    log.info(f"hrv({user_date}): {hrv_data[:120] if hrv_data else 'None'}")
                    if hrv_data and hrv_data != "(sin datos)":
                        context_parts.append(f"HRV del {user_date}:\n{hrv_data}")

                # training_load
                if raw_tl is not None:
                    tl_data = _compact_tool_result(raw_tl, "get_training_load_trend")
                    log.debug(f"training_load: {tl_data[:80] if tl_data else 'None'}")
                    if tl_data and tl_data != "(sin datos)":
                        context_parts.append(
                            "CARGA GARMIN (carga nativa de Garmin, NO es TSS; el TSS de Kairos va en el resumen):\n"
                            f"{tl_data}"
                        )

                # Guardar en caché para collect_startup_snapshot_48h (evita re-fetch si es hoy)
                if user_date == date.today().isoformat():
                    _prefetch_cache["body_today"] = raw_bb
                    _prefetch_cache["hrv_today"] = raw_hrv

                # ── Zonas reales de FC ──────────────────────────────────────────────
                # Estrategia 1: buscar en el raw de get_activity (ya disponible, sin llamada extra)
                raw_hr_zones = None
                try:
                    _act_data = json.loads(raw_pre) if raw_pre else {}
                    _zones_in_act = _find_hr_zones_in_json(_act_data)
                    if _zones_in_act:
                        raw_hr_zones = json.dumps(_zones_in_act)
                        log.info("hr_zones: encontradas %d zonas en get_activity", len(_zones_in_act))
                    else:
                        log.info("hr_zones: get_activity no contiene datos de zonas (requiere llamada específica)")
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass

                # Estrategia 2: get_activity_hr_in_timezones (nombre real en garminconnect / garmin-mcp)
                if not raw_hr_zones:
                    for _param in ({"activity_id": pre_id}, {"activityId": pre_id}):
                        try:
                            _raw = await call_tool(self.mcp_session, "get_activity_hr_in_timezones", _param)
                            log.debug("get_activity_hr_in_timezones(%s): %s", _param, (_raw or "")[:200])
                            if not _raw or "Unknown tool" in _raw or "unknown tool" in _raw.lower():
                                log.info("hr_zones: get_activity_hr_in_timezones no disponible en este servidor MCP")
                                break  # no reintentar con otros params si la herramienta no existe
                            if _raw.strip() not in ("null", "[]", "{}", "(sin datos)", ""):
                                _parsed = _parse_hr_zones_list(_raw)
                                if _parsed:
                                    raw_hr_zones = _raw
                                    log.info("hr_zones: %d zonas via get_activity_hr_in_timezones", len(_parsed))
                                    break
                        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError):
                            break

                if raw_hr_zones:
                    context_parts.append(f"ZONAS FC (datos reales):\n{raw_hr_zones}")
                else:
                    log.info("hr_zones: NO se encontraron datos reales de zonas — usando estimación gaussiana")

                # Si tenemos zonas reales, actualizar pre_data para reemplazar estimación gaussiana
                _zones_for_predata = _parse_hr_zones_list(raw_hr_zones)
                if _zones_for_predata:
                    try:
                        _pd = json.loads(pre_data)
                        if isinstance(_pd, dict):
                            _pd.pop("zonas_fc_estimadas", None)
                            _pd.pop("nota_zonas", None)
                            _total_z = sum(float(z.get("secsInZone") or 0) for z in _zones_for_predata)
                            # Garmin Connect divide por duración total, no por suma de zonas
                            _z_denom = _act_dur_s if _act_dur_s and _act_dur_s > 0 else _total_z
                            if _total_z > 0:
                                _zr = {}
                                for _z in sorted(_zones_for_predata, key=lambda x: int(x.get("zoneNumber") or 0)):
                                    _zn = int(_z.get("zoneNumber") or 0)
                                    _zs = float(_z.get("secsInZone") or 0)
                                    _pct = round(_zs / _z_denom * 100, 1)
                                    _lo = _z.get("minHeartRateIn") or "?"
                                    _hi = _z.get("maxHeartRateIn") or "?"
                                    _zname = _z.get("zoneName") or f"Z{_zn}"
                                    _zr[f"Z{_zn}_{_zname}_{_lo}-{_hi}bpm"] = f"{_pct:.1f}% (~{int(_zs/60)} min)"
                                _pd["zonas_fc_reales_garmin"] = _zr
                                _pd["nota_zonas"] = "Zonas reales de Garmin (Tiempo en Zonas del dispositivo)."
                            pre_data = json.dumps(_pd, ensure_ascii=False, separators=(",", ":"))
                            context_parts[0] = f"ACTIVIDAD (activityId={pre_id}, fecha={user_date}):\n{pre_data}"
                            log.info("pre_data: zonas_fc_reales_garmin inyectadas (%d zonas)", len(_zones_for_predata))
                    except (TypeError, ValueError, json.JSONDecodeError, KeyError) as _ze:
                        log.debug("pre_data zone update error: %s", _ze)

                # Construir bloque de análisis pre-computado en Python

                analysis_block = _build_activity_analysis_block(
                    activity_raw=raw_pre,
                    body_battery_raw=next((p for p in context_parts[1:] if "BODY BATTERY" in p), None),
                    sleep_raw=next((p for p in context_parts if "SUENO" in p), None),
                    hrv_raw=next((p for p in context_parts if "HRV" in p), None),
                    hr_zones_raw=raw_hr_zones,
                    stored_tss_row=(
                        _load_stored_activity_tss(str(user_date)).get(_activity_id_int({"activityId": pre_id}))
                        if pre_id is not None and user_date
                        else None
                    ),
                )
                analysis_block_for_rescue = analysis_block
                prefetch_date_for_rescue = user_date

                # Eliminar del array de mensajes cualquier respuesta previa del asistente
                # que sea un análisis de actividad — evita copiar floats crudos y formato viejo.
                # Incluye tanto los headers del bloque pre-computado como el texto libre
                # que el LLM genera, para limpiar también respuestas de sesiones anteriores.
                _ANALYSIS_MARKERS = (
                    # Headers del bloque pre-computado
                    "Resumen ejecutivo", "zonas de FC", "Distribución por zonas",
                    "Plan de recuperación", "Efecto de entrenamiento",
                    "Recomendaciones para la próxima", "Training load:",
                    "Body battery:", "Hidratación recomendada",
                    # Texto libre típico del LLM en español (respuestas de sesiones anteriores)
                    "velocidad media fue", "velocidad máxima fue",
                    "ritmo medio fue", "frecuencia cardíaca media fue",
                    "efecto de entrenamiento de", "zonas de frecuencia cardíaca",
                    "carga de entrenamiento (TSS)", "elevación ganada fue",
                    "tiempo de recuperación de",
                    # Identificadores de la actividad concreta
                    user_date, str(pre_id),
                )
                messages = [
                    msg for msg in messages
                    if not (
                        msg.get("role") == "assistant"
                        and any(m in (msg.get("content") or "") for m in _ANALYSIS_MARKERS)
                    )
                ]

                # Pre-computar texto de zonas para forzar salida exacta al LLM
                _zones_direct_text = None
                if _zones_for_predata:
                    _total_zd = sum(float(z.get("secsInZone") or 0) for z in _zones_for_predata)
                    # Garmin Connect divide por duración total de la actividad, no por suma de zonas
                    _zd_denom = _act_dur_s if _act_dur_s and _act_dur_s > 0 else _total_zd
                    if _total_zd > 0:
                        _sorted_zd = sorted(_zones_for_predata, key=lambda x: int(x.get("zoneNumber") or 0))
                        _zdlines = []
                        for _zdi, _z in enumerate(_sorted_zd):
                            _zn = int(_z.get("zoneNumber") or 0)
                            _zs = float(_z.get("secsInZone") or 0)
                            _pct = round(_zs / _zd_denom * 100, 1)
                            _mins = int(_zs / 60)
                            _zname = (_GARMIN_ZONE_NAMES_ES.get(_zn)
                                      if not _z.get("zoneName") or str(_z.get("zoneName")).startswith("Z")
                                      else _z.get("zoneName"))
                            _lo_d = _z.get("minHeartRateIn") or "?"
                            _hi_d = _z.get("maxHeartRateIn") or "?"
                            # Calcular límite alto desde la siguiente zona si no está disponible
                            if _hi_d == "?" and _lo_d != "?" and _zdi + 1 < len(_sorted_zd):
                                _next_lo = _sorted_zd[_zdi + 1].get("minHeartRateIn") or "?"
                                if _next_lo != "?":
                                    _hi_d = str(int(float(_next_lo)) - 1)
                            if _lo_d != "?" and _hi_d != "?":
                                _rng = f"{_lo_d}–{_hi_d} bpm"
                            elif _lo_d != "?":
                                _rng = f">{_lo_d} bpm"
                            else:
                                _rng = ""
                            _zdlines.append(
                                f"Z{_zn} · {_zname:<14} · {_rng:<14} {_pct:5.1f}% (~{_mins} min)"
                            )
                        _zones_direct_text = "\n".join(_zdlines)

                _zones_override = (
                    "\n" + _render_prompt("activity_analysis/zones", zonas=_zones_direct_text) + "\n"
                    if _zones_direct_text else ""
                )

                _post_activity_spec = _build_post_activity_section_spec(user_date)
                _post_header = str(_post_activity_spec.get("header") or "## 🔄 Recuperación y próximas sesiones")
                _post_section_name = str(_post_activity_spec.get("section_name") or _post_header)
                _post_guidance = str(_post_activity_spec.get("guidance") or "")
                _post_plan_mode = str(_post_activity_spec.get("plan_context") or "recent")

                # Contexto del plan de entrenamiento para la sección de recuperación
                _plan_obj = _get_active_training_plan(self.user_profile)
                if _post_plan_mode == "historical":
                    _plan_ctx = "\n" + _render_prompt("activity_analysis/plan_historical") + "\n"
                elif _plan_obj:
                    _plan_title = _plan_obj.get("title") or _plan_obj.get("name") or "Plan activo"
                    _plan_ctx = "\n" + _render_prompt("activity_analysis/plan_active", plan=_plan_title) + "\n"
                else:
                    _plan_ctx = "\n" + _render_prompt("activity_analysis/plan_none") + "\n"

                messages.insert(len(messages) - 1, {
                    "role": "system",
                    "content": _render_prompt(
                        "activity_analysis/system",
                        fecha=user_date,
                        datos=analysis_block,
                        zonas=_zones_override,
                        plan=_plan_ctx,
                        cabecera_final=_post_header,
                        seccion_final=_post_section_name,
                        guia_final=_post_guidance,
                    ),
                })
            else:
                log.debug(f"Pre-fetch {user_date}: no se encontro actividad")

        # G15 (28/10): qué entrenar hoy, preguntado de forma que no llega a la ruta de estado del día. El LLM
        # recibe la decisión del motor y el marco de la sesión, y S15 comprueba que lo respeta.
        loop_decision = None
        if not analysis_block_for_rescue and _is_today_training_question(user_message):
            frame, loop_decision = await self._today_frame_for_loop(_prefetch_cache)
            if frame:
                messages.insert(len(messages) - 1, {"role": "system", "content": frame})

        _MAX_TOOL_ITER = 15
        # Metatooling (26/10): solo los grupos de tools que pide la pregunta; el LLM puede pedir más.
        self._turn_tool_groups = _tool_groups.groups_for(user_message)
        log.info("[TOOL] grupos de la pregunta: %s", ",".join(sorted(self._turn_tool_groups)) or "todos")
        # Cortacircuitos (24/10): garminconnect espera hasta 30 s por petición y aquí se corta a los 8 s; con
        # Garmin lento, cada llamada nueva se encolaba tras la abandonada y agotaba también su tiempo (5 seguidas,
        # 144 s en una pregunta). Tras 2 timeouts en la misma respuesta no se llama más a Garmin.
        _MAX_TOOL_TIMEOUTS = 2
        tool_timeouts = 0
        iteration = 0
        while True:
            iteration += 1
            if iteration > _MAX_TOOL_ITER:
                log.debug(f"Límite de {_MAX_TOOL_ITER} iteraciones de herramientas alcanzado. Abortando.")
                assistant_reply = "[Lo siento, la consulta requirió demasiadas llamadas a herramientas. Por favor, reformula tu pregunta de forma más concreta.]"
                return await self._finalize_chat_reply(user_message, assistant_reply, route="max_tool_iterations")
            try:
                _t0 = time.perf_counter()
                turn_tools = _tool_groups.select(self.tools_schema, self._turn_tool_groups) if self.tools_schema else []
                log.info("[LLM] ▶ llamada iter=%d modelo=%s route=chat msgs=%d",
                         iteration, self.model, len(messages))
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=turn_tools or None,
                        tool_choice="auto" if turn_tools else None,
                        **({
                            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
                        } if getattr(self, "provider", "") == "nvidia" else {}),
                    ),
                    timeout=_llm_timeout_seconds(),
                )
                log.info("[LLM] ⏱ iter=%d elapsed=%.1fs", iteration, time.perf_counter() - _t0)
            except asyncio.TimeoutError as api_exc:
                log.warning("[LLM] ⏱ iter=%d TIMEOUT elapsed=%.1fs (límite=%.0fs)",
                            iteration, time.perf_counter() - _t0, _llm_timeout_seconds())
                self._log_chat_error(
                    stage="llm_completion_timeout",
                    error=api_exc,
                    extra={"user_message": user_message},
                )
                timeout_msg = (
                    "La consulta ha excedido el tiempo máximo de respuesta del modelo.\n\n"
                    "Prueba una de estas opciones:\n"
                    "- Reintentar la consulta\n"
                    "- Hacer una pregunta más concreta\n"
                    "- Cambiar de proveedor o de modelo con `/proveedor`"
                )
                return await self._finalize_chat_reply(user_message, timeout_msg, route="llm_timeout")
            except Exception as api_exc:
                err_str = str(api_exc)
                self._log_chat_error(
                    stage="llm_completion",
                    error=api_exc,
                    extra={"user_message": user_message},
                )

                if "413" in err_str or "tokens_limit_reached" in err_str or "Request body too large" in err_str:
                    msg = (
                        "La consulta es demasiado extensa para el modelo actual (límite de tokens del proveedor).\n\n"
                        "Prueba con una de estas opciones:\n"
                        "- Haz una pregunta más específica y acotada (ej: *¿Cómo estoy hoy?* en lugar de *analiza 8 semanas*)\n"
                        "- Divide el análisis en pasos: primero métricas de hoy, luego tendencias, luego plan\n"
                        "- Si no estás en red corporativa, reinicia el agente o usa /proveedor para cambiar a un modelo con contexto más grande"
                    )
                    return await self._finalize_chat_reply(user_message, msg, route="llm_request_too_large")
                if "410" in err_str or "end of life" in err_str.lower() or "no longer available" in err_str.lower():
                    msg = (
                        f"El modelo `{self.model}` ha llegado a su fin de vida y ya no está disponible.\n\n"
                        "Elige otro modelo con `/proveedor`."
                    )
                    return await self._finalize_chat_reply(user_message, msg, route="llm_model_eol")
                if (
                    "403" in err_str
                    or "forbidden" in err_str.lower()
                    or "zscaler" in err_str.lower()
                    or "access denied" in err_str.lower()
                ):
                    msg = (
                        "El proveedor del modelo rechazó la petición (403/Forbidden), probablemente por red corporativa o política del proxy.\n\n"
                        "Prueba una de estas opciones:\n"
                        "- Cambiar de proveedor o de modelo con `/proveedor`\n"
                        "- Reintentar desde una red sin bloqueo del proveedor actual\n"
                        "- Hacer una consulta factual de ruta determinista mientras tanto"
                    )
                    return await self._finalize_chat_reply(user_message, msg, route="llm_forbidden")
                raise

            # Track and log token usage
            if getattr(response, "usage", None):
                u = response.usage
                p_toks = getattr(u, "prompt_tokens", 0) or 0
                c_toks = getattr(u, "completion_tokens", 0) or 0
                self.total_prompt_tokens += p_toks
                self.total_completion_tokens += c_toks
                total_step_tokens = p_toks + c_toks
                # Medición del metatooling (26/10): tokens de cada vuelta y cuántas tools se mandaron.
                log.info("[LLM] ◀ iter=%d tools=%d tokens entrada=%d salida=%d",
                         iteration, len(turn_tools), p_toks, c_toks)
                _add_ia_tokens(total_step_tokens)

            message = response.choices[0].message

            # Debug: muestra si el modelo llama herramientas
            if message.tool_calls:
                # Qué pide el LLM en cada vuelta (28/10, pendiente 8a): lo que siempre pide en la primera se puede
                # precargar y ahorrar esa vuelta.
                log.info("[TOOL] iter=%d pide: %s", iteration,
                         "; ".join(f"{tc.function.name}({(tc.function.arguments or '')[:120]})" for tc in message.tool_calls))
            else:
                log.debug(f"Iteración {iteration}: respuesta directa (sin tool calls)")
                log.debug(f"finish_reason: {response.choices[0].finish_reason}")

            # Si el modelo quiere llamar herramientas de Garmin
            if message.tool_calls:
                messages.append(message)

                # Las llamadas de un mismo mensaje del LLM van en paralelo (24/10): el servidor MCP las atiende
                # en hilos con un tope de llamadas simultáneas a Garmin. Cada una devuelve su mensaje "tool" y
                # gather conserva el orden. El cortacircuitos de timeouts se aplica a los mensajes siguientes.
                async def _run_tool_call(tool_call) -> dict:
                    nonlocal tool_timeouts
                    tool_name = tool_call.function.name
                    # Los nombres de tools van en minúsculas; el modelo a veces escribe "Kairos_weekly_..." (24/10).
                    if tool_name and tool_name != tool_name.lower():
                        tool_name = tool_name.lower()
                    # Compatibilidad: algunas guías/prompts antiguos usan plural.
                    if tool_name == "get_personal_records":
                        tool_name = "get_personal_record"
                    try:
                        arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                    # json.loads("null") devuelve None → las tools MCP
                    # esperan un objeto, no null → convertir a {}
                    if arguments is None:
                        arguments = {}
                    # Normalizar fechas: convertir palabras como 'hoy'/'ayer' a ISO
                    arguments = _normalize_date_args(arguments)
                    if tool_name == "get_activity":
                        # Si la pregunta contiene una fecha explícita, SIEMPRE resolver por fecha,
                        # ignorando el activity_id que haya propuesto el modelo (puede ser de
                        # conversaciones anteriores o alucinado).
                        user_date = _extract_iso_date_from_text(user_message)
                        if user_date:
                            resolved_id = await _find_activity_id_by_date(self.mcp_session, user_date)
                            if resolved_id is not None:
                                log.debug(f"Fecha explicita {user_date} -> resolviendo a activity_id={resolved_id} (modelo propuso {arguments.get('activity_id', 'nada')})")
                                arguments = {"activity_id": resolved_id}
                            else:
                                log.debug(f"Fecha explicita {user_date} -> no se encontro actividad ese dia")
                                arguments = {}
                        else:
                            arguments = await _normalize_get_activity_args(
                                self.mcp_session,
                                arguments,
                                user_message=user_message,
                            )
                            if not (isinstance(arguments, dict) and arguments.get("activity_id")):
                                resolved_id = await _resolve_activity_id_from_query(self.mcp_session, user_message)
                                if resolved_id is not None:
                                    arguments = {"activity_id": resolved_id}
                    arguments = _normalize_trend_date_range(tool_name, arguments)


                    # Herramienta que no existe (24/10: el LLM llamó a "goals", que es una tabla citada en el
                    # prompt): no se manda al servidor, que lo devolvía como fallo de Garmin y se avisaba al
                    # usuario de que Garmin no respondía. Se le dice al LLM qué herramientas hay.
                    known_tools = {
                        (item.get("function") or {}).get("name")
                        for item in (self.tools_schema or []) if isinstance(item, dict)
                    }
                    if known_tools and tool_name not in known_tools and not tool_name.startswith("kairos_"):
                        log.warning("[LLM] herramienta inexistente pedida por el modelo: %s", tool_name)
                        return {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps({
                                "error": "unknown_tool",
                                "tool": tool_name,
                                "message": "Esa herramienta no existe. Usa solo las herramientas disponibles; "
                                           "el perfil, el objetivo y el plan ya están en el contexto.",
                            }, ensure_ascii=False),
                        }

                    group = _tool_groups.GROUP_OF.get(tool_name)
                    if group and self._turn_tool_groups and group not in self._turn_tool_groups:
                        log.info("[TOOL] %s fuera de los grupos enviados: se añade %s", tool_name, group)
                        self._turn_tool_groups.add(group)

                    if tool_timeouts >= _MAX_TOOL_TIMEOUTS and not tool_name.startswith("kairos_"):
                        return {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps({
                                "error": "garmin_slow",
                                "tool": tool_name,
                                "message": "Garmin no está respondiendo ahora. No pidas más datos a Garmin en esta "
                                           "respuesta: contesta con lo que ya tienes y di qué no has podido consultar.",
                            }, ensure_ascii=False),
                        }

                    log.debug(f"Ejecutando: {tool_name}({arguments})")
                    try:
                        if tool_name == "get_activity" and not (isinstance(arguments, dict) and arguments.get("activity_id")):
                            raw_result = await _build_activity_candidates_payload(self.mcp_session, user_message)
                        elif tool_name.startswith("kairos_"):
                            raw_result = await self._handle_internal_tool(tool_name, arguments)
                        else:
                            raw_result = await asyncio.wait_for(
                                call_tool(self.mcp_session, tool_name, arguments),
                                timeout=_tool_timeout_seconds(),
                            )
                    except asyncio.TimeoutError as tool_exc:
                        tool_timeouts += 1
                        record_tool_failure(tool_name, f"sin respuesta en {_tool_timeout_seconds():.0f} s")
                        self._log_chat_error(
                            stage="tool_call_timeout",
                            error=tool_exc,
                            extra={"tool_name": tool_name, "arguments": arguments},
                        )
                        raw_result = json.dumps(
                            {
                                "error": "tool_call_timeout",
                                "tool": tool_name,
                                "message": "La herramienta excedió el tiempo máximo permitido.",
                            },
                            ensure_ascii=False,
                        )
                    except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as tool_exc:
                        self._log_chat_error(
                            stage="tool_call",
                            error=tool_exc,
                            extra={"tool_name": tool_name, "arguments": arguments},
                        )
                        raw_result = json.dumps(
                            {
                                "error": "tool_call_failed",
                                "tool": tool_name,
                                "message": str(tool_exc),
                            },
                            ensure_ascii=False,
                        )

                    # Si no hay training_readiness, enriquecer contexto con métricas de recuperación
                    if (
                        tool_name in {"get_training_readiness", "get_morning_training_readiness"}
                        and _is_no_data_result(raw_result)
                    ):
                        requested_date = arguments.get("date") if isinstance(arguments, dict) else None
                        fallback_snapshot = await _build_recovery_fallback_snapshot(
                            self.mcp_session,
                            requested_date if isinstance(requested_date, str) else None,
                        )
                        if fallback_snapshot:
                            log.debug("Training readiness sin datos; usando snapshot alternativo de recuperación")
                            raw_result = fallback_snapshot

                    # Las tools de Kairos ya devuelven sus datos compactos: la poda genérica (listas a 4 elementos, los
                    # más antiguos) dejaba a kairos_load_trends sin las últimas semanas ni los últimos días (28/10).
                    tool_result = raw_result if str(tool_name).startswith("kairos_") else _compact_tool_result(raw_result, tool_name)
                    log.debug(f"Resultado ({len(raw_result or '')} -> {len(tool_result)} chars): {tool_result[:150]}")

                    return {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    }

                messages.extend(await asyncio.gather(*(_run_tool_call(tc) for tc in message.tool_calls)))

                # Continúa el loop para que el modelo procese los resultados
                continue

            # Respuesta final del agente
            assistant_reply = await self._apply_output_sensors(message.content or "", messages, "llm", decision=loop_decision)

            # Fallback para récords personales cuando el modelo responde
            # con "sin acceso" pese a tener herramientas MCP activas.
            if _is_no_access_reply(assistant_reply) and (
                _is_personal_records_intent(user_message)
                or _is_personal_records_followup_intent(user_message, self.conversation_history)
            ):
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
                except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                    log.debug("records tool route no disponible: %s", exc)
                    self._log_chat_error(
                        stage="records_no_access_fallback",
                        error=exc,
                        extra={"user_message": user_message},
                    )

            # Fallback anti-respuesta genérica ("necesito más información"): reintento por LLM con el
            # snapshot para proponer una sesión útil. Nunca escribe en la base de datos: hasta el 24/10, con
            # objetivo en el perfil, creaba un plan "activo" vacío y desactivaba el plan real del atleta.
            if (
                _is_generic_needs_more_info_reply(assistant_reply)
                and (
                    _is_planning_intent(user_message, self.conversation_history)
                    or _is_tomorrow_workout_intent(user_message)
                    or _is_post_activity_feedback_intent(user_message)
                )
            ):
                rescue_messages: list[dict[str, str]]
                if _is_post_activity_feedback_intent(user_message) and analysis_block_for_rescue:
                    target_date = prefetch_date_for_rescue or user_date or "fecha consultada"
                    rescue_messages = [
                        {
                            "role": "system",
                            "content": (
                                _render_prompt("rescue/activity_feedback")
                                + "\n\n" + _calendar_block()
                                + "\n\n" + _load_coaching_rules()
                                + self._profile_context_block()
                                + self._plan_context_block()
                            ),
                        },
                        {
                            "role": "user",
                            "content": _render_prompt(
                                "rescue/activity_feedback_user",
                                pregunta=user_message,
                                fecha=target_date,
                                datos=analysis_block_for_rescue,
                            ),
                        },
                    ]
                else:
                    snapshot_ctx: dict[str, Any] = {}
                    try:
                        startup = await self.collect_startup_snapshot_48h()
                        load_summary = _format_load_fatigue_summary(startup.get("load_fatigue") or {})
                        snapshot_ctx = {
                            "today": (startup.get("dates") or {}).get("today"),
                            "body_battery": (startup.get("body_battery") or {}).get("summary"),
                            "hrv": (startup.get("hrv") or {}).get("summary"),
                            "sleep": (startup.get("sleep") or {}).get("summary"),
                            "load": load_summary,
                            "recent_trainings": (startup.get("trainings") or [])[:3],
                        }
                    except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                        log.debug("No se pudo obtener snapshot para fallback LLM de planificación: %s", exc)

                    rescue_messages = [
                        {
                            "role": "system",
                            "content": (
                                _render_prompt("rescue/next_session", horizonte=_coaching_horizon(user_message))
                                + "\n\n" + _calendar_block()
                                + "\n\n" + _load_coaching_rules()
                                + self._profile_context_block()
                                + self._load_and_anchors_context_block()
                                + self._plan_context_block()
                            ),
                        },
                        {
                            "role": "user",
                            "content": _render_prompt(
                                "rescue/next_session_user",
                                solicitud=user_message,
                                contexto=json.dumps(snapshot_ctx, ensure_ascii=False),
                            ),
                        },
                    ]
                try:
                    rescue_response = await self.client.chat.completions.create(
                        model=self.model,
                        messages=rescue_messages,
                    )
                    if getattr(rescue_response, "usage", None):
                        u = rescue_response.usage
                        p_toks = getattr(u, "prompt_tokens", 0) or 0
                        c_toks = getattr(u, "completion_tokens", 0) or 0
                        self.total_prompt_tokens += p_toks
                        self.total_completion_tokens += c_toks
                        _add_ia_tokens(p_toks + c_toks)
                    rescue_text = str((rescue_response.choices[0].message.content or "")).strip()
                    rescue_text = await self._apply_output_sensors(rescue_text, rescue_messages, "rescue")
                    if rescue_text:
                        assistant_reply = rescue_text
                except Exception as exc:
                    log.debug("Fallback LLM de planificación no disponible: %s", exc)

            return await self._finalize_chat_reply(user_message, assistant_reply, route="llm")


