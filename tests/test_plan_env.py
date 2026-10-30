"""
tests/test_plan_env.py
Entorno del atleta en el plan (30/10, pendiente 11 a): desnivel de sus salidas desde Garmin (asfalto y trail por
separado, sin umbrales), dónde entrena y que ambos llegan al LLM. Criterios fijados antes en docs/proximos_pasos.md.
"""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from agent import plan_builder as pb
from agent.prompts import _render_prompt


def _act(key: str, km: float, gain, days_ago: int = 3) -> dict:
    return {"activityType": {"typeKey": key}, "distance": km * 1000, "elevationGain": gain, "duration": km * 300,
            "startTimeLocal": (date.today() - timedelta(days=days_ago)).isoformat() + " 08:00:00"}


def _context(acts: list[dict]) -> dict:
    from agent.trainer_agent import TrainerAgent

    agent = object.__new__(TrainerAgent)
    agent.user_profile, agent.mcp_session = {}, None
    with patch("agent.context._fetch_activities_for_load_calc", AsyncMock(return_value=acts)), \
         patch("agent.context._load_metrics_series", return_value=[]):
        return asyncio.run(agent._build_plan_load_context())


def test_terrain_is_metres_per_km_for_road_and_trail_separately():
    ctx = _context([_act("running", 10, 80), _act("running", 20, 160, 30), _act("trail_running", 15, 750),
                    _act("cycling", 50, 900), _act("walking", 5, 300)])  # bici y caminata no cuentan
    assert ctx["terrain"] == {"road": {"km": 30.0, "m_per_km": 8.0}, "trail": {"km": 15.0, "m_per_km": 50.0}}


def test_terrain_without_km_or_without_gain():
    assert "terrain" not in _context([_act("cycling", 40, 500)])  # sin salidas de carrera: no sale
    ctx = _context([_act("running", 12, None)])  # sin desnivel en Garmin: 0 m/km, no un fallo
    assert ctx["terrain"] == {"road": {"km": 12.0, "m_per_km": 0.0}}


def test_environment_reaches_the_llm_as_data_and_the_rule_is_in_the_prompt():
    start = date(2026, 11, 2)
    f = pb.PlanFrame(start=start, weeks=8, race="10K", race_date=start + timedelta(weeks=7, days=6), distance_km=10.0,
                     target_time="", threshold_pace=240.0, race_pace=None, location="Elche (Alicante)",
                     terrain={"road": {"km": 120.0, "m_per_km": 8.0}, "trail": {"km": 30.0, "m_per_km": 45.0}})
    text = pb.frame_text(f)
    assert "## Entorno" in text and "- Entrena en: Elche (Alicante)" in text
    assert "carrera en asfalto (últimas 6 semanas): 8 m/km en 120 km" in text and "trail (últimas 6 semanas): 45 m/km" in text
    plain = pb.frame_text(pb.PlanFrame(start=start, weeks=8, race="10K", race_date=f.race_date, distance_km=10.0,
                                       target_time="", threshold_pace=240.0, race_pace=None))
    assert "## Entorno" not in plain
    system = _render_prompt("plan/system")
    assert "Entorno" in system and "clima habitual" in system and "desnivel" in system


def test_plan_frame_takes_the_location_from_the_profile_and_the_terrain_from_the_load_context():
    from agent.trainer_agent import TrainerAgent

    agent = object.__new__(TrainerAgent)
    agent.user_profile = {"goals": {"training_location": "Elche", "target_race": "Media",
                                    "target_race_date": (date.today() + timedelta(days=70)).isoformat()}}
    f = agent._plan_frame({"terrain": {"road": {"km": 50.0, "m_per_km": 6.0}}})
    assert f.location == "Elche" and f.terrain == {"road": {"km": 50.0, "m_per_km": 6.0}}
