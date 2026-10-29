"""
tests/test_plan_llm.py
El LLM construye el plan y el código lo comprueba (agent/plan_builder.py, agent/plan_llm.py, 28/10).
Criterios de cierre fijados antes de medir: el código calcula ritmos, km y TSS de los tramos; cada regla de
estructura y de semana detecta su fallo; los fallos vuelven al LLM y la versión corregida se acepta; si no hay
plan válido tras los intentos, no se devuelve plan.
"""

import json
import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from agent import plan_builder as pb
from agent import run_plan as rp
from agent.plan_llm import PlanLLMMixin
from agent.plans import _plan_detail_markdown, _validate_structured_plan

ZONES = [("Z1", 115, 134), ("Z2", 135, 145), ("Z3", 146, 157), ("Z4", 158, 168), ("Z5", 169, 179)]
START = date.today() - timedelta(days=date.today().weekday())  # lunes de esta semana (la revisión parte de hoy)
RACE = START + timedelta(weeks=7, days=6)  # domingo de la semana 8


def _frame(**kw) -> pb.PlanFrame:
    args = dict(start=START, weeks=8, race="10K de Navidad", race_date=RACE, distance_km=10.0, target_time="45:00",
                threshold_pace=250.0, race_pace=270.0, hr_zones=ZONES, hr_threshold_tp=169.0, weekly_hours=8.0,
                recent_weekly_minutes=300.0, longest_recent_min=90.0, available_days=list(range(1, 8)))
    args.update(kw)
    return pb.PlanFrame(**args)


MACRO = {"title": "Plan hacia el 10K de Navidad", "rationale": "Base, construcción y específico con descarga.", "weeks": [
    {"week": 1, "phase": "base", "deload": False, "minutes": 320, "long_min": 90, "focus": "fartlek"},
    {"week": 2, "phase": "base", "deload": False, "minutes": 345, "long_min": 100, "focus": "cuestas"},
    {"week": 3, "phase": "build", "deload": False, "minutes": 370, "long_min": 110, "focus": "umbral"},
    {"week": 4, "phase": "build", "deload": True, "minutes": 290, "long_min": 80, "focus": "descarga"},
    {"week": 5, "phase": "specific", "deload": False, "minutes": 395, "long_min": 120, "focus": "RP"},
    {"week": 6, "phase": "specific", "deload": False, "minutes": 420, "long_min": 125, "focus": "RP"},
    {"week": 7, "phase": "taper", "deload": False, "minutes": 340, "long_min": 95, "focus": "afinamiento"},
    {"week": 8, "phase": "race", "deload": False, "minutes": 250, "long_min": 0, "focus": "carrera"},
]}


def _quality(title="Fartlek 5×(3'/2')", zone="Z4"):
    return {"type": "quality", "title": title, "rpe": "7", "steps": [
        {"kind": "warmup", "min": 15, "zone": "Z2"},
        {"repeat": 5, "steps": [{"kind": "work", "min": 3, "zone": zone}, {"kind": "recovery", "min": 2, "zone": "Z2"}]},
        {"kind": "cooldown", "min": 10, "zone": "Z1"}]}


def _easy(minutes):
    return {"type": "easy", "title": "Rodaje suave", "steps": [
        {"kind": "warmup", "min": 10, "zone": "Z1"}, {"kind": "steady", "min": minutes - 15, "zone": "Z2"},
        {"kind": "cooldown", "min": 5, "zone": "Z1"}]}


def _week_days(w: dict) -> list[dict]:
    if w["phase"] == "race":
        race = {"type": "race", "title": "10K de Navidad", "steps": [
            {"kind": "warmup", "min": 12, "zone": "Z1"}, {"kind": "work", "km": 10, "zone": "RP"},
            {"kind": "cooldown", "min": 10, "zone": "Z1"}]}
        days = [{"type": "rest"}, _quality(), _easy(45), _easy(45), {"type": "rest"}, _easy(40), race]
    else:
        easy = (w["minutes"] - 40 - 100 - w["long_min"]) / 2
        long = {"type": "long", "title": "Tirada larga", "steps": [
            {"kind": "warmup", "min": 10, "zone": "Z1"}, {"kind": "steady", "min": w["long_min"] - 15, "zone": "Z2"},
            {"kind": "cooldown", "min": 5, "zone": "Z1"}]}
        zones = ["Z3", "Z4", "Z5a", "Z5b", "Z5c", "Z4", "Z5a", "Z3"]  # la calidad progresa de una semana a otra
        days = [{"type": "strength", "title": "Fuerza", "minutes": 40, "rpe": "6-7", "note": "3×8"},
                _quality(zone=zones[w["week"] - 1]), _easy(easy), _quality("Umbral 5×(3'/2')", zones[w["week"]]),
                {"type": "rest"}, _easy(easy), long]
    return [{"day": i, **d} for i, d in enumerate(days, 1)]


