"""
tests/test_plans.py
Motor de planes y sesión planificada (agent/plans.py, daily_engine.py).
"""

import json
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.trainer_agent as ta

from tests.agent_patch import patch_everywhere
import agent.prompts as _prompts
from agent.dates import _normalize_trend_date_range
import agent.daily_engine as _daily_engine
from agent.daily_engine import _build_startup_plan_recommendation, _get_active_training_plan, _get_planned_session_for_date
from agent.plans import _build_goal_plan_fallback, _build_training_plan_status_markdown, _generate_structured_plan_payload, _has_goal_in_profile, _summarize_plan_changes, _validate_structured_plan
from agent.replies import _is_generic_needs_more_info_reply
import agent.router as _router
from agent.router import _is_goal_status_intent, _is_plan_status_intent, _is_planning_intent


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales



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
