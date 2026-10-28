"""
tests/test_tool_loop.py
Bucle con tools y chat: fechas imposibles, tools inexistentes o de escritura, cortacircuitos, rescates, transparencia, tools kairos_* y caché de lecturas (agent/tool_loop.py, trainer_agent.py).
"""

import json
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.trainer_agent as ta

from tests.agent_patch import patch_all, patch_everywhere
import agent.dates as _dates
import agent.hr_profile as _hr_profile
import agent.kairos_tools as _kairos_tools
import agent.routes.load as _routes_load
from agent.replies import _build_mcp_error_warning_markdown, _build_mcp_transparency_markdown
import agent.routes.day_data as _routes_day_data


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales



class TestImpossibleDateAndUnknownTool:
    def test_impossible_dates_are_detected_and_valid_ones_pass(self):
        assert _dates._find_impossible_date("¿Qué actividades hice el 31/02/2026?") == "31/02/2026"
        assert _dates._find_impossible_date("¿y el 29/02/2027?") == "29/02/2027"
        for ok in ("29/02/2028", "RPE 7/10", "semana del 05/10 al 11/10", "ritmo 3:55/km", "45/60 min"):
            assert _dates._find_impossible_date(ok) is None, ok

    @pytest.mark.asyncio
    async def test_chat_answers_impossible_date_without_tools_or_llm(self):
        agent = object.__new__(ta.TrainerAgent)
        agent.user_profile = {}
        agent.conversation_history = []
        agent.client = MagicMock()
        agent.client.chat.completions.create = AsyncMock()
        with patch_all("_save_history_entries"):
            out = await ta.TrainerAgent.chat(agent, "¿Qué actividades hice el 31/02/2026?")
        assert "31/02/2026 no existe" in out
        agent.client.chat.completions.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_tool_is_not_sent_to_garmin(self, monkeypatch):
        # Exploración del 24/10: el LLM llamó a "goals" y se avisaba de que Garmin había fallado.
        def _resp(content=None, tool_calls=None):
            m = MagicMock()
            m.content = content
            m.tool_calls = tool_calls
            r = MagicMock()
            r.choices = [MagicMock(message=m, finish_reason="stop")]
            r.usage = None
            return r

        call = MagicMock()
        call.id = "c1"
        call.function.name = "goals"
        call.function.arguments = "{}"
        agent = object.__new__(ta.TrainerAgent)
        agent.user_profile = {}
        agent.conversation_history = []
        agent.tools_schema = [{"type": "function", "function": {"name": "get_activities"}}]
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: [{"role": "user", "content": "x"}]
        agent.model = "m"
        agent.total_prompt_tokens = agent.total_completion_tokens = 0
        agent.client = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=[
            _resp(tool_calls=[call]), _resp(content="Tu objetivo es la media de Santa Pola."),
        ])
        sent = AsyncMock()
        patch_everywhere(monkeypatch, "call_tool", sent)
        patch_everywhere(monkeypatch, "_resolve_hr_zones", lambda profile, sport: ([], "n/d"))
        with patch_all("_save_history_entries"):
            out = await ta.TrainerAgent.chat(agent, "¿Qué opinas de entrenar con calor?")

        sent.assert_not_awaited()
        assert "No se pudo consultar Garmin" not in out
        tool_msg = agent.client.chat.completions.create.await_args_list[1].kwargs["messages"][-1]
        assert tool_msg["role"] == "tool" and "unknown_tool" in tool_msg["content"]


