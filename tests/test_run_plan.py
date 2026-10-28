"""
tests/test_run_plan.py
Plan de carrera en asfalto por bloques (agent/run_plan.py, 28/10): criterios de cierre fijados antes de verlo.
"""

from datetime import date, timedelta

from agent import run_plan as rp
from agent.daily_engine import _apply_structured_workout_adjustment
from agent.plans import _generate_structured_plan_payload, _plan_detail_markdown, _validate_structured_plan

T = 240.0  # ritmo umbral 4:00/km
START = date(2026, 10, 26)  # lunes
RACE = date(2027, 1, 17)  # domingo


def _plan(**kw):
    args = dict(race="Media Maratón de Santa Pola", race_date=RACE, target_time="1:22:59", threshold_pace=T, start=START,
                weekly_minutes_goal=600, recent_weekly_minutes=300, longest_recent_min=100)
    args.update(kw)
    return rp.build_run_plan(**args)


def _by_week(sessions):
    out = {}
    for s in sessions:
        out.setdefault(s["week_index"], []).append(s)
    return out


class TestPaces:
    def test_zones_come_from_the_threshold_and_race_pace_from_the_target(self):
        p = rp.Paces(T, 4979 / 21.0975)  # 1:22:59 en media maratón
        assert p.zone("Z2") == (274, 310)  # Friel: 114–129 % del umbral
        assert p.zone("Z4") == (238, 252)  # 99–105 %
        assert p.zone("Z5b") == (216, 230)  # 90–96 %
        assert p.race_range() == (233, 239)  # 3:56/km ± 3 s
        assert rp.parse_target_time("1:22:59") == 4979 and rp.parse_target_time("39:30") == 2370

    def test_planned_tss_is_trainingpeaks_rtss(self):
        p = rp.Paces(T, T)
        at_threshold = [rp.step(p, "work", "Z4", 60)]
        minutes, km, tss = rp.workout_totals(at_threshold, T)
        mid = (238 + 252) / 2
        assert minutes == 60 and tss == round(100 * (T / mid) ** 2, 1) and km == round(3600 / mid, 1)


class TestPlanStructure:
    def test_weeks_phases_deloads_and_race_on_its_date(self):
        data, sessions = _plan()
        weeks = _by_week(sessions)
        assert len(weeks) == 12 and all(len({s["day_index"] for s in w}) == 7 for w in weeks.values())
        assert data["phases"][-2:] == ["taper", "taper"] and data["deload_weeks"] == [4, 8]
        race = [s for s in sessions if s["session_type"] == "race_run"]
        assert len(race) == 1 and race[0]["week_index"] == 12 and race[0]["day_index"] == RACE.isoweekday()
        assert abs(race[0]["structured_workout"]["summary"]["distance_km"] - 21.1 - 4) < 3  # carrera + calentamiento

    def test_volume_progresses_at_most_10_percent_and_deloads_taper(self):
        data, _ = _plan()
        wm, deloads, phases = data["week_minutes"], set(data["deload_weeks"]), data["phases"]
        last_load = None
        peak = 0.0
        for i, minutes in enumerate(wm, 1):
            if phases[i - 1] == "taper":
                assert minutes <= peak * 0.85, (i, minutes, peak)
            elif i in deloads:
                assert minutes <= last_load * 0.80, (i, minutes, last_load)
            else:
                if last_load:
                    assert minutes <= last_load * 1.10 + 1, (i, minutes, last_load)
                last_load = minutes
                peak = max(peak, minutes)
        assert wm[0] <= 300 * 1.10 + 1  # parte de lo que el atleta sostiene

    def test_quality_progresses_by_phase(self):
        _, sessions = _plan()
        titles = [s["structured_workout"]["title"] for s in sessions if s["session_type"] == "running_quality"]
        assert any("Cuestas" in t for t in titles) and any("Fartlek" in t for t in titles)
        assert any("VO2máx" in t for t in titles) and any("Umbral" in t for t in titles)
        assert any("ritmo de carrera" in t for t in titles)
        assert len(set(titles)) >= 10

    def test_steps_are_the_single_source_of_description_duration_and_targets(self):
        _, sessions = _plan()
        for s in sessions:
            wk = s["structured_workout"]
            if s["session_type"] in ("rest", "strength"):
                continue
            minutes, km, tss = rp.workout_totals(wk["steps"], T)
            assert wk["summary"]["duration_min"] == minutes and wk["summary"]["tss"] == tss
            assert wk["notes"] == " · ".join(rp.describe_steps(wk["steps"]))
            for leaf, _ in rp._leaf_steps(wk["steps"]):
                rng = leaf["target"]["range"]
                assert rng[0] <= rng[1] and leaf["target"]["metric"] == "pace"

    def test_the_plan_passes_the_validator(self):
        data, sessions = _plan()
        plan = {"title": "Plan", "objective": "Media", "duration_weeks": 12, "plan_data": data}
        assert _validate_structured_plan(plan, sessions, {"goals": {"weekly_training_hours": 10}}) == []


class TestDailyEngineAndDisplay:
    def test_reducing_a_session_lowers_its_pace_zone(self):
        p = rp.Paces(T, T)
        wk = rp.quality_session(p, "build", 1, 1, False)  # 5×1000 m en Z5b
        adjusted, trace = _apply_structured_workout_adjustment({"structured_workout": wk}, "reduce")
        work = adjusted["steps"][1]["steps"][0]
        assert work["target"]["pace_zone"] == "Z5a" and tuple(work["target"]["range"]) == p.zone("Z5a")
        assert any("ritmo Z5b->Z5a" in t for t in trace)

    def test_each_day_shows_date_title_duration_km_tss_and_steps(self):
        data, sessions = _plan()
        tuesday = next(s for s in sessions if s["week_index"] == 1 and s["day_index"] == 2)
        lines = rp.session_lines(tuesday, START + timedelta(days=1), [("Z1", 115, 134), ("Z2", 135, 145),
                                                                       ("Z3", 146, 157), ("Z4", 158, 168), ("Z5", 169, 179)])
        assert lines[0].startswith("Martes 27/10/2026 — Cuestas cortas") and "TSS" in lines[0] and "km" in lines[0]
        assert any("@ 3:36–3:50/km (Z5b · FC Z5 169–179)" in x for x in lines[1:])
        plan = {"title": "Plan", "objective": "Media", "plan_data": {**data, "start_date": START.isoformat()}}
        md = _plan_detail_markdown(plan, sessions, {}, weeks=[12])
        assert "| 4 | 16/11–22/11 | Base · descarga |" in md or "descarga" in md
        assert "Carrera: Media Maratón de Santa Pola" in md


def test_a_trail_athlete_preparing_a_road_half_gets_the_block_plan():
    profile = {"goals": {"primary": "trail running", "target_race": "Media Maratón de Santa Pola (21,1 km)",
                         "target_race_date": (date.today() + timedelta(weeks=10)).isoformat(), "target_time": "01:22:59",
                         "weekly_training_hours": 10},
               "performance": {"running_threshold_pace_history": [{"date": "2026-01-01", "sec_per_km": 240}]}}
    plan, sessions = _generate_structured_plan_payload(profile, "Crea un plan nuevo", load_context={"recent_weekly_minutes": 300})
    assert plan["plan_data"]["method"] == "bloques_con_descarga"
    assert _validate_structured_plan(plan, sessions, profile) == []
    trail, _ = _generate_structured_plan_payload(profile, "Quiero un plan de trail", load_context={})
    assert trail["plan_data"].get("method") != "bloques_con_descarga"
