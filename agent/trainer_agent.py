"""
agent/trainer_agent.py
TrainerAgent: el agente entrenador que compone Kairos (host MCP). Recibe cada pregunta (`chat`), la enruta con
`ToolRouter` y la responde con una ruta determinista o mixta (tabla `ROUTE_HANDLERS`) o con el bucle con tools.

Desde el 28/10 (pendiente 7) la lógica vive en sus módulos y aquí solo se compone:
- LoadMixin (agent/load_compute.py): TSS de las actividades nuevas y serie de carga.
- StartupMixin (agent/startup.py): arranque, foto de 48 h y resumen inicial.
- ContextMixin (agent/context.py): prompt de sistema y mensajes del LLM.
- LLMMixin (agent/llm_calls.py): proveedor, capa de coaching y sensores con su reintento.
- RoutesMixin (agent/routes/handlers.py): rutas deterministas y mixtas.
- PrefetchMixin (agent/prefetch.py) y ToolLoopMixin (agent/tool_loop.py): precarga de una actividad y bucle con tools.
- PlanLLMMixin (agent/plan_llm.py): el LLM construye y revisa el plan; el código lo comprueba.
"""

from __future__ import annotations

import json
import logging
import os
import time

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # solo para anotaciones: mcp se importa al usarlo (8c)
    from mcp import ClientSession

from agent import storage as _storage
from agent import tool_groups as _tool_groups
from agent.context import ContextMixin
from agent.daily_engine import _build_today_session_frame, _compute_daily_plan_adjustment, _get_active_training_plan
from agent.data_access import _load_user_profile, _save_history_entries
from agent.dates import _find_impossible_date
from agent.kairos_tools import _kairos_correlate, _kairos_load_trends, _kairos_weekly_sport_breakdown
from agent.knowledge import _load_athlete_knowledge_chunks
from agent.llm_calls import LLMMixin
from agent.load_compute import LoadMixin
from agent.mcp.client import consume_tool_transparency_events, reset_tool_transparency_events
from agent.plan_llm import PlanLLMMixin
from agent.prefetch import PrefetchMixin
from agent.prompts import _load_system_prompt
from agent.replies import _build_mcp_error_warning_markdown, _build_mcp_transparency_markdown
from agent.router import ToolRouter
from agent.routes.db_count import _is_db_count_question
from agent.routes.handlers import FIRST_ROUTES, ROUTE_HANDLERS, RoutesMixin
from agent.startup import StartupMixin
from agent.tool_loop import ToolLoopMixin

log = logging.getLogger(__name__)


class TrainerAgent(LoadMixin, StartupMixin, ContextMixin, LLMMixin, RoutesMixin, PrefetchMixin, ToolLoopMixin,
                   PlanLLMMixin):
    """Agente entrenador: historial de la conversación, perfil del atleta, cliente del LLM y sesión MCP."""

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
        route_key = self.tool_router.route_key(
            user_message,
            self.conversation_history,
            self.user_profile,
        )

        if route_key not in FIRST_ROUTES and _is_db_count_question(user_message):
            route_key = "db_count"
        handler = ROUTE_HANDLERS.get(route_key or "")
        if handler:
            result = await getattr(self, handler)(user_message)
            if result is not None:
                assistant_reply, route = result if isinstance(result, tuple) else (result, route_key)
                return await self._finalize_chat_reply(user_message, assistant_reply, route=route)

        # Mensajes del LLM con su prompt de sistema (perfil, carga, plan…): solo las preguntas que llegan aquí los
        # usan; antes se construían también para las rutas deterministas, con una lectura de Supabase de más (8b).
        messages = self._build_messages(user_message)
        messages, analysis_block, analysis_date, prefetch_cache = await self._prefetch_activity_context(user_message, messages)
        return await self._run_tool_loop(user_message, messages, analysis_block, analysis_date, prefetch_cache)