def _weeks_json(weeks: list[int]) -> dict:
    return {"weeks": [{"week": n, "days": _week_days(MACRO["weeks"][n - 1])} for n in weeks]}


class TestStepsAreComputedByTheCode:
    def test_pace_step_gets_its_range_km_and_rtss(self):
        f = _frame()
        session, errors = pb.expand_day({"day": 2, **_quality()}, f, 1)
        assert errors == []
        wk = session["structured_workout"]
        work = wk["steps"][1]["steps"][0]
        assert work["target"]["range"] == list(rp.Paces(250, 270).zone("Z4")) or tuple(work["target"]["range"]) == rp.Paces(250, 270).zone("Z4")
        minutes, km, tss = pb.workout_totals(wk["steps"], f)
        assert (minutes, km, tss) == (wk["summary"]["duration_min"], wk["summary"]["distance_km"], wk["summary"]["tss"])
        assert minutes == 50 and km > 0 and session["session_type"] == "running_quality"

    def test_hr_step_uses_tp_hrtss_table(self):
        f = _frame()
        day = {"day": 3, "type": "cross", "title": "Bici suave", "steps": [
            {"kind": "warmup", "min": 10, "zone": "FC1"}, {"kind": "steady", "min": 60, "zone": "FC2"},
            {"kind": "cooldown", "min": 10, "zone": "FC1"}]}
        session, errors = pb.expand_day(day, f, 1)
        assert errors == []
        # FC2 140/169 = 0.83 → 40.8 TSS/h; FC1 124.5/169 → 40.8 TSS/h
        assert session["structured_workout"]["summary"]["tss"] == round(80 / 60 * 40.8, 1)
        assert session["structured_workout"]["summary"]["distance_km"] == 0  # bici o cross por FC: sin km
        run = dict(day, type="easy", title="Rodaje por FC")
        run_session, _ = pb.expand_day(run, f, 1)
        # Rodaje por FC: km con el ritmo medio de la zona equivalente (FC1 → Z1, FC2 → Z2); el TSS sigue siendo hrTSS.
        z1, z2 = rp.Paces(250, 270).zone("Z1"), rp.Paces(250, 270).zone("Z2")
        expected = round(20 * 60 / (sum(z1) / 2) + 60 * 60 / (sum(z2) / 2), 1)
        assert run_session["structured_workout"]["summary"]["distance_km"] == expected
        assert run_session["structured_workout"]["summary"]["tss"] == session["structured_workout"]["summary"]["tss"]
        lines = rp.session_lines(session, START, ZONES)
        assert any("FC Z2 135–145 lpm" in x for x in lines)

    def test_invalid_steps_are_reported_not_guessed(self):
        f = _frame()
        _, errors = pb.expand_day({"day": 2, "type": "easy", "steps": [{"kind": "steady", "m": 5000, "zone": "FC2"}]}, f, 1)
        assert any("por tiempo" in e for e in errors)
        _, errors = pb.expand_day({"day": 2, "type": "easy", "steps": [{"kind": "steady", "min": 30, "zone": "Z9"}]}, f, 1)
        assert any("no válida" in e for e in errors)
        _, errors = pb.expand_day({"day": 2, "type": "easy", "steps": [{"kind": "steady", "min": 30, "zone": "Z2"}]}, f, 1)
        assert any("warmup" in e for e in errors)
        _, errors = pb.expand_day({"day": 2, "type": "easy", "steps": [{"kind": "warmup", "min": 30, "zone": "Z2"}]},
                                  _frame(threshold_pace=None), 1)
        assert any("zonas de FC" in e for e in errors)


