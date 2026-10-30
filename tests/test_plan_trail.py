"""
tests/test_plan_trail.py
Planes de trail (30/10, punto 12 de docs/proximos_pasos.md): decisiones del usuario sobre la tirada larga, el D+ semanal,
la marcha y la bici con su nombre. Un plan de trail completo con el LLM simulado y cada regla a favor y en contra.
"""

import json
import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from agent import plan_builder as pb
from agent import run_plan as rp
from agent.plan_llm import PlanLLMMixin
from agent.plans import _plan_detail_markdown
from agent.prompts import _render_prompt

ZONES = [("Z1", 115, 134), ("Z2", 135, 145), ("Z3", 146, 157), ("Z4", 158, 168), ("Z5", 169, 179)]
START = date(2026, 11, 2)  # lunes
RACE = START + timedelta(weeks=7, days=6)  # domingo de la semana 8


def _frame(**kw) -> pb.PlanFrame:
    args = dict(start=START, weeks=8, race="Trail de prueba 30K", race_date=RACE, distance_km=30.0, target_time="",
                threshold_pace=240.0, race_pace=None, hr_zones=ZONES, hr_threshold_tp=169.0, weekly_hours=8.0,
                recent_weekly_minutes=300.0, longest_recent_min=120.0, trail=True, race_elevation_m=1200.0,
                recent_weekly_vert_m=800.0)
    args.update(kw)
    return pb.PlanFrame(**args)


MACRO = {"title": "Plan hacia el Trail de prueba 30K", "rationale": "Tiempo en pie y desnivel progresivos.", "weeks": [
    {"week": 1, "phase": "base", "deload": False, "minutes": 320, "long_min": 110, "vert_m": 880, "focus": "subidas"},
    {"week": 2, "phase": "base", "deload": False, "minutes": 345, "long_min": 125, "vert_m": 968, "focus": "subidas"},
    {"week": 3, "phase": "build", "deload": False, "minutes": 370, "long_min": 140, "vert_m": 1064, "focus": "umbral"},
    {"week": 4, "phase": "build", "deload": True, "minutes": 290, "long_min": 100, "vert_m": 850, "focus": "descarga"},
    {"week": 5, "phase": "specific", "deload": False, "minutes": 395, "long_min": 160, "vert_m": 1170, "focus": "b2b"},
    {"week": 6, "phase": "specific", "deload": False, "minutes": 420, "long_min": 175, "vert_m": 1287, "focus": "b2b"},
    {"week": 7, "phase": "taper", "deload": False, "minutes": 330, "long_min": 110, "vert_m": 900, "focus": "afinar"},
    {"week": 8, "phase": "race", "deload": False, "minutes": 250, "long_min": 0, "vert_m": 500, "focus": "carrera"},
]}


def _steps(*parts):
    return [{"kind": k, "min": m, "zone": z} for k, m, z in parts]


def _run(kind, title, minutes, vert):
    """Rodaje o tirada por FC: "auto" si minutes es None."""
    steady = "auto" if minutes is None else minutes - 15
    return {"type": kind, "title": title, "vert": vert, "steps": _steps(("warmup", 10, "FC1"), ("steady", steady, "FC2"),
                                                                         ("cooldown", 5, "FC1"))}


def _quality(week, vert, zone="FC4", minutes=4):
    reps = 4 + week % 3  # progresa de una semana a otra (variedad)
    return {"type": "quality", "title": f"Subidas {reps}×{minutes}'", "vert": vert, "steps": [
        {"kind": "warmup", "min": 10, "zone": "FC1"},
        {"repeat": reps, "steps": [{"kind": "work", "min": minutes, "zone": zone, "note": "subida"},
                                   {"kind": "recovery", "min": 3, "zone": "FC1", "note": "bajada al trote"}]},
        {"kind": "cooldown", "min": 5, "zone": "FC1"}]}


