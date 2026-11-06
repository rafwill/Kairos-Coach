"""Bici suave opcional en un día de descanso del plan (06/11, petición del usuario): recuperación activa."""

from datetime import date, timedelta

from agent.daily_engine import _compute_daily_plan_adjustment, _missed_endurance_sessions

TODAY = date.today()
TODAY_ISO = TODAY.isoformat()
YESTERDAY_ISO = (TODAY - timedelta(days=1)).isoformat()


def _plan(rows_by_day: dict) -> dict:
    monday = TODAY - timedelta(days=TODAY.weekday() + 7)
    sessions = []
    for day_iso, rows in rows_by_day.items():
        d = date.fromisoformat(day_iso)
        for slot, (kind, minutes, title) in enumerate(rows, 1):
            sessions.append({"week_index": (d - monday).days // 7 + 1, "day_index": d.isoweekday(), "session_type": kind,
                             "duration_min": minutes, "intensity": "RPE 3",
                             "structured_workout": {"slot": slot, "title": title}})
    return {"title": "Plan", "plan_data": {"start_date": monday.isoformat()}, "sessions": sessions}


REST_TODAY = _plan({TODAY_ISO: [("rest", 0, "Descanso")],
                    YESTERDAY_ISO: [("running_z2", 47, "Rodaje de recuperación"), ("strength", 30, "Movilidad y core")]})


def _decide(*, tsb=10.0, ctl=57.0, hrv_low=False, missed=(), cross=None, plan=REST_TODAY):
    snapshot = {
        "dates": {"today": TODAY_ISO},
        "load_fatigue": {"status": "ready", "latest": {"tsb": tsb, "ctl": ctl, "atl": ctl - tsb}, "weekly": {},
                         "ranges": {}},
        "sleep": {"today": {"date": TODAY_ISO, "sleep_hours": 8.0, "sleep_score": 85, "bodyBatteryAtWake": 90}},
        "hrv": {"today": {"date": TODAY_ISO, "lastNightAvg": 40 if hrv_low else 70, "weeklyAvg": 66, "status": "balanced"}},
        "yesterday_missed": list(missed), "cross_sports": cross, "ftp_w": 205,
    }
    return _compute_daily_plan_adjustment(snapshot, plan)["active_recovery"]


def test_missed_run_is_the_run_not_the_strength():
    acts = [{"activityId": 1, "activityName": "Fuerza", "activityType": {"typeKey": "strength_training"}}]
    assert _missed_endurance_sessions(REST_TODAY, YESTERDAY_ISO, acts) == ["Rodaje de recuperación"]
    assert _missed_endurance_sessions(REST_TODAY, YESTERDAY_ISO, []) == ["Rodaje de recuperación"]
    run = [{"activityId": 2, "activityName": "Rodaje", "activityType": {"typeKey": "running"}}]
    assert _missed_endurance_sessions(REST_TODAY, YESTERDAY_ISO, run) == []


def test_fresh_rest_day_suggests_an_easy_ride():
    text = _decide(tsb=10.0, ctl=57.0)  # +17,5 %: Fresco
    assert text.startswith("Opcional (no es del plan): bici suave") and "Fresco" in text and "113 W" in text


def test_a_missed_run_is_a_reason_even_in_the_grey_zone():
    assert "ayer te saltaste Rodaje de recuperación" in _decide(tsb=0.0, missed=["Rodaje de recuperación"])
    assert _decide(tsb=0.0) == ""


def test_not_with_low_hrv_without_bike_or_on_a_training_day():
    assert _decide(hrv_low=True) == ""
    assert _decide(cross={"sports": []}) == ""
    assert _decide(cross={"sports": ["swimming"]}) == ""
    assert _decide(cross={"sports": ["gravel"]}) != ""
    training_day = _plan({TODAY_ISO: [("running_z2", 50, "Rodaje")]})
    assert _decide(plan=training_day) == ""