class TestAutoDurations:
    """El LLM marca "min": "auto" y el código pone la duración: la semana suma sus minutos y la tirada, lo previsto."""

    def _auto_week(self, w):
        auto_easy = {"type": "easy", "title": "Rodaje suave", "steps": [
            {"kind": "warmup", "min": 10, "zone": "Z1"}, {"kind": "steady", "min": "auto", "zone": "Z2"},
            {"kind": "cooldown", "min": 5, "zone": "Z1"}]}
        long = {"type": "long", "title": "Tirada larga", "steps": [
            {"kind": "warmup", "min": 10, "zone": "Z1"}, {"kind": "steady", "min": "auto", "zone": "Z2"},
            {"repeat": 2, "steps": [{"kind": "work", "min": 10, "zone": "RP"}, {"kind": "recovery", "min": 3, "zone": "Z2"}]},
            {"kind": "cooldown", "min": 5, "zone": "Z1"}]}
        days = [{"type": "strength", "title": "Fuerza", "minutes": 40}, _quality(), auto_easy, _quality("Umbral", "Z5a"),
                {"type": "rest"}, auto_easy, long]
        return {"week": w["week"], "days": [{"day": i, **d} for i, d in enumerate(days, 1)]}

    def test_week_and_long_run_match_the_structure(self):
        w = MACRO["weeks"][4]  # 395 min, tirada 120
        sessions, errors = pb.expand_week(self._auto_week(w), _frame(), w)
        assert errors == []
        total = sum(s["structured_workout"]["summary"]["duration_min"] for s in sessions)
        long = next(s for s in sessions if s["session_type"] == "long_run")["structured_workout"]
        assert abs(total - 395) <= 1 and long["summary"]["duration_min"] == 120
        assert not any(st.get("auto") for s in sessions for st in s["structured_workout"].get("steps") or [])
        assert pb.check_week(5, sessions, w, _frame()) == []

    def test_impossible_fill_goes_back_to_the_llm(self):
        w = {**MACRO["weeks"][4], "minutes": 900}
        _, errors = pb.expand_week(self._auto_week(w), _frame(), w)
        assert any("cada rodaje \"auto\" duraría" in e and "añade un" in e for e in errors)
        bad = self._auto_week(MACRO["weeks"][4])
        bad["days"][1]["steps"][1]["steps"][0] = {"kind": "steady", "min": "auto", "zone": "Z4"}
        _, errors = pb.expand_week(bad, _frame(), MACRO["weeks"][4])
        assert any("fuera de las series" in e for e in errors)


class TestMacroRules:
    def test_the_reference_macro_passes(self):
        assert pb.check_macro(MACRO, _frame()) == []

    @pytest.mark.parametrize("week, field, value, expected", [
        (1, "minutes", 340, "más de un 10 % sobre el volumen que sostiene"),
        (3, "minutes", 390, "más de un 10 % sobre la semana de carga anterior"),
        (4, "minutes", 330, "la descarga debe bajar"),
        (7, "minutes", 400, "el afinamiento debe bajar"),
        (2, "long_min", 140, "40 %"),
        (5, "phase", "race", "la fase \"race\" es solo la semana 8"),
        (4, "deload", False, "más de 3 semanas de carga seguidas"),
    ])
    def test_each_rule_catches_its_failure(self, week, field, value, expected):
        macro = json.loads(json.dumps(MACRO))
        macro["weeks"][week - 1][field] = value
        if field == "deload":
            macro["weeks"][3]["minutes"] = 380  # semana de carga normal en lugar de descarga
        errors = pb.check_macro(macro, _frame())
        assert any(expected in e for e in errors), errors

    def test_week_numbers_must_cover_the_plan(self):
        macro = {**MACRO, "weeks": MACRO["weeks"][:5]}
        assert any("semanas 1 a 8" in e for e in pb.check_macro(macro, _frame()))


class TestWeekRules:
    def _sessions(self, n, mutate=None):
        days = _week_days(MACRO["weeks"][n - 1])
        if mutate:
            mutate(days)
        sessions, errors = pb.expand_week({"week": n, "days": days}, _frame())
        assert errors == []
        return sessions

    def test_reference_weeks_pass(self):
        f = _frame()
        for n in range(1, 9):
            assert pb.check_week(n, self._sessions(n), MACRO["weeks"][n - 1], f) == [], n

    def test_rules(self):
        f = _frame(available_days=[1, 2, 3, 4, 6, 7])
        assert any("no entrena" in e for e in pb.check_week(1, self._sessions(1, lambda d: d.__setitem__(4, {"day": 5, **_easy(30)})), MACRO["weeks"][0], f))
        f = _frame()
        assert any("los 7 días" in e for e in pb.check_week(1, self._sessions(1, lambda d: d.pop()), MACRO["weeks"][0], f))
        assert any("días seguidos" in e for e in pb.check_week(1, self._sessions(1, lambda d: d.__setitem__(2, {"day": 3, **_quality()})), MACRO["weeks"][0], f))
        assert any("suman" in e for e in pb.check_week(1, self._sessions(1), {**MACRO["weeks"][0], "minutes": 500}, f))
        moved = pb.check_week(8, self._sessions(8, lambda d: (d.__setitem__(5, {**d[6], "day": 6}), d.__setitem__(6, {"day": 7, "type": "rest"}))), MACRO["weeks"][7], f)
        assert any("la carrera" in e for e in moved)


