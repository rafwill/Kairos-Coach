"""
tests/test_plan_drift.py
Detector de desvíos del plan (30/10, pendiente 11 d1): un caso a favor y otro en contra por señal, con los
umbrales fijados antes de programarlo (docs/proximos_pasos.md).
"""

from datetime import date, timedelta
from unittest.mock import patch

from agent import plan_drift as pd
from agent.startup import _plan_drift_lines
from agent.startup_status import _build_proactive_status_markdown

START = date(2026, 10, 19)  # lunes
TODAY = date(2026, 10, 28)  # miércoles: la ventana va del 21 al 27/10


def _session(d: date, kind: str, tss: float = 50.0, title: str = "") -> dict:
    w, day = (d - START).days // 7 + 1, d.isoweekday()
    return {"week_index": w, "day_index": day, "session_type": kind,
            "structured_workout": {"title": title or kind, "summary": {"tss": 0.0 if kind == "rest" else tss}}}


def _week_plan() -> list[dict]:
    """Del 19 al 1/11: calidad los martes y jueves, tirada el domingo, descanso el lunes y rodajes el resto."""
    kinds = {1: "rest", 2: "running_quality", 3: "running_z2", 4: "running_quality", 5: "running_z2",
             6: "running_z2", 7: "long_run"}
    return [_session(START + timedelta(days=i), kinds[(START + timedelta(days=i)).isoweekday()],
                     tss=90.0 if kinds[(START + timedelta(days=i)).isoweekday()] == "long_run" else 50.0)
            for i in range(14)]


def _did(d: date, tss: float = 50.0, sport: str = "running") -> dict:
    return {"start_date": d.isoformat(), "sport": sport, "tss": tss}


def _all_done(skip: tuple = (), sport: str = "running") -> list[dict]:
    """Lo previsto, hecho tal cual, salvo los días de `skip`."""
    out = []
    for s in _week_plan():
        d = START + timedelta(weeks=s["week_index"] - 1, days=s["day_index"] - 1)
        if s["session_type"] != "rest" and d not in skip:
            out.append(_did(d, s["structured_workout"]["summary"]["tss"], sport))
    return out


def _codes(signals) -> set[str]:
    return {s.code for s in signals}


def test_plan_followed_gives_no_signal():
    assert pd.detect(_week_plan(), START, _all_done(), [], TODAY) == []


def test_window_starts_yesterday_back_and_not_before_the_plan_or_its_revision():
    assert pd.window(TODAY, START, None) == [date(2026, 10, 21) + timedelta(days=i) for i in range(7)]
    assert pd.window(TODAY, START, date(2026, 10, 26)) == [date(2026, 10, 26), date(2026, 10, 27)]
    assert pd.window(TODAY, START, TODAY) == []


def test_two_key_sessions_missed():
    missed = (date(2026, 10, 22), date(2026, 10, 27))  # jueves 22 y martes 27: calidad
    signals = pd.detect(_week_plan(), START, _all_done(skip=missed), [], TODAY)
    assert "missed_key" in _codes(signals)
    assert "jueves 22/10 (running_quality)" in signals[0].text and "martes 27/10" in signals[0].text
    # Una sola, no.
    assert "missed_key" not in _codes(pd.detect(_week_plan(), START, _all_done(skip=missed[:1]), [], TODAY))


def test_a_key_session_moved_to_the_next_day_is_not_missed():
    # Jueves 22 sin hacer y el viernes 23 con su rodaje (50) más la calidad (50): movida, no perdida.
    acts = [a for a in _all_done(skip=(date(2026, 10, 22), date(2026, 10, 27)))
            if a["start_date"] != "2026-10-23"] + [_did(date(2026, 10, 23), 100.0)]
    assert "missed_key" not in _codes(pd.detect(_week_plan(), START, acts, [], TODAY))


def test_three_training_days_in_a_row_without_activity():
    gap = (date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25))
    signals = pd.detect(_week_plan(), START, _all_done(skip=gap), [], TODAY)
    assert "inactive" in _codes(signals)
    assert any("3 días de entrenamiento seguidos" in s.text for s in signals)
    assert "inactive" not in _codes(pd.detect(_week_plan(), START, _all_done(skip=gap[:2]), [], TODAY))


def test_a_planned_rest_day_does_not_break_the_inactive_streak():
    # Sin hacer el domingo 25 y el martes 27; el lunes 26 es descanso previsto: 2 días, no 3.
    gap = (date(2026, 10, 25), date(2026, 10, 27))
    assert "inactive" not in _codes(pd.detect(_week_plan(), START, _all_done(skip=gap), [], TODAY))
    # Con el sábado 24 también: sábado, domingo y martes, 3 días de entrenamiento seguidos.
    signals = pd.detect(_week_plan(), START, _all_done(skip=gap + (date(2026, 10, 24),)), [], TODAY)
    assert any(s.code == "inactive" and "del sábado 24/10 al martes 27/10" in s.text for s in signals)