class TestToolTimeoutBreaker:
    @pytest.mark.asyncio
    async def test_parallel_calls_then_breaker_stops_further_garmin_calls(self, monkeypatch):
        # Exploración del 24/10: 5 get_activity seguidos agotaron su tiempo (144 s en una pregunta).
        # Ahora las llamadas de un mismo mensaje van en paralelo y, tras 2 timeouts, no se llama más a Garmin.
        import asyncio
        import time as _time

        def _call(i):
            c = MagicMock()
            c.id = f"c{i}"
            c.function.name = "get_activity"
            c.function.arguments = json.dumps({"activity_id": 1000 + i})
            return c

        def _resp(content=None, tool_calls=None):
            m = MagicMock()
            m.content = content
            m.tool_calls = tool_calls
            r = MagicMock()
            r.choices = [MagicMock(message=m, finish_reason="stop")]
            r.usage = None
            return r

        calls_to_garmin = []

        async def slow_tool(session, name, args):
            calls_to_garmin.append(args)
            await asyncio.sleep(1)
            return "{}"

        agent = object.__new__(ta.TrainerAgent)
        agent.user_profile = {}
        agent.conversation_history = []
        agent.tools_schema = [{"type": "function", "function": {"name": "get_activity"}}]
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: [{"role": "user", "content": "x"}]
        agent.model = "m"
        agent.total_prompt_tokens = agent.total_completion_tokens = 0
        agent.client = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=[
            _resp(tool_calls=[_call(1), _call(2), _call(3)]),
            _resp(tool_calls=[_call(4), _call(5)]),
            _resp(content="No he podido consultar todas las actividades."),
        ])
        patch_everywhere(monkeypatch, "call_tool", slow_tool)
        patch_everywhere(monkeypatch, "_tool_timeout_seconds", lambda: 0.2)
        patch_everywhere(monkeypatch, "_resolve_hr_zones", lambda profile, sport: ([], "n/d"))
        t0 = _time.perf_counter()
        with patch_all("_save_history_entries"):
            await ta.TrainerAgent.chat(agent, "¿Qué opinas de entrenar con calor?")
        elapsed = _time.perf_counter() - t0

        # Las 3 del primer mensaje salieron a la vez (0,2 s, no 0,6 s); las 2 del segundo ya no.
        assert len(calls_to_garmin) == 3
        assert elapsed < 0.5, elapsed
        # El LLM simulado recibe siempre la misma lista de mensajes: se mira la final.
        final = agent.client.chat.completions.create.await_args_list[-1].kwargs["messages"]
        tool_msgs = [m for m in final if isinstance(m, dict) and m.get("role") == "tool"]
        # Los resultados conservan el orden de las llamadas.
        assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2", "c3", "c4", "c5"]
        assert all("tool_call_timeout" in m["content"] for m in tool_msgs[:3])
        assert all("garmin_slow" in m["content"] for m in tool_msgs[3:])


class TestWriteToolsNeverReachTheServer:
    @pytest.mark.asyncio
    async def test_chat_never_sends_a_write_tool_to_the_server(self):
        # El servidor propio solo tiene tools get_*: una de escritura que pida el LLM no está en el esquema y no
        # llega al servidor (guardia de tools inexistentes, 24/10).
        from agent.trainer_agent import (
            TrainerAgent,
        )

        tool_call = MagicMock()
        tool_call.id = "tc_1"
        tool_call.function = MagicMock()
        tool_call.function.name = "schedule_workout"
        tool_call.function.arguments = "{}"

        msg_with_tool = MagicMock()
        msg_with_tool.tool_calls = [tool_call]
        msg_with_tool.content = ""

        msg_final = MagicMock()
        msg_final.tool_calls = None
        msg_final.content = "respuesta final"

        choice1 = MagicMock()
        choice1.message = msg_with_tool
        choice1.finish_reason = "tool_calls"

        choice2 = MagicMock()
        choice2.message = msg_final
        choice2.finish_reason = "stop"

        response1 = MagicMock()
        response1.choices = [choice1]
        response1.usage = None

        response2 = MagicMock()
        response2.choices = [choice2]
        response2.usage = None

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {}
        agent.conversation_history = []
        agent.tools_schema = [{
            "type": "function",
            "function": {
                "name": "get_activity",
                "description": "read",
                "parameters": {"type": "object", "properties": {}}
            }
        }]
        agent.client = MagicMock()
        agent.model = "test-model"
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=[response1, response2])
        agent._build_messages = lambda _msg: []

        with patch_all("call_tool", new=AsyncMock(side_effect=AssertionError("Should not call MCP write tools"))):
            with patch_all("_save_history_entries"):
                out = await TrainerAgent.chat(agent, "Programa un entrenamiento para mañana")

        assert out == "respuesta final"


