"""
tests/test_llm_calls.py
Llamadas al LLM fuera del bucle: clave del proveedor, sensores con su reintento y horizonte del coaching (agent/llm.py, llm_calls.py, coaching.py).
"""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest


from tests.agent_patch import patch_everywhere
import agent.llm as _llm
from agent.llm import LLMAuthError
import agent.wellness as _wellness
import agent.coaching as _coaching


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales


NVIDIA = {"provider": "nvidia", "base_url": "https://integrate.api.nvidia.com/v1", "model": "m"}
GEMINI = {"provider": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
          "model": "gemini-3.6-flash"}


class TestCoachingHorizon:
    """Horizonte de la capa de coaching; fecha fija: sábado 24/10/2026."""

    @pytest.fixture(autouse=True)
    def _fixed_today(self, monkeypatch):
        class FixedDate(date):
            @classmethod
            def today(cls):
                return date(2026, 10, 24)

        patch_everywhere(monkeypatch, "date", FixedDate)

    def test_next_week_is_not_this_week(self):
        out = _coaching._coaching_horizon("¿Y la semana que viene qué me recomiendas?")
        assert out.startswith("PRÓXIMA SEMANA: del lunes 26/10 al domingo 01/11")
        assert _coaching._coaching_horizon("¿Qué hago la próxima semana?").startswith("PRÓXIMA SEMANA")

    def test_this_week_lists_remaining_days(self):
        out = _coaching._coaching_horizon("¿Qué hago esta semana?")
        assert "sábado 24/10, domingo 25/10" in out and "lunes" not in out

    def test_day_after_tomorrow_is_not_tomorrow(self):
        assert _coaching._coaching_horizon("¿Qué hago pasado mañana?") == "PASADO MAÑANA (lunes 26/10): la sesión de ese día"
        assert _coaching._coaching_horizon("¿Y mañana?") == "MAÑANA (domingo 25/10): la sesión de mañana"
        assert _coaching._coaching_horizon("¿Qué me toca?").startswith("HOY")
        assert _coaching._coaching_horizon("¿Qué me toca?") == "HOY (sábado 24/10): la sesión de hoy"

    def test_calendar_gives_the_llm_days_and_dates(self):
        # 24/10/2026 es sábado: el LLM llamaba "mañana" al lunes 26/10 (S27) y ponía días que no tocaban (S28).
        cal = _coaching._calendar_block()
        assert "- Hoy: sábado 24/10/2026" in cal and "- Ayer: viernes 23/10/2026" in cal
        assert "- Mañana: domingo 25/10/2026" in cal and "- Pasado mañana: lunes 26/10/2026" in cal
        assert "lun 19/10, mar 20/10, mié 21/10, jue 22/10, vie 23/10, sáb 24/10, dom 25/10" in cal
        assert "La semana que viene: lun 26/10" in cal and "dom 01/11" in cal

    def test_hrv_deviation_is_given_already_computed(self):
        assert _wellness._hrv_deviation_text(58, 59) == "-2 % frente a su media de 7 días"
        assert _wellness._hrv_deviation_text(66, 59) == "+12 % frente a su media de 7 días"
        assert _wellness._hrv_deviation_text(59.2, 59) == "igual que su media de 7 días"
        assert _wellness._hrv_deviation_text(None, 59) == "" and _wellness._hrv_deviation_text(58, 0) == ""
        day = {"calendarDate": "2026-10-24", "lastNightAvg": 58, "weeklyAvg": 59, "status": "UNBALANCED"}
        assert _wellness._format_hrv_day([day], "2026-10-24") == (
            "58.0 ms · 7d 59.0 ms · -2 % frente a su media de 7 días · UNBALANCED")


