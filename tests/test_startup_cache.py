"""Caché del resumen inicial (31/10): huella, frescura, nota y flujo en agent/startup.py."""

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
    def test_under_an_hour_is_recent(self):
        assert sc.is_recent(_row(59), NOW)

    def test_an_hour_or_more_is_not(self):
        assert not sc.is_recent(_row(60), NOW) and not sc.is_recent(_row(61), NOW)

    def test_a_future_or_broken_date_is_not(self):
        assert not sc.is_recent(_row(-3), NOW)
        assert not sc.is_recent({"generated_at": "ayer"}, NOW) and not sc.is_recent(None, NOW)

    def test_note_says_when_and_how_to_refresh(self):
        text = sc.with_note(_row(5), NOW)
        assert "hace 5 min" in text and "`/resumen`" in text and text.endswith("## Resumen")


def _agent():
    agent = object.__new__(st.StartupMixin)
    agent.mcp_session = MagicMock()
    return agent


async def _cached(agent, *, row, acts, sleep, plan=None, changes=None):
    async def fake_call(_s, tool, _args):
        return acts if tool == "get_activities_by_date" else sleep

    with patch.object(st._storage, "get_startup_summary", return_value=row), \
            patch.object(st._storage, "get_active_training_plan", return_value=plan or {"id": "p1", "updated_at": "t1"}), \
            patch.object(st, "_call_json", fake_call), patch.object(st._startup_cache, "is_recent", return_value=True), \
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

    def test_does_not_save_when_garmin_gave_nothing(self):
        snap = self._snapshot(body_battery={"today": None}, sleep={"today": None})
        self._save(snap, "## R", False).assert_not_called()

    def test_does_not_save_without_the_coach_text(self):
        self._save(self._snapshot(), "## R", True).assert_not_called()

    def test_a_storage_failure_does_not_break_the_startup(self):
        with patch.object(st._storage, "save_startup_summary", side_effect=RuntimeError("x")), \
                patch.object(st._storage, "get_active_training_plan", return_value=None):
            st.StartupMixin._save_startup_summary(_agent(), self._snapshot(), [], "## R", False)

