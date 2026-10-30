"""
agent/tool_loop.py
Bucle con tools: el LLM pide tools de Garmin y de Kairos (solo los grupos de la pregunta, en paralelo y con
cortacircuitos de tiempo) hasta responder; marco de hoy (G15), rescates y sensores sobre la respuesta final.
"""

import time
import logging
import json
import asyncio
from typing import Any


from agent import tool_groups as _tool_groups
from agent.mcp.client import (
    call_tool,
    record_tool_failure,
)
from agent.prompts import (
    _load_coaching_rules,
    _render_prompt,
)
from agent.llm import (
    _llm_timeout_seconds,
    _tool_timeout_seconds,
)
from agent.dates import (
    _extract_iso_date_from_text,
    _normalize_date_args,
    _normalize_trend_date_range,
)
from agent.tool_results import (
    _compact_tool_result,
    _is_no_data_result,
)
from agent.data_access import (
    _add_ia_tokens,
)
from agent.routes.load import (
    _format_load_fatigue_summary,
)
from agent.routes.records import (
    _build_personal_records_markdown,
)
from agent.activity_analysis import (
    _build_activity_candidates_payload,
    _find_activity_id_by_date,
    _normalize_get_activity_args,
    _resolve_activity_id_from_query,
)
from agent.replies import (
    _is_generic_needs_more_info_reply,
    _is_no_access_reply,
)
from agent.router import (
    _detect_personal_records_sport_intent,
    _is_personal_records_followup_intent,
    _is_personal_records_intent,
    _is_planning_intent,
    _is_post_activity_feedback_intent,
    _is_today_training_question,
    _is_tomorrow_workout_intent,
)
from agent.routes.day_data import (
    _build_recovery_fallback_snapshot,
)
from agent.coaching import (
    _calendar_block,
    _coaching_horizon,
)

log = logging.getLogger(__name__)

# Respuestas fijas cuando el bucle no llega a responder (28/10, pendiente 8b): el juez de la batería las cuenta como
# fallo (las de Kairos) o como fallo de entorno (las del proveedor), en vez de darlas por buenas.
MAX_ITERATIONS_REPLY = ("[Lo siento, la consulta requirió demasiadas llamadas a herramientas. Por favor, reformula tu "
                        "pregunta de forma más concreta.]")
REQUEST_TOO_LARGE_TEXT = "La consulta es demasiado extensa para el modelo actual"
PROVIDER_FAILURE_TEXTS = (
    "La consulta ha excedido el tiempo máximo de respuesta del modelo",
    "ha llegado a su fin de vida y ya no está disponible",
    "El proveedor del modelo rechazó la petición",
)


class ToolLoopMixin:
    """La pregunta que no tiene ruta determinista."""

    async def _run_tool_loop(self, user_message: str, messages: list, analysis_block_for_rescue: str,
                             prefetch_date_for_rescue: str, _prefetch_cache: dict) -> str:
        """Responde con el LLM y las tools; termina siempre en `_finalize_chat_reply`."""
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
                assistant_reply = MAX_ITERATIONS_REPLY
                return await self._finalize_chat_reply(user_message, assistant_reply, route="max_tool_iterations")
            try:
                _t0 = time.perf_counter()
                turn_tools = _tool_groups.select(self.tools_schema, self._turn_tool_groups) if self.tools_schema else []
                log.info("[LLM] ▶ llamada iter=%d modelo=%s route=chat msgs=%d",
                         iteration, self._chat_llm_model(), len(messages))
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=self._chat_llm_model(),
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
                        f"El modelo `{self._chat_llm_model()}` ha llegado a su fin de vida y ya no está disponible.\n\n"
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
                    target_date = prefetch_date_for_rescue or "fecha consultada"
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
                        model=self._chat_llm_model(),
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
