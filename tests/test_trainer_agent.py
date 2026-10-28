"""
tests/test_trainer_agent.py
Suite de tests unitarios para las funciones puras de trainer_agent.py.

Cubre:
  - _seconds_to_hhmmss
  - _normalize_date_args
  - _strip_garmin_object
  - _compact_tool_result / _compact_personal_records
"""

import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.trainer_agent as ta
from agent.load_metrics import _extract_threshold_pace_sec_per_km

from agent.trainer_agent import (
    _resolve_running_threshold_pace_sec_per_km,
    _resolve_hr_profile_values,
    _strip_simulated_user_turn,
    TrainerAgent,
)
from tests.agent_patch import patch_all, patch_everywhere
import agent.prompts as _prompts
from agent.prompts import PROMPTS_DIR, _PROMPT_MARKER, _load_coaching_rules, _load_system_prompt, _render_prompt
import agent.llm as _llm
from agent.llm import LLMAuthError
import agent.dates as _dates
from agent.dates import _extract_iso_date_from_text, _extract_iso_date_range_from_text, _normalize_date_args, _normalize_trend_date_range, _seconds_to_hhmmss
from agent.knowledge import _build_athlete_knowledge_context, _load_athlete_knowledge_chunks, _resolve_kb_paths, _retrieve_athlete_knowledge
from agent.tool_results import _compact_personal_records, _compact_tool_result, _extract_activities_list, _flatten_garmin_activity, _is_no_data_result, _strip_garmin_object
import agent.data_access as _data_access
from agent.garmin_data import _is_activity_in_last_48h
import agent.hr_profile as _hr_profile
from agent.hr_profile import _extract_cycling_ftp_watts, _resolve_running_threshold_pace_for_date, _resolve_tp_hr_threshold_for_date
import agent.wellness as _wellness
from agent.wellness import _format_body_battery_day, _format_hrv_day, _format_sleep_day, _pick_day_payload
import agent.kairos_tools as _kairos_tools
import agent.daily_engine as _daily_engine
from agent.daily_engine import _build_startup_plan_recommendation, _compute_daily_plan_adjustment, _compute_plan_execution_feedback, _get_active_training_plan, _get_planned_session_for_date
from agent.plans import _build_goal_plan_fallback, _build_training_plan_status_markdown, _generate_structured_plan_payload, _has_goal_in_profile, _summarize_plan_changes, _validate_structured_plan
import agent.routes.load as _routes_load
from agent.routes.load import _build_load_trend_table, _fetch_activities_for_load_calc, _format_load_fatigue_summary, _resolve_load_parameters_effective_date, _resolve_week_window
from agent.routes.records import _build_personal_records_markdown
from agent.routes.profile import _build_goal_status_markdown
import agent.routes.period as _routes_period
import agent.routes.sleep as _routes_sleep
import agent.activity_analysis as _activity_analysis
from agent.activity_analysis import _build_activity_candidates_payload, _build_post_activity_section_spec, _format_activity_analysis_for_markdown, _normalize_get_activity_args, _resolve_activity_id_from_query
from agent.startup_status import _build_proactive_status_markdown
from agent.replies import _build_mcp_error_warning_markdown, _build_mcp_transparency_markdown, _is_generic_needs_more_info_reply
import agent.router as _router
from agent.router import ToolRouter, _detect_personal_records_sport_intent, _is_activity_details_query_intent, _is_coaching_recommendation_intent, _is_config_options_intent, _is_daily_readiness_intent, _is_goal_status_intent, _is_hr_threshold_query_intent, _is_load_trend_intent, _is_mcp_factual_query_intent, _is_personal_records_followup_intent, _is_personal_records_intent, _is_plan_status_intent, _is_planning_intent, _is_running_threshold_query_intent, _is_today_load_status_intent, _is_week_activities_intent, _is_week_tss_followup_intent, _is_week_tss_intent
import agent.routes.day_data as _routes_day_data
from agent.routes.day_data import _build_recovery_fallback_snapshot
import agent.coaching as _coaching


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────


class TestSecondsToHhmmss:
    def test_below_one_hour_returns_mmss(self):
        assert _seconds_to_hhmmss(90) == "00:01:30"

    def test_zero_returns_mmss(self):
        assert _seconds_to_hhmmss(0) == "00:00:00"

    def test_sub_minute(self):
        assert _seconds_to_hhmmss(45) == "00:00:45"

    def test_exactly_one_hour(self):
        assert _seconds_to_hhmmss(3600) == "01:00:00"

    def test_above_one_hour(self):
        assert _seconds_to_hhmmss(5400) == "01:30:00"


class TestResolveHrProfileValues:
    def test_prefers_kairos_performance_hr_keys(self):
        profile = {
            "performance": {
                "hr_rest_bpm": 49,
                "hr_max_bpm": 188,
            }
        }

        hr_rest, hr_max = _resolve_hr_profile_values(profile)

        assert hr_rest == 49
        assert hr_max == 188

    def test_reads_garmin_user_data_hr_keys(self):
        profile = {
            "userData": {
                "restingHeartRate": 52,
                "maxHeartRate": 183,
            }
        }

        hr_rest, hr_max = _resolve_hr_profile_values(profile)

        assert hr_rest == 52
        assert hr_max == 183

    def test_float_rounds_up(self):
        # 90.6 → 91 segundos → 01:31
        assert _seconds_to_hhmmss(90.6) == "00:01:31"

    def test_marathon_time(self):
        # 3h30m = 12600s
        assert _seconds_to_hhmmss(12600) == "03:30:00"


# ─── _normalize_date_args ─────────────────────────────────────────────────────