class TestOutputSensors:
    """Autocorrección en vivo (docs/Harness.md §6): sensores sobre el texto del LLM con un reintento."""

    ZONES = [("Z1 Recuperación", 115, 134), ("Z2 Aeróbica", 135, 145), ("Z3 Tempo", 146, 157),
             ("Z4 Subumbral", 158, 168), ("Z5 Supraumbral", 169, 179)]

    def _agent(self, monkeypatch, replies):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        patch_everywhere(monkeypatch, "_resolve_hr_zones", lambda profile, sport: (self.ZONES, "Garmin"))
        patch_everywhere(monkeypatch, "_resolve_running_threshold_pace_for_date", lambda profile, iso: 240.0)
        agent = object.__new__(TrainerAgent)
        agent.user_profile = {}
        agent.model = "test-model"
        agent.provider = "nvidia"
        agent.total_prompt_tokens = 0
        agent.total_completion_tokens = 0
        responses = []
        for text in replies:
            r = MagicMock()
            r.choices = [MagicMock(message=MagicMock(content=text))]
            r.usage = None
            responses.append(r)
        agent.client = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=responses)
        return agent

    @pytest.mark.asyncio
    async def test_bad_range_is_fixed_by_one_retry(self, monkeypatch):
        agent = self._agent(monkeypatch, ["- Rodaje Z2 (135–145 lpm)."])
        context = [{"role": "system", "content": "sys"}, {"role": "user", "content": "¿qué hago hoy?"}]
        out = await agent._apply_output_sensors("- Rodaje Z2 alto / Z3 bajo (140–152 lpm).", context, "coaching")

        assert out == "- Rodaje Z2 (135–145 lpm)."
        kwargs = agent.client.chat.completions.create.await_args.kwargs
        assert "tools" not in kwargs
        correction = kwargs["messages"][-1]["content"]
        assert "140–152" in correction and "Z2 135–145" in correction
        assert kwargs["messages"][-2] == {"role": "assistant", "content": "- Rodaje Z2 alto / Z3 bajo (140–152 lpm)."}

    @pytest.mark.asyncio
    async def test_persisting_failure_keeps_best_and_adds_notice(self, monkeypatch):
        agent = self._agent(monkeypatch, ["- Series Z4–Z5 (160–175 lpm)."])
        out = await agent._apply_output_sensors("- Series Z4–Z5 (160–175 lpm).", [], "coaching")

        agent.client.chat.completions.create.assert_awaited_once()
        assert out.startswith("- Series Z4–Z5 (160–175 lpm).")
        assert "Comprobación de Kairos" in out and "rango 160–175 lpm" in out
        assert "LLM:" not in out

    @staticmethod
    def _provider_errors():
        """Caída, 429 y 503 del proveedor: excepciones de openai que no son RuntimeError ni OSError."""
        import httpx
        import openai

        req = httpx.Request("POST", "https://proveedor")
        return [
            openai.APIConnectionError(request=req),
            openai.RateLimitError("rate", response=httpx.Response(429, request=req), body=None),
            openai.InternalServerError("down", response=httpx.Response(503, request=req), body=None),
        ]

    @pytest.mark.asyncio
    async def test_provider_error_in_retry_keeps_text_and_notice(self, monkeypatch):
        # 28/10: antes estas excepciones se escapaban de la capa de sensores y cerraban Kairos.
        for exc in self._provider_errors():
            agent = self._agent(monkeypatch, [])
            agent.client.chat.completions.create = AsyncMock(side_effect=exc)
            out = await agent._apply_output_sensors("- Series Z4–Z5 (160–175 lpm).", [], "coaching")
            assert out.startswith("- Series Z4–Z5 (160–175 lpm).")
            assert "Comprobación de Kairos" in out

    @pytest.mark.asyncio
    async def test_provider_error_in_coaching_keeps_deterministic_block(self, monkeypatch):
        for exc in self._provider_errors():
            agent = self._agent(monkeypatch, [])
            agent.client.chat.completions.create = AsyncMock(side_effect=exc)
            out = await agent._augment_with_llm_coaching_if_needed("¿Cómo he dormido?", "TABLA", "sleep_trend", always=True)
            assert out.startswith("TABLA")
            assert "No pude generar una recomendación de coaching" in out

    @pytest.mark.asyncio
    async def test_clean_text_needs_no_extra_call(self, monkeypatch):
        agent = self._agent(monkeypatch, [])
        out = await agent._apply_output_sensors("- Rodaje suave Z1–Z2 (115–145 lpm), umbral 4:00/km.", [], "coaching")

        assert out == "- Rodaje suave Z1–Z2 (115–145 lpm), umbral 4:00/km."
        agent.client.chat.completions.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_iso_dates_are_fixed_without_llm(self, monkeypatch):
        agent = self._agent(monkeypatch, [])
        out = await agent._apply_output_sensors("- El 2026-10-23 corriste 10 km.", [], "llm")

        assert out == "- El 23/10/2026 corriste 10 km."
        agent.client.chat.completions.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_glucose_note_added_without_llm_for_insulin_diabetes(self, monkeypatch):
        from agent import sensors

        agent = self._agent(monkeypatch, [])
        agent.user_profile = {"health": {"injuries": ["DT1"], "notes": ""}}
        out = await agent._apply_output_sensors("- Parte principal: rodaje suave 40 min en Z2 (135–145 lpm).", [], "coaching")

        assert out.endswith(sensors.GLUCOSE_NOTE)
        agent.client.chat.completions.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_glucose_note_without_hypoglycemia_risk(self, monkeypatch):
        from agent import sensors

        session = "- Parte principal: rodaje suave 40 min en Z2 (135–145 lpm)."
        agent = self._agent(monkeypatch, [])
        # Sin diabetes o DT2 con medicación sin riesgo de hipoglucemia: sin nota (26/10).
        for health in ({"injuries": ["asma"]}, {"injuries": ["DT2"], "diabetes_medication": ["metformina"]}):
            agent.user_profile = {"health": health}
            assert not (await agent._apply_output_sensors(session, [], "coaching")).endswith(sensors.GLUCOSE_NOTE)
        agent.user_profile = {"health": {"injuries": ["DT2"], "diabetes_medication": ["sulfonilureas"]}}
        assert (await agent._apply_output_sensors(session, [], "coaching")).endswith(sensors.GLUCOSE_NOTE)

    @pytest.mark.asyncio
    async def test_decision_sensor_retries_when_today_is_soft(self, monkeypatch):
        agent = self._agent(monkeypatch, ["- Rodaje suave Z2 (135–145 lpm)."])
        out = await agent._apply_output_sensors("- Principal: 4x4 min en Z4 (158–168 lpm).", [], "coaching", decision="easy")

        assert out == "- Rodaje suave Z2 (135–145 lpm)."
        assert "sesión suave" in agent.client.chat.completions.create.await_args.kwargs["messages"][-1]["content"]

    @pytest.mark.asyncio
    async def test_invented_load_figure_retries_against_received_data(self, monkeypatch):
        agent = self._agent(monkeypatch, ["- Tu CTL 60.4 indica buena base."])
        context = [{"role": "user", "content": "Datos: Estado físico (CTL) 60.4 · Fatiga (ATL) 38.9"}]
        out = await agent._apply_output_sensors("- Tu CTL 55 indica buena base.", context, "coaching")

        assert out == "- Tu CTL 60.4 indica buena base."

    @pytest.mark.asyncio
    async def test_retry_never_sees_tool_results(self, monkeypatch):
        agent = self._agent(monkeypatch, ["- Rodaje Z2 (135–145 lpm)."])
        tool_call_msg = MagicMock()  # mensaje del SDK con tool_calls: no es un dict
        context = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "analiza"},
            tool_call_msg,
            {"role": "tool", "tool_call_id": "1", "content": "{...}"},
        ]
        await agent._apply_output_sensors("- Series Z4–Z5 (160–175 lpm).", context, "llm")

        sent = agent.client.chat.completions.create.await_args.kwargs["messages"]
        assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]


