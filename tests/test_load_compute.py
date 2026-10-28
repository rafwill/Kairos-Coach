"""
tests/test_load_compute.py
Cálculo de la carga: TSS de las actividades nuevas, refresco de días recientes y serie (agent/load_compute.py).
"""

import json
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


from tests.agent_patch import patch_all
import agent.data_access as _data_access
from agent.routes.load import _fetch_activities_for_load_calc


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales



class TestComputeAndPersistLoadMetrics:
    @pytest.mark.asyncio
    async def test_compute_persists_activity_tss_rows_matching_golden_controls(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()

        activities = [
            {
                "activityId": 24172582627,
                "type": {"typeKey": "trail_running"},
                "startTimeLocal": f"{today_iso}T07:00:00",
            },
            {
                "activityId": 24398763300,
                "type": {"typeKey": "running"},
                "startTimeLocal": f"{today_iso}T08:00:00",
                "activityTrainingLoad": 118.471,
            },
            {
                "activityId": 24227364808,
                "type": {"typeKey": "running"},
                "startTimeLocal": f"{today_iso}T09:00:00",
            },
            {
                "activityId": 24618238695,
                "type": {"typeKey": "strength_training"},
                "startTimeLocal": f"{today_iso}T10:00:00",
            },
            {
                "activityId": 24502427862,
                "type": {"typeKey": "walking"},
                "startTimeLocal": f"{today_iso}T11:00:00",
                "activityTrainingLoad": 17.073,
            },
            {
                "activityId": 24013969366,
                "type": {"typeKey": "hiking"},
                "startTimeLocal": f"{today_iso}T12:00:00",
                "activityTrainingLoad": 102.75,
            },
            {
                "activityId": 23950882994,
                "type": {"typeKey": "gravel_cycling"},
                "startTimeLocal": f"{today_iso}T13:00:00",
            },
            {
                "activityId": 24383422318,
                "type": {"typeKey": "elliptical"},
                "startTimeLocal": f"{today_iso}T14:00:00",
            },
        ]
        expected = {
            24172582627: (228.266, "hrTSS", "kairos_model", "trail:lthr_details", None),
            24398763300: (118.471, "TSS", "garmin_native", "garmin:native_load", 118.471),
            24227364808: (91.996, "TSS", "kairos_model", "running:tp_like_adjusted", None),
            24618238695: (16.597, "hrTSS", "kairos_model", "strength:modelo_B", None),
            24502427862: (17.073, "TSS", "kairos_model", "walk_hike:metabolico", 17.073),
            24013969366: (102.75, "TSS", "kairos_model", "walk_hike:metabolico", 102.75),
            23950882994: (50.825, "TSS", "kairos_model", "cycling:power_ftp", None),
            24383422318: (23.772, "hrTSS", "kairos_model", "gym_cardio:lthr", None),
        }

        async def _fake_fetch(_session, _start_date, _end_date):
            return activities

        def _fake_estimate(act, **_kwargs):
            aid = int(act.get("activityId") or 0)
            tss, label, source, model, _native_load = expected[aid]
            act["_kairos_model_tag"] = model
            act["_kairos_used_native_tss"] = source == "garmin_native"
            if model.startswith("gym_cardio:"):
                act["_kairos_tss_source_tag"] = model
            return tss, label

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {"primary": "running"},
            "performance": {"running_threshold_pace_sec_per_km": 252.0},
        }

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[[], []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=None), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series"), \
             patch("agent.trainer_agent._storage.upsert_activity_tss_rows") as upsert_activity_tss_mock, \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch_all("_estimate_session_tss", side_effect=_fake_estimate), \
             patch_all("_hydrate_profile_hr_anchors_if_missing", new=AsyncMock(return_value=(37.0, None, 169.0))), \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=205.0)), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=True)

        upsert_activity_tss_mock.assert_called_once()
        persisted_rows = upsert_activity_tss_mock.call_args[0][0]
        by_id = {int(r["activity_id"]): r for r in persisted_rows}
        assert len(by_id) == 8
        type_key_by_id = {int(a["activityId"]): a["type"]["typeKey"] for a in activities}
        for aid, (tss, label, source, model, native_load) in expected.items():
            assert aid in by_id
            assert by_id[aid]["sport"] == type_key_by_id[aid]
            assert by_id[aid]["start_date"] == today_iso
            assert by_id[aid]["method"] == label
            assert by_id[aid]["tss_source"] == source
            assert by_id[aid]["model"] == model
            if native_load is None:
                assert by_id[aid]["native_load"] is None
            else:
                assert by_id[aid]["native_load"] == pytest.approx(native_load, abs=0.001)
            assert by_id[aid]["tss"] == pytest.approx(tss, abs=0.001)

    @pytest.mark.asyncio
    async def test_compute_continues_when_activity_tss_persistence_fails(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()

        async def _fake_fetch(_session, _start_date, _end_date):
            return [
                {
                    "activityId": 123,
                    "type": {"typeKey": "running"},
                    "startTimeLocal": f"{today_iso}T07:00:00",
                }
            ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {"primary": "running"},
            "performance": {"running_threshold_pace_sec_per_km": 252.0},
        }

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[[], []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=None), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_daily_mock, \
             patch("agent.trainer_agent._storage.upsert_activity_tss_rows", side_effect=RuntimeError("boom")), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch_all("_estimate_session_tss", return_value=(50.0, "TSS")), \
             patch_all("_infer_tss_source_tag", return_value="kairos_model"), \
             patch_all("_hydrate_profile_hr_anchors_if_missing", new=AsyncMock(return_value=(37.0, None, 169.0))), \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=205.0)), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=True)

        upsert_daily_mock.assert_called_once()

    async def _run_compute_capturing_details(self, activities, details_by_id):
        """Ejecuta compute_and_persist_load_metrics y devuelve {activity_id: activity_details_raw pasado}."""
        from agent.trainer_agent import (
            TrainerAgent,
        )

        async def _fake_fetch(_session, _start_date, _end_date):
            return activities

        async def _fake_call_tool(_session, tool_name, args):
            if tool_name == "get_activity_details":
                return details_by_id.get(int(args["activity_id"]), "")
            return ""

        passed: dict[int, str | None] = {}

        def _fake_estimate(act, **kwargs):
            passed[int(act.get("activityId") or 0)] = kwargs.get("activity_details_raw")
            return 50.0, "TSS"

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {"primary": "running"},
            "performance": {"running_threshold_pace_sec_per_km": 252.0},
        }

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[[], []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=None), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series"), \
             patch("agent.trainer_agent._storage.upsert_activity_tss_rows"), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch_all("_estimate_session_tss", side_effect=_fake_estimate), \
             patch_all("_hydrate_profile_hr_anchors_if_missing", new=AsyncMock(return_value=(37.0, None, 169.0))), \
             patch_all("call_tool", new=AsyncMock(side_effect=_fake_call_tool)) as call_mock, \
             patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=205.0)), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=True)

        details_calls = [
            int(c.args[2]["activity_id"]) for c in call_mock.await_args_list if c.args[1] == "get_activity_details"
        ]
        return passed, details_calls

    async def _run_compute(
        self,
        activities,
        *,
        stored=None,
        seed=None,
        rhr_by_day=None,
        rebuild_from=None,
        profile_perf=None,
    ):
        """Ejecuta compute_and_persist_load_metrics y captura lecturas/escrituras."""
        from agent.trainer_agent import (
            TrainerAgent,
        )

        async def _fake_fetch(_session, _start_date, _end_date):
            return [dict(a) for a in activities]

        async def _fake_call_tool(_session, tool_name, args):
            if tool_name == "get_rhr_day":
                value = (rhr_by_day or {}).get(args["date"])
                return json.dumps({"restingHeartRate": value}) if value is not None else ""
            if tool_name == "get_activity_details":
                return '{"activityId": %d}' % int(args["activity_id"])
            return ""

        estimate_calls: list[dict] = []

        def _fake_estimate(act, **kwargs):
            estimate_calls.append({"activity_id": int(act.get("activityId") or 0), **kwargs})
            return 50.0, "TSS"

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {"primary": "running"},
            "performance": {"running_threshold_pace_sec_per_km": 252.0, **(profile_perf or {})},
        }
        stored_mock = MagicMock(return_value=stored if stored is not None else {})

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[[], []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=None), \
             patch("agent.trainer_agent._storage.get_activity_tss_map", stored_mock), \
             patch("agent.trainer_agent._storage.get_load_metrics_row", return_value=seed), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_series_mock, \
             patch("agent.trainer_agent._storage.upsert_activity_tss_rows") as upsert_rows_mock, \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch_all("_estimate_session_tss", side_effect=_fake_estimate), \
             patch_all("_hydrate_profile_hr_anchors_if_missing", new=AsyncMock(return_value=(37.0, None, 169.0))), \
             patch_all("call_tool", new=AsyncMock(side_effect=_fake_call_tool)) as call_mock, \
             patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=205.0)), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(
                agent, force_full_recalc=True, rebuild_from=rebuild_from
            )

        series = upsert_series_mock.call_args[0][0] if upsert_series_mock.called else None
        rows = upsert_rows_mock.call_args[0][0] if upsert_rows_mock.called else []
        tool_calls = [(c.args[1], c.args[2]) for c in call_mock.await_args_list]
        return {
            "estimate_calls": estimate_calls,
            "series": series,
            "rows": rows,
            "tool_calls": tool_calls,
            "stored_mock": stored_mock,
            "profile": agent.user_profile,
        }

    @pytest.mark.asyncio
    async def test_stored_activity_tss_is_reused_not_recomputed(self):
        today_iso = date.today().isoformat()
        out = await self._run_compute(
            [
                {"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T07:00:00"},
                {"activityId": 2, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T19:00:00"},
            ],
            stored={1: {"activity_id": 1, "start_date": today_iso, "tss": 80.0}},
        )
        assert [c["activity_id"] for c in out["estimate_calls"]] == [2]
        assert [int(r["activity_id"]) for r in out["rows"]] == [2]
        assert all(args.get("activity_id") != 1 for name, args in out["tool_calls"] if name == "get_activity_details")
        today_row = next(r for r in out["series"] if r["date"] == today_iso)
        assert today_row["tss"] == pytest.approx(130.0)
        assert today_row["activities_count"] == 2

    @pytest.mark.asyncio
    async def test_threshold_change_applies_only_from_its_date(self):
        # Caso real (22/10): ritmo umbral 4:12 → 4:00 desde hoy. Lo ya guardado no se toca;
        # cada actividad nueva se calcula con el umbral vigente en su fecha.
        today = date.today()
        yday = (today - timedelta(days=1)).isoformat()
        history = [
            {"date": (today - timedelta(days=30)).isoformat(), "sec_per_km": 252},
            {"date": today.isoformat(), "sec_per_km": 240.0},
        ]
        out = await self._run_compute(
            [
                {"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{yday}T07:00:00"},
                {"activityId": 3, "type": {"typeKey": "running"}, "startTimeLocal": f"{yday}T19:00:00"},
                {"activityId": 2, "type": {"typeKey": "running"}, "startTimeLocal": f"{today.isoformat()}T07:00:00"},
            ],
            stored={1: {"activity_id": 1, "start_date": yday, "tss": 80.0}},
            profile_perf={"running_threshold_pace_sec_per_km": 240.0, "running_threshold_pace_history": history},
        )
        paces = {c["activity_id"]: c["running_threshold_pace_sec_per_km"] for c in out["estimate_calls"]}
        assert paces == {3: 252.0, 2: 240.0}   # la guardada (1) no se recalcula
        yday_row = next(r for r in out["series"] if r["date"] == yday)
        assert yday_row["tss"] == pytest.approx(130.0)  # 80 guardado + 50 nuevo

    @pytest.mark.asyncio
    async def test_unreadable_activity_tss_does_not_touch_history(self):
        out_none = await self._run_compute_with_stored_none(date.today().isoformat())
        assert out_none["estimate_calls"] == []
        assert out_none["rows"] == []
        assert out_none["series"] is None

    async def _run_compute_with_stored_none(self, today_iso):
        with patch("agent.trainer_agent._storage.get_activity_tss_map", return_value=None):
            from agent.trainer_agent import (
                TrainerAgent,
            )

            agent = object.__new__(TrainerAgent)
            agent.mcp_session = MagicMock()
            agent.user_profile = {"performance": {}}
            estimate_mock = MagicMock(return_value=(50.0, "TSS"))
            with patch("agent.trainer_agent._storage.get_load_metrics_series", return_value=[]), \
                 patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=None), \
                 patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_series_mock, \
                 patch("agent.trainer_agent._storage.upsert_activity_tss_rows") as upsert_rows_mock, \
                 patch_all("_fetch_activities_for_load_calc", new=AsyncMock(return_value=[
                     {"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T07:00:00"}
                 ])), \
                 patch_all("_estimate_session_tss", estimate_mock), \
                 patch_all("call_tool", new=AsyncMock(return_value="")), \
                 patch.object(TrainerAgent, "_get_or_refresh_cycling_ftp", new=AsyncMock(return_value=None)), \
                 patch.object(TrainerAgent, "_apply_series_to_profile"):
                await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=True)
            return {
                "estimate_calls": estimate_mock.call_args_list,
                "rows": upsert_rows_mock.call_args[0][0] if upsert_rows_mock.called else [],
                "series": upsert_series_mock.call_args[0][0] if upsert_series_mock.called else None,
            }

    @pytest.mark.asyncio
    async def test_hr_rest_is_mean_of_14_days_before_activity(self):
        today = date.today()
        rhr = {(today - timedelta(days=i)).isoformat(): 40.0 + (i % 2) * 2.0 for i in range(1, 15)}
        rhr[today.isoformat()] = 99.0  # el propio día no cuenta
        out = await self._run_compute(
            [{"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today.isoformat()}T07:00:00"}],
            rhr_by_day=rhr,
        )
        assert out["estimate_calls"][0]["hr_rest_bpm"] == pytest.approx(41.0)
        assert out["rows"][0]["hr_rest_used"] == pytest.approx(41.0)

    @pytest.mark.asyncio
    async def test_hr_rest_falls_back_to_profile_without_rhr_history(self):
        today_iso = date.today().isoformat()
        out = await self._run_compute(
            [{"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T07:00:00"}],
        )
        assert out["estimate_calls"][0]["hr_rest_bpm"] == pytest.approx(37.0)

    @pytest.mark.asyncio
    async def test_ewma_is_seeded_from_previous_day_row(self):
        out = await self._run_compute([], seed={"date": "x", "atl": 70.0, "ctl": 60.0, "tsb": -10.0, "tss": 0.0})
        first = out["series"][0]
        assert first["tss"] == 0.0
        assert first["atl"] == pytest.approx(70.0 - 70.0 / 7.0, abs=0.01)
        assert first["ctl"] == pytest.approx(60.0 - 60.0 / 42.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_rebuild_ignores_stored_tss_and_seeds_zero(self):
        start = (date.today() - timedelta(days=3)).isoformat()
        out = await self._run_compute(
            [{"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{start}T07:00:00"}],
            stored={1: {"activity_id": 1, "start_date": start, "tss": 80.0}},
            seed={"date": "x", "atl": 70.0, "ctl": 60.0, "tsb": -10.0, "tss": 0.0},
            rebuild_from=start,
        )
        out["stored_mock"].assert_not_called()
        assert [c["activity_id"] for c in out["estimate_calls"]] == [1]
        first = out["series"][0]
        assert first["date"] == start
        assert first["ctl"] == pytest.approx(50.0 / 42.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_walk_threshold_uses_profile_anchor_outside_rebuild(self):
        today_iso = date.today().isoformat()
        out = await self._run_compute(
            [{"activityId": 1, "type": {"typeKey": "walking"}, "startTimeLocal": f"{today_iso}T07:00:00"}],
            profile_perf={"walk_hike_threshold_speed_m_min": 101.5},
        )
        assert out["rows"][0]["walk_threshold_used"] == pytest.approx(101.5)

    @pytest.mark.asyncio
    async def test_compute_passes_activity_details_to_running_non_trail(self):
        today_iso = date.today().isoformat()
        details = '{"activityId": 1, "metricDescriptors": []}'
        passed, details_calls = await self._run_compute_capturing_details(
            [
                {"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T07:00:00"},
                {"activityId": 2, "type": {"typeKey": "strength_training"}, "startTimeLocal": f"{today_iso}T18:00:00"},
            ],
            {1: details, 2: '{"activityId": 2}'},
        )
        assert passed[1] == details
        assert 2 not in details_calls
        assert passed[2] is None

    @pytest.mark.asyncio
    async def test_compute_ignores_error_text_as_activity_details(self):
        today_iso = date.today().isoformat()
        passed, details_calls = await self._run_compute_capturing_details(
            [{"activityId": 1, "type": {"typeKey": "running"}, "startTimeLocal": f"{today_iso}T07:00:00"}],
            {1: "Error executing tool get_activity_details: 429 Too Many Requests"},
        )
        assert details_calls == [1]
        assert passed[1] is None

    @pytest.mark.asyncio
    async def test_force_full_recalc_fetches_full_window_for_new_user(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        expected_start = (today - timedelta(days=120)).isoformat()
        captured_fetch = {}

        async def _fake_fetch(_session, start_date, end_date):
            captured_fetch["start_date"] = start_date
            captured_fetch["end_date"] = end_date
            return []

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {}

        existing_series = [
            {
                "date": today.isoformat(),
                "tss": 45.0,
                "atl": 40.0,
                "ctl": 30.0,
                "tsb": -10.0,
                "activities_count": 1,
            }
        ]

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today.isoformat()), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch.object(TrainerAgent, "_apply_series_to_profile") as apply_mock:
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=True)

        assert captured_fetch["start_date"] == expected_start
        assert captured_fetch["end_date"] == today.isoformat()
        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows, "Debe persistir la serie completa del rango histórico"
        assert persisted_rows[0]["date"] == expected_start
        assert persisted_rows[-1]["date"] == today.isoformat()
        apply_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_existing_user_up_to_date_keeps_incremental_path(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        existing_series = [
            {
                "date": today.isoformat(),
                "tss": 55.0,
                "atl": 48.0,
                "ctl": 36.0,
                "tsb": -12.0,
                "activities_count": 1,
            }
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "load_metrics": {
                "formula_version": _data_access._TSS_FORMULA_VERSION,
            }
        }

        with patch("agent.trainer_agent._storage.get_load_metrics_series", return_value=existing_series), \
                 patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today.isoformat()), \
                 patch_all("_fetch_activities_for_load_calc", new=AsyncMock(return_value=[])) as fetch_mock, \
                 patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
                 patch.object(TrainerAgent, "_apply_series_to_profile") as apply_mock:
                await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert fetch_mock.await_count >= 1
        assert any(
            c.args[1] == today.isoformat() and c.args[2] == today.isoformat()
            for c in fetch_mock.await_args_list
        )
        upsert_mock.assert_not_called()
        apply_mock.assert_called_once_with(existing_series, today)

    @pytest.mark.asyncio
    async def test_existing_user_up_to_date_refreshes_today_when_new_activity_detected(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        existing_series = [
            {
                "date": today_iso,
                "tss": 0.0,
                "atl": 48.0,
                "ctl": 36.0,
                "tsb": -12.0,
                "activities_count": 0,
            }
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {"load_metrics": {"formula_version": _data_access._TSS_FORMULA_VERSION}}

        async def _fake_fetch(_session, start_date, end_date):
            if start_date == today_iso and end_date == today_iso:
                return [{"activityId": 123, "startTimeLocal": f"{today_iso}T07:10:00", "trainingLoad": 82.49, "type": "running"}]
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile") as apply_mock:
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert fetch_mock.await_count >= 2
        assert any(
            c.args[1] == today_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows
        assert persisted_rows[0]["date"] == today_iso
        apply_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_formula_change_full_recalc_bypasses_effective_date_clamp(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        expected_start = (today - timedelta(days=120)).isoformat()
        existing_series = [
            {
                "date": today_iso,
                "tss": 10.0,
                "atl": 20.0,
                "ctl": 30.0,
                "tsb": 10.0,
                "activities_count": 1,
            }
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        # Fuerza formula_changed y además effective_date=today para reproducir el bug.
        agent.user_profile = {
            "load_metrics": {"formula_version": 0},
            "performance": {"performance_params_updated_at": today_iso},
        }

        captured = {}

        async def _fake_fetch(_session, start_date, end_date):
            captured["start_date"] = start_date
            captured["end_date"] = end_date
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert captured["start_date"] == expected_start
        assert captured["end_date"] == today_iso
        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows
        assert persisted_rows[0]["date"] == expected_start

    @pytest.mark.asyncio
    async def test_existing_user_up_to_date_refreshes_from_yesterday_when_missing_activity(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        two_days_ago_iso = (today - timedelta(days=2)).isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()
        existing_series = [
            {
                "date": two_days_ago_iso,
                "tss": 32.0,
                "atl": 42.0,
                "ctl": 33.0,
                "tsb": -9.0,
                "activities_count": 1,
            },
            {
                "date": yesterday_iso,
                "tss": 0.0,
                "atl": 44.0,
                "ctl": 34.0,
                "tsb": -10.0,
                "activities_count": 0,
            },
            {
                "date": today_iso,
                "tss": 0.0,
                "atl": 46.0,
                "ctl": 35.0,
                "tsb": -11.0,
                "activities_count": 0,
            },
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {"load_metrics": {"formula_version": _data_access._TSS_FORMULA_VERSION}}

        async def _fake_fetch(_session, start_date, end_date):
            if start_date == today_iso and end_date == today_iso:
                return []
            if start_date == two_days_ago_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 321,
                        "startTimeLocal": f"{yesterday_iso}T07:10:00",
                        "trainingLoad": 67.2,
                        "type": "hiking",
                        "duration": 5400,
                        "averageHR": 128,
                        "maxHR": 162,
                    }
                ]
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile") as apply_mock:
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert any(
            c.args[1] == two_days_ago_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        assert any(
            c.args[1] == yesterday_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows
        assert persisted_rows[0]["date"] == yesterday_iso
        apply_mock.assert_called_once()


class TestFetchActivitiesForLoadCalc:
    @pytest.mark.asyncio
    async def test_fetch_activities_does_not_stop_when_first_page_is_older(self):
        from datetime import date as _date

        today = _date.today()
        today_iso = today.isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()
        old_iso = (today - timedelta(days=30)).isoformat()

        first_page = json.dumps(
            {
                "activities": [
                    {"activityId": 1, "startTimeLocal": f"{old_iso}T08:00:00"},
                ],
                "has_more": True,
                "next_start": 100,
            }
        )
        second_page = json.dumps(
            {
                "activities": [
                    {"activityId": 2, "startTimeLocal": f"{yesterday_iso}T07:00:00", "trainingLoad": 42.0},
                ],
                "has_more": False,
                "next_start": 200,
            }
        )

        session = MagicMock()
        with patch_all("call_tool", new=AsyncMock(side_effect=[first_page, second_page])):
            out = await _fetch_activities_for_load_calc(session, yesterday_iso, today_iso)

        assert len(out) == 1
        assert int(out[0].get("activityId") or 0) == 2

    @pytest.mark.asyncio
    async def test_fetch_activities_parses_concatenated_json_objects(self):
        from datetime import date as _date

        today = _date.today()
        today_iso = today.isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()

        raw_concat = "\n".join(
            [
                json.dumps(
                    {
                        "activityId": 10,
                        "startTimeLocal": f"{today_iso}T07:00:00",
                        "trainingLoad": 50.0,
                    }
                ),
                json.dumps(
                    {
                        "activityId": 11,
                        "startTimeLocal": f"{yesterday_iso}T08:00:00",
                        "trainingLoad": 40.0,
                    }
                ),
            ]
        )

        session = MagicMock()
        with patch_all("call_tool", new=AsyncMock(return_value=raw_concat)):
            out = await _fetch_activities_for_load_calc(session, yesterday_iso, today_iso)

        ids = {int(a.get("activityId") or 0) for a in out}
        assert ids == {10, 11}

    @pytest.mark.asyncio
    async def test_fetch_activities_paginates_full_plain_list_pages(self):
        from datetime import date as _date

        today = _date.today()
        start_iso = (today - timedelta(days=300)).isoformat()

        def _page(first_id, first_day_back):
            return json.dumps([
                {"activityId": first_id + i, "startTimeLocal": f"{(today - timedelta(days=first_day_back + i)).isoformat()}T07:00:00"}
                for i in range(100)
            ])

        pages = [_page(1, 0), _page(101, 100), json.dumps([
            {"activityId": 201, "startTimeLocal": f"{(today - timedelta(days=250)).isoformat()}T07:00:00"},
        ])]
        session = MagicMock()
        call_mock = AsyncMock(side_effect=pages)
        with patch_all("call_tool", new=call_mock):
            out = await _fetch_activities_for_load_calc(session, start_iso, today.isoformat())

        assert len(out) == 201
        starts = [c.args[2]["start"] for c in call_mock.await_args_list]
        assert starts == ["0", "100", "200"]

    @pytest.mark.asyncio
    async def test_fetch_activities_stops_when_whole_page_is_older_than_range(self):
        from datetime import date as _date

        today = _date.today()
        start_iso = (today - timedelta(days=50)).isoformat()
        recent = json.dumps([
            {"activityId": i, "startTimeLocal": f"{(today - timedelta(days=i // 2)).isoformat()}T07:00:00"}
            for i in range(100)
        ])
        older = json.dumps([
            {"activityId": 1000 + i, "startTimeLocal": f"{(today - timedelta(days=60 + i)).isoformat()}T07:00:00"}
            for i in range(100)
        ])
        session = MagicMock()
        call_mock = AsyncMock(side_effect=[recent, older, older])
        with patch_all("call_tool", new=call_mock):
            out = await _fetch_activities_for_load_calc(session, start_iso, today.isoformat())

        assert len(out) == 100
        assert call_mock.await_count == 2


class TestLoadRefreshWindow:
    """Refresco de los días recientes al arrancar (venían en TestActivityAnalysisMarkdownFormatting)."""

    @pytest.mark.asyncio
    async def test_recent_refresh_from_yesterday_bypasses_effective_date_clamp(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()
        existing_series = [
            {
                "date": yesterday_iso,
                "tss": 0.0,
                "atl": 44.0,
                "ctl": 34.0,
                "tsb": -10.0,
                "activities_count": 0,
            },
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "load_metrics": {"formula_version": _data_access._TSS_FORMULA_VERSION},
            "performance": {"performance_params_updated_at": today_iso},
        }

        async def _fake_fetch(_session, start_date, end_date):
            if start_date == today_iso and end_date == today_iso:
                return []
            if start_date == yesterday_iso and end_date == yesterday_iso:
                return [
                    {
                        "activityId": 999,
                        "startTimeLocal": f"{yesterday_iso}T09:00:00",
                        "trainingLoad": 58.5,
                        "type": "running",
                        "duration": 3600,
                        "averageHR": 140,
                        "maxHR": 170,
                    },
                    {
                        "activityId": 1001,
                        "startTimeLocal": f"{yesterday_iso}T18:20:00",
                        "trainingLoad": 21.5,
                        "type": "running",
                        "duration": 1800,
                        "averageHR": 138,
                        "maxHR": 168,
                    },
                ]
            if start_date == yesterday_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 999,
                        "startTimeLocal": f"{yesterday_iso}T09:00:00",
                        "trainingLoad": 58.5,
                        "type": "running",
                        "duration": 3600,
                        "averageHR": 140,
                        "maxHR": 170,
                    },
                    {
                        "activityId": 1001,
                        "startTimeLocal": f"{yesterday_iso}T18:20:00",
                        "trainingLoad": 21.5,
                        "type": "running",
                        "duration": 1800,
                        "averageHR": 138,
                        "maxHR": 168,
                    },
                ]
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch), \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows
        # Debe iniciar en ayer, no en hoy, aunque performance_params_updated_at == hoy.
        assert persisted_rows[0]["date"] == yesterday_iso

    @pytest.mark.asyncio
    async def test_up_to_date_checks_from_last_trained_day_not_fixed_recent_window(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        fourteen_days_ago_iso = (today - timedelta(days=14)).isoformat()
        existing_series = [
            {
                "date": fourteen_days_ago_iso,
                "tss": 60.0,
                "atl": 40.0,
                "ctl": 32.0,
                "tsb": -8.0,
                "activities_count": 1,
            },
            {
                "date": today_iso,
                "tss": 0.0,
                "atl": 38.0,
                "ctl": 31.0,
                "tsb": -7.0,
                "activities_count": 0,
            },
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "load_metrics": {"formula_version": _data_access._TSS_FORMULA_VERSION}
        }

        async def _fake_fetch(_session, start_date, end_date):
            if start_date == fourteen_days_ago_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 777,
                        "startTimeLocal": f"{today_iso}T07:10:00",
                        "trainingLoad": 72.0,
                        "type": "running",
                        "duration": 3600,
                        "averageHR": 142,
                        "maxHR": 172,
                    }
                ]
            if start_date == today_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 777,
                        "startTimeLocal": f"{today_iso}T07:10:00",
                        "trainingLoad": 72.0,
                        "type": "running",
                        "duration": 3600,
                        "averageHR": 142,
                        "maxHR": 172,
                    }
                ]
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert any(
            c.args[1] == fourteen_days_ago_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        upsert_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_existing_user_up_to_date_refreshes_from_two_days_ago_when_missing_activity(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        today = date.today()
        today_iso = today.isoformat()
        three_days_ago_iso = (today - timedelta(days=3)).isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()
        two_days_ago_iso = (today - timedelta(days=2)).isoformat()
        existing_series = [
            {
                "date": three_days_ago_iso,
                "tss": 18.0,
                "atl": 40.0,
                "ctl": 32.0,
                "tsb": -8.0,
                "activities_count": 1,
            },
            {
                "date": two_days_ago_iso,
                "tss": 0.0,
                "atl": 42.0,
                "ctl": 33.0,
                "tsb": -9.0,
                "activities_count": 0,
            },
            {
                "date": yesterday_iso,
                "tss": 0.0,
                "atl": 43.5,
                "ctl": 33.8,
                "tsb": -9.7,
                "activities_count": 0,
            },
            {
                "date": today_iso,
                "tss": 0.0,
                "atl": 41.0,
                "ctl": 33.5,
                "tsb": -7.5,
                "activities_count": 0,
            },
        ]

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "load_metrics": {"formula_version": _data_access._TSS_FORMULA_VERSION},
            "performance": {"performance_params_updated_at": today_iso},
        }

        async def _fake_fetch(_session, start_date, end_date):
            if start_date == today_iso and end_date == today_iso:
                return []
            if start_date == three_days_ago_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 888,
                        "startTimeLocal": f"{two_days_ago_iso}T07:10:00",
                        "trainingLoad": 61.4,
                        "type": "hiking",
                        "duration": 5400,
                        "averageHR": 126,
                        "maxHR": 160,
                    }
                ]
            if start_date == two_days_ago_iso and end_date == today_iso:
                return [
                    {
                        "activityId": 888,
                        "startTimeLocal": f"{two_days_ago_iso}T07:10:00",
                        "trainingLoad": 61.4,
                        "type": "hiking",
                        "duration": 5400,
                        "averageHR": 126,
                        "maxHR": 160,
                    }
                ]
            return []

        with patch("agent.trainer_agent._storage.get_load_metrics_series", side_effect=[existing_series, []]), \
             patch("agent.trainer_agent._storage.get_load_metrics_last_date", return_value=today_iso), \
             patch_all("_fetch_activities_for_load_calc", side_effect=_fake_fetch) as fetch_mock, \
             patch("agent.trainer_agent._storage.upsert_load_metrics_series") as upsert_mock, \
             patch_all("call_tool", new=AsyncMock(return_value="")), \
             patch.object(TrainerAgent, "_apply_series_to_profile"):
            await TrainerAgent.compute_and_persist_load_metrics(agent, force_full_recalc=False)

        assert any(
            c.args[1] == three_days_ago_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        assert any(
            c.args[1] == two_days_ago_iso and c.args[2] == today_iso
            for c in fetch_mock.await_args_list
        )
        upsert_mock.assert_called_once()
        persisted_rows = upsert_mock.call_args[0][0]
        assert persisted_rows
        assert persisted_rows[0]["date"] == two_days_ago_iso