class TestNormalizeDateArgs:
    def test_hoy(self):
        today = date.today().isoformat()
        assert _normalize_date_args({"date": "hoy"})["date"] == today

    def test_today_english(self):
        today = date.today().isoformat()
        assert _normalize_date_args({"startDate": "today"})["startDate"] == today

    def test_ayer(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert _normalize_date_args({"date": "ayer"})["date"] == yesterday

    def test_yesterday_english(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert _normalize_date_args({"endDate": "yesterday"})["endDate"] == yesterday

    def test_iso_passthrough(self):
        result = _normalize_date_args({"date": "2026-06-01"})
        assert result["date"] == "2026-06-01"

    def test_dd_mm_yyyy_converted_to_iso(self):
        result = _normalize_date_args({"start_date": "27/07/2026", "end_date": "02/08/2026"})
        assert result["start_date"] == "2026-07-27"
        assert result["end_date"] == "2026-08-02"

    def test_dd_mm_without_year_converted_to_iso(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        result = _normalize_date_args({"date": "27/07"})
        assert result["date"] == "2026-07-27"

    def test_non_date_field_never_replaced(self):
        # "activityId" no está en DATE_FIELDS → aunque el valor sea "hoy" no se toca
        result = _normalize_date_args({"activityId": "hoy"})
        assert result["activityId"] == "hoy"

    def test_keyword_case_insensitive(self):
        today = date.today().isoformat()
        result = _normalize_date_args({"date": "HOY"})
        assert result["date"] == today

    def test_empty_dict(self):
        assert _normalize_date_args({}) == {}

    def test_multiple_fields(self):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        result = _normalize_date_args({
            "startDate": "hoy",
            "endDate":   "ayer",
            "activityId": 123,
        })
        assert result["startDate"] == today
        assert result["endDate"]   == yesterday
        assert result["activityId"] == 123


# ─── _extract_iso_date_from_text ────────────────────────────────────────────

class TestExtractIsoDateFromText:
    def test_extracts_iso(self):
        assert _extract_iso_date_from_text("2026-07-02") == "2026-07-02"

    def test_extracts_spanish_month_with_year(self):
        assert _extract_iso_date_from_text("2 de julio de 2026") == "2026-07-02"

    def test_extracts_dd_mm_yyyy(self):
        assert _extract_iso_date_from_text("02/07/2026") == "2026-07-02"

    def test_extracts_dd_mm_yy_short_year(self):
        assert _extract_iso_date_from_text("17/08/26") == "2026-08-17"

    def test_extracts_dd_mm_without_year(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        assert _extract_iso_date_from_text("27/07") == "2026-07-27"


class TestExtractIsoDateRangeFromText:
    def test_extracts_del_al_without_year(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        assert _extract_iso_date_range_from_text("semana del 27/07 al 02/08") == ("2026-07-27", "2026-08-02")

    def test_extracts_entre_y_iso(self):
        assert _extract_iso_date_range_from_text("entre 2026-07-27 y 2026-08-02") == ("2026-07-27", "2026-08-02")

    def test_extracts_entre_el_y_el_without_year(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        assert _extract_iso_date_range_from_text("entre el 25/08 y el 30/08") == ("2026-08-25", "2026-08-30")

    def test_extracts_desde_hasta_without_year(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        assert _extract_iso_date_range_from_text("desde 25/08 hasta 30/08") == ("2026-08-25", "2026-08-30")

    def test_extracts_cross_year_without_year(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 12, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        assert _extract_iso_date_range_from_text("del 27/12 al 03/01") == ("2026-12-27", "2027-01-03")


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


class TestResolveRunningThresholdFromProfile:
    def test_resolve_running_threshold_from_flat_profile(self):
        profile = {
            "performance": {
                "running_threshold_pace": "4:20",
            }
        }
        out = _resolve_running_threshold_pace_sec_per_km(profile)
        assert out == 260.0

    def test_resolve_running_threshold_from_nested_profile(self):
        profile = {
            "userProfile": {
                "zones": {
                    "running": {
                        "thresholdPace": "4:30"
                    }
                }
            }
        }
        out = _resolve_running_threshold_pace_sec_per_km(profile)
        assert out == 270.0

    def test_resolve_running_threshold_from_nested_speed(self):
        profile = {
            "settings": {
                "runningThresholdSpeedMps": 3.7
            }
        }
        out = _resolve_running_threshold_pace_sec_per_km(profile)
        assert out is not None
        assert 269.0 <= out <= 271.0


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


class TestPromptFiles:
    """Los textos para el LLM viven en prompts/ (23/10); el código solo los compone."""

    def _prompt_names(self):
        pass

        return sorted(
            p.relative_to(PROMPTS_DIR).with_suffix("").as_posix()
            for p in PROMPTS_DIR.rglob("*.md")
            if p.name not in {"system_prompt.md", "coaching_rules.md"}
        )

    def test_every_prompt_renders_with_all_markers_resolved(self):
        pass

        names = self._prompt_names()
        assert len(names) >= 19
        for name in names:
            keys = set(_PROMPT_MARKER.findall((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")))
            out = _render_prompt(name, **{k: f"<{k}>" for k in keys})
            assert "{{" not in out and "}}" not in out, name
            assert out == out.strip("\n"), name

    def test_render_rejects_missing_or_extra_values(self):
        pass

        with pytest.raises(ValueError, match="marcadores"):
            _render_prompt("coaching/user", pregunta="x", ruta="y", horizonte="z")
        with pytest.raises(ValueError, match="marcadores"):
            _render_prompt("coaching/brief", sobra="x")

    def test_every_prompt_file_is_used_by_the_code(self):
        from pathlib import Path

        import agent.trainer_agent as ta

        # Todo agent/: al dividir trainer_agent.py (28/10) los prompts se usan desde varios módulos.
        src = "\n".join(p.read_text(encoding="utf-8") for p in Path(ta.__file__).parent.rglob("*.py"))
        unused = [n for n in self._prompt_names() if f'"{n}"' not in src]
        assert unused == [], f"Prompts sin usar: {unused}"


class TestCoachingRulesSingleSource:
    def test_system_prompt_embeds_coaching_rules_once(self):
        pass

        prompt = _load_system_prompt()
        rules = _load_coaching_rules()
        assert "{{REGLAS_DE_COACHING}}" not in prompt
        assert prompt.count(rules) == 1
        # Reglas de entrenador que viven solo en coaching_rules.md
        for heading in ("## Diabetes", "## Lesiones, sobrecargas y molestias a lo largo de la temporada",
                        "## Intensidades", "## Estado de hoy", "# Límites éticos"):
            assert heading in rules
        # El protocolo de diabetes va con el perfil, solo a quien lo necesita (26/10).
        assert "120–180 mg/dL" not in rules and "hipoglucemia" not in rules.lower()


NVIDIA = {"provider": "nvidia", "base_url": "https://integrate.api.nvidia.com/v1", "model": "m"}


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


class TestDiabetesProtocolInContext:
    """Atleta sano, con otra patología, con DT1 o con DT2 (26/10): protocolo según patologías y medicación."""

    def _block(self, health):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {"personal": {"name": "Ana"}, "health": health}
        return agent._profile_context_block()

    def test_healthy_athlete_with_an_injury_is_still_healthy(self):
        block = self._block({"pathologies": [], "injuries": ["periostitis"], "notes": ""})
        assert "- Patologías: ninguna declarada (atleta sano)" in block and "- Lesiones: periostitis" in block
        assert "Protocolo de diabetes" not in block and "gluc" not in block.lower() and "- Diabetes:" not in block

    def test_other_pathology_is_left_to_the_llm(self):
        block = self._block({"pathologies": ["asma"], "injuries": []})
        assert "- Patologías: asma" in block
        assert "Protocolo de diabetes" not in block and "atleta sano" not in block

    def test_diabetes_and_injury_together(self):
        block = self._block({"pathologies": ["DT1"], "diabetes_medication": ["bomba_insulina"], "injuries": ["periostitis"]})
        assert "- Patologías: DT1" in block and "- Lesiones: periostitis" in block
        assert "Protocolo de diabetes con riesgo de hipoglucemia" in block

    def test_old_profile_with_diabetes_in_injuries_is_not_healthy(self):
        block = self._block({"injuries": ["DT1"]})
        assert "atleta sano" not in block and "Protocolo de diabetes con riesgo de hipoglucemia" in block

    def test_dt1_gets_hypoglycemia_protocol_with_its_medication(self):
        block = self._block({"injuries": ["DT1"], "diabetes_medication": ["insulina_basal", "insulina_rapida"]})
        assert "- Diabetes: Diabetes tipo 1 · Insulina lenta (basal), Insulina rápida (bolos)" in block
        assert "Protocolo de diabetes con riesgo de hipoglucemia" in block and "120–180 mg/dL" in block

    def test_dt2_with_metformin_gets_its_own_protocol(self):
        block = self._block({"injuries": ["diabetes tipo 2"], "diabetes_medication": ["metformina"]})
        assert "- Diabetes: Diabetes tipo 2 · Metformina" in block
        assert "Protocolo de diabetes con dieta o fármacos" in block and "120–180 mg/dL" not in block
        # Ni siquiera nombra la hipoglucemia: el LLM la repetía para tranquilizar (prueba en vivo del 26/10).
        assert "hipoglu" not in block.lower()

    def test_dt2_with_insulin_gets_hypoglycemia_protocol(self):
        block = self._block({"injuries": ["DT2"], "diabetes_medication": ["metformina", "insulina_basal"]})
        assert "Protocolo de diabetes con riesgo de hipoglucemia" in block


class TestSystemPromptDateFormatRules:
    def test_full_prompt_requires_global_spanish_date_format(self):
        prompt = _load_system_prompt()
        assert "Formato de fecha obligatorio (España)" in prompt
        assert "Regla global de fechas (OBLIGATORIA)" in prompt
        assert "Regla de rangos explícitos (OBLIGATORIA)" in prompt
        assert "DD/MM/AAAA" in prompt
        assert "YYYY-MM-DD" in prompt


class TestSystemPromptPlanStatusRules:
    def test_full_prompt_includes_plan_status_intent_variants(self):
        prompt = _load_system_prompt()
        assert "Consulta de estado del plan (OBLIGATORIO)" in prompt
        assert "que plan llevo esta semana?" in prompt
        assert "sigo con el plan?" in prompt
        assert "goals" in prompt and "training_plan" in prompt


class TestSystemPromptPlanManagementRules:
    def test_full_prompt_includes_functional_plan_management(self):
        prompt = _load_system_prompt()
        assert "Generacion y manejo funcional de planes (OBLIGATORIO)" in prompt
        assert "/plan crear" in prompt
        assert "/plan listar" in prompt
        assert "/plan ver <plan_id>" in prompt
        assert "/plan activar <plan_id>" in prompt
        assert "No afirmes que un plan quedó guardado/activado" in prompt



class TestConcatenatedJsonFromMcp:
    # Formato real del MCP para get_personal_record: objetos JSON seguidos, sin lista.
    RAW = (
        '{\n  "typeId": 3,\n  "activityName": "Carrera 5K",\n  "activityType": "running",\n  "value": 1659.0\n}\n'
        '{\n  "typeId": 5,\n  "activityName": "Media",\n  "activityType": "running",\n  "value": 5483.0\n}'
    )

    def test_try_parse_json_reads_concatenated_objects_as_list(self):
        from agent.trainer_agent import (
            _try_parse_json,
        )

        data = _try_parse_json(self.RAW)
        assert isinstance(data, list) and [d["typeId"] for d in data] == [3, 5]

    def test_personal_records_route_renders_concatenated_payload(self):
        compact = _compact_tool_result(self.RAW, "get_personal_record")
        out = _build_personal_records_markdown(compact, preferred_sport="running")
        assert "| 5K | 27:39 |" in out
        assert "| Medio Maratón | 01:31:23 |" in out

    def test_flatten_garmin_activity_lifts_summary_dto(self):
        pass

        act = {"activityName": "X", "activityTypeDTO": {"typeKey": "trail_running"},
               "summaryDTO": {"duration": 3600.0, "distance": 10000.0, "averageHR": 140}}
        flat = _flatten_garmin_activity(act)
        assert (flat["duration"], flat["distance"], flat["averageHR"], flat["activityType"]) == (3600.0, 10000.0, 140, "trail_running")


class TestPostActivitySectionSpec:
    def test_recent_activity_keeps_recovery_next_sessions(self):
        today_d = date(2026, 7, 30)
        spec = _build_post_activity_section_spec("2026-07-29", today_d=today_d)
        assert spec["plan_context"] == "recent"
        assert "Recuperación y próximas sesiones" in spec["header"]
        assert "mañana" in spec["guidance"]

    def test_historical_activity_uses_learnings_no_short_term_schedule(self):
        today_d = date(2026, 7, 30)
        spec = _build_post_activity_section_spec("2026-07-20", today_d=today_d)
        assert spec["plan_context"] == "historical"
        assert "Aprendizajes" in spec["header"]
        assert "PROHIBIDO dar plan temporal corto" in spec["guidance"]


# ─── _strip_garmin_object ─────────────────────────────────────────────────────

class TestStripGarminObject:
    def test_removes_strip_fields(self):
        obj = {
            "startTimeGMT": "2026-06-17T08:00:00",
            "distance": 10000,
        }
        result = _strip_garmin_object(obj)
        assert "startTimeGMT" not in result
        assert result["distance"] == 10000

    def test_activity_id_NOT_stripped(self):
        """activityId debe llegar al LLM para que pueda llamar get_activity después de get_activities."""
        obj = {"activityId": "abc123", "distance": 5000}
        result = _strip_garmin_object(obj)
        assert "activityId" in result, "activityId no debe estar en _GARMIN_STRIP_FIELDS"

    def test_removes_image_url_keys(self):
        obj = {"profileImageUrl": "http://cdn.example.com/img.png", "steps": 8000}
        result = _strip_garmin_object(obj)
        assert "profileImageUrl" not in result
        assert result["steps"] == 8000

    def test_simplifies_activity_type_dict(self):
        obj = {"activityType": {"typeKey": "running", "sortOrder": 1}}
        result = _strip_garmin_object(obj)
        assert result["activityType"] == "running"

    def test_simplifies_event_type_dict(self):
        obj = {"eventType": {"typeKey": "race", "sortOrder": 2}}
        result = _strip_garmin_object(obj)
        assert result["eventType"] == "race"

    def test_nested_strip(self):
        obj = {"heartRate": {"avg": 155, "userProfileId": 999}}
        result = _strip_garmin_object(obj)
        assert "userProfileId" not in result["heartRate"]
        assert result["heartRate"]["avg"] == 155

    def test_list_truncated_to_4(self):
        lst = [{"v": i} for i in range(10)]
        result = _strip_garmin_object(lst)
        assert len(result) == 4

    def test_scalar_passthrough(self):
        assert _strip_garmin_object(42)      == 42
        assert _strip_garmin_object("hello") == "hello"
        assert _strip_garmin_object(None)    is None

    def test_empty_values_removed(self):
        obj = {"distance": 5000, "nothing": {}, "empty_list": []}
        result = _strip_garmin_object(obj)
        assert "nothing"    not in result
        assert "empty_list" not in result
        assert result["distance"] == 5000


# ─── _compact_tool_result ─────────────────────────────────────────────────────

class TestCompactToolResult:
    def test_none_returns_sin_datos(self):
        assert _compact_tool_result(None) == "(sin datos)"

    def test_empty_string_returns_sin_datos(self):
        assert _compact_tool_result("") == "(sin datos)"

    def test_list_truncated_to_8(self):
        raw = json.dumps([{"v": i} for i in range(20)])
        result = _compact_tool_result(raw)
        assert len(json.loads(result)) == 8

    def test_non_json_passthrough(self):
        assert _compact_tool_result("respuesta de texto plano") == "respuesta de texto plano"

    def test_long_string_truncated(self):
        long_str = "x" * 5000
        result = _compact_tool_result(long_str)
        assert result.endswith("...(truncado)")
        assert len(result) <= 3015  # _MAX_TOOL_RESULT_CHARS + sufijo

    def test_long_json_truncated(self):
        # JSON válido pero muy grande
        raw = json.dumps([{"data": "a" * 500} for _ in range(20)])
        result = _compact_tool_result(raw)
        assert result.endswith("...(truncado)")

    def test_personal_records_dispatched(self):
        data = [{"typeId": 3, "value": 1200.0, "activityName": "5K race", "activityType": "running"}]
        result = _compact_tool_result(json.dumps(data), tool_name="get_personal_records")
        assert "20:00" in result  # 1200 s = 20 min
        assert "5K"    in result

    def test_personal_record_singular_dispatched(self):
        data = [{"typeId": 3, "value": 1200.0, "activityName": "5K race", "activityType": "running"}]
        result = _compact_tool_result(json.dumps(data), tool_name="get_personal_record")
        assert "20:00" in result
        assert "5K" in result

    def test_dict_json_stripped(self):
        data = {"activityId": "abc", "startTimeGMT": "2026-01-01T07:00:00", "distance": 10000}
        result = _compact_tool_result(json.dumps(data))
        result_dict = json.loads(result)
        assert "startTimeGMT" not in result_dict
        assert result_dict["distance"] == 10000

    def test_get_activity_adds_normalized_fields(self):
        data = {"activityId": 123, "duration": 36612.18359375, "distance": 54428.41015625}
        result = _compact_tool_result(json.dumps(data), tool_name="get_activity")
        result_dict = json.loads(result)
        assert result_dict["duration_hhmmss"] == "10:10:12"
        assert result_dict["distance_km"] == 54.43

    def test_get_activity_without_distance_does_not_raise_unboundlocal(self):
        data = {
            "activityId": 123,
            "activityType": "running",
            "duration": 3600,
            "avgPower": 250,
            "maxPower": 410,
        }
        result = _compact_tool_result(json.dumps(data), tool_name="get_activity")
        result_dict = json.loads(result)
        assert result_dict["duration_hhmmss"] == "01:00:00"

    def test_get_activity_without_duration_does_not_raise_unboundlocal(self):
        data = {
            "activityId": 456,
            "activityType": "running",
            "distance": 10000,
            "avgHr": 150,
            "maxHr": 178,
        }
        result = _compact_tool_result(json.dumps(data), tool_name="get_activity")
        result_dict = json.loads(result)
        assert result_dict["distance_km"] == 10.0

    def test_get_body_battery_prefers_item_with_level_when_multiple_entries(self):
        data = [
            {"date": "2026-10-06", "charged": 58, "drained": 31},
            {"date": "2026-10-06", "bodyBatteryLevel": 69, "charged": 58, "drained": 31},
        ]
        result = _compact_tool_result(json.dumps(data), tool_name="get_body_battery")
        parsed = json.loads(result)
        assert parsed.get("level") == 69
        assert parsed.get("charged") == 58
        assert parsed.get("drained") == 31

    def test_get_hrv_data_reads_nested_hrv_summary(self):
        # Forma real de Garmin: los valores van en hrvSummary; antes caía a la poda genérica.
        data = {"userProfilePk": 1, "hrvReadings": [{"hrvValue": 70}] * 50,
                "hrvSummary": {"weeklyAvg": 61, "lastNightAvg": 56, "status": "BALANCED"}}
        parsed = json.loads(_compact_tool_result(json.dumps(data), tool_name="get_hrv_data"))
        assert parsed == {"lastNightAvg": 56, "weeklyAvg": 61, "status": "BALANCED"}

    def test_hrv_trend_keeps_every_day_of_the_series(self):
        # La poda genérica dejaba 4 de 15 días y el LLM inventaba el resto.
        rows = [{"date": f"2026-10-{d:02d}", "hrv": None if d == 9 else 60 + d % 5, "weekly_avg": 61,
                 "status": "BALANCED"} for d in range(8, 23)]
        data = {"start_date": "2026-10-08", "end_date": "2026-10-22", "days_with_data": 14, "trend": rows}
        parsed = json.loads(_compact_tool_result(json.dumps(data), tool_name="get_hrv_trend"))
        assert parsed["columns"] == ["date", "hrv", "weekly_avg", "status"]
        assert len(parsed["rows"]) == 15
        assert parsed["rows"][1] == ["2026-10-09", None, 61, "BALANCED"]
        assert parsed["days_with_data"] == 14
        assert "omitted_oldest_days" not in parsed
        # Resumen determinista para que el LLM no cuente ni promedie.
        assert parsed["rows_total"] == 15
        hrv = parsed["summary"]["hrv"]
        values = [r["hrv"] for r in rows if r["hrv"] is not None]
        assert hrv["n"] == 14 and hrv["min"] == min(values) and hrv["max"] == max(values)
        assert hrv["mean"] == round(sum(values) / 14, 1)
        assert hrv["first"] == {"date": "2026-10-08", "value": rows[0]["hrv"]}
        assert hrv["last"] == {"date": "2026-10-22", "value": rows[-1]["hrv"]}
        assert parsed["summary"]["status"] == {"n": 15, "counts": {"BALANCED": 15}}
        assert "date" not in parsed["summary"]

    def test_long_daily_series_drops_oldest_days_and_says_so(self):
        rows = [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "trainingLoad": 123.45} for i in range(200)]
        data = {"start_date": "x", "end_date": "y", "days_with_data": 200, "trend": rows}
        compact = _compact_tool_result(json.dumps(data), tool_name="get_training_load_trend")
        parsed = json.loads(compact)
        assert len(compact) <= 3000
        assert parsed["omitted_oldest_days"] + len(parsed["rows"]) == 200
        assert parsed["rows"][-1] == [rows[-1]["date"], 123.45]


# ─── Contexto de la sesión anterior ─────────────────────────────────────────

class TestPreviousSessionContext:
    """De la sesión anterior solo llegan al LLM las preguntas, nunca las respuestas (cifras viejas)."""

    def _agent(self, history):
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.conversation_history = []
        agent.knowledge_chunks = []
        agent._build_system_prompt = lambda: "SYSTEM"
        return agent, {"history": history}

    def test_restores_only_user_questions_and_no_old_answers(self, monkeypatch):
        agent, ctx = self._agent([
            {"role": "user", "content": "¿Cómo ha evolucionado mi HRV?"},
            {"role": "assistant", "content": "No hay datos de HRV (0 días con datos)."},
        ])
        patch_everywhere(monkeypatch, "_load_session_context", lambda: ctx)
        agent._restore_previous_session()
        assert agent.previous_session_questions == ("¿Cómo ha evolucionado mi HRV?",)
        assert agent.conversation_history == []

        messages = agent._build_messages("¿Y hoy?")
        text = "\n".join(m["content"] for m in messages)
        assert "¿Cómo ha evolucionado mi HRV?" in text
        assert "0 días con datos" not in text
        assert [m["role"] for m in messages] == ["system", "system", "user"]

    def test_no_note_without_previous_session(self):
        agent, _ = self._agent([])
        assert [m["role"] for m in agent._build_messages("hola")] == ["system", "user"]

    def test_battery_neither_reads_nor_writes_the_athlete_memory(self, monkeypatch):
        # 26/10: las preguntas de la batería llenaban la memoria del atleta y el LLM las recibía como suyas.
        import agent.trainer_agent as ta
        monkeypatch.setenv("KAIROS_SESSION_MEMORY", "false")
        load, save, persist = MagicMock(), MagicMock(), MagicMock()
        patch_everywhere(monkeypatch, "_load_session_context", load)
        monkeypatch.setattr(ta._storage, "save_history_entry", save)
        monkeypatch.setattr(ta._storage, "persist_session_summary_daily", persist)
        agent, _ = self._agent([])
        agent.conversation_history = [{"role": "user", "content": "¿Dime los TSS de mis 20 últimas actividades?"}]

        agent._restore_previous_session()
        _data_access._save_history_entry("user", "¿Dime los TSS?")
        agent.save_session_summary_checkpoint()

        assert agent.previous_session_questions == ()
        load.assert_not_called()
        save.assert_not_called()
        persist.assert_not_called()


# ─── Base de conocimiento del atleta (RAG) ──────────────────────────────────

class TestAthleteKnowledgeRag:
    def test_resolve_kb_paths_uses_defaults_when_env_empty(self, tmp_path: Path):
        paths = _resolve_kb_paths("", project_root=tmp_path)
        assert len(paths) >= 3
        assert str(paths[0]).startswith(str(tmp_path))

    def test_load_knowledge_chunks_from_txt_and_json(self, tmp_path: Path):
        txt_file = tmp_path / "athlete_notes.txt"
        txt_file.write_text("Objetivo: bajar de 10h en la PDA.\nFuerza de sóleo 2 veces/semana.", encoding="utf-8")

        json_file = tmp_path / "athlete_profile.json"
        json_file.write_text(
            json.dumps({"nutrition": {"during_long_run": "60-90g CH/h"}, "injuries": ["soleo"]}, ensure_ascii=False),
            encoding="utf-8",
        )

        env_paths = f"{txt_file},{json_file}"
        chunks, sources = _load_athlete_knowledge_chunks(env_paths, project_root=tmp_path, chunk_size=120)

        assert chunks, "Debe generar chunks desde archivos válidos"
        assert "athlete_notes.txt" in sources
        assert "athlete_profile.json" in sources
        joined = "\n".join(c["text"] for c in chunks)
        assert "bajar de 10h" in joined
        assert "during_long_run" in joined

    def test_retrieve_returns_most_relevant_chunks(self):
        chunks = [
            {"source": "a.txt", "text": "Trabajo de umbral y VO2max para 10K."},
            {"source": "b.txt", "text": "Diabetes tipo 1: controlar glucemia antes de tiradas largas."},
            {"source": "c.txt", "text": "Series en cuesta y técnica de bajada con bastones."},
        ]
        out = _retrieve_athlete_knowledge("Tengo diabetes y haré tirada larga", chunks, top_k=2)
        assert out
        assert out[0]["source"] == "b.txt"

    def test_build_knowledge_context_includes_header_and_source(self):
        chunks = [{"source": "kb.md", "text": "Objetivo principal: PDA sub10h."}]
        ctx = _build_athlete_knowledge_context("objetivo PDA", chunks)
        assert "Base de Conocimiento del atleta" in ctx
        assert "Fuente: kb.md" in ctx
        assert "PDA sub10h" in ctx


# ─── _compact_personal_records ────────────────────────────────────────────────

class TestCompactPersonalRecords:
    def test_5k_time_conversion(self):
        data = [{"typeId": 3, "value": 1200.0, "activityName": "5K", "activityType": "running"}]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["tiempo"] == "20:00"
        assert result[0]["tipo"]   == "5K"

    def test_half_marathon_time(self):
        data = [{"typeId": 5, "value": 5400.0, "activityName": "HM", "activityType": "running"}]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["tiempo"] == "01:30:00"

    def test_marathon_time(self):
        # 4 horas = 14400 s
        data = [{"typeId": 6, "value": 14400.0, "activityName": "Marathon", "activityType": "running"}]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["tiempo"] == "04:00:00"

    def test_longest_run_km(self):
        data = [{"typeId": 7, "value": 42195.0, "activityName": "Long Run", "activityType": "running"}]
        result = json.loads(_compact_personal_records(data))
        assert "42.20 km" in result[0]["distancia"]

    def test_swim_short_returns_metres(self):
        # 400 m de natación → metros
        data = [{"typeId": 17, "value": 400.0, "activityName": "Swim", "activityType": "pool_swimming"}]
        result = json.loads(_compact_personal_records(data))
        assert "400 m" in result[0]["distancia"]

    def test_swim_long_returns_km(self):
        data = [{"typeId": 17, "value": 3800.0, "activityName": "Open water", "activityType": "open_water"}]
        result = json.loads(_compact_personal_records(data))
        assert "3.80 km" in result[0]["distancia"]

    def test_unknown_type_id(self):
        data = [{"typeId": 999, "value": 100, "activityName": "X", "activityType": "running"}]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["tipo"] == "typeId=999"

    def test_skips_non_dict_entries(self):
        data = [
            {"typeId": 3, "value": 900.0, "activityName": "5K", "activityType": "running"},
            "bad_entry",
            None,
        ]
        result = json.loads(_compact_personal_records(data))
        assert len(result) == 1

    def test_daily_steps(self):
        data = [{"typeId": 12, "value": 25432, "activityName": "Day", "activityType": "steps"}]
        result = json.loads(_compact_personal_records(data))
        assert "pasos" in result[0]

    def test_supports_snake_case_payload_shape(self):
        data = [
            {
                "record_type": "Fastest 10K",
                "type_id": 4,
                "value": "35:53",
                "raw_value": 2153.6,
            }
        ]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["categoria"] == "10K"
        assert result[0]["valor"] == "35:53"

    def test_translates_unknown_record_type_to_spanish(self):
        data = [
            {
                "record_type": "Longest Ride",
                "value": "199.02 km",
            }
        ]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["categoria"] == "Ciclismo más largo"


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


# ─── _normalize_get_activity_args ───────────────────────────────────────────

class TestNormalizeGetActivityArgs:
    @pytest.mark.asyncio
    async def test_keeps_numeric_activity_id(self):
        out = await _normalize_get_activity_args(MagicMock(), {"activity_id": "12345"})
        assert out == {"activity_id": 12345}

    @pytest.mark.asyncio
    async def test_resolves_spanish_date_to_activity_id(self):
        fake_response = json.dumps(
            {
                "start": 0,
                "limit": 100,
                "count": 2,
                "has_more": False,
                "next_start": 100,
                "activities": [
                    {"activityId": 111, "startTimeLocal": "2026-07-01T07:00:00.0"},
                    {"activityId": 222, "startTimeLocal": "2026-07-02T07:30:15.0"},
                ],
            }
        )
        with patch_all("call_tool", return_value=fake_response):
            out = await _normalize_get_activity_args(MagicMock(), {"activity_id": "2 de julio de 2026"})
        assert out == {"activity_id": 222}

    @pytest.mark.asyncio
    async def test_resolves_activity_name_hint_to_activity_id(self):
        fake_response = json.dumps(
            {
                "start": 0,
                "limit": 100,
                "count": 2,
                "has_more": False,
                "next_start": 100,
                "activities": [
                    {"activityId": 1001, "name": "Rodaje suave 8km", "startTimeLocal": "2026-07-03T07:00:00.0"},
                    {
                        "activityId": 2002,
                        "name": "Ultra Trail. Hoka Val d'Aran Pyrenees by UTMB PDA 2026",
                        "startTimeLocal": "2026-07-02T07:30:15.0",
                    },
                ],
            }
        )
        with patch_all("call_tool", return_value=fake_response):
            out = await _normalize_get_activity_args(MagicMock(), {"activity_id": "Ultra Trail. Hoka Val d"})
        assert out == {"activity_id": 2002}

    @pytest.mark.asyncio
    async def test_returns_empty_args_for_unresolved_text_activity_id(self):
        fake_response = json.dumps(
            {
                "start": 0,
                "limit": 100,
                "count": 1,
                "has_more": False,
                "next_start": 100,
                "activities": [
                    {"activityId": 3003, "name": "Paseo", "startTimeLocal": "2026-07-01T07:00:00.0"},
                ],
            }
        )
        with patch_all("call_tool", return_value=fake_response):
            out = await _normalize_get_activity_args(MagicMock(), {"activity_id": "actividad inventada"})
        assert out == {}

    @pytest.mark.asyncio
    async def test_recovers_activity_id_from_user_message_date_when_args_empty(self):
        fake_response = json.dumps(
            {
                "start": 0,
                "limit": 100,
                "count": 2,
                "has_more": False,
                "next_start": 100,
                "activities": [
                    {"activityId": 111, "startTimeLocal": "2026-07-01T07:00:00.0"},
                    {"activityId": 222, "startTimeLocal": "2026-07-02T09:30:00.0"},
                ],
            }
        )
        with patch_all("call_tool", return_value=fake_response):
            out = await _normalize_get_activity_args(
                MagicMock(),
                {},
                user_message="Analiza mi competición del 2 de julio de 2026",
            )
        assert out == {"activity_id": 222}

    @pytest.mark.asyncio
    async def test_recovers_activity_id_from_user_message_name_when_args_empty(self):
        fake_response = json.dumps(
            {
                "start": 0,
                "limit": 100,
                "count": 2,
                "has_more": False,
                "next_start": 100,
                "activities": [
                    {"activityId": 1001, "name": "Rodaje suave 8km", "startTimeLocal": "2026-07-03T07:00:00.0"},
                    {
                        "activityId": 2002,
                        "name": "Ultra Trail. Hoka Val d'Aran Pyrenees by UTMB PDA 2026",
                        "startTimeLocal": "2026-07-02T07:30:15.0",
                    },
                ],
            }
        )
        with patch_all("call_tool", return_value=fake_response):
            out = await _normalize_get_activity_args(
                MagicMock(),
                {},
                user_message="Analiza mi Ultra Trail. Hoka Val d",
            )
        assert out == {"activity_id": 2002}

    @pytest.mark.asyncio
    async def test_date_query_does_not_fallback_to_name_when_date_missing(self):
        fake_response = json.dumps(
            {
                "start": 0,
                "limit": 100,
                "count": 2,
                "has_more": False,
                "next_start": 100,
                "activities": [
                    {
                        "activityId": 4001,
                        "name": "Competición local 10K",
                        "startTimeLocal": "2026-07-04T08:00:00.0",
                    },
                    {
                        "activityId": 4002,
                        "name": "Senderismo",
                        "startTimeLocal": "2026-07-04T10:00:00.0",
                    },
                ],
            }
        )
        with patch_all("call_tool", return_value=fake_response):
            out = await _normalize_get_activity_args(
                MagicMock(),
                {},
                user_message="Analiza mi competición del 2 de julio de 2026",
            )
        assert out == {}


class TestResolveActivityIdFromQuery:
    @pytest.mark.asyncio
    async def test_resolves_from_query_date(self):
        fake_response = json.dumps(
            {
                "start": 0,
                "limit": 100,
                "count": 2,
                "has_more": False,
                "next_start": 100,
                "activities": [
                    {"activityId": 111, "startTimeLocal": "2026-07-01T07:00:00.0"},
                    {"activityId": 222, "startTimeLocal": "2026-07-02T07:30:00.0"},
                ],
            }
        )
        with patch_all("call_tool", return_value=fake_response):
            out = await _resolve_activity_id_from_query(
                MagicMock(),
                "Analiza mi competición del 2 de julio de 2026",
            )
        assert out == 222

    @pytest.mark.asyncio
    async def test_build_candidates_payload_returns_candidates(self):
        fake_response = json.dumps(
            {
                "activities": [
                    {
                        "activityId": 333,
                        "name": "Zara Speed Run 10k",
                        "startTimeLocal": "2026-07-02T08:00:00.0",
                    }
                ]
            }
        )
        with patch_all("call_tool", return_value=fake_response):
            raw = await _build_activity_candidates_payload(
                MagicMock(),
                "Analiza mi competición del 2 de julio de 2026",
            )

        parsed = json.loads(raw)
        assert parsed["error"] == "missing_activity_id"
        assert parsed["candidates"]

    @pytest.mark.asyncio
    async def test_date_query_strict_no_name_fallback(self):
        fake_response = json.dumps(
            {
                "activities": [
                    {
                        "activityId": 5001,
                        "name": "Competición 10K",
                        "startTimeLocal": "2026-07-04T08:00:00.0",
                    }
                ]
            }
        )
        with patch_all("call_tool", return_value=fake_response):
            out = await _resolve_activity_id_from_query(
                MagicMock(),
                "Analiza mi competición del 2 de julio de 2026",
            )
        assert out is None


# ─── Fallback de recuperación ───────────────────────────────────────────────

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


# ─── Startup 48h proactivo ────────────────────────────────────────────────

class TestStartupProactive:
    def test_extract_activities_list_supports_dict_payload(self):
        payload = {"activities": [{"activityId": 1}, {"activityId": 2}]}
        out = _extract_activities_list(payload)
        assert len(out) == 2

    def test_is_activity_in_last_48h_true_for_recent_day(self):
        today = date.today().isoformat()
        activity = {"startTimeLocal": f"{today}T08:00:00.0"}
        now = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
        assert _is_activity_in_last_48h(activity, now=now)

    def test_is_activity_in_last_48h_true_for_recent_day_snake_case(self):
        today = date.today().isoformat()
        activity = {"start_time_local": f"{today}T08:00:00.0"}
        now = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
        assert _is_activity_in_last_48h(activity, now=now)

    def test_build_proactive_status_markdown_contains_sections(self):
        payload = {
            "profile_changes": ["peso", "altura"],
            "body_battery": {"summary": "hoy=ok · ayer=ok"},
            "hrv": {"summary": "hoy=no · ayer=ok"},
            "sleep": {"summary": "hoy=ok · ayer=no"},
            "load_fatigue": {
                "latest": {"tss": 75.0, "atl": 62.0, "ctl": 55.0, "tsb": -7.0},
                "weekly": {"current_tss": 420.0},
                "ranges": {"tsb_low": -10.0, "tsb_high": 5.0, "atl_high": 70.0},
                "recommendation": "Puedes mantener sesión de calidad o progresión controlada según plan.",
                "action": "buena disponibilidad",
            },
            "trainings": [{"date": "2026-07-06", "name": "Trail suave"}],
        }
        out = _build_proactive_status_markdown(payload)
        assert "Estado proactivo" in out
        assert "Perfil Garmin actualizado" in out
        assert "Trail suave" in out
        assert "Carga/Fatiga (TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma))" in out
        assert "No tienes plan asignado" in out

    def test_proactive_status_flags_unsynced_today_instead_of_datos_disponibles(self):
        # Caso real (22/10): Garmin devuelve el registro de hoy sin valores (reloj sin sincronizar).
        today, yday = "2026-10-22", "2026-10-21"
        payload = {
            "dates": {"today": today, "yesterday": yday},
            "body_battery": {
                "today": [{"date": today, "charged": None, "drained": None, "bodyBatteryValuesArray": [[1, None]]}],
                "yesterday": [{"date": yday, "charged": 48, "drained": 15}],
            },
            "hrv": {"today": None, "yesterday": {"calendarDate": yday, "lastNightAvg": 56, "weeklyAvg": 61, "status": "BALANCED"}},
            "sleep": {
                "today": {"calendarDate": today, "sleepTimeSeconds": None},
                "yesterday": {"calendarDate": yday, "sleepTimeSeconds": 29040, "sleepScore": 69},
            },
            "load_fatigue": {},
            "trainings": [],
        }

        out = _build_proactive_status_markdown(payload)

        assert "datos disponibles" not in out
        assert "noche anterior=8 h 04 min · puntuación 69" in out
        assert "Garmin aún no tiene Body Battery de hoy, HRV de anoche, sueño de anoche" in out
        assert "sincroniza el reloj" in out

    def test_format_wellness_day_without_values_says_sin_datos(self):
        today = date.today().isoformat()
        assert _format_body_battery_day([{"date": today, "charged": None, "drained": None}], today) == "sin datos"
        assert _format_sleep_day({"calendarDate": today, "sleepTimeSeconds": None}, today) == "sin datos"

    def test_format_body_battery_day_prefers_level_from_most_informative_entry(self):
        payload = [
            {"date": date.today().isoformat(), "charged": 58, "drained": 31},
            {"date": date.today().isoformat(), "bodyBatteryLevel": 69, "charged": 58, "drained": 31},
        ]
        out = _format_body_battery_day(payload, date.today().isoformat())
        assert "nivel 69" in out
        assert "cargado/drenado +58/-31" in out

    def test_pick_day_payload_supports_nested_data_envelopes(self):
        target = date.today().isoformat()
        payload = {
            "ok": True,
            "data": {
                "items": [
                    {"date": "2026-01-01", "value": 10},
                    {"date": target, "value": 55},
                ]
            },
        }
        picked = _pick_day_payload(payload, target)
        assert picked is not None
        assert picked.get("value") == 55

    def test_format_hrv_day_with_nested_envelope(self):
        target = date.today().isoformat()
        payload = {
            "result": {
                "measurements": [
                    {"date": target, "lastNightAvg": 51, "weeklyAvg": 55, "status": "balanced"}
                ]
            }
        }
        out = _format_hrv_day(payload, target)
        assert "51.0 ms" in out
        assert "7d 55.0 ms" in out

    def test_build_proactive_status_markdown_shows_plan_recommendation_when_assigned(self):
        payload = {
            "plan_assigned": True,
            "plan_recommendation": "Tienes un objetivo activo (10K). ¿Quieres que adapte la sesion de hoy a ese plan?",
            "body_battery": {"summary": "sin datos"},
            "hrv": {"summary": "sin datos"},
            "sleep": {"summary": "sin datos"},
            "trainings": [],
        }
        out = _build_proactive_status_markdown(payload)
        assert "Tienes un objetivo activo (10K)" in out

    def test_build_proactive_status_markdown_shows_deterministic_decision(self):
        payload = {
            "plan_assigned": True,
            "plan_recommendation": "Plan activo",
            "daily_plan_decision": {
                "decision": "reduce",
                "reason": "estado neutral con una señal de riesgo",
                "resulting_session": "Rodaje 50' -> reducir volumen 20-30%",
            },
            "body_battery": {"summary": "sin datos"},
            "hrv": {"summary": "sin datos"},
            "sleep": {"summary": "sin datos"},
            "trainings": [],
        }
        out = _build_proactive_status_markdown(payload)
        assert "Elección recomendada hoy: rodaje suave" in out
        assert "Motor determinista" not in out
        assert "Motivo: estado neutral con una señal de riesgo" in out

    def test_compute_daily_plan_adjustment_overload_forces_rest(self):
        snapshot = {
            "dates": {"today": date.today().isoformat()},
            "load_fatigue": {
                "status": "overload",
                "latest": {"tsb": -40.0, "atl": 90.0},
                "weekly": {"current_tss": 700.0},
                "ranges": {"atl_high": 75.0},
            },
            "body_battery": {"today": {"date": date.today().isoformat(), "body_battery_level": 20}},
            "sleep": {"today": {"date": date.today().isoformat(), "sleep_hours": 5.2, "sleep_score": 50}},
            "hrv": {"today": {"date": date.today().isoformat(), "lastNightAvg": 35, "weeklyAvg": 50, "status": "low"}},
        }
        plan = {"title": "Plan 10K", "today_focus": "Series 6x800"}
        out = _compute_daily_plan_adjustment(snapshot, plan)
        assert out is not None
        assert out["decision"] == "rest"
        assert out["rule"] == "overload"

    def test_compute_daily_plan_adjustment_ready_can_maintain(self):
        snapshot = {
            "dates": {"today": date.today().isoformat()},
            "load_fatigue": {
                "status": "ready",
                "latest": {"tsb": 4.0, "atl": 55.0},
                "weekly": {"current_tss": 300.0, "high_tss": 450.0},
                "ranges": {"atl_high": 70.0},
            },
            "body_battery": {"today": {"date": date.today().isoformat(), "body_battery_level": 70}},
            "sleep": {"today": {"date": date.today().isoformat(), "sleep_hours": 7.8, "sleep_score": 82}},
            "hrv": {"today": {"date": date.today().isoformat(), "lastNightAvg": 52, "weeklyAvg": 50, "status": "balanced"}},
        }
        plan = {"title": "Plan 10K", "today_focus": "Tempo 40'"}
        out = _compute_daily_plan_adjustment(snapshot, plan)
        assert out is not None
        assert out["decision"] == "maintain"
        assert out["rule"] == "ready"

    def test_compute_daily_plan_adjustment_ready_with_weekly_spike_reduces(self):
        snapshot = {
            "dates": {"today": date.today().isoformat()},
            "load_fatigue": {
                "status": "ready",
                "latest": {"tsb": 4.5, "atl": 52.0},
                "weekly": {"current_tss": 310.0, "high_tss": 450.0},
                "ranges": {"atl_high": 70.0},
                "flags": {"weekly_spike_alert": True},
            },
            "body_battery": {"today": {"date": date.today().isoformat(), "body_battery_level": 72}},
            "sleep": {"today": {"date": date.today().isoformat(), "sleep_hours": 8.0, "sleep_score": 84}},
            "hrv": {"today": {"date": date.today().isoformat(), "lastNightAvg": 54, "weeklyAvg": 52, "status": "balanced"}},
        }
        plan = {"title": "Plan 10K", "today_focus": "Tempo 40'"}

        out = _compute_daily_plan_adjustment(snapshot, plan)

        assert out is not None
        assert out["rule"] == "ready"
        assert out["decision"] == "reduce"

    def test_compute_plan_execution_feedback_returns_adherence_and_deviation(self):
        yday = (date.today() - timedelta(days=1)).isoformat()
        plan = {
            "sessions": [
                {
                    "week_index": 1,
                    "day_index": date.fromisoformat(yday).isoweekday(),
                    "session_type": "running_quality",
                    "duration_min": 60,
                    "intensity": "RPE 7-8",
                    "structured_workout": {
                        "schema": "kairos-workout-v1",
                        "sessionType": "running_quality",
                        "steps": [
                            {"name": "Warm-up", "type": "warmup", "duration_min": 12, "reps": 1, "intensityClass": "endurance"},
                            {
                                "name": "Main Intervals",
                                "type": "interval_block",
                                "reps": 6,
                                "steps": [
                                    {"name": "Work", "type": "work", "duration_min": 3, "intensityClass": "threshold"},
                                    {"name": "Recovery", "type": "recovery", "duration_min": 2, "intensityClass": "recovery"},
                                ],
                            },
                            {"name": "Cool-down", "type": "cooldown", "duration_min": 10, "reps": 1, "intensityClass": "recovery"},
                        ],
                    },
                }
            ]
        }
        acts = [
            {
                "activityId": 101,
                "type": "running",
                "averagePace": "3:20",  # 3:20 min/km
                "trainingLoad": 90,
                "startTimeLocal": f"{yday}T08:00:00.0",
            }
        ]

        out = _compute_plan_execution_feedback(plan, acts, yday, profile={})
        assert out["adherence_score"] >= 0.75
        assert out["planned"]["duration_min"] == 60.0
        assert "load_deviation_pct" in out
        assert "bloque principal" in str(out["planned"].get("structured_summary") or "")
        block_feedback = out.get("block_feedback") or {}
        summary = block_feedback.get("summary") or {}
        assert int(summary.get("total_blocks") or 0) >= 1
        assert "time_deviation_pct" in summary

    def test_compute_daily_plan_adjustment_degrades_on_high_positive_deviation(self):
        snapshot = {
            "dates": {"today": date.today().isoformat()},
            "load_fatigue": {
                "status": "ready",
                "latest": {"tsb": 3.0, "atl": 50.0},
                "weekly": {"current_tss": 250.0, "high_tss": 450.0},
                "ranges": {"atl_high": 70.0},
            },
            "body_battery": {"today": {"date": date.today().isoformat(), "body_battery_level": 75}},
            "sleep": {"today": {"date": date.today().isoformat(), "sleep_hours": 8.0, "sleep_score": 85}},
            "hrv": {"today": {"date": date.today().isoformat(), "lastNightAvg": 50, "weeklyAvg": 50, "status": "balanced"}},
            "plan_execution_feedback": {
                "adherence_score": 0.9,
                "load_deviation_pct": 0.5,
            },
        }
        today_idx = date.today().isoweekday()
        week_start = date.today() - timedelta(days=today_idx - 1)
        plan = {
            "title": "Plan 10K",
            "today_focus": "Tempo 45'",
            "plan_data": {"start_date": week_start.isoformat()},
            "sessions": [
                {
                    "week_index": 1,
                    "day_index": today_idx,
                    "session_type": "running_quality",
                    "duration_min": 45,
                    "intensity": "RPE 7-8",
                    "structured_workout": {
                        "schema": "kairos-workout-v1",
                        "sessionType": "running_quality",
                        "steps": [
                            {"name": "Warm-up", "type": "warmup", "duration_min": 10, "reps": 1, "intensityClass": "endurance"},
                            {
                                "name": "Main Intervals",
                                "type": "interval_block",
                                "reps": 5,
                                "steps": [
                                    {"name": "Work", "type": "work", "duration_min": 3, "intensityClass": "threshold"},
                                    {"name": "Recovery", "type": "recovery", "duration_min": 2, "intensityClass": "recovery"},
                                ],
                            },
                            {"name": "Cool-down", "type": "cooldown", "duration_min": 10, "reps": 1, "intensityClass": "recovery"},
                        ],
                    },
                }
            ],
        }
        out = _compute_daily_plan_adjustment(snapshot, plan)
        assert out is not None
        assert out["decision"] == "reduce"
        assert out["adherence_adjustment"] == "down"
        assert "bloque principal" in str(out.get("resulting_session") or "").lower() or "repeticiones" in str(out.get("resulting_session") or "").lower()
        adjusted = out.get("adjusted_structured_workout") or {}
        trace = out.get("adjustment_trace") or []
        assert adjusted.get("schema") == "kairos-workout-v1"
        assert isinstance(trace, list) and trace

    def test_build_proactive_status_markdown_shows_block_feedback_summary(self):
        payload = {
            "plan_assigned": True,
            "plan_recommendation": "Plan activo",
            "plan_execution_feedback": {
                "adherence_score": 0.82,
                "adherence_label": "adherente",
                "load_deviation_pct": -0.12,
                "planned": {"structured_summary": "bloque principal: 5x3' + 2' rec (threshold)"},
                "block_feedback": {
                    "summary": {
                        "completed_blocks": 2,
                        "partial_blocks": 1,
                        "missed_blocks": 0,
                        "total_blocks": 3,
                        "time_deviation_pct": -0.10,
                    }
                },
            },
            "body_battery": {"summary": "sin datos"},
            "hrv": {"summary": "sin datos"},
            "sleep": {"summary": "sin datos"},
            "trainings": [],
        }
        out = _build_proactive_status_markdown(payload)
        assert "Bloques:" in out
        assert "2/3 completos" in out

    def test_compute_daily_plan_adjustment_respects_unavailable_day(self):
        today_iso = date.today().isoformat()
        today_idx = date.today().isoweekday()
        plan = {
            "title": "Plan general",
            "plan_data": {"constraints": {"unavailable_days": [today_idx]}},
            "sessions": [
                {
                    "week_index": 1,
                    "day_index": today_idx,
                    "session_type": "running_quality",
                    "duration_min": 60,
                    "intensity": "RPE 7-8",
                }
            ],
        }
        snapshot = {
            "dates": {"today": today_iso},
            "load_fatigue": {"status": "ready", "latest": {}, "weekly": {}, "ranges": {}},
            "body_battery": {"today": {"date": today_iso, "body_battery_level": 80}},
            "sleep": {"today": {"date": today_iso, "sleep_hours": 8.0, "sleep_score": 85}},
            "hrv": {"today": {"date": today_iso, "lastNightAvg": 55, "weeklyAvg": 50, "status": "balanced"}},
        }
        out = _compute_daily_plan_adjustment(snapshot, plan)
        assert out is not None
        assert out["decision"] == "rest"
        assert out["rule"] == "availability"

    @pytest.mark.asyncio
    async def test_collect_startup_snapshot_48h_collects_metrics(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )
        captured_calls: list[tuple[str, dict]] = []

        async def _fake_call_tool(_session, tool_name, _arguments):
            captured_calls.append((tool_name, dict(_arguments or {})))
            today = date.today().isoformat()
            if tool_name == "get_activities":
                return json.dumps({
                    "activities": [
                        {"activityId": 777, "name": "Rodaje", "startTimeLocal": f"{today}T07:30:00.0"}
                    ]
                })
            if tool_name == "get_activities_by_date":
                # Simula actividades históricas de 56 días para el modelo de carga
                from datetime import timedelta
                acts = []
                for i in range(40):
                    d = (date.today() - timedelta(days=i)).isoformat()
                    if i % 7 != 0:  # descanso 1 día/semana
                        acts.append({"activityId": 800 + i, "name": f"Trail {i}",
                                     "startTimeLocal": f"{d}T08:00:00.0",
                                     "trainingLoad": 55.0})
                return json.dumps({"activities": acts, "has_more": False})
            return json.dumps({"ok": True})

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()

        with patch_all("call_tool", side_effect=_fake_call_tool):
            snapshot = await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert snapshot["window_hours"] == 48
        assert snapshot["body_battery"]["summary"].startswith("hoy=")
        assert snapshot["trainings"]
        bb_calls = [args for name, args in captured_calls if name == "get_body_battery"]
        assert len(bb_calls) == 2
        assert all("start_date" in args and "end_date" in args for args in bb_calls)
        # get_activities_by_date debe haberse llamado con start_date y end_date
        hist_calls = [args for name, args in captured_calls if name == "get_activities_by_date"]
        assert hist_calls and all("start_date" in args and "end_date" in args for args in hist_calls)
        # Solo el rango corto de 48h para entrenamientos recientes: sin serie en load_metrics_daily no se
        # recalcula el TSS en vivo (27/10: antes pedía 56 días de actividades y daba un segundo TSS).
        assert all(args.get("page_size") == 100 for args in hist_calls)
        assert snapshot["load_fatigue"] == {}

    @pytest.mark.asyncio
    async def test_collect_startup_snapshot_refreshes_today_wellness_from_mcp(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )
        captured_calls: list[tuple[str, dict]] = []

        now_iso = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        today = date.today().isoformat()
        yday = (date.today() - timedelta(days=1)).isoformat()

        async def _fake_call_tool(_session, tool_name, _arguments):
            captured_calls.append((tool_name, dict(_arguments or {})))
            if tool_name == "get_body_battery":
                req_start = (_arguments or {}).get("start_date")
                if req_start == today:
                    return json.dumps([
                        {
                            "date": today,
                            "bodyBatteryLevel": 66,
                            "charged": 58,
                            "drained": 31,
                        }
                    ])
                return json.dumps([
                    {
                        "date": yday,
                        "bodyBatteryLevel": 61,
                        "charged": 61,
                        "drained": 58,
                    }
                ])
            if tool_name == "get_hrv_data":
                req_date = (_arguments or {}).get("date")
                if req_date == today:
                    return json.dumps({"date": today, "lastNightAvg": 42, "weeklyAvg": 48, "status": "balanced"})
                return json.dumps({"date": yday, "lastNightAvg": 74, "weeklyAvg": 73, "status": "balanced"})
            if tool_name == "get_activities_by_date":
                return json.dumps({
                    "activities": [
                        {"activityId": 901, "name": "Rodaje", "startTimeLocal": f"{today}T07:30:00.0", "trainingLoad": 50.0}
                    ],
                    "has_more": False,
                })
            if tool_name == "get_training_load_trend":
                return json.dumps({"dailyTrainingLoadAcute": []})
            return json.dumps({"ok": True})

        def _fake_get_wellness(metric_date: str):
            return {
                "metric_date": metric_date,
                "body_battery_level": 69,
                "bb_charged": 58,
                "bb_drained": 31,
                "hrv_last_night_avg_ms": 51,
                "hrv_weekly_avg_ms": 55,
                "hrv_status": "balanced",
                "pulled_at": now_iso,
            }

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()

        with patch_all("call_tool", side_effect=_fake_call_tool), \
             patch("agent.trainer_agent._storage.get_wellness_daily", side_effect=_fake_get_wellness), \
             patch("agent.trainer_agent._storage.upsert_wellness_daily") as upsert_mock:
            snapshot = await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert "nivel 66" in snapshot["body_battery"]["summary"]
        assert "42.0 ms" in snapshot["hrv"]["summary"]
        assert any(name == "get_body_battery" for name, _ in captured_calls)
        assert any(name == "get_hrv_data" for name, _ in captured_calls)
        assert snapshot.get("wellness_sources", {}).get("body_today") == "mcp"
        assert snapshot.get("wellness_sources", {}).get("hrv_today") == "mcp"
        upsert_mock.assert_called()

    @pytest.mark.asyncio
    async def test_collect_startup_snapshot_no_db_fallback_when_mcp_unavailable(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        async def _fake_call_tool(_session, tool_name, _arguments):
            if tool_name in {"get_body_battery", "get_hrv_data", "get_sleep_data"}:
                return json.dumps({"ok": True})
            if tool_name == "get_activities_by_date":
                return json.dumps({"activities": [], "has_more": False})
            if tool_name == "get_training_load_trend":
                return json.dumps({"dailyTrainingLoadAcute": []})
            return json.dumps({"ok": True})

        def _fake_get_wellness(_metric_date: str):
            return {
                "metric_date": date.today().isoformat(),
                "body_battery_level": 77,
                "bb_charged": 50,
                "bb_drained": 20,
                "hrv_last_night_avg_ms": 55,
                "hrv_weekly_avg_ms": 58,
                "hrv_status": "balanced",
            }

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()

        with patch_all("call_tool", side_effect=_fake_call_tool), \
             patch("agent.trainer_agent._storage.get_wellness_daily", side_effect=_fake_get_wellness):
            snapshot = await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert "nivel 77" not in snapshot["body_battery"]["summary"]
        assert "55.0 ms" not in snapshot["hrv"]["summary"]
        assert snapshot.get("wellness_sources", {}).get("body_today") == "mcp_unavailable"
        assert snapshot.get("wellness_sources", {}).get("hrv_today") == "mcp_unavailable"

        status_md = _build_proactive_status_markdown(snapshot)
        assert "No disponible en este momento" in status_md

    @pytest.mark.asyncio
    async def test_collect_startup_snapshot_fallback_when_by_date_unavailable(self):
        """Si get_activities_by_date lanza excepción, el snapshot sigue funcionando."""
        from agent.trainer_agent import (
            TrainerAgent,
        )

        async def _fake_call_tool(_session, tool_name, _arguments):
            today = date.today().isoformat()
            if tool_name == "get_activities":
                return json.dumps({
                    "activities": [
                        {"activityId": 1, "name": "Run", "startTimeLocal": f"{today}T08:00:00.0",
                         "trainingLoad": 50.0}
                    ]
                })
            if tool_name == "get_activities_by_date":
                raise RuntimeError("tool not available")
            return json.dumps({"ok": True})

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()

        with patch_all("call_tool", side_effect=_fake_call_tool):
            snapshot = await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert snapshot["window_hours"] == 48
        assert "load_fatigue" in snapshot

    @pytest.mark.asyncio
    async def test_collect_startup_snapshot_preserves_formula_version_in_profile_cache(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        async def _fake_call_tool(_session, tool_name, _arguments):
            today = date.today().isoformat()
            if tool_name == "get_activities":
                return json.dumps({
                    "activities": [
                        {"activityId": 1, "name": "Run", "startTimeLocal": f"{today}T08:00:00.0", "trainingLoad": 50.0}
                    ]
                })
            if tool_name == "get_activities_by_date":
                return json.dumps({
                    "activities": [
                        {"activityId": 2, "name": "Run", "startTimeLocal": f"{today}T09:00:00.0", "trainingLoad": 65.0}
                    ],
                    "has_more": False,
                })
            return json.dumps({"ok": True})

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {"primary": "running"},
            "load_metrics": {"formula_version": _data_access._TSS_FORMULA_VERSION},
        }

        with patch_all("call_tool", side_effect=_fake_call_tool), patch_all(
            "_save_user_profile"
        ):
            await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert (
            (agent.user_profile.get("load_metrics") or {}).get("formula_version")
            == _data_access._TSS_FORMULA_VERSION
        )

    @pytest.mark.asyncio
    async def test_build_startup_status_markdown_uses_training_plan_not_goals(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": "2026-11-22",
            }
        }

        with patch.object(TrainerAgent, "collect_startup_snapshot_48h", return_value={"body_battery": {}, "hrv": {}, "sleep": {}, "trainings": []}):
            out = await TrainerAgent.build_startup_status_markdown(agent)

        assert "No tienes plan asignado" in out


@pytest.fixture(autouse=True)
def _no_baseline_in_db():
    """Por defecto: activity_tss vacía y sin fila de semilla (los tests la sobrescriben si la necesitan)."""
    with patch("agent.trainer_agent._storage.get_activity_tss_map", return_value={}), \
         patch("agent.trainer_agent._storage.get_load_metrics_row", return_value=None):
        yield


class TestComputeAndPersistLoadMetrics:
    @pytest.mark.asyncio
    async def test_compute_persists_activity_tss_rows_matching_golden_controls(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()

        activities = [
            {
                "activityId": 24172582627,
                "type": {"typeKey": "trail_running"},
                "startTimeLocal": f"{today_iso}T07:00:00",
            },
            {
                "activityId": 24398763300,
                "type": {"typeKey": "running"},
                "startTimeLocal": f"{today_iso}T08:00:00",
                "activityTrainingLoad": 118.471,
            },
            {
                "activityId": 24227364808,
                "type": {"typeKey": "running"},
                "startTimeLocal": f"{today_iso}T09:00:00",
            },
            {
                "activityId": 24618238695,
                "type": {"typeKey": "strength_training"},
                "startTimeLocal": f"{today_iso}T10:00:00",
            },
            {
                "activityId": 24502427862,
                "type": {"typeKey": "walking"},
                "startTimeLocal": f"{today_iso}T11:00:00",
                "activityTrainingLoad": 17.073,
            },
            {
                "activityId": 24013969366,
                "type": {"typeKey": "hiking"},
                "startTimeLocal": f"{today_iso}T12:00:00",
                "activityTrainingLoad": 102.75,
            },
            {
                "activityId": 23950882994,
                "type": {"typeKey": "gravel_cycling"},
                "startTimeLocal": f"{today_iso}T13:00:00",
            },
            {
                "activityId": 24383422318,
                "type": {"typeKey": "elliptical"},
                "startTimeLocal": f"{today_iso}T14:00:00",
            },
        ]
        expected = {
            24172582627: (228.266, "hrTSS", "kairos_model", "trail:lthr_details", None),
            24398763300: (118.471, "TSS", "garmin_native", "garmin:native_load", 118.471),
            24227364808: (91.996, "TSS", "kairos_model", "running:tp_like_adjusted", None),
            24618238695: (16.597, "hrTSS", "kairos_model", "strength:modelo_B", None),
            24502427862: (17.073, "TSS", "kairos_model", "walk_hike:metabolico", 17.073),
            24013969366: (102.75, "TSS", "kairos_model", "walk_hike:metabolico", 102.75),
            23950882994: (50.825, "TSS", "kairos_model", "cycling:power_ftp", None),
            24383422318: (23.772, "hrTSS", "kairos_model", "gym_cardio:lthr", None),
        }

        async def _fake_fetch(_session, _start_date, _end_date):
            return activities

        def _fake_estimate(act, **_kwargs):
            aid = int(act.get("activityId") or 0)
            tss, label, source, model, _native_load = expected[aid]
            act["_kairos_model_tag"] = model
            act["_kairos_used_native_tss"] = source == "garmin_native"
            if model.startswith("gym_cardio:"):
                act["_kairos_tss_source_tag"] = model
            return tss, label

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {"primary": "running"},
            "performance": {"running_threshold_pace_sec_per_km": 252.0},
        }

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[[], []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=None), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series"), \
             patch("agent.trainer_agent._storage.upsert_activity_tss_rows") as upsert_activity_tss_mock, \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch_all("_estimate_session_tss", side_effect=_fake_estimate), \
             patch_all("_hydrate_profile_hr_anchors_if_missing", new=AsyncMock(return_value=(37.0, None, 169.0))), \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=205.0)), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=True)

        upsert_activity_tss_mock.assert_called_once()
        persisted_rows = upsert_activity_tss_mock.call_args[0][0]
        by_id = {int(r["activity_id"]): r for r in persisted_rows}
        assert len(by_id) == 8
        type_key_by_id = {int(a["activityId"]): a["type"]["typeKey"] for a in activities}
        for aid, (tss, label, source, model, native_load) in expected.items():
            assert aid in by_id
            assert by_id[aid]["sport"] == type_key_by_id[aid]
            assert by_id[aid]["start_date"] == today_iso
            assert by_id[aid]["method"] == label
            assert by_id[aid]["tss_source"] == source
            assert by_id[aid]["model"] == model
            if native_load is None:
                assert by_id[aid]["native_load"] is None
            else:
                assert by_id[aid]["native_load"] == pytest.approx(native_load, abs=0.001)
            assert by_id[aid]["tss"] == pytest.approx(tss, abs=0.001)

    @pytest.mark.asyncio
    async def test_compute_continues_when_activity_tss_persistence_fails(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()

        async def _fake_fetch(_session, _start_date, _end_date):
            return [
                {
                    "activityId": 123,
                    "type": {"typeKey": "running"},
                    "startTimeLocal": f"{today_iso}T07:00:00",
                }
            ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {"primary": "running"},
            "performance": {"running_threshold_pace_sec_per_km": 252.0},
        }

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[[], []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=None), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_daily_mock, \
             patch("agent.trainer_agent._storage.upsert_activity_tss_rows", side_effect=RuntimeError("boom")), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch_all("_estimate_session_tss", return_value=(50.0, "TSS")), \
             patch_all("_infer_tss_source_tag", return_value="kairos_model"), \
             patch_all("_hydrate_profile_hr_anchors_if_missing", new=AsyncMock(return_value=(37.0, None, 169.0))), \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=205.0)), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=True)

        upsert_daily_mock.assert_called_once()

    async def _run_compute_capturing_details(self, activities, details_by_id):
        """Ejecuta compute_and_persist_load_metrics y devuelve {activity_id: activity_details_raw pasado}."""
        from agent.trainer_agent import (
            TrainerAgent,
        )

        async def _fake_fetch(_session, _start_date, _end_date):
            return activities

        async def _fake_call_tool(_session, tool_name, args):
            if tool_name == "get_activity_details":
                return details_by_id.get(int(args["activity_id"]), "")
            return ""

        passed: dict[int, str | None] = {}

        def _fake_estimate(act, **kwargs):
            passed[int(act.get("activityId") or 0)] = kwargs.get("activity_details_raw")
            return 50.0, "TSS"

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {"primary": "running"},
            "performance": {"running_threshold_pace_sec_per_km": 252.0},
        }

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[[], []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=None), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series"), \
             patch("agent.trainer_agent._storage.upsert_activity_tss_rows"), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch_all("_estimate_session_tss", side_effect=_fake_estimate), \
             patch_all("_hydrate_profile_hr_anchors_if_missing", new=AsyncMock(return_value=(37.0, None, 169.0))), \
             patch_all("call_tool", new=AsyncMock(side_effect=_fake_call_tool)) as call_mock, \
             patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=205.0)), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=True)

        details_calls = [
            int(c.args[2]["activity_id"]) for c in call_mock.await_args_list if c.args[1] == "get_activity_details"
        ]
        return passed, details_calls

    async def _run_compute(
        self,
        activities,
        *,
        stored=None,
        seed=None,
        rhr_by_day=None,
        rebuild_from=None,
        profile_perf=None,
    ):
        """Ejecuta compute_and_persist_load_metrics y captura lecturas/escrituras."""
        from agent.trainer_agent import (
            TrainerAgent,
        )

        async def _fake_fetch(_session, _start_date, _end_date):
            return [dict(a) for a in activities]

        async def _fake_call_tool(_session, tool_name, args):
            if tool_name == "get_rhr_day":
                value = (rhr_by_day or {}).get(args["date"])
                return json.dumps({"restingHeartRate": value}) if value is not None else ""
            if tool_name == "get_activity_details":
                return '{"activityId": %d}' % int(args["activity_id"])
            return ""

        estimate_calls: list[dict] = []

        def _fake_estimate(act, **kwargs):
            estimate_calls.append({"activity_id": int(act.get("activityId") or 0), **kwargs})
            return 50.0, "TSS"

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {"primary": "running"},
            "performance": {"running_threshold_pace_sec_per_km": 252.0, **(profile_perf or {})},
        }
        stored_mock = MagicMock(return_value=stored if stored is not None else {})

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[[], []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=None), \
             patch("agent.trainer_agent._storage.get_activity_tss_map", stored_mock), \
             patch("agent.trainer_agent._storage.get_load_metrics_row", return_value=seed), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_series_mock, \
             patch("agent.trainer_agent._storage.upsert_activity_tss_rows") as upsert_rows_mock, \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch_all("_estimate_session_tss", side_effect=_fake_estimate), \
             patch_all("_hydrate_profile_hr_anchors_if_missing", new=AsyncMock(return_value=(37.0, None, 169.0))), \
             patch_all("call_tool", new=AsyncMock(side_effect=_fake_call_tool)) as call_mock, \
             patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=205.0)), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(
                agent, force_full_recalc=True, rebuild_from=rebuild_from
            )

        series = upsert_series_mock.call_args[0][0] if upsert_series_mock.called else None
        rows = upsert_rows_mock.call_args[0][0] if upsert_rows_mock.called else []
        tool_calls = [(c.args[1], c.args[2]) for c in call_mock.await_args_list]
        return {
            "estimate_calls": estimate_calls,
            "series": series,
            "rows": rows,
            "tool_calls": tool_calls,
            "stored_mock": stored_mock,
            "profile": agent.user_profile,
        }

    @pytest.mark.asyncio
    async def test_stored_activity_tss_is_reused_not_recomputed(self):
        today_iso = date.today().isoformat()
        out = await self._run_compute(
            [
                {"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T07:00:00"},
                {"activityId": 2, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T19:00:00"},
            ],
            stored={1: {"activity_id": 1, "start_date": today_iso, "tss": 80.0}},
        )
        assert [c["activity_id"] for c in out["estimate_calls"]] == [2]
        assert [int(r["activity_id"]) for r in out["rows"]] == [2]
        assert all(args.get("activity_id") != 1 for name, args in out["tool_calls"] if name == "get_activity_details")
        today_row = next(r for r in out["series"] if r["date"] == today_iso)
        assert today_row["tss"] == pytest.approx(130.0)
        assert today_row["activities_count"] == 2

    @pytest.mark.asyncio
    async def test_threshold_change_applies_only_from_its_date(self):
        # Caso real (22/10): ritmo umbral 4:12 → 4:00 desde hoy. Lo ya guardado no se toca;
        # cada actividad nueva se calcula con el umbral vigente en su fecha.
        today = date.today()
        yday = (today - timedelta(days=1)).isoformat()
        history = [
            {"date": (today - timedelta(days=30)).isoformat(), "sec_per_km": 252},
            {"date": today.isoformat(), "sec_per_km": 240.0},
        ]
        out = await self._run_compute(
            [
                {"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{yday}T07:00:00"},
                {"activityId": 3, "type": {"typeKey": "running"}, "startTimeLocal": f"{yday}T19:00:00"},
                {"activityId": 2, "type": {"typeKey": "running"}, "startTimeLocal": f"{today.isoformat()}T07:00:00"},
            ],
            stored={1: {"activity_id": 1, "start_date": yday, "tss": 80.0}},
            profile_perf={"running_threshold_pace_sec_per_km": 240.0, "running_threshold_pace_history": history},
        )
        paces = {c["activity_id"]: c["running_threshold_pace_sec_per_km"] for c in out["estimate_calls"]}
        assert paces == {3: 252.0, 2: 240.0}   # la guardada (1) no se recalcula
        yday_row = next(r for r in out["series"] if r["date"] == yday)
        assert yday_row["tss"] == pytest.approx(130.0)  # 80 guardado + 50 nuevo

    @pytest.mark.asyncio
    async def test_unreadable_activity_tss_does_not_touch_history(self):
        out_none = await self._run_compute_with_stored_none(date.today().isoformat())
        assert out_none["estimate_calls"] == []
        assert out_none["rows"] == []
        assert out_none["series"] is None

    async def _run_compute_with_stored_none(self, today_iso):
        with patch("agent.trainer_agent._storage.get_activity_tss_map", return_value=None):
            from agent.trainer_agent import (
                TrainerAgent,
            )

            agent = object.__new__(TrainerAgent)
            agent.mcp_session = MagicMock()
            agent.user_profile = {"performance": {}}
            estimate_mock = MagicMock(return_value=(50.0, "TSS"))
            with patch("agent.trainer_agent._storage.get_load_metrics_series", return_value=[]), \
                 patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=None), \
                 patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_series_mock, \
                 patch("agent.trainer_agent._storage.upsert_activity_tss_rows") as upsert_rows_mock, \
                 patch_all("_fetch_activities_for_load_calc", new=AsyncMock(return_value=[
                     {"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T07:00:00"}
                 ])), \
                 patch_all("_estimate_session_tss", estimate_mock), \
                 patch_all("call_tool", new=AsyncMock(return_value="")), \
                 patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=None)), \
                 patch.object(TrainerAgent, "_apply_series_to_profile"):
                await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=True)
            return {
                "estimate_calls": estimate_mock.call_args_list,
                "rows": upsert_rows_mock.call_args[0][0] if upsert_rows_mock.called else [],
                "series": upsert_series_mock.call_args[0][0] if upsert_series_mock.called else None,
            }

    @pytest.mark.asyncio
    async def test_hr_rest_is_mean_of_14_days_before_activity(self):
        today = date.today()
        rhr = {(today - timedelta(days=i)).isoformat(): 40.0 + (i % 2) * 2.0 for i in range(1, 15)}
        rhr[today.isoformat()] = 99.0  # el propio día no cuenta
        out = await self._run_compute(
            [{"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today.isoformat()}T07:00:00"}],
            rhr_by_day=rhr,
        )
        assert out["estimate_calls"][0]["hr_rest_bpm"] == pytest.approx(41.0)
        assert out["rows"][0]["hr_rest_used"] == pytest.approx(41.0)

    @pytest.mark.asyncio
    async def test_hr_rest_falls_back_to_profile_without_rhr_history(self):
        today_iso = date.today().isoformat()
        out = await self._run_compute(
            [{"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T07:00:00"}],
        )
        assert out["estimate_calls"][0]["hr_rest_bpm"] == pytest.approx(37.0)

    @pytest.mark.asyncio
    async def test_ewma_is_seeded_from_previous_day_row(self):
        out = await self._run_compute([], seed={"date": "x", "atl": 70.0, "ctl": 60.0, "tsb": -10.0, "tss": 0.0})
        first = out["series"][0]
        assert first["tss"] == 0.0
        assert first["atl"] == pytest.approx(70.0 - 70.0 / 7.0, abs=0.01)
        assert first["ctl"] == pytest.approx(60.0 - 60.0 / 42.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_rebuild_ignores_stored_tss_and_seeds_zero(self):
        start = (date.today() - timedelta(days=3)).isoformat()
        out = await self._run_compute(
            [{"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{start}T07:00:00"}],
            stored={1: {"activity_id": 1, "start_date": start, "tss": 80.0}},
            seed={"date": "x", "atl": 70.0, "ctl": 60.0, "tsb": -10.0, "tss": 0.0},
            rebuild_from=start,
        )
        out["stored_mock"].assert_not_called()
        assert [c["activity_id"] for c in out["estimate_calls"]] == [1]
        first = out["series"][0]
        assert first["date"] == start
        assert first["ctl"] == pytest.approx(50.0 / 42.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_walk_threshold_uses_profile_anchor_outside_rebuild(self):
        today_iso = date.today().isoformat()
        out = await self._run_compute(
            [{"activityId": 1, "type": {"typeKey": "walking"}, "startTimeLocal": f"{today_iso}T07:00:00"}],
            profile_perf={"walk_hike_threshold_speed_m_min": 101.5},
        )
        assert out["rows"][0]["walk_threshold_used"] == pytest.approx(101.5)

    @pytest.mark.asyncio
    async def test_compute_passes_activity_details_to_running_non_trail(self):
        today_iso = date.today().isoformat()
        details = '{"activityId": 1, "metricDescriptors": []}'
        passed, details_calls = await self._run_compute_capturing_details(
            [
                {"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T07:00:00"},
                {"activityId": 2, "type": {"typeKey": "strength_training"}, "startTimeLocal": f"{today_iso}T18:00:00"},
            ],
            {1: details, 2: '{"activityId": 2}'},
        )
        assert passed[1] == details
        assert 2 not in details_calls
        assert passed[2] is None

    @pytest.mark.asyncio
    async def test_compute_ignores_error_text_as_activity_details(self):
        today_iso = date.today().isoformat()
        passed, details_calls = await self._run_compute_capturing_details(
            [{"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T07:00:00"}],
            {1: "Error executing tool get_activity_details: 429 Too Many Requests"},
        )
        assert details_calls == [1]
        assert passed[1] is None

    @pytest.mark.asyncio
    async def test_force_full_recalc_fetches_full_window_for_new_user(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        expected_start = (today - timedelta(days=120)).isoformat()
        captured_fetch = {}

        async def _fake_fetch(_session, start_date, end_date):
            captured_fetch["start_date"] = start_date
            captured_fetch["end_date"] = end_date
            return []

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {}

        existing_series = [
            {
                "date": today.isoformat(),
                "tss": 45.0,
                "atl": 40.0,
                "ctl": 30.0,
                "tsb": -10.0,
                "activities_count": 1,
            }
        ]

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today.isoformat()), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch.object(TrainerAgent, "_apply_series_to_profile") as apply_mock:
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=True)

        assert captured_fetch["start_date"] == expected_start
        assert captured_fetch["end_date"] == today.isoformat()
        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows, "Debe persistir la serie completa del rango histórico"
        assert persisted_rows[0]["date"] == expected_start
        assert persisted_rows[-1]["date"] == today.isoformat()
        apply_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_existing_user_up_to_date_keeps_incremental_path(self):
        import agent.trainer_agent as trainer_mod
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        existing_series = [
            {
                "date": today.isoformat(),
                "tss": 55.0,
                "atl": 48.0,
                "ctl": 36.0,
                "tsb": -12.0,
                "activities_count": 1,
            }
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "load_metrics": {
                "formula_version": trainer_mod._TSS_FORMULA_VERSION,
            }
        }

        with patch("agent.trainer_agent._storage.get_load_metrics_series", return_value=existing_series), \
                 patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today.isoformat()), \
                 patch_all("_fetch_activities_for_load_calc", new=AsyncMock(return_value=[])) as fetch_mock, \
                 patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
                 patch.object(TrainerAgent, "_apply_series_to_profile") as apply_mock:
                await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert fetch_mock.await_count >= 1
        assert any(
            c.args[1] == today.isoformat() and c.args[2] == today.isoformat()
            for c in fetch_mock.await_args_list
        )
        upsert_mock.assert_not_called()
        apply_mock.assert_called_once_with(existing_series, today)

    @pytest.mark.asyncio
    async def test_existing_user_up_to_date_refreshes_today_when_new_activity_detected(self):
        import agent.trainer_agent as trainer_mod
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        existing_series = [
            {
                "date": today_iso,
                "tss": 0.0,
                "atl": 48.0,
                "ctl": 36.0,
                "tsb": -12.0,
                "activities_count": 0,
            }
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {"load_metrics": {"formula_version": trainer_mod._TSS_FORMULA_VERSION}}

        async def _fake_fetch(_session, start_date, end_date):
            if start_date == today_iso and end_date == today_iso:
                return [{"activityId": 123, "startTimeLocal": f"{today_iso}T07:10:00", "trainingLoad": 82.49, "type": "running"}]
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile") as apply_mock:
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert fetch_mock.await_count >= 2
        assert any(
            c.args[1] == today_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows
        assert persisted_rows[0]["date"] == today_iso
        apply_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_formula_change_full_recalc_bypasses_effective_date_clamp(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        expected_start = (today - timedelta(days=120)).isoformat()
        existing_series = [
            {
                "date": today_iso,
                "tss": 10.0,
                "atl": 20.0,
                "ctl": 30.0,
                "tsb": 10.0,
                "activities_count": 1,
            }
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        # Fuerza formula_changed y además effective_date=today para reproducir el bug.
        agent.user_profile = {
            "load_metrics": {"formula_version": 0},
            "performance": {"performance_params_updated_at": today_iso},
        }

        captured = {}

        async def _fake_fetch(_session, start_date, end_date):
            captured["start_date"] = start_date
            captured["end_date"] = end_date
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert captured["start_date"] == expected_start
        assert captured["end_date"] == today_iso
        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows
        assert persisted_rows[0]["date"] == expected_start

    @pytest.mark.asyncio
    async def test_existing_user_up_to_date_refreshes_from_yesterday_when_missing_activity(self):
        import agent.trainer_agent as trainer_mod
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        two_days_ago_iso = (today - timedelta(days=2)).isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()
        existing_series = [
            {
                "date": two_days_ago_iso,
                "tss": 32.0,
                "atl": 42.0,
                "ctl": 33.0,
                "tsb": -9.0,
                "activities_count": 1,
            },
            {
                "date": yesterday_iso,
                "tss": 0.0,
                "atl": 44.0,
                "ctl": 34.0,
                "tsb": -10.0,
                "activities_count": 0,
            },
            {
                "date": today_iso,
                "tss": 0.0,
                "atl": 46.0,
                "ctl": 35.0,
                "tsb": -11.0,
                "activities_count": 0,
            },
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {"load_metrics": {"formula_version": trainer_mod._TSS_FORMULA_VERSION}}

        async def _fake_fetch(_session, start_date, end_date):
            if start_date == today_iso and end_date == today_iso:
                return []
            if start_date == two_days_ago_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 321,
                        "startTimeLocal": f"{yesterday_iso}T07:10:00",
                        "trainingLoad": 67.2,
                        "type": "hiking",
                        "duration": 5400,
                        "averageHR": 128,
                        "maxHR": 162,
                    }
                ]
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile") as apply_mock:
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert any(
            c.args[1] == two_days_ago_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        assert any(
            c.args[1] == yesterday_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows
        assert persisted_rows[0]["date"] == yesterday_iso
        apply_mock.assert_called_once()


class TestActivityAnalysisMarkdownFormatting:
    def test_format_activity_analysis_for_markdown_adds_sections_and_bullets(self):
        raw = "\n".join([
            "=== RESUMEN DE ACTIVIDAD (calculado) ===",
            "Nombre: Sesion test",
            "Deporte: Senderismo",
            "hrTSS: 79.4",
            "",
            "=== ZONAS DE FRECUENCIA CARDIACA (datos reales Garmin — Tiempo en Zonas) ===",
            "Z1 · Calentamiento · >46 bpm 100.0% (~119 min)",
        ])

        out = _format_activity_analysis_for_markdown(raw)

        assert "### RESUMEN DE ACTIVIDAD (calculado)" in out
        assert "- Nombre: Sesion test" in out
        assert "- Deporte: Senderismo" in out
        assert "- hrTSS: 79.4" in out
        assert "### ZONAS DE FRECUENCIA CARDIACA (datos reales Garmin — Tiempo en Zonas)" in out
        assert "- Z1 · Calentamiento · >46 bpm 100.0% (~119 min)" in out

    @pytest.mark.asyncio
    async def test_recent_refresh_from_yesterday_bypasses_effective_date_clamp(self):
        import agent.trainer_agent as trainer_mod
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()
        existing_series = [
            {
                "date": yesterday_iso,
                "tss": 0.0,
                "atl": 44.0,
                "ctl": 34.0,
                "tsb": -10.0,
                "activities_count": 0,
            },
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "load_metrics": {"formula_version": trainer_mod._TSS_FORMULA_VERSION},
            "performance": {"performance_params_updated_at": today_iso},
        }

        async def _fake_fetch(_session, start_date, end_date):
            if start_date == today_iso and end_date == today_iso:
                return []
            if start_date == yesterday_iso and end_date == yesterday_iso:
                return [
                    {
                        "activityId": 999,
                        "startTimeLocal": f"{yesterday_iso}T09:00:00",
                        "trainingLoad": 58.5,
                        "type": "running",
                        "duration": 3600,
                        "averageHR": 140,
                        "maxHR": 170,
                    },
                    {
                        "activityId": 1001,
                        "startTimeLocal": f"{yesterday_iso}T18:20:00",
                        "trainingLoad": 21.5,
                        "type": "running",
                        "duration": 1800,
                        "averageHR": 138,
                        "maxHR": 168,
                    },
                ]
            if start_date == yesterday_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 999,
                        "startTimeLocal": f"{yesterday_iso}T09:00:00",
                        "trainingLoad": 58.5,
                        "type": "running",
                        "duration": 3600,
                        "averageHR": 140,
                        "maxHR": 170,
                    },
                    {
                        "activityId": 1001,
                        "startTimeLocal": f"{yesterday_iso}T18:20:00",
                        "trainingLoad": 21.5,
                        "type": "running",
                        "duration": 1800,
                        "averageHR": 138,
                        "maxHR": 168,
                    },
                ]
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows
        # Debe iniciar en ayer, no en hoy, aunque performance_params_updated_at == hoy.
        assert persisted_rows[0]["date"] == yesterday_iso

    @pytest.mark.asyncio
    async def test_up_to_date_checks_from_last_trained_day_not_fixed_recent_window(self):
        import agent.trainer_agent as trainer_mod
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        fourteen_days_ago_iso = (today - timedelta(days=14)).isoformat()
        existing_series = [
            {
                "date": fourteen_days_ago_iso,
                "tss": 60.0,
                "atl": 40.0,
                "ctl": 32.0,
                "tsb": -8.0,
                "activities_count": 1,
            },
            {
                "date": today_iso,
                "tss": 0.0,
                "atl": 38.0,
                "ctl": 31.0,
                "tsb": -7.0,
                "activities_count": 0,
            },
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "load_metrics": {"formula_version": trainer_mod._TSS_FORMULA_VERSION}
        }

        async def _fake_fetch(_session, start_date, end_date):
            if start_date == fourteen_days_ago_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 777,
                        "startTimeLocal": f"{today_iso}T07:10:00",
                        "trainingLoad": 72.0,
                        "type": "running",
                        "duration": 3600,
                        "averageHR": 142,
                        "maxHR": 172,
                    }
                ]
            if start_date == today_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 777,
                        "startTimeLocal": f"{today_iso}T07:10:00",
                        "trainingLoad": 72.0,
                        "type": "running",
                        "duration": 3600,
                        "averageHR": 142,
                        "maxHR": 172,
                    }
                ]
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert any(
            c.args[1] == fourteen_days_ago_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        upsert_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_existing_user_up_to_date_refreshes_from_two_days_ago_when_missing_activity(self):
        import agent.trainer_agent as trainer_mod
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        three_days_ago_iso = (today - timedelta(days=3)).isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()
        two_days_ago_iso = (today - timedelta(days=2)).isoformat()
        existing_series = [
            {
                "date": three_days_ago_iso,
                "tss": 18.0,
                "atl": 40.0,
                "ctl": 32.0,
                "tsb": -8.0,
                "activities_count": 1,
            },
            {
                "date": two_days_ago_iso,
                "tss": 0.0,
                "atl": 42.0,
                "ctl": 33.0,
                "tsb": -9.0,
                "activities_count": 0,
            },
            {
                "date": yesterday_iso,
                "tss": 0.0,
                "atl": 43.5,
                "ctl": 33.8,
                "tsb": -9.7,
                "activities_count": 0,
            },
            {
                "date": today_iso,
                "tss": 0.0,
                "atl": 41.0,
                "ctl": 33.5,
                "tsb": -7.5,
                "activities_count": 0,
            },
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "load_metrics": {"formula_version": trainer_mod._TSS_FORMULA_VERSION},
            "performance": {"performance_params_updated_at": today_iso},
        }

        async def _fake_fetch(_session, start_date, end_date):
            if start_date == today_iso and end_date == today_iso:
                return []
            if start_date == three_days_ago_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 888,
                        "startTimeLocal": f"{two_days_ago_iso}T07:10:00",
                        "trainingLoad": 61.4,
                        "type": "hiking",
                        "duration": 5400,
                        "averageHR": 126,
                        "maxHR": 160,
                    }
                ]
            if start_date == two_days_ago_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 888,
                        "startTimeLocal": f"{two_days_ago_iso}T07:10:00",
                        "trainingLoad": 61.4,
                        "type": "hiking",
                        "duration": 5400,
                        "averageHR": 126,
                        "maxHR": 160,
                    }
                ]
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert any(
            c.args[1] == three_days_ago_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        assert any(
            c.args[1] == two_days_ago_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows
        assert persisted_rows[0]["date"] == two_days_ago_iso


class TestFetchActivitiesForLoadCalc:
    @pytest.mark.asyncio
    async def test_fetch_activities_does_not_stop_when_first_page_is_older(self):
        from datetime import date as _date

        today = _date.today()
        today_iso = today.isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()
        old_iso = (today - timedelta(days=30)).isoformat()

        first_page = json.dumps(
            {
                "activities": [
                    {"activityId": 1, "startTimeLocal": f"{old_iso}T08:00:00"},
                ],
                "has_more": True,
                "next_start": 100,
            }
        )
        second_page = json.dumps(
            {
                "activities": [
                    {"activityId": 2, "startTimeLocal": f"{yesterday_iso}T07:00:00", "trainingLoad": 42.0},
                ],
                "has_more": False,
                "next_start": 200,
            }
        )

        session = MagicMock()
        with patch_all("call_tool", new=AsyncMock(side_effect=[first_page, second_page])):
            out = await _fetch_activities_for_load_calc(session, yesterday_iso, today_iso)

        assert len(out) == 1
        assert int(out[0].get("activityId") or 0) == 2

    @pytest.mark.asyncio
    async def test_fetch_activities_parses_concatenated_json_objects(self):
        from datetime import date as _date

        today = _date.today()
        today_iso = today.isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()

        raw_concat = "\n".join(
            [
                json.dumps(
                    {
                        "activityId": 10,
                        "startTimeLocal": f"{today_iso}T07:00:00",
                        "trainingLoad": 50.0,
                    }
                ),
                json.dumps(
                    {
                        "activityId": 11,
                        "startTimeLocal": f"{yesterday_iso}T08:00:00",
                        "trainingLoad": 40.0,
                    }
                ),
            ]
        )

        session = MagicMock()
        with patch_all("call_tool", new=AsyncMock(return_value=raw_concat)):
            out = await _fetch_activities_for_load_calc(session, yesterday_iso, today_iso)

        ids = {int(a.get("activityId") or 0) for a in out}
        assert ids == {10, 11}

    @pytest.mark.asyncio
    async def test_fetch_activities_paginates_full_plain_list_pages(self):
        from datetime import date as _date

        today = _date.today()
        start_iso = (today - timedelta(days=300)).isoformat()

        def _page(first_id, first_day_back):
            return json.dumps([
                {"activityId": first_id + i, "startTimeLocal": f"{(today - timedelta(days=first_day_back + i)).isoformat()}T07:00:00"}
                for i in range(100)
            ])

        pages = [_page(1, 0), _page(101, 100), json.dumps([
            {"activityId": 201, "startTimeLocal": f"{(today - timedelta(days=250)).isoformat()}T07:00:00"},
        ])]
        session = MagicMock()
        call_mock = AsyncMock(side_effect=pages)
        with patch_all("call_tool", new=call_mock):
            out = await _fetch_activities_for_load_calc(session, start_iso, today.isoformat())

        assert len(out) == 201
        starts = [c.args[2]["start"] for c in call_mock.await_args_list]
        assert starts == ["0", "100", "200"]

    @pytest.mark.asyncio
    async def test_fetch_activities_stops_when_whole_page_is_older_than_range(self):
        from datetime import date as _date

        today = _date.today()
        start_iso = (today - timedelta(days=50)).isoformat()
        recent = json.dumps([
            {"activityId": i, "startTimeLocal": f"{(today - timedelta(days=i // 2)).isoformat()}T07:00:00"}
            for i in range(100)
        ])
        older = json.dumps([
            {"activityId": 1000 + i, "startTimeLocal": f"{(today - timedelta(days=60 + i)).isoformat()}T07:00:00"}
            for i in range(100)
        ])
        session = MagicMock()
        call_mock = AsyncMock(side_effect=[recent, older, older])
        with patch_all("call_tool", new=call_mock):
            out = await _fetch_activities_for_load_calc(session, start_iso, today.isoformat())

        assert len(out) == 100
        assert call_mock.await_count == 2


# ─── Fallback de planificacion y rangos trend ─────────────────────────────

class TestPlanningFallbackAndRanges:
    def test_normalize_trend_date_range_clamps_future_end_date(self):
        out = _normalize_trend_date_range(
            "get_training_load_trend",
            {"start_date": "2026-07-07", "end_date": "2099-01-01"},
        )
        assert "start_date" in out and "end_date" in out
        assert out["end_date"] <= date.today().isoformat()

    def test_normalize_trend_date_range_enforces_max_window(self):
        out = _normalize_trend_date_range(
            "get_hrv_trend",
            {"start_date": "2020-01-01", "end_date": date.today().isoformat()},
        )
        s = date.fromisoformat(out["start_date"])
        e = date.fromisoformat(out["end_date"])
        assert (e - s).days <= 30

    def test_generic_needs_more_info_detection(self):
        txt = "Lo siento, pero no puedo crear una planificación para tu objetivo sin más información"
        assert _is_generic_needs_more_info_reply(txt)

    def test_generic_needs_more_info_detection_insufficient_info_phrase(self):
        txt = "Lo siento, pero no tengo suficiente información para proponerte un entrenamiento para mañana."
        assert _is_generic_needs_more_info_reply(txt)

    def test_planning_intent_detection_true(self):
        assert _is_planning_intent("¿puedes crearme una planificación para mi objetivo?")

    def test_planning_intent_detection_false_for_activity_analysis(self):
        assert not _is_planning_intent("Analiza mi entrenamiento del día 2 de julio de 2026")

    # ── Punto D: 'semana' en consultas de stats no activa planning intent ─────

    def test_planning_intent_false_for_weekly_stats_query(self):
        """'cuántos km he corrido esta semana' NO es planning intent."""
        assert not _is_planning_intent("cuántos km he corrido esta semana")

    def test_planning_intent_false_for_weekly_steps_query(self):
        assert not _is_planning_intent("¿cuántos pasos llevo esta semana?")

    def test_planning_intent_true_for_plan_creation(self):
        assert _is_planning_intent("Crea un plan para prepararme para la ultra")

    def test_planning_intent_true_for_planificacion_keyword(self):
        assert _is_planning_intent("Planifícame la semana próxima")

    def test_planning_intent_true_for_microciclo(self):
        assert _is_planning_intent("¿cómo planteo el microciclo esta semana?")

    def test_planning_intent_true_for_affirmative_followup_to_plan_offer(self):
        history = [
            {
                "role": "assistant",
                "content": "## ✅ Siguiente paso\nSi quieres, te preparo un plan activo a partir de ese objetivo.",
            }
        ]
        assert _is_planning_intent("Sí", history)

    def test_planning_intent_false_for_affirmative_without_plan_offer(self):
        history = [{"role": "assistant", "content": "¿Cómo te encuentras hoy?"}]
        assert not _is_planning_intent("Sí", history)

    def test_plan_status_intent_true_for_have_plan_question(self):
        assert _is_plan_status_intent("Tengo algun plan asignado?")

    def test_plan_status_intent_false_for_plan_creation_request(self):
        assert not _is_plan_status_intent("Puedes planificarme la semana?")

    def test_plan_status_intent_false_for_plan_adjustment_request(self):
        assert not _is_plan_status_intent("Ajusta mi plan de esta semana")

    def test_goal_status_intent_true_for_next_goal_question(self):
        assert _is_goal_status_intent("¿Cuál es mi próximo objetivo?")

    def test_goal_status_intent_false_for_plan_creation_request(self):
        assert not _is_goal_status_intent("Planifícame un objetivo para octubre")

    def test_has_goal_in_profile(self):
        profile = {"goals": {"target_race": "10k", "target_race_date": "2026-11-22"}}
        assert _has_goal_in_profile(profile)

    def test_get_active_training_plan_requires_plan_entity(self):
        profile = {"goals": {"target_race": "10k"}}
        assert _get_active_training_plan(profile) is None

    def test_get_active_training_plan_detects_active(self):
        profile = {"training_plan": {"active": True, "title": "Plan 10K", "status": "active"}}
        plan = _get_active_training_plan(profile)
        assert plan is not None
        assert plan["title"] == "Plan 10K"

    def test_get_active_training_plan_prefers_storage_source_of_truth(self):
        profile = {"training_plan": {"active": True, "title": "Plan local", "status": "active"}}
        with patch("agent.trainer_agent._storage.get_active_training_plan", return_value={
            "id": "plan-db-1",
            "title": "Plan DB",
            "status": "active",
            "source": "agent",
            "plan_data": {"target_race": "10K"},
        }), patch("agent.trainer_agent._storage.list_training_plan_sessions", return_value=[]):
            plan = _get_active_training_plan(profile)

        assert plan is not None
        assert plan["title"] == "Plan DB"
        assert plan["id"] == "plan-db-1"


class TestCyclingFtpResolution:
    def test_extract_cycling_ftp_watts_accepts_common_shapes(self):
        assert _extract_cycling_ftp_watts({"cyclingFtp": 261}) == 261.0
        assert _extract_cycling_ftp_watts({"ftp": "259"}) == 259.0
        assert _extract_cycling_ftp_watts({"data": {"functionalThresholdPower": 255}}) == 255.0
        assert _extract_cycling_ftp_watts({"functional_threshold_power_watts": 205}) == 205.0
        assert _extract_cycling_ftp_watts([{"ignored": 1}, {"functional_threshold_power": 250}]) == 250.0

    @pytest.mark.asyncio
    async def test_get_or_refresh_cycling_ftp_fetches_and_persists_when_missing(self):
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.user_profile = {"performance": {}}
        agent.mcp_session = object()

        with patch_all("call_tool", new=AsyncMock(return_value='{"data":{"cyclingFtp":266}}')) as call_mock, \
             patch_all("_save_user_profile") as save_mock:
            ftp = await TrainerAgent._get_or_refresh_cycling_ftp(agent)

        assert ftp == 266.0
        assert agent.user_profile["performance"]["cycling_ftp"] == 266.0
        call_mock.assert_awaited_once()
        save_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_refresh_cycling_ftp_uses_cache_without_mcp_call(self):
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.user_profile = {"performance": {"cycling_ftp": 248.0}}
        agent.mcp_session = object()

        with patch_all("call_tool", new=AsyncMock()) as call_mock, \
             patch_all("_save_user_profile") as save_mock:
            ftp = await TrainerAgent._get_or_refresh_cycling_ftp(agent)

        assert ftp == 248.0
        call_mock.assert_not_awaited()
        save_mock.assert_not_called()


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
    async def test_chat_planning_generates_structured_plan_without_llm(self):
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
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

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

        assert "Resumen" in out
        assert "Plan activo: Plan hacia 10K" in out
        mocked_create.assert_called_once()
        assert agent.user_profile["training_plan"]["id"] == "plan-db-1"

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
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

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


class TestPlannedSessionSelection:
    def test_get_planned_session_for_date_respects_week_index_when_available(self):
        plan = {
            "plan_data": {"start_date": "2026-01-06"},  # lunes
            "sessions": [
                {"week_index": 1, "day_index": 2, "session_type": "running_z2", "duration_min": 40},
                {"week_index": 2, "day_index": 2, "session_type": "running_quality", "duration_min": 55},
            ],
        }
        # 2026-01-13 es martes de la semana 2 desde start_date.
        out = _get_planned_session_for_date(plan, "2026-01-13")
        assert out is not None
        assert out["session_type"] == "running_quality"


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


class TestCyclingFtpRefreshDatePolicy:
    @pytest.mark.asyncio
    async def test_refresh_same_ftp_does_not_update_change_date(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "performance": {
                "cycling_ftp": 250.0,
                "cycling_ftp_date": "2026-08-10",
                "performance_params_updated_at": "2026-08-10",
            }
        }
        agent.mcp_session = MagicMock()

        with patch_all("call_tool", new=AsyncMock(return_value='{"functionalThresholdPower": 250}')), patch_all(
            "_save_user_profile"
        ):
            out = await TrainerAgent._get_or_refresh_cycling_ftp(agent, force_refresh=True)

        assert out == 250.0
        perf = agent.user_profile.get("performance") or {}
        assert perf.get("cycling_ftp_date") == "2026-08-10"
        assert perf.get("performance_params_updated_at") == "2026-08-10"


class TestSessionSummaryCheckpoint:
    def test_generate_session_summary_checkpoint_uses_recent_turns(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.conversation_history = [
            {"role": "user", "content": "Cuánto TSS llevo esta semana?"},
            {"role": "assistant", "content": "TSS acumulado: 260.4"},
            {"role": "user", "content": "Cómo está mi HRV hoy?"},
            {"role": "assistant", "content": "HRV 73 ms, balanced."},
        ]

        summary = TrainerAgent.generate_session_summary_checkpoint(agent)
        assert "Temas recientes:" in summary
        assert "Cómo está mi HRV hoy?" in summary
        # Las respuestas no se guardan: sus cifras caducan.
        assert "Última respuesta:" not in summary
        assert "260.4" not in summary
        assert "73 ms" not in summary

    def test_summary_checkpoint_empty_without_user_questions(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.conversation_history = [{"role": "assistant", "content": "HRV 73 ms, balanced."}]

        assert TrainerAgent.generate_session_summary_checkpoint(agent) == ""

    def test_save_session_summary_checkpoint_calls_daily_upsert(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.conversation_history = [
            {"role": "user", "content": "Mi FTP?"},
            {"role": "assistant", "content": "Tu FTP es 205W."},
        ]

        with patch("agent.trainer_agent._storage.persist_session_summary_daily") as upsert_mock:
            TrainerAgent.save_session_summary_checkpoint(agent)

        upsert_mock.assert_called_once()


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
        import agent.trainer_agent as ta
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

        monkeypatch.setattr(ta.asyncio, "wait_for", _boom_wait_for)
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
        monkeypatch.setattr(ta.asyncio, "wait_for", _boom_wait_for)
        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=120: profile["load_metrics"]["series"])

        out = await _routes_load._build_current_week_tss_markdown(object(), profile, "¿Cuánto TSS hice esta semana?")
        assert "TSS acumulado" in out
        assert "Actividades:" in out

    @pytest.mark.asyncio
    async def test_build_mcp_factual_query_markdown_does_not_use_asyncio_wait_for(self, monkeypatch):
        import agent.trainer_agent as ta

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

        monkeypatch.setattr(ta.asyncio, "wait_for", _boom_wait_for)
        patch_everywhere(monkeypatch, "call_tool", _fake_call_tool)

        out = await _routes_day_data._build_mcp_factual_query_markdown(
            object(),
            {"load_metrics": {"series": []}},
            "Analiza mi última actividad",
        )
        assert "TSS (actividad)" in out


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


class TestThresholdHistoryResolvers:
    """Umbrales del perfil con historial por fecha (ritmo, FC de TP) y lectura del umbral de Garmin."""

    def test_extract_threshold_pace_from_lactate_speed_mps_scaled_value(self):
        pace = _extract_threshold_pace_sec_per_km({"lactate_threshold_speed_mps": 0.4083})
        assert pace is not None
        # 0.4083*10 m/s => ~4:05/km
        assert 230.0 <= pace <= 260.0

    def test_resolve_tp_hr_threshold_for_date_picks_value_in_force(self):
        pass

        profile = {
            "performance": {
                "hr_threshold_bpm": 169,
                "hr_threshold_tp_history": [
                    {"date": "2026-10-21", "bpm": 169},
                    {"date": "2026-01-30", "bpm": 166},
                ],
            }
        }
        assert _resolve_tp_hr_threshold_for_date(profile, "2026-01-29") is None
        assert _resolve_tp_hr_threshold_for_date(profile, "2026-05-01") == 166
        assert _resolve_tp_hr_threshold_for_date(profile, "2026-10-21") == 169
        assert _resolve_tp_hr_threshold_for_date({"performance": {}}, "2026-05-01") is None

    def test_resolve_running_threshold_pace_for_date_picks_value_in_force(self):
        pass

        profile = {
            "performance": {
                "running_threshold_pace_sec_per_km": 252.0,
                "running_threshold_pace_history": [
                    {"date": "2026-01-30", "sec_per_km": 230},
                    {"date": "2026-05-09", "sec_per_km": 252},
                ],
            }
        }
        assert _resolve_running_threshold_pace_for_date(profile, "2026-01-29") is None
        assert _resolve_running_threshold_pace_for_date(profile, "2026-05-08") == 230
        assert _resolve_running_threshold_pace_for_date(profile, "2026-05-09") == 252
        assert _resolve_running_threshold_pace_for_date({"performance": {}}, "2026-05-01") is None


class TestStructuredPlanEngine:
    """Motor de planes estructurados: volumen real, progresión, restricciones, trail, multideporte y validación."""

    @staticmethod
    def _week_minutes(sessions: list[dict], week: int) -> int:
        return sum(int(s.get("duration_min") or 0) for s in sessions if int(s.get("week_index") or 0) == week)

    @staticmethod
    def _hm_profile(weeks_ahead: int = 12, hours: float = 10.0) -> dict:
        return {
            "goals": {
                "primary": "running",
                "target_race": "Media Maratón de Santa Pola (21,1 km)",
                "target_race_date": (date.today() + timedelta(days=7 * weeks_ahead)).isoformat(),
                "target_time": "01:22:59",
                "weekly_training_hours": hours,
            },
            "health": {},
        }

    def test_get_active_training_plan_falls_back_to_profile_when_storage_unavailable(self):
        profile = {"training_plan": {"active": True, "title": "Plan local", "status": "active"}}
        with patch("agent.trainer_agent._storage.get_active_training_plan", side_effect=RuntimeError("db down")):
            plan = _get_active_training_plan(profile)

        assert plan is not None
        assert plan["title"] == "Plan local"

    def test_build_startup_plan_recommendation_includes_title(self):
        msg = _build_startup_plan_recommendation({"title": "Plan 10K", "active": True})
        assert "Plan 10K" in msg

    def test_build_goal_plan_fallback_contains_target(self):
        profile = {
            "goals": {
                "target_race": "Zara Speed Run 10k",
                "target_race_date": "2026-11-22",
                "target_time": "0:35:59",
            },
            "health": {"injuries": ["DT 1"]},
        }
        out = _build_goal_plan_fallback(profile)
        assert "Zara Speed Run 10k" in out
        assert "Estructura semanal propuesta" in out

    def test_build_training_plan_status_markdown_no_plan_is_explicit(self):
        profile = {
            "goals": {
                "target_race": "Trail 42K",
                "target_race_date": "2026-11-22",
                "target_time": "05:30:00",
                "weekly_training_hours": 10,
            }
        }
        out = _build_training_plan_status_markdown(profile)
        assert "No tienes plan asignado" in out
        assert "22/11/2026" in out

    def test_build_training_plan_status_markdown_active_plan_uses_plan_entity(self):
        profile = {
            "goals": {"target_race": "Trail 42K", "target_race_date": "2026-11-22"},
            "training_plan": {
                "active": True,
                "status": "active",
                "title": "Plan Trail 42K",
                "target_race": "Trail 42K",
                "target_race_date": "2026-11-22",
                "today_focus": "Rodaje Z2 50 min",
            },
        }
        out = _build_training_plan_status_markdown(profile)
        assert "Sí, tienes un plan activo: Plan Trail 42K." in out
        assert "22/11/2026" in out
        assert "Rodaje Z2 50 min" in out

    def test_generate_structured_plan_payload_returns_plan_and_sessions(self):
        profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": (date.today() + timedelta(days=56)).isoformat(),
                "weekly_training_hours": 7,
            },
            "health": {},
        }
        plan, sessions = _generate_structured_plan_payload(profile, "Planifícame para mi 10K")
        assert plan["title"].startswith("Plan hacia")
        assert plan["duration_weeks"] >= 4
        assert len(sessions) == plan["duration_weeks"] * 7
        assert all(isinstance(s.get("structured_workout"), dict) for s in sessions)

        week_indexes = sorted({int(s.get("week_index") or 0) for s in sessions})
        assert week_indexes[0] == 1
        assert week_indexes[-1] == plan["duration_weeks"]

    def test_plan_starts_from_real_recent_volume_and_never_jumps_more_than_10pct(self):
        ctx = {"recent_weekly_minutes": 300.0, "longest_recent_min": 100.0, "ctl": 60.0, "atl": 40.0, "tsb": 20.0, "status": "ready"}
        plan, sessions = _generate_structured_plan_payload(self._hm_profile(), "créame un plan", load_context=ctx)
        weeks = [self._week_minutes(sessions, w) for w in range(1, plan["duration_weeks"] + 1)]
        # La semana 1 no supera lo sostenido +10 % (con margen por mínimos de sesión).
        assert weeks[0] <= 300 * 1.10 + 40
        sustained = 300.0
        for w in weeks:
            assert w <= sustained * 1.10 + 40
            sustained = max(sustained, w)
        assert weeks[0] < 10 * 60 * 0.8  # muy por debajo de las 10 h declaradas
        assert "Punto de partida: 300 min/semana reales" in plan["description"]
        assert plan["plan_data"]["load_context"]["status"] == "ready"

    def test_plan_long_session_grows_from_longest_recent(self):
        ctx = {"recent_weekly_minutes": 600.0, "longest_recent_min": 90.0}
        _, sessions = _generate_structured_plan_payload(self._hm_profile(), "créame un plan", load_context=ctx)
        long_w1 = max(int(s["duration_min"]) for s in sessions if s["week_index"] == 1 and s["session_type"] == "long_run")
        assert long_w1 <= round(90 * 1.10)

    def test_plan_with_fatigue_softens_first_week(self):
        ctx = {"recent_weekly_minutes": 400.0, "status": "fatigue_high", "tsb": -25.0, "ctl": 60.0, "atl": 85.0}
        plan, sessions = _generate_structured_plan_payload(self._hm_profile(), "créame un plan", load_context=ctx)
        w1_quality = [s for s in sessions if s["week_index"] == 1 and s["session_type"] == "running_quality"]
        assert w1_quality and all(s["intensity"] == "RPE 5-6" for s in w1_quality)
        assert "Inicio con fatiga" in plan["description"]

    def test_plan_derives_half_marathon_target_pace(self):
        plan, sessions = _generate_structured_plan_payload(self._hm_profile(), "créame un plan")
        # 1:22:59 / 21,0975 km = 236 s/km = 3:56/km
        assert plan["plan_data"]["target_pace_sec_per_km"] == pytest.approx(236.0, abs=0.5)
        assert any("min/km" in str(s.get("notes")) for s in sessions if s["session_type"] == "running_quality")

    def test_plan_ignores_past_target_race(self):
        profile = self._hm_profile()
        profile["goals"]["target_race_date"] = (date.today() - timedelta(days=12)).isoformat()
        plan, _ = _generate_structured_plan_payload(profile, "créame un plan")
        assert plan["duration_weeks"] == 8
        assert "ya pasó" in plan["description"]
        assert plan["plan_data"]["target_race_date"] == ""

    def test_structured_hr_targets_are_garmin_zones(self):
        # 23/10: la referencia es la zona de Garmin (la del reloj), no el % del LTHR de Friel.
        _, sessions = _generate_structured_plan_payload(self._hm_profile(), "créame un plan")
        quality = next(s for s in sessions if s["session_type"] == "running_quality")
        sw = quality["structured_workout"]
        primary = sw["targets"]["primary"]
        assert primary["metric"] == "hr_zone"
        assert primary["zone"] == _daily_engine._zone_for_intensity_class(sw["summary"]["intensityClass"])
        warmup = sw["steps"][0]["target"]
        assert warmup == {"metric": "hr_zone", "zone": 1, "zone_to": 2}
        assert "hr_pct" not in json.dumps(sw)

    def test_session_label_shows_garmin_zone_with_rpe(self, monkeypatch):
        zones = [("Z1 Recuperación", 115, 134), ("Z2 Aeróbica", 135, 145), ("Z3 Tempo", 146, 157),
                 ("Z4 Subumbral", 158, 168), ("Z5 Supraumbral", 169, 179)]
        patch_everywhere(monkeypatch, "_resolve_hr_zones", lambda profile, sport: (zones, "Garmin"))
        z2 = {"session_type": "running_z2", "duration_min": 45, "intensity": "RPE 3-4",
              "structured_workout": {"summary": {"intensityClass": "endurance"},
                                     "targets": {"primary": {"metric": "hr_zone", "zone": 2}}}}
        assert _daily_engine._session_intensity_label(z2, {}) == "Z2 Aeróbica (135–145 lpm) · RPE 3-4"
        assert _daily_engine._session_intensity_label({"session_type": "strength", "duration_min": 40, "intensity": "RPE 5-6"}, {}) == "RPE 5-6"
        assert _daily_engine._session_intensity_label({"session_type": "rest", "duration_min": 0, "intensity": ""}, {}) == "descanso"

    def test_daily_adjustment_lowers_one_zone_without_rescaling_intensity(self):
        session = {"structured_workout": {"steps": [
            {"name": "Warm-up", "type": "warmup", "duration_min": 10, "target": {"metric": "hr_zone", "zone": 1, "zone_to": 2}},
            {"name": "Main", "type": "steady", "duration_min": 30, "intensityClass": "tempo",
             "target": {"metric": "hr_zone", "zone": 3}},
        ]}}
        sw, trace = _daily_engine._apply_structured_workout_adjustment(session, "easy")
        main = sw["steps"][1]
        assert main["target"]["zone"] == 2 and main["intensityClass"] == "endurance"
        assert main["duration_min"] == 21.0
        assert any("Z3->Z2" in t for t in trace)

    def test_today_session_frame_per_decision(self, monkeypatch):
        zones = [("Z1 Recuperación", 115, 134), ("Z2 Aeróbica", 135, 145), ("Z3 Tempo", 146, 157),
                 ("Z4 Subumbral", 158, 168), ("Z5 Supraumbral", 169, 179)]
        patch_everywhere(monkeypatch, "_resolve_hr_zones", lambda profile, sport: (zones, "Garmin"))

        easy = _daily_engine._build_today_session_frame(
            {"decision": "easy", "resulting_session": "rodaje suave 30-50 min en Z1-Z2 + movilidad"}, {})
        assert "sesión suave" in easy
        assert "Z1–Z2 (115–145 lpm), techo 145 lpm" in easy
        assert "Duración máxima: 50 min" in easy

        reduce = _daily_engine._build_today_session_frame(
            {"decision": "reduce", "resulting_session": "rodaje controlado 40-60 min (sin bloques intensos)"}, {})
        assert "hasta Z3 (115–157 lpm)" in reduce and "sin bloques en Z4–Z5" in reduce
        assert "Duración máxima: 60 min" in reduce

        rest = _daily_engine._build_today_session_frame({"decision": "rest", "resulting_session": "descanso + movilidad ligera (20-30 min)"}, {})
        assert "Decisión del motor diario: descanso" in rest and "Duración máxima: 30 min" in rest

        planned = {"decision": "maintain", "resulting_session": "tempo 3x10'",
                   "adjusted_structured_workout": {"summary": {"intensityClass": "threshold"},
                                                   "steps": [{"type": "steady", "duration_min": 55}]}}
        maintain = _daily_engine._build_today_session_frame(planned, {})
        assert "Z4 Subumbral (158–168 lpm)" in maintain and "Duración máxima: 55 min" in maintain

        assert _daily_engine._build_today_session_frame({}, {}) == ""

    @pytest.mark.asyncio
    async def test_coaching_gets_session_frame_only_for_today(self, monkeypatch):
        patch_everywhere(monkeypatch, "_build_today_session_frame", lambda decision, profile: "MARCO-DE-HOY")
        agent = object.__new__(ta.TrainerAgent)
        agent.user_profile = {}
        agent.model = "m"
        agent.provider = "nvidia"
        agent.total_prompt_tokens = agent.total_completion_tokens = 0
        reply = MagicMock()
        reply.choices = [MagicMock(message=MagicMock(content="- Rodaje suave Z2 (135–145 lpm)."))]
        reply.usage = None
        agent.client = MagicMock()
        agent.client.chat.completions.create = AsyncMock(return_value=reply)
        patch_everywhere(monkeypatch, "_resolve_hr_zones", lambda profile, sport: ([], "n/d"))

        await agent._augment_with_llm_coaching_if_needed(
            "¿Qué me recomiendas hoy?", "datos", "daily_readiness", daily_plan_decision={"decision": "easy"})
        today_user = agent.client.chat.completions.create.await_args.kwargs["messages"][1]["content"]
        await agent._augment_with_llm_coaching_if_needed(
            "¿Qué sesión me recomiendas para esta semana?", "datos", "daily_readiness", daily_plan_decision={"decision": "easy"})
        week_user = agent.client.chat.completions.create.await_args.kwargs["messages"][1]["content"]

        assert "MARCO-DE-HOY" in today_user
        assert "MARCO-DE-HOY" not in week_user

    def test_today_training_question_for_loop_g15(self):
        yes = ["¿Me conviene hacer la tirada larga hoy?", "¿Hoy debería correr suave?", "¿Qué sesión me recomiendas hoy?",
               "¿Puedo hacer cuestas hoy o mejor descanso?"]
        no = ["¿Cómo fue mi entreno de hoy?", "Analiza la carrera de hoy", "¿Qué hice hoy?",
              "¿Me conviene descansar mañana?", "¿Cuántos pasos llevo hoy?"]
        assert [q for q in yes if not _router._is_today_training_question(q)] == []
        assert [q for q in no if _router._is_today_training_question(q)] == []

    @pytest.mark.asyncio
    async def test_today_frame_for_loop_uses_daily_engine(self, monkeypatch):
        agent = object.__new__(ta.TrainerAgent)
        agent.user_profile = {}
        agent.collect_startup_snapshot_48h = AsyncMock(return_value={"dates": {}})
        patch_everywhere(monkeypatch, "_get_active_training_plan", lambda profile: None)
        monkeypatch.setattr(ta, "_compute_daily_plan_adjustment",
                            lambda snapshot, plan: {"decision": "easy", "resulting_session": "rodaje suave 30-45 min"})
        patch_everywhere(monkeypatch, "_build_today_session_frame", lambda decision, profile: "MARCO " + decision["decision"])

        frame, decision = await agent._today_frame_for_loop({"hrv_today": "{}"})
        assert (frame, decision) == ("MARCO easy", "easy")
        agent.collect_startup_snapshot_48h.assert_awaited_once_with(prefetch_today={"hrv_today": "{}"})

        # Si los datos de hoy fallan, el bucle sigue sin marco.
        agent.collect_startup_snapshot_48h = AsyncMock(side_effect=RuntimeError("Garmin caído"))
        assert await agent._today_frame_for_loop() == ("", None)

    @pytest.mark.asyncio
    async def test_sleep_trend_always_gets_its_own_prompt_and_period(self, monkeypatch):
        agent = object.__new__(ta.TrainerAgent)
        agent.user_profile = {}
        agent.model = "m"
        agent.provider = "nvidia"
        agent.total_prompt_tokens = agent.total_completion_tokens = 0
        reply = MagicMock()
        reply.choices = [MagicMock(message=MagicMock(content="- Sueño estable."))]
        reply.usage = None
        agent.client = MagicMock()
        agent.client.chat.completions.create = AsyncMock(return_value=reply)
        patch_everywhere(monkeypatch, "_resolve_hr_zones", lambda profile, sport: ([], "n/d"))

        # Sin palabras de recomendación: con always=True la lectura del LLM se añade igual.
        out = await agent._augment_with_llm_coaching_if_needed(
            "¿Cómo he dormido últimamente?", "TABLA", "sleep_trend", always=True, prompt="coaching/sleep_trend",
            horizon="las últimas 7 noches frente a las 7 anteriores")
        messages = agent.client.chat.completions.create.await_args.kwargs["messages"]
        assert _prompts._render_prompt("coaching/sleep_trend") in messages[0]["content"]
        assert "las últimas 7 noches frente a las 7 anteriores" in messages[1]["content"]
        assert "TABLA" in out and "Sueño estable" in out

    def test_structured_summary_names_garmin_zone(self):
        row = {"structured_workout": {"steps": [
            {"type": "warmup", "duration_min": 10},
            {"type": "steady", "duration_min": 30, "intensityClass": "endurance"},
            {"type": "cooldown", "duration_min": 5},
        ]}}
        assert _daily_engine._structured_workout_summary(row) == "bloque principal: 30' (Z2 Aeróbica)"

    def test_generate_structured_plan_payload_structured_workout_contract(self):
        profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": (date.today() + timedelta(days=56)).isoformat(),
                "weekly_training_hours": 8,
            },
            "health": {},
        }
        _, sessions = _generate_structured_plan_payload(profile, "Planifícame para mi 10K")
        sample = next((s for s in sessions if str(s.get("session_type")) != "rest"), sessions[0])
        sw = sample.get("structured_workout") or {}

        assert sw.get("schema") == "kairos-workout-v1"
        assert sw.get("sessionType") == sample.get("session_type")
        assert isinstance(sw.get("steps"), list) and sw.get("steps")

        first_step = sw["steps"][0]
        assert isinstance(first_step.get("name"), str) and first_step.get("name")
        assert isinstance(first_step.get("type"), str) and first_step.get("type")
        assert isinstance(first_step.get("intensityClass"), str) and first_step.get("intensityClass")

    def test_generate_structured_plan_payload_rest_sessions_have_rest_step(self):
        profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": (date.today() + timedelta(days=56)).isoformat(),
                "weekly_training_hours": 8,
                "availability": {"unavailable_days": ["domingo"]},
            },
            "health": {},
        }
        _, sessions = _generate_structured_plan_payload(profile, "Planifícame")
        rest = next((s for s in sessions if str(s.get("session_type") or "").lower() == "rest"), None)
        assert rest is not None
        sw = rest.get("structured_workout") or {}
        assert sw.get("schema") == "kairos-workout-v1"
        assert isinstance(sw.get("steps"), list) and sw.get("steps")
        assert sw["steps"][0].get("type") == "rest"

    def test_generate_structured_plan_payload_has_weekly_progression(self):
        profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": (date.today() + timedelta(days=98)).isoformat(),
                "weekly_training_hours": 8,
            },
            "health": {},
        }
        plan, sessions = _generate_structured_plan_payload(profile, "Planifícame para 10K")
        weekly_totals = []
        for wi in range(1, int(plan["duration_weeks"]) + 1):
            rows = [s for s in sessions if int(s.get("week_index") or 0) == wi]
            weekly_totals.append(sum(int((r or {}).get("duration_min") or 0) for r in rows))

        assert len(set(weekly_totals)) >= 3
        assert weekly_totals[-1] < max(weekly_totals)  # taper final

    def test_generate_structured_plan_payload_respects_unavailable_days(self):
        profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": (date.today() + timedelta(days=56)).isoformat(),
                "weekly_training_hours": 8,
                "availability": {
                    "unavailable_days": ["sabado", "domingo"],
                },
            },
            "health": {},
        }
        plan, sessions = _generate_structured_plan_payload(profile, "Planifícame")
        assert plan.get("plan_data", {}).get("constraints")
        assert len(sessions) == int(plan["duration_weeks"]) * 7

        for s in sessions:
            if int(s.get("day_index") or 0) in {6, 7}:
                assert str(s.get("session_type") or "").lower() == "rest"

    def test_generate_structured_plan_payload_respects_day_max_minutes(self):
        profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": (date.today() + timedelta(days=56)).isoformat(),
                "weekly_training_hours": 8,
                "availability": {
                    "max_minutes_per_day": {"lunes": 40, "martes": 45, "miércoles": 50, "jueves": 45, "viernes": 0, "sábado": 70, "domingo": 35},
                },
            },
            "health": {},
        }
        _, sessions = _generate_structured_plan_payload(profile, "Plan general")
        caps = {1: 40, 2: 45, 3: 50, 4: 45, 5: 0, 6: 70, 7: 35}
        for s in sessions:
            d = int(s.get("day_index") or 0)
            dur = int(s.get("duration_min") or 0)
            assert dur <= caps[d]
            if d == 5:
                assert str(s.get("session_type") or "").lower() == "rest"

    def test_trail_plan_uses_trail_session_types(self):
        """Plan con 'trail running' debe tener session_types específicos de trail."""
        profile = {
            "goals": {
                "primary": "trail running",
                "target_race": "Ultra Pirineos 55K",
                "target_race_date": (date.today() + timedelta(days=84)).isoformat(),
                "weekly_training_hours": 11,
            },
            "health": {"injuries": []},
        }
        _, sessions = _generate_structured_plan_payload(profile, "Crea plan para la ultra")
        session_types = {s["session_type"] for s in sessions}
        assert "trail_long" in session_types
        assert "trail_hills" in session_types
        assert "trail_z2" in session_types
        assert "trail_tempo" in session_types
        # No deben quedar tipos genéricos de running
        assert "running_quality" not in session_types
        assert "long_run" not in session_types

    def test_trail_plan_long_run_notes_mention_desnivel(self):
        """La tirada larga de trail debe mencionar desnivel en las notas."""
        profile = {
            "goals": {"primary": "trail running", "target_race": "Ultra 55K",
                      "weekly_training_hours": 10},
            "health": {"injuries": []},
        }
        _, sessions = _generate_structured_plan_payload(profile, "plan trail")
        long_sessions = [s for s in sessions if s["session_type"] == "trail_long"]
        assert long_sessions, "Debe existir al menos una sesión trail_long"
        notes = long_sessions[0].get("notes", "")
        assert "desnivel" in notes.lower()

    def test_trail_plan_hills_mention_bajadas(self):
        """La sesión de cuestas de trail debe mencionar técnica de bajada."""
        profile = {
            "goals": {"primary": "trail running", "target_race": "Ultra 55K",
                      "weekly_training_hours": 10},
            "health": {"injuries": []},
        }
        _, sessions = _generate_structured_plan_payload(profile, "plan trail")
        hills = [s for s in sessions if s["session_type"] == "trail_hills"]
        assert hills
        all_text = " ".join([
            " ".join(hills[0].get("exercises", [])),
            hills[0].get("notes", ""),
        ]).lower()
        assert "bajada" in all_text

    def test_non_trail_plan_does_not_use_trail_types(self):
        """Un plan de running estándar no debe tener session_types de trail."""
        profile = {
            "goals": {"primary": "running", "target_race": "10K",
                      "weekly_training_hours": 7},
            "health": {"injuries": []},
        }
        _, sessions = _generate_structured_plan_payload(profile, "plan running")
        session_types = {s["session_type"] for s in sessions}
        assert "trail_long" not in session_types
        assert "trail_hills" not in session_types

    def test_multisport_preferences_add_cycling_and_gym_sessions(self):
        profile = {
            "goals": {
                "primary": "running",
                "target_race": "10K",
                "target_race_date": (date.today() + timedelta(days=70)).isoformat(),
                "weekly_training_hours": 9,
            },
            "health": {"injuries": []},
        }
        _, sessions = _generate_structured_plan_payload(
            profile,
            "Quiero alternar gimnasio, ciclismo de carretera, de montaña y trail",
        )
        types = {str(s.get("session_type") or "") for s in sessions}
        assert any("strength" in t for t in types)
        assert any("cycl" in t for t in types)

    def test_plan_with_injury_has_difficulty_reason_in_description(self):
        """La descripción del plan debe mencionar la razón del ajuste de dificultad."""
        profile = {
            "goals": {"primary": "trail running", "target_race": "Ultra 55K",
                      "weekly_training_hours": 11},
            "health": {"injuries": ["tendinitis rotuliana leve"]},
        }
        plan, _ = _generate_structured_plan_payload(profile, "plan trail")
        assert plan["difficulty"] == "easy"
        # La descripción debe mencionar la lesión
        assert "lesi" in plan["description"].lower() or "lesi" in (plan.get("plan_data") or {}).get("difficulty_reason", "").lower()

    def test_plan_with_injury_stores_difficulty_reason_in_plan_data(self):
        """plan_data.difficulty_reason debe explicar el motivo del ajuste."""
        profile = {
            "goals": {"primary": "running", "target_race": "10K", "weekly_training_hours": 8},
            "health": {"injuries": ["fascitis plantar"]},
        }
        plan, _ = _generate_structured_plan_payload(profile, "plan running")
        reason = (plan.get("plan_data") or {}).get("difficulty_reason", "")
        assert "easy" in reason.lower()
        assert "fascitis plantar" in reason.lower()

    def test_plan_without_injury_high_hours_has_hard_reason(self):
        """Con ≥10h y sin lesión, difficulty_reason debe indicar 'hard'."""
        profile = {
            "goals": {"primary": "trail running", "target_race": "Ultra 55K",
                      "weekly_training_hours": 12},
            "health": {"injuries": []},
        }
        plan, _ = _generate_structured_plan_payload(profile, "plan trail")
        reason = (plan.get("plan_data") or {}).get("difficulty_reason", "")
        assert plan["difficulty"] == "hard"
        assert "hard" in reason.lower()

    def test_validate_structured_plan_flags_invalid_session_day(self):
        plan = {
            "title": "Plan",
            "objective": "Objetivo",
            "duration_weeks": 8,
        }
        sessions = [{"day_index": 9, "duration_min": 40, "session_type": "running_z2"}]
        errors = _validate_structured_plan(plan, sessions, {"goals": {"weekly_training_hours": 6}})
        assert any("día fuera de rango" in e for e in errors)

    def test_validate_structured_plan_flags_flat_weekly_template(self):
        plan = {
            "title": "Plan",
            "objective": "Objetivo",
            "duration_weeks": 8,
        }
        base_week = [
            (1, "strength", 40, "RPE 4-5"),
            (2, "running_quality", 45, "RPE 7-8"),
            (3, "running_z2", 40, "RPE 3-4"),
            (4, "running_quality", 45, "RPE 7-8"),
            (5, "rest", 0, "RPE 1-2"),
            (6, "long_run", 80, "RPE 4-5"),
            (7, "recovery", 30, "RPE 2-3"),
        ]
        sessions = []
        for wi in range(1, 9):
            for day, stype, dur, intensity in base_week:
                sessions.append(
                    {
                        "week_index": wi,
                        "day_index": day,
                        "session_type": stype,
                        "duration_min": dur,
                        "intensity": intensity,
                        "notes": "template",
                        "structured_workout": {
                            "schema": "kairos-workout-v1",
                            "sessionType": stype,
                            "steps": (
                                [{"name": "Rest", "type": "rest", "duration_min": 0, "reps": 1, "intensityClass": "recovery"}]
                                if stype == "rest"
                                else [
                                    {"name": "Warm-up", "type": "warmup", "duration_min": max(5, int(dur * 0.2)), "reps": 1, "intensityClass": "endurance"},
                                    {"name": "Main", "type": "steady", "duration_min": max(1, dur - max(5, int(dur * 0.2)) - max(5, int(dur * 0.2))), "reps": 1, "intensityClass": "tempo" if "quality" in stype else "endurance"},
                                    {"name": "Cool-down", "type": "cooldown", "duration_min": max(5, int(dur * 0.2)), "reps": 1, "intensityClass": "recovery"},
                                ]
                            ),
                        },
                    }
                )
        errors = _validate_structured_plan(plan, sessions, {"goals": {"weekly_training_hours": 8}})
        assert any("demasiado plano" in e or "repiten" in e for e in errors)

    def test_validate_structured_plan_requires_structured_workout_for_non_rest(self):
        profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": (date.today() + timedelta(days=56)).isoformat(),
                "weekly_training_hours": 8,
            },
            "health": {},
        }
        plan, sessions = _generate_structured_plan_payload(profile, "Planifícame para mi 10K")
        first_active = next((s for s in sessions if str(s.get("session_type") or "") != "rest"), None)
        assert first_active is not None
        first_active.pop("structured_workout", None)

        errors = _validate_structured_plan(plan, sessions, profile)
        assert any("structured_workout" in e for e in errors)

    def test_validate_structured_plan_flags_invalid_structured_order(self):
        profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": (date.today() + timedelta(days=56)).isoformat(),
                "weekly_training_hours": 8,
            },
            "health": {},
        }
        plan, sessions = _generate_structured_plan_payload(profile, "Planifícame para mi 10K")
        first_active = next((s for s in sessions if str(s.get("session_type") or "") != "rest"), None)
        assert first_active is not None
        sw = first_active.get("structured_workout") or {}
        steps = list(sw.get("steps") or [])
        assert len(steps) >= 3
        sw["steps"] = [steps[-1]] + steps[1:-1] + [steps[0]]

        errors = _validate_structured_plan(plan, sessions, profile)
        assert any("warmup" in e.lower() and "cooldown" in e.lower() for e in errors)

    def test_summarize_plan_changes_includes_duration_and_volume(self):
        previous_plan = {"duration_weeks": 8, "difficulty": "moderate"}
        new_plan = {"duration_weeks": 10, "difficulty": "hard"}
        previous_sessions = [{"duration_min": 40}, {"duration_min": 60}]
        new_sessions = [{"duration_min": 50}, {"duration_min": 70}]
        out = _summarize_plan_changes(previous_plan, new_plan, previous_sessions, new_sessions)
        assert "Duración" in out
        assert "Volumen semanal estimado" in out