class TestLlmKey:
    """Clave del LLM rechazada (26/10): se marca, se convierte en LLMAuthError y la CLI la pide de nuevo."""

    class _Unauthorized(Exception):
        status_code = 401

    def _agent(self, monkeypatch, error):
        from agent import trainer_agent as ta

        monkeypatch.setenv("PROVIDER_API_KEY", "k")
        agent = object.__new__(ta.TrainerAgent)
        agent.set_provider(NVIDIA)
        # El envoltorio guarda el create original al instalarse: se vuelve a instalar sobre un doble.
        agent.client.chat.completions.create = AsyncMock(side_effect=error)
        agent._watch_llm_auth()
        return agent

    def test_rejected_key_sets_the_flag_and_raises_a_runtime_error(self, monkeypatch):
        pass

        agent = self._agent(monkeypatch, self._Unauthorized("Error code: 401 - Unauthorized"))
        with pytest.raises(LLMAuthError) as exc:
            asyncio.run(agent.client.chat.completions.create(model="m", messages=[]))
        assert isinstance(exc.value, RuntimeError) and agent.llm_auth_failed is True

    def test_other_errors_pass_through_untouched(self, monkeypatch):
        agent = self._agent(monkeypatch, Exception("403 Forbidden: Zscaler"))
        with pytest.raises(Exception, match="Zscaler"):
            asyncio.run(agent.client.chat.completions.create(model="m", messages=[]))
        assert agent.llm_auth_failed is False

    class _Busy(Exception):
        def __init__(self, status):
            super().__init__(f"Error code: {status}")
            self.status_code = status

    def test_busy_provider_is_retried_after_waiting(self, monkeypatch):
        # 29/10, Gemini gratuito: 503 "high demand" y 429; se espera 5, 10 y 20 s y se repite.
        waits = []

        async def fake_sleep(s):
            waits.append(s)

        monkeypatch.setattr("agent.llm_calls.asyncio.sleep", fake_sleep)
        agent = self._agent(monkeypatch, [self._Busy(503), self._Busy(429), "ok"])
        shown = []
        agent.llm_wait_notice = shown.append  # la CLI lo pinta en pantalla
        assert asyncio.run(agent.client.chat.completions.create(model="m", messages=[])) == "ok"
        assert waits == [5, 10]
        assert shown == ["NVIDIA NIM está saturado (503): espero 5 s y reintento.",
                         "NVIDIA NIM ha dado límite de peticiones (429): espero 10 s y reintento."]
        agent = self._agent(monkeypatch, [self._Busy(503)] * 4)
        with pytest.raises(self._Busy):
            asyncio.run(agent.client.chat.completions.create(model="m", messages=[]))
        assert waits[2:] == [5, 10, 20]
        agent = self._agent(monkeypatch, [self._Busy(400)])
        with pytest.raises(self._Busy):  # otro error: sin espera ni reintento
            asyncio.run(agent.client.chat.completions.create(model="m", messages=[]))
        assert len(waits) == 5

    def test_daily_quota_is_not_retried_and_says_when_it_resets(self, monkeypatch):
        # 29/10, Gemini gratuito: 20 peticiones al día por modelo; esperar segundos no sirve.
        from agent.llm_calls import LLMQuotaError

        waits = []

        async def fake_sleep(s):
            waits.append(s)

        monkeypatch.setattr("agent.llm_calls.asyncio.sleep", fake_sleep)
        daily = self._Busy(429)
        daily.body = [{"error": {"code": 429, "message": "Quota exceeded for metric: generate_content_free_tier_requests, "
                       "limit: 20, model: gemini-3.6-flash, description: Generate content requests per day limit "
                       "exceeded on Free Tier.", "details": [{"retryDelay": "62453s"}]}}]
        agent = self._agent(monkeypatch, [daily, "ok"])
        shown = []
        agent.llm_wait_notice = shown.append
        with pytest.raises(LLMQuotaError):
            asyncio.run(agent.client.chat.completions.create(model="m", messages=[]))
        assert waits == [] and shown == ["NVIDIA NIM: cupo diario de peticiones agotado; se libera en 17 h. Hasta "
                                         "entonces, cambia de proveedor con /proveedor si lo necesitas."]

    def _two_models(self, monkeypatch, error):
        from agent import trainer_agent as ta

        monkeypatch.setenv("PROVIDER_API_KEY", "k")
        monkeypatch.delenv("KAIROS_MODEL", raising=False)
        agent = object.__new__(ta.TrainerAgent)
        agent.set_provider({**GEMINI, "chat_model": "gemini-lite"})
        agent.client.chat.completions.create = AsyncMock(side_effect=error)
        agent._watch_llm_auth()
        return agent

    def test_chat_model_relieves_the_main_one_without_waiting(self, monkeypatch):
        # 30/10, decisión del usuario: Flash (planes y arranque) sin cupo o saturado → sigue Flash Lite al momento.
        from agent.llm_calls import LLMQuotaError

        waits = []

        async def fake_sleep(s):
            waits.append(s)

        monkeypatch.setattr("agent.llm_calls.asyncio.sleep", fake_sleep)
        daily = self._Busy(429)
        daily.body = "Generate content requests per day limit exceeded. retryDelay: '62453s'"
        agent = self._two_models(monkeypatch, [daily, "ok"])
        shown = []
        agent.llm_wait_notice = shown.append
        assert asyncio.run(agent.client.chat.completions.create(model="gemini-3.6-flash", messages=[])) == "ok"
        assert waits == [] and shown == ["Google Gemini: gemini-3.6-flash sin cupo diario; sigo con gemini-lite."]
        agent = self._two_models(monkeypatch, [self._Busy(503), "ok"])
        assert asyncio.run(agent.client.chat.completions.create(model="gemini-3.6-flash", messages=[])) == "ok"
        assert waits == []
        # El del chat no tiene relevo: sus 503 esperan y su cupo diario se avisa como siempre.
        agent = self._two_models(monkeypatch, [daily])
        with pytest.raises(LLMQuotaError):
            asyncio.run(agent.client.chat.completions.create(model="gemini-lite", messages=[]))
        # Si el relevo también falla, ya no hay otro: su 503 espera y reintenta.
        agent = self._two_models(monkeypatch, [self._Busy(503), self._Busy(503), "ok"])
        assert asyncio.run(agent.client.chat.completions.create(model="gemini-3.6-flash", messages=[])) == "ok"
        assert waits == [5]

    def test_coaching_uses_the_chat_model_unless_told_otherwise(self, monkeypatch):
        agent = self._two_models(monkeypatch, None)
        inner = AsyncMock(side_effect=RuntimeError("caída"))
        agent.client.chat.completions.create = inner
        agent.user_profile = {}
        asyncio.run(agent._augment_with_llm_coaching_if_needed("q", "Datos", "daily", always=True))
        asyncio.run(agent._augment_with_llm_coaching_if_needed("q", "Datos", "daily", always=True,
                                                               model="gemini-3.6-flash"))
        assert [c.kwargs["model"] for c in inner.call_args_list] == ["gemini-lite", "gemini-3.6-flash"]
        assert (agent.model, agent._chat_llm_model()) == ("gemini-3.6-flash", "gemini-lite")
        monkeypatch.setenv("KAIROS_MODEL", "otro")  # batería con --modelo: ese para todo
        agent.set_provider({**GEMINI, "chat_model": "gemini-lite"})
        assert (agent.model, agent._chat_llm_model()) == ("otro", "otro")

    def test_coaching_layer_survives_a_rejected_key(self, monkeypatch):
        # Antes, el 401 de la capa de coaching no lo capturaba nadie y cerraba la sesión.
        agent = self._agent(monkeypatch, self._Unauthorized("401"))
        agent.user_profile = {}
        out = asyncio.run(agent._augment_with_llm_coaching_if_needed("q", "Datos deterministas", "daily", always=True))
        assert out.startswith("Datos deterministas") and agent.llm_auth_failed is True

    @pytest.mark.parametrize("error, status", [
        (None, "ok"), (_Unauthorized("401"), "auth"), (Exception("API key not valid. API_KEY_INVALID"), "auth"),
        # NVIDIA con clave mala, texto real del 26/10; un 403 del proxy no es la clave.
        (Exception("Error code: 403 - {'status': 403, 'title': 'Forbidden', 'detail': 'Authorization failed'}"), "auth"),
        (Exception("403 Forbidden: Zscaler"), "error"), (TimeoutError("lento"), "error"),
    ])
    def test_probe_classifies(self, monkeypatch, error, status):

        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=error)
        patch_everywhere(monkeypatch, "_build_llm_client", lambda c: (client, "m", "k"))
        assert asyncio.run(_llm.probe_llm_key(NVIDIA))[0] == status

    def test_client_uses_the_athlete_config_and_key(self, monkeypatch):

        monkeypatch.setenv("PROVIDER_API_KEY", "clave")
        monkeypatch.delenv("KAIROS_MODEL", raising=False)
        config = {"provider": "Mi Proveedor", "base_url": "https://api.ejemplo.com/v1", "model": "modelo-x"}
        client, model, key = _llm._build_llm_client(config)
        assert (str(client.base_url), model, key) == ("https://api.ejemplo.com/v1/", "modelo-x", "clave")
        monkeypatch.setenv("KAIROS_MODEL", "otro")  # batería con --modelo
        assert _llm._build_llm_client(config)[1] == "otro"

    def test_set_provider_without_config_fails_clearly(self):
        from agent import trainer_agent as ta

        with pytest.raises(ValueError, match="proveedor de IA"):
            object.__new__(ta.TrainerAgent).set_provider({})
