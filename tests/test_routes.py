"""
tests/test_routes.py
Rutas deterministas y mixtas del chat (agent/routes/ y la tabla de rutas).
"""

import json
import re
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


from tests.agent_patch import patch_all, patch_everywhere
from agent.prompts import _load_coaching_rules
import agent.dates as _dates
from agent.tool_results import _is_no_data_result
import agent.hr_profile as _hr_profile
import agent.routes.load as _routes_load
from agent.routes.records import _build_personal_records_markdown
import agent.routes.period as _routes_period
import agent.routes.sleep as _routes_sleep
import agent.router as _router
from agent.router import _detect_personal_records_sport_intent, _is_activity_details_query_intent, _is_coaching_recommendation_intent, _is_config_options_intent, _is_daily_readiness_intent, _is_hr_threshold_query_intent, _is_mcp_factual_query_intent, _is_personal_records_followup_intent, _is_personal_records_intent, _is_running_threshold_query_intent, _is_today_load_status_intent
import agent.routes.day_data as _routes_day_data
from agent.routes.day_data import _build_recovery_fallback_snapshot
from agent.sensors import strip_simulated_user_turn as _strip_simulated_user_turn


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales



class TestPersonalRecordsMarkdown:
    def test_personal_records_intent_accepts_accented_plural_phrase(self):
        assert _is_personal_records_intent("¿Cuáles son mis récords personales running?")

    def test_build_personal_records_markdown_shows_running_rows(self):
        compact = json.dumps(
            [
                {"categoria": "5K", "valor": "17:48", "type_id": 3},
                {"categoria": "Ciclismo más largo", "valor": "199.02 km", "type_id": 8},
            ]
        )
        out = _build_personal_records_markdown(compact)
        assert "mejores registros personales en running" in out.lower()
        assert "| 5K | 17:48 |" in out
        assert "Ciclismo más largo" not in out

    def test_personal_records_followup_intent_true_with_context(self):
        history = [{"role": "assistant", "content": "Estos son tus mejores registros personales en running."}]
        assert _is_personal_records_followup_intent("En que distancias son esas marcas?", history)

    def test_build_personal_records_markdown_cycling_does_not_return_running(self):
        compact = json.dumps(
            [
                {"categoria": "5K", "valor": "17:48", "type_id": 3},
                {"categoria": "Ciclismo más largo", "valor": "199.02 km", "type_id": 8},
                {"categoria": "40K ciclismo", "valor": "54:42", "type_id": 11},
            ]
        )
        out = _build_personal_records_markdown(compact, preferred_sport="cycling")
        assert "registros personales en ciclismo" in out.lower()
        assert "Ciclismo más largo" in out
        assert "40K ciclismo" in out
        assert "5K" not in out

    def test_detect_personal_records_sport_intent_cycling(self):
        assert _detect_personal_records_sport_intent("Y mis mejores marcas en ciclismo?", []) == "cycling"

    def test_detect_personal_records_sport_intent_from_followup_context(self):
        history = [{"role": "assistant", "content": "Estos son tus mejores registros personales en ciclismo."}]
        assert _detect_personal_records_sport_intent("En que distancias son esas marcas?", history) == "cycling"


class TestRecoveryFallback:
    def test_is_no_data_result_true(self):
        assert _is_no_data_result("No training readiness data found for 2026-07-03")

    def test_is_no_data_result_false(self):
        assert not _is_no_data_result('{"value":42}')

    @pytest.mark.asyncio
    async def test_build_recovery_fallback_snapshot_returns_payload(self):
        async def _fake_call_tool(_session, tool_name, arguments):
            if (
                tool_name == "get_body_battery"
                and arguments.get("start_date") == "2026-07-03"
                and arguments.get("end_date") == "2026-07-03"
            ):
                return '{"charged":72,"drained":28}'
            return "No data found"

        with patch_all("call_tool", side_effect=_fake_call_tool):
            payload = await _build_recovery_fallback_snapshot(MagicMock(), "2026-07-03")

        assert payload is not None
        parsed = json.loads(payload)
        assert parsed["fallback_reason"] == "training_readiness_unavailable"
        assert "get_body_battery" in parsed["snapshot"]

    @pytest.mark.asyncio
    async def test_build_recovery_fallback_snapshot_handles_plain_text(self):
        async def _fake_call_tool(_session, tool_name, arguments):
            if (
                tool_name == "get_body_battery"
                and arguments.get("start_date") == "2026-07-03"
                and arguments.get("end_date") == "2026-07-03"
            ):
                return "Battery score: 58"
            return "No data found"

        with patch_all("call_tool", side_effect=_fake_call_tool):
            payload = await _build_recovery_fallback_snapshot(MagicMock(), "2026-07-03")

        assert payload is not None
        parsed = json.loads(payload)
        assert parsed["snapshot"]["get_body_battery"]["data"]["raw"] == "Battery score: 58"