class TestTomorrowWorkoutViaLlm:
    @pytest.mark.asyncio
    async def test_chat_planning_generic_reply_uses_llm_rescue_without_goal(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        msg_generic = MagicMock()
        msg_generic.tool_calls = None
        msg_generic.content = "Lo siento, pero no tengo suficiente información para proponerte un entrenamiento para mañana."
        choice_generic = MagicMock()
        choice_generic.message = msg_generic
        choice_generic.finish_reason = "stop"
        response_generic = MagicMock()
        response_generic.choices = [choice_generic]
        response_generic.usage = None

        msg_rescue = MagicMock()
        msg_rescue.tool_calls = None
        msg_rescue.content = "Para mañana te propongo 45 min aeróbicos suaves (RPE 3-4) + 4 progresivos de 20s."
        choice_rescue = MagicMock()
        choice_rescue.message = msg_rescue
        choice_rescue.finish_reason = "stop"
        response_rescue = MagicMock()
        response_rescue.choices = [choice_rescue]
        response_rescue.usage = None

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {}
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.model = "test-model"
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=[response_generic, response_rescue])
        agent.total_prompt_tokens = 0
        agent.total_completion_tokens = 0
        agent._api_key = ""

        with patch_all("_save_history_entries"), patch.object(
            TrainerAgent,
            "collect_startup_snapshot_48h",
            new=AsyncMock(
                return_value={
                    "dates": {"today": "2026-08-15"},
                    "body_battery": {"summary": "hoy=72 · ayer=65"},
                    "hrv": {"summary": "hoy=62 ms · ayer=59 ms"},
                    "sleep": {"summary": "hoy=7h10"},
                    "load_fatigue": {},
                    "trainings": [],
                }
            ),
        ):
            out = await TrainerAgent.chat(agent, "Que entrenamiento me propones para mañana?")

        assert "45 min aeróbicos" in out

    @pytest.mark.asyncio
    async def test_chat_generic_reply_with_goal_never_writes_a_plan(self):
        # Hasta el 24/10, con objetivo en el perfil, una respuesta genérica del LLM creaba un plan "activo"
        # vacío en Supabase y desactivaba el plan real del atleta. Ahora pasa por el rescate, sin escribir.
        from agent.trainer_agent import (
            TrainerAgent,
        )

        def _resp(text):
            m = MagicMock()
            m.tool_calls = None
            m.content = text
            r = MagicMock()
            r.choices = [MagicMock(message=m, finish_reason="stop")]
            r.usage = None
            return r

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {"goals": {"target_race": "Media maratón", "target_race_date": "2026-12-20"}}
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.model = "test-model"
        agent.client.chat.completions.create = AsyncMock(side_effect=[
            _resp("Lo siento, pero no tengo suficiente información para proponerte un entrenamiento para mañana."),
            _resp("Para mañana: 45 min en Z2 (135–145 lpm), RPE 3-4."),
        ])
        agent.total_prompt_tokens = agent.total_completion_tokens = 0
        agent._api_key = ""

        with patch_all("_save_history_entries"), \
             patch("agent.trainer_agent._storage.create_training_plan") as create_plan, \
             patch_all("_save_user_profile") as save_profile, \
             patch.object(TrainerAgent, "collect_startup_snapshot_48h", new=AsyncMock(return_value={
                 "dates": {"today": "2026-10-24"}, "body_battery": {}, "hrv": {}, "sleep": {},
                 "load_fatigue": {}, "trainings": [],
             })):
            out = await TrainerAgent.chat(agent, "Que entrenamiento me propones para mañana?")

        create_plan.assert_not_called()
        save_profile.assert_not_called()
        assert "45 min en Z2" in out
        rescue_system = agent.client.chat.completions.create.await_args_list[1].kwargs["messages"][0]["content"]
        assert "Horizonte: MAÑANA" in rescue_system


