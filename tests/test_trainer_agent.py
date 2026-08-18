"""
tests/test_trainer_agent.py
Suite de tests unitarios para las funciones puras de trainer_agent.py.

Cubre:
  - _seconds_to_hhmmss
  - _normalize_date_args
  - _strip_garmin_object
  - _compact_tool_result / _compact_personal_records
  - _clean_schema_for_gemini
  - _GeminiCompletions._parse  (sin llamada real a la API)
"""

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.trainer_agent import (
    _build_training_plan_status_markdown,
    _build_tools_schema,
    _build_mcp_read_only_block_message,
    _build_personal_records_markdown,
    _build_startup_plan_recommendation,
    _build_activity_candidates_payload,
    _build_goal_plan_fallback,
    _build_athlete_knowledge_context,
    _build_proactive_status_markdown,
    _build_post_activity_section_spec,
    _build_activity_analysis_block,
    _format_activity_analysis_for_markdown,
    _build_load_trend_table,
    _classify_running_session_with_confidence,
    _estimate_session_tss,
    _compute_load_fatigue_metrics,
    _format_load_fatigue_summary,
    _resolve_sport_model_cfg,
    _build_recovery_fallback_snapshot,
    _compute_daily_plan_adjustment,
    _compute_plan_execution_feedback,
    _clean_schema_for_gemini,
    _compact_personal_records,
    _compact_tool_result,
    _extract_activities_list,
    _fetch_activities_for_load_calc,
    _extract_cycling_ftp_watts,
    _infer_tss_source_tag,
        _extract_threshold_pace_sec_per_km,
    _extract_iso_date_from_text,
    _generate_structured_plan_payload,
    _GeminiCompletions,
    _get_active_training_plan,
    _get_planned_session_for_date,
    _has_goal_in_profile,
    _detect_personal_records_sport_intent,
    _is_generic_needs_more_info_reply,
    _is_personal_records_followup_intent,
    _is_plan_status_intent,
    _is_daily_readiness_intent,
    _is_hr_threshold_query_intent,
    _is_mcp_factual_query_intent,
    _is_activity_details_query_intent,
    _is_running_threshold_query_intent,
    _is_config_options_intent,
    _is_week_tss_intent,
    _is_week_activities_intent,
    _is_planning_intent,
    _is_write_mcp_tool,
    _resolve_week_window,
    _resolve_load_parameters_effective_date,
    _resolve_activity_id_from_query,
    _summarize_plan_changes,
    _is_activity_in_last_48h,
    _is_no_data_result,
    _normalize_trend_date_range,
    _normalize_get_activity_args,
    _normalize_date_args,
    _load_system_prompt,
    _load_athlete_knowledge_chunks,
    _resolve_kb_paths,
    _retrieve_athlete_knowledge,
    _seconds_to_hhmmss,
    _strip_garmin_object,
    _validate_structured_plan,
    TrainerAgent,
)


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


class TestSystemPromptDateFormatRules:
    def test_full_prompt_requires_global_spanish_date_format(self):
        prompt = _load_system_prompt(compact=False)
        assert "Formato de fecha obligatorio (España)" in prompt
        assert "Regla global de fechas (OBLIGATORIA)" in prompt
        assert "DD/MM/AAAA" in prompt
        assert "YYYY-MM-DD" in prompt

    def test_compact_prompt_requires_global_spanish_date_format(self):
        prompt = _load_system_prompt(compact=True)
        assert "Regla global de fechas (España)" in prompt
        assert "DD/MM/AAAA" in prompt
        assert "YYYY-MM-DD" in prompt


class TestSystemPromptPlanStatusRules:
    def test_full_prompt_includes_plan_status_intent_variants(self):
        prompt = _load_system_prompt(compact=False)
        assert "Consulta de estado del plan (OBLIGATORIO)" in prompt
        assert "que plan llevo esta semana?" in prompt
        assert "sigo con el plan?" in prompt
        assert "goals" in prompt and "training_plan" in prompt

    def test_compact_prompt_includes_plan_status_rules(self):
        prompt = _load_system_prompt(compact=True)
        assert "Estado del plan (OBLIGATORIO)" in prompt
        assert "Nunca inferir plan activo desde `goals`" in prompt
        assert "training_plan" in prompt


class TestSystemPromptPlanManagementRules:
    def test_full_prompt_includes_functional_plan_management(self):
        prompt = _load_system_prompt(compact=False)
        assert "Generacion y manejo funcional de planes (OBLIGATORIO)" in prompt
        assert "/plan crear" in prompt
        assert "/plan listar" in prompt
        assert "/plan ver <plan_id>" in prompt
        assert "/plan activar <plan_id>" in prompt
        assert "No afirmes que un plan quedó guardado/activado" in prompt

    def test_compact_prompt_includes_functional_plan_management(self):
        prompt = _load_system_prompt(compact=True)
        assert "Generación y manejo de planes (OBLIGATORIO)" in prompt
        assert "/plan crear" in prompt
        assert "/plan listar" in prompt
        assert "/plan ver <plan_id>" in prompt
        assert "/plan activar <plan_id>" in prompt
        assert "nueva versión" in prompt or "nueva version" in prompt


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


# ─── _clean_schema_for_gemini ─────────────────────────────────────────────────

class TestCleanSchemaForGemini:
    def test_removes_exclusive_minimum(self):
        schema = {"type": "integer", "exclusiveMinimum": 0, "description": "Activity ID"}
        result = _clean_schema_for_gemini(schema)
        assert "exclusiveMinimum" not in result
        assert result["type"]        == "integer"
        assert result["description"] == "Activity ID"

    def test_removes_additional_properties(self):
        schema = {"type": "object", "additionalProperties": False, "properties": {"x": {"type": "string"}}}
        result = _clean_schema_for_gemini(schema)
        assert "additionalProperties" not in result
        assert "properties" in result

    def test_keeps_required(self):
        schema = {"type": "object", "required": ["date"], "properties": {}}
        result = _clean_schema_for_gemini(schema)
        assert result["required"] == ["date"]

    def test_keeps_enum(self):
        schema = {"type": "string", "enum": ["running", "cycling"]}
        result = _clean_schema_for_gemini(schema)
        assert result["enum"] == ["running", "cycling"]

    def test_recursive_properties_cleaned(self):
        schema = {
            "type": "object",
            "properties": {
                "activityId": {"type": "number", "exclusiveMinimum": 0}
            }
        }
        result = _clean_schema_for_gemini(schema)
        assert "exclusiveMinimum" not in result["properties"]["activityId"]
        assert result["properties"]["activityId"]["type"] == "number"

    def test_nested_items_cleaned(self):
        schema = {
            "type": "array",
            "items": {"type": "number", "exclusiveMinimum": 0, "description": "value"}
        }
        result = _clean_schema_for_gemini(schema)
        assert "exclusiveMinimum" not in result["items"]
        assert result["items"]["type"] == "number"

    def test_empty_schema(self):
        assert _clean_schema_for_gemini({}) == {}


# ─── _GeminiCompletions._parse ────────────────────────────────────────────────

