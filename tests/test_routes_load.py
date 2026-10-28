"""
tests/test_routes_load.py
Rutas de carga: TSS de la semana, actividades, tendencia y TSS recientes por deporte (agent/routes/load.py).
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


from tests.agent_patch import patch_all, patch_everywhere
import agent.routes.load as _routes_load
from agent.routes.load import _build_load_trend_table, _format_load_fatigue_summary, _resolve_load_parameters_effective_date, _resolve_week_window
from agent.routes.profile import _build_goal_status_markdown
import agent.router as _router
from agent.router import ToolRouter, _is_load_trend_intent, _is_mcp_factual_query_intent, _is_week_activities_intent, _is_week_tss_followup_intent, _is_week_tss_intent


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales



class TestLoadParametersEffectiveDate:
    def test_resolve_effective_date_prefers_latest_parameter_change(self):
        profile = {
            "performance": {
                "running_threshold_pace_date": "2026-08-10",
                "cycling_ftp_date": "2026-08-12",
                "performance_params_updated_at": "2026-08-15",
            }
        }
        out = _resolve_load_parameters_effective_date(profile)
        assert out is not None
        assert out.isoformat() == "2026-08-15"

    def test_resolve_effective_date_ignores_invalid_values(self):
        profile = {
            "performance": {
                "running_threshold_pace_date": "no-date",
                "cycling_ftp_date": "",
            }
        }
        assert _resolve_load_parameters_effective_date(profile) is None


class TestWeekTssDeterministicRoute:
    def test_is_week_tss_intent_detects_weekly_tss_queries(self):
        assert _is_week_tss_intent("Cuanto TSS llevo esta semana?")
        assert _is_week_tss_intent("Cuanto TSS hice esta semana?")
        assert _is_week_tss_intent("¿Cuánto TSS hice esta semana?")
        assert _is_week_tss_intent("Cuales son los TSS de esta semana?")
        assert _is_week_tss_intent("Dame el acumulado semanal de TSS")
        assert _is_week_tss_intent("Compárame esta semana vs la semana pasada: TSS total, diferencia porcentual y si hay spike >20%")
        assert _is_week_tss_intent("¿Qué actividades han contribuido al TSS de esta semana? Dame día, actividad y TSS estimado por sesión")
        assert not _is_week_tss_intent("Como esta mi HRV hoy?")

    def test_is_week_tss_intent_does_not_hijack_single_day_query(self):
        assert not _is_week_tss_intent("Dime los TSSs del domingo 20 de septiembre")
        assert _is_mcp_factual_query_intent("Dime los TSSs del domingo 20 de septiembre")

    def test_is_week_activities_intent_detects_weekly_activity_queries(self):
        assert _is_week_activities_intent("Cuales son mis actividades de la semana del 10 de agosto 2026?")
        assert _is_week_activities_intent("Que entrenamientos hice esta semana?")
        assert not _is_week_activities_intent("Cuanto TSS llevo esta semana?")

    def test_is_week_tss_followup_intent_true_with_weekly_tss_context(self):
        history = [
            {
                "role": "assistant",
                "content": "## 🧭 Resumen\nConsulta de TSS semanal resuelta con datos reales.\n\n## 📊 Métricas clave\n| Métrica | Valor | Fuente |\n|---|---|---|\n| TSS acumulado | 434.4 | load_metrics_daily/garmin_activity_load |",
            }
        ]
        assert _is_week_tss_followup_intent("Y para la semana del 13 de agosto de 2026?", history)

    def test_is_week_tss_followup_intent_ignores_llm_coaching_text(self):
        # Batería del 23/10: el consejo del LLM decía "TSS acumulado" y la pregunta siguiente
        # ("¿qué sesión me recomiendas para esta semana…?") acababa en week_tss en vez de readiness.
        history = [{
            "role": "assistant",
            "content": "## 🧭 Resumen\nEstado de hoy.\n\n## 🧠 Interpretación de coaching\n"
                       "- Si el TSS acumulado del lunes a miércoles supera ~130, reduce.",
        }]
        pass

        message = "¿Qué tipo de sesión me recomiendas para esta semana dado mi estado?"
        assert not _is_week_tss_followup_intent(message, history)
        assert ToolRouter().route_key(message, history, {}) == "daily_readiness"

    def test_is_week_tss_followup_intent_false_without_context(self):
        history = [{"role": "assistant", "content": "¿Cómo te encuentras hoy?"}]
        assert not _is_week_tss_followup_intent("Y para la semana del 13 de agosto de 2026?", history)

    def test_is_week_tss_followup_intent_does_not_hijack_load_trend_query(self):
        history = [
            {
                "role": "assistant",
                "content": "## 🧭 Resumen\nConsulta de TSS semanal resuelta con datos reales.",
            }
        ]
        assert not _is_week_tss_followup_intent(
            "¿Cuál es mi tendencia de carga de las últimas 4 semanas?",
            history,
        )

    def test_tool_router_routes_week_tss_followup_without_tss_word(self):

        router = _router.ToolRouter()
        history = [
            {
                "role": "assistant",
                "content": "## 🧭 Resumen\nConsulta de TSS semanal resuelta con datos reales.\n\n## 📊 Métricas clave\n| Métrica | Valor | Fuente |\n|---|---|---|\n| TSS acumulado | 434.4 | load_metrics_daily/garmin_activity_load |",
            }
        ]
        route = router.route_key("Y para la semana del 13 de agosto de 2026?", history, {})
        assert route == "week_tss"

    def test_tool_router_routes_week_tss_for_comparative_weekly_query(self):

        router = _router.ToolRouter()
        route = router.route_key(
            "Compárame esta semana vs la semana pasada: TSS total, diferencia porcentual y si hay spike >20%",
            [],
            {},
        )
        assert route == "week_tss"

    def test_tool_router_routes_week_tss_for_weekly_tss_contribution_query(self):

        router = _router.ToolRouter()
        route = router.route_key(
            "¿Qué actividades han contribuido al TSS de esta semana? Dame día, actividad y TSS estimado por sesión",
            [],
            {},
        )
        assert route == "week_tss"

    def test_tool_router_routes_single_day_tss_to_mcp_factual(self):

        router = _router.ToolRouter()
        route = router.route_key(
            "Dime los TSSs del domingo 20 de septiembre",
            [],
            {},
        )
        assert route == "mcp_factual"

    def test_is_load_trend_intent_detects_atl_ctl_tsb_trend_query(self):
        assert _is_load_trend_intent(
            "Muéstrame la tendencia de los últimos 14 días de ATL, CTL y TSB, y dime si voy hacia fatiga o disponibilidad"
        )

    def test_tool_router_routes_load_trend_query(self):

        router = _router.ToolRouter()
        route = router.route_key(
            "Muéstrame la tendencia de los últimos 14 días de ATL, CTL y TSB, y dime si voy hacia fatiga o disponibilidad",
            [],
            {},
        )
        assert route == "load_trend"

    @pytest.mark.asyncio
    async def test_chat_load_trend_route_does_not_call_llm(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {}
        series = [
            {"date": "2026-08-01", "tss": 40.0, "atl": 50.0, "ctl": 60.0, "tsb": 10.0},
            {"date": "2026-08-14", "tss": 60.0, "atl": 56.0, "ctl": 63.0, "tsb": 7.0},
        ]
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM no debe ejecutarse en load_trend"))

        with patch_all("_save_history_entries"), patch_all("_save_user_profile"),                 patch_all("_load_metrics_series", return_value=series):
            out = await TrainerAgent.chat(
                agent,
                "Muéstrame la tendencia de los últimos 14 días de ATL, CTL y TSB, y dime si voy hacia fatiga o disponibilidad",
            )

        assert "Tendencia de los últimos" in out

    @pytest.mark.asyncio
    async def test_chat_load_trend_with_recommendation_uses_hybrid_llm_layer(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "load_metrics": {
                "series": [
                    {"date": "2026-08-01", "atl": 50.0, "ctl": 60.0, "tsb": 10.0},
                    {"date": "2026-08-14", "atl": 56.0, "ctl": 63.0, "tsb": 7.0},
                ],
                "ranges": {"tsb_low": -10.0, "tsb_high": 5.0, "atl_high": 74.5},
            }
        }
        agent.model = "test-model"
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        llm_response = MagicMock()
        llm_response.choices = [MagicMock(message=MagicMock(content="- Recomendación: reduce calidad 24-48h y prioriza recuperación activa."))]
        llm_response.usage = None
        agent.client.chat.completions.create = AsyncMock(return_value=llm_response)

        with patch_all("_save_history_entries"), patch_all("_save_user_profile"):
            out = await TrainerAgent.chat(
                agent,
                "Muéstrame la tendencia de los últimos 14 días de ATL, CTL y TSB, y recomiéndame si hoy debo hacer calidad o rodaje suave",
            )

        agent.client.chat.completions.create.assert_awaited_once()
        assert "## 🧠 Interpretación de coaching" in out


class TestGoalStatusDeterministicRoute:
    def test_build_goal_status_markdown_with_goal(self):
        profile = {
            "goals": {
                "target_race": "Media maratón A Coruña",
                "target_race_date": "2026-11-15",
                "target_time": "1:35:00",
                "weekly_training_hours": 8,
                "primary": "running",
            }
        }
        out = _build_goal_status_markdown(profile)
        assert out.startswith("## 🧭 Resumen")
        assert "Media maratón A Coruña" in out
        assert "15/11/2026" in out

    @pytest.mark.asyncio
    async def test_chat_goal_status_route_does_not_call_llm(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "goals": {
                "target_race": "Media maratón A Coruña",
                "target_race_date": "2026-11-15",
            }
        }
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        with patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(agent, "¿Cuál es mi próximo objetivo?")

        assert out.startswith("## 🧭 Resumen")
        assert "Media maratón A Coruña" in out

    @pytest.mark.asyncio
    async def test_chat_week_tss_route_does_not_call_llm(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {"load_metrics": {"series": []}}
        agent.model = "test-model"
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        with patch_all("_build_current_week_tss_markdown", new=AsyncMock(return_value="## 🧭 Resumen\nok\n\n## 🎯 Próximo paso\n- Fuente: respuesta determinista (sin inferencias del LLM para nombres/tipos de actividad).")) as weekly_mock, patch_all(
            "_save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "Cuánto TSS llevo esta semana?")

        weekly_mock.assert_awaited_once()
        assert out.startswith("## 🧭 Resumen")
        assert "sin inferencias del LLM" in out
        assert len(agent.conversation_history) == 2

    @pytest.mark.asyncio
    async def test_chat_week_tss_with_recommendation_uses_hybrid_llm_layer(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {"load_metrics": {"series": []}}
        agent.model = "test-model"
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        llm_response = MagicMock()
        llm_response.choices = [MagicMock(message=MagicMock(content="- Recomendación: reduce intensidad por spike semanal."))]
        llm_response.usage = None
        agent.client.chat.completions.create = AsyncMock(return_value=llm_response)

        with patch_all("_build_current_week_tss_markdown", new=AsyncMock(return_value="## 🧭 Resumen\nok\n\n## 🎯 Próximo paso\n- Fuente: respuesta determinista (sin inferencias del LLM para nombres/tipos de actividad).")) as weekly_mock, patch_all(
            "_save_history_entry"
        ):
            out = await TrainerAgent.chat(
                agent,
                "Compárame el TSS de esta semana vs la semana pasada y recomiéndame si debo hacer calidad o rodaje suave",
            )

        weekly_mock.assert_awaited_once()
        agent.client.chat.completions.create.assert_awaited_once()
        assert "## 🧠 Interpretación de coaching" in out

    @pytest.mark.asyncio
    async def test_chat_week_activities_route_does_not_call_llm(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {"load_metrics": {"series": []}}
        agent.model = "test-model"
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        with patch_all("_build_week_activities_markdown", new=AsyncMock(return_value="## 🧭 Resumen\nok\n\n## 🎯 Próximo paso\n- Fuente: respuesta determinista (sin inferencias del LLM para nombres/tipos de actividad).")) as weekly_mock, patch_all(
            "_save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "Cuales son mis actividades de la semana del 10 de agosto 2026?")

        weekly_mock.assert_awaited_once()
        assert out.startswith("## 🧭 Resumen")
        assert "sin inferencias del LLM" in out
        assert len(agent.conversation_history) == 2

    @pytest.mark.asyncio
    async def test_chat_week_activities_with_recommendation_uses_hybrid_llm_layer(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {"load_metrics": {"series": []}}
        agent.model = "test-model"
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        llm_response = MagicMock()
        llm_response.choices = [MagicMock(message=MagicMock(content="- Recomendación: mantén volumen y evita picos de intensidad."))]
        llm_response.usage = None
        agent.client.chat.completions.create = AsyncMock(return_value=llm_response)

        with patch_all("_build_week_activities_markdown", new=AsyncMock(return_value="## 🧭 Resumen\nok\n\n## 🎯 Próximo paso\n- Fuente: respuesta determinista (sin inferencias del LLM para nombres/tipos de actividad).")) as weekly_mock, patch_all(
            "_save_history_entry"
        ):
            out = await TrainerAgent.chat(
                agent,
                "Cuales son mis actividades de la semana del 10 de agosto 2026 y recomiéndame si debo priorizar calidad o rodaje suave",
            )

        weekly_mock.assert_awaited_once()
        agent.client.chat.completions.create.assert_awaited_once()
        assert "## 🧠 Interpretación de coaching" in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_uses_activity_fallback_when_today_missing(self, monkeypatch):
        import agent.trainer_agent as ta

        class _FakeDate:
            @staticmethod
            def today():
                from datetime import date as _Date

                return _Date(2026, 8, 15)

            @staticmethod
            def fromisoformat(value):
                from datetime import date as _Date

                return _Date.fromisoformat(value)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])
        monkeypatch.setattr(ta._storage, "get_activity_tss_map", lambda _start: {501: {"activity_id": 501, "start_date": "2026-08-15", "tss": 82.49, "method": "TSS"}})

        profile = {
            "load_metrics": {
                "series": [
                    {"date": "2026-08-10", "tss": 33.35},
                    {"date": "2026-08-11", "tss": 43.44},
                    {"date": "2026-08-12", "tss": 89.26},
                    {"date": "2026-08-13", "tss": 61.74},
                    {"date": "2026-08-14", "tss": 32.59},
                ]
            }
        }

        activities_payload = [
            {
                "activityId": 501,
                "activityName": "Rodaje sábado",
                "activityType": "running",
                "startTimeLocal": "2026-08-15 08:00:00",
                "trainingLoad": 82.49,
            }
        ]

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return activities_payload

        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_load._build_current_week_tss_markdown(mcp_session=object(), profile=profile)

        assert "| TSS acumulado | 342.9 |" in out
        assert "sábado 15/08: 82.5" in out
        assert "Día con más carga:" in out
        assert "fallback" in out

    def test_resolve_week_window_uses_explicit_historical_date(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 8, 18)

        patch_everywhere(monkeypatch, "date", _FakeDate)

        week_start, week_end = _resolve_week_window("semana del 10 de agosto de 2026", _FakeDate.today())
        assert week_start.isoformat() == "2026-08-10"
        assert week_end.isoformat() == "2026-08-16"

    def test_resolve_week_window_uses_explicit_range_without_year(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)

        week_start, week_end = _resolve_week_window(
            "Cuales fueron las actividades y TSS de la semana del 27/07 al 02/08 ?",
            _FakeDate.today(),
        )
        assert week_start.isoformat() == "2026-07-27"
        assert week_end.isoformat() == "2026-08-02"

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_honors_explicit_week_date(self, monkeypatch):
        import agent.trainer_agent as ta
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 8, 18)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])

        profile = {
            "load_metrics": {
                "series": [
                    {"date": "2026-08-10", "tss": 40.0},
                    {"date": "2026-08-11", "tss": 30.0},
                    {"date": "2026-08-12", "tss": 20.0},
                    {"date": "2026-08-13", "tss": 10.0},
                    {"date": "2026-08-14", "tss": 0.0},
                    {"date": "2026-08-15", "tss": 5.0},
                    {"date": "2026-08-16", "tss": 15.0},
                    {"date": "2026-08-17", "tss": 99.0},
                ]
            }
        }

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return [
                {
                    "activityName": "Rodaje lunes",
                    "activityType": "running",
                    "startTimeLocal": "2026-08-10 07:00:00",
                    "trainingLoad": 40.0,
                },
                {
                    "activityName": "Senderismo domingo",
                    "activityType": "hiking",
                    "startTimeLocal": "2026-08-16 08:00:00",
                    "trainingLoad": 15.0,
                },
                {
                    "activityName": "Actividad fuera de semana",
                    "activityType": "running",
                    "startTimeLocal": "2026-08-17 08:00:00",
                    "trainingLoad": 99.0,
                },
            ]

        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_load._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="Dime los entrenamientos y Tss de la semana del 10 de agosto 2026",
        )

        assert "| Semana natural | 10/08/2026 → 16/08/2026 |" in out
        assert "| TSS acumulado | 120.0 |" in out
        # Sin fila en activity_tss no se conoce la unidad: no se asigna a rTSS.
        assert "lunes 10/08: 40.0 (rTSS 0.0 · hrTSS 0.0 · TSS 40.0)" in out
        assert "domingo 16/08: 15.0 (rTSS 0.0 · hrTSS 0.0 · TSS 15.0)" in out
        assert "- 10/08: TSS 40.0" in out
        assert "- 16/08: TSS 15.0" in out
        assert "- Running — Rodaje lunes" in out
        assert "- Senderismo — Senderismo domingo" in out
        assert "Actividad fuera de semana" not in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_replaces_zero_day_with_activity_load(self, monkeypatch):
        import agent.trainer_agent as ta

        class _FakeDate:
            @staticmethod
            def today():
                from datetime import date as _Date

                return _Date(2026, 8, 18)

            @staticmethod
            def fromisoformat(value):
                from datetime import date as _Date

                return _Date.fromisoformat(value)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])
        monkeypatch.setattr(ta._storage, "get_activity_tss_map", lambda _start: {502: {"activity_id": 502, "start_date": "2026-08-17", "tss": 79.4, "method": "TSS"}})

        profile = {
            "load_metrics": {
                "series": [
                    {"date": "2026-08-17", "tss": 0.0},
                    {"date": "2026-08-18", "tss": 0.0},
                ]
            }
        }

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return [
                {
                    "activityId": 502,
                    "activityName": "Senderismo",
                    "activityType": "hiking",
                    "startTimeLocal": "2026-08-17 08:00:00",
                    "trainingLoad": 79.4,
                }
            ]

        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_load._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="Dime los TSSs de esta semana",
        )

        assert "lunes 17/08: 79.4" in out
        assert "| TSS acumulado | 79.4 |" in out
        assert "fallback" in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_never_uses_garmin_load_as_tss(self, monkeypatch):
        import agent.trainer_agent as ta

        class _FakeDate:
            @staticmethod
            def today():
                from datetime import date as _Date

                return _Date(2026, 8, 18)

            @staticmethod
            def fromisoformat(value):
                from datetime import date as _Date

                return _Date.fromisoformat(value)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])
        monkeypatch.setattr(ta._storage, "get_activity_tss_map", lambda _start: {})

        profile = {"load_metrics": {"series": [{"date": "2026-08-17", "tss": 0.0}, {"date": "2026-08-18", "tss": 0.0}]}}

        async def _fake_call_tool(_session, tool_name, _args):
            return [
                {
                    "activityId": 507,
                    "activityName": "Senderismo",
                    "activityType": "hiking",
                    "startTimeLocal": "2026-08-17 08:00:00",
                    "trainingLoad": 79.4,
                }
            ]

        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_load._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="Dime los TSSs de esta semana",
        )

        assert "79.4" not in out
        assert "Senderismo · TSS pendiente de cálculo" in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_includes_weekly_comparison_and_spike(self, monkeypatch):
        import agent.trainer_agent as ta
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 8, 21)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])

        # Semana previa (10-14/08): 100.0 total
        # Semana actual (17-21/08): 176.9 total -> +76.9% y spike >20%
        profile = {
            "load_metrics": {
                "series": [
                    {"date": "2026-08-10", "tss": 20.0},
                    {"date": "2026-08-11", "tss": 20.0},
                    {"date": "2026-08-12", "tss": 20.0},
                    {"date": "2026-08-13", "tss": 20.0},
                    {"date": "2026-08-14", "tss": 20.0},
                    {"date": "2026-08-17", "tss": 79.3},
                    {"date": "2026-08-18", "tss": 62.0},
                    {"date": "2026-08-19", "tss": 35.6},
                    {"date": "2026-08-20", "tss": 0.0},
                    {"date": "2026-08-21", "tss": 0.0},
                ]
            }
        }

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return []

        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_load._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="Compárame esta semana vs la semana pasada: TSS total, diferencia porcentual y si hay spike >20%",
        )

        assert "| TSS semana previa | 100.0 |" in out
        assert "| Diferencia porcentual | +76.9% |" in out
        assert "| Spike >20% | SI |" in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_shows_daily_tss_and_per_activity_tss_only_for_multi_activity_days(self, monkeypatch):
        import agent.trainer_agent as ta
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 20)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])

        profile = {
            "load_metrics": {
                "series": [
                    {"date": "2026-09-14", "tss": 0.0},
                    {"date": "2026-09-15", "tss": 78.7},
                    {"date": "2026-09-16", "tss": 59.4},
                    {"date": "2026-09-17", "tss": 115.7},
                    {"date": "2026-09-18", "tss": 180.2},
                    {"date": "2026-09-19", "tss": 0.0},
                    {"date": "2026-09-20", "tss": 134.0},
                ]
            }
        }

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return [
                {
                    "activityName": "Eliptica. 30' Z1 Transferencia Fuerza",
                    "activityType": "elliptical",
                    "startTimeLocal": "2026-09-16 07:00:00",
                    "trainingLoad": 18.5,
                },
                {
                    "activityName": "Trabajo neuromuscular con alta carga",
                    "activityType": "strength_training",
                    "startTimeLocal": "2026-09-16 19:00:00",
                    "trainingLoad": 3.6,
                },
            ]

        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_load._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="Dime los TSS de esta semana y actividades",
        )

        assert "- 16/09: TSS 59.4" in out
        assert "Eliptica. 30' Z1 Transferencia Fuerza · TSS " in out
        assert "Trabajo neuromuscular con alta carga" in out
        assert "Trabajo neuromuscular con alta carga · TSS " in out
        assert "TSS diario canónico" not in out
        assert "suma TSS actividades listadas" not in out
        assert "Carga Garmin" not in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_semana_pasada_uses_non_inverted_activity_window(self, monkeypatch):
        import agent.trainer_agent as ta
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                # Martes: semana actual empieza 31/08, semana pasada es 24/08-30/08
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])
        monkeypatch.setattr(ta._storage, "get_activity_tss_map", lambda _start: {503: {"activity_id": 503, "start_date": "2026-08-30", "tss": 220.4, "method": "hrTSS"}, 504: {"activity_id": 504, "start_date": "2026-09-01", "tss": 20.0, "method": "TSS"}})

        profile = {
            "load_metrics": {
                "series": [
                    # Semana pasada (consulta): sin cierres útiles en serie diaria
                    {"date": "2026-08-24", "tss": 0.0},
                    {"date": "2026-08-25", "tss": 0.0},
                    {"date": "2026-08-26", "tss": 0.0},
                    {"date": "2026-08-27", "tss": 0.0},
                    {"date": "2026-08-28", "tss": 0.0},
                    {"date": "2026-08-29", "tss": 0.0},
                    {"date": "2026-08-30", "tss": 0.0},
                    # Semana actual (comparación)
                    {"date": "2026-08-31", "tss": 10.0},
                    {"date": "2026-09-01", "tss": 20.0},
                ]
            }
        }

        captured_args = []

        async def _fake_call_tool(_session, tool_name, args):
            assert tool_name == "get_activities_by_date"
            captured_args.append(dict(args))
            return [
                {
                    "activityId": 503,
                    "activityName": "Trail largo domingo",
                    "activityType": "trail_running",
                    "startTimeLocal": "2026-08-30 07:30:00",
                    "trainingLoad": 220.4,
                },
                {
                    "activityId": 504,
                    "activityName": "Rodaje martes actual",
                    "activityType": "running",
                    "startTimeLocal": "2026-09-01 18:00:00",
                    "trainingLoad": 20.0,
                },
            ]

        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_load._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="cuales fueron mis actividades y TSS de la semana pasada?",
        )

        # Verifica que no se invierte el rango (start <= end) en la llamada a Garmin.
        assert captured_args, "Se esperaba al menos una llamada a get_activities_by_date"
        first_args = captured_args[0]
        start_key = "start_date" if "start_date" in first_args else "startdate"
        end_key = "end_date" if "end_date" in first_args else "enddate"
        assert first_args[start_key] <= first_args[end_key]

        # Semana pasada debe rellenarse con fallback por actividad real.
        assert "domingo 30/08: 220.4" in out
        assert "Semana pasada (24/08 → 30/08)" in out
        assert "| Semana pasada (24/08 → 30/08) | 220.4" in out
        assert "Trail largo domingo" in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_uses_stored_strength_tss_without_training_load(self, monkeypatch):
        import agent.trainer_agent as ta

        class _FakeDate:
            @staticmethod
            def today():
                from datetime import date as _Date

                return _Date(2026, 8, 18)

            @staticmethod
            def fromisoformat(value):
                from datetime import date as _Date

                return _Date.fromisoformat(value)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])
        monkeypatch.setattr(ta._storage, "get_activity_tss_map", lambda _start: {505: {"activity_id": 505, "start_date": "2026-08-17", "tss": 45.0, "method": "hrTSS"}})

        profile = {
            "load_metrics": {
                "series": [
                    {"date": "2026-08-17", "tss": 0.0},
                    {"date": "2026-08-18", "tss": 0.0},
                ]
            }
        }

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return [
                {
                    "activityId": 505,
                    "activityName": "Fuerza tren inferior",
                    "activityType": "strength_training",
                    "startTimeLocal": "2026-08-17 19:00:00",
                    "duration": 3600,
                    "averageHR": 120,
                    "maxHR": 160,
                }
            ]

        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_load._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="Dime los TSSs de esta semana",
        )

        assert "lunes 17/08: 45.0" in out
        assert "| TSS acumulado | 45.0 |" in out


class TestRecentTssBySportRoute:
    QUESTION = (
        "Obten los ultimos 20 entrenamientos de trail, fuerza y running y dime los TSSs que calcula kairos"
    )

    def test_original_question_routes_to_recent_tss_with_three_sports(self):

        assert _router.ToolRouter().route_key(self.QUESTION, [], {}) == "recent_tss_by_sport"
        sports, n = _routes_load._parse_recent_tss_sports_request(self.QUESTION)
        assert set(sports) == {"trail", "strength", "running"}
        assert n == 20

    def test_single_sport_activities_phrasing_still_routes(self):

        assert _router.ToolRouter().route_key("TSS de mis 10 últimas actividades de trail", [], {}) == "recent_tss_by_sport"

    @pytest.mark.asyncio
    async def test_lists_stored_tss_with_garmin_load_apart(self):

        activities = [
            {"activityId": 1, "activityName": "Rodaje", "activityType": "running", "startTimeLocal": "2026-10-18 08:00:00", "activityTrainingLoad": 256.2},
            {"activityId": 2, "activityName": "Trail largo", "activityType": "trail_running", "startTimeLocal": "2026-10-09 07:00:00", "activityTrainingLoad": 419.4},
            {"activityId": 3, "activityName": "Fuerza", "activityType": "strength_training", "startTimeLocal": "2026-10-05 19:00:00", "activityTrainingLoad": 0.1},
            {"activityId": 4, "activityName": "Bici", "activityType": "road_biking", "startTimeLocal": "2026-10-04 09:00:00"},
            {"activityId": 5, "activityName": "Rodaje nuevo", "activityType": "running", "startTimeLocal": "2026-10-20 07:00:00"},
            {"activityId": 6, "activityName": "Rodaje antiguo", "activityType": "running", "startTimeLocal": "2026-01-10 07:00:00"},
        ]
        stored = {
            1: {"activity_id": 1, "start_date": "2026-10-18", "tss": 54.4, "method": "TSS"},
            2: {"activity_id": 2, "start_date": "2026-10-09", "tss": 543.1, "method": "hrTSS"},
            3: {"activity_id": 3, "start_date": "2026-10-05", "tss": 16.6, "method": "hrTSS"},
            4: {"activity_id": 4, "start_date": "2026-10-04", "tss": 60.0, "method": "TSS"},
        }

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return activities

        with patch_all("call_tool", _fake_call_tool), \
             patch("agent.trainer_agent._storage.get_activity_tss_map", return_value=stored), \
             patch_all("_estimate_session_tss") as estimate_mock:
            out = await _routes_load._build_recent_tss_by_sport_markdown(object(), self.QUESTION)

        estimate_mock.assert_not_called()
        assert "| Fecha | Deporte | Actividad | TSS | Carga Garmin |" in out
        assert "| 18/10/2026 | running | Rodaje | 54.4 | 256.2 |" in out
        assert "| 09/10/2026 | trail | Trail largo | 543.1 | 419.4 |" in out
        assert "| 05/10/2026 | fuerza | Fuerza | 16.6 | 0.1 |" in out
        assert "| 20/10/2026 | running | Rodaje nuevo | pendiente | — |" in out
        assert "Bici" not in out
        assert "Rodaje antiguo" not in out
        rows = [line for line in out.splitlines() if line.endswith("|") and "/2026 | " in line]
        assert [r.split(" | ")[0] for r in rows] == ["| 20/10/2026", "| 18/10/2026", "| 09/10/2026", "| 05/10/2026"]

    @pytest.mark.asyncio
    async def test_power_ride_shows_garmin_load_not_garmin_tss(self):
        # Caso real (juez, 22/10): con potenciómetro Garmin envía también su TSS
        # (trainingStressScore) y salía en "Carga Garmin" en lugar de activityTrainingLoad.

        activities = [{
            "activityId": 7, "activityName": "Road. Un poquito de CDC", "activityType": "road_biking",
            "startTimeLocal": "2026-07-28 09:00:00", "trainingStressScore": 101.3, "activityTrainingLoad": 256.971,
        }]
        stored = {7: {"activity_id": 7, "start_date": "2026-07-28", "tss": 102.446, "method": "TSS", "native_load": 256.971}}

        async def _fake_call_tool(_session, _tool_name, _args):
            return activities

        with patch_all("call_tool", _fake_call_tool), \
             patch("agent.trainer_agent._storage.get_activity_tss_map", return_value=stored):
            out = await _routes_load._build_recent_tss_by_sport_markdown(object(), "¿Dime los TSS de mis 20 ultimas actividades de Ciclismo?")

        assert "| 28/07/2026 | ciclismo | Road. Un poquito de CDC | 102.4 | 257.0 |" in out
        assert "101.3" not in out


class TestLoadTrendDisplay:
    """Pantalla de carga: tabla /carga y resumen del estado de carga."""

    def _make_series(self, n_days: int = 56, tss_value: float = 50.0) -> list[dict]:
        today = date.today()
        rows = []
        atl = ctl = 0.0
        for i in range(n_days, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            tss = tss_value if i % 7 != 0 else 0.0  # descanso un día por semana
            atl = atl + (tss - atl) / 7
            ctl = ctl + (tss - ctl) / 42
            rows.append({"date": d, "tss": round(tss, 1), "atl": round(atl, 1), "ctl": round(ctl, 1), "tsb": round(ctl - atl, 1)})
        return rows

    def test_format_load_fatigue_summary_handles_missing_data(self):
        assert _format_load_fatigue_summary(None) == "sin datos suficientes"

    def test_build_load_trend_table_weekly_returns_markdown_table(self):
        series = self._make_series(56)
        out = _build_load_trend_table(series, mode="weeks")
        assert "| Semana |" in out
        assert "TSS" in out and "ATL" in out and "CTL" in out and "TSB" in out

    def test_build_load_trend_table_monthly_returns_markdown_table(self):
        series = self._make_series(90)
        out = _build_load_trend_table(series, mode="months")
        assert "| Mes |" in out
        assert "TSS total" in out

    def test_build_load_trend_table_empty_series_returns_message(self):
        out = _build_load_trend_table([], mode="weeks")
        assert "Sin datos" in out

    def test_build_load_trend_table_weekly_has_8_data_rows_max(self):
        series = self._make_series(56)
        out = _build_load_trend_table(series, mode="weeks")
        # Contar filas de datos (empieza con |, no contiene --- ni encabezado de columnas)
        data_rows = [
            l for l in out.splitlines()
            if l.startswith("|") and "---" not in l and "Semana" not in l
        ]
        assert len(data_rows) <= 8

    def test_build_load_trend_table_contains_status_emoji(self):
        series = self._make_series(56)
        out = _build_load_trend_table(series, mode="weeks")
        status_emojis = {"🟢", "🟠", "🔴", "🟡"}
        assert any(e in out for e in status_emojis)
