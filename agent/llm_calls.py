"""
agent/llm_calls.py
Llamadas al LLM fuera del bucle con tools: proveedor y clave, capa de coaching de las rutas mixtas y sensores sobre cada salida con su único reintento.
"""

import time
import logging
import json
import asyncio
from datetime import date

from agent import sensors as _sensors
from agent import diabetes as _diabetes
from agent import ia_provider as _ia_provider
from agent.sensors import strip_simulated_user_turn as _strip_simulated_user_turn
from agent.prompts import (
    _load_coaching_rules,
    _render_prompt,
)
from agent.llm import (
    LLMAuthError,
    _build_llm_client,
    _is_llm_auth_error,
    _llm_timeout_seconds,
)
from agent.data_access import (
    _add_ia_tokens,
)
from agent.hr_profile import (
    _resolve_hr_zones,
    _resolve_running_threshold_pace_for_date,
)
from agent.daily_engine import (
    _build_today_session_frame,
)
from agent.router import (
    _is_coaching_recommendation_intent,
)
from agent.coaching import (
    _calendar_block,
    _coaching_horizon,
)

log = logging.getLogger(__name__)

# Esperas (s) antes de repetir una llamada al LLM que devuelve 429 o 503 (29/10).
_BUSY_RETRY_WAITS = (5, 10, 20)


def _provider_error() -> type[Exception]:
    """openai.APIError (caída, 429 o 503 del proveedor), importado cuando hace falta (8c: openai tarda ≈ 0,9 s)."""
    from openai import APIError

    return APIError