class TestGeminiCompletionsParse:
    """
    Tests de la capa de parsing sin llamadas reales a la API.
    _parse() solo accede a response.candidates[0].content.parts
    y a response.usage_metadata — ambos se mockean con MagicMock.
    """

    def _make_gemini(self) -> _GeminiCompletions:
        """Instancia _GeminiCompletions omitiendo __init__ (que requiere google-genai)."""
        gc = object.__new__(_GeminiCompletions)
        gc._api_key = "fake-key"
        return gc

    def _make_response(self, parts, usage_metadata=None):
        content   = MagicMock()
        content.parts = parts
        candidate = MagicMock()
        candidate.content = content
        response  = MagicMock()
        response.candidates    = [candidate]
        response.usage_metadata = usage_metadata
        return response

    # --- respuesta de texto ---

    def test_text_response_sets_content(self):
        part = MagicMock(spec=["text", "function_call"])
        part.text           = "Hola, soy tu entrenador."
        part.function_call  = None
        response = self._make_response([part])

        result = self._make_gemini()._parse(response)

        msg = result.choices[0].message
        assert msg.content    == "Hola, soy tu entrenador."
        assert msg.tool_calls is None

    def test_multi_text_parts_concatenated(self):
        p1 = MagicMock(spec=["text", "function_call"]); p1.text = "Parte 1 "; p1.function_call = None
        p2 = MagicMock(spec=["text", "function_call"]); p2.text = "Parte 2";  p2.function_call = None
        response = self._make_response([p1, p2])

        result = self._make_gemini()._parse(response)
        assert result.choices[0].message.content == "Parte 1 Parte 2"

    # --- respuesta con function call ---

    def test_function_call_sets_tool_calls(self):
        fn_call      = MagicMock()
        fn_call.name = "get_daily_steps"
        fn_call.args = {"date": "2026-06-17"}

        part               = MagicMock()
        part.function_call = fn_call

        with patch("agent.trainer_agent.update_gemini_daily_usage"):
            response = self._make_response([part])
            result   = self._make_gemini()._parse(response)

        msg = result.choices[0].message
        assert msg.tool_calls is not None
        assert msg.tool_calls[0].function.name == "get_daily_steps"
        args = json.loads(msg.tool_calls[0].function.arguments)
        assert args["date"] == "2026-06-17"

    def test_function_call_id_generated(self):
        fn_call      = MagicMock()
        fn_call.name = "get_body_battery"
        fn_call.args = {}
        part               = MagicMock()
        part.function_call = fn_call

        with patch("agent.trainer_agent.update_gemini_daily_usage"):
            result = self._make_gemini()._parse(self._make_response([part]))

        assert result.choices[0].message.tool_calls[0].id.startswith("gcall_")

    # --- sin usage_metadata ---

    def test_usage_none_when_no_metadata(self):
        part = MagicMock(spec=["text", "function_call"])
        part.text          = "ok"
        part.function_call = None
        response = self._make_response([part], usage_metadata=None)

        result = self._make_gemini()._parse(response)
        assert result.usage is None


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
        with patch("agent.trainer_agent.call_tool", return_value=fake_response):
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
        with patch("agent.trainer_agent.call_tool", return_value=fake_response):
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
        with patch("agent.trainer_agent.call_tool", return_value=fake_response):
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
        with patch("agent.trainer_agent.call_tool", return_value=fake_response):
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
        with patch("agent.trainer_agent.call_tool", return_value=fake_response):
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
        with patch("agent.trainer_agent.call_tool", return_value=fake_response):
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
        with patch("agent.trainer_agent.call_tool", return_value=fake_response):
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
        with patch("agent.trainer_agent.call_tool", return_value=fake_response):
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
        with patch("agent.trainer_agent.call_tool", return_value=fake_response):
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

        with patch("agent.trainer_agent.call_tool", side_effect=_fake_call_tool):
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

        with patch("agent.trainer_agent.call_tool", side_effect=_fake_call_tool):
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
        assert _is_activity_in_last_48h(activity)

    def test_is_activity_in_last_48h_true_for_recent_day_snake_case(self):
        today = date.today().isoformat()
        activity = {"start_time_local": f"{today}T08:00:00.0"}
        assert _is_activity_in_last_48h(activity)

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
        assert "Motor determinista (día N): reducir" in out
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
        from agent.trainer_agent import TrainerAgent
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

        with patch("agent.trainer_agent.call_tool", side_effect=_fake_call_tool):
            snapshot = await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert snapshot["window_hours"] == 48
        assert snapshot["body_battery"]["summary"].startswith("hoy=")
        assert snapshot["trainings"]
        bb_calls = [args for name, args in captured_calls if name == "get_body_battery"]
        assert len(bb_calls) == 2
        assert all("start_date" in args and "end_date" in args for args in bb_calls)
        # get_activities_by_date debe haberse llamado con start_date y end_date
        hist_calls = [args for name, args in captured_calls if name == "get_activities_by_date"]
        assert len(hist_calls) == 1
        assert "start_date" in hist_calls[0] and "end_date" in hist_calls[0]

    @pytest.mark.asyncio
    async def test_collect_startup_snapshot_fallback_when_by_date_unavailable(self):
        """Si get_activities_by_date lanza excepción, el snapshot sigue funcionando."""
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent.call_tool", side_effect=_fake_call_tool):
            snapshot = await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert snapshot["window_hours"] == 48
        assert "load_fatigue" in snapshot

    @pytest.mark.asyncio
    async def test_build_startup_status_markdown_uses_training_plan_not_goals(self):
        from agent.trainer_agent import TrainerAgent

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


