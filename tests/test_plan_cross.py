"""
tests/test_plan_cross.py
Deportes cruzados en el plan (30/10, pendiente 11 b): formato del día "cross", TSS por FC, reglas por semana,
pregunta de /plan crear y volumen sostenido sin caminatas. Criterios fijados antes en docs/proximos_pasos.md.
"""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from agent import plan_builder as pb
from agent.main import _parse_cross_sports

ZONES = [("Z1", 115, 134), ("Z2", 135, 145), ("Z3", 146, 157), ("Z4", 158, 168), ("Z5", 169, 179)]
START = date(2026, 11, 2)


def _frame(**kw) -> pb.PlanFrame:
    args = dict(start=START, weeks=8, race="10K", race_date=START + timedelta(weeks=7, days=6), distance_km=10.0,
                target_time="", threshold_pace=250.0, race_pace=None, hr_zones=ZONES, hr_threshold_tp=169.0)
    args.update(kw)
    return pb.PlanFrame(**args)


def _cross(sport="cycling", zone="FC2", **kw):
    return {"day": 3, "type": "cross", "sport": sport, "steps": [
        {"kind": "warmup", "min": 10, "zone": "FC1"}, {"kind": "steady", "min": 40, "zone": zone},
        {"kind": "cooldown", "min": 10, "zone": "FC1"}], **kw}


def test_cross_day_needs_a_sport_and_hr_steps():
    session, errors = pb.expand_day(_cross(), _frame(), 1)
    assert errors == [] and session["structured_workout"]["sport"] == "cycling"
    assert session["structured_workout"]["title"] == "Bici"  # título por defecto, del deporte
    assert pb.expand_day(_cross("rowing", title="Remo técnico"), _frame(), 1)[0]["structured_workout"]["title"] == "Remo técnico"
    for bad in ({"day": 3, "type": "cross", "minutes": 45, "rpe": "5"},  # antes: "Fuerza" con 35 TSS/h
                {**_cross(), "sport": "padel"}, {k: v for k, v in _cross().items() if k != "sport"}):
        session, errors = pb.expand_day(bad, _frame(), 1)
        assert session is None and "\"sport\"" in errors[0]
    session, errors = pb.expand_day(_cross(zone="Z2"), _frame(), 1)  # zona de ritmo en bici: daría rTSS de carrera
    assert session is None and "por FC" in errors[0]


def test_cross_tss_comes_from_hr_not_from_the_strength_rate():
    session, _ = pb.expand_day(_cross(zone="FC4"), _frame(), 1)
    summary = session["structured_workout"]["summary"]
    # FC1 124.5/169 = 0.74 → 40.8/h (20 min); FC4 163/169 = 0.96 → 91.8/h (40 min). Sin km.
    assert summary["tss"] == round(20 / 60 * 40.8 + 40 / 60 * 91.8, 1) and summary["distance_km"] == 0
    assert summary["tss"] != round(60 / 60 * 35.0, 1)


def _week(*cross_days) -> list[dict]:
    """Semana de 7 días: los "cross" dados y descanso el resto (check_week exige los 7)."""
    f = _frame()
    used = {d["day"] for d in cross_days}
    days = [*cross_days, *({"day": d, "type": "rest"} for d in range(1, 8) if d not in used)]
    return [pb.expand_day(d, f, 1)[0] for d in days]


def _cross_errors(sessions, **kw) -> list[str]:
    return [e for e in pb.check_week(1, sessions, {"minutes": 0}, _frame(**kw)) if "cross" in e]


def test_week_rules_for_cross_sports():
    week = _week(_cross("cycling", day=3), _cross("rowing", day=5))
    assert _cross_errors(week) == []  # sin respuesta en /plan crear: sin regla
    assert _cross_errors(week, cross_sports=["cycling", "rowing"], cross_max=2) == []
    assert any("rowing; el atleta solo hace cycling" in e for e in _cross_errors(week, cross_sports=["cycling"]))
    assert any("2 sesiones \"cross\"; como mucho 1" in e for e in _cross_errors(week, cross_sports=["cycling", "rowing"],
                                                                                  cross_max=1))
    assert any("no quiere otros deportes" in e for e in _cross_errors(week, cross_sports=[]))


def test_the_llm_gets_the_cross_preferences_and_rules():
    f = _frame(cross_sports=["cycling", "swimming"], cross_max=2)
    assert "Deportes cruzados dentro del plan: bici, natación; como mucho 2 sesiones por semana" in pb.frame_text(f)
    assert "\"cross\" solo de cycling, swimming, como mucho 2 por semana." in pb.rules_text(f)
    assert "ninguno (solo carrera y fuerza)" in pb.frame_text(_frame(cross_sports=[]))
    assert "Deportes cruzados" not in pb.frame_text(_frame()) and "\"cross\"" not in pb.rules_text(_frame())


@pytest.mark.parametrize("text, expected", [
    ("bici, remo", ["cycling", "rowing"]), ("Gravel y natación", ["cycling", "swimming"]),
    ("no", []), ("ninguno", []), ("", None),
])
def test_parse_cross_sports(text, expected):
    assert _parse_cross_sports(text) == expected


def test_unknown_sport_is_not_none():
    with pytest.raises(ValueError):
        _parse_cross_sports("pádel")


def test_plan_create_asks_and_saves_the_cross_sports():
    from agent import main as m

    class _Agent:
        user_profile = {"goals": {}}

    for answers, expected in ((["todos", "", "", "1", "1", "pádel", "bici, remo", "2"],
                               {"sports": ["cycling", "rowing"], "max_per_week": 2}),
                              (["todos", "", "", "1", "1", "no"], {"sports": []}),
                              (["todos", "", "", "1", "1", ""], None)):
        agent = _Agent()
        agent.user_profile = {"goals": {}}
        it = iter(answers)
        with patch.object(m.Prompt, "ask", side_effect=lambda *a, **k: next(it)), \
             patch.object(m.Confirm, "ask", return_value=False), patch.object(m, "_save_user_profile"), \
             patch.object(m.console, "print") as printed:
            m._ask_plan_preferences(agent)
        assert agent.user_profile["goals"]["plan_preferences"].get("cross") == expected
        if "pádel" in answers:  # no lo conoce: lo dice y vuelve a preguntar
            assert any("pádel" in str(c.args[0]) for c in printed.call_args_list)


def test_sustained_volume_leaves_out_walks():
    from agent.trainer_agent import TrainerAgent

    today = date.today()
    acts = [{"activityType": {"typeKey": key}, "startTimeLocal": (today - timedelta(days=d)).isoformat() + " 08:00:00",
             "duration": minutes * 60}
            for key, minutes, d in (("running", 60, 2), ("walking", 120, 3), ("hiking", 180, 5), ("cycling", 90, 6))]
    agent = object.__new__(TrainerAgent)
    agent.user_profile, agent.mcp_session = {}, None
    with patch("agent.context._fetch_activities_for_load_calc", AsyncMock(return_value=acts)), \
         patch("agent.context._load_metrics_series", return_value=[]):
        ctx = asyncio.run(agent._build_plan_load_context())
    assert ctx["recent_weekly_minutes"] == round((60 + 90) / 4, 1)  # ni la caminata ni el senderismo
