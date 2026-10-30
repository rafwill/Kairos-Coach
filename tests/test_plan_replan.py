"""
tests/test_plan_replan.py
«Replanifica el plan» (30/10, pendiente 11 d2): el LLM rehace desde el lunes que viene hasta la carrera. Criterios
fijados antes de programarlo (docs/proximos_pasos.md): enrutado (replanificar frente a ajustar), fechas, unión de la
semana actual con las nuevas y de plan_data, el fallo que no toca nada y el caso sin semanas que rehacer.
"""

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from agent import plan_builder as pb
from agent.plans import _wants_replan_intent
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales

OLD_START = date(2026, 10, 26)  # lunes
RACE = date(2027, 1, 24)  # domingo


class _FakeDate(date):
    today_value = date(2026, 11, 4)  # miércoles de la semana 2 del plan

    @classmethod
    def today(cls):
        return cls.today_value


def _s(week: int, day: int, kind: str = "running_z2", minutes: float = 50.0) -> dict:
    return {"week_index": week, "day_index": day, "session_type": kind, "duration_min": minutes,
            "structured_workout": {"title": kind, "summary": {"duration_min": minutes, "tss": minutes}}}


OLD_PD = {"author": "llm", "method": "bloques_con_descarga", "start_date": OLD_START.isoformat(),
          "target_race": "Media Maratón de Santa Pola", "target_race_date": RACE.isoformat(), "target_time": "1:23:00",
          "phases": ["base", "base", "build"], "focus": ["f1", "f2", "f3"], "planned_week_minutes": [300, 330, 360],
          "long_minutes": [80, 90, 100], "week_minutes": [300, 330, 360], "deload_weeks": [2]}
OLD_SESSIONS = [_s(1, 1), _s(2, 2, "running_quality"), _s(2, 6, "long_run", 90), _s(3, 1)]


def _new_plan(weeks: int, start: date) -> tuple[dict, list[dict]]:
    pd = {"author": "llm", "method": "bloques_con_descarga", "start_date": start.isoformat(),
          "phases": ["base"] * (weeks - 1) + ["race"], "focus": [f"n{w}" for w in range(1, weeks + 1)],
          "planned_week_minutes": [200] * weeks, "long_minutes": [70] * weeks, "week_minutes": [100.0] * weeks,
          "deload_weeks": [3], "phase_weeks": {}}
    plan = {"title": "Plan replanificado", "description": "", "objective": "Media", "difficulty": "moderate",
            "duration_weeks": weeks, "plan_data": pd}
    return plan, [_s(w, 2, minutes=100.0) for w in range(1, weeks + 1)]


def test_replan_wording_is_not_an_adjustment():
    assert _wants_replan_intent("replanifica el plan") and _wants_replan_intent("Rehaz mi plan, he estado enfermo")
    assert not _wants_replan_intent("ajusta el plan de esta semana")


def test_merge_keeps_this_week_as_week_1_and_shifts_the_new_weeks():
    new_plan, new_sessions = _new_plan(3, date(2026, 11, 9))
    pd, sessions = pb.merge_replan(OLD_PD, OLD_SESSIONS, new_plan["plan_data"], new_sessions, current_week=2)
    assert [(s["week_index"], s["day_index"], s["session_type"]) for s in sessions] == [
        (1, 2, "running_quality"), (1, 6, "long_run"), (2, 2, "running_z2"), (3, 2, "running_z2"), (4, 2, "running_z2")]
    assert pd["start_date"] == "2026-11-02"  # lunes de esta semana
    assert pd["phases"] == ["base", "base", "base", "race"] and pd["focus"] == ["f2", "n1", "n2", "n3"]
    assert pd["planned_week_minutes"] == [330, 200, 200, 200] and pd["long_minutes"] == [90, 70, 70, 70]
    assert pd["week_minutes"] == [140.0, 100.0, 100.0, 100.0]  # la semana actual, con lo que suman sus sesiones
    assert pd["deload_weeks"] == [1, 4]  # la actual era de descarga; la 3 nueva pasa a ser la 4
    assert pd["phase_weeks"]["base"] == 3 and pd["phase_weeks"]["race"] == 1


def _agent():
    from agent.trainer_agent import TrainerAgent

    agent = object.__new__(TrainerAgent)
    agent.user_profile = {"goals": {"target_race": "Media", "target_race_date": RACE.isoformat()}, "health": {}}
    agent.model = "fake"
    return agent


def _plan() -> dict:
    return {"id": "p1", "title": "Plan", "status": "active", "plan_data": dict(OLD_PD), **OLD_PD}