class TestComputeAndPersistLoadMetrics:
    @pytest.mark.asyncio
    async def test_force_full_recalc_fetches_full_window_for_new_user(self):
        from agent.trainer_agent import TrainerAgent

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
             patch("agent.trainer_agent.call_tool", new=AsyncMock(return_value="")), \
             patch("agent.trainer_agent._fetch_activities_for_load_calc", side_effect=_fake_fetch), \
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
        from agent.trainer_agent import TrainerAgent

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
             patch("agent.trainer_agent._fetch_activities_for_load_calc", new=AsyncMock()) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch.object(TrainerAgent, "_apply_series_to_profile") as apply_mock:
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert fetch_mock.await_count >= 2
        assert any(
            c.args[1] == today.isoformat() and c.args[2] == today.isoformat()
            for c in fetch_mock.await_args_list
        )
        upsert_mock.assert_not_called()
        apply_mock.assert_called_once_with(existing_series, today)

    @pytest.mark.asyncio
    async def test_existing_user_up_to_date_refreshes_today_when_new_activity_detected(self):
        import agent.trainer_agent as trainer_mod
        from agent.trainer_agent import TrainerAgent

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
             patch("agent.trainer_agent._fetch_activities_for_load_calc", side_effect=_fake_fetch) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch("agent.trainer_agent.call_tool", new=AsyncMock(return_value="")), \
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
        from agent.trainer_agent import TrainerAgent

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
             patch("agent.trainer_agent._fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch("agent.trainer_agent.call_tool", new=AsyncMock(return_value="")), \
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
        from agent.trainer_agent import TrainerAgent

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
            {
                "date": today_iso,
                "tss": 20.0,
                "atl": 46.0,
                "ctl": 35.0,
                "tsb": -11.0,
                "activities_count": 1,
            },
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {"load_metrics": {"formula_version": trainer_mod._TSS_FORMULA_VERSION}}

        async def _fake_fetch(_session, start_date, end_date):
            if start_date == today_iso and end_date == today_iso:
                return []
            if start_date == yesterday_iso and end_date == yesterday_iso:
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
            if start_date == yesterday_iso and end_date == today_iso:
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
             patch("agent.trainer_agent._fetch_activities_for_load_calc", side_effect=_fake_fetch) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch("agent.trainer_agent.call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile") as apply_mock:
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert any(
            c.args[1] == yesterday_iso and c.args[2] == yesterday_iso
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
        from agent.trainer_agent import TrainerAgent

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
            {
                "date": today_iso,
                "tss": 10.0,
                "atl": 46.0,
                "ctl": 35.0,
                "tsb": -11.0,
                "activities_count": 1,
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
                    }
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
                    }
                ]
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch("agent.trainer_agent._fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch("agent.trainer_agent.call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows
        # Debe iniciar en ayer, no en hoy, aunque performance_params_updated_at == hoy.
        assert persisted_rows[0]["date"] == yesterday_iso

    @pytest.mark.asyncio
    async def test_existing_user_up_to_date_refreshes_from_two_days_ago_when_missing_activity(self):
        import agent.trainer_agent as trainer_mod
        from agent.trainer_agent import TrainerAgent

        today = date.today()
        today_iso = today.isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()
        two_days_ago_iso = (today - timedelta(days=2)).isoformat()
        existing_series = [
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
                "tss": 15.0,
                "atl": 43.5,
                "ctl": 33.8,
                "tsb": -9.7,
                "activities_count": 1,
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
            if start_date == yesterday_iso and end_date == yesterday_iso:
                return []
            if start_date == two_days_ago_iso and end_date == two_days_ago_iso:
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
             patch("agent.trainer_agent._fetch_activities_for_load_calc", side_effect=_fake_fetch) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch("agent.trainer_agent.call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert any(
            c.args[1] == two_days_ago_iso and c.args[2] == two_days_ago_iso
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
        with patch("agent.trainer_agent.call_tool", new=AsyncMock(side_effect=[first_page, second_page])):
            out = await _fetch_activities_for_load_calc(session, yesterday_iso, today_iso)

        assert len(out) == 1
        assert int(out[0].get("activityId") or 0) == 2


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


class TestSessionTssEstimation:
    def test_compute_load_fatigue_metrics_returns_latest_and_ranges(self):
        today = date.today()
        activities = []
        for idx in range(21):
            d = (today - timedelta(days=idx)).isoformat()
            activities.append(
                {
                    "startTimeLocal": f"{d}T07:00:00.0",
                    "trainingLoad": 40 + (idx % 4) * 10,
                }
            )

        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={"load": []},
            profile={},
            days_window=42,
        )

        assert out is not None
        assert out["latest"]["date"] == today.isoformat()
        assert "atl" in out["latest"] and "ctl" in out["latest"] and "tsb" in out["latest"]
        assert "tsb_low" in out["ranges"] and "tsb_high" in out["ranges"]
        assert out["status"] in {"overload", "fatigue_high", "ready", "neutral"}

    def test_compute_load_fatigue_metrics_detects_overload(self):
        today = date.today()
        trend = []
        for idx in range(14):
            d = (today - timedelta(days=idx)).isoformat()
            trend.append({"date": d, "trainingLoad": 130})

        out = _compute_load_fatigue_metrics(
            activities=[],
            trend_payload={"points": trend},
            profile={},
            days_window=28,
        )

        assert out is not None
        assert out["flags"]["sustained_overload"] or out["status"] in {"overload", "fatigue_high"}

    def test_compute_load_fatigue_metrics_flags_weekly_spike_over_20_percent(self):
        import agent.trainer_agent as ta

        class _FakeDate:
            @staticmethod
            def today():
                from datetime import date as _Date

                # Domingo fijo para garantizar semana completa (lunes->domingo)
                return _Date(2026, 8, 16)

            @staticmethod
            def fromisoformat(value):
                from datetime import date as _Date

                return _Date.fromisoformat(value)

        with patch.object(ta, "date", _FakeDate):
            today = _FakeDate.today()
            activities = []
            for idx in range(14):
                d = (today - timedelta(days=idx)).isoformat()
                # Semana actual (0..6): 35 TSS/día; semana anterior (7..13): 20 TSS/día
                load = 35 if idx <= 6 else 20
                activities.append(
                    {
                        "startTimeLocal": f"{d}T07:00:00.0",
                        "trainingLoad": load,
                    }
                )

            out = _compute_load_fatigue_metrics(
                activities=activities,
                trend_payload={"load": []},
                profile={},
                days_window=28,
            )

        assert out is not None
        assert out["flags"]["weekly_spike_alert"] is True
        assert float(out["weekly"]["previous_tss"]) > 0.0
        assert float(out["weekly"]["spike_delta_pct"]) > 20.0
        if out["status"] in {"ready", "neutral"}:
            assert "Spike semanal >20%" in str(out.get("recommendation") or "")

    def test_format_load_fatigue_summary_handles_missing_data(self):
        assert _format_load_fatigue_summary(None) == "sin datos suficientes"

    # ── Tests por deporte ─────────────────────────────────────────────────────

    def test_resolve_sport_model_cfg_trail_running_has_larger_atl_tau(self):
        profile = {"goals": {"primary": "trail running"}}
        cfg = _resolve_sport_model_cfg(profile)
        assert cfg["atl_tau_days"] == 8

    def test_resolve_sport_model_cfg_ciclismo_has_larger_ctl_tau(self):
        profile = {"goals": {"primary": "ciclismo"}}
        cfg = _resolve_sport_model_cfg(profile)
        assert cfg["ctl_tau_days"] == 45

    def test_resolve_sport_model_cfg_unknown_sport_falls_back_to_running(self):
        profile = {"goals": {"primary": "patinaje"}}
        cfg = _resolve_sport_model_cfg(profile)
        running_cfg = _resolve_sport_model_cfg({"goals": {"primary": "running"}})
        assert cfg["atl_tau_days"] == running_cfg["atl_tau_days"]

    def test_resolve_sport_model_cfg_manual_override_wins(self):
        profile = {
            "goals": {"primary": "running"},
            "load_metrics": {"model": {"atl_tau_days": 10}},
        }
        cfg = _resolve_sport_model_cfg(profile)
        assert cfg["atl_tau_days"] == 10

    def test_compute_load_fatigue_metrics_trail_uses_sport_tau(self):
        today = date.today()
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=i)).isoformat()}T08:00:00.0", "trainingLoad": 60}
            for i in range(20)
        ]
        profile = {"goals": {"primary": "trail running"}}
        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=profile,
            days_window=28,
        )
        assert out is not None
        assert out["model"]["sport"] == "trail running"
        assert out["model"]["atl_tau_days"] == 8

    def test_compute_load_fatigue_metrics_ciclismo_uses_sport_tau(self):
        today = date.today()
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=i)).isoformat()}T08:00:00.0", "trainingLoad": 80}
            for i in range(20)
        ]
        profile = {"goals": {"primary": "ciclismo"}}
        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=profile,
            days_window=28,
        )
        assert out is not None
        assert out["model"]["sport"] == "ciclismo"
        assert out["model"]["ctl_tau_days"] == 45

    def test_abs_overload_triggers_when_tsb_below_floor_trail(self):
        """TSB <= tsb_abs_floor (-35 para trail) debe forzar OVERLOAD aunque el percentil no lo detecte."""
        today = date.today()
        # Serie de carga extrema sostenida que lleva TSB muy por debajo de -35
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=i)).isoformat()}T08:00:00.0",
             "trainingLoad": 130}
            for i in range(30)
        ]
        profile = {"goals": {"primary": "trail running"}}
        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=profile,
            days_window=56,
        )
        assert out is not None
        assert out["ranges"]["tsb_abs_floor"] == -35.0
        if out["latest"]["tsb"] <= -35.0:
            assert out["status"] == "overload"
            assert out["flags"]["abs_overload"] is True

    def test_abs_overload_running_floor_is_minus_30(self):
        cfg = _resolve_sport_model_cfg({"goals": {"primary": "running"}})
        assert cfg["tsb_abs_floor"] == -30.0

    def test_abs_overload_trail_floor_is_minus_35(self):
        cfg = _resolve_sport_model_cfg({"goals": {"primary": "trail running"}})
        assert cfg["tsb_abs_floor"] == -35.0

    def test_sustained_overload_boundary_le_fix(self):
        """Cuando tsb_now == tsb_low (valor == percentil p15), debe detectarse como overload.
        Antes del fix se usaba < estricto y el caso límite se escapaba."""
        today = date.today()
        # Serie con carga alta los últimos 7 días para que p15 == valor actual (TSB muy negativo)
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=i)).isoformat()}T08:00:00.0",
             "trainingLoad": 110 if i < 14 else 55}
            for i in range(42)
        ]
        profile = {"goals": {"primary": "trail running"}}
        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=profile,
            days_window=56,
        )
        assert out is not None
        # Con carga de 110 durante 14 días consecutivos, el status no debe ser "neutral" ni "ready"
        assert out["status"] in {"overload", "fatigue_high"}

    # ── Tests de warming_up ───────────────────────────────────────────────────

    def test_warming_up_flag_true_with_few_training_days(self):
        """Con < 21 días de entrenamiento real, warming_up debe ser True."""
        today = date.today()
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=i)).isoformat()}T08:00:00.0",
             "trainingLoad": 50}
            for i in range(10)  # solo 10 días
        ]
        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile={"goals": {"primary": "trail running"}},
            days_window=28,
        )
        assert out is not None
        assert out["warming_up"] is True
        assert out["flags"]["warming_up"] is True
        assert out["warming_up_days_remaining"] > 0

    def test_warming_up_flag_false_with_enough_history(self):
        """Con >= 21 días de entrenamiento real, warming_up debe ser False."""
        today = date.today()
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=i)).isoformat()}T08:00:00.0",
             "trainingLoad": 50}
            for i in range(25)  # 25 días
        ]
        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile={"goals": {"primary": "running"}},
            days_window=42,
        )
        assert out is not None
        assert out["warming_up"] is False
        assert out["warming_up_days_remaining"] == 0

    def test_proactive_markdown_shows_calibration_notice_when_warming_up(self):
        """El estado proactivo debe incluir nota ⚙️ cuando warming_up=True."""
        load_fatigue = {
            "latest": {"tss": 50.0, "atl": 30.0, "ctl": 10.0, "tsb": -20.0},
            "weekly": {"current_tss": 300.0},
            "ranges": {"tsb_low": -22.0, "tsb_high": -5.0, "atl_high": 40.0, "tsb_abs_floor": -35.0},
            "recommendation": "Mantén carga aeróbica controlada.",
            "action": "carga estable",
            "warming_up": True,
            "warming_up_days_remaining": 11,
            "days_with_load": 10,
        }
        snapshot = {
            "body_battery": {"summary": "sin datos"},
            "hrv": {"summary": "sin datos"},
            "sleep": {"summary": "sin datos"},
            "load_fatigue": load_fatigue,
            "trainings": [],
        }
        out = _build_proactive_status_markdown(snapshot)
        assert "calibracion" in out.lower() or "calibración" in out.lower()
        assert "⚙️" in out

    def test_proactive_markdown_no_calibration_notice_when_not_warming_up(self):
        """Sin warming_up, la nota de calibración no debe aparecer."""
        load_fatigue = {
            "latest": {"tss": 50.0, "atl": 42.0, "ctl": 38.0, "tsb": -4.0},
            "weekly": {"current_tss": 310.0},
            "ranges": {"tsb_low": -12.0, "tsb_high": 4.0, "atl_high": 55.0, "tsb_abs_floor": -35.0},
            "recommendation": "Puedes mantener sesión de calidad.",
            "action": "buena disponibilidad",
            "warming_up": False,
            "warming_up_days_remaining": 0,
            "days_with_load": 30,
        }
        snapshot = {
            "body_battery": {"summary": "sin datos"},
            "hrv": {"summary": "sin datos"},
            "sleep": {"summary": "sin datos"},
            "load_fatigue": load_fatigue,
            "trainings": [],
        }
        out = _build_proactive_status_markdown(snapshot)
        assert "⚙️" not in out

    def test_load_trend_table_shows_warmup_note_when_ctl_low(self):
        """La tabla /carga debe mostrar nota cuando CTL de primera fila < 15."""
        today = date.today()
        series = []
        atl = ctl = 0.0
        for i in range(28, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            tss = 50.0 if i % 7 != 0 else 0.0
            atl = atl + (tss - atl) / 8
            ctl = ctl + (tss - ctl) / 42
            series.append({"date": d, "tss": round(tss, 1), "atl": round(atl, 1),
                           "ctl": round(ctl, 1), "tsb": round(ctl - atl, 1)})
        out = _build_load_trend_table(series, mode="weeks")
        # CTL empieza < 15 → debe aparecer la nota
        assert "⚙️" in out

    # ── Tests de _build_load_trend_table ─────────────────────────────────────

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


class TestSportSpecificLoadProfiles:
    @staticmethod
    def _build_fake_activities(days: int, default_tss: float, high_tss_days: int = 0, high_tss: float = 0.0) -> list[dict]:
        """Genera actividades ficticias con trainingLoad diario para pruebas del modelo."""
        today = date.today()
        out: list[dict] = []
        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            tss = high_tss if i < high_tss_days else default_tss
            out.append(
                {
                    "activityId": 900000 + i,
                    "startTimeLocal": f"{d}T08:00:00.0",
                    "trainingLoad": float(tss),
                }
            )
        return out

    def test_running_profile_matches_tp_like_defaults(self):
        profile = {"goals": {"primary": "running"}}
        activities = self._build_fake_activities(days=56, default_tss=55.0)

        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=profile,
            days_window=56,
        )

        assert out is not None
        assert out["model"]["sport"] == "running"
        assert out["model"]["atl_tau_days"] == 7
        assert out["model"]["ctl_tau_days"] == 42
        assert out["ranges"]["tsb_abs_floor"] == -30.0
        assert out["latest"]["atl"] > 0 and out["latest"]["ctl"] > 0

    def test_trail_profile_uses_slower_atl_and_deeper_tsb_floor(self):
        activities = self._build_fake_activities(days=56, default_tss=55.0)
        running_profile = {"goals": {"primary": "running"}}
        trail_profile = {"goals": {"primary": "trail running"}}

        run_out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=running_profile,
            days_window=56,
        )
        trail_out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=trail_profile,
            days_window=56,
        )

        assert run_out is not None and trail_out is not None
        assert trail_out["model"]["sport"] == "trail running"
        assert trail_out["model"]["atl_tau_days"] == 8
        assert trail_out["model"]["ctl_tau_days"] == 42
        assert trail_out["ranges"]["tsb_abs_floor"] == -35.0
        # Misma carga, ATL trail debe responder algo más lento (igual o menor que running).
        assert float(trail_out["latest"]["atl"]) <= float(run_out["latest"]["atl"]) + 0.2

    def test_triathlon_profile_uses_longer_ctl_and_flags_recent_overload(self):
        # Bloque agudo reciente para simular sobrecarga en un atleta de triatlón.
        activities = self._build_fake_activities(
            days=56,
            default_tss=45.0,
            high_tss_days=10,
            high_tss=140.0,
        )
        tri_profile = {"goals": {"primary": "triatlón"}}

        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=tri_profile,
            days_window=56,
        )

        assert out is not None
        assert out["model"]["sport"] == "triatlón"
        assert out["model"]["atl_tau_days"] == 7
        assert out["model"]["ctl_tau_days"] == 45
        assert out["ranges"]["tsb_abs_floor"] == -35.0
        assert out["status"] in {"fatigue_high", "overload"}