def _hrv(day: date, night: float, week: float = 60.0) -> dict:
    return {"metric_date": day.isoformat(), "hrv_last_night_avg_ms": night, "hrv_weekly_avg_ms": week}


def test_low_hrv_three_nights_in_a_row():
    rows = [_hrv(date(2026, 10, 25), 50), _hrv(date(2026, 10, 26), 52), _hrv(date(2026, 10, 27), 53)]
    assert "hrv_low" in _codes(pd.detect(_week_plan(), START, _all_done(), rows, TODAY))
    # Una noche al 90 % justo corta la racha; un día sin fila, también.
    rows[1] = _hrv(date(2026, 10, 26), 54)
    assert "hrv_low" not in _codes(pd.detect(_week_plan(), START, _all_done(), rows, TODAY))
    assert "hrv_low" not in _codes(pd.detect(_week_plan(), START, _all_done(), [rows[0], rows[2]], TODAY))


def test_other_sport_instead_of_running_two_days():
    acts = _all_done(skip=(date(2026, 10, 23), date(2026, 10, 24)))
    acts += [_did(date(2026, 10, 23), 50, "gravel_cycling"), _did(date(2026, 10, 24), 50, "elliptical")]
    signals = pd.detect(_week_plan(), START, acts, [], TODAY)
    assert "other_sport" in _codes(signals)
    assert any("elliptical, gravel_cycling" in s.text for s in signals)
    # Trail cuenta como carrera, y un solo día no basta.
    acts = _all_done(skip=(date(2026, 10, 23), date(2026, 10, 24)))
    acts += [_did(date(2026, 10, 23), 50, "trail_running"), _did(date(2026, 10, 24), 50, "elliptical")]
    assert "other_sport" not in _codes(pd.detect(_week_plan(), START, acts, [], TODAY))


def test_load_far_below_or_above_the_plan():
    half = [dict(a, tss=a["tss"] * 0.45) for a in _all_done()]
    signals = pd.detect(_week_plan(), START, half, [], TODAY)
    assert "load_low" in _codes(signals)
    assert any("previstos (45 %)" in s.text for s in signals)
    more = [dict(a, tss=a["tss"] * 1.35) for a in _all_done()]
    assert "load_high" in _codes(pd.detect(_week_plan(), START, more, [], TODAY))
    near = [dict(a, tss=a["tss"] * 1.2) for a in _all_done()]
    assert _codes(pd.detect(_week_plan(), START, near, [], TODAY)) == set()


def test_load_needs_five_days_in_the_window():
    half = [dict(a, tss=a["tss"] * 0.3) for a in _all_done()]
    assert "load_low" not in _codes(pd.detect(_week_plan(), START, half, [], TODAY, since=date(2026, 10, 24)))


def test_startup_block_only_with_signals_and_only_since_the_last_revision():
    plan = {"start_date": START.isoformat(), "created_at": "2026-10-18T10:00:00+00:00", "sessions": _week_plan()}
    gap = (date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25))
    with patch("agent.startup._storage.get_activity_tss_map",
               return_value={i: a for i, a in enumerate(_all_done(skip=gap))}), \
         patch("agent.startup._storage.get_wellness_daily_range", return_value=[]):
        lines = _plan_drift_lines(plan, TODAY)
        assert lines[1] == "## 🔄 Tu plan ya no encaja" and "replanifica el plan" in lines[-1]
        # Revisado el 26/10: lo de antes ya se tuvo en cuenta.
        assert _plan_drift_lines({**plan, "plan_data": {"revised_on": "2026-10-26"}}, TODAY) == []
    with patch("agent.startup._storage.get_activity_tss_map", return_value=None):
        assert _plan_drift_lines(plan, TODAY) == []  # sin poder leer lo hecho, no se juzga


def test_status_shows_the_block_after_todays_session():
    snapshot = {"body_battery": {}, "hrv": {}, "sleep": {}, "trainings": [], "plan_assigned": True,
                "today_session_lines": ["- **Miércoles 28/10/2026 — Rodaje**"],
                "plan_drift_lines": ["", "## 🔄 Tu plan ya no encaja", "- 3 días sin actividad."]}
    md = _build_proactive_status_markdown(snapshot)
    assert md.index("## 🗓️ Sesión de hoy") < md.index("## 🔄 Tu plan ya no encaja") < md.index("## ✅ Recomendación")
    assert "Tu plan ya no encaja" not in _build_proactive_status_markdown({**snapshot, "plan_drift_lines": []})