class TestPlanStatusChatRoute:
    @pytest.mark.asyncio
    async def test_chat_plan_status_does_not_call_llm_and_is_consistent_with_profile(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "goals": {
                "target_race": "Trail 42K",
                "target_race_date": "2026-11-22",
                "weekly_training_hours": 10,
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
            out = await TrainerAgent.chat(agent, "Tengo algun plan?")

        assert "No tienes plan asignado" in out
        assert len(agent.conversation_history) == 2

    @pytest.mark.asyncio
    async def test_chat_planning_saves_no_generic_plan_when_the_llm_fails(self):
        """El plan lo construye el LLM (28/10); si no lo consigue, no se guarda ninguno (29/10: "PLANES GENÉRICOS NO")."""
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": "2026-11-22",
                "target_time": "00:45:00",
            },
            "health": {},
        }
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent.model = "test-model"
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=RuntimeError("proveedor caído"))

        with patch("agent.trainer_agent._storage.get_active_training_plan", return_value=None), patch("agent.trainer_agent._storage.create_training_plan", return_value={
            "id": "plan-db-1",
            "title": "Plan hacia 10K",
            "status": "active",
            "source": "agent_structured_plan",
            "duration_weeks": 8,
            "difficulty": "moderate",
            "plan_data": {
                "target_race": "10K",
                "target_race_date": "2026-11-22",
            },
        }) as mocked_create, patch_all("_save_user_profile"), patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(agent, "Puedes planificarme la semana para mi 10K?")

        assert "No he creado ningún plan" in out and "no guardo uno genérico" in out and "/proveedor" in out
        mocked_create.assert_not_called()
        assert "training_plan" not in agent.user_profile

    @pytest.mark.asyncio
    async def test_chat_planning_updates_existing_plan_and_reports_changes(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": "2026-11-22",
                "weekly_training_hours": 8,
            },
            "health": {},
        }
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent.model = "test-model"
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        existing = {
            "id": "plan-db-1",
            "title": "Plan actual 10K",
            "objective": "Preparación 10K",
            "difficulty": "moderate",
            "duration_weeks": 8,
            "status": "active",
            "source": "agent_structured_plan",
            "plan_data": {"target_race": "10K", "target_race_date": "2026-11-22"},
        }

        with patch("agent.trainer_agent._storage.get_active_training_plan", return_value=existing), patch(
            "agent.trainer_agent._storage.list_training_plan_sessions",
            return_value=[
                {"duration_min": 40, "day_index": 1, "session_type": "running_z2"},
                {"duration_min": 50, "day_index": 2, "session_type": "running_quality"},
            ],
        ), patch("agent.trainer_agent._storage.update_training_plan", return_value={
            "id": "plan-db-1",
            "title": "Plan hacia 10K",
            "objective": "Preparación para 10K",
            "difficulty": "moderate",
            "duration_weeks": 10,
            "status": "active",
            "source": "agent_structured_plan",
            "plan_data": {"target_race": "10K", "target_race_date": "2026-11-22"},
        }) as mocked_update, patch("agent.trainer_agent._storage.create_training_plan") as mocked_create, patch_all(
            "_save_user_profile"
        ), patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(agent, "Ajusta mi plan de esta semana")

        mocked_update.assert_called_once()
        mocked_create.assert_not_called()
        assert "Cambios de versión" in out or "Cambios de version" in out
        assert "Volumen semanal estimado" in out

    @pytest.mark.asyncio
    async def test_chat_planning_validation_error_returns_user_facing_message(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "goals": {"target_race": "10K", "target_race_date": "2026-11-22"},
            "health": {},
        }
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent.model = "test-model"
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("no debe llamarse"))
        agent._build_plan_with_llm = AsyncMock(return_value=({"title": "Plan", "plan_data": {}}, [], []))

        with patch("agent.trainer_agent._storage.get_active_training_plan", return_value=None), patch_all(
            "_validate_structured_plan", return_value=["Error de validación de prueba"]
        ), patch("agent.trainer_agent._storage.create_training_plan") as mocked_create, patch(
            "agent.trainer_agent._storage.update_training_plan"
        ) as mocked_update, patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(agent, "Planifícame para mi 10K")

        mocked_create.assert_not_called()
        mocked_update.assert_not_called()
        assert "No pude persistir el plan propuesto" in out
        assert "Error de validación de prueba" in out


