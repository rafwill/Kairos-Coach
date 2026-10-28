"""
tests/test_hr_profile.py
Zonas y umbrales de FC, ritmo umbral y FTP (agent/hr_profile.py y load_compute).
"""

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.load_metrics import _extract_threshold_pace_sec_per_km

from agent.trainer_agent import (
    TrainerAgent,
)
from tests.agent_patch import patch_all
from agent.dates import _seconds_to_hhmmss
import agent.hr_profile as _hr_profile
from agent.hr_profile import _extract_cycling_ftp_watts, _resolve_running_threshold_pace_for_date, _resolve_tp_hr_threshold_for_date
from agent.load_metrics import _resolve_hr_profile_values, _resolve_running_threshold_pace_sec_per_km


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales



class TestResolveHrProfileValues:
    def test_prefers_kairos_performance_hr_keys(self):
        profile = {
            "performance": {
                "hr_rest_bpm": 49,
                "hr_max_bpm": 188,
            }
        }

        hr_rest, hr_max = _resolve_hr_profile_values(profile)

        assert hr_rest == 49
        assert hr_max == 188

    def test_reads_garmin_user_data_hr_keys(self):
        profile = {
            "userData": {
                "restingHeartRate": 52,
                "maxHeartRate": 183,
            }
        }

        hr_rest, hr_max = _resolve_hr_profile_values(profile)

        assert hr_rest == 52
        assert hr_max == 183

    def test_float_rounds_up(self):
        # 90.6 → 91 segundos → 01:31
        assert _seconds_to_hhmmss(90.6) == "00:01:31"

    def test_marathon_time(self):
        # 3h30m = 12600s
        assert _seconds_to_hhmmss(12600) == "03:30:00"


class TestResolveRunningThresholdFromProfile:
    def test_resolve_running_threshold_from_flat_profile(self):
        profile = {
            "performance": {
                "running_threshold_pace": "4:20",
            }
        }
        out = _resolve_running_threshold_pace_sec_per_km(profile)
        assert out == 260.0

    def test_resolve_running_threshold_from_nested_profile(self):
        profile = {
            "userProfile": {
                "zones": {
                    "running": {
                        "thresholdPace": "4:30"
                    }
                }
            }
        }
        out = _resolve_running_threshold_pace_sec_per_km(profile)
        assert out == 270.0

    def test_resolve_running_threshold_from_nested_speed(self):
        profile = {
            "settings": {
                "runningThresholdSpeedMps": 3.7
            }
        }
        out = _resolve_running_threshold_pace_sec_per_km(profile)
        assert out is not None
        assert 269.0 <= out <= 271.0


class TestThresholdHistoryResolvers:
    """Umbrales del perfil con historial por fecha (ritmo, FC de TP) y lectura del umbral de Garmin."""

    def test_extract_threshold_pace_from_lactate_speed_mps_scaled_value(self):
        pace = _extract_threshold_pace_sec_per_km({"lactate_threshold_speed_mps": 0.4083})
        assert pace is not None
        # 0.4083*10 m/s => ~4:05/km
        assert 230.0 <= pace <= 260.0

    def test_resolve_tp_hr_threshold_for_date_picks_value_in_force(self):
        pass

        profile = {
            "performance": {
                "hr_threshold_bpm": 169,
                "hr_threshold_tp_history": [
                    {"date": "2026-10-21", "bpm": 169},
                    {"date": "2026-01-30", "bpm": 166},
                ],
            }
        }
        assert _resolve_tp_hr_threshold_for_date(profile, "2026-01-29") is None
        assert _resolve_tp_hr_threshold_for_date(profile, "2026-05-01") == 166
        assert _resolve_tp_hr_threshold_for_date(profile, "2026-10-21") == 169
        assert _resolve_tp_hr_threshold_for_date({"performance": {}}, "2026-05-01") is None

    def test_resolve_running_threshold_pace_for_date_picks_value_in_force(self):
        pass

        profile = {
            "performance": {
                "running_threshold_pace_sec_per_km": 252.0,
                "running_threshold_pace_history": [
                    {"date": "2026-01-30", "sec_per_km": 230},
                    {"date": "2026-05-09", "sec_per_km": 252},
                ],
            }
        }
        assert _resolve_running_threshold_pace_for_date(profile, "2026-01-29") is None
        assert _resolve_running_threshold_pace_for_date(profile, "2026-05-08") == 230
        assert _resolve_running_threshold_pace_for_date(profile, "2026-05-09") == 252
        assert _resolve_running_threshold_pace_for_date({"performance": {}}, "2026-05-01") is None