def _week_days(w: dict) -> list[dict]:
    n, v = w["week"], w["vert_m"]
    if w["phase"] == "race":
        race = {"type": "race", "title": "Trail de prueba 30K", "vert": 1200,
                "steps": _steps(("warmup", 10, "FC1"), ("work", 180, "FC3"), ("cooldown", 10, "FC1"))}
        days = [{"type": "rest"}, _quality(n, 100, minutes=2), _run("easy", "Rodaje", 35, 100), {"type": "rest"},
                _run("easy", "Rodaje", 30, 100), {"type": "rest"}, race]
        return [{"day": i, **d} for i, d in enumerate(days, 1)]
    q1, q2, easy = round(v * 0.2), round(v * 0.15), round(v * 0.1)
    if w["phase"] == "specific":
        # Dos tiradas largas seguidas (sábado y domingo): la del sábado con sus minutos, la del domingo "auto".
        second = round(v * 0.2)
        days = [{"type": "strength", "title": "Fuerza excéntrica", "minutes": 30, "rpe": "6"},
                _quality(n, q1), _run("easy", "Rodaje", None, easy), _quality(n, q2, "FC3", 3), {"type": "rest"},
                _run("long", "Tirada larga del sábado", 60, second),
                _run("long", "Tirada larga del domingo", None, v - q1 - q2 - easy - second)]
    else:
        days = [{"type": "strength", "title": "Fuerza excéntrica", "minutes": 30, "rpe": "6"},
                _quality(n, q1), _run("easy", "Rodaje", None, easy), _quality(n, q2, "FC3", 3), {"type": "rest"},
                {"type": "cross", "sport": "hiking", "title": "Marcha con palos", "vert": easy,
                 "steps": _steps(("warmup", 5, "FC1"), ("steady", 20, "FC2"), ("cooldown", 5, "FC1"))},
                _run("long", "Tirada larga", None, v - q1 - q2 - 2 * easy)]
    return [{"day": i, **d} for i, d in enumerate(days, 1)]


class _FakeLLM:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, model, messages, **kw):
        self.calls.append(messages)
        prompt = messages[1]["content"]
        if "Diseña la estructura" in prompt:
            data = MACRO
        else:
            weeks = [int(x) for x in re.search(r"semanas ([\d, ]+), y solo esas", prompt).group(1).split(",")]
            data = {"weeks": [{"week": n, "days": _week_days(MACRO["weeks"][n - 1])} for n in weeks]}
        content = "```json\n" + json.dumps(data) + "\n```"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


class _Agent(PlanLLMMixin):
    def __init__(self, llm, frame):
        self.client, self.model, self.provider, self._frame = llm, "fake", "fake", frame
        self.total_prompt_tokens = self.total_completion_tokens = 0
        self.user_profile, self.mcp_session = {}, None

    def _profile_context_block(self):
        return ""

    def _plan_frame(self, load_context, user_message="", plan=None, from_monday=None):
        return self._frame


# ─── Un plan de trail completo ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_whole_trail_plan_passes_the_rules(monkeypatch):
    monkeypatch.setattr("agent.plan_llm._add_ia_tokens", lambda n: None)
    llm = _FakeLLM()
    plan, sessions, errors = await _Agent(llm, _frame())._build_plan_with_llm("Crea un plan de trail", {})
    assert errors == [] and plan is not None
    pd = plan["plan_data"]
    assert pd["trail"] is True and pd["race_elevation_m"] == 1200.0
    assert pd["planned_week_vert"][:3] == [880, 968, 1064] and pd["week_vert"][5] == 1287
    # Las instrucciones de trail llegaron a la estructura y a las semanas; con las cifras de la tirada larga de trail.
    macro_prompt = llm.calls[0][1]["content"]
    assert "\"vert_m\"" in macro_prompt and "no pasa del 50 %" in macro_prompt and "más de 30 min" in macro_prompt
    weeks_prompt = next(c[1]["content"] for c in llm.calls if "y solo esas" in c[1]["content"])
    assert "subida andando con palos" in weeks_prompt and "\"sport\": \"hiking\"" in weeks_prompt
    # Sesiones por FC en trail: sin km estimados con ritmos de asfalto; el D+ de cada sesión, guardado y a la vista.
    long6 = [s for s in sessions if s["week_index"] == 6 and s["session_type"] == "long_run"]
    assert len(long6) == 2 and all(s["structured_workout"]["summary"]["distance_km"] == 0 for s in long6)
    md = _plan_detail_markdown(plan, sessions, {}, weeks=[6])
    assert "| D+ |" in md and "| 1287 |" in md and "+257 m" in md


def test_the_asphalt_frame_gets_no_trail_text():
    f = _frame(trail=False, race_elevation_m=None, recent_weekly_vert_m=None)
    assert "D+" not in pb.frame_text(f) and "tiradas largas seguidas" not in pb.rules_text(f)
    # El asfalto no pide "vert_m".
    assert not any("vert_m" in e for e in pb.check_macro({**MACRO, "weeks": [
        {k: v for k, v in w.items() if k != "vert_m"} for w in MACRO["weeks"]]}, f))


# ─── Tirada larga (decisión del usuario) ──────────────────────────────────────

def _macro_with(n: int, **changes) -> dict:
    return {**MACRO, "weeks": [{**w, **changes} if w["week"] == n else w for w in MACRO["weeks"]]}