class TestLoadFatigueModel:
    # ── Tests de _estimate_session_tss ────────────────────────────────────────

    def test_estimate_tss_priority1_uses_garmin_training_load(self):
        """Si la actividad tiene trainingLoad, se usa directamente."""
        act = {"trainingLoad": 95.0, "averageHR": 155, "duration": 3600}
        tss, label = _estimate_session_tss(act)
        assert tss == 95.0
        assert label == "TSS"

    def test_estimate_tss_priority1_allows_over_500(self):
        act = {"trainingLoad": 999.0}
        tss, _ = _estimate_session_tss(act)
        assert tss == 999.0

    def test_estimate_tss_priority2_hr_based_z2(self):
        """Sin trainingLoad, Z2 (avg_hr ~140, hr_rest 50, hr_max 185) → TSS razonable."""
        act = {"averageHR": 140, "maxHR": 185, "duration": 3600}
        tss, _ = _estimate_session_tss(act)
        # Z2 1h → esperamos entre 50 y 80 TSS
        assert 50 <= tss <= 80, f"TSS Z2 1h inesperado: {tss}"

    def test_estimate_tss_priority2_hr_based_z4_higher_than_z2(self):
        """Z4 debe producir más TSS que Z2 para la misma duración."""
        z2, _ = _estimate_session_tss({"averageHR": 140, "maxHR": 185, "duration": 3600})
        z4, _ = _estimate_session_tss({"averageHR": 165, "maxHR": 185, "duration": 3600})
        assert z4 > z2

    def test_estimate_tss_priority2_uses_max_hr_from_activity(self):
        """max_hr de la actividad se usa como referencia; si no, se asume 185."""
        with_max, _ = _estimate_session_tss({"averageHR": 150, "maxHR": 175, "duration": 3600})
        without_max, _ = _estimate_session_tss({"averageHR": 150, "duration": 3600})
        # Con max_hr real del reloj (175) vs estimado (185): la %HRR difiere → TSS difiere
        assert with_max != without_max

    def test_estimate_tss_priority2_uses_profile_hr_when_activity_has_no_max(self):
        act = {"averageHR": 150, "duration": 3600}
        default_tss, _ = _estimate_session_tss(act)
        profile_tss, _ = _estimate_session_tss(act, hr_rest_bpm=60, hr_max_bpm=190)
        assert profile_tss != default_tss
        assert profile_tss < default_tss

    def test_estimate_tss_priority3_training_effect_fallback(self):
        """Sin HR ni trainingLoad, usa Training Effect aeróbico."""
        act = {"aerobicTrainingEffect": 3.0, "duration": 3600}
        tss, _ = _estimate_session_tss(act)
        # TE 3.0 → IF ~0.77 → 1h → ~59 TSS
        assert 45 <= tss <= 75, f"TSS con TE=3.0 inesperado: {tss}"

    def test_estimate_tss_priority4_default_if_no_data(self):
        """Sin ningún dato de intensidad, usa IF=0.68 → 1h → ~46 TSS."""
        act = {"duration": 3600}
        tss, _ = _estimate_session_tss(act)
        expected = 0.68 ** 2 * 100
        assert abs(tss - expected) < 1.0

    def test_estimate_tss_other_modalities_prioritize_hr_zones(self):
        act = {"type": "rowing", "duration": 3600, "averageHR": 150, "maxHR": 185}
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 110,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
                {
                    "zoneNumber": 4,
                    "secsInZone": 0,
                    "minHeartRateIn": 152,
                    "maxHeartRateIn": 165,
                },
                {
                    "zoneNumber": 5,
                    "secsInZone": 0,
                    "minHeartRateIn": 166,
                    "maxHeartRateIn": 185,
                },
            ]
        )

        tss, label = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw, hr_rest_bpm=50, hr_max_bpm=185)

        assert label == "hrTSS"
        # Con 1h íntegra en Z1, el mapeo por HRR da IF≈0.725 => ~52.56 TSS
        assert abs(tss - 52.56) < 0.3

    def test_estimate_tss_strength_prefers_hr_then_rpe(self):
        act_hr = {"type": "strength_training", "averageHR": 145, "maxHR": 180, "duration": 3600, "rpe": 9}
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
            ]
        )

        tss_zones, label_zones = _estimate_session_tss(act_hr, hr_zones_raw=hr_zones_raw)
        tss_hr, label_hr = _estimate_session_tss(act_hr)

        assert label_zones == "hrTSS"
        assert abs(tss_zones - 56.25) < 0.05
        assert label_hr == "TSS"
        # RPE 9 => fuerza maxima/potencia (IF 0.80) => ~64 TSS en 1h.
        assert abs(tss_hr - 64.0) < 0.1

        act_rpe = {"type": "strength_training", "duration": 3600, "rpe": "7-8"}
        tss_rpe, label_rpe = _estimate_session_tss(act_rpe)
        assert label_rpe == "TSS"
        # "7-8" => RPE medio 7.5 => bucket intenso (IF 0.80) en el método IF.
        assert abs(tss_rpe - 64.0) < 0.1

    def test_estimate_tss_strength_rpe_fraction_uses_numerator(self):
        act_fraction = {"type": "strength_training", "duration": 3600, "rpe": "7/10"}
        act_plain = {"type": "strength_training", "duration": 3600, "rpe": 7}
        tss_fraction, label_fraction = _estimate_session_tss(act_fraction)
        tss_plain, label_plain = _estimate_session_tss(act_plain)

        assert label_fraction == "TSS"
        assert label_plain == "TSS"
        assert abs(tss_fraction - tss_plain) < 0.01

    def test_estimate_tss_strength_sparse_hr_zones_falls_back_to_hr(self):
        act = {
            "type": "strength_training",
            "duration": 3600,
            "averageHR": 145,
            "maxHR": 180,
        }
        # Solo 1 minuto de zonas para una sesión de 60 minutos: cobertura insuficiente.
        hr_zones_raw = json.dumps([
            {
                "zoneNumber": 1,
                "secsInZone": 60,
                "minHeartRateIn": 115,
                "maxHeartRateIn": 125,
            }
        ])

        tss, label = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        assert label == "TSS"
        # Sin cobertura suficiente de zonas, usa IF conservador por defecto (0.56).
        assert abs(tss - 31.36) < 0.2

    def test_estimate_tss_strength_manual_if_override(self):
        act = {
            "type": "strength_training",
            "duration": 3600,
            "gym_if": 0.85,
        }

        tss, label = _estimate_session_tss(act)

        assert label == "TSS"
        assert abs(tss - 72.25) < 0.1

    def test_estimate_tss_strength_light_session_keyword(self):
        act = {
            "type": "strength_training",
            "duration": 3600,
            "name": "Movilidad y tonificación ligera",
        }

        tss, label = _estimate_session_tss(act)

        assert label == "TSS"
        assert abs(tss - 25.0) < 0.1

    def test_estimate_tss_running_prefers_threshold_pace_over_hr(self):
        act = {
            "type": "running",
            "duration": 3600,
            "distance": 10000,
            "averageHR": 160,
            "maxHR": 185,
        }
        tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)
        # 10k en 1h => 6:00/km. Umbral 5:00/km => IF=0.833... => ~69.4 TSS
        assert label == "TSS"
        assert abs(tss - 69.4) < 1.0

    def test_estimate_tss_running_prefers_effective_pace_when_available(self):
        act = {
            "type": "running",
            "duration": 3600,
            "distance": 10000,
            "averagePace": "6:00",
            "normalizedPace": "5:00",
        }
        tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)
        # Si usa normalizedPace=5:00 con umbral 5:00, IF=1.0 => 100 TSS
        assert label == "TSS"
        assert abs(tss - 100.0) < 1.0

    def test_estimate_tss_running_uses_distance_meters_for_pace_fallback(self):
        act = {
            "type": "running",
            "duration_seconds": 3600,
            "distance_meters": 10000,
        }
        tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)
        assert label == "TSS"
        # 10k en 1h => 6:00/km. Umbral 5:00/km => IF=0.833... => ~69.4 TSS
        assert abs(tss - 69.4) < 1.0

    def test_extract_threshold_pace_from_lactate_speed_mps_scaled_value(self):
        pace = _extract_threshold_pace_sec_per_km({"lactate_threshold_speed_mps": 0.4083})
        assert pace is not None
        # 0.4083*10 m/s => ~4:05/km
        assert 230.0 <= pace <= 260.0

    def test_estimate_tss_running_uses_higher_if_ceiling_than_trail(self):
        running_act = {
            "type": "running",
            "duration": 3600,
            "averagePace": "3:20",  # 3:20 min/km
        }
        trail_act = {
            "type": "hiking",
            "duration": 3600,
            "averagePace": "3:20",  # 3:20 min/km
        }

        running_tss, running_label = _estimate_session_tss(
            running_act,
            running_threshold_pace_sec_per_km=300.0,
        )
        trail_tss, trail_label = _estimate_session_tss(
            trail_act,
            running_threshold_pace_sec_per_km=300.0,
        )


        def test_estimate_tss_running_interval_exam_always_on_increases_tss(self):
            act = {
                "type": "running",
                "duration_seconds": 5278.944,
                "distance_meters": 16168.79,
                "avg_speed_mps": 3.063,
                "max_speed_mps": 3.882,
                "lap_count": 20,
                "has_splits": True,
                "vigorous_intensity_minutes": 85,
                "workout_rpe": 80,
                "training_effect_label": "LACTATE_THRESHOLD",
                "name": "Fartlek cuestas",
                "description": "6 x 4' Z4 + recuperacion",
            }

            tss_examined, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)

            # Baseline (sin ajuste intervalico): IF=300/(5278.944/(16.16879)) ~= 0.919
            # El examen intervalico debe elevar la carga por encima de ese baseline.
            baseline_if = 300.0 / (5278.944 / 16.16879)
            baseline_tss = (5278.944 / 3600.0) * (baseline_if ** 2) * 100.0

            assert label == "TSS"
            assert tss_examined > baseline_tss + 1.0

        def test_estimate_tss_running_rodaje_not_artificially_inflated(self):
            act = {
                "type": "running",
                "duration": 3600,
                "distance": 10000,
                "avg_speed_mps": 3.03,
                "max_speed_mps": 3.33,
                "lap_count": 17,
                "has_splits": True,
                "workout_rpe": 30,
                "training_effect_label": "AEROBIC_BASE",
                "name": "Rodaje suave",
            }

            tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)
            assert label == "TSS"
            # 10k en 1h => 6:00/km. Umbral 5:00/km => ~69.4 TSS (sin inflado intervalico)
            assert abs(tss - 69.4) < 1.0
        # Running no trail: clamp IF a 1.30 => 1h => 169 TSS
        assert running_label == "TSS"
        assert abs(running_tss - 169.0) < 1.0

        # Hike/walk usa bandas específicas (no clamp de running/trail).
        assert trail_label == "TSS"
        assert abs(trail_tss - 50.41) < 1.0

    def test_estimate_tss_running_fartlek_not_overinflated_vs_pace_baseline(self):
        act = {
            "type": "running",
            "duration_seconds": 4385.93,
            "distance_meters": 15115.87,
            "avg_speed_mps": 3.446,
            "max_speed_mps": 4.647,
            "lap_count": 27,
            "vigorous_intensity_minutes": 70,
            "workout_rpe": 80,
            "training_effect_label": "VO2MAX",
            "name": "Fartlek. 2x5' + 2x4' +4x2'",
        }

        tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)

        baseline_if = 300.0 / (4385.93 / 15.11587)
        baseline_tss = (4385.93 / 3600.0) * (baseline_if ** 2) * 100.0

        assert label == "TSS"
        # Debe mantenerse controlado frente al baseline por ritmo (sin sobreinflar).
        assert tss >= baseline_tss
        assert tss <= baseline_tss + 6.0

    def test_estimate_tss_running_series_keeps_interval_uplift(self):
        act = {
            "type": "running",
            "duration_seconds": 3475.483,
            "distance_meters": 10776.09,
            "avg_speed_mps": 3.101,
            "max_speed_mps": 4.843,
            "lap_count": 38,
            "vigorous_intensity_minutes": 49,
            "workout_rpe": 60,
            "training_effect_label": "VO2MAX",
            "name": "Series en el C.A.R. 8x500 REC 1'30",
        }

        tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)

        baseline_if = 300.0 / (3475.483 / 10.77609)
        baseline_tss = (3475.483 / 3600.0) * (baseline_if ** 2) * 100.0

        assert label == "TSS"
        # Mantener uplift en series para no perder el ajuste del caso 8x500.
        assert tss > baseline_tss + 7.0

    def test_classify_running_session_rodaje(self):
        act = {
            "type": "running",
            "avg_speed_mps": 3.10,
            "max_speed_mps": 3.35,
            "lap_count": 10,
            "vigorous_intensity_minutes": 12,
            "workout_rpe": 30,
            "training_effect_label": "AEROBIC_BASE",
            "name": "Rodaje suave zona 2",
        }
        cls = _classify_running_session_with_confidence(act)
        assert cls["session_kind"] == "rodaje"
        assert cls["confidence"] in {"medium", "high"}

    def test_classify_running_session_fartlek(self):
        act = {
            "type": "running",
            "avg_speed_mps": 3.40,
            "max_speed_mps": 4.55,
            "lap_count": 22,
            "vigorous_intensity_minutes": 55,
            "workout_rpe": 75,
            "training_effect_label": "VO2MAX",
            "name": "Fartlek 6x3' rec 2'",
        }
        cls = _classify_running_session_with_confidence(act)
        assert cls["session_kind"] == "fartlek"
        assert cls["scores"]["fartlek"] >= cls["scores"]["series"]

    def test_classify_running_session_series(self):
        act = {
            "type": "running",
            "avg_speed_mps": 3.05,
            "max_speed_mps": 4.80,
            "lap_count": 34,
            "vigorous_intensity_minutes": 48,
            "workout_rpe": 65,
            "training_effect_label": "VO2MAX",
            "name": "Series 10x400 rec 1'",
            "description": "Trabajo de reps en pista",
        }
        cls = _classify_running_session_with_confidence(act)
        assert cls["session_kind"] == "series"
        assert cls["scores"]["series"] > cls["scores"]["rodaje"]

    def test_classify_running_session_rodaje_from_name_only(self):
        act = {
            "type": "running",
            "name": "Rodaje suave Z2 60'",
            "training_effect_label": "AEROBIC_BASE",
        }
        cls = _classify_running_session_with_confidence(act)
        assert cls["session_kind"] == "rodaje"

    def test_estimate_tss_trail_prefers_hr_then_threshold_then_rpe(self):
        act_hr = {
            "type": "trail_running",
            "duration": 7200,
            "distance": 14000,
            "averageHR": 150,
            "maxHR": 180,
            "rpe": 9,
        }
        tss_hr, label_hr = _estimate_session_tss(act_hr, running_threshold_pace_sec_per_km=320.0)
        assert label_hr == "hrTSS"
        assert tss_hr > 0

        act_pace = {"type": "hiking", "duration": 7200, "distance": 14000}
        tss_pace, label_pace = _estimate_session_tss(act_pace, running_threshold_pace_sec_per_km=320.0)
        assert label_pace == "TSS"
        assert tss_pace > 0

        act_hike_hr = {
            "type": "hiking",
            "duration": 7200,
            "distance": 14000,
            "averageHR": 130,
            "maxHR": 165,
        }
        tss_hike_hr, label_hike_hr = _estimate_session_tss(act_hike_hr, running_threshold_pace_sec_per_km=320.0)
        assert label_hike_hr == "hrTSS"
        assert tss_hike_hr > 0

        act_rpe = {"type": "walking", "duration": 3600, "rpe": 6}
        tss_rpe, label_rpe = _estimate_session_tss(act_rpe)
        assert label_rpe == "TSS"
        assert tss_rpe > 0

    def test_estimate_tss_trail_uses_hr_zones_when_available(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
            ]
        )

        tss_zones, label_zones = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)
        tss_avg, label_avg = _estimate_session_tss(act)

        assert label_zones == "hrTSS"
        assert label_avg == "hrTSS"
        assert tss_zones > 0
        assert tss_zones < tss_avg

    def test_estimate_tss_trail_hr_zones_applies_calibration_factor(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
            ]
        )

        tss_zones, label_zones = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        assert label_zones == "hrTSS"
        # Base sin calibración: 56.25 TSS (IF=0.75). Con factor 0.72 => 40.50
        assert abs(tss_zones - 40.5) < 0.05

        hike_act = {
            "type": "hiking",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
        }
        tss_hike_zones, label_hike_zones = _estimate_session_tss(hike_act, hr_zones_raw=hr_zones_raw)
        assert label_hike_zones == "hrTSS"
        # En hike/walk se usa banda específica y blend con zonas (sin factor trail).
        assert 45.0 <= tss_hike_zones <= 60.0

    def test_estimate_tss_trail_fast_pace_uses_raw_hr_zones(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
            "averagePaceSecPerKm": 330,
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
            ]
        )

        tss_zones, label_zones = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        assert label_zones == "hrTSS"
        # Base sin calibración: 56.25 TSS (IF=0.75). Para trail rápido se conserva bruto.
        assert abs(tss_zones - 56.25) < 0.05

    def test_estimate_tss_trail_pace_at_6min_keeps_calibration(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
            "averagePaceSecPerKm": 360,
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
            ]
        )

        tss_zones, label_zones = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        assert label_zones == "hrTSS"
        # El criterio es estrictamente menor que 6:00/km; a 6:00/km aplica factor trail.
        assert abs(tss_zones - 40.5) < 0.05

    def test_activity_analysis_block_trail_shows_raw_and_applied_hrtss(self):
        activity_raw = json.dumps(
            {
                "type": "trail_running",
                "duration": 3600,
                "distance": 10000,
                "averageHR": 170,
                "maxHR": 180,
                "name": "Trail test",
            }
        )
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
            ]
        )

        out = _build_activity_analysis_block(activity_raw=activity_raw, hr_zones_raw=hr_zones_raw)

        assert "hrTSS bruto zonas: 56.2" in out
        assert "hrTSS Kairos aplicado: 40.5" in out

    def test_activity_analysis_block_trail_fast_shows_raw_rule_note(self):
        activity_raw = json.dumps(
            {
                "type": "trail_running",
                "duration": 3600,
                "distance": 10000,
                "averagePaceSecPerKm": 330,
                "averageHR": 170,
                "maxHR": 180,
                "name": "Trail rapido",
            }
        )
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
            ]
        )

        out = _build_activity_analysis_block(activity_raw=activity_raw, hr_zones_raw=hr_zones_raw)

        assert "hrTSS bruto zonas: 56.2" in out
        assert "hrTSS Kairos aplicado: 56.2" in out
        assert "Regla trail rapido activa (<6:00/km)" in out

    def test_estimate_tss_walking_easy_band_caps_zones(self):
        act = {
            "type": "walking",
            "duration": 3600,
            "name": "Paseo regenerativo suave",
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 3,
                    "secsInZone": 3600,
                    "minHeartRateIn": 145,
                    "maxHeartRateIn": 155,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 130,
                    "maxHeartRateIn": 144,
                },
                {
                    "zoneNumber": 1,
                    "secsInZone": 0,
                    "minHeartRateIn": 110,
                    "maxHeartRateIn": 129,
                },
            ]
        )

        tss, label = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        assert label == "hrTSS"
        assert 15.0 <= tss <= 25.0

    def test_estimate_tss_walking_power_band(self):
        act = {
            "type": "walking",
            "duration": 3600,
            "name": "Power walking ritmo vivo",
        }

        tss, label = _estimate_session_tss(act)

        assert label == "TSS"
        assert 25.0 <= tss <= 40.0

    def test_estimate_tss_trail_uses_embedded_hr_zones_payload(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
            "heartRateZones": [
                {"zoneNumber": 1, "secsInZone": 3600, "minHeartRateIn": 115, "maxHeartRateIn": 125},
                {"zoneNumber": 2, "secsInZone": 0, "minHeartRateIn": 126, "maxHeartRateIn": 138},
                {"zoneNumber": 3, "secsInZone": 0, "minHeartRateIn": 139, "maxHeartRateIn": 151},
            ],
        }

        tss_embedded, label_embedded = _estimate_session_tss(act)
        tss_avg_only, _ = _estimate_session_tss({
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
        })

        assert label_embedded == "hrTSS"
        assert tss_embedded > 0
        assert tss_embedded < tss_avg_only

    def test_estimate_tss_cycling_prefers_power_ftp_then_hr_zones_then_hr(self):
        act_pow = {
            "type": "cycling",
            "duration": 3600,
            "normalizedPower": 210,
            "averageHR": 160,
            "maxHR": 185,
        }
        tss_pow, label_pow = _estimate_session_tss(act_pow, ftp=250.0)
        assert label_pow == "TSS"
        assert abs(tss_pow - 70.6) < 1.0

        hr_zones_raw = json.dumps(
            [
                {"zoneNumber": 1, "secsInZone": 3600, "minHeartRateIn": 115, "maxHeartRateIn": 125},
                {"zoneNumber": 2, "secsInZone": 0, "minHeartRateIn": 126, "maxHeartRateIn": 138},
                {"zoneNumber": 3, "secsInZone": 0, "minHeartRateIn": 139, "maxHeartRateIn": 151},
            ]
        )

        # Sin FTP (aunque haya potencia), usa zonas FC.
        tss_no_ftp, label_no_ftp = _estimate_session_tss(act_pow, ftp=None, hr_zones_raw=hr_zones_raw)
        tss_no_ftp_hr_only, _ = _estimate_session_tss(act_pow, ftp=None)
        assert label_no_ftp == "hrTSS"
        assert tss_no_ftp > 0
        assert abs(tss_no_ftp - tss_no_ftp_hr_only) > 0.5

        # Sin potencia (aunque haya FTP), usa zonas FC.
        act_no_power = {"type": "cycling", "duration": 3600, "averageHR": 145, "maxHR": 175}
        tss_zones, label_zones = _estimate_session_tss(act_no_power, ftp=250.0, hr_zones_raw=hr_zones_raw)
        tss_no_zones_hr_only, _ = _estimate_session_tss(act_no_power, ftp=250.0)
        assert label_zones == "hrTSS"
        assert tss_zones > 0
        assert abs(tss_zones - tss_no_zones_hr_only) > 0.5

        # Sin zonas, fallback final a FC media.
        act_hr = {"type": "cycling", "duration": 3600, "averageHR": 145, "maxHR": 175}
        tss_hr, label_hr = _estimate_session_tss(act_hr, ftp=250.0)
        assert label_hr == "hrTSS"
        assert tss_hr > 0

    def test_estimate_tss_zero_duration_returns_zero(self):
        act = {"averageHR": 145, "duration": 0}
        tss, _ = _estimate_session_tss(act)
        assert tss == 0.0

    def test_estimate_tss_invalid_activity_returns_zero(self):
        tss1, _ = _estimate_session_tss(None)
        tss2, _ = _estimate_session_tss("not a dict")
        assert tss1 == 0.0
        assert tss2 == 0.0


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

        with patch("agent.trainer_agent.call_tool", new=AsyncMock(return_value='{"data":{"cyclingFtp":266}}')) as call_mock, \
             patch("agent.trainer_agent._save_user_profile") as save_mock:
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

        with patch("agent.trainer_agent.call_tool", new=AsyncMock()) as call_mock, \
             patch("agent.trainer_agent._save_user_profile") as save_mock:
            ftp = await TrainerAgent._get_or_refresh_cycling_ftp(agent)

        assert ftp == 248.0
        call_mock.assert_not_awaited()
        save_mock.assert_not_called()