class TestHrZonesFromGarmin:
    """Zonas de FC: las de Garmin (reloj y TP) guardadas en el perfil; respaldo % LTHR sin huecos."""

    GARMIN = {"sports": [
        {"sport": "cycling", "method": "HR_RESERVE", "resting_hr": 40, "max_hr": 179, "lactate_threshold_hr": 153,
         "zones": [{"zone": i + 1, "min_bpm": f, "max_bpm": None} for i, f in enumerate((105, 125, 136, 146, 159))]},
        {"sport": "running", "method": "HR_RESERVE", "resting_hr": 40, "max_hr": 179, "lactate_threshold_hr": 166,
         "zones": [{"zone": i + 1, "min_bpm": f, "max_bpm": None} for i, f in enumerate((115, 135, 146, 158, 169))]},
    ]}

    def _profile(self, zones=None):
        perf = {"hr_threshold_bpm": 169}
        if zones:
            perf["hr_zones_garmin"] = {"updated_at": "2026-10-22", **zones}
        return {"performance": perf}

    def test_uses_garmin_zones_per_sport(self):
        zones, source = _hr_profile._resolve_hr_zones(self._profile(self.GARMIN), "running")
        assert source == "Garmin (reserva de FC)"
        assert zones == [("Z1 Recuperación", 115, 134), ("Z2 Aeróbica", 135, 145), ("Z3 Tempo", 146, 157),
                         ("Z4 Subumbral", 158, 168), ("Z5 Supraumbral", 169, 179)]
        cycling, _ = _hr_profile._resolve_hr_zones(self._profile(self.GARMIN), "cycling")
        assert cycling[1] == ("Z2 Aeróbica", 125, 135)
        # Deporte sin zonas propias y sin "default": no inventa; cae al respaldo por LTHR.
        other, source = _hr_profile._resolve_hr_zones(self._profile(self.GARMIN), "swimming")
        assert source.startswith("% del LTHR")

    def test_lthr_fallback_has_no_gaps(self):
        zones, source = _hr_profile._resolve_hr_zones(self._profile(), "running")
        assert source.startswith("% del LTHR")
        covered = {bpm for _, lo, hi in zones for bpm in range(lo, hi + 1)}
        assert set(range(100, 200)) <= covered  # antes 151 y 160 no caían en ninguna zona
        assert [z[0] for z in zones][1] == "Z2 Aeróbica"

    def test_context_block_lists_garmin_zones(self):
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.user_profile = self._profile(self.GARMIN)
        out = agent._load_and_anchors_context_block()
        assert "Zonas de FC de carrera [Garmin (reserva de FC)]: Z1 Recuperación 115–134 · Z2 Aeróbica 135–145" in out
        assert "Zonas de FC de ciclismo [Garmin (reserva de FC)]: Z1 Recuperación 105–124" in out

    def _agent_with_tool(self, payload, profile):
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = profile
        return agent, patch_all("call_tool", AsyncMock(return_value=payload))

    def _payload(self, z2_floor=135):
        return json.dumps({"sports": [
            {**s, "zones": [dict(z, min_bpm=(z2_floor if s["sport"] == "running" and z["zone"] == 2 else z["min_bpm"]))
                            for z in s["zones"]]}
            for s in self.GARMIN["sports"]]})

    @pytest.mark.asyncio
    async def test_refresh_saves_only_when_zones_change(self):
        profile = self._profile(self.GARMIN)
        agent, tool_patch = self._agent_with_tool(self._payload(), profile)
        with tool_patch, patch_all("_save_user_profile") as save_mock:
            await agent._refresh_hr_zones_from_garmin()
        save_mock.assert_not_called()

        agent, tool_patch = self._agent_with_tool(self._payload(z2_floor=136), profile)
        with tool_patch, patch_all("_save_user_profile") as save_mock:
            await agent._refresh_hr_zones_from_garmin()
        save_mock.assert_called_once()
        perf = profile["performance"]
        running = next(s for s in perf["hr_zones_garmin"]["sports"] if s["sport"] == "running")
        assert running["zones"][1]["min_bpm"] == 136
        assert perf["hr_zones_garmin"]["updated_at"] == date.today().isoformat()
        assert perf["hr_zones_garmin_history"][-1]["valid_from"] == "2026-10-22"

    @pytest.mark.asyncio
    async def test_refresh_keeps_stored_zones_when_garmin_fails(self):
        profile = self._profile(self.GARMIN)
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = profile
        with patch_all("call_tool", AsyncMock(side_effect=TimeoutError("sin red"))), \
             patch_all("_save_user_profile") as save_mock:
            await agent._refresh_hr_zones_from_garmin()
        save_mock.assert_not_called()
        assert profile["performance"]["hr_zones_garmin"]["sports"] == self.GARMIN["sports"]


