"""Tras entrenar hoy (31/10): el arranque reconoce la sesión hecha y no vuelve a proponerla."""

from datetime import date, timedelta

from agent.daily_engine import _compute_daily_plan_adjustment, _today_completion
from agent.plans import _today_session_lines
from agent.startup_status import _build_proactive_status_markdown

TODAY = date.today()
TODAY_ISO = TODAY.isoformat()


def _plan(*rows):
    monday = TODAY - timedelta(days=TODAY.weekday())
    sessions = [{"week_index": 1, "day_index": TODAY.isoweekday(), "session_type": kind, "duration_min": minutes,
                 "intensity": "RPE 4", "structured_workout": {"slot": slot, "title": title}}
                for slot, (kind, minutes, title) in enumerate(rows, 1)]
    return {"title": "Plan", "plan_data": {"start_date": monday.isoformat()}, "sessions": sessions}


def _acts(*names):
    return [{"activityId": i, "name": n} for i, n in enumerate(names, 1)]


class TestTodayCompletion:
    def test_nothing_without_activities_or_plan(self):
        assert _today_completion(_plan(("running_z2", 65, "Rodaje")), TODAY_ISO, []) is None
        assert _today_completion(None, TODAY_ISO, _acts("Fartlek")) is None

    def test_one_activity_covers_one_session(self):
        out = _today_completion(_plan(("running_z2", 65, "Rodaje")), TODAY_ISO, _acts("Fartlek"))
        assert out["all_done"] and out["planned"] == 1 and out["pending"] == [] and out["names"] == ["Fartlek"]

    def test_double_day_with_one_activity_leaves_the_second_pending(self):
        plan = _plan(("running_z2", 40, "Rodaje"), ("strength", 40, "Fuerza"))
        out = _today_completion(plan, TODAY_ISO, _acts("Rodaje"))
        assert not out["all_done"] and [r["session_type"] for r in out["pending"]] == ["strength"]

    def test_training_on_a_rest_day_closes_the_day(self):
        out = _today_completion(_plan(("rest", 0, "Descanso")), TODAY_ISO, _acts("Bici"))
        assert out["all_done"] and out["planned"] == 0


class TestDecision:
    def _snapshot(self, completion):
        return {"dates": {"today": TODAY_ISO}, "today_completion": completion}

    def test_done_today_is_rest_with_its_reason(self):
        plan = _plan(("running_z2", 65, "Rodaje"))
        out = _compute_daily_plan_adjustment(self._snapshot(_today_completion(plan, TODAY_ISO, _acts("Fartlek"))), plan)
        assert out["rule"] == "done_today" and out["decision"] == "rest"
        assert "ya realizada (Fartlek)" in out["resulting_session"] and "20-30 min" in out["resulting_session"]

    def test_double_day_decides_about_the_pending_session(self):
        plan = _plan(("running_z2", 40, "Rodaje"), ("strength", 40, "Fuerza"))
        out = _compute_daily_plan_adjustment(self._snapshot(_today_completion(plan, TODAY_ISO, _acts("Rodaje"))), plan)
        assert out["rule"] != "done_today" and out["planned_session"] == "Fuerza"
        assert out["inputs"]["planned_duration_min"] == 40


class TestTodaySessionLines:
    def test_done_sessions_are_marked(self):
        plan = _plan(("running_z2", 40, "Rodaje"), ("strength", 40, "Fuerza"))
        lines = _today_session_lines(plan, {}, done=1)
        assert "✅ Realizada" in lines[0] and "✅ Realizada" not in lines[1]


class TestMarkdown:
    def _payload(self, done=True):
        return {
            "plan_assigned": True,
            "plan_recommendation": "Plan activo",
            "daily_plan_decision": {"decision": "rest", "rule": "done_today", "reason": "ya has hecho la sesión de hoy",
                                    "resulting_session": "sesión de hoy ya realizada (Fartlek)"},
            "today_completion": {"planned": 1, "executed": 1, "all_done": done, "names": ["Fartlek"], "pending": []},
            "today_execution": {"adherence_score": 0.5, "adherence_label": "parcial", "load_deviation_pct": 0.1,
                                "planned": {"tss_est": 60.0}, "executed": {"activities": 1, "tss": 68.0}},
            "plan_execution_feedback": {"adherence_score": 0.0, "adherence_label": "baja", "load_deviation_pct": 84.6,
                                        "planned": {"tss_est": 0.0}, "executed": {"activities": 1, "tss": 84.6}},
            "today_session_lines": ["- **Sábado — Rodaje ✅ Realizada**"],
            "tomorrow_session_lines": ["- **Domingo — Tirada larga**"],
            "load_fatigue": {
                "latest": {"tss": 68.0, "atl": 51.7, "ctl": 59.5, "tsb": 10.3},
                "weekly": {"current_tss": 279.5}, "ranges": {"tsb_low": 10.3, "tsb_high": 22.8, "atl_high": 58.6},
                "recommendation": "Fresco: buen momento para calidad o para competir.",
            },
            "body_battery": {"summary": "sin datos"}, "hrv": {"summary": "sin datos"}, "sleep": {"summary": "sin datos"},
            "trainings": [],
        }

    def test_done_today_shows_today_comparison_done_session_tomorrow_and_rest(self):
        out = _build_proactive_status_markdown(self._payload())
        assert "Sesión de hoy (realizada)" in out and "Hecho hoy: Fartlek · 68 TSS" in out
        assert "Plan vs ejecutado (hoy)" in out and "(ayer)" not in out
        assert "## 🗓️ Mañana" in out and "Tirada larga" in out
        assert "descanso o movilidad suave: ya has entrenado hoy" in out
        assert "Tras la sesión de hoy: Forma de mañana (CTL − ATL) +7.8" in out
        assert "antes de entrenar" in out and "Regla aplicada" not in out

    def test_not_done_keeps_yesterday_comparison_and_the_morning_rule(self):
        payload = self._payload(done=False)
        payload["today_execution"] = {}
        out = _build_proactive_status_markdown(payload)
        assert "Plan vs ejecutado (ayer)" in out and "Regla aplicada" in out
        assert "(realizada)" not in out and "Mañana" not in out
