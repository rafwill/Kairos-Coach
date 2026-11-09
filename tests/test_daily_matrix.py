"""Motor diario con la forma bien (06/11, decisión del usuario): manda la HRV y cuenta qué sesión toca."""

from datetime import date, timedelta

import pytest

from agent.daily_engine import _build_today_session_frame, _compute_daily_plan_adjustment

TODAY = date.today()
TODAY_ISO = TODAY.isoformat()


def _plan(kind: str, intensity_class: str) -> dict:
    monday = TODAY - timedelta(days=TODAY.weekday())
    session = {"week_index": 1, "day_index": TODAY.isoweekday(), "session_type": kind, "duration_min": 60,
               "intensity": "RPE 4", "structured_workout": {"title": kind, "summary": {"intensityClass": intensity_class}}}
    return {"title": "Plan", "plan_data": {"start_date": monday.isoformat()}, "sessions": [session]}


HARD = ("running_quality", "threshold")
SOFT = ("running_z2", "endurance")


def _decide(session, *, hrv_low=False, bb_low=False, sleep_bad=False, spike=False) -> str:
    snapshot = {
        "dates": {"today": TODAY_ISO},
        "load_fatigue": {"status": "ready", "latest": {"tsb": 2.0, "atl": 50.0}, "weekly": {"current_tss": 300.0},
                         "ranges": {}, "flags": {"weekly_spike_alert": spike}},
        "sleep": {"today": {"date": TODAY_ISO, "sleep_hours": 5.0 if sleep_bad else 7.8, "sleep_score": 80,
                            "bodyBatteryAtWake": 25 if bb_low else 80}},
        "hrv": {"today": {"date": TODAY_ISO, "lastNightAvg": 40 if hrv_low else 52, "weeklyAvg": 50, "status": "balanced"}},
    }
    return _compute_daily_plan_adjustment(snapshot, _plan(*session))["decision"]


@pytest.mark.parametrize("signals, hard, soft", [
    ({}, "maintain", "maintain"),
    ({"bb_low": True}, "reduce", "maintain"),
    ({"sleep_bad": True}, "reduce", "maintain"),
    ({"hrv_low": True}, "easy", "maintain"),
    ({"hrv_low": True, "bb_low": True}, "easy", "reduce"),
    ({"hrv_low": True, "bb_low": True, "sleep_bad": True}, "rest", "reduce"),
    ({"spike": True}, "reduce", "reduce"),
])
def test_matrix(signals, hard, soft):
    assert _decide(HARD, **signals) == hard
    assert _decide(SOFT, **signals) == soft


def test_strength_is_not_a_hard_session():
    assert _decide(("strength", "tempo"), hrv_low=True) == "maintain"


def test_reduced_strength_stays_strength_in_the_frame():
    # 09/11: fuerza recortada por la carrera de ayer; el marco decía "Z3 Tempo" y el título del plan, y el LLM recetó
    # 24' de tempo corriendo.
    plan = _plan("strength", "tempo")
    plan["sessions"][0]["structured_workout"].update({"title": "Fuerza base", "steps": [
        {"type": "warmup", "duration_min": 10, "intensityClass": "endurance"},
        {"type": "steady", "duration_min": 25, "intensityClass": "tempo", "target": {"metric": "rpe", "range": [6, 6]}},
    ]})
    snapshot = {"dates": {"today": TODAY_ISO}, "load_fatigue": {"status": "ready", "flags": {"weekly_spike_alert": True}}}
    out = _compute_daily_plan_adjustment(snapshot, plan)
    assert out["decision"] == "reduce" and out["planned_session"] == "Fuerza base"
    assert "RPE 6" in out["resulting_session"] and "Z3" not in out["resulting_session"]
    frame = _build_today_session_frame(out, {})
    assert "fuerza, no de carrera" in frame and "Z3" not in frame and "lpm" not in frame