class TestTssSourceTagging:
    def test_infer_tss_source_tag_cycling_power_ftp(self):
        act = {"type": "gravel_cycling", "normalizedPower": 180, "duration": 3600}
        src = _infer_tss_source_tag(act, tss_label="TSS", ftp=260.0, hr_zones_raw=None)
        assert src == "power_ftp"

    def test_infer_tss_source_tag_cycling_hr_zones_without_ftp(self):
        act = {"type": "road_cycling", "normalizedPower": 180, "duration": 3600}
        src = _infer_tss_source_tag(act, tss_label="hrTSS", ftp=None, hr_zones_raw='[{"zoneNumber":1,"secsInZone":1200}]')
        assert src == "hr_zones"

    def test_infer_tss_source_tag_cycling_hr_avg_without_ftp(self):
        act = {"type": "mountain_biking", "averageHR": 150, "duration": 3600}
        src = _infer_tss_source_tag(act, tss_label="hrTSS", ftp=None, hr_zones_raw=None)
        assert src == "hr_avg"

    def test_estimate_tss_double_session_same_day_accumulates(self):
        """Dos sesiones en el mismo día deben sumar sus TSS en el modelo."""
        morning = {"averageHR": 145, "maxHR": 185, "duration": 3600}
        afternoon = {"trainingLoad": 80.0}
        tss_morning, _ = _estimate_session_tss(morning)
        tss_afternoon, _ = _estimate_session_tss(afternoon)
        # Ambas > 0 y distintas, y el modelo las sumará en tss_by_day
        assert tss_morning > 0 and tss_afternoon > 0
        assert tss_morning != tss_afternoon

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

    # ── Punto E: plan con lesión informa del ajuste de dificultad ────────────

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