class TestHrThresholdDeterministicRoute:
    def test_is_hr_threshold_intent_detects_fc_queries(self):
        assert _is_hr_threshold_query_intent("Cual es mi FC umbral?")
        assert _is_hr_threshold_query_intent("Dime mi frecuencia cardiaca umbral")
        assert not _is_hr_threshold_query_intent("Cual es mi ritmo umbral?")

    def test_running_threshold_intent_excludes_fc_threshold_queries(self):
        assert not _is_running_threshold_query_intent("Cual es mi FC umbral?")

    def test_config_options_intent_detects_configuration_question(self):
        assert _is_config_options_intent("que opciones puedo cambiar?")
        assert _is_config_options_intent("que puedo configurar en mi perfil?")
        assert _is_config_options_intent("qué parámetros puedo editar en mi perfil")
        assert _is_config_options_intent("/menu")
        assert not _is_config_options_intent("que entrenamiento hago manana?")

    def test_extract_hr_threshold_from_payload_supports_camel_case(self):

        payload = {
            "userData": {
                "lactateThresholdHeartRate": 169,
            }
        }
        out = _hr_profile._extract_hr_threshold_from_payload(payload)
        assert out == 169

    @pytest.mark.asyncio
    async def test_chat_hr_threshold_route_does_not_call_llm(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "performance": {
                "hr_threshold_bpm": 174,
                "hr_threshold_date": "2026-08-10",
            }
        }
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.chat = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        with patch_all("_load_user_profile", return_value=agent.user_profile), patch_all(
            "_save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "Cual es mi FC umbral?")

        assert out.startswith("## 🧭 Resumen")
        assert "174 lpm" in out

    @pytest.mark.asyncio
    async def test_hr_threshold_route_reads_garmin_user_profile_camel_case(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

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
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        async def _fake_call_tool(_session, tool_name, _args):
            if tool_name == "get_user_profile":
                return {"userData": {"lactateThresholdHeartRate": 169}}
            raise AssertionError(f"Unexpected tool: {tool_name}")

        with patch_all("_load_user_profile", return_value=agent.user_profile), patch_all(
            "_save_history_entry"
        ), patch_all("call_tool", _fake_call_tool), patch_all("_save_user_profile"):
            out = await TrainerAgent.chat(agent, "puedes consultar a garmin connect cual es mi FC umbral?")

        assert out.startswith("## 🧭 Resumen")
        assert "169 lpm" in out

    @pytest.mark.asyncio
    async def test_hr_threshold_route_does_not_update_load_effective_date_marker(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "performance": {
                "performance_params_updated_at": "2026-08-10",
            }
        }
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.model = "test-model"
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        async def _fake_call_tool(_session, tool_name, _args):
            if tool_name == "get_user_profile":
                return {"userData": {"lactateThresholdHeartRate": 169}}
            raise AssertionError(f"Unexpected tool: {tool_name}")

        with patch_all("_load_user_profile", return_value=agent.user_profile), patch_all(
            "_save_history_entry"
        ), patch_all("call_tool", _fake_call_tool), patch_all("_save_user_profile"):
            out = await TrainerAgent.chat(agent, "mi frecuencia cardiaca umbral")

        assert "169 lpm" in out and "/perfil umbral fc 169" in out
        perf = (agent.user_profile.get("performance") or {})
        assert perf.get("performance_params_updated_at") == "2026-08-10"
        # Solo lee: la FC del reloj no se guarda (se registra con /perfil umbral fc, que pone también la de TP).
        assert "hr_threshold_bpm" not in perf and "hr_threshold_tp_history" not in perf

    @pytest.mark.asyncio
    async def test_hr_threshold_route_never_calls_lactate_threshold_tool(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

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
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        async def _fake_call_tool(_session, tool_name, _args):
            if tool_name == "get_lactate_threshold":
                raise AssertionError("FC route must not call get_lactate_threshold")
            if tool_name == "get_user_profile":
                return {"userData": {"lactateThresholdHeartRate": 169}}
            raise AssertionError(f"Unexpected tool: {tool_name}")

        with patch_all("_load_user_profile", return_value=agent.user_profile), patch_all(
            "_save_history_entry"
        ), patch_all("call_tool", _fake_call_tool), patch_all("_save_user_profile"):
            out = await TrainerAgent.chat(agent, "cual es mi FC umbral?")

        assert "169 lpm" in out

    @pytest.mark.asyncio
    async def test_chat_config_options_route_does_not_call_llm(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

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
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        with patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(agent, "que opciones puedo cambiar?")

        assert out.startswith("## 🧭 Resumen")
        assert "/perfil umbral" in out
        assert "/perfil fc" in out


class TestDailyReadinessDeterministicRoute:
    def test_is_daily_readiness_intent_detects_status_queries(self):
        assert _is_daily_readiness_intent("¿Cómo estoy hoy para entrenar y qué me recomiendas?")
        assert _is_daily_readiness_intent("¿Puedo entrenar hoy?")
        assert _is_daily_readiness_intent("Dame mi training readiness")
        assert not _is_daily_readiness_intent("¿Qué zapatillas me recomiendas para trail?")

    def test_is_coaching_recommendation_intent_detects_forma_fisica_queries(self):
        assert _is_coaching_recommendation_intent("¿Cómo está mi forma física hoy?")
        assert _is_coaching_recommendation_intent("Dime mi estado de forma actual")
        assert not _is_coaching_recommendation_intent("Dame mi training readiness")

    def test_strip_simulated_user_turn_glued_after_closing_question(self):
        # Caso real (Nemotron Ultra, batería 21/10): el LLM continúa como si fuera el usuario.
        text = (
            "### ⚠️ Qué vigilar\n- HRV < 50 ms → suave.\n\n"
            "**¿Quieres que te genere un plan semanal hacia la Media Maratón de Santa Pola (19/01/2027)?**"
            "elijo entrenar fuerte mañana (calidad) y quiero que me planifiques la semana completa. Dame el reparto."
        )
        out = _strip_simulated_user_turn(text)
        assert out.endswith("(19/01/2027)?**")
        assert "elijo" not in out

    def test_strip_simulated_user_turn_explicit_role_markers(self):
        base = "### ✅ Qué hacer\n- Rodaje 45 min < 144 lpm."
        assert _strip_simulated_user_turn(base + "\n\nUsuario: vale, hazme el plan") == base
        assert _strip_simulated_user_turn(base + "\n**Usuario:** dame más") == base
        assert _strip_simulated_user_turn(base + "<|im_start|>user\nsí") == base

    def test_strip_simulated_user_turn_keeps_legit_text(self):
        legit = (
            "### ✅ Qué hacer\n- ¿Notas pesadez? Dame tu RPE tras el calentamiento y ajustamos.\n"
            "- Tu ritmo umbral: 4:12 min/km.\n\n¿Te encaja esta sesión o prefieres que la adapte?"
        )
        assert _strip_simulated_user_turn(legit) == legit
        closing = "¿Quieres que te prepare el plan?\nSi lo prefieres, también puedo ajustarte solo mañana."
        assert _strip_simulated_user_turn(closing) == closing
        coach_ask = "¿Quieres el plan completo? Dame tu ritmo objetivo y lo ajusto."
        assert _strip_simulated_user_turn(coach_ask) == coach_ask

    @pytest.mark.asyncio
    async def test_chat_daily_readiness_route_does_not_call_llm(self):
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
        agent.collect_startup_snapshot_48h = AsyncMock(return_value={
            "dates": {"today": date.today().isoformat(), "yesterday": (date.today() - timedelta(days=1)).isoformat()},
            "body_battery": {"today": {"bodyBatteryLevel": 90}, "yesterday": {}, "summary": ""},
            "hrv": {"today": {"lastNightAvg": 60, "weeklyAvg": 58, "status": "BALANCED"}, "yesterday": {}, "summary": ""},
            "sleep": {"today": {"sleepDuration": 8 * 3600, "sleepScore": 85}, "yesterday": {}, "summary": ""},
            "load_fatigue": {"latest": {"tss": 0.0, "atl": 50.0, "ctl": 55.0, "tsb": 5.0}, "weekly": {"current_tss": 220.0}, "action": "carga estable"},
            "trainings": [],
        })

        with patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(agent, "Dame mi training readiness")

        agent.collect_startup_snapshot_48h.assert_awaited_once()
        assert "Estado proactivo" in out
        assert "sin inferencias numéricas del LLM" in out
        assert len(agent.conversation_history) == 2

    @pytest.mark.asyncio
    async def test_chat_daily_readiness_with_recommendation_uses_hybrid_llm_layer(self):
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
        llm_response.choices = [MagicMock(message=MagicMock(content="- Recomendación: rodaje suave hoy por recuperación incompleta."))]
        llm_response.usage = None
        agent.client.chat.completions.create = AsyncMock(return_value=llm_response)
        agent.collect_startup_snapshot_48h = AsyncMock(return_value={
            "dates": {"today": date.today().isoformat(), "yesterday": (date.today() - timedelta(days=1)).isoformat()},
            "body_battery": {"today": {"bodyBatteryLevel": 90}, "yesterday": {}, "summary": ""},
            "hrv": {"today": {"lastNightAvg": 60, "weeklyAvg": 58, "status": "BALANCED"}, "yesterday": {}, "summary": ""},
            "sleep": {"today": {"sleepDuration": 8 * 3600, "sleepScore": 85}, "yesterday": {}, "summary": ""},
            "load_fatigue": {"latest": {"tss": 0.0, "atl": 50.0, "ctl": 55.0, "tsb": 5.0}, "weekly": {"current_tss": 220.0}, "status": "neutral", "ranges": {"atl_high": 70.0}, "flags": {"weekly_spike_alert": False}},
            "trainings": [],
        })

        with patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(agent, "Con mi estado actual (TSB + ATL + CTL), ¿recomiendas calidad, rodaje suave o descanso hoy? Justifica con datos")

        agent.client.chat.completions.create.assert_awaited_once()
        assert "## 🧠 Interpretación de coaching" in out

    @pytest.mark.asyncio
    async def test_chat_daily_readiness_forma_fisica_uses_hybrid_llm_layer(self):
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
        llm_response.choices = [MagicMock(message=MagicMock(content="- Recomendación: mantén sesión controlada y revisa recuperación mañana."))]
        llm_response.usage = None
        agent.client.chat.completions.create = AsyncMock(return_value=llm_response)
        agent.collect_startup_snapshot_48h = AsyncMock(return_value={
            "dates": {"today": date.today().isoformat(), "yesterday": (date.today() - timedelta(days=1)).isoformat()},
            "body_battery": {"today": {"bodyBatteryLevel": 88}, "yesterday": {}, "summary": ""},
            "hrv": {"today": {"lastNightAvg": 58, "weeklyAvg": 56, "status": "BALANCED"}, "yesterday": {}, "summary": ""},
            "sleep": {"today": {"sleepDuration": 8 * 3600, "sleepScore": 84}, "yesterday": {}, "summary": ""},
            "load_fatigue": {"latest": {"tss": 0.0, "atl": 49.0, "ctl": 54.0, "tsb": 5.0}, "weekly": {"current_tss": 190.0}, "status": "ready", "ranges": {"atl_high": 70.0}, "flags": {"weekly_spike_alert": False}},
            "trainings": [],
        })

        with patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(agent, "¿Cómo está mi forma física hoy?")

        agent.client.chat.completions.create.assert_awaited_once()
        assert "## 🧠 Interpretación de coaching" in out
        # La capa de coaching recibe las mismas reglas que el system prompt (fuente única)
        # y ya no pide la intensidad en % del umbral de FC.
        pass
        system = agent.client.chat.completions.create.await_args.kwargs["messages"][0]["content"]
        assert _load_coaching_rules() in system
        assert "% del umbral de FC" not in system

    @pytest.mark.asyncio
    async def test_chat_daily_readiness_without_plan_returns_explicit_choice(self):
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
        llm_response.choices = [MagicMock(message=MagicMock(content="- Recomendación: rodaje suave con foco en recuperación."))]
        llm_response.usage = None
        agent.client.chat.completions.create = AsyncMock(return_value=llm_response)
        agent.collect_startup_snapshot_48h = AsyncMock(return_value={
            "dates": {"today": date.today().isoformat(), "yesterday": (date.today() - timedelta(days=1)).isoformat()},
            "body_battery": {"today": {"bodyBatteryLevel": 90}, "yesterday": {}, "summary": ""},
            "hrv": {"today": {"lastNightAvg": 60, "weeklyAvg": 58, "status": "BALANCED"}, "yesterday": {}, "summary": ""},
            "sleep": {"today": {"sleepDuration": 8 * 3600, "sleepScore": 85}, "yesterday": {}, "summary": ""},
            "load_fatigue": {
                "status": "ready",
                "latest": {"tss": 0.0, "atl": 49.4, "ctl": 61.7, "tsb": 12.3},
                "weekly": {"current_tss": 176.9, "high_tss": 220.0},
                "ranges": {"atl_high": 74.5},
                "flags": {"weekly_spike_alert": False},
                "recommendation": "Mantén carga aeróbica controlada y reevalúa mañana con HRV/sueño/estrés.",
            },
            "trainings": [],
        })

        with patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(agent, "Con mi estado actual (TSB + ATL + CTL), ¿recomiendas calidad, rodaje suave o descanso hoy? Justifica con datos")

        agent.client.chat.completions.create.assert_awaited_once()
        assert "Elección recomendada hoy:" in out
        assert ("calidad" in out.lower()) or ("rodaje suave" in out.lower()) or ("descanso" in out.lower())


class TestMcpFactualDeterministicRoute:
    def test_trend_questions_go_to_llm_not_single_day_route(self):
        # Caso real (22/10): "¿Cómo ha evolucionado mi HRV en las últimas dos semanas?" iba a la ruta
        # factual de un día y respondía con los datos de hoy. Las tendencias van al LLM (get_hrv_trend…).

        router = _router.ToolRouter()
        for q in ("¿Cómo ha evolucionado mi HRV en las últimas dos semanas?", "¿Cómo ha ido mi HRV este mes?",
                  "Evolución de mi FC en reposo este mes"):
            assert not _router._is_mcp_factual_query_intent(q), q
            assert router.route_key(q, [], {}) is None, q
        for q in ("¿Cómo está mi HRV hoy?", "¿Qué HRV tuve ayer?", "¿Cuál fue mi FC en reposo ayer?"):
            assert router.route_key(q, [], {}) == "mcp_factual", q
        # Desde el 25/10 el sueño de anoche tiene su ruta (antes, tabla de todo el día).
        assert router.route_key("¿Cómo dormí anoche?", [], {}) == "sleep_night"
        # La tendencia de sueño tiene su ruta mixta (25/10): tabla de Python y lectura del LLM.
        assert router.route_key("Tendencia de mi sueño en las últimas semanas", [], {}) == "sleep_trend"
        assert router.route_key("¿Cuál es mi tendencia de carga de las últimas 4 semanas?", [], {}) == "load_trend"
        assert router.route_key("¿Dime los TSS de mis 20 ultimas actividades de Trail?", [], {}) == "recent_tss_by_sport"

    def test_is_mcp_factual_query_intent_detects_data_queries(self):
        assert _is_mcp_factual_query_intent("¿Qué TSS hice ayer?")
        assert _is_mcp_factual_query_intent("¿Cuánto he dormido hoy?")
        assert _is_mcp_factual_query_intent("Dime mi FC en reposo")
        assert _is_mcp_factual_query_intent("¿Qué entrené ayer?")
        assert _is_mcp_factual_query_intent("¿Cuáles fueron los datos de mi entrenamiento de ayer?")
        assert not _is_mcp_factual_query_intent("Recomiéndame el entrenamiento de hoy")

    def test_is_today_load_status_intent_detects_rule_applied_query(self):
        assert _is_today_load_status_intent(
            "Dame mi estado de carga/fatiga de hoy con TSS, ATL, CTL y TSB, e indica la regla aplicada"
        )

    def test_tool_router_routes_today_load_status_query(self):

        router = _router.ToolRouter()
        route = router.route_key(
            "Dame mi estado de carga/fatiga de hoy con TSS, ATL, CTL y TSB, e indica la regla aplicada",
            [],
            {},
        )
        assert route == "today_load_status"

    @pytest.mark.asyncio
    async def test_chat_today_load_status_route_does_not_call_llm(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {"sports": ["running"]}
        series = [{"date": "2026-08-20", "tss": 55.0, "atl": 56.5, "ctl": 63.2, "tsb": 6.8}]
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        with patch_all("_save_history_entries"),                 patch_all("_load_metrics_series", return_value=series):
            out = await TrainerAgent.chat(
                agent,
                "Dame mi estado de carga/fatiga de hoy con TSS, ATL, CTL y TSB, e indica la regla aplicada",
            )

        assert "Zona de Forma" in out
        assert "TSS del día" in out
        assert len(agent.conversation_history) == 2

    @pytest.mark.asyncio
    async def test_chat_today_load_status_with_recommendation_uses_hybrid_llm_layer(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "sports": ["running"],
            "load_metrics": {
                "series": [
                    {"date": "2026-08-20", "tss": 55.0, "atl": 56.5, "ctl": 63.2, "tsb": 6.8}
                ]
            },
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
        llm_response.choices = [MagicMock(message=MagicMock(content="- Recomendación: mantén sesión aeróbica controlada hoy."))]
        llm_response.usage = None
        agent.client.chat.completions.create = AsyncMock(return_value=llm_response)

        with patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(
                agent,
                "Dame mi estado de carga/fatiga de hoy con TSS, ATL, CTL y TSB, e indica la regla aplicada y recomiéndame la sesión",
            )

        agent.client.chat.completions.create.assert_awaited_once()
        assert "## 🧠 Interpretación de coaching" in out

    @pytest.mark.asyncio
    async def test_chat_today_load_status_with_recommendation_reports_llm_unavailable_on_timeout(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "sports": ["running"],
            "load_metrics": {
                "series": [
                    {"date": "2026-08-20", "tss": 55.0, "atl": 56.5, "ctl": 63.2, "tsb": 6.8}
                ]
            },
        }
        agent.model = "test-model"
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=TimeoutError())

        with patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(
                agent,
                "Dame mi estado de carga/fatiga de hoy con TSS, ATL, CTL y TSB, regla aplicada y recomiéndame sesión para hoy",
            )

        agent.client.chat.completions.create.assert_awaited_once()
        assert "## 🧠 Interpretación de coaching" in out
        assert "No pude generar una recomendación de coaching en este intento" in out

    def test_is_activity_details_query_intent_detects_detail_requests(self):
        assert _is_activity_details_query_intent("¿Cuáles fueron los datos de mi entrenamiento de ayer?")
        assert _is_activity_details_query_intent("Dame el resumen de la actividad de hoy")
        assert _is_activity_details_query_intent("Examina mi entrenamiento del martes")
        assert _is_activity_details_query_intent("Analiza mi ultima actividad")
        assert _is_activity_details_query_intent("Analiza mi ultima actividad de trail")
        assert not _is_activity_details_query_intent("¿Qué TSS hice ayer?")

    def test_is_daily_readiness_intent_detects_me_toca_hoy_queries(self):
        assert _is_daily_readiness_intent("y que me toca hoy?")
        assert _is_daily_readiness_intent("que me toca hoy")

    def test_is_daily_readiness_intent_detects_me_encuentro_hoy_queries(self):
        assert _is_daily_readiness_intent("Como me encuentro hoy?")
        assert _is_daily_readiness_intent("¿Cómo me encuentro hoy?")

    @pytest.mark.asyncio
    async def test_build_mcp_factual_query_markdown_resolves_last_trail_activity_without_explicit_date(self):

        async def _fake_call_tool(_session, tool_name, args):
            if tool_name == "get_activities":
                return [
                    {
                        "activityId": 123,
                        "activityTypeDTO": {"typeKey": "trail_running"},
                        "startTimeLocal": "2026-08-30 07:00:00",
                    }
                ]
            if tool_name == "get_activities_fordate":
                if args.get("date") == "2026-08-30":
                    return [
                        {
                            "activityId": 123,
                            "activityName": "Trail largo",
                            "activityType": "trail_running",
                            "startTimeLocal": "2026-08-30 07:00:00",
                            "duration": 3600,
                            "activityTrainingLoad": 120.0,
                        }
                    ]
                return []
            if tool_name == "get_activities_by_date":
                return []
            if tool_name == "get_activity":
                return {
                    "activityId": 123,
                    "activityName": "Trail largo",
                    "activityType": "trail_running",
                    "startTimeLocal": "2026-08-30 07:00:00",
                    "duration": 3600,
                    "activityTrainingLoad": 120.0,
                }
            if tool_name == "get_training_effect":
                return {
                    "aerobicTrainingEffect": 4.0,
                    "anaerobicTrainingEffect": 0.3,
                }
            if tool_name == "get_activity_hr_in_timezones":
                return []
            return {}

        stored = {123: {"activity_id": 123, "start_date": "2026-08-30", "tss": 140.0, "method": "hrTSS"}}
        with patch_all("call_tool", _fake_call_tool), \
             patch("agent.trainer_agent._storage.get_activity_tss_map", return_value=stored):
            out = await _routes_day_data._build_mcp_factual_query_markdown(
                mcp_session=object(),
                profile={"load_metrics": {"series": []}},
                user_message="Analiza mi ultima actividad de trail",
            )

        assert "| Fecha consultada | 30/08/2026 |" in out
        assert "Trail Running" in out
        assert "| TSS (actividad) | 140.0 | Kairos (hrTSS, guardado) |" in out
        assert "| Carga Garmin (no TSS) | 120.0 |" in out
        assert "hrTSS (Kairos): 140.0" in out

    @pytest.mark.asyncio
    async def test_chat_returns_controlled_message_on_llm_forbidden(self):
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
        agent.client.chat.completions.create = AsyncMock(side_effect=Exception("403 Forbidden from provider (Zscaler)"))

        with patch_all("_save_history_entries"):
            out = await TrainerAgent.chat(agent, "Que tipo de sesion me recomiendas esta semana?")

        assert "403/Forbidden" in out
        assert "Cambiar de proveedor o de modelo con `/proveedor`" in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_includes_type_breakdown_for_queried_week(self, monkeypatch):
        import agent.trainer_agent as ta
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])

        profile = {
            "load_metrics": {
                "series": [
                    {"date": "2026-08-31", "tss": 40.4, "ctl": 69.1, "atl": 67.6, "tsb": 1.5},
                    {"date": "2026-09-01", "tss": 0.0, "ctl": 69.1, "atl": 67.6, "tsb": 1.5},
                ]
            }
        }

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return [
                {
                    "activityName": "Gimnasio",
                    "activityType": "strength_training",
                    "startTimeLocal": "2026-08-31 08:00:00",
                    "trainingLoad": 37.1,
                    "duration": 4105,
                }
            ]

        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_load._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="cuanto tss hice esta semana?",
        )

        assert "Desglose por tipo de TSS:" in out
        # Sin fila en activity_tss la unidad es desconocida: va a "TSS", nunca a rTSS.
        assert "rTSS: 0.0" in out
        assert "hrTSS:" in out
        assert "TSS (potencia o metabólico): 40.4" in out
        assert "sTSS" not in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_buckets_by_stored_model(self, monkeypatch):
        import agent.trainer_agent as ta
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 8, 2)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])
        monkeypatch.setattr(
            ta._storage,
            "get_activity_tss_map",
            lambda _start: {
                1: {"activity_id": 1, "tss": 50.0, "method": "TSS", "model": "running:pipeline_fisico"},
                2: {"activity_id": 2, "tss": 100.0, "method": "TSS", "model": "cycling:power_ftp"},
                3: {"activity_id": 3, "tss": 30.0, "method": "hrTSS", "model": "strength:modelo_B"},
                4: {"activity_id": 4, "tss": 60.0, "method": "TSS", "model": "walk_hike:metabolico"},
            },
        )
        profile = {"load_metrics": {"series": [
            {"date": "2026-07-27", "tss": 50.0}, {"date": "2026-07-28", "tss": 100.0},
            {"date": "2026-07-29", "tss": 30.0}, {"date": "2026-07-30", "tss": 60.0},
        ]}}

        async def _fake_call_tool(_session, tool_name, _args):
            return [
                {"activityId": 1, "activityName": "Rodaje", "activityType": "running", "startTimeLocal": "2026-07-27 07:00:00"},
                {"activityId": 2, "activityName": "Road", "activityType": "road_biking", "startTimeLocal": "2026-07-28 07:00:00"},
                {"activityId": 3, "activityName": "Gym", "activityType": "strength_training", "startTimeLocal": "2026-07-29 07:00:00"},
                {"activityId": 4, "activityName": "Paseo", "activityType": "walking", "startTimeLocal": "2026-07-30 07:00:00"},
            ]

        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)
        out = await _routes_load._build_current_week_tss_markdown(mcp_session=object(), profile=profile, user_message="cuanto tss hice esta semana?")
        assert "rTSS: 50.0" in out
        assert "hrTSS: 30.0" in out
        assert "TSS (potencia o metabólico): 160.0" in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_shows_cycling_tss_label_not_rtss_in_activity_row(self, monkeypatch):
        import agent.trainer_agent as ta
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])
        monkeypatch.setattr(ta._storage, "get_activity_tss_map", lambda _start: {506: {"activity_id": 506, "start_date": "2026-08-31", "tss": 40.4, "method": "TSS"}})

        profile = {
            "load_metrics": {
                "series": [
                    {"date": "2026-08-31", "tss": 40.4, "ctl": 69.1, "atl": 67.6, "tsb": 1.5},
                    {"date": "2026-09-01", "tss": 0.0, "ctl": 69.1, "atl": 67.6, "tsb": 1.5},
                ]
            }
        }

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return [
                {
                    "activityId": 506,
                    "activityName": "Road. Un poquito de CDC",
                    "activityType": "road_biking",
                    "startTimeLocal": "2026-08-31 08:00:00",
                    "trainingLoad": 40.4,
                    "duration": 4105,
                }
            ]

        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_load._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="cuanto tss hice esta semana?",
        )

        assert "Road. Un poquito de CDC (TSS)" in out
        assert "Road. Un poquito de CDC (rTSS)" not in out

    @pytest.mark.asyncio
    async def test_chat_factual_route_does_not_call_llm(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {"load_metrics": {"series": []}}
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        with patch_all("_build_mcp_factual_query_markdown", new=AsyncMock(return_value="## 🧭 Resumen\nok\n\n## 🎯 Próximo paso\n- Fuente: respuesta determinista (datos factuales MCP, sin inferencias numéricas del LLM).")) as factual_mock, patch_all(
            "_save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "¿Qué TSS hice ayer?")

        factual_mock.assert_awaited_once()
        assert out.startswith("## 🧭 Resumen")
        assert "sin inferencias numéricas del LLM" in out
        assert len(agent.conversation_history) == 2

    @pytest.mark.asyncio
    async def test_chat_factual_route_with_recommendation_uses_hybrid_llm_layer(self):
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
        llm_response.choices = [MagicMock(message=MagicMock(content="- Recomendación: mantén intensidad baja mañana."))]
        llm_response.usage = None
        agent.client.chat.completions.create = AsyncMock(return_value=llm_response)

        with patch_all("_build_mcp_factual_query_markdown", new=AsyncMock(return_value="## 🧭 Resumen\nok\n\n## 🎯 Próximo paso\n- Fuente: respuesta determinista (datos factuales MCP, sin inferencias numéricas del LLM).")) as factual_mock, patch_all(
            "_save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "¿Qué TSS hice ayer y qué me recomiendas para mañana?")

        factual_mock.assert_awaited_once()
        agent.client.chat.completions.create.assert_awaited_once()
        assert "## 🧠 Interpretación de coaching" in out

    @pytest.mark.asyncio
    async def test_chat_activity_details_weekday_route_does_not_call_llm(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {"load_metrics": {"series": []}}
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        with patch_all("_build_mcp_factual_query_markdown", new=AsyncMock(return_value="## 🧭 Resumen\nok\n\n## 🎯 Próximo paso\n- Fuente: respuesta determinista (datos factuales MCP, sin inferencias numéricas del LLM).")) as factual_mock, patch_all(
            "_save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "Examina mi entrenamiento del martes")

        factual_mock.assert_awaited_once()
        assert out.startswith("## 🧭 Resumen")
        assert "sin inferencias numéricas del LLM" in out
        assert len(agent.conversation_history) == 2

    @pytest.mark.asyncio
    async def test_chat_activity_details_with_recommendation_uses_hybrid_llm_layer(self):
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
        llm_response.choices = [MagicMock(message=MagicMock(content="- Recomendación: recuperación activa por la carga observada."))]
        llm_response.usage = None
        agent.client.chat.completions.create = AsyncMock(return_value=llm_response)

        with patch_all("_build_mcp_factual_query_markdown", new=AsyncMock(return_value="## 🧭 Resumen\nok\n\n## 🎯 Próximo paso\n- Fuente: respuesta determinista (datos factuales MCP, sin inferencias numéricas del LLM).")) as factual_mock, patch_all(
            "_save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "Examina mi entrenamiento del martes y recomiéndame qué hacer mañana")

        factual_mock.assert_awaited_once()
        agent.client.chat.completions.create.assert_awaited_once()
        assert "## 🧠 Interpretación de coaching" in out

    @pytest.mark.asyncio
    async def test_build_mcp_factual_query_markdown_falls_back_to_activity_tss_when_series_is_zero(self):

        target_iso = "2026-08-17"

        async def _fake_call_tool(_session, tool_name, args):
            if tool_name == "get_activities_by_date":
                return [
                    {
                        "activityId": 601,
                        "activityName": "Senderismo",
                        "activityType": "hiking",
                        "startTimeLocal": f"{target_iso} 08:00:00",
                        "trainingLoad": 79.4,
                        "duration": 7140,
                    }
                ]
            if tool_name == "get_training_load_trend":
                return []
            return {}

        profile = {
            "load_metrics": {
                "series": [
                    {"date": target_iso, "tss": 0.0},
                ]
            }
        }

        stored = {601: {"activity_id": 601, "start_date": target_iso, "tss": 70.5, "method": "TSS"}}
        with patch_all("call_tool", _fake_call_tool), patch_all(
            "_resolve_target_date_from_message", return_value=date.fromisoformat(target_iso)
        ), patch("agent.trainer_agent._storage.get_activity_tss_map", return_value=stored):
            out = await _routes_day_data._build_mcp_factual_query_markdown(
                mcp_session=object(),
                profile=profile,
                user_message="Cuales son los TSS de la actividad del 17/08/26?",
            )

        m = re.search(r"\| (?:TSS \(actividad\)|TSS del día) \|\s*([0-9]+(?:\.[0-9]+)?)\s*\|", out)
        assert m is not None
        assert float(m.group(1)) == pytest.approx(70.5)
        assert "79.4" not in out.split("Carga Garmin")[0]

    @pytest.mark.asyncio
    async def test_daily_tss_comes_from_kairos_not_garmin_trend(self):
        # Antes: "TSS del día" se tomaba primero de get_training_load_trend (carga nativa de Garmin).

        target_iso = "2026-10-18"

        async def _fake_call_tool(_session, tool_name, _args):
            if tool_name == "get_training_load_trend":
                return {"trend": [{"date": target_iso, "trainingLoad": 250.0}]}
            return {}

        profile = {}
        with patch_all("_load_metrics_series", return_value=[{"date": target_iso, "tss": 62.3}]),                 patch_all("call_tool", _fake_call_tool), patch_all(
            "_resolve_target_date_from_message", return_value=date.fromisoformat(target_iso)
        ), patch("agent.trainer_agent._storage.get_activity_tss_map", return_value={}):
            out = await _routes_day_data._build_mcp_factual_query_markdown(
                mcp_session=object(), profile=profile, user_message="¿Qué hice el 18/10?"
            )

        assert "| TSS del día | 62.3 | load_metrics(series) |" in out
        assert "| Carga Garmin del día (no TSS) | 250.0 | get_training_load_trend |" in out


class TestRunningThresholdDeterministicRoute:
    def test_is_running_threshold_query_intent_detects_queries(self):
        assert _is_running_threshold_query_intent("Cual es mi ritmo umbral?")
        assert _is_running_threshold_query_intent("Dime el umbral de running")
        assert _is_running_threshold_query_intent("What is my threshold pace?")
        assert not _is_running_threshold_query_intent("Cual es mi umbral de lactato en mmol?")

    @pytest.mark.asyncio
    async def test_chat_running_threshold_uses_latest_persisted_profile(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "performance": {
                "running_threshold_pace": "4:54",
                "running_threshold_pace_sec_per_km": 294.0,
                "running_threshold_pace_date": "2026-08-10",
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

        latest_profile = {
            "performance": {
                "running_threshold_pace": "4:12",
                "running_threshold_pace_sec_per_km": 252.0,
                "running_threshold_pace_date": "2026-08-15",
            }
        }

        with patch_all("_load_user_profile", return_value=latest_profile), patch_all(
            "_save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "Cual es mi ritmo umbral actual?")

        assert "4:12 min/km" in out
        assert "15/08/2026" in out
        assert "perfil persistido" in out
        assert len(agent.conversation_history) == 2


class TestPeriodAndSleepRoutes:
    """Rutas deterministas del punto 3 de producción (25/10): periodo (km, la más larga) y sueño de anoche."""

    TODAY = date(2026, 10, 25)

    def test_resolve_activity_period(self):
        r = _routes_period._resolve_activity_period
        assert r("¿Cuántos km este mes?", self.TODAY)[:2] == (date(2026, 10, 1), self.TODAY)
        assert r("¿Y el mes pasado?", self.TODAY)[:2] == (date(2026, 9, 1), date(2026, 9, 30))
        assert r("La más larga de septiembre", self.TODAY)[:2] == (date(2026, 9, 1), date(2026, 9, 30))
        # Un mes posterior al actual sin año es el del año pasado; con año, el pedido.
        assert r("km de diciembre", self.TODAY)[:2] == (date(2025, 12, 1), date(2025, 12, 31))
        assert r("km de febrero de 2026", self.TODAY)[:2] == (date(2026, 2, 1), date(2026, 2, 28))
        assert r("km este año", self.TODAY)[:2] == (date(2026, 1, 1), self.TODAY)
        assert r("km la semana pasada", self.TODAY)[:2] == (date(2026, 10, 12), date(2026, 10, 18))  # 25/10 es domingo
        assert r("km esta semana", self.TODAY)[:2] == (date(2026, 10, 19), self.TODAY)
        assert r("¿Cuántos km corrí el 30/08?", self.TODAY) is None

    ACTS = [
        {"activityName": "Rodaje", "startTimeLocal": "2026-09-01 08:00:00", "distance": 16520.0,
         "duration": 5447.0, "elevationGain": 199.0, "activityType": {"typeKey": "running"}},
        {"activityName": "Trail largo", "startTimeLocal": "2026-09-11 08:00:00", "distance": 33520.0,
         "duration": 19858.0, "elevationGain": 1968.0, "activityType": {"typeKey": "trail_running"}},
        {"activityName": "Fuerza", "startTimeLocal": "2026-09-02 19:00:00", "distance": 0.0,
         "duration": 3880.0, "activityType": {"typeKey": "strength_training"}},
        {"activityName": "Fuera de rango", "startTimeLocal": "2026-10-01 08:00:00", "distance": 90000.0,
         "duration": 30000.0, "activityType": {"typeKey": "running"}},
    ]

    @pytest.mark.asyncio
    async def test_period_distance_sums_requested_sport_and_pages(self, monkeypatch):
        calls = []

        async def fake_call(_session, name, args):
            calls.append(args["page"])
            page = self.ACTS[:2] if args["page"] == 0 else self.ACTS[2:]
            return json.dumps({"activities": page, "has_more": args["page"] == 0})

        patch_everywhere(monkeypatch, "call_tool", fake_call)
        patch_everywhere(monkeypatch, "date", type("D", (date,), {"today": classmethod(lambda cls: self.TODAY)}))
        out = await _routes_period._build_period_activities_markdown(None, "¿Cuántos kilómetros he corrido en septiembre?")
        assert calls == [0, 1]
        assert "50.04 km en 2 actividades" in out  # carrera + trail; ni fuerza ni la de octubre
        assert "Fuerza" not in out and "Fuera de rango" not in out

    @pytest.mark.asyncio
    async def test_period_totals_give_rest_days_and_filter_strength(self, monkeypatch):
        async def fake_call(_session, name, args):
            return json.dumps({"activities": self.ACTS, "has_more": False})

        patch_everywhere(monkeypatch, "call_tool", fake_call)
        patch_everywhere(monkeypatch, "date", type("D", (date,), {"today": classmethod(lambda cls: self.TODAY)}))
        # 28/10: días sin entrenar y horas los sumaba el LLM.
        out = await _routes_period._build_period_activities_markdown(None, "¿Cuántos días he descansado en septiembre?")
        assert "Días con actividad: 3 de 30; sin actividad: 27." in out
        assert "en 3 actividades (08:06:25)" in out
        strength = await _routes_period._build_period_activities_markdown(None, "¿Cuántas sesiones de fuerza hice en septiembre?")
        assert "Fuerza en septiembre de 2026: 1 actividad (01:04:40)." in strength
        assert "| Fuerza | 1 | — | 01:04:40 |" in strength
        assert "Días con actividad: 1 de 30" in strength and "Trail largo" not in strength

    @pytest.mark.asyncio
    async def test_period_longest_activity(self, monkeypatch):
        async def fake_call(_session, name, args):
            return json.dumps({"activities": self.ACTS, "has_more": False})

        patch_everywhere(monkeypatch, "call_tool", fake_call)
        patch_everywhere(monkeypatch, "date", type("D", (date,), {"today": classmethod(lambda cls: self.TODAY)}))
        out = await _routes_period._build_period_activities_markdown(None, "¿Cuál fue mi actividad más larga de septiembre?")
        summary = out.split("## 📊")[0]
        assert "Trail largo · 33.52 km" in summary and "Fuera de rango" not in out

    @pytest.mark.asyncio
    async def test_sleep_last_night_falls_back_to_last_recorded_night(self, monkeypatch):
        nights = {"2026-10-24": {"sleepTimeSeconds": 24540, "sleepScore": 74, "deepSleepSeconds": 2940,
                                 "lightSleepSeconds": 18480, "remSleepSeconds": 3120, "awakeSleepSeconds": 60},
                  "2026-10-23": {"sleepTimeSeconds": 28800, "sleepScore": 80}}

        async def fake_call(_session, name, args):
            return json.dumps(nights.get(args["date"], {"date": args["date"], "sleepTimeSeconds": None}))

        patch_everywhere(monkeypatch, "call_tool", fake_call)
        patch_everywhere(monkeypatch, "date", type("D", (date,), {"today": classmethod(lambda cls: self.TODAY)}))
        out = await _routes_sleep._build_sleep_night_markdown(None)
        assert "aún no tiene el sueño de anoche" in out
        assert "La noche que terminó el sábado 24/10/2026 dormiste 6 h 49 min, puntuación 74/100" in out
        assert "Media de las 1 noches anteriores: 8 h 00 min" in out
        nights["2026-10-25"] = {"sleepTimeSeconds": 27000, "sleepScore": 85}
        out = await _routes_sleep._build_sleep_night_markdown(None)
        assert out.count("Anoche (noche que termina el domingo 25/10/2026) dormiste 7 h 30 min") == 1
        assert "aún no tiene" not in out

    def test_resolve_sleep_night_and_window(self):
        r = _routes_sleep._resolve_sleep_night
        assert r("¿Cómo dormí anoche?", self.TODAY) == self.TODAY
        assert r("¿Qué tal dormí ayer?", self.TODAY) == date(2026, 10, 24)
        assert r("¿Y anteayer?  sueño", self.TODAY) == date(2026, 10, 23)
        assert r("¿Cómo dormí el martes?", self.TODAY) == date(2026, 10, 20)  # el último martes, fecha de Garmin
        assert r("¿Cómo dormí el 20/10?", self.TODAY) == date(2026, 10, 20)
        assert r("mi sueño del 18 de octubre", self.TODAY) == date(2026, 10, 18)
        assert r("¿Cómo dormí el 30/12?", self.TODAY) == date(2025, 12, 30)  # sin año, nunca futura
        assert r("¿Cómo he dormido últimamente?", self.TODAY) is None
        assert r("Dormí mal, ¿puedo entrenar?", self.TODAY) is None
        w = _routes_sleep._resolve_sleep_window
        assert w("¿Cómo he dormido últimamente?") == 7
        assert w("¿Cómo he dormido las últimas 10 noches?") == 10
        assert w("Tendencia de mi sueño en las últimas semanas") == 14
        assert w("¿Cómo ha ido mi sueño este mes?") == 30
        assert w("¿Cómo dormí anoche?") is None

    @pytest.mark.asyncio
    async def test_other_night_without_data_says_so(self, monkeypatch):
        async def fake_call(_session, name, args):
            return json.dumps({"date": args["date"], "sleepTimeSeconds": None})

        patch_everywhere(monkeypatch, "call_tool", fake_call)
        patch_everywhere(monkeypatch, "date", type("D", (date,), {"today": classmethod(lambda cls: self.TODAY)}))
        out = await _routes_sleep._build_sleep_night_markdown(None, date(2026, 10, 20))
        assert "no tiene sueño registrado de la noche que termina el martes 20/10/2026" in out

    @pytest.mark.asyncio
    async def test_sleep_trend_compares_with_previous_period(self, monkeypatch):
        # Últimas 7 noches: 7 h salvo una de 5 h; las 7 anteriores: 8 h. Una noche sin datos.
        def night(i):
            if i == 3:
                return None
            hours = (5 if i == 1 else 7) if i < 7 else 8
            return {"sleepTimeSeconds": hours * 3600, "sleepScore": 70 if i < 7 else 85,
                    "deepSleepSeconds": 3600, "remSleepSeconds": 3600}

        async def fake_call(_session, name, args):
            i = (self.TODAY - date.fromisoformat(args["date"])).days
            return json.dumps(night(i) or {"sleepTimeSeconds": None})

        patch_everywhere(monkeypatch, "call_tool", fake_call)
        patch_everywhere(monkeypatch, "date", type("D", (date,), {"today": classmethod(lambda cls: self.TODAY)}))
        out = await _routes_sleep._build_sleep_trend_markdown(None, 7)
        # 6 noches con datos: 5 de 7 h y 1 de 5 h → 6 h 40 min; frente a 8 h → −80 min y −15 puntos.
        assert "media 6 h 40 min, puntuación 70/100; 1 por debajo de 6 h. Sin datos en 1 noche(s)." in out
        assert "-80 min de sueño y -15 puntos" in out
        assert "- jue 22/10: sin datos" in out
        assert _dates._signed(-0.3) == "0" and _dates._signed(4.6) == "+5"