class TestHrZonesFromGarmin:
    """Zonas de FC: las de Garmin (reloj y TP) guardadas en el perfil; respaldo % LTHR sin huecos."""

    GARMIN = {"sports": [
        {"sport": "cycling", "method": "HR_RESERVE", "resting_hr": 40, "max_hr": 179, "lactate_threshold_hr": 153,
         "zones": [{"zone": i + 1, "min_bpm": f, "max_bpm": None} for i, f in enumerate((105, 125, 136, 146, 159))]},
        {"sport": "running", "method": "HR_RESERVE", "resting_hr": 40, "max_hr": 179, "lactate_threshold_hr": 166,
         "zones": [{"zone": i + 1, "min_bpm": f, "max_bpm": None} for i, f in enumerate((115, 135, 146, 158, 169))]},
    ]}

    def _profile(self, zones=None):
        perf = {"hr_threshold_bpm": 169}
        if zones:
            perf["hr_zones_garmin"] = {"updated_at": "2026-10-22", **zones}
        return {"performance": perf}

    def test_uses_garmin_zones_per_sport(self):
        zones, source = _hr_profile._resolve_hr_zones(self._profile(self.GARMIN), "running")
        assert source == "Garmin (reserva de FC)"
        assert zones == [("Z1 Recuperación", 115, 134), ("Z2 Aeróbica", 135, 145), ("Z3 Tempo", 146, 157),
                         ("Z4 Subumbral", 158, 168), ("Z5 Supraumbral", 169, 179)]
        cycling, _ = _hr_profile._resolve_hr_zones(self._profile(self.GARMIN), "cycling")
        assert cycling[1] == ("Z2 Aeróbica", 125, 135)
        # Deporte sin zonas propias y sin "default": no inventa; cae al respaldo por LTHR.
        other, source = _hr_profile._resolve_hr_zones(self._profile(self.GARMIN), "swimming")
        assert source.startswith("% del LTHR")

    def test_lthr_fallback_has_no_gaps(self):
        zones, source = _hr_profile._resolve_hr_zones(self._profile(), "running")
        assert source.startswith("% del LTHR")
        covered = {bpm for _, lo, hi in zones for bpm in range(lo, hi + 1)}
        assert set(range(100, 200)) <= covered  # antes 151 y 160 no caían en ninguna zona
        assert [z[0] for z in zones][1] == "Z2 Aeróbica"

    def test_context_block_lists_garmin_zones(self):
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.user_profile = self._profile(self.GARMIN)
        out = agent._load_and_anchors_context_block()
        assert "Zonas de FC de carrera [Garmin (reserva de FC)]: Z1 Recuperación 115–134 · Z2 Aeróbica 135–145" in out
        assert "Zonas de FC de ciclismo [Garmin (reserva de FC)]: Z1 Recuperación 105–124" in out

    def _agent_with_tool(self, payload, profile):
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = profile
        return agent, patch_all("call_tool", AsyncMock(return_value=payload))

    def _payload(self, z2_floor=135):
        return json.dumps({"sports": [
            {**s, "zones": [dict(z, min_bpm=(z2_floor if s["sport"] == "running" and z["zone"] == 2 else z["min_bpm"]))
                            for z in s["zones"]]}
            for s in self.GARMIN["sports"]]})

    @pytest.mark.asyncio
    async def test_refresh_saves_only_when_zones_change(self):
        profile = self._profile(self.GARMIN)
        agent, tool_patch = self._agent_with_tool(self._payload(), profile)
        with tool_patch, patch_all("_save_user_profile") as save_mock:
            await agent._refresh_hr_zones_from_garmin()
        save_mock.assert_not_called()

        agent, tool_patch = self._agent_with_tool(self._payload(z2_floor=136), profile)
        with tool_patch, patch_all("_save_user_profile") as save_mock:
            await agent._refresh_hr_zones_from_garmin()
        save_mock.assert_called_once()
        perf = profile["performance"]
        running = next(s for s in perf["hr_zones_garmin"]["sports"] if s["sport"] == "running")
        assert running["zones"][1]["min_bpm"] == 136
        assert perf["hr_zones_garmin"]["updated_at"] == date.today().isoformat()
        assert perf["hr_zones_garmin_history"][-1]["valid_from"] == "2026-10-22"

    @pytest.mark.asyncio
    async def test_refresh_keeps_stored_zones_when_garmin_fails(self):
        profile = self._profile(self.GARMIN)
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = profile
        with patch_all("call_tool", AsyncMock(side_effect=TimeoutError("sin red"))), \
             patch_all("_save_user_profile") as save_mock:
            await agent._refresh_hr_zones_from_garmin()
        save_mock.assert_not_called()
        assert profile["performance"]["hr_zones_garmin"]["sports"] == self.GARMIN["sports"]


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