class TestPlanStatusChatRoute:
    @pytest.mark.asyncio
    async def test_chat_plan_status_does_not_call_llm_and_is_consistent_with_profile(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent._save_history_entry"):
            out = await TrainerAgent.chat(agent, "Tengo algun plan?")

        assert "No tienes plan asignado" in out
        assert len(agent.conversation_history) == 2

    @pytest.mark.asyncio
    async def test_chat_planning_generates_structured_plan_without_llm(self):
        from agent.trainer_agent import TrainerAgent

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
        agent.mcp_read_only = True
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
        }) as mocked_create, patch("agent.trainer_agent._save_user_profile"), patch("agent.trainer_agent._save_history_entry"):
            out = await TrainerAgent.chat(agent, "Puedes planificarme la semana para mi 10K?")

        assert "Resumen" in out
        assert "Plan activo: Plan hacia 10K" in out
        mocked_create.assert_called_once()
        assert agent.user_profile["training_plan"]["id"] == "plan-db-1"

    @pytest.mark.asyncio
    async def test_chat_planning_updates_existing_plan_and_reports_changes(self):
        from agent.trainer_agent import TrainerAgent

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
        agent.mcp_read_only = True
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
        }) as mocked_update, patch("agent.trainer_agent._storage.create_training_plan") as mocked_create, patch(
            "agent.trainer_agent._save_user_profile"
        ), patch("agent.trainer_agent._save_history_entry"):
            out = await TrainerAgent.chat(agent, "Ajusta mi plan de esta semana")

        mocked_update.assert_called_once()
        mocked_create.assert_not_called()
        assert "Cambios de versión" in out or "Cambios de version" in out
        assert "Volumen semanal estimado" in out

    @pytest.mark.asyncio
    async def test_chat_planning_validation_error_returns_user_facing_message(self):
        from agent.trainer_agent import TrainerAgent

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "goals": {"target_race": "10K", "target_race_date": "2026-11-22"},
            "health": {},
        }
        agent.conversation_history = []
        agent.tools_schema = []
        agent.mcp_session = MagicMock()
        agent.mcp_read_only = True
        agent.model = "test-model"
        agent._build_messages = lambda _msg: []
        agent.client = MagicMock()
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        with patch("agent.trainer_agent._storage.get_active_training_plan", return_value=None), patch(
            "agent.trainer_agent._validate_structured_plan", return_value=["Error de validación de prueba"]
        ), patch("agent.trainer_agent._storage.create_training_plan") as mocked_create, patch(
            "agent.trainer_agent._storage.update_training_plan"
        ) as mocked_update, patch("agent.trainer_agent._save_history_entry"):
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


