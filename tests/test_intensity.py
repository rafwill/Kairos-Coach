"""
tests/test_intensity.py
Cuánto de lo previsto se hizo (11/11, agent/intensity.py). Criterios fijados antes de ver resultados: una sesión hecha
tal cual sale completa y a la mitad sale a la mitad; ir más rápido cuenta en calidad y no en lo suave; las pausas no
cuentan; ningún segundo cuenta dos veces.
"""

from agent import intensity as it

DESCRIPTORS = [{"key": k, "metricsIndex": i} for i, k in
               enumerate(["directTimestamp", "directSpeed", "directHeartRate", "directElevation", "sumDistance"])]
HR_ZONES = [("Z1", 115, 135), ("Z2", 135, 146), ("Z3", 146, 158), ("Z4", 158, 169), ("Z5", 169, 179)]


def _details(*segments):
    """Tramos (segundos, ritmo s/km o 0 = parado, FC) → detalle de Garmin a 1 Hz, en llano."""
    rows, t, dist = [], 0, 0.0
    for seconds, pace, hr in segments:
        speed = 1000 / pace if pace else 0.0
        for _ in range(int(seconds)):
            rows.append({"metrics": [t, speed, hr, 100.0, dist]})
            t += 1
            dist += speed
    return {"metricDescriptors": DESCRIPTORS, "activityDetailMetrics": rows}


def _pace(kind, zone, minutes, lo, hi, reps=1):
    return {"type": kind, "duration_min": minutes, "reps": reps,
            "target": {"metric": "pace", "range": [lo, hi], "zone": zone}}


def _session(*steps):
    return {"structured_workout": {"steps": list(steps)}}


# 15' de calentamiento en Z2 (5:00-5:30), 6×(3' en Z4 a 4:00-4:10 + 2' en Z1 a 5:40-6:10), 10' de vuelta a la calma
SERIES = _session(_pace("warmup", 2, 15, 300, 330),
                  {"type": "interval_block", "reps": 6, "steps": [_pace("work", 4, 3, 240, 250),
                                                                  _pace("recovery", 1, 2, 340, 370)]},
                  _pace("cooldown", 2, 10, 300, 330))


def _done(*segments, session=SERIES):
    dist = it.distribution(_details(*segments), with_pace=True)
    return it.completion(it.planned_parts([session], HR_ZONES), dist)


def _as_planned(work_pace=245, easy_pace=315, rec_pace=355, reps=6):
    segs = [(15 * 60, easy_pace, 140)]
    for _ in range(reps):
        segs += [(180, work_pace, 162), (120, rec_pace, 140)]
    return segs + [(10 * 60, easy_pace, 140)]


def test_as_planned_is_complete():
    out = _done(*_as_planned())
    assert out["pct"] == 100, out
    assert out["quality"] == {"done_s": 18 * 60, "planned_s": 18 * 60}
    assert out["easy"] == {"done_s": 37 * 60, "planned_s": 37 * 60}
    assert out["easy_above_s"] == 0 and out["easy_below_s"] == 0


def test_gps_spikes_do_not_break_a_zone():
    """Un salto de GPS de unos segundos (4:00 → 3:00 → 4:00) no saca de la zona: lo quita la mediana."""
    segs = [(15 * 60, 315, 140)]
    for _ in range(6):
        segs += [(90, 245, 162), (4, 180, 162), (86, 245, 162), (120, 355, 140)]
    segs += [(10 * 60, 315, 140)]
    assert _done(*segs)["pct"] == 100


def test_half_the_reps_is_half_the_quality():
    out = _done(*_as_planned(reps=3))
    assert out["quality"]["done_s"] == 9 * 60


def test_faster_counts_in_quality_but_not_in_easy():
    fast_reps = _done(*_as_planned(work_pace=225))  # series a 3:45 en vez de 4:00-4:10: el trabajo está
    assert fast_reps["quality"]["done_s"] >= 17 * 60
    fast_easy = _done(*_as_planned(easy_pace=280))  # calentamiento y vuelta a la calma a 4:40 en vez de Z2
    assert fast_easy["easy"]["done_s"] <= 13 * 60  # solo cuentan las recuperaciones
    assert fast_easy["easy_above_s"] == 25 * 60 and fast_easy["easy_below_s"] == 0  # y se dice que fue más rápido
    slow_easy = _done(*_as_planned(easy_pace=345))  # a 5:45 en vez de 5:00-5:30
    assert slow_easy["easy_below_s"] == 25 * 60 and slow_easy["easy_above_s"] == 0


def test_slower_reps_do_not_count_as_quality():
    out = _done(*_as_planned(work_pace=275))  # series a 4:35: ni Z4 ni más rápido
    assert out["quality"]["done_s"] == 0


def test_pauses_do_not_count():
    segs = _as_planned()
    paused = segs[:1] + [(300, 0, 110)] + segs[1:]  # 5' parado tras calentar
    assert _done(*paused)["pct"] == _done(*segs)["pct"]
    assert it.distribution(_details(*paused), with_pace=True)["moving_s"] == sum(s for s, _, _ in segs)


def test_watch_paused_gaps_are_not_moving_time():
    """8/11: con el reloj en pausa Garmin no manda muestras; al pasar a 1 Hz se rellenaba el hueco (12' de más)."""
    det = _details(*_as_planned())
    rows = det["activityDetailMetrics"]
    for r in rows[15 * 60:]:
        r["metrics"][0] += 600  # 10' de reloj parado tras calentar
    dist = it.distribution(det, with_pace=True)
    assert dist["moving_s"] == len(rows)
    assert it.completion(it.planned_parts([SERIES], HR_ZONES), dist)["pct"] == 100