class _FakeLLM:
    """Responde como un LLM: estructura y semanas; `bad_first` estropea la primera respuesta de la semana 1."""

    def __init__(self, bad_first=False, always_bad=False, revisions=None, overshoot=False, bad_first_conversation=False):
        self.calls = []
        self.bad_first_conversation = bad_first_conversation
        self.week1_conversations = 0
        self.overshoot = overshoot
        self.revisions = list(revisions or [])
        self.bad_first = bad_first
        self.always_bad = always_bad
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, model, messages, **kw):
        self.calls.append(messages)
        prompt = messages[1]["content"]
        if "quedan guardados en el plan" in messages[-1]["content"]:
            text = "Cambio el martes de la semana 2 por un rodaje suave de 50 min por la molestia del gemelo."
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None)
        if "El atleta dice" in prompt:
            data = self.revisions.pop(0)
        elif "Diseña la estructura" in prompt:
            data = MACRO
        else:
            weeks = [int(x) for x in re.search(r"semanas ([\d, ]+), y solo esas", prompt).group(1).split(",")]
            data = _weeks_json(weeks)
            if 2 in weeks and self.overshoot and len(messages) == 2:
                # Semana 2 con 380 min: dentro del ±12 % de su estructura (345), pero +19 % sobre lo que suma la 1 (320).
                data = json.loads(json.dumps(data))
                data["weeks"][1]["days"][2] = {"day": 3, **_easy(52.5 + 35)}
            if 1 in weeks and len(messages) == 2:
                self.week1_conversations += 1
            if 1 in weeks and (self.always_bad or (self.bad_first and len(messages) == 2)
                               or (self.bad_first_conversation and self.week1_conversations == 1)):
                data = json.loads(json.dumps(data))
                data["weeks"][0]["days"].pop()
        content = "```json\n" + json.dumps(data) + "\n```"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


class _Agent(PlanLLMMixin):
    def __init__(self, llm):
        self.client = llm
        self.model = "fake"
        self.provider = "fake"
        self.total_prompt_tokens = self.total_completion_tokens = 0
        self.user_profile = {}
        self.mcp_session = None

    def _profile_context_block(self):
        return ""

    def _plan_frame(self, load_context, user_message="", plan=None):
        return _frame()