def test_long_run_share_and_step_are_wider_in_trail():
    share = _macro_with(1, long_min=150)  # 47 % de 320
    assert not any("tirada larga" in e for e in pb.check_macro(share, _frame()))
    assert any("supera el 40 %" in e for e in pb.check_macro(share, _frame(trail=False)))
    step = _macro_with(2, long_min=135)  # +25 min sobre 110
    assert not any("tirada larga sube" in e for e in pb.check_macro(step, _frame()))
    assert any("más de 15 min" in e for e in pb.check_macro(step, _frame(trail=False)))
    assert any("más de 30 min" in e for e in pb.check_macro(_macro_with(2, long_min=145), _frame()))


def _week(n: int, days: list[dict], f=None) -> list[dict]:
    sessions, errors = pb.expand_week({"week": n, "days": days}, f or _frame(), MACRO["weeks"][n - 1])
    assert errors == [], errors
    return sessions


def test_back_to_back_long_runs_only_in_specific_on_the_chosen_days():
    f = _frame()
    days5 = _week_days(MACRO["weeks"][4])
    assert pb.check_week(5, _week(5, days5), MACRO["weeks"][4], f) == []
    # En base, dos tiradas largas no valen.
    base = [{**d} for d in days5]
    errors = pb.check_week(5, _week(5, base), {**MACRO["weeks"][4], "phase": "base"}, f)
    assert any("dos seguidas solo en la fase específica" in e for e in errors)
    # Otros días: el atleta dijo viernes y sábado.
    errors = pb.check_week(5, _week(5, days5), MACRO["weeks"][4], _frame(back_to_back_days=[5, 6]))
    assert any("van el viernes y el sábado" in e for e in errors)
    # Juntas más del 60 %: con minutos fijos (sin "auto") para que la semana no se reparta.
    fixed = days5[:2] + [{"day": 3, **_run("easy", "Rodaje", 20, days5[2]["vert"])}] + days5[3:5] + [
        {"day": 6, **_run("long", "Sábado", 120, days5[5]["vert"])}, {"day": 7, **_run("long", "Domingo", 160, days5[6]["vert"])}]
    errors = pb.check_week(5, _week(5, fixed), MACRO["weeks"][4], f)
    assert any("más del 60 %" in e for e in errors)


def test_only_one_long_run_can_be_auto():
    days5 = _week_days(MACRO["weeks"][4])
    both_auto = days5[:5] + [_run("long", "Sábado", None, days5[5]["vert"]), days5[6]]
    _, errors = pb.expand_week({"week": 5, "days": both_auto}, _frame(), MACRO["weeks"][4])
    assert any("solo una tirada larga puede llevar \"auto\"" in e for e in errors)


# ─── Desnivel semanal (decisión del usuario) ──────────────────────────────────

def test_weekly_vert_progression_deload_and_taper():
    assert pb.check_vert(MACRO["weeks"], _frame()) == []
    assert any("falta \"vert_m\"" in e for e in pb.check_vert(_macro_with(3, vert_m=None)["weeks"], _frame()))
    assert any("suben más de un 10 % sobre el D+ que sostiene" in e
               for e in pb.check_vert(_macro_with(1, vert_m=900)["weeks"], _frame()))
    assert any("la semana de carga anterior" in e for e in pb.check_vert(_macro_with(2, vert_m=1000)["weeks"], _frame()))
    assert any("descarga debe bajar el D+" in e for e in pb.check_vert(_macro_with(4, vert_m=1000)["weeks"], _frame()))
    assert any("afinamiento debe bajar el D+" in e for e in pb.check_vert(_macro_with(7, vert_m=1200)["weeks"], _frame()))


def test_peak_vert_reaches_the_race_or_what_the_progression_allows():
    # Hasta 60 km: el D+ de la carrera (1200 ≤ 1287 alcanzables).
    assert pb.check_vert(MACRO["weeks"], _frame(race_elevation_m=1250)) == []
    errors = pb.check_vert(_macro_with(6, vert_m=1150)["weeks"], _frame(race_elevation_m=1250))
    assert any("debe llegar al menos a 1250 m (el D+ de la carrera" in e for e in errors)
    # Más de 60 km: la mitad.
    assert pb.check_vert(MACRO["weeks"], _frame(distance_km=80.0, race_elevation_m=2500)) == []
    errors = pb.check_vert(_macro_with(6, vert_m=1150)["weeks"], _frame(distance_km=80.0, race_elevation_m=2400))
    assert any("la mitad del D+ de la carrera" in e for e in errors)
    # No da tiempo con el 10 %: lo que permite la progresión (800 → 880 → 968 → 1064 → 1170 → 1287).
    assert pb.check_vert(MACRO["weeks"], _frame(race_elevation_m=3000)) == []
    errors = pb.check_vert(_macro_with(6, vert_m=1250)["weeks"], _frame(race_elevation_m=3000))
    assert any("1287 m (lo que permite subir un 10 % por semana de carga)" in e for e in errors)