class TestCyclingFtpResolution:
    def test_extract_cycling_ftp_watts_accepts_common_shapes(self):
        assert _extract_cycling_ftp_watts({"cyclingFtp": 261}) == 261.0
        assert _extract_cycling_ftp_watts({"ftp": "259"}) == 259.0
        assert _extract_cycling_ftp_watts({"data": {"functionalThresholdPower": 255}}) == 255.0
        assert _extract_cycling_ftp_watts({"functional_threshold_power_watts": 205}) == 205.0
        assert _extract_cycling_ftp_watts([{"ignored": 1}, {"functional_threshold_power": 250}]) == 250.0

    @pytest.mark.asyncio
    async def test_get_or_refresh_cycling_ftp_fetches_and_persists_when_missing(self):
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.user_profile = {"performance": {}}
        agent.mcp_session = object()

        with patch_all("call_tool", new=AsyncMock(return_value='{"data":{"cyclingFtp":266}}')) as call_mock, \
             patch_all("_save_user_profile") as save_mock:
            ftp = await TrainerAgent._get_or_refresh_cycling_ftp(agent)

        assert ftp == 266.0
        assert agent.user_profile["performance"]["cycling_ftp"] == 266.0
        call_mock.assert_awaited_once()
        save_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_refresh_cycling_ftp_uses_cache_without_mcp_call(self):
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.user_profile = {"performance": {"cycling_ftp": 248.0}}
        agent.mcp_session = object()

        with patch_all("call_tool", new=AsyncMock()) as call_mock, \
             patch_all("_save_user_profile") as save_mock:
            ftp = await TrainerAgent._get_or_refresh_cycling_ftp(agent)

        assert ftp == 248.0
        call_mock.assert_not_awaited()
        save_mock.assert_not_called()


class TestCyclingFtpRefreshDatePolicy:
    @pytest.mark.asyncio
    async def test_refresh_same_ftp_does_not_update_change_date(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {
            "performance": {
                "cycling_ftp": 250.0,
                "cycling_ftp_date": "2026-08-10",
                "performance_params_updated_at": "2026-08-10",
            }
        }
        agent.mcp_session = MagicMock()

        with patch_all("call_tool", new=AsyncMock(return_value='{"functionalThresholdPower": 250}')), patch_all(
            "_save_user_profile"
        ):
            out = await TrainerAgent._get_or_refresh_cycling_ftp(agent, force_refresh=True)

        assert out == 250.0
        perf = agent.user_profile.get("performance") or {}
        assert perf.get("cycling_ftp_date") == "2026-08-10"
        assert perf.get("performance_params_updated_at") == "2026-08-10"
