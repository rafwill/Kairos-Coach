"""
tests/test_plan_events.py
Eventos fijos del atleta en su plan (31/10, petición del usuario): viajes y salidas obligatorias. Validación,
guardado, comprobaciones de cada semana, exenciones de la estructura, enrutado del chat, extracción y readaptación.
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from agent import plan_builder as pb
from agent import plan_events as pe
from agent.main import _parse_plan_command, _with_plan_slash
from agent.router import PLAN_EVENT_ASK_MARK, ToolRouter, _is_plan_event_intent
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales

TODAY = date.today()
START = TODAY - timedelta(days=TODAY.weekday()) + timedelta(weeks=1)  # lunes de la semana que viene
ZONES = [("Z1", 115, 134), ("Z2", 135, 145), ("Z3", 146, 157), ("Z4", 158, 168), ("Z5", 169, 179)]
SATURDAY = START + timedelta(days=5)  # día 6 de la semana 1


def _frame(events=None, **kw) -> pb.PlanFrame:
    args = dict(start=START, weeks=8, race="10K", race_date=START + timedelta(weeks=7, days=6), distance_km=10.0,
                target_time="", threshold_pace=250.0, race_pace=None, hr_zones=ZONES, hr_threshold_tp=169.0,
                events=events or [])
    args.update(kw)
    return pb.PlanFrame(**args)


def _session_event(**kw) -> dict:
    ev = {"id": "e1", "kind": "session", "start": SATURDAY.isoformat(), "end": SATURDAY.isoformat(),
          "title": "Salida en grupo", "sport": "running", "duration_min": 180, "intensity": "easy"}
    return {**ev, **kw}


def _travel(start: date, end: date, cap: int = 0, **kw) -> dict:
    return {"id": "e2", "kind": "travel", "start": start.isoformat(), "end": end.isoformat(), "title": "Viaje",
            "max_minutes": cap, "equipment": "", **kw}


def _day(day: int, kind: str, minutes: float = 0, **extra) -> dict:
    if kind == "rest":
        return {"day": day, "type": "rest"}
    return {"day": day, "type": kind, "steps": [
        {"kind": "warmup", "min": 5, "zone": "FC1"}, {"kind": "steady", "min": minutes - 10, "zone": "FC2"},
        {"kind": "cooldown", "min": 5, "zone": "FC1"}], **extra}


def _week(f: pb.PlanFrame, *days: dict) -> list[dict]:
    used = {d["day"] for d in days}
    return [pb.expand_day(d, f, 1)[0] for d in [*days, *({"day": x, "type": "rest"} for x in range(1, 8) if x not in used)]]


def _event_errors(f: pb.PlanFrame, *days: dict) -> list[str]:
    return [e for e in pb.check_week(1, _week(f, *days), {"minutes": 0}, f) if "«" in e or "viaje" in e]


# ─── Validación ──────────────────────────────────────────────────────────────

class TestNormalize:
    def test_complete_session_is_valid(self):
        raw = {"kind": "session", "start": SATURDAY.isoformat(), "end": SATURDAY.isoformat(), "title": "Salida",
               "sport": "running", "duration_min": 180, "intensity": "easy"}
        event, errors, questions = pe.normalize(raw, TODAY)
        assert errors == [] and questions == [] and event["duration_min"] == 180 and event["sport"] == "running"

    def test_missing_data_is_asked_not_invented(self):
        event, errors, questions = pe.normalize({"kind": "session", "start": SATURDAY.isoformat()}, TODAY)
        assert event is None and errors == []
        assert len(questions) == 3 and any("Cuánto durará" in q for q in questions) and any("intensidad" in q for q in questions)

    def test_travel_needs_the_minutes_even_if_zero(self):
        raw = {"kind": "travel", "start": SATURDAY.isoformat(), "end": (SATURDAY + timedelta(days=2)).isoformat()}
        assert pe.normalize(raw, TODAY)[2] and pe.normalize({**raw, "max_minutes": 0}, TODAY)[0]["max_minutes"] == 0
        assert pe.normalize({**raw, "max_minutes": 40, "equipment": "gimnasio"}, TODAY)[0]["equipment"] == "gimnasio"

    def test_past_dates_a_multi_day_session_and_a_bad_kind_are_errors(self):
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        assert "ya ha pasado" in pe.normalize({"kind": "travel", "start": yesterday, "max_minutes": 0}, TODAY)[1][0]
        multi = {"kind": "session", "start": SATURDAY.isoformat(), "end": (SATURDAY + timedelta(days=1)).isoformat(),
                 "sport": "running", "duration_min": 90, "intensity": "easy"}
        assert "de un día" in pe.normalize(multi, TODAY)[1][0]
        assert "no válido" in pe.normalize({"kind": "fiesta"}, TODAY)[1][0]


# ─── Guardado ────────────────────────────────────────────────────────────────

class TestStorage:
    def test_add_gives_incremental_ids_and_remove_deletes(self):
        profile: dict = {}
        a = pe.add(profile, _session_event(id=""), TODAY)
        b = pe.add(profile, _travel(SATURDAY, SATURDAY), TODAY)
        assert (a["id"], b["id"]) == ("e1", "e2")
        assert [e["id"] for e in pe.remove(profile, ["e1"])] == ["e1"] and [e["id"] for e in pe.load(profile)] == ["e2"]
        assert pe.add(profile, _session_event(id=""), TODAY)["id"] == "e3"

    def test_past_events_are_pruned_and_for_plan_filters_the_window(self):
        old = _travel(TODAY - timedelta(days=5), TODAY - timedelta(days=3))
        mine = _session_event()
        beyond = _travel(START + timedelta(weeks=20), START + timedelta(weeks=20, days=2), id="e3")
        race = START + timedelta(weeks=7, days=6)
        assert pe.prune([old, mine], TODAY) == [mine]
        assert pe.for_plan([old, mine, beyond], START, race, TODAY) == [mine]

    def test_a_recent_trip_stays_saved_for_plan_drift_but_an_old_one_goes(self):
        recent = _travel(TODAY - timedelta(days=5), TODAY - timedelta(days=3), id="e1")
        old = _travel(TODAY - timedelta(days=30), TODAY - timedelta(days=28), id="e2")
        profile = {"goals": {"plan_events": [old, recent]}}
        pe.add(profile, _session_event(id=""), TODAY)
        kept = pe.load(profile)
        assert recent in kept and old not in kept and len(kept) == 2
        assert TODAY - timedelta(days=4) in pe.travel_days(kept)
        assert pe.travel_days([_session_event()]) == set()

    def test_dates_and_minutes_typed_by_hand(self):
        assert pe.parse_dates("15/11/2030", TODAY) == ("2030-11-15", "2030-11-15")
        assert pe.parse_dates("20/11/2030-23/11/2030", TODAY) == ("2030-11-20", "2030-11-23")
        assert pe.parse_dates("2030-11-20 al 2030-11-23", TODAY) == ("2030-11-20", "2030-11-23")
        assert pe.parse_dates("23/11/2030-20/11/2030", TODAY) is None and pe.parse_dates("mañana", TODAY) is None
        assert [pe.parse_minutes(t) for t in ("90", "90 min", "2h", "2h30", "1:30", "abc")] == [90, 90, 120, 150, 90, None]


# ─── Lo que ve el LLM ────────────────────────────────────────────────────────

def test_the_llm_gets_the_events_with_their_id_and_the_rules():
    f = _frame([_session_event(), _travel(SATURDAY + timedelta(days=7), SATURDAY + timedelta(days=9), 40,
                                           equipment="solo gimnasio")])
    text = pb.frame_text(f)
    assert "## Eventos fijos del atleta (obligatorios)" in text and "id e1 · semana 1 · Salida en grupo" in text
    assert "180 min" in text and "como mucho 40 min al día; material: solo gimnasio" in text and "semana 2" in text
    assert "\"event\": \"<id>\"" in pb.rules_text(f)
    assert "Eventos fijos" not in pb.frame_text(_frame()) and "event" not in pb.rules_text(_frame())


# ─── Comprobaciones de la semana ─────────────────────────────────────────────

class TestCheckWeek:
    def test_a_fixed_session_must_be_on_its_day_with_its_duration(self):
        f = _frame([_session_event()])
        assert any("tiene fijado «Salida en grupo»" in e for e in _event_errors(f))  # el día es descanso
        ok = _day(6, "long", 180, event="e1")
        assert _event_errors(f, ok) == []
        assert any("dura 180 min y tu sesión 120" in e for e in _event_errors(f, _day(6, "long", 120, event="e1")))
        assert any("tiene fijado" in e for e in _event_errors(f, _day(6, "long", 180)))  # sin "event": no cuenta

    def test_the_sport_must_match(self):
        f = _frame([_session_event(sport="cycling")])
        assert any("«Salida en grupo» es de bici de carretera" in e
                   for e in _event_errors(f, _day(6, "long", 180, event="e1")))
        bike = {"day": 6, "type": "cross", "sport": "cycling", "event": "e1", "steps": _day(6, "easy", 180)["steps"]}
        assert _event_errors(f, bike) == []
        assert any("es de carrera" in e for e in _event_errors(_frame([_session_event()]), bike))

    def test_travel_without_training_or_with_a_cap(self):
        none = _frame([_travel(START, START + timedelta(days=2), 0)])
        assert any("no puede entrenar" in e for e in _event_errors(none, _day(2, "easy", 40)))
        assert _event_errors(none, _day(4, "easy", 40)) == []  # fuera del viaje
        capped = _frame([_travel(START, START + timedelta(days=2), 40)])
        assert any("como mucho 40 min" in e for e in _event_errors(capped, _day(2, "easy", 60)))
        assert _event_errors(capped, _day(2, "easy", 40)) == []

    def test_days_already_past_are_not_checked(self):
        yesterday = TODAY - timedelta(days=1)
        monday = yesterday - timedelta(days=yesterday.weekday())
        f = _frame([_travel(yesterday, yesterday, 0)], start=monday)
        assert pe.check_week(1, _week(f, _day(yesterday.isoweekday(), "easy", 40)), monday, f.events) == []

    def test_a_fixed_hard_session_does_not_count_as_quality(self):
        f = _frame([_session_event(intensity="hard")], max_quality=1)
        hard = {"day": 6, "type": "quality", "event": "e1", "steps": [
            {"kind": "warmup", "min": 10, "zone": "FC1"}, {"kind": "work", "min": 160, "zone": "FC4"},
            {"kind": "cooldown", "min": 10, "zone": "FC1"}]}
        own = _day(2, "quality", 60)
        assert [e for e in pb.check_week(1, _week(f, own, hard), {"minutes": 0}, f) if "calidad" in e] == []
        loose = {k: v for k, v in hard.items() if k != "event"}
        assert any("2 sesiones de calidad" in e for e in pb.check_week(1, _week(_frame(max_quality=1), own, loose),
                                                                      {"minutes": 0}, _frame(max_quality=1)))


    def test_a_fixed_long_run_lasts_what_the_athlete_said_not_what_the_structure_says(self):
        # 06/11: al replanificar, la estructura daba 100 min de tirada y la salida a la sierra dura 120; el código pedía
        # "quita unos 20 min" a un evento fijo.
        f = _frame([_session_event(duration_min=120)])
        planned = {"minutes": 0, "long_min": 100}
        fixed = _week(f, _day(6, "long", 120, event="e1"))
        assert not [e for e in pb.check_week(1, fixed, planned, f) if "la tirada larga dura" in e]
        loose = _week(f, _day(6, "long", 120))
        assert any("la tirada larga dura 120 min y la estructura dice 100" in e for e in pb.check_week(1, loose, planned, f))


class TestStructure:
    def _macro(self, minutes: float) -> dict:
        weeks = [{"week": w, "phase": "base", "minutes": 100 if w > 1 else minutes, "long_min": 40} for w in range(1, 9)]
        weeks[-1]["phase"] = "race"
        return {"title": "Plan", "weeks": weeks}

    def test_the_event_minutes_are_the_floor_and_lift_the_volume_caps(self):
        base = dict(weekly_hours=3.0, recent_weekly_minutes=100.0)
        without = pb.check_macro(self._macro(240), _frame(**base))
        assert any("Semana 1" in e and "superan las horas disponibles" in e for e in without)
        assert any("Semana 1" in e and "suben más de un 10 %" in e for e in without)
        with_event = pb.check_macro(self._macro(240), _frame([_session_event(duration_min=240)], **base))
        assert [e for e in with_event if e.startswith("Semana 1")] == []
        short = pb.check_macro(self._macro(200), _frame([_session_event(duration_min=240)], **base))
        assert any("no caben los eventos fijos" in e for e in short)

    def test_a_fixed_long_run_is_not_limited_by_the_long_run_rules(self):
        long_week = self._macro(240)
        long_week["weeks"][0]["long_min"] = 180
        base = dict(weekly_hours=6.0, recent_weekly_minutes=230.0)
        assert any("la tirada larga" in e for e in pb.check_macro(long_week, _frame(**base)))
        assert not [e for e in pb.check_macro(long_week, _frame([_session_event()], **base)) if "la tirada larga" in e]


# ─── Enrutado y comandos ─────────────────────────────────────────────────────

class TestIntent:
    @pytest.mark.parametrize("phrase", [
        "El sábado 15 tengo una salida en grupo de 3 horas",
        "Del 20 al 23 de noviembre estoy de viaje, solo puedo correr 40 min",
        "Mañana viajo a Madrid y no podré entrenar",
        "Este fin de semana hay una quedada con el club",
        "Quita el viaje del 20",
        "El sabado dia 14 tengo una salida a la sierra programada. ¿Puedes actualizar el plan de la semana que viene?",
        "Ajusta el plan: el domingo tengo una ruta con amigos",
    ])
    def test_event_phrases(self, phrase):
        assert _is_plan_event_intent(phrase, []) and ToolRouter().route_key(phrase, [], {}) == "plan_event"

    @pytest.mark.parametrize("phrase", [
        "¿Cómo dormí en mi viaje del martes?", "Entrené el sábado en la salida en grupo", "¿Qué me toca el sábado?",
        "Ajusta el plan, tengo una molestia en el gemelo", "Cuántos km hice el domingo", "/plan evento",
        "Mi salida del sábado fue dura", "¿Cómo fue mi salida del sábado?", "Analiza mi última salida de trail",
    ])
    def test_other_phrases(self, phrase):
        assert not _is_plan_event_intent(phrase, [])

    def test_the_answer_to_a_missing_data_question_follows_the_same_route(self):
        history = [{"role": "user", "content": "El sábado tengo una salida en grupo"},
                   {"role": "assistant", "content": f"## Necesito un dato más para {PLAN_EVENT_ASK_MARK}\n- ¿Cuánto durará?"}]
        assert _is_plan_event_intent("3 horas y suave", history)
        assert not _is_plan_event_intent("3 horas y suave", history + [{"role": "assistant", "content": "Listo."}])


def test_plan_commands():
    assert _parse_plan_command("/plan evento") == ("event", None)
    assert _parse_plan_command("/plan eventos") == ("events", None)
    assert _parse_plan_command("/plan eventos quitar e1") == ("events", "quitar e1")
    assert _with_plan_slash("plan evento") == "/plan evento"


# ─── Extracción, guardado y readaptación ─────────────────────────────────────

def _agent(profile=None):
    from agent.trainer_agent import TrainerAgent

    agent = object.__new__(TrainerAgent)
    agent.user_profile = profile if profile is not None else {"goals": {}}
    agent.model = "principal"
    agent._chat_llm_model = lambda: "ligero"
    agent._route_plan_revision = AsyncMock(return_value="## 🗓️ Cambios en el plan\n- sábado")
    agent._build_plan_load_context = AsyncMock(return_value={})
    return agent


LLM_PLAN = {"id": "p1", "status": "active", "title": "Plan", "plan_data": {"author": "llm", "start_date": START.isoformat()}}


async def _apply(agent, raws, removals=None, plan=LLM_PLAN):
    with patch("agent.routes.handlers._save_user_profile") as save, \
            patch("agent.routes.handlers._storage.get_active_training_plan", return_value=plan), \
            patch("agent.routes.handlers._storage.list_training_plan_sessions", return_value=[]):
        out = await agent._apply_plan_events(raws, removals or [])
    return out, save


def _raw(day: date, **kw) -> dict:
    return {"kind": "session", "start": day.isoformat(), "end": day.isoformat(), "title": "Salida en grupo",
            "sport": "running", "duration_min": 180, "intensity": "easy", **kw}


@pytest.mark.asyncio
async def test_extraction_uses_the_chat_model_and_the_calendar():
    agent = _agent({"goals": {"plan_events": [_travel(SATURDAY, SATURDAY)]}})
    agent._plan_llm_json = AsyncMock(return_value=({"events": [], "remove": []}, [], []))
    data, errors = await agent._extract_plan_events("el sábado salida en grupo", [{"role": "user", "content": "hola"}])
    messages = agent._plan_llm_json.await_args.args[0]
    assert data == {"events": [], "remove": []} and errors == []
    assert agent._plan_llm_json.await_args.kwargs["model"] == "ligero"
    prompt = messages[0]["content"]
    assert "el sábado salida en grupo" in prompt and "`e2`" in prompt and "- hola" in prompt


@pytest.mark.asyncio
async def test_missing_data_asks_and_saves_nothing():
    agent = _agent()
    out, save = await _apply(agent, [{"kind": "session", "start": SATURDAY.isoformat()}])
    assert PLAN_EVENT_ASK_MARK in out.lower() and "¿Cuánto durará?" in out
    assert pe.load(agent.user_profile) == [] and not save.called


@pytest.mark.asyncio
async def test_an_event_this_week_or_next_is_saved_and_revises_the_plan_right_away():
    agent = _agent()
    soon = TODAY + timedelta(days=2)
    out, save = await _apply(agent, [_raw(soon)])
    assert out.startswith("## 📅 Evento guardado") and "`e1` Salida en grupo" in out and "Cambios en el plan" in out
    assert [e["id"] for e in pe.load(agent.user_profile)] == ["e1"] and save.called
    message = agent._route_plan_revision.await_args.args[0]
    assert "eventos obligatorios" in message and "Salida en grupo" in message


@pytest.mark.asyncio
async def test_a_far_event_is_saved_and_offers_to_replan_without_spending_requests():
    agent = _agent()
    far = TODAY + timedelta(days=30)
    out, _ = await _apply(agent, [_raw(far)])
    assert "replanifica el plan" in out and not agent._route_plan_revision.called
    assert len(pe.load(agent.user_profile)) == 1


@pytest.mark.asyncio
async def test_without_an_llm_plan_it_is_kept_for_when_the_plan_is_created():
    agent = _agent()
    out, _ = await _apply(agent, [_raw(TODAY + timedelta(days=2))], plan=None)
    assert "cuando crees el plan" in out and not agent._route_plan_revision.called


@pytest.mark.asyncio
async def test_removing_an_event_keeps_the_plan_and_says_how_to_recover_the_days():
    agent = _agent({"goals": {"plan_events": [_session_event()]}})
    out, save = await _apply(agent, [], ["e1"])
    assert "Evento quitado" in out and "ajusta el plan" in out and pe.load(agent.user_profile) == [] and save.called
    assert not agent._route_plan_revision.called


@pytest.mark.asyncio
async def test_the_plan_frame_carries_the_saved_events():
    agent = _agent({"goals": {"target_race": "Media", "target_race_date": (START + timedelta(weeks=9)).isoformat(),
                              "plan_events": [_session_event(), _travel(TODAY - timedelta(days=9), TODAY - timedelta(days=8))]},
                    "health": {}})
    f = agent._plan_frame({})
    assert [e["id"] for e in f.events] == ["e1"]  # el que ya pasó se queda fuera


class TestAnchorYear:
    """05/11: el modelo del chat devolvió 2025-11-14 para "el sábado 14" y el evento se rechazó por pasado."""

    def test_without_a_year_in_the_message_the_code_puts_it(self):
        today = date(2026, 11, 5)
        raw = {"kind": "session", "start": "2025-11-14", "end": "2025-11-14"}
        out = pe.anchor_year(raw, "El sábado 14 tengo una salida a la sierra", today)
        assert out["start"] == out["end"] == "2026-11-14"
        rng = pe.anchor_year({"start": "2025-12-30", "end": "2026-01-02"}, "del 30 al 2 estoy de viaje", today)
        assert (rng["start"], rng["end"]) == ("2026-12-30", "2027-01-02")  # conserva lo que dura
        jan = pe.anchor_year({"start": "2026-01-10"}, "el 10 de enero tengo una quedada", today)
        assert jan["start"] == "2027-01-10"  # ese día de este año ya pasó: el siguiente

    def test_a_year_in_the_message_or_a_right_date_is_left_alone(self):
        today = date(2026, 11, 5)
        raw = {"start": "2025-11-14", "end": "2025-11-14"}
        assert pe.anchor_year(raw, "el 14/11/2025 tuve una salida", today) is raw
        assert pe.anchor_year(raw, "en 2025 tenía una salida el 14", today) is raw
        ok = {"start": "2026-11-14", "end": "2026-11-14"}
        assert pe.anchor_year(ok, "el sábado 14", today) is ok