def test_the_days_add_up_to_the_week_vert():
    days = _week_days(MACRO["weeks"][0])
    days[6] = {**days[6], "vert": days[6]["vert"] + 300}
    errors = pb.check_week(1, _week(1, days), MACRO["weeks"][0], _frame())
    assert any("1180 m de D+" in e and "quita unos 300 m" in e for e in errors)


# ─── Sesiones ─────────────────────────────────────────────────────────────────

def test_session_vert_hiking_and_no_rp_in_trail():
    hike = {"day": 6, "type": "cross", "sport": "hiking", "title": "Marcha con palos", "vert": 700,
            "steps": _steps(("warmup", 10, "FC1"), ("steady", 100, "FC2"), ("cooldown", 10, "FC1"))}
    session, errors = pb.expand_day(hike, _frame(), 1)
    assert errors == [] and session["structured_workout"]["summary"]["elevation_gain_m"] == 700
    assert "+700 m" in rp.session_lines(session, START + timedelta(days=5), ZONES)[0]
    assert pb.expand_day({**hike, "vert": "mucho"}, _frame(), 1)[1][0].endswith("(un número ≥ 0).")
    rp_step = {"day": 2, "type": "quality", "title": "RP", "steps": [
        {"kind": "warmup", "min": 10, "zone": "FC1"}, {"kind": "work", "min": 20, "zone": "RP"},
        {"kind": "cooldown", "min": 10, "zone": "FC1"}]}
    assert any("en trail no hay ritmo de carrera (RP)" in e for e in pb.expand_day(rp_step, _frame(), 1)[1])
    for sport, title in (("gravel", "Gravel"), ("mtb", "MTB"), ("cycling", "Bici de carretera")):
        bike = {"day": 3, "type": "cross", "sport": sport, "steps": _steps(("warmup", 10, "FC1"), ("steady", 60, "FC2"),
                                                                         ("cooldown", 5, "FC1"))}
        assert pb.expand_day(bike, _frame(), 1)[0]["structured_workout"]["title"] == title


def test_trail_frame_text_and_rules():
    f = _frame(back_to_back_days=[5, 6])
    text = pb.frame_text(f)
    assert "desnivel positivo 1200 m" in text and "sin ritmo de carrera (RP)" in text
    assert "al menos a 1200 m de D+" in text and "Desnivel positivo sostenido: 800 m/semana" in text
    rules = pb.rules_text(f)
    assert "el viernes y el sábado, juntas como mucho el 60 %" in rules and "como mucho el 50 % de la semana" in rules
    system = _render_prompt("plan/system")
    assert "Trail" in system and "fuerza excéntrica" in system and "nutrición" in system


# ─── Del perfil al plan ───────────────────────────────────────────────────────

def test_plan_frame_decides_trail_by_the_race_not_by_the_main_sport():
    from agent.trainer_agent import TrainerAgent

    agent = object.__new__(TrainerAgent)
    race_date = (date.today() + timedelta(days=90)).isoformat()
    # El perfil real del usuario (30/10): deporte principal trail y una media en asfalto de objetivo.
    agent.user_profile = {"goals": {"primary": "trail running", "target_race": "Media Maratón de Santa Pola (21,1 km)",
                                    "target_race_date": race_date, "target_time": "01:22:59"}}
    f = agent._plan_frame({"recent_weekly_minutes": 300.0, "recent_weekly_walk_minutes": 60.0})
    assert not f.trail and f.race_pace is not None and f.recent_weekly_minutes == 300.0
    agent.user_profile = {"goals": {"primary": "running", "target_race": "Ultra PDA", "target_race_date": race_date,
                                    "target_race_distance_km": 55, "target_race_elevation_m": 2400, "target_time": "7:30:00",
                                    "availability": {"back_to_back_days": [5, 6]}}}
    f = agent._plan_frame({"recent_weekly_minutes": 300.0, "recent_weekly_walk_minutes": 60.0, "recent_weekly_vert_m": 900})
    assert f.trail and f.race_pace is None and f.distance_km == 55 and f.race_elevation_m == 2400
    assert f.recent_weekly_minutes == 360.0 and f.recent_weekly_vert_m == 900 and f.back_to_back_days == [5, 6]
    assert f.peak_vert_target == 2400 and _frame(distance_km=80.0).peak_vert_target == 600


def test_km_in_the_race_name():
    from agent.main import _km_in_name

    assert _km_in_name("Ultra PDA 55km") == 55 and _km_in_name("Media Maratón de Santa Pola (21,1 km)") == 21.1
    assert _km_in_name("Trail Sierra de Crevillente") is None
