"""
tests/test_tool_groups.py
Metatooling (agent/tool_groups.py, 26/10): qué tools recibe el LLM según la pregunta.
"""

import asyncio
import json

from agent import tool_groups as tg

GARMIN_42 = {tool for tools in tg.GROUPS.values() for tool in tools}
KAIROS = ("kairos_load_trends", "kairos_correlate", "kairos_weekly_sport_breakdown")


def _schema(names):
    return [{"type": "function", "function": {"name": n, "description": "", "parameters": {}}} for n in names]


def _names(schema):
    return [t["function"]["name"] for t in schema]


def test_groups_cover_the_42_garmin_tools_once():
    assert len(GARMIN_42) == 42 == sum(len(t) for t in tg.GROUPS.values())


def test_server_tools_are_all_grouped():
    # Una tool nueva del servidor sin grupo iría siempre (no se pierde), pero debe ponerse en su grupo.
    from garmin_mcp.server import mcp

    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == GARMIN_42


def test_questions_pick_their_groups():
    cases = {
        "¿Cuánto estrés tuve ayer?": {"dia_a_dia"},
        "¿Cómo ha evolucionado mi VO2max?": {"rendimiento"},
        "¿Cuáles son mis predicciones de carrera para 10K?": {"rendimiento", "actividades"},
        "¿Qué tal mi saturación de oxígeno?": {"dia_a_dia"},
        "Analiza las vueltas de mi última carrera": {"actividades"},
        "¿Cómo he dormido?": {"sueno_recuperacion"},
        "¿Cómo está mi composición corporal?": {"semanal_cuerpo"},
        "Hola, ¿qué tal?": set(),
    }
    for question, expected in cases.items():
        assert tg.groups_for(question) == expected, question


def test_select_sends_the_groups_kairos_tools_and_the_request_tool():
    schema = _schema([*sorted(GARMIN_42), *KAIROS])
    out = _names(tg.select(schema, {"dia_a_dia"}))
    assert set(out) == {*tg.GROUPS["dia_a_dia"], *KAIROS, tg.REQUEST_TOOL}
    assert len(out) < len(schema) // 2


def test_no_group_sends_everything_as_before():
    schema = _schema([*sorted(GARMIN_42), *KAIROS])
    assert _names(tg.select(schema, set())) == _names(schema)


def test_all_groups_need_no_request_tool():
    schema = _schema([*sorted(GARMIN_42), *KAIROS])
    assert tg.REQUEST_TOOL not in _names(tg.select(schema, set(tg.GROUPS)))


def test_request_tool_adds_the_group_for_the_next_call():
    from agent.trainer_agent import TrainerAgent

    agent = object.__new__(TrainerAgent)
    agent._turn_tool_groups = {"dia_a_dia"}
    out = json.loads(asyncio.run(agent._handle_internal_tool(tg.REQUEST_TOOL, {"grupo": "rendimiento"})))
    assert out["ok"] and "get_vo2max_trend" in out["herramientas"]
    assert agent._turn_tool_groups == {"dia_a_dia", "rendimiento"}
    bad = json.loads(asyncio.run(agent._handle_internal_tool(tg.REQUEST_TOOL, {"grupo": "inventado"})))
    assert "error" in bad
