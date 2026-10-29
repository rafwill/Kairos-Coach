"""
tests/test_startup.py
Arranque: foto de 48 h, resumen inicial y pasos en paralelo (agent/startup.py, startup_status.py).
"""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.trainer_agent as ta

from tests.agent_patch import patch_all, patch_everywhere
from agent.tool_results import _extract_activities_list
import agent.data_access as _data_access
from agent.garmin_data import _is_activity_in_last_48h
from agent.wellness import _format_body_battery_day, _format_hrv_day, _format_sleep_day, _pick_day_payload
from agent.daily_engine import _compute_daily_plan_adjustment, _compute_plan_execution_feedback
from agent.startup_status import _build_proactive_status_markdown


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales



class TestStartupProactive:
    def test_extract_activities_list_supports_dict_payload(self):
        payload = {"activities": [{"activityId": 1}, {"activityId": 2}]}
        out = _extract_activities_list(payload)
        assert len(out) == 2

    def test_is_activity_in_last_48h_true_for_recent_day(self):
        today = date.today().isoformat()
        activity = {"startTimeLocal": f"{today}T08:00:00.0"}
        now = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
        assert _is_activity_in_last_48h(activity, now=now)

    def test_is_activity_in_last_48h_true_for_recent_day_snake_case(self):
        today = date.today().isoformat()
        activity = {"start_time_local": f"{today}T08:00:00.0"}
        now = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
        assert _is_activity_in_last_48h(activity, now=now)

    def test_build_proactive_status_markdown_contains_sections(self):
        payload = {
            "profile_changes": ["peso", "altura"],
            "body_battery": {"summary": "hoy=ok · ayer=ok"},
            "hrv": {"summary": "hoy=no · ayer=ok"},
            "sleep": {"summary": "hoy=ok · ayer=no"},
            "load_fatigue": {
                "latest": {"tss": 75.0, "atl": 62.0, "ctl": 55.0, "tsb": -7.0},
                "weekly": {"current_tss": 420.0},
                "ranges": {"tsb_low": -10.0, "tsb_high": 5.0, "atl_high": 70.0},
                "recommendation": "Puedes mantener sesión de calidad o progresión controlada según plan.",
                "action": "buena disponibilidad",
            },
            "trainings": [{"date": "2026-07-06", "name": "Trail suave"}],
        }
        out = _build_proactive_status_markdown(payload)
        assert "Estado proactivo" in out
        assert "Perfil Garmin actualizado" in out
        assert "Trail suave" in out
        assert "Carga/Fatiga (TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma))" in out
        assert "No tienes plan asignado" in out

    def test_proactive_status_flags_unsynced_today_instead_of_datos_disponibles(self):
        # Caso real (22/10): Garmin devuelve el registro de hoy sin valores (reloj sin sincronizar).
        today, yday = "2026-10-22", "2026-10-21"
        payload = {
            "dates": {"today": today, "yesterday": yday},
            "body_battery": {
                "today": [{"date": today, "charged": None, "drained": None, "bodyBatteryValuesArray": [[1, None]]}],
                "yesterday": [{"date": yday, "charged": 48, "drained": 15}],
            },
            "hrv": {"today": None, "yesterday": {"calendarDate": yday, "lastNightAvg": 56, "weeklyAvg": 61, "status": "BALANCED"}},
            "sleep": {
                "today": {"calendarDate": today, "sleepTimeSeconds": None},
                "yesterday": {"calendarDate": yday, "sleepTimeSeconds": 29040, "sleepScore": 69},
            },
            "load_fatigue": {},
            "trainings": [],
        }

        out = _build_proactive_status_markdown(payload)

        assert "datos disponibles" not in out
        assert "noche anterior=8 h 04 min · puntuación 69" in out
        assert "Garmin aún no tiene Body Battery de hoy, HRV de anoche, sueño de anoche" in out
        assert "sincroniza el reloj" in out

    def test_format_wellness_day_without_values_says_sin_datos(self):
        today = date.today().isoformat()
        assert _format_body_battery_day([{"date": today, "charged": None, "drained": None}], today) == "sin datos"
        assert _format_sleep_day({"calendarDate": today, "sleepTimeSeconds": None}, today) == "sin datos"

    def test_format_body_battery_day_prefers_level_from_most_informative_entry(self):
        payload = [
            {"date": date.today().isoformat(), "charged": 58, "drained": 31},
            {"date": date.today().isoformat(), "bodyBatteryLevel": 69, "charged": 58, "drained": 31},
        ]
        out = _format_body_battery_day(payload, date.today().isoformat())
        assert "nivel 69" in out
        assert "cargado/drenado +58/-31" in out

    def test_pick_day_payload_supports_nested_data_envelopes(self):
        target = date.today().isoformat()
        payload = {
            "ok": True,
            "data": {
                "items": [
                    {"date": "2026-01-01", "value": 10},
                    {"date": target, "value": 55},
                ]
            },
        }
        picked = _pick_day_payload(payload, target)
        assert picked is not None
        assert picked.get("value") == 55

    def test_format_hrv_day_with_nested_envelope(self):
        target = date.today().isoformat()
        payload = {
            "result": {
                "measurements": [
                    {"date": target, "lastNightAvg": 51, "weeklyAvg": 55, "status": "balanced"}
                ]
            }
        }
        out = _format_hrv_day(payload, target)
        assert "51.0 ms" in out
        assert "7d 55.0 ms" in out

    def test_build_proactive_status_markdown_shows_plan_recommendation_when_assigned(self):
        payload = {
            "plan_assigned": True,
            "plan_recommendation": "Tienes un objetivo activo (10K). ¿Quieres que adapte la sesion de hoy a ese plan?",
            "body_battery": {"summary": "sin datos"},
            "hrv": {"summary": "sin datos"},
            "sleep": {"summary": "sin datos"},
            "trainings": [],
        }
        out = _build_proactive_status_markdown(payload)
        assert "Tienes un objetivo activo (10K)" in out

    def test_build_proactive_status_markdown_shows_deterministic_decision(self):
        payload = {
            "plan_assigned": True,
            "plan_recommendation": "Plan activo",
            "daily_plan_decision": {
                "decision": "reduce",
                "reason": "estado neutral con una señal de riesgo",
                "resulting_session": "Rodaje 50' -> reducir volumen 20-30%",
            },
            "body_battery": {"summary": "sin datos"},
            "hrv": {"summary": "sin datos"},
            "sleep": {"summary": "sin datos"},
            "trainings": [],
        }
        out = _build_proactive_status_markdown(payload)
        # Con plan, "reduce" es la sesión del plan con menos volumen (29/10: salía "rodaje suave" junto a un tempo).
        assert "Elección recomendada hoy: la sesión del plan, con un 20-30 % menos" in out
        assert "Motor determinista" not in out
        assert "Motivo: estado neutral con una señal de riesgo" in out

    def test_compute_daily_plan_adjustment_overload_forces_rest(self):
        snapshot = {
            "dates": {"today": date.today().isoformat()},
            "load_fatigue": {
                "status": "overload",
                "latest": {"tsb": -40.0, "atl": 90.0},
                "weekly": {"current_tss": 700.0},
                "ranges": {"atl_high": 75.0},
            },
            "body_battery": {"today": {"date": date.today().isoformat(), "body_battery_level": 20}},
            "sleep": {"today": {"date": date.today().isoformat(), "sleep_hours": 5.2, "sleep_score": 50}},
            "hrv": {"today": {"date": date.today().isoformat(), "lastNightAvg": 35, "weeklyAvg": 50, "status": "low"}},
        }
        plan = {"title": "Plan 10K", "today_focus": "Series 6x800"}
        out = _compute_daily_plan_adjustment(snapshot, plan)
        assert out is not None
        assert out["decision"] == "rest"
        assert out["rule"] == "overload"

    def test_compute_daily_plan_adjustment_ready_can_maintain(self):
        snapshot = {
            "dates": {"today": date.today().isoformat()},
            "load_fatigue": {
                "status": "ready",
                "latest": {"tsb": 4.0, "atl": 55.0},
                "weekly": {"current_tss": 300.0, "high_tss": 450.0},
                "ranges": {"atl_high": 70.0},
            },
            "body_battery": {"today": {"date": date.today().isoformat(), "body_battery_level": 70}},
            "sleep": {"today": {"date": date.today().isoformat(), "sleep_hours": 7.8, "sleep_score": 82}},
            "hrv": {"today": {"date": date.today().isoformat(), "lastNightAvg": 52, "weeklyAvg": 50, "status": "balanced"}},
        }
        plan = {"title": "Plan 10K", "today_focus": "Tempo 40'"}
        out = _compute_daily_plan_adjustment(snapshot, plan)
        assert out is not None
        assert out["decision"] == "maintain"
        assert out["rule"] == "ready"

    def test_compute_daily_plan_adjustment_ready_with_weekly_spike_reduces(self):
        snapshot = {
            "dates": {"today": date.today().isoformat()},
            "load_fatigue": {
                "status": "ready",
                "latest": {"tsb": 4.5, "atl": 52.0},
                "weekly": {"current_tss": 310.0, "high_tss": 450.0},
                "ranges": {"atl_high": 70.0},
                "flags": {"weekly_spike_alert": True},
            },
            "body_battery": {"today": {"date": date.today().isoformat(), "body_battery_level": 72}},
            "sleep": {"today": {"date": date.today().isoformat(), "sleep_hours": 8.0, "sleep_score": 84}},
            "hrv": {"today": {"date": date.today().isoformat(), "lastNightAvg": 54, "weeklyAvg": 52, "status": "balanced"}},
        }
        plan = {"title": "Plan 10K", "today_focus": "Tempo 40'"}

        out = _compute_daily_plan_adjustment(snapshot, plan)

        assert out is not None
        assert out["rule"] == "ready"
        assert out["decision"] == "reduce"

    def test_compute_plan_execution_feedback_returns_adherence_and_deviation(self):
        yday = (date.today() - timedelta(days=1)).isoformat()
        plan = {
            "sessions": [
                {
                    "week_index": 1,
                    "day_index": date.fromisoformat(yday).isoweekday(),
                    "session_type": "running_quality",
                    "duration_min": 60,
                    "intensity": "RPE 7-8",
                    "structured_workout": {
                        "schema": "kairos-workout-v1",
                        "sessionType": "running_quality",
                        "steps": [
                            {"name": "Warm-up", "type": "warmup", "duration_min": 12, "reps": 1, "intensityClass": "endurance"},
                            {
                                "name": "Main Intervals",
                                "type": "interval_block",
                                "reps": 6,
                                "steps": [
                                    {"name": "Work", "type": "work", "duration_min": 3, "intensityClass": "threshold"},
                                    {"name": "Recovery", "type": "recovery", "duration_min": 2, "intensityClass": "recovery"},
                                ],
                            },
                            {"name": "Cool-down", "type": "cooldown", "duration_min": 10, "reps": 1, "intensityClass": "recovery"},
                        ],
                    },
                }
            ]
        }
        acts = [
            {
                "activityId": 101,
                "type": "running",
                "averagePace": "3:20",  # 3:20 min/km
                "trainingLoad": 90,
                "startTimeLocal": f"{yday}T08:00:00.0",
            }
        ]

        out = _compute_plan_execution_feedback(plan, acts, yday, profile={})
        assert out["adherence_score"] >= 0.75
        assert out["planned"]["duration_min"] == 60.0
        assert "load_deviation_pct" in out
        assert "bloque principal" in str(out["planned"].get("structured_summary") or "")
        block_feedback = out.get("block_feedback") or {}
        summary = block_feedback.get("summary") or {}
        assert int(summary.get("total_blocks") or 0) >= 1
        assert "time_deviation_pct" in summary

    def test_compute_daily_plan_adjustment_degrades_on_high_positive_deviation(self):
        snapshot = {
            "dates": {"today": date.today().isoformat()},
            "load_fatigue": {
                "status": "ready",
                "latest": {"tsb": 3.0, "atl": 50.0},
                "weekly": {"current_tss": 250.0, "high_tss": 450.0},
                "ranges": {"atl_high": 70.0},
            },
            "body_battery": {"today": {"date": date.today().isoformat(), "body_battery_level": 75}},
            "sleep": {"today": {"date": date.today().isoformat(), "sleep_hours": 8.0, "sleep_score": 85}},
            "hrv": {"today": {"date": date.today().isoformat(), "lastNightAvg": 50, "weeklyAvg": 50, "status": "balanced"}},
            "plan_execution_feedback": {
                "adherence_score": 0.9,
                "load_deviation_pct": 0.5,
            },
        }
        today_idx = date.today().isoweekday()
        week_start = date.today() - timedelta(days=today_idx - 1)
        plan = {
            "title": "Plan 10K",
            "today_focus": "Tempo 45'",
            "plan_data": {"start_date": week_start.isoformat()},
            "sessions": [
                {
                    "week_index": 1,
                    "day_index": today_idx,
                    "session_type": "running_quality",
                    "duration_min": 45,
                    "intensity": "RPE 7-8",
                    "structured_workout": {
                        "schema": "kairos-workout-v1",
                        "sessionType": "running_quality",
                        "steps": [
                            {"name": "Warm-up", "type": "warmup", "duration_min": 10, "reps": 1, "intensityClass": "endurance"},
                            {
                                "name": "Main Intervals",
                                "type": "interval_block",
                                "reps": 5,
                                "steps": [
                                    {"name": "Work", "type": "work", "duration_min": 3, "intensityClass": "threshold"},
                                    {"name": "Recovery", "type": "recovery", "duration_min": 2, "intensityClass": "recovery"},
                                ],
                            },
                            {"name": "Cool-down", "type": "cooldown", "duration_min": 10, "reps": 1, "intensityClass": "recovery"},
                        ],
                    },
                }
            ],
        }
        out = _compute_daily_plan_adjustment(snapshot, plan)
        assert out is not None
        assert out["decision"] == "reduce"
        assert out["adherence_adjustment"] == "down"
        assert "bloque principal" in str(out.get("resulting_session") or "").lower() or "repeticiones" in str(out.get("resulting_session") or "").lower()
        adjusted = out.get("adjusted_structured_workout") or {}
        trace = out.get("adjustment_trace") or []
        assert adjusted.get("schema") == "kairos-workout-v1"
        assert isinstance(trace, list) and trace

    def _ready_snapshot(self, feedback):
        today = date.today().isoformat()
        return {
            "dates": {"today": today},
            "load_fatigue": {"status": "ready", "latest": {"tsb": 19.0, "atl": 40.0},
                             "weekly": {"current_tss": 250.0, "high_tss": 450.0}, "ranges": {"atl_high": 70.0}},
            "body_battery": {"today": {"date": today, "body_battery_level": 75}},
            "sleep": {"today": {"date": today, "sleep_hours": 8.0, "sleep_score": 85}},
            "hrv": {"today": {"date": today, "lastNightAvg": 50, "weeklyAvg": 50, "status": "balanced"}},
            "plan_execution_feedback": feedback,
        }

    def test_skipping_yesterday_does_not_lower_today(self):
        # 29/10: "ready" con TSB +19 bajaba a "reducir" porque ayer no entrenó (adherencia 0.12, 0 actividades).
        missed = {"adherence_score": 0.12, "load_deviation_pct": -1.0, "executed": {"activities": 0}}
        out = _compute_daily_plan_adjustment(self._ready_snapshot(missed), {"sessions": []})
        assert out["decision"] == "maintain" and out["adherence_adjustment"] == "none"
        other = {"adherence_score": 0.2, "load_deviation_pct": 0.1, "executed": {"activities": 1}}
        out = _compute_daily_plan_adjustment(self._ready_snapshot(other), {"sessions": []})
        assert out["decision"] == "reduce" and "ayer hiciste una sesión distinta de la prevista" in out["reason"]

    def test_days_before_the_plan_existed_are_not_judged(self):
        yday = (date.today() - timedelta(days=1)).isoformat()
        plan = {"created_at": f"{date.today().isoformat()}T11:48:00+00:00", "plan_data": {},
                "sessions": [{"week_index": 1, "day_index": date.fromisoformat(yday).isoweekday(),
                              "session_type": "running_z2", "duration_min": 65}]}
        assert _compute_plan_execution_feedback(plan, [], yday, profile={}) is None
        plan["created_at"] = f"{yday}T07:00:00+00:00"
        assert _compute_plan_execution_feedback(plan, [], yday, profile={}) is not None

    def test_build_proactive_status_markdown_shows_block_feedback_summary(self):
        payload = {
            "plan_assigned": True,
            "plan_recommendation": "Plan activo",
            "plan_execution_feedback": {
                "adherence_score": 0.82,
                "adherence_label": "adherente",
                "load_deviation_pct": -0.12,
                "planned": {"structured_summary": "bloque principal: 5x3' + 2' rec (threshold)"},
                "block_feedback": {
                    "summary": {
                        "completed_blocks": 2,
                        "partial_blocks": 1,
                        "missed_blocks": 0,
                        "total_blocks": 3,
                        "time_deviation_pct": -0.10,
                    }
                },
            },
            "body_battery": {"summary": "sin datos"},
            "hrv": {"summary": "sin datos"},
            "sleep": {"summary": "sin datos"},
            "trainings": [],
        }
        out = _build_proactive_status_markdown(payload)
        assert "Bloques:" in out
        assert "2/3 completos" in out

    def test_compute_daily_plan_adjustment_respects_unavailable_day(self):
        today_iso = date.today().isoformat()
        today_idx = date.today().isoweekday()
        plan = {
            "title": "Plan general",
            "plan_data": {"constraints": {"unavailable_days": [today_idx]}},
            "sessions": [
                {
                    "week_index": 1,
                    "day_index": today_idx,
                    "session_type": "running_quality",
                    "duration_min": 60,
                    "intensity": "RPE 7-8",
                }
            ],
        }
        snapshot = {
            "dates": {"today": today_iso},
            "load_fatigue": {"status": "ready", "latest": {}, "weekly": {}, "ranges": {}},
            "body_battery": {"today": {"date": today_iso, "body_battery_level": 80}},
            "sleep": {"today": {"date": today_iso, "sleep_hours": 8.0, "sleep_score": 85}},
            "hrv": {"today": {"date": today_iso, "lastNightAvg": 55, "weeklyAvg": 50, "status": "balanced"}},
        }
        out = _compute_daily_plan_adjustment(snapshot, plan)
        assert out is not None
        assert out["decision"] == "rest"
        assert out["rule"] == "availability"

    @pytest.mark.asyncio
    async def test_collect_startup_snapshot_48h_collects_metrics(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )
        captured_calls: list[tuple[str, dict]] = []

        async def _fake_call_tool(_session, tool_name, _arguments):
            captured_calls.append((tool_name, dict(_arguments or {})))
            today = date.today().isoformat()
            if tool_name == "get_activities":
                return json.dumps({
                    "activities": [
                        {"activityId": 777, "name": "Rodaje", "startTimeLocal": f"{today}T07:30:00.0"}
                    ]
                })
            if tool_name == "get_activities_by_date":
                # Simula actividades históricas de 56 días para el modelo de carga
                from datetime import timedelta
                acts = []
                for i in range(40):
                    d = (date.today() - timedelta(days=i)).isoformat()
                    if i % 7 != 0:  # descanso 1 día/semana
                        acts.append({"activityId": 800 + i, "name": f"Trail {i}",
                                     "startTimeLocal": f"{d}T08:00:00.0",
                                     "trainingLoad": 55.0})
                return json.dumps({"activities": acts, "has_more": False})
            return json.dumps({"ok": True})

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()

        with patch_all("call_tool", side_effect=_fake_call_tool):
            snapshot = await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert snapshot["window_hours"] == 48
        assert snapshot["body_battery"]["summary"].startswith("hoy=")
        assert snapshot["trainings"]
        bb_calls = [args for name, args in captured_calls if name == "get_body_battery"]
        assert len(bb_calls) == 2
        assert all("start_date" in args and "end_date" in args for args in bb_calls)
        # get_activities_by_date debe haberse llamado con start_date y end_date
        hist_calls = [args for name, args in captured_calls if name == "get_activities_by_date"]
        assert hist_calls and all("start_date" in args and "end_date" in args for args in hist_calls)
        # Solo el rango corto de 48h para entrenamientos recientes: sin serie en load_metrics_daily no se
        # recalcula el TSS en vivo (27/10: antes pedía 56 días de actividades y daba un segundo TSS).
        assert all(args.get("page_size") == 100 for args in hist_calls)
        assert snapshot["load_fatigue"] == {}

    @pytest.mark.asyncio
    async def test_collect_startup_snapshot_refreshes_today_wellness_from_mcp(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )
        captured_calls: list[tuple[str, dict]] = []

        now_iso = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        today = date.today().isoformat()
        yday = (date.today() - timedelta(days=1)).isoformat()

        async def _fake_call_tool(_session, tool_name, _arguments):
            captured_calls.append((tool_name, dict(_arguments or {})))
            if tool_name == "get_body_battery":
                req_start = (_arguments or {}).get("start_date")
                if req_start == today:
                    return json.dumps([
                        {
                            "date": today,
                            "bodyBatteryLevel": 66,
                            "charged": 58,
                            "drained": 31,
                        }
                    ])
                return json.dumps([
                    {
                        "date": yday,
                        "bodyBatteryLevel": 61,
                        "charged": 61,
                        "drained": 58,
                    }
                ])
            if tool_name == "get_hrv_data":
                req_date = (_arguments or {}).get("date")
                if req_date == today:
                    return json.dumps({"date": today, "lastNightAvg": 42, "weeklyAvg": 48, "status": "balanced"})
                return json.dumps({"date": yday, "lastNightAvg": 74, "weeklyAvg": 73, "status": "balanced"})
            if tool_name == "get_activities_by_date":
                return json.dumps({
                    "activities": [
                        {"activityId": 901, "name": "Rodaje", "startTimeLocal": f"{today}T07:30:00.0", "trainingLoad": 50.0}
                    ],
                    "has_more": False,
                })
            if tool_name == "get_training_load_trend":
                return json.dumps({"dailyTrainingLoadAcute": []})
            return json.dumps({"ok": True})

        def _fake_get_wellness(metric_date: str):
            return {
                "metric_date": metric_date,
                "body_battery_level": 69,
                "bb_charged": 58,
                "bb_drained": 31,
                "hrv_last_night_avg_ms": 51,
                "hrv_weekly_avg_ms": 55,
                "hrv_status": "balanced",
                "pulled_at": now_iso,
            }

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()

        with patch_all("call_tool", side_effect=_fake_call_tool), \
             patch("agent.trainer_agent._storage.get_wellness_daily", side_effect=_fake_get_wellness), \
             patch("agent.trainer_agent._storage.upsert_wellness_daily") as upsert_mock:
            snapshot = await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert "nivel 66" in snapshot["body_battery"]["summary"]
        assert "42.0 ms" in snapshot["hrv"]["summary"]
        assert any(name == "get_body_battery" for name, _ in captured_calls)
        assert any(name == "get_hrv_data" for name, _ in captured_calls)
        assert snapshot.get("wellness_sources", {}).get("body_today") == "mcp"
        assert snapshot.get("wellness_sources", {}).get("hrv_today") == "mcp"
        upsert_mock.assert_called()

    @pytest.mark.asyncio
    async def test_collect_startup_snapshot_no_db_fallback_when_mcp_unavailable(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        async def _fake_call_tool(_session, tool_name, _arguments):
            if tool_name in {"get_body_battery", "get_hrv_data", "get_sleep_data"}:
                return json.dumps({"ok": True})
            if tool_name == "get_activities_by_date":
                return json.dumps({"activities": [], "has_more": False})
            if tool_name == "get_training_load_trend":
                return json.dumps({"dailyTrainingLoadAcute": []})
            return json.dumps({"ok": True})

        def _fake_get_wellness(_metric_date: str):
            return {
                "metric_date": date.today().isoformat(),
                "body_battery_level": 77,
                "bb_charged": 50,
                "bb_drained": 20,
                "hrv_last_night_avg_ms": 55,
                "hrv_weekly_avg_ms": 58,
                "hrv_status": "balanced",
            }

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()

        with patch_all("call_tool", side_effect=_fake_call_tool), \
             patch("agent.trainer_agent._storage.get_wellness_daily", side_effect=_fake_get_wellness):
            snapshot = await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert "nivel 77" not in snapshot["body_battery"]["summary"]
        assert "55.0 ms" not in snapshot["hrv"]["summary"]
        assert snapshot.get("wellness_sources", {}).get("body_today") == "mcp_unavailable"
        assert snapshot.get("wellness_sources", {}).get("hrv_today") == "mcp_unavailable"

        status_md = _build_proactive_status_markdown(snapshot)
        assert "No disponible en este momento" in status_md

    @pytest.mark.asyncio
    async def test_collect_startup_snapshot_fallback_when_by_date_unavailable(self):
        """Si get_activities_by_date lanza excepción, el snapshot sigue funcionando."""
        from agent.trainer_agent import (
            TrainerAgent,
        )

        async def _fake_call_tool(_session, tool_name, _arguments):
            today = date.today().isoformat()
            if tool_name == "get_activities":
                return json.dumps({
                    "activities": [
                        {"activityId": 1, "name": "Run", "startTimeLocal": f"{today}T08:00:00.0",
                         "trainingLoad": 50.0}
                    ]
                })
            if tool_name == "get_activities_by_date":
                raise RuntimeError("tool not available")
            return json.dumps({"ok": True})

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()

        with patch_all("call_tool", side_effect=_fake_call_tool):
            snapshot = await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert snapshot["window_hours"] == 48
        assert "load_fatigue" in snapshot

    @pytest.mark.asyncio
    async def test_collect_startup_snapshot_preserves_formula_version_in_profile_cache(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        async def _fake_call_tool(_session, tool_name, _arguments):
            today = date.today().isoformat()
            if tool_name == "get_activities":
                return json.dumps({
                    "activities": [
                        {"activityId": 1, "name": "Run", "startTimeLocal": f"{today}T08:00:00.0", "trainingLoad": 50.0}
                    ]
                })
            if tool_name == "get_activities_by_date":
                return json.dumps({
                    "activities": [
                        {"activityId": 2, "name": "Run", "startTimeLocal": f"{today}T09:00:00.0", "trainingLoad": 65.0}
                    ],
                    "has_more": False,
                })
            return json.dumps({"ok": True})

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {"primary": "running"},
            "load_metrics": {"formula_version": _data_access._TSS_FORMULA_VERSION},
        }

        with patch_all("call_tool", side_effect=_fake_call_tool), patch_all(
            "_save_user_profile"
        ):
            await TrainerAgent.collect_startup_snapshot_48h(agent)

        assert (
            (agent.user_profile.get("load_metrics") or {}).get("formula_version")
            == _data_access._TSS_FORMULA_VERSION
        )

    @pytest.mark.asyncio
    async def test_build_startup_status_markdown_uses_training_plan_not_goals(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {
            "goals": {
                "target_race": "10K",
                "target_race_date": "2026-11-22",
            }
        }

        with patch.object(TrainerAgent, "collect_startup_snapshot_48h", return_value={"body_battery": {}, "hrv": {}, "sleep": {}, "trainings": []}):
            out = await TrainerAgent.build_startup_status_markdown(agent)

        assert "No tienes plan asignado" in out

    @pytest.mark.asyncio
    async def test_with_a_plan_today_session_comes_first_and_the_coach_writes_within_the_decision(self):
        # 29/10, petición del usuario: primero lo que toca hoy; la decisión es del motor y el LLM la explica.
        from agent.trainer_agent import TrainerAgent

        agent = object.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {}
        agent.client = MagicMock()
        plan = {"title": "Plan", "sessions": []}
        decision = {"decision": "maintain", "reason": "disponibilidad alta y recuperación estable"}
        coach = AsyncMock(side_effect=lambda q, md, **kw: md + "\n\n## 🧠 Interpretación de coaching\nHaz el tempo tranquilo."
                          "\n\n_Fase coaching por LLM sobre datos deterministas (sin recalcular métricas)._")
        snapshot = {"body_battery": {}, "hrv": {}, "sleep": {}, "trainings": []}
        with patch.object(TrainerAgent, "collect_startup_snapshot_48h", return_value=snapshot), \
             patch_all("_get_active_training_plan", return_value=plan), \
             patch_all("_compute_daily_plan_adjustment", return_value=decision), \
             patch_all("_today_session_lines", return_value=["- **Jueves 29/10/2026 — Tempo 25'**"]), \
             patch.object(TrainerAgent, "_augment_with_llm_coaching_if_needed", coach):
            out = await TrainerAgent.build_startup_status_markdown(agent)
        assert out.index("## 🗓️ Sesión de hoy") < out.index("## ✅ Recomendación") < out.index("### 🧠 Tu entrenador")
        # Con plan no hay "Próximo paso" (29/10): el texto del entrenador cierra y el pie dice qué es de cada uno.
        assert "Próximo paso" not in out and out.rstrip().endswith("«Tu entrenador» es el LLM sobre esos datos._")
        assert out.index("Haz el tempo tranquilo.") < out.index("_Fuente:")
        assert "la sesión del plan, tal cual" in out and "Fase coaching" not in out
        assert coach.await_args.kwargs["daily_plan_decision"] == decision
        # Si el LLM no responde, queda la recomendación determinista, sin la nota de error.
        coach.side_effect = lambda q, md, **kw: md + "\n\n## 🧠 Interpretación de coaching\n- No pude generar..."
        with patch.object(TrainerAgent, "collect_startup_snapshot_48h", return_value=snapshot), \
             patch_all("_get_active_training_plan", return_value=plan), \
             patch_all("_compute_daily_plan_adjustment", return_value=decision), \
             patch_all("_today_session_lines", return_value=["- **Jueves 29/10/2026 — Tempo 25'**"]), \
             patch.object(TrainerAgent, "_augment_with_llm_coaching_if_needed", coach):
            out = await TrainerAgent.build_startup_status_markdown(agent)
        assert "Tu entrenador" not in out and "No pude generar" not in out and "## 🗓️ Sesión de hoy" in out


class TestStartupInParallel:
    """Arranque (8c, 28/10): el resumen inicial va a la vez que la carga y el perfil de Garmin se pide una vez."""

    @pytest.mark.asyncio
    async def test_snapshot_reads_the_load_series_only_after_the_load_is_computed(self, monkeypatch):
        agent = ta.TrainerAgent.__new__(ta.TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.user_profile = {}
        order = []

        async def garmin(_session, name, _args):
            order.append(name)
            return "{}"

        async def load():
            await asyncio.sleep(0.05)
            order.append("carga guardada")

        def series(*_a, **_k):
            order.append("serie leída")
            return []

        patch_everywhere(monkeypatch, "call_tool", garmin)
        patch_everywhere(monkeypatch, "_load_metrics_series", series)
        monkeypatch.setattr(ta._storage, "upsert_wellness_daily", lambda *a, **k: None)
        load_task = asyncio.ensure_future(load())
        await agent.collect_startup_snapshot_48h(load_ready=load_task)

        assert order.index("carga guardada") < order.index("serie leída")
        assert order.index("carga guardada") > 0  # Garmin no esperó a la carga

    @pytest.mark.asyncio
    async def test_personal_data_reuses_the_profile_already_read(self, monkeypatch):
        agent = ta.TrainerAgent.__new__(ta.TrainerAgent)
        agent.mcp_session = MagicMock()
        garmin = AsyncMock(return_value="{}")
        patch_everywhere(monkeypatch, "call_tool", garmin)
        raw = json.dumps({"userData": {"gender": "MALE", "weight": 67000.0, "height": 178}})

        out = await agent.fetch_garmin_personal_data(profile_raw=raw)

        assert out == {"gender": "hombre", "height_cm": 178, "weight_kg": 67.0}
        garmin.assert_not_called()
