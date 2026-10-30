"""
tests/test_plan_prefs.py
Preferencias que duran (30/10, pendiente 11 c): un test por tipo de frase, a favor y en contra, con los criterios
fijados antes de programarlo (docs/proximos_pasos.md).
"""

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from agent import plan_builder as pb
from agent import plan_prefs as pp


@pytest.mark.parametrize("message, days", [
    ("Los martes no puedo entrenar", {2}),
    ("ajusta el plan, no puedo entrenar los martes ni los jueves", {2, 4}),
    ("A partir de ahora los miércoles no puedo", {3}),
    ("El viernes nunca puedo", {5}),
    ("Los fines de semana no tengo tiempo", {6, 7}),
    ("No puedo los martes y jueves por trabajo", {2, 4}),
])
def test_days_the_athlete_never_can(message, days):
    assert pp.detect(message)[0] == days


@pytest.mark.parametrize("message", [
    "Este martes no puedo entrenar",
    "El martes que viene no puedo",
    "Mañana no puedo, muévelo",
    "El jueves no puedo",  # un día en singular, sin "nunca" ni "siempre": para una vez
    "Los martes hago series",  # sin "no puedo"
])
def test_one_off_days_are_not_saved(message):
    assert pp.detect(message)[0] == set()


def test_lasting_preferences_are_saved_as_text_and_one_offs_are_not():
    days, notes = pp.detect("Replanifica el plan. Prefiero hacer las series en pista. Esta semana prefiero descansar.")
    assert days == set() and notes == ["Prefiero hacer las series en pista"]
    assert pp.detect("De ahora en adelante quiero la tirada larga el sábado")[1] == [
        "De ahora en adelante quiero la tirada larga el sábado"]
    assert pp.detect("ajusta el plan, tengo una molestia en el gemelo") == (set(), [])


def test_remember_saves_days_as_rest_and_notes_without_duplicates():
    profile = {"goals": {"availability": {"training_days": [1, 2, 3, 4, 5, 6, 7], "rest_days": [5]},
                         "plan_preferences": {"doubles": True, "notes": ["Prefiero hacer las series en pista"]}}}
    saved = pp.remember(profile, "Los martes no puedo. Prefiero hacer las series en pista. Siempre fuerza los lunes.")
    goals = profile["goals"]
    assert goals["availability"]["training_days"] == [1, 3, 4, 5, 6, 7] and goals["availability"]["rest_days"] == [2, 5]
    assert goals["plan_preferences"]["notes"] == ["Prefiero hacer las series en pista", "Siempre fuerza los lunes"]
    assert goals["plan_preferences"]["doubles"] is True  # lo demás de las preferencias, intacto
    assert saved == ["Sin entrenar los martes (descanso obligatorio).", "«Siempre fuerza los lunes»"]
    assert pp.remember(profile, "Los martes no puedo") == []  # ya estaba: nada nuevo que decir
    assert "Lo tendré en cuenta" in pp.saved_markdown(saved) and pp.saved_markdown([]) == ""


def test_notes_keep_the_last_eight_and_are_cut_to_200_chars():
    profile = {"goals": {}}
    for i in range(10):
        pp.remember(profile, f"Prefiero la opción {i}")
    notes = profile["goals"]["plan_preferences"]["notes"]
    assert len(notes) == 8 and notes[0] == "Prefiero la opción 2" and notes[-1] == "Prefiero la opción 9"
    assert len(pp.detect("Prefiero " + "x" * 300)[1][0]) == 200


def test_notes_reach_the_llm_and_the_rules_enforce_the_day():
    start = date(2026, 11, 2)
    f = pb.PlanFrame(start=start, weeks=8, race="10K", race_date=date(2026, 12, 27),
                     distance_km=10.0, target_time="", threshold_pace=240.0, race_pace=None,
                     available_days=[1, 3, 4, 5, 6, 7], notes=["Prefiero hacer las series en pista"])
    text = pb.frame_text(f)
    assert "Siempre (lo dijo el atleta): Prefiero hacer las series en pista" in text
    assert "Días sin entrenar (descanso obligatorio): Martes" in text
    assert "el martes siempre \"rest\"" in pb.rules_text(f)


@pytest.mark.asyncio
async def test_planning_route_saves_first_and_says_so():
    from agent.trainer_agent import TrainerAgent

    agent = object.__new__(TrainerAgent)
    agent.user_profile = {"goals": {"target_race": "Media"}}
    seen = {}

    async def plan_request(message, force_new=False):
        seen["availability"] = dict(agent.user_profile["goals"].get("availability") or {})
        return "revisado"

    agent._plan_request = plan_request
    with patch("agent.routes.handlers._save_user_profile") as save:
        out = await TrainerAgent._route_planning(agent, "ajusta el plan, los martes no puedo entrenar")
    save.assert_called_once()
    assert seen["availability"]["rest_days"] == [2]  # ya vale para esta misma petición
    assert out.startswith("## 📝 Lo tendré en cuenta en tus planes") and out.endswith("revisado")
    agent._plan_request = AsyncMock(return_value=("respaldo", "planning_fallback"))
    with patch("agent.routes.handlers._save_user_profile") as save:
        assert await TrainerAgent._route_planning(agent, "ajusta el plan") == ("respaldo", "planning_fallback")
    save.assert_not_called()