class TestPostActivityGenericRescue:
    @pytest.mark.asyncio
    async def test_chat_activity_feedback_generic_reply_uses_llm_rescue(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        msg_generic = MagicMock()
        msg_generic.tool_calls = None
        msg_generic.content = "Lo siento, pero no tengo suficiente información para evaluar tu entrenamiento de ayer."
        choice_generic = MagicMock()
        choice_generic.message = msg_generic
        choice_generic.finish_reason = "stop"
        response_generic = MagicMock()
        response_generic.choices = [choice_generic]
        response_generic.usage = None

        msg_rescue = MagicMock()
        msg_rescue.tool_calls = None
        msg_rescue.content = "Buen trabajo ayer: intensidad controlada y buena base aeróbica; ajusta recuperación hoy."
        choice_rescue = MagicMock()
        choice_rescue.message = msg_rescue
        choice_rescue.finish_reason = "stop"
        response_rescue = MagicMock()
        response_rescue.choices = [choice_rescue]
        response_rescue.usage = None

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {}
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.model = "test-model"
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=[response_generic, response_rescue])
        agent.total_prompt_tokens = 0
        agent.total_completion_tokens = 0
        agent._api_key = ""

        async def _fake_call_tool(_session, tool_name, _arguments):
            if tool_name == "get_activities_by_date":
                return json.dumps([{"activityId": 123}])
            if tool_name == "get_activity":
                return json.dumps({"activityId": 123, "type": "running", "duration": 3600})
            if tool_name == "get_body_battery":
                return json.dumps([])
            if tool_name == "get_sleep_data":
                return json.dumps([])
            if tool_name == "get_hrv_data":
                return json.dumps({})
            if tool_name == "get_training_load_trend":
                return json.dumps([])
            if tool_name == "get_activity_hr_zones":
                return "Unknown tool: get_activity_hr_zones"
            if tool_name == "get_activity_hr_in_timezones":
                return json.dumps([])
            return json.dumps({})

        with patch_all("call_tool", new=AsyncMock(side_effect=_fake_call_tool)), patch_all(
            "_build_activity_analysis_block",
            return_value="RESUMEN DE ACTIVIDAD PRECOMPUTADO",
        ), patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=None)), patch_all(
            "_save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "¿que opinión tienes de mi entrenamiento de ayer?")

        assert "Buen trabajo ayer" in out


class TestMcpTransparencyAndLatencyRegression:
    def test_mcp_errors_produce_top_warning_and_correct_transparency(self):
        # Caso real (21/10): tokens de Garmin rechazados → la semana salía "0 actividades" sin aviso.
        events = [
            {"tool": "get_activities_by_date", "mode": "mcp_error", "reason": "kind=unknown;http=;retry_after="},
            {"tool": "get_activities_by_date", "mode": "mcp_error", "reason": "kind=unknown;http=;retry_after="},
        ]

        warning = _build_mcp_error_warning_markdown(events)
        out = _build_mcp_transparency_markdown(events)

        assert "No se pudo consultar Garmin" in warning
        assert "get_activities_by_date" in warning
        assert "Algunas consultas a Garmin (MCP) fallaron." in out
        assert "get_activities_by_date: mcp_error x2" in out

    @pytest.mark.asyncio
    async def test_finalize_reply_puts_garmin_error_warning_first(self, monkeypatch):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        events = [{"tool": "get_activities_by_date", "mode": "mcp_error", "reason": "kind=unknown"}]
        patch_everywhere(monkeypatch, "consume_tool_transparency_events", lambda: list(events))
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.conversation_history = []
        patch_everywhere(monkeypatch, "_save_history_entry", lambda *a, **k: None)

        reply = await agent._finalize_chat_reply("¿Qué actividades hice esta semana?", "## 🧭 Resumen\n- Sin actividades.", "week_activities")

        assert reply.startswith("> ⚠️ **No se pudo consultar Garmin**")
        assert "## 🧭 Resumen" in reply

    @pytest.mark.asyncio
    async def test_build_week_activities_markdown_does_not_use_asyncio_wait_for(self, monkeypatch):
        from datetime import date as _Date

        today = _Date.today().isoformat()

        async def _boom_wait_for(*_args, **_kwargs):
            raise AssertionError("wait_for no debe usarse en week_activities")

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return {
                "activities": [
                    {
                        "activityName": "Rodaje",
                        "activityType": "running",
                        "startTimeLocal": f"{today} 10:00:00",
                        "duration": 3600,
                    }
                ]
            }

        monkeypatch.setattr(_hr_profile.asyncio, "wait_for", _boom_wait_for)
        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_load._build_week_activities_markdown(object(), "¿Qué actividades hice esta semana?")
        assert "Actividades detectadas | 1" in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_does_not_use_asyncio_wait_for(self, monkeypatch):
        import agent.trainer_agent as ta
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 4)

        async def _boom_wait_for(*_args, **_kwargs):
            raise AssertionError("wait_for no debe usarse en week_tss")

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return {
                "activities": [
                    {
                        "activityName": "Rodaje",
                        "activityType": "running",
                        "startTimeLocal": "2026-09-01 10:00:00",
                        "trainingLoad": 50.0,
                        "duration": 3600,
                    }
                ]
            }

        profile = {
            "load_metrics": {
                "series": [
                    {"date": "2026-09-01", "tss": 0.0, "ctl": 60.0, "atl": 55.0, "tsb": 5.0},
                    {"date": "2026-09-02", "tss": 0.0, "ctl": 60.0, "atl": 55.0, "tsb": 5.0},
                    {"date": "2026-09-03", "tss": 0.0, "ctl": 60.0, "atl": 55.0, "tsb": 5.0},
                    {"date": "2026-09-04", "tss": 0.0, "ctl": 60.0, "atl": 55.0, "tsb": 5.0},
                ]
            }
        }

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(_hr_profile.asyncio, "wait_for", _boom_wait_for)
        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])

        out = await _routes_load._build_current_week_tss_markdown(object(), profile, "¿Cuánto TSS hice esta semana?")
        assert "TSS acumulado" in out
        assert "Actividades:" in out

    @pytest.mark.asyncio
    async def test_build_mcp_factual_query_markdown_does_not_use_asyncio_wait_for(self, monkeypatch):

        async def _boom_wait_for(*_args, **_kwargs):
            raise AssertionError("wait_for no debe usarse en mcp_factual/activity_details")

        async def _fake_call_tool(_session, tool_name, _args):
            if tool_name == "get_activities_fordate":
                return {
                    "activities": [
                        {
                            "activityId": 123,
                            "activityName": "Fuerza",
                            "activityType": {"typeKey": "strength_training"},
                            "startTimeLocal": "2026-09-02 18:00:00",
                            "duration": 3600,
                            "activityTrainingLoad": 2.4,
                        }
                    ]
                }
            if tool_name == "get_activity":
                return {
                    "activityId": 123,
                    "activityName": "Fuerza",
                    "activityType": {"typeKey": "strength_training"},
                    "startTimeLocal": "2026-09-02 18:00:00",
                    "duration": 3600,
                    "activityTrainingLoad": 2.4,
                }
            return {}

        monkeypatch.setattr(_hr_profile.asyncio, "wait_for", _boom_wait_for)
        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_day_data._build_mcp_factual_query_markdown(
            object(),
            {"load_metrics": {"series": []}},
            "Analiza mi última actividad",
        )
        assert "TSS (actividad)" in out