class TestResolveLastActivity:
    """Precarga de "mi última actividad / mi último trail…" en chat() (28/10, pendiente 10)."""

    ACTS = [
        {"activityId": 30, "startTimeLocal": "2026-10-27 18:00:00", "activityType": {"typeKey": "strength_training"}},
        {"activityId": 20, "startTimeLocal": "2026-10-25 09:00:00", "activityType": {"typeKey": "road_biking"}},
        {"activityId": 10, "startTimeLocal": "2026-10-09 22:00:00", "activityType": {"typeKey": "trail_running"}},
    ]

    @staticmethod
    def _garmin(monkeypatch, acts=None, fail=False):
        async def fake_call(_session, name, args):
            assert name == "get_activities"
            if fail:
                return "Error al llamar a 'get_activities': Connection closed"
            return json.dumps(acts)  # call_tool devuelve texto: antes no se leía como JSON y nunca encontraba nada

        patch_everywhere(monkeypatch, "call_tool", fake_call)

    @pytest.mark.asyncio
    async def test_finds_latest_of_requested_sport(self, monkeypatch):
        self._garmin(monkeypatch, self.ACTS)
        assert await _activity_analysis._resolve_last_activity(None, "¿Qué tal fue mi último trail?") == ("2026-10-09", 10)
        # road_biking también es bici (el filtro anterior solo miraba "cycl").
        assert await _activity_analysis._resolve_last_activity(None, "Analiza mi última salida en bici") == ("2026-10-25", 20)
        assert await _activity_analysis._resolve_last_activity(None, "Analiza mi última actividad") == ("2026-10-27", 30)

    @pytest.mark.asyncio
    async def test_no_activity_of_that_sport_means_no_prefetch(self, monkeypatch):
        # Antes analizaba la primera actividad de hoy, fuera del deporte que fuera.
        self._garmin(monkeypatch, self.ACTS[:2])
        assert await _activity_analysis._resolve_last_activity(None, "¿Cómo fue mi último trail?") == (None, None)

    @pytest.mark.asyncio
    async def test_without_garmin_uses_last_day_with_load(self, monkeypatch):
        self._garmin(monkeypatch, fail=True)
        patch_everywhere(monkeypatch, "_load_metrics_series", lambda: [
            {"date": "2026-10-26", "tss": 55.0}, {"date": "2026-10-27", "tss": 0.0, "activities_count": 0}])
        assert await _activity_analysis._resolve_last_activity(None, "Feedback de mi último entreno") == ("2026-10-26", None)

    @pytest.mark.asyncio
    async def test_other_questions_do_not_prefetch(self, monkeypatch):
        self._garmin(monkeypatch, self.ACTS)
        assert await _activity_analysis._resolve_last_activity(None, "¿Cuál es mi FC umbral?") == (None, None)


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