class TestMcpReadOnlyPolicy:
    def test_is_write_mcp_tool_detects_mutations(self):
        assert _is_write_mcp_tool("create_custom_food")
        assert _is_write_mcp_tool("update_custom_food")
        assert _is_write_mcp_tool("delete_workout")
        assert _is_write_mcp_tool("schedule_workout")
        assert _is_write_mcp_tool("upload_workout")

    def test_is_write_mcp_tool_allows_read_tools(self):
        assert not _is_write_mcp_tool("get_activity")
        assert not _is_write_mcp_tool("get_training_status")

    def test_read_only_block_message_has_expected_shape(self):
        payload = json.loads(_build_mcp_read_only_block_message("schedule_workout"))
        assert payload["error"] == "mcp_read_only_mode"
        assert payload["tool"] == "schedule_workout"

    def test_build_tools_schema_can_be_filtered_for_read_only(self):
        tools = [
            {
                "name": "get_activity",
                "description": "read",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "create_run_workout",
                "description": "write",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]
        filtered = [t for t in tools if not _is_write_mcp_tool(t["name"])]
        schema = _build_tools_schema(filtered)
        names = {(item.get("function") or {}).get("name") for item in schema}
        assert "get_activity" in names
        assert "create_run_workout" not in names

    @pytest.mark.asyncio
    async def test_chat_blocks_write_tool_calls_in_read_only_mode(self):
        from agent.trainer_agent import TrainerAgent

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
                "name": "schedule_workout",
                "description": "write",
                "parameters": {"type": "object", "properties": {}}
            }
        }]
        agent.mcp_read_only = True
        agent.client = MagicMock()
        agent.model = "test-model"
        agent.client.chat = MagicMock()
        agent.client.chat.completions = MagicMock()
        agent.client.chat.completions.create = AsyncMock(side_effect=[response1, response2])
        agent._build_messages = lambda _msg: []

        with patch("agent.trainer_agent.call_tool", new=AsyncMock(side_effect=AssertionError("Should not call MCP write tools"))):
            with patch("agent.trainer_agent._save_history_entry"):
                out = await TrainerAgent.chat(agent, "Programa un entrenamiento para mañana")

        assert out == "respuesta final"


