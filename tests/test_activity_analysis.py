"""
tests/test_activity_analysis.py
Localizar una actividad y su análisis precalculado (agent/activity_analysis.py).
"""

import json
from datetime import date
from unittest.mock import MagicMock

import pytest


from tests.agent_patch import patch_all, patch_everywhere
import agent.activity_analysis as _activity_analysis
from agent.activity_analysis import _build_activity_candidates_payload, _build_post_activity_section_spec, _format_activity_analysis_for_markdown, _normalize_get_activity_args, _resolve_activity_id_from_query


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales



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


class TestActivityAnalysisMarkdownFormatting:
    """Formato del análisis de una actividad."""

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