class TestStartupInParallel:
    """Arranque (8c, 28/10): el resumen inicial va a la vez que la carga y el perfil de Garmin se pide una vez."""

    @pytest.mark.asyncio
    async def test_snapshot_reads_the_load_series_only_after_the_load_is_computed(self, monkeypatch):
        agent = ta.TrainerAgent.__new__(ta.TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {}
        order = []

        async def garmin(_session, name, _args):
            order.append(name)
            return "{}"

        async def load():
            await asyncio.sleep(0.05)
            order.append("carga guardada")

        def series(*_a, **_k):
            order.append("serie leída")
            return []

        patch_everywhere(monkeypatch, "call_tool", garmin)
        patch_everywhere(monkeypatch, "_load_metrics_series", series)
        monkeypatch.setattr(ta._storage, "upsert_wellness_daily", lambda *a, **k: None)
        load_task = asyncio.ensure_future(load())
        await agent.collect_startup_snapshot_48h(load_ready=load_task)

        assert order.index("carga guardada") < order.index("serie leída")
        assert order.index("carga guardada") > 0  # Garmin no esperó a la carga

    @pytest.mark.asyncio
    async def test_personal_data_reuses_the_profile_already_read(self, monkeypatch):
        agent = ta.TrainerAgent.__new__(ta.TrainerAgent)
        agent.mcp_session = MagicMock()
        garmin = AsyncMock(return_value="{}")
        patch_everywhere(monkeypatch, "call_tool", garmin)
        raw = json.dumps({"userData": {"gender": "MALE", "weight": 67000.0, "height": 178}})

        out = await agent.fetch_garmin_personal_data(profile_raw=raw)

        assert out == {"gender": "hombre", "height_cm": 178, "weight_kg": 67.0}
        garmin.assert_not_called()


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