class TestWeekTssDeterministicRoute:
    def test_is_week_tss_intent_detects_weekly_tss_queries(self):
        assert _is_week_tss_intent("Cuanto TSS llevo esta semana?")
        assert _is_week_tss_intent("Cuales son los TSS de esta semana?")
        assert _is_week_tss_intent("Dame el acumulado semanal de TSS")
        assert not _is_week_tss_intent("Como esta mi HRV hoy?")

    def test_is_week_activities_intent_detects_weekly_activity_queries(self):
        assert _is_week_activities_intent("Cuales son mis actividades de la semana del 10 de agosto 2026?")
        assert _is_week_activities_intent("Que entrenamientos hice esta semana?")
        assert not _is_week_activities_intent("Cuanto TSS llevo esta semana?")

    @pytest.mark.asyncio
    async def test_chat_week_tss_route_does_not_call_llm(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent._build_current_week_tss_markdown", new=AsyncMock(return_value="## 🧭 Resumen\nok\n\n## 🎯 Próximo paso\n- Fuente: respuesta determinista (sin inferencias del LLM para nombres/tipos de actividad).")) as weekly_mock, patch(
            "agent.trainer_agent._save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "Cuánto TSS llevo esta semana?")

        weekly_mock.assert_awaited_once()
        assert out.startswith("## 🧭 Resumen")
        assert "sin inferencias del LLM" in out
        assert len(agent.conversation_history) == 2

    @pytest.mark.asyncio
    async def test_chat_week_activities_route_does_not_call_llm(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent._build_week_activities_markdown", new=AsyncMock(return_value="## 🧭 Resumen\nok\n\n## 🎯 Próximo paso\n- Fuente: respuesta determinista (sin inferencias del LLM para nombres/tipos de actividad).")) as weekly_mock, patch(
            "agent.trainer_agent._save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "Cuales son mis actividades de la semana del 10 de agosto 2026?")

        weekly_mock.assert_awaited_once()
        assert out.startswith("## 🧭 Resumen")
        assert "sin inferencias del LLM" in out
        assert len(agent.conversation_history) == 2

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

        monkeypatch.setattr(ta, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=14: [])

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
                "activityName": "Rodaje sábado",
                "activityType": "running",
                "startTimeLocal": "2026-08-15 08:00:00",
                "trainingLoad": 82.49,
            }
        ]

        async def _fake_call_tool(_session, tool_name, _args):
            assert tool_name == "get_activities_by_date"
            return activities_payload

        monkeypatch.setattr(ta, "call_tool", _fake_call_tool)

        out = await ta._build_current_week_tss_markdown(mcp_session=object(), profile=profile)

        assert "| TSS acumulado | 342.9 |" in out
        assert "sabado 15/08: 82.5" in out
        assert "fallback" in out

    def test_resolve_week_window_uses_explicit_historical_date(self, monkeypatch):
        import agent.trainer_agent as ta
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 8, 18)

        monkeypatch.setattr(ta, "date", _FakeDate)

        week_start, week_end = _resolve_week_window("semana del 10 de agosto de 2026", _FakeDate.today())
        assert week_start.isoformat() == "2026-08-10"
        assert week_end.isoformat() == "2026-08-16"

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_honors_explicit_week_date(self, monkeypatch):
        import agent.trainer_agent as ta
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 8, 18)

        monkeypatch.setattr(ta, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=14: [])

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

        monkeypatch.setattr(ta, "call_tool", _fake_call_tool)

        out = await ta._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="Dime los entrenamientos y Tss de la semana del 10 de agosto 2026",
        )

        assert "| Semana natural | 10/08/2026 → 16/08/2026 |" in out
        assert "| TSS acumulado | 120.0 |" in out
        assert "lunes 10/08: 40.0" in out
        assert "domingo 16/08: 15.0" in out
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

        monkeypatch.setattr(ta, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=14: [])

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
                    "activityName": "Senderismo",
                    "activityType": "hiking",
                    "startTimeLocal": "2026-08-17 08:00:00",
                    "trainingLoad": 79.4,
                }
            ]

        monkeypatch.setattr(ta, "call_tool", _fake_call_tool)

        out = await ta._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="Dime los TSSs de esta semana",
        )

        assert "lunes 17/08: 79.4" in out
        assert "| TSS acumulado | 79.4 |" in out
        assert "fallback" in out

    @pytest.mark.asyncio
    async def test_build_current_week_tss_markdown_estimates_strength_tss_when_training_load_missing(self, monkeypatch):
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

        monkeypatch.setattr(ta, "date", _FakeDate)
        monkeypatch.setattr(ta._storage, "get_load_metrics_series", lambda days=14: [])

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
                    "activityName": "Fuerza tren inferior",
                    "activityType": "strength_training",
                    "startTimeLocal": "2026-08-17 19:00:00",
                    "duration": 3600,
                    "averageHR": 120,
                    "maxHR": 160,
                }
            ]

        monkeypatch.setattr(ta, "call_tool", _fake_call_tool)

        out = await ta._build_current_week_tss_markdown(
            mcp_session=object(),
            profile=profile,
            user_message="Dime los TSSs de esta semana",
        )

        assert "lunes 17/08: 0.0" not in out
        assert "TSS acumulado: 0.0" not in out


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
        import agent.trainer_agent as ta

        payload = {
            "userData": {
                "lactateThresholdHeartRate": 169,
            }
        }
        out = ta._extract_hr_threshold_from_payload(payload)
        assert out == 169

    @pytest.mark.asyncio
    async def test_chat_hr_threshold_route_does_not_call_llm(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent._load_user_profile", return_value=agent.user_profile), patch(
            "agent.trainer_agent._save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "Cual es mi FC umbral?")

        assert out.startswith("## 🧭 Resumen")
        assert "174 bpm" in out

    @pytest.mark.asyncio
    async def test_hr_threshold_route_reads_garmin_user_profile_camel_case(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent._load_user_profile", return_value=agent.user_profile), patch(
            "agent.trainer_agent._save_history_entry"
        ), patch("agent.trainer_agent.call_tool", _fake_call_tool), patch("agent.trainer_agent._save_user_profile"):
            out = await TrainerAgent.chat(agent, "puedes consultar a garmin connect cual es mi FC umbral?")

        assert out.startswith("## 🧭 Resumen")
        assert "169 bpm" in out

    @pytest.mark.asyncio
    async def test_hr_threshold_route_does_not_update_load_effective_date_marker(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent._load_user_profile", return_value=agent.user_profile), patch(
            "agent.trainer_agent._save_history_entry"
        ), patch("agent.trainer_agent.call_tool", _fake_call_tool), patch("agent.trainer_agent._save_user_profile"):
            out = await TrainerAgent.chat(agent, "mi frecuencia cardiaca umbral")

        assert "169 bpm" in out
        perf = (agent.user_profile.get("performance") or {})
        assert perf.get("performance_params_updated_at") == "2026-08-10"

    @pytest.mark.asyncio
    async def test_hr_threshold_route_never_calls_lactate_threshold_tool(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent._load_user_profile", return_value=agent.user_profile), patch(
            "agent.trainer_agent._save_history_entry"
        ), patch("agent.trainer_agent.call_tool", _fake_call_tool), patch("agent.trainer_agent._save_user_profile"):
            out = await TrainerAgent.chat(agent, "cual es mi FC umbral?")

        assert "169 bpm" in out

    @pytest.mark.asyncio
    async def test_chat_config_options_route_does_not_call_llm(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent._save_history_entry"):
            out = await TrainerAgent.chat(agent, "que opciones puedo cambiar?")

        assert out.startswith("## 🧭 Resumen")
        assert "/perfil umbral" in out
        assert "/perfil fc" in out


class TestTomorrowWorkoutViaLlm:
    @pytest.mark.asyncio
    async def test_chat_planning_generic_reply_uses_llm_rescue_without_goal(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent._save_history_entry"), patch.object(
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


class TestPostActivityGenericRescue:
    @pytest.mark.asyncio
    async def test_chat_activity_feedback_generic_reply_uses_llm_rescue(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent.call_tool", new=AsyncMock(side_effect=_fake_call_tool)), patch(
            "agent.trainer_agent._build_activity_analysis_block",
            return_value="RESUMEN DE ACTIVIDAD PRECOMPUTADO",
        ), patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=None)), patch(
            "agent.trainer_agent._save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "¿que opinión tienes de mi entrenamiento de ayer?")

        assert "Buen trabajo ayer" in out


class TestDailyReadinessDeterministicRoute:
    def test_is_daily_readiness_intent_detects_status_queries(self):
        assert _is_daily_readiness_intent("¿Cómo estoy hoy para entrenar y qué me recomiendas?")
        assert _is_daily_readiness_intent("¿Puedo entrenar hoy?")
        assert _is_daily_readiness_intent("Dame mi training readiness")
        assert not _is_daily_readiness_intent("¿Qué zapatillas me recomiendas para trail?")

    @pytest.mark.asyncio
    async def test_chat_daily_readiness_route_does_not_call_llm(self):
        from agent.trainer_agent import TrainerAgent

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
        agent.collect_startup_snapshot_48h = AsyncMock(return_value={
            "dates": {"today": date.today().isoformat(), "yesterday": (date.today() - timedelta(days=1)).isoformat()},
            "body_battery": {"today": {"bodyBatteryLevel": 90}, "yesterday": {}, "summary": ""},
            "hrv": {"today": {"lastNightAvg": 60, "weeklyAvg": 58, "status": "BALANCED"}, "yesterday": {}, "summary": ""},
            "sleep": {"today": {"sleepDuration": 8 * 3600, "sleepScore": 85}, "yesterday": {}, "summary": ""},
            "load_fatigue": {"latest": {"tss": 0.0, "atl": 50.0, "ctl": 55.0, "tsb": 5.0}, "weekly": {"current_tss": 220.0}, "action": "carga estable"},
            "trainings": [],
        })

        with patch("agent.trainer_agent._save_history_entry"):
            out = await TrainerAgent.chat(agent, "¿Cómo estoy hoy para entrenar y qué me recomiendas?")

        agent.collect_startup_snapshot_48h.assert_awaited_once()
        assert "Estado proactivo" in out
        assert "sin inferencias numéricas del LLM" in out
        assert len(agent.conversation_history) == 2


class TestMcpFactualDeterministicRoute:
    def test_is_mcp_factual_query_intent_detects_data_queries(self):
        assert _is_mcp_factual_query_intent("¿Qué TSS hice ayer?")
        assert _is_mcp_factual_query_intent("¿Cuánto he dormido hoy?")
        assert _is_mcp_factual_query_intent("Dime mi FC en reposo")
        assert _is_mcp_factual_query_intent("¿Qué entrené ayer?")
        assert _is_mcp_factual_query_intent("¿Cuáles fueron los datos de mi entrenamiento de ayer?")
        assert not _is_mcp_factual_query_intent("Recomiéndame el entrenamiento de hoy")

    def test_is_activity_details_query_intent_detects_detail_requests(self):
        assert _is_activity_details_query_intent("¿Cuáles fueron los datos de mi entrenamiento de ayer?")
        assert _is_activity_details_query_intent("Dame el resumen de la actividad de hoy")
        assert not _is_activity_details_query_intent("¿Qué TSS hice ayer?")

    @pytest.mark.asyncio
    async def test_chat_factual_route_does_not_call_llm(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent._build_mcp_factual_query_markdown", new=AsyncMock(return_value="## 🧭 Resumen\nok\n\n## 🎯 Próximo paso\n- Fuente: respuesta determinista (datos factuales MCP, sin inferencias numéricas del LLM).")) as factual_mock, patch(
            "agent.trainer_agent._save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "¿Qué TSS hice ayer?")

        factual_mock.assert_awaited_once()
        assert out.startswith("## 🧭 Resumen")
        assert "sin inferencias numéricas del LLM" in out
        assert len(agent.conversation_history) == 2

    @pytest.mark.asyncio
    async def test_build_mcp_factual_query_markdown_falls_back_to_activity_tss_when_series_is_zero(self):
        import agent.trainer_agent as ta

        target_iso = "2026-08-17"

        async def _fake_call_tool(_session, tool_name, args):
            if tool_name == "get_activities_by_date":
                return [
                    {
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

        with patch("agent.trainer_agent.call_tool", _fake_call_tool), patch(
            "agent.trainer_agent._resolve_target_date_from_message", return_value=date.fromisoformat(target_iso)
        ):
            out = await ta._build_mcp_factual_query_markdown(
                mcp_session=object(),
                profile=profile,
                user_message="Cuales son los TSS de la actividad del 17/08/26?",
            )

        assert "| TSS del día | 79.4 |" in out
        assert "garmin_activities(fallback)" in out


class TestRunningThresholdDeterministicRoute:
    def test_is_running_threshold_query_intent_detects_queries(self):
        assert _is_running_threshold_query_intent("Cual es mi ritmo umbral?")
        assert _is_running_threshold_query_intent("Dime el umbral de running")
        assert _is_running_threshold_query_intent("What is my threshold pace?")
        assert not _is_running_threshold_query_intent("Cual es mi umbral de lactato en mmol?")

    @pytest.mark.asyncio
    async def test_chat_running_threshold_uses_latest_persisted_profile(self):
        from agent.trainer_agent import TrainerAgent

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

        with patch("agent.trainer_agent._load_user_profile", return_value=latest_profile), patch(
            "agent.trainer_agent._save_history_entry"
        ):
            out = await TrainerAgent.chat(agent, "Cual es mi ritmo umbral actual?")

        assert "4:12 min/km" in out
        assert "2026-08-15" in out
        assert "perfil persistido" in out
        assert len(agent.conversation_history) == 2


class TestCyclingFtpRefreshDatePolicy:
    @pytest.mark.asyncio
    async def test_refresh_same_ftp_does_not_update_change_date(self):
        from agent.trainer_agent import TrainerAgent

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "performance": {
                "cycling_ftp": 250.0,
                "cycling_ftp_date": "2026-08-10",
                "performance_params_updated_at": "2026-08-10",
            }
        }
        agent.mcp_session = MagicMock()

        with patch("agent.trainer_agent.call_tool", new=AsyncMock(return_value='{"functionalThresholdPower": 250}')), patch(
            "agent.trainer_agent._save_user_profile"
        ):
            out = await TrainerAgent._get_or_refresh_cycling_ftp(agent, force_refresh=True)

        assert out == 250.0
        perf = agent.user_profile.get("performance") or {}
        assert perf.get("cycling_ftp_date") == "2026-08-10"
        assert perf.get("performance_params_updated_at") == "2026-08-10"


class TestSessionSummaryCheckpoint:
    def test_generate_session_summary_checkpoint_uses_recent_turns(self):
        from agent.trainer_agent import TrainerAgent

        agent = object.__new__(TrainerAgent)
        agent.conversation_history = [
            {"role": "user", "content": "Cuánto TSS llevo esta semana?"},
            {"role": "assistant", "content": "TSS acumulado: 260.4"},
            {"role": "user", "content": "Cómo está mi HRV hoy?"},
            {"role": "assistant", "content": "HRV 73 ms, balanced."},
        ]

        summary = TrainerAgent.generate_session_summary_checkpoint(agent)
        assert "Temas recientes:" in summary
        assert "HRV" in summary
        assert "Última respuesta:" in summary

    def test_save_session_summary_checkpoint_calls_daily_upsert(self):
        from agent.trainer_agent import TrainerAgent

        agent = object.__new__(TrainerAgent)
        agent.conversation_history = [
            {"role": "user", "content": "Mi FTP?"},
            {"role": "assistant", "content": "Tu FTP es 205W."},
        ]

        with patch("agent.trainer_agent._storage.persist_session_summary_daily") as upsert_mock:
            TrainerAgent.save_session_summary_checkpoint(agent)

        upsert_mock.assert_called_once()