async def _rebuild(agent, today: date, build):
    _FakeDate.today_value = today
    agent._build_plan_with_llm = build
    with patch("agent.routes.handlers.date", _FakeDate), patch("agent.plan_llm.date", _FakeDate), \
         patch("agent.routes.handlers._storage.update_training_plan", return_value={"id": "p1"}) as update, \
         patch("agent.routes.handlers._save_user_profile"):
        out = await agent._route_plan_rebuild("replanifica el plan, he estado enfermo", _plan(), OLD_SESSIONS, {})
    return out, update


@pytest.mark.asyncio
async def test_rebuild_from_next_monday_keeps_this_week_and_saves_a_new_version():
    agent = _agent()
    build = AsyncMock(side_effect=lambda msg, ctx, plan=None, from_monday=None: _new_plan(12, from_monday) + ([],))
    out, update = await _rebuild(agent, date(2026, 11, 4), build)
    kwargs = build.await_args.kwargs
    assert kwargs["from_monday"] == date(2026, 11, 9) and kwargs["plan"]["plan_data"]["target_race_date"] == RACE.isoformat()
    assert "he estado enfermo" in build.await_args.args[0]  # lo que cuenta el atleta llega al LLM
    plan_id, patch_ = update.call_args.args[:2]
    assert plan_id == "p1" and update.call_args.kwargs["change_reason"] == "llm_replan"
    assert patch_["duration_weeks"] == 13 and patch_["source"] == "agent_llm_plan" and patch_["status"] == "active"
    pd = patch_["plan_data"]
    assert pd["start_date"] == "2026-11-02" and pd["revised_on"] == "2026-11-04" and pd["author"] == "llm"
    sessions = update.call_args.kwargs["sessions"]
    assert [s["session_type"] for s in sessions if s["week_index"] == 1] == ["running_quality", "long_run"]
    assert max(s["week_index"] for s in sessions) == 13
    assert "Plan replanificado desde el 09/11/2026" in out and "Esta semana sigue como estaba" in out
    assert "24/01/2027" in out


@pytest.mark.asyncio
async def test_on_a_monday_it_rebuilds_from_today_without_keeping_the_week():
    agent = _agent()
    build = AsyncMock(side_effect=lambda msg, ctx, plan=None, from_monday=None: _new_plan(12, from_monday) + ([],))
    out, update = await _rebuild(agent, date(2026, 11, 2), build)
    assert build.await_args.kwargs["from_monday"] == date(2026, 11, 2)
    patch_ = update.call_args.args[1]
    assert patch_["duration_weeks"] == 12 and patch_["plan_data"]["start_date"] == "2026-11-02"
    assert "Esta semana sigue" not in out


@pytest.mark.asyncio
async def test_when_the_llm_fails_nothing_changes():
    agent = _agent()
    out, update = await _rebuild(agent, date(2026, 11, 4), AsyncMock(return_value=(None, [], ["Semana 3: sin descanso"])))
    update.assert_not_called()
    assert "No he replanificado" in out and "Semana 3: sin descanso" in out and "sigue como estaba" in out


@pytest.mark.asyncio
async def test_without_a_whole_week_before_the_race_nothing_is_rebuilt():
    agent = _agent()
    build = AsyncMock()
    out, update = await _rebuild(agent, date(2027, 1, 13), build)  # miércoles: desde el lunes 18 solo queda la de la carrera
    build.assert_not_awaited()
    update.assert_not_called()
    assert "ajusta el plan" in out


@pytest.mark.asyncio
async def test_planning_route_sends_replan_and_adjust_to_their_own_flows():
    from agent.trainer_agent import TrainerAgent

    agent = _agent()
    agent.conversation_history = []
    agent._build_plan_load_context = AsyncMock(return_value={})
    agent._route_plan_rebuild = AsyncMock(return_value="replanificado")
    agent._route_plan_revision = AsyncMock(return_value="revisado")
    row = {"id": "p1", "title": "Plan", "status": "active", "plan_data": dict(OLD_PD)}
    with patch("agent.trainer_agent._storage.get_active_training_plan", return_value=row), \
         patch("agent.trainer_agent._storage.list_training_plan_sessions", return_value=OLD_SESSIONS):
        assert await TrainerAgent._route_planning(agent, "replanifica el plan") == "replanificado"
        assert await TrainerAgent._route_planning(agent, "ajusta el plan de esta semana") == "revisado"


def test_the_frame_starts_on_the_given_monday_with_the_plan_race():
    agent = _agent()
    with patch("agent.plan_llm.date", _FakeDate):
        _FakeDate.today_value = date(2026, 11, 4)
        f = agent._plan_frame({}, "", plan=_plan(), from_monday=date(2026, 11, 11))  # un miércoles: su lunes
    assert f.start == date(2026, 11, 9) and f.race_date == RACE and f.race_week == 11 and f.weeks == 11