class TestTheLLMBuildsThePlan:
    @pytest.mark.asyncio
    async def test_failures_go_back_to_the_llm_and_the_corrected_plan_is_kept(self, monkeypatch):
        monkeypatch.setattr("agent.plan_llm._add_ia_tokens", lambda n: None)
        llm = _FakeLLM(bad_first=True)
        plan, sessions, errors = await _Agent(llm)._build_plan_with_llm("Crea un plan nuevo", {})
        assert errors == [] and plan is not None
        corrections = [m for m in llm.calls if len(m) > 2]
        assert len(corrections) == 1 and "los 7 días" in corrections[0][-1]["content"]
        assert len(llm.calls) == 1 + 2 + 1  # estructura + 2 tandas de 4 semanas + 1 corrección
        assert pb.chunk_weeks(13, 4) == [[1, 2, 3, 4], [5, 6, 7], [8, 9, 10], [11, 12, 13]]
        assert pb.chunk_weeks(8, 4) == [[1, 2, 3, 4], [5, 6, 7, 8]] and pb.chunk_weeks(3, 4) == [[1, 2, 3]]
        assert plan["plan_data"]["author"] == "llm" and plan["duration_weeks"] == 8
        assert _validate_structured_plan(plan, sessions, {"goals": {"weekly_training_hours": 8}}) == []
        md = _plan_detail_markdown(plan, sessions, {}, weeks=[1])
        assert "| Enfoque |" in md and "Fartlek 5×(3'/2')" in md

    @pytest.mark.asyncio
    async def test_progression_is_checked_on_what_the_days_really_add_up_to(self, monkeypatch):
        monkeypatch.setattr("agent.plan_llm._add_ia_tokens", lambda n: None)
        llm = _FakeLLM(overshoot=True)
        plan, sessions, errors = await _Agent(llm)._build_plan_with_llm("Crea un plan", {})
        assert errors == [] and plan is not None
        fixes = [m[-1]["content"] for m in llm.calls if len(m) > 2]
        assert len(fixes) == 1 and "Semana 2: 380 min suben más de un 10 %" in fixes[0] and "suma real" in fixes[0]
        totals = plan["plan_data"]["week_minutes"]
        assert totals[1] <= totals[0] * 1.10

    @pytest.mark.asyncio
    async def test_a_batch_that_never_complies_starts_over_once(self, monkeypatch):
        monkeypatch.setattr("agent.plan_llm._add_ia_tokens", lambda n: None)
        llm = _FakeLLM(bad_first_conversation=True)
        plan, _, errors = await _Agent(llm)._build_plan_with_llm("Crea un plan", {})
        assert errors == [] and plan is not None and llm.week1_conversations == 2

    @pytest.mark.asyncio
    async def test_no_plan_when_the_llm_never_complies(self, monkeypatch):
        monkeypatch.setattr("agent.plan_llm._add_ia_tokens", lambda n: None)
        plan, sessions, errors = await _Agent(_FakeLLM(always_bad=True))._build_plan_with_llm("Crea un plan", {})
        assert plan is None and sessions == [] and any("los 7 días" in e for e in errors)


class TestTheLLMRevisesThePlan:
    @pytest.mark.asyncio
    async def test_only_days_from_today_change_and_the_rules_still_apply(self, monkeypatch):
        monkeypatch.setattr("agent.plan_llm._add_ia_tokens", lambda n: None)

        async def no_activities(*a, **k):
            return []

        monkeypatch.setattr("agent.plan_llm._fetch_activities_for_load_calc", no_activities)
        monkeypatch.setattr("agent.plan_llm._storage.get_activity_tss_map", lambda start: {})
        swap = {"day": 2, "type": "easy", "title": "Rodaje suave por molestia", "steps": [
            {"kind": "warmup", "min": 10, "zone": "Z1"}, {"kind": "steady", "min": 35, "zone": "Z2"},
            {"kind": "cooldown", "min": 5, "zone": "Z1"}]}
        too_late = {"reply": "x", "weeks": [{"week": 3, "days": [swap]}]}
        good = {"reply": "Cambio las series del martes por un rodaje suave por la molestia del gemelo.",
                "weeks": [{"week": 2, "days": [swap]}]}
        llm = _FakeLLM(revisions=[too_late, good])
        agent = _Agent(llm)
        plan, sessions, _ = await agent._build_plan_with_llm("Crea un plan", {})
        new, reply, errors, _ = await agent._revise_plan_with_llm("Me molesta el gemelo", plan, sessions, {})
        assert errors == [] and "gemelo" in reply
        assert any("solo puedes cambiar las semanas [1, 2]" in m[-1]["content"] for m in llm.calls)
        # El texto para el atleta se escribe con los días guardados, ya calculados.
        assert "Rodaje suave por molestia · 0:50:00" in llm.calls[-1][-1]["content"]
        before = {(s["week_index"], s["day_index"]): s for s in sessions}
        diff = [(s["week_index"], s["day_index"]) for s in new if s is not before[(s["week_index"], s["day_index"])]]
        assert diff == [(2, 2)] and len(new) == len(sessions)
        assert new[8]["structured_workout"]["title"] == "Rodaje suave por molestia"

    @pytest.mark.asyncio
    async def test_a_revision_cannot_raise_the_week_more_than_10_percent(self, monkeypatch):
        monkeypatch.setattr("agent.plan_llm._add_ia_tokens", lambda n: None)

        async def no_activities(*a, **k):
            return []

        monkeypatch.setattr("agent.plan_llm._fetch_activities_for_load_calc", no_activities)
        monkeypatch.setattr("agent.plan_llm._storage.get_activity_tss_map", lambda start: {})
        big = {"reply": "más", "weeks": [{"week": 2, "days": [{"day": 5, **_easy(120)}]}]}
        agent = _Agent(_FakeLLM(revisions=[big] * 4))  # una por intento
        plan, sessions, _ = await agent._build_plan_with_llm("Crea un plan", {})
        new, _, errors, _ = await agent._revise_plan_with_llm("Quiero más", plan, sessions, {})
        assert new is None and any("como mucho" in e for e in errors)