def test_no_second_counts_twice():
    # 20' a 4:00: valen para 18' de Z4 y para nada más (ni para 10' más de Z3 que se pidieran también)
    session = _session(_pace("work", 4, 18, 240, 250), _pace("work", 3, 10, 260, 275))
    out = _done((20 * 60, 245, 162), session=session)
    assert out["quality"]["done_s"] <= 20 * 60 and out["quality"]["planned_s"] == 28 * 60
    # lo más exigente primero: los 18' de Z4 completos, los 10' de Z3 solo con lo que sobra
    parts = it.planned_parts([session], HR_ZONES)
    it.completion(parts, it.distribution(_details((20 * 60, 245, 162)), with_pace=True))
    assert parts[0]["done_s"] == 18 * 60 and parts[1]["done_s"] <= 2 * 60


def test_heart_rate_targets():
    session = _session({"type": "steady", "duration_min": 40, "reps": 1, "target": {"metric": "hr_zone", "zone": 2}},
                       {"type": "work", "duration_min": 10, "reps": 1, "target": {"metric": "hr_zone", "zone": 4}})
    dist = it.distribution(_details((40 * 60, 330, 140), (10 * 60, 280, 165)), with_pace=False)
    assert dist["pace"] == {}  # en bici o elíptica, sin ritmo
    out = it.completion(it.planned_parts([session], HR_ZONES), dist)
    assert out["pct"] == 100 and out["quality"]["done_s"] == 600


def test_strength_and_unmeasured_steps_are_left_out():
    strength = _session({"type": "main", "duration_min": 40, "reps": 1, "target": {"metric": "rpe", "range": [6, 7]}})
    assert it.planned_parts([strength], HR_ZONES) == []
    assert it.completion([], {"pace": {"300": 10}}) is None
    assert it.distribution({"nada": 1}, with_pace=True) is None


def test_two_activities_of_one_day_are_merged():
    a = it.distribution(_details((600, 315, 140)), with_pace=True)
    b = it.distribution(_details((600, 315, 140)), with_pace=True)
    merged = it.merge([a, b])
    assert merged["moving_s"] == 1200 and sum(merged["pace"].values()) == 1200


def test_the_day_needs_every_measured_activity(monkeypatch):
    """Sin la distribución de alguna actividad del día no hay porcentaje: con media sesión sería falso."""
    from agent import daily_engine as de

    dist = it.distribution(_details(*_as_planned()), with_pace=True)
    acts = [{"activityId": 1, "type": "running"}, {"activityId": 2, "type": "strength_training"}]
    monkeypatch.setattr(de._storage, "get_activity_intensity", lambda ids: {1: dist} if 1 in ids else {})
    out = de._day_completion([SERIES], acts, {})  # la fuerza no cuenta
    assert out["pct"] == 100  # sin zonas de FC en el perfil, el plan por ritmo se mide igual
    monkeypatch.setattr(de._storage, "get_activity_intensity", lambda ids: {})
    assert de._day_completion([SERIES], acts, {}) is None


def test_completion_line_in_the_summary():
    from agent.startup_status import _plan_vs_done_lines

    feedback = {"adherence_score": 0.9, "adherence_label": "adherente",
                "planned": {"duration_min": 55.0, "tss_est": 70.0}, "executed": {"activities": 1, "duration_min": 55.0,
                                                                                  "tss": 72.0},
                "completion": {"pct": 92, "quality": {"done_s": 1080, "planned_s": 1080},
                               "easy": {"done_s": 2040, "planned_s": 2220}, "easy_above_s": 120,
                               "easy_below_s": 50}}
    lines = _plan_vs_done_lines(feedback, "hoy")
    assert lines[-1] == ("  - Completado: **92 %** · calidad 18' de 18' (en su zona o más rápido) · suave 34' de 37' "
                         "en su zona (2' más rápido)")  # menos de 1' no se dice
    by_hr = {**feedback["completion"], "easy_by_hr": True, "easy_below_s": 300}
    assert _plan_vs_done_lines({**feedback, "completion": by_hr}, "hoy")[-1].endswith(
        "(2' con la FC por encima y 5' con la FC por debajo)")
    assert len(_plan_vs_done_lines({**feedback, "completion": None}, "hoy")) == 3


def test_easy_steps_of_a_plan_saved_by_pace_are_measured_by_hr():
    # 18/11, petición del usuario: lo suave de fuera de las series va por FC. En un plan guardado antes por ritmo
    # (Z2 4:34-5:10), un rodaje a 5:25 con la FC en Z2 está completo; la recuperación dentro de las series, por ritmo.
    rodaje = _session({"type": "steady", "duration_min": 20, "reps": 1,
                       "target": {"metric": "pace", "range": [274, 310], "zone": 2, "pace_zone": "Z2"}})
    out = it.completion(it.planned_parts([rodaje], HR_ZONES), it.distribution(_details((1200, 325, 140)), with_pace=True))
    assert out["pct"] == 100 and out["easy_by_hr"]
    series = _session({"type": "interval_block", "reps": 2, "steps": [
        {"type": "work", "duration_min": 4, "reps": 1, "target": {"metric": "pace", "range": [240, 250], "zone": 4, "pace_zone": "Z4"}},
        {"type": "recovery", "duration_min": 2, "reps": 1, "target": {"metric": "pace", "range": [274, 310], "zone": 2, "pace_zone": "Z2"}}]})
    assert [p["metric"] for p in it.planned_parts([series], HR_ZONES)] == ["pace", "pace"]