class TestReadCacheLifetime:
    """La caché de lecturas de Supabase vive lo que dura una pregunta, también si la pregunta falla (8b, 28/10)."""

    @pytest.mark.asyncio
    async def test_cache_is_closed_after_an_answer_and_after_an_error(self, monkeypatch):
        agent = ta.TrainerAgent.__new__(ta.TrainerAgent)
        seen = []

        async def answer(_msg):
            seen.append(ta._storage._read_cache is not None)
            if _msg == "falla":
                raise RuntimeError("boom")
            return "ok"

        monkeypatch.setattr(agent, "_answer", answer)
        assert await agent.chat("hola") == "ok"
        assert ta._storage._read_cache is None
        with pytest.raises(RuntimeError):
            await agent.chat("falla")
        assert ta._storage._read_cache is None
        assert seen == [True, True]


class TestKairosLoadTrends:
    """kairos_load_trends (8a, 28/10): las cuatro métricas en una llamada y sin la poda de las tools de Garmin."""

    @staticmethod
    def _series(days=90):
        today = date.today()
        return [{"date": (today - timedelta(days=days - 1 - i)).isoformat(), "tss": 50.0 + i, "ctl": 40.0 + i / 10,
                 "atl": 45.0 + i / 10, "tsb": -5.0, "activities_count": 1} for i in range(days)]

    def test_all_metrics_daily_and_every_week(self, monkeypatch):
        series = self._series()
        patch_everywhere(monkeypatch, "_load_metrics_series", lambda *a, **k: series)
        out = json.loads(_kairos_tools._kairos_load_trends({}, weeks_back=12))
        assert out["daily"]["columns"] == ["date", "tss", "ctl", "atl", "tsb"]
        assert [r[0] for r in out["daily"]["rows"]] == [r["date"] for r in series[-14:]]
        assert len(out["weekly"]["rows"]) == 12
        # Semana actual: TSS sumado y CTL/ATL/TSB del último día.
        monday = date.today() - timedelta(days=date.today().weekday())
        this_week = [r for r in series if r["date"] >= monday.isoformat()]
        assert out["weekly"]["rows"][-1][1:] == [round(sum(r["tss"] for r in this_week), 1), round(series[-1]["ctl"], 1),
                                                round(series[-1]["atl"], 1), -5.0]
        assert out["latest"]["date"] == series[-1]["date"]

    @pytest.mark.asyncio
    async def test_the_llm_receives_every_week(self, monkeypatch):
        # Antes la poda genérica dejaba 4 semanas (las más antiguas) y el LLM decía que septiembre estaba "en 0".
        patch_everywhere(monkeypatch, "_load_metrics_series", lambda *a, **k: self._series())
        call = MagicMock()
        call.id = "k1"
        call.function.name = "kairos_load_trends"
        call.function.arguments = json.dumps({"weeks_back": 12})

        def _resp(content=None, tool_calls=None):
            m = MagicMock(content=content, tool_calls=tool_calls)
            r = MagicMock(usage=None)
            r.choices = [MagicMock(message=m, finish_reason="stop")]
            return r

        agent = object.__new__(ta.TrainerAgent)
        agent.user_profile = {}
        agent.conversation_history = []
        agent.tools_schema = list(_kairos_tools._KAIROS_INTERNAL_TOOLS)
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: [{"role": "user", "content": "x"}]
        agent.model = "m"
        agent.total_prompt_tokens = agent.total_completion_tokens = 0
        agent.client = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=[_resp(tool_calls=[call]), _resp(content="Hecho.")])
        patch_everywhere(monkeypatch, "_resolve_hr_zones", lambda profile, sport: ([], "n/d"))
        with patch_all("_save_history_entries"):
            await ta.TrainerAgent.chat(agent, "¿Qué opinas de entrenar con calor?")

        final = agent.client.chat.completions.create.await_args_list[-1].kwargs["messages"]
        tool_msg = next(m for m in final if isinstance(m, dict) and m.get("role") == "tool")
        payload = json.loads(tool_msg["content"])
        assert len(payload["weekly"]["rows"]) == 12 and len(payload["daily"]["rows"]) == 14
