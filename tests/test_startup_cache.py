"""Caché del resumen inicial (31/10; 11/11, todo el día y la mañana reutilizable): huella, frescura, nota y flujo en
agent/startup.py."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent import startup as st
from agent import startup_cache as sc
from agent.load_metrics import TSS_FORMULA_VERSION

NOW = datetime(2026, 10, 31, 14, 40, tzinfo=timezone.utc)


def _row(minutes_ago=5, fp=None, md="## Resumen"):
    return {"generated_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "fingerprint": fp or {}, "markdown": md}


def _fp(**over):
    base = dict(today="2026-10-31", activity_ids=[2, 1], sleep="7 h", plan_row={"id": "p1", "updated_at": "t1"},
                formula_version=TSS_FORMULA_VERSION, profile_changes=[])
    base.update(over)
    return sc.fingerprint(**base)


class TestFingerprint:
    def test_ids_are_sorted_strings_and_none_is_dropped(self):
        assert _fp(activity_ids=[20, None, 3])["activities"] == ["20", "3"]

    def test_plan_is_id_and_last_update_or_none(self):
        assert _fp()["plan"] == ["p1", "t1"]
        assert _fp(plan_row=None)["plan"] is None

    @pytest.mark.parametrize("over", [
        {"today": "2026-11-01"}, {"activity_ids": [1, 2, 3]}, {"sleep": "6 h"},
        {"plan_row": {"id": "p1", "updated_at": "t2"}}, {"formula_version": TSS_FORMULA_VERSION + 1},
        {"profile_changes": ["peso"]},
    ])
    def test_any_change_gives_another_fingerprint(self, over):
        assert _fp(**over) != _fp()


class TestFreshness:
    def test_the_same_day_is_valid_hours_later(self):
        """11/11, petición del usuario: entra a las 10, vuelve por la tarde sin nada nuevo → el mismo resumen."""
        now = datetime.now().astimezone().replace(hour=18, minute=0)
        assert sc.is_today(_row(5), NOW) and sc.is_today(_row(8 * 60, md="x") | {
            "generated_at": (now - timedelta(hours=8)).isoformat()}, now)

    def test_another_day_is_not(self):
        now = datetime.now().astimezone().replace(hour=9, minute=0)
        assert not sc.is_today({"generated_at": (now - timedelta(hours=12)).isoformat()}, now)

    def test_a_future_or_broken_date_is_not(self):
        assert not sc.is_today(_row(-3), NOW)
        assert not sc.is_today({"generated_at": "ayer"}, NOW) and not sc.is_today(None, NOW)

    def test_note_says_when_and_how_to_refresh(self):
        text = sc.with_note(_row(5), NOW)
        assert "hace 5 min" in text and "`/resumen`" in text and text.endswith("## Resumen")
        assert "Body Battery es el de esa hora" in text
        assert "hace 5 h 12 min" in sc.with_note(_row(5 * 60 + 12), NOW)


class TestReusableMorning:
    MORNING = {"date": "2026-10-31", "hrv_today": {"h": 1}, "sleep_yday": {"s": 1}, "body_yday": None}

    def test_same_day_and_same_sleep_gives_the_morning_data(self):
        row = {"fingerprint": _fp(), "morning": self.MORNING}
        assert sc.reusable_morning(row, _fp(activity_ids=[1, 2, 3])) == {"hrv_today": {"h": 1}, "sleep_yday": {"s": 1}}

    @pytest.mark.parametrize("current, morning", [
        (_fp(sleep="6 h"), MORNING),  # el reloj sincronizó tarde o hubo siesta
        (_fp(today="2026-11-01"), MORNING),  # otro día
        (_fp(), {**MORNING, "date": "2026-10-30"}),  # mañana de otro día
        (_fp(), {}),  # resumen guardado antes del 11/11, sin la mañana
    ])
    def test_otherwise_nothing(self, current, morning):
        assert sc.reusable_morning({"fingerprint": _fp(), "morning": morning}, current) == {}
        assert sc.reusable_morning(None, current) == {}


def _agent():
    agent = object.__new__(st.StartupMixin)
    agent.mcp_session = MagicMock()
    return agent


async def _cached(agent, *, row, acts, sleep, plan=None, changes=None):
    async def fake_call(_s, tool, _args):
        return acts if tool == "get_activities_by_date" else sleep

    with patch.object(st._storage, "get_startup_summary", return_value=row), \
            patch.object(st._storage, "get_active_training_plan", return_value=plan or {"id": "p1", "updated_at": "t1"}), \
            patch.object(st, "_call_json", fake_call), patch.object(st._startup_cache, "is_today", return_value=True), \
            patch.object(st._startup_cache, "with_note", side_effect=lambda r: "NOTA " + r["markdown"]):
        return await agent.cached_startup_summary(changes)


def _stored_fp(ids=("1",), sleep_text=None, today=None):
    today = today or date.today().isoformat()
    return sc.fingerprint(today=today, activity_ids=list(ids), sleep=sleep_text if sleep_text is not None else
                          st._format_sleep_day({"sleep_hours": 7, "sleep_score": 80}, today), plan_row={"id": "p1", "updated_at": "t1"},
                          formula_version=TSS_FORMULA_VERSION, profile_changes=[])


class TestCachedStartupSummary:
    def _acts(self, *ids):
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.0")
        return {"activities": [{"activityId": i, "startTimeLocal": now} for i in ids]}

    def test_same_fingerprint_returns_the_saved_summary(self):
        row = _row(fp=_stored_fp())
        out = asyncio.run(_cached(_agent(), row=row, acts=self._acts(1), sleep={"sleep_hours": 7, "sleep_score": 80}))
        assert out == "NOTA ## Resumen"

    def test_a_new_activity_rebuilds(self):
        row = _row(fp=_stored_fp())
        assert asyncio.run(_cached(_agent(), row=row, acts=self._acts(1, 2), sleep={"sleep_hours": 7, "sleep_score": 80})) is None

    def test_a_new_activity_leaves_the_morning_and_both_answers_to_reuse(self):
        """11/11: entrenó entre la mañana y la tarde, mismo sueño → la HRV de la noche y lo de ayer no se piden."""
        today = date.today().isoformat()
        row = {**_row(fp=_stored_fp()), "morning": {"date": today, "hrv_today": {"h": 1}, "body_yday": {"b": 1}}}
        agent, acts, sleep = _agent(), self._acts(1, 2), {"sleep_hours": 7, "sleep_score": 80}
        assert asyncio.run(_cached(agent, row=row, acts=acts, sleep=sleep)) is None
        assert agent._startup_reuse == {"hrv_today": {"h": 1}, "body_yday": {"b": 1}, "sleep_today": sleep,
                                        "activities": acts}

    def test_new_sleep_reuses_only_the_two_answers(self):
        today = date.today().isoformat()
        row = {**_row(fp=_stored_fp(sleep_text="sin datos")), "morning": {"date": today, "hrv_today": {"h": 1}}}
        agent, acts, sleep = _agent(), self._acts(1), {"sleep_hours": 7, "sleep_score": 80}
        assert asyncio.run(_cached(agent, row=row, acts=acts, sleep=sleep)) is None
        assert agent._startup_reuse == {"sleep_today": sleep, "activities": acts}

    def test_a_hit_leaves_nothing_to_reuse(self):
        agent = _agent()
        asyncio.run(_cached(agent, row=_row(fp=_stored_fp()), acts=self._acts(1), sleep={"sleep_hours": 7, "sleep_score": 80}))
        assert agent._startup_reuse is None

    def test_new_sleep_data_rebuilds(self):
        row = _row(fp=_stored_fp(sleep_text="sin datos"))
        assert asyncio.run(_cached(_agent(), row=row, acts=self._acts(1), sleep={"sleep_hours": 7, "sleep_score": 80})) is None

    def test_a_changed_plan_rebuilds(self):
        row = _row(fp=_stored_fp())
        out = asyncio.run(_cached(_agent(), row=row, acts=self._acts(1), sleep={"sleep_hours": 7, "sleep_score": 80},
                                  plan={"id": "p1", "updated_at": "t2"}))
        assert out is None

    def test_profile_changes_rebuild(self):
        row = _row(fp=_stored_fp())
        out = asyncio.run(_cached(_agent(), row=row, acts=self._acts(1), sleep={"sleep_hours": 7, "sleep_score": 80}, changes=["peso"]))
        assert out is None

    def test_garmin_not_answering_rebuilds(self):
        row = _row(fp=_stored_fp(ids=()))
        assert asyncio.run(_cached(_agent(), row=row, acts=None, sleep=None)) is None

    def test_an_old_or_missing_summary_makes_no_garmin_calls(self):
        agent = _agent()
        with patch.object(st._storage, "get_startup_summary", return_value=_row(120)), \
                patch.object(st, "_call_json") as call:
            assert asyncio.run(agent.cached_startup_summary()) is None
        call.assert_not_called()

    def test_any_failure_falls_back_to_rebuilding(self):
        with patch.object(st._storage, "get_startup_summary", side_effect=RuntimeError("sin usuario")):
            assert asyncio.run(_agent().cached_startup_summary()) is None


class TestSaveStartupSummary:
    def _snapshot(self, **over):
        snap = {"dates": {"today": "2026-10-31"}, "body_battery": {"today": {"x": 1}}, "hrv": {"today": None},
                "sleep": {"today": {"sleep_hours": 7, "sleep_score": 80}}, "trainings": [{"activity_id": 9}]}
        snap.update(over)
        return snap

    def _save(self, snapshot, md, needs_coach):
        with patch.object(st._storage, "save_startup_summary") as save, \
                patch.object(st._storage, "get_active_training_plan", return_value=None):
            st.StartupMixin._save_startup_summary(_agent(), snapshot, [], md, needs_coach)
        return save

    def test_saves_summary_and_fingerprint(self):
        save = self._save(self._snapshot(), "## R\n### 🧠 Tu entrenador\nx", True)
        fp, md = save.call_args.args
        assert md.startswith("## R") and fp["activities"] == ["9"] and fp["date"] == "2026-10-31"
        assert save.call_args.kwargs["morning"] == {"date": "2026-10-31"}  # sin HRV ni ayer: nada que reutilizar

    def test_saves_only_the_morning_data_that_has_values(self):
        hrv = {"hrvSummary": {"calendarDate": "2026-10-31", "lastNightAvg": 62, "weeklyAvg": 60}}
        sleep_yday = {"sleep_hours": 7.5, "sleep_score": 82}
        snap = self._snapshot(hrv={"today": hrv, "yesterday": {"hrvSummary": {}}},
                              sleep={"today": {"sleep_hours": 7}, "yesterday": sleep_yday})
        morning = st._morning_data(snap, "2026-10-31")
        assert morning == {"date": "2026-10-31", "hrv_today": hrv, "sleep_yday": sleep_yday}

    def test_does_not_save_when_garmin_gave_nothing(self):
        snap = self._snapshot(body_battery={"today": None}, sleep={"today": None})
        self._save(snap, "## R", False).assert_not_called()

    def test_does_not_save_without_the_coach_text(self):
        self._save(self._snapshot(), "## R", True).assert_not_called()

    def test_a_storage_failure_does_not_break_the_startup(self):
        with patch.object(st._storage, "save_startup_summary", side_effect=RuntimeError("x")), \
                patch.object(st._storage, "get_active_training_plan", return_value=None):
            st.StartupMixin._save_startup_summary(_agent(), self._snapshot(), [], "## R", False)



class TestSnapshotReuse:
    """11/11: el snapshot no vuelve a pedir a Garmin lo que le llega en `reuse`."""

    def test_reused_payloads_skip_their_calls(self):
        today = date.today().isoformat()
        calls = []

        async def fake_call(_s, tool, args):
            calls.append((tool, args.get("date") or args.get("start_date")))
            return None

        async def no_weather():
            return None

        agent = _agent()
        agent.user_profile = {}
        agent._today_weather = no_weather
        hrv = {"hrvSummary": {"calendarDate": today, "lastNightAvg": 62, "weeklyAvg": 60}}
        sleep = {"sleep_hours": 7, "sleep_score": 80}
        reuse = {"hrv_today": hrv, "sleep_today": sleep, "sleep_yday": {"sleep_hours": 6}, "hrv_yday": {"x": 1},
                 "body_yday": {"y": 1}, "activities": {"activities": []}}
        with patch.object(st, "_call_json", fake_call), patch.object(st, "_get_active_training_plan", return_value=None), \
                patch.object(st, "_load_metrics_series", return_value=[]), \
                patch.object(st._storage, "upsert_wellness_daily"):
            snap = asyncio.run(agent.collect_startup_snapshot_48h(reuse=reuse))
        asked = {tool for tool, _ in calls}
        assert "get_hrv_data" not in asked and "get_sleep_summary" not in asked
        assert ("get_body_battery", today) in calls  # el Body Battery de hoy baja con el día: siempre se pide
        assert snap["hrv"]["today"] == hrv and snap["sleep"]["today"] == sleep
        assert snap["wellness_sources"]["hrv_today"] == "morning"

    def test_the_rebuild_uses_the_reuse_once(self):
        agent = _agent()
        agent.user_profile = {}
        agent._startup_reuse = {"sleep_today": {"s": 1}}
        seen = []

        async def fake_collect(**kwargs):
            seen.append(kwargs.get("reuse"))
            return {"body_battery": {}, "hrv": {}, "sleep": {}, "trainings": []}

        agent.collect_startup_snapshot_48h = fake_collect
        with patch.object(st, "_get_active_training_plan", return_value=None), \
                patch.object(st, "_build_proactive_status_markdown", return_value="## R"), \
                patch.object(st.StartupMixin, "_save_startup_summary"):
            asyncio.run(st.StartupMixin.build_startup_status_markdown(agent))
            asyncio.run(st.StartupMixin.build_startup_status_markdown(agent))  # /resumen: todo de Garmin
        assert seen == [{"sleep_today": {"s": 1}}, None]