class LLMMixin:
    """Llamadas al LLM fuera del bucle con tools: proveedor y clave, capa de coaching de las rutas mixtas y sensores sobre cada salida con su único reintento."""

    def set_provider(self, config: dict) -> None:
        """Configura o cambia el proveedor de IA (fila de ia_provider: provider, base_url, model)."""
        if not _ia_provider.is_valid(config):
            raise ValueError("Falta el proveedor de IA del atleta (provider, base_url y model): entra en Kairos para darlo.")
        self.provider_config = dict(config)
        self.provider = str(config["provider"])
        self.client, self.model, self._api_key = _build_llm_client(config)
        self._watch_llm_auth()

        # Variables para tracking de tokens de la sesión
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def _watch_llm_auth(self) -> None:
        """Clave rechazada en cualquier llamada al LLM (26/10): marca `llm_auth_failed` y lanza LLMAuthError.

        Además (29/10), un 429 o 503 del proveedor se repite tras esperar 5, 10 y 20 s.

        LLMAuthError es un RuntimeError, que todas las capas (bucle con tools, coaching, rescates, sensores)
        ya capturan; la CLI ve la marca, pide la clave nueva y repite la pregunta. Antes, un 401 de la capa de
        coaching (openai.AuthenticationError) no lo capturaba nadie y cerraba la sesión.
        """
        self.llm_auth_failed = False
        completions = self.client.chat.completions
        create = completions.create

        async def _create(*args, **kwargs):
            for wait in (*_BUSY_RETRY_WAITS, None):
                try:
                    return await create(*args, **kwargs)
                except LLMAuthError:
                    raise
                except Exception as exc:
                    if _is_llm_auth_error(exc):
                        self.llm_auth_failed = True
                        raise LLMAuthError(f"{self.provider}: el proveedor rechaza la clave ({exc})") from exc
                    # Proveedor saturado o al límite (29/10, Gemini gratuito: 503 "high demand" y 429): se espera y se
                    # repite; los reintentos de la librería son de menos de un segundo y no bastan.
                    if wait is None or getattr(exc, "status_code", None) not in (429, 503):
                        raise
                    log.info("[LLM] %s %s: reintento en %ss", self.provider, exc.status_code, wait)
                    # Aviso en pantalla (29/10, petición del usuario): la CLI pone `llm_wait_notice`.
                    notice = getattr(self, "llm_wait_notice", None)
                    if notice:
                        name = _ia_provider.KNOWN.get(_ia_provider.known(self.provider) or "", (self.provider,))[0]
                        what = ("ha dado límite de peticiones (429)" if exc.status_code == 429
                                else "está saturado (503)")
                        notice(f"{name} {what}: espero {wait} s y reintento.")
                    await asyncio.sleep(wait)

        completions.create = _create

    def _log_chat_error(self, stage: str, error: Exception, extra: dict | None = None) -> None:
        """Registra un error de una etapa del chat (planificación, LLM, tool…) sin interrumpir la respuesta."""
        log.warning(
            "chat error stage=%s provider=%s %s: %s%s",
            stage,
            getattr(self, "provider", "unknown"),
            type(error).__name__,
            error,
            f" extra={extra}" if extra else "",
        )

    def _retry_findings(self, text: str, reference: str = "", decision: str | None = None) -> list[_sensors.Finding]:
        """Sensores que piden reintento, con las zonas y el ritmo umbral vigentes del perfil."""
        zones, _ = _resolve_hr_zones(self.user_profile, "running")
        zones_cycling, _ = _resolve_hr_zones(self.user_profile, "cycling")
        pace = _resolve_running_threshold_pace_for_date(self.user_profile, date.today().isoformat())
        return _sensors.findings_to_retry(text, zones, zones_cycling, pace, reference=reference, decision=decision,
                                          today=date.today())

    def _has_insulin_diabetes(self) -> bool:
        """Diabetes con riesgo de hipoglucemia (DT1, insulina, sulfonilureas o glinidas): nota glucémica S16."""
        return _diabetes.protocol((self.user_profile or {}).get("health")) == "insulina"

    async def _apply_output_sensors(
        self, text: str, context_messages: list, kind: str, decision: str | None = None
    ) -> str:
        """Pasa los sensores al texto del LLM antes de enseñarlo (docs/Harness.md §6).

        Corrige sin LLM las fechas ISO y la nota glucémica; si otro sensor falla, devuelve al LLM las pistas
        en una sola llamada más (sin tools) y se queda con la versión con menos fallos. Lo que siga fallando
        se avisa al pie y queda en el log. `decision` es la del motor diario, solo si la respuesta es para hoy.
        """
        # Referencia para S14: todo lo que recibió el LLM (contexto, datos deterministas y resultados de tools).
        reference = "\n".join(str(m.get("content") or "") for m in context_messages if isinstance(m, dict))
        text = _sensors.fix_iso_dates(text)
        findings = self._retry_findings(text, reference, decision)
        if not findings or not text.strip():
            return _sensors.add_glucose_note(text, self._has_insulin_diabetes())
        log.info("[SENSOR] %s: %s", kind, " | ".join(f"{f.sensor} {f.message}" for f in findings))
        # El reintento ve los mensajes de sistema y de usuario y su propia respuesta, no los resultados de tools.
        base = [
            m for m in context_messages
            if isinstance(m, dict) and (m.get("role") in {"system", "user"} or (m.get("role") == "assistant" and not m.get("tool_calls")))
        ]
        retry_messages = base + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": _render_prompt(
                "system/sensor_correction", fallos="\n".join(f"- {f.hint or f.message}" for f in findings)
            )},
        ]
        try:
            _t0 = time.perf_counter()
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=retry_messages,
                    **({
                        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
                    } if getattr(self, "provider", "") == "nvidia" else {}),
                ),
                timeout=_llm_timeout_seconds(),
            )
            log.info("[SENSOR] %s reintento elapsed=%.1fs", kind, time.perf_counter() - _t0)
            if getattr(response, "usage", None):
                p_toks = getattr(response.usage, "prompt_tokens", 0) or 0
                c_toks = getattr(response.usage, "completion_tokens", 0) or 0
                self.total_prompt_tokens += p_toks
                self.total_completion_tokens += c_toks
                _add_ia_tokens(p_toks + c_toks)
            fixed = _strip_simulated_user_turn(str(response.choices[0].message.content or "").strip())
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, asyncio.TimeoutError, KeyError, IndexError,
                _provider_error()) as exc:
            # APIError: caída, 429 o 503 del proveedor (28/10: antes se escapaba y cerraba Kairos).
            self._log_chat_error(stage=f"sensor_retry_{kind}", error=exc)
            fixed = ""
        if fixed:
            fixed = _sensors.fix_iso_dates(fixed)
            remaining = self._retry_findings(fixed, reference, decision)
            if len(remaining) < len(findings):
                text, findings = fixed, remaining
        text = _sensors.add_glucose_note(text, self._has_insulin_diabetes())
        if findings:
            log.warning("[SENSOR] %s sin corregir: %s", kind, " | ".join(f"{f.sensor} {f.message}" for f in findings))
            text += f"\n\n> {_sensors.NOTICE_HEADER}: revisa esto, no cuadra con tus datos:\n" + "\n".join(
                f"> - {_sensors.user_notice(f)}" for f in findings
            )
        return text

    async def _augment_with_llm_coaching_if_needed(
        self,
        user_message: str,
        deterministic_reply: str,
        route: str,
        daily_plan_decision: dict | None = None,
        always: bool = False,
        prompt: str | None = None,
        horizon: str | None = None,
    ) -> str:
        """Añade una interpretación de coaching por LLM cuando el usuario la pide (o siempre, con `always`).

        `prompt` sustituye las instrucciones de formato (por defecto, readiness o brief) y `horizon` el horizonte
        deducido de la pregunta (la tendencia de sueño habla de un periodo, no de hoy o mañana).

        `daily_plan_decision` es la del motor diario (ruta de estado del día). Si la respuesta es para hoy,
        el LLM recibe el marco de la sesión de hoy (G12) y el sensor S15 comprueba que lo respeta.

        Mantiene el bloque determinista como fuente de verdad y limita el LLM
        a interpretar/recomendar usando SOLO esos datos.
        """
        # La capa LLM se activa solo si el usuario pide explicitamente
        # interpretacion/recomendacion de coaching.
        if not always and not _is_coaching_recommendation_intent(user_message):
            return deterministic_reply

        try:
            is_readiness = route == "daily_readiness"
            horizon_text = horizon or _coaching_horizon(user_message)
            is_today = horizon is None and horizon_text.startswith("HOY")
            today_decision = (daily_plan_decision or {}).get("decision") if is_today else None
            session_frame = _build_today_session_frame(daily_plan_decision, self.user_profile) if is_today else ""
            coaching_messages = [
                {
                    "role": "system",
                    "content": (
                        _render_prompt("coaching/system")
                        + " " + _render_prompt(prompt or ("coaching/readiness" if is_readiness else "coaching/brief"))
                        + "\n\n" + _calendar_block()
                        # Reglas de entrenador: las mismas que el system prompt (prompts/coaching_rules.md).
                        + "\n\n" + _load_coaching_rules()
                        + self._profile_context_block()
                        + self._load_and_anchors_context_block()
                        + self._plan_context_block()
                    ),
                },
                {
                    "role": "user",
                    "content": _render_prompt(
                        "coaching/user",
                        pregunta=user_message,
                        ruta=route,
                        horizonte=horizon_text,
                        datos=deterministic_reply + ("\n\n" + session_frame if session_frame else ""),
                    ),
                },
            ]

            _t0_coaching = time.perf_counter()
            log.info("[LLM] ▶ llamada route=%s modelo=%s tipo=coaching", route, self.model)
            coaching_response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=coaching_messages,
                    **({
                        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
                    } if getattr(self, "provider", "") == "nvidia" else {}),
                ),
                timeout=_llm_timeout_seconds(),
            )
            log.info("[LLM] ⏱ coaching elapsed=%.1fs", time.perf_counter() - _t0_coaching)

            if getattr(coaching_response, "usage", None):
                u = coaching_response.usage
                p_toks = getattr(u, "prompt_tokens", 0) or 0
                c_toks = getattr(u, "completion_tokens", 0) or 0
                self.total_prompt_tokens += p_toks
                self.total_completion_tokens += c_toks
                _add_ia_tokens(p_toks + c_toks)

            coaching_text = _strip_simulated_user_turn(
                str((coaching_response.choices[0].message.content or "")).strip()
            )
            coaching_text = await self._apply_output_sensors(
                coaching_text, coaching_messages, "coaching", decision=today_decision
            )
            if not coaching_text:
                return (
                    deterministic_reply
                    + "\n\n## 🧠 Interpretación de coaching\n"
                    + "- No pude generar una recomendación de coaching en este intento (respuesta vacía del modelo)."
                    + "\n\n_Fase coaching por LLM solicitada; sin resultado utilizable en esta ejecución._"
                )

            return (
                deterministic_reply
                + "\n\n## 🧠 Interpretación de coaching\n"
                + coaching_text
                + "\n\n_Fase coaching por LLM sobre datos deterministas (sin recalcular métricas)._"
            )
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, asyncio.TimeoutError, json.JSONDecodeError, KeyError,
                _provider_error()) as exc:
            # APIError: caída, 429 o 503 del proveedor (28/10: antes se escapaba y cerraba Kairos).
            log.warning("No se pudo completar capa LLM de coaching (route=%s): %s", route, exc or type(exc).__name__)
            return (
                deterministic_reply
                + "\n\n## 🧠 Interpretación de coaching\n"
                + "- No pude generar una recomendación de coaching en este intento (timeout/error del modelo)."
                + "\n\n_Fase coaching por LLM solicitada; mantengo solo el bloque factual como fuente de verdad._"
            )