class TestCoachPreferences:
    """29/10, decisiones del usuario: 20 % en Z3 o más, días dobles, ritmo concreto o zona, progresión 3+1 o 2+1."""

    def _week(self, mutate=None, **frame):
        days = _week_days(MACRO["weeks"][4])
        if mutate:
            mutate(days)
        f = _frame(**frame)
        sessions, errors = pb.expand_week({"week": 5, "days": days}, f)
        assert errors == []
        return pb.check_week(5, sessions, MACRO["weeks"][4], f)

    def test_at_most_20_percent_of_the_week_in_z3_or_above(self):
        assert self._week() == []
        hard = {"type": "quality", "title": "Umbral largo", "steps": [
            {"kind": "warmup", "min": 15, "zone": "Z2"}, {"kind": "work", "min": 80, "zone": "Z4"},
            {"kind": "cooldown", "min": 10, "zone": "Z1"}]}
        errors = self._week(lambda d: d.__setitem__(3, {"day": 4, **hard}))
        assert any("en Z3 o más (RP incluido: martes 15, jueves 80); como mucho el 20 %" in e and "jueves: 80 → 67 min" in e for e in errors), errors

    def test_doubles_only_if_the_athlete_doubles_and_never_on_a_rest_day(self):
        gym = {"day": 3, "type": "strength", "title": "Gimnasio", "minutes": 40}
        assert any("no dobla" in e for e in self._week(lambda d: d.append(gym)))
        assert not any("sesiones" in e for e in self._week(lambda d: d.append(gym), doubles=True))
        rest_double = {"day": 5, "type": "strength", "title": "Gimnasio", "minutes": 40}
        assert any("es descanso" in e for e in self._week(lambda d: d.append(rest_double), doubles=True))
        capped = self._week(lambda d: d.append(gym), doubles=True, max_minutes_per_day={3: 60})
        assert any("sumando sus sesiones" in e for e in capped)

    def test_double_day_sessions_keep_their_order(self):
        f = _frame(doubles=True)
        days = _week_days(MACRO["weeks"][4]) + [{"day": 3, "type": "strength", "title": "Gimnasio", "minutes": 40}]
        sessions, _ = pb.expand_week({"week": 5, "days": days}, f)
        wednesday = [s for s in sessions if s["day_index"] == 3]
        assert [s["structured_workout"]["slot"] for s in wednesday] == [1, 2]
        assert len({pb.session_key(s) for s in sessions}) == len(sessions)

    def test_a_segment_can_go_by_exact_pace_or_by_hr_zone(self):
        f = _frame()
        day = {"day": 2, "type": "quality", "title": "4×1000 m a 4:00", "steps": [
            {"kind": "warmup", "min": 15, "zone": "FC2"},
            {"repeat": 4, "steps": [{"kind": "work", "m": 1000, "pace": "4:00"}, {"kind": "recovery", "min": 2, "zone": "Z1"}]},
            {"kind": "cooldown", "min": 10, "zone": "FC1"}]}
        session, errors = pb.expand_day(day, f, 1)
        assert errors == []
        work = session["structured_workout"]["steps"][1]["steps"][0]
        assert work["target"]["range"] == [237, 243] and work["duration_min"] == 4.0
        lines = rp.session_lines(session, START, ZONES)
        assert any("1000 m @ 4:00/km" in x for x in lines) and any("FC Z2 135–145 lpm" in x for x in lines)
        _, errors = pb.expand_day({**day, "steps": [{"kind": "warmup", "min": 10, "pace": "rápido"}]}, f, 1)
        assert any("m:ss" in e for e in errors)

    def test_progression_2_plus_1_needs_a_deload_after_two_load_weeks(self):
        assert pb.check_macro(MACRO, _frame(max_load_weeks=3)) == []
        assert any("más de 2 semanas de carga seguidas" in e for e in pb.check_macro(MACRO, _frame(max_load_weeks=2)))

    def test_preferences_reach_the_llm(self):
        text = pb.frame_text(_frame(doubles=True, intensity="piramidal", max_load_weeks=2))
        assert "Dobles: sí" in text and "piramidal" in text and "bloques de 2 semanas de carga" in text
        assert "Como mucho un 20 %" in pb.rules_text(_frame())
