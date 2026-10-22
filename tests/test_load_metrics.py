"""Tests de agent/load_metrics.py: TSS por actividad y deporte, PMC (CTL/ATL/TSB), zonas de Forma,
duración de actividad, modelo metabólico de caminar/senderismo y etiquetado del modelo de TSS."""


import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from agent.trainer_agent import (
    _build_proactive_status_markdown,
    _build_activity_analysis_block,
    _build_load_trend_table,
    _classify_running_session_with_confidence,
    _classify_strength_session_with_confidence,
    _classify_walk_hike_session_with_confidence,
    _estimate_session_tss,
    _compute_load_fatigue_metrics,
    _resolve_sport_model_cfg,
    _infer_tss_model_tag,
    _infer_tss_source_tag,
)


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from agent.load_metrics import (
    _extract_duration_seconds_from_activity_details_payload,
    _resolve_activity_duration_hours,
)
from agent.load_metrics import estimate_session_tss
import agent.load_metrics as lm


@pytest.fixture
def pmc_tss_from_fixture(monkeypatch):
    """Tests del PMC (CTL/ATL/TSB, rangos, avisos): el TSS de cada actividad sintética es su campo
    trainingLoad. Aquí no se prueba el cálculo de TSS; en producción la carga de Garmin nunca es TSS."""
    import agent.load_metrics as lm

    monkeypatch.setattr(lm, "_estimate_session_tss", lambda act, **_kw: (float(act.get("trainingLoad") or 0.0), "TSS"))


class TestSessionTssEstimation:

    def test_compute_load_fatigue_metrics_returns_latest_and_ranges(self, pmc_tss_from_fixture):
        today = date.today()
        activities = []
        for idx in range(21):
            d = (today - timedelta(days=idx)).isoformat()
            activities.append(
                {
                    "startTimeLocal": f"{d}T07:00:00.0",
                    "trainingLoad": 40 + (idx % 4) * 10,
                }
            )

        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={"load": []},
            profile={},
            days_window=42,
        )

        assert out is not None
        assert out["latest"]["date"] == today.isoformat()
        assert "atl" in out["latest"] and "ctl" in out["latest"] and "tsb" in out["latest"]
        assert "tsb_low" in out["ranges"] and "tsb_high" in out["ranges"]
        assert out["status"] in {"overload", "fatigue_high", "ready", "neutral"}

    def test_compute_load_fatigue_metrics_detects_overload(self, pmc_tss_from_fixture):
        today = date.today()
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=idx)).isoformat()}T07:00:00.0", "trainingLoad": 130}
            for idx in range(14)
        ]

        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile={},
            days_window=28,
        )

        assert out is not None
        assert out["flags"]["sustained_overload"] or out["status"] in {"overload", "fatigue_high"}

    def test_compute_load_fatigue_metrics_ignores_garmin_training_load_trend(self):
        today = date.today()
        trend = [{"date": (today - timedelta(days=idx)).isoformat(), "trainingLoad": 130} for idx in range(14)]

        out = _compute_load_fatigue_metrics(activities=[], trend_payload={"points": trend}, profile={}, days_window=28)

        assert out is None

    def test_compute_load_fatigue_metrics_tsb_uses_previous_day_like_tp(self, pmc_tss_from_fixture):
        today = date.today()
        activities = [{"startTimeLocal": f"{today.isoformat()}T07:00:00.0", "trainingLoad": 100}]

        out = _compute_load_fatigue_metrics(activities=activities, trend_payload={}, profile={}, days_window=28)

        # Hoy entra TSS 100: ATL y CTL suben, pero la Forma usa los valores de ayer (0).
        assert out["latest"]["atl"] > 0
        assert out["latest"]["tsb"] == 0.0

    def test_compute_load_fatigue_metrics_flags_weekly_spike_over_20_percent(self, pmc_tss_from_fixture):
        import agent.trainer_agent as ta

        class _FakeDate:
            @staticmethod
            def today():
                from datetime import date as _Date

                # Domingo fijo para garantizar semana completa (lunes->domingo)
                return _Date(2026, 8, 16)

            @staticmethod
            def fromisoformat(value):
                from datetime import date as _Date

                return _Date.fromisoformat(value)

        with patch.object(ta, "date", _FakeDate):
            today = _FakeDate.today()
            activities = []
            for idx in range(14):
                d = (today - timedelta(days=idx)).isoformat()
                # Semana actual (0..6): 35 TSS/día; semana anterior (7..13): 20 TSS/día
                load = 35 if idx <= 6 else 20
                activities.append(
                    {
                        "startTimeLocal": f"{d}T07:00:00.0",
                        "trainingLoad": load,
                    }
                )

            out = _compute_load_fatigue_metrics(
                activities=activities,
                trend_payload={"load": []},
                profile={},
                days_window=28,
            )

        assert out is not None
        assert out["flags"]["weekly_spike_alert"] is True
        assert float(out["weekly"]["previous_tss"]) > 0.0
        assert float(out["weekly"]["spike_delta_pct"]) > 20.0
        if out["status"] in {"ready", "neutral"}:
            assert "Spike semanal >20%" in str(out.get("recommendation") or "")

    def test_resolve_sport_model_cfg_uses_tp_pmc_constants_for_every_sport(self):
        for sport in ("running", "trail running", "ciclismo", "triatlón", "patinaje"):
            cfg = _resolve_sport_model_cfg({"goals": {"primary": sport}})
            assert (cfg["ctl_tau_days"], cfg["atl_tau_days"]) == (42, 7), sport

    def test_resolve_sport_model_cfg_unknown_sport_falls_back_to_running(self):
        profile = {"goals": {"primary": "patinaje"}}
        cfg = _resolve_sport_model_cfg(profile)
        running_cfg = _resolve_sport_model_cfg({"goals": {"primary": "running"}})
        assert cfg["atl_tau_days"] == running_cfg["atl_tau_days"]

    def test_resolve_sport_model_cfg_ignores_taus_saved_in_profile_model(self):
        # load_metrics.model lo reescribe Kairos; no debe fijar constantes antiguas (p. ej. ATL 8).
        profile = {
            "goals": {"primary": "trail running"},
            "load_metrics": {"model": {"atl_tau_days": 8, "ctl_tau_days": 45, "tsb_low_pct": 0.3}},
        }
        cfg = _resolve_sport_model_cfg(profile)
        assert (cfg["ctl_tau_days"], cfg["atl_tau_days"]) == (42, 7)
        assert cfg["tsb_low_pct"] == 0.3

    def test_compute_load_fatigue_metrics_trail_uses_sport_tau(self, pmc_tss_from_fixture):
        today = date.today()
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=i)).isoformat()}T08:00:00.0", "trainingLoad": 60}
            for i in range(20)
        ]
        profile = {"goals": {"primary": "trail running"}}
        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=profile,
            days_window=28,
        )
        assert out is not None
        assert out["model"]["sport"] == "trail running"
        assert out["model"]["atl_tau_days"] == 7

    def test_compute_load_fatigue_metrics_ciclismo_uses_sport_tau(self, pmc_tss_from_fixture):
        today = date.today()
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=i)).isoformat()}T08:00:00.0", "trainingLoad": 80}
            for i in range(20)
        ]
        profile = {"goals": {"primary": "ciclismo"}}
        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=profile,
            days_window=28,
        )
        assert out is not None
        assert out["model"]["sport"] == "ciclismo"
        assert out["model"]["ctl_tau_days"] == 42

    def test_form_zone_boundaries_relative_to_ctl(self):
        # Zonas de Friel en % del CTL (como intervals.icu): >20 / 5..20 / -10..5 / -30..-10 / <-30.
        from agent.load_metrics import form_zone
        assert form_zone(27.0, 59.2)["key"] == "transition"   # +46 %
        assert form_zone(12.0, 60.0)["key"] == "fresh"        # +20 % → aún Fresco (límite)
        assert form_zone(3.0, 60.0)["key"] == "grey"          # +5 % exacto → gris (Fresco es > +5 %)
        assert form_zone(-6.0, 60.0)["key"] == "grey"         # −10 % → gris (límite incluido)
        assert form_zone(-12.2, 73.7)["key"] == "optimal"     # −17 %
        assert form_zone(-18.0, 60.0)["key"] == "optimal"     # −30 % → óptimo (límite incluido)
        assert form_zone(-18.1, 60.0)["key"] == "high_risk"
        assert form_zone(5.0, 0.0)["form_pct"] == 0.0         # sin CTL no hay referencia

    def test_load_status_follows_form_zone_not_personal_percentiles(self):
        from agent.load_metrics import resolve_load_status
        def _series(points):
            return [{"date": f"2026-10-{i + 1:02d}", "tss": 50.0, "ctl": ctl, "atl": ctl - tsb, "tsb": tsb}
                    for i, (tsb, ctl) in enumerate(points)]
        # Bloque normal (−21 %): Óptimo → el motor mantiene, no "sobrecarga".
        out = resolve_load_status(_series([(5.0, 70.0)] * 6 + [(-14.7, 70.1)]))
        assert out["zone"]["key"] == "optimal" and out["status"] == "neutral"
        # Riesgo alto (< −30 %) → descarga.
        out = resolve_load_status(_series([(-25.0, 70.0)]))
        assert out["zone"]["key"] == "high_risk" and out["status"] == "overload"
        # 7 días seguidos en zona de carga (< −10 %) → suave / programar descarga.
        out = resolve_load_status(_series([(-12.0, 70.0)] * 7))
        assert out["status"] == "fatigue_high" and out["week_in_load"] is True
        # Transición tras descargar (+46 %) → disponible, avisa de pérdida de forma.
        out = resolve_load_status(_series([(27.0, 59.2)]))
        assert out["zone"]["key"] == "transition" and out["status"] == "ready"
        assert "perder forma" in out["recommendation"]

    def test_sustained_overload_boundary_le_fix(self, pmc_tss_from_fixture):
        """Cuando tsb_now == tsb_low (valor == percentil p15), debe detectarse como overload.
        Antes del fix se usaba < estricto y el caso límite se escapaba."""
        today = date.today()
        # Serie con carga alta los últimos 7 días para que p15 == valor actual (TSB muy negativo)
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=i)).isoformat()}T08:00:00.0",
             "trainingLoad": 110 if i < 14 else 55}
            for i in range(42)
        ]
        profile = {"goals": {"primary": "trail running"}}
        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=profile,
            days_window=56,
        )
        assert out is not None
        # Con carga de 110 durante 14 días consecutivos, el status no debe ser "neutral" ni "ready"
        assert out["status"] in {"overload", "fatigue_high"}

    def test_warming_up_flag_true_with_few_training_days(self, pmc_tss_from_fixture):
        """Con < 21 días de entrenamiento real, warming_up debe ser True."""
        today = date.today()
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=i)).isoformat()}T08:00:00.0",
             "trainingLoad": 50}
            for i in range(10)  # solo 10 días
        ]
        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile={"goals": {"primary": "trail running"}},
            days_window=28,
        )
        assert out is not None
        assert out["warming_up"] is True
        assert out["flags"]["warming_up"] is True
        assert out["warming_up_days_remaining"] > 0

    def test_warming_up_flag_false_with_enough_history(self, pmc_tss_from_fixture):
        """Con >= 21 días de entrenamiento real, warming_up debe ser False."""
        today = date.today()
        activities = [
            {"startTimeLocal": f"{(today - timedelta(days=i)).isoformat()}T08:00:00.0",
             "trainingLoad": 50}
            for i in range(25)  # 25 días
        ]
        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile={"goals": {"primary": "running"}},
            days_window=42,
        )
        assert out is not None
        assert out["warming_up"] is False
        assert out["warming_up_days_remaining"] == 0

    def test_proactive_markdown_shows_calibration_notice_when_warming_up(self):
        """El estado proactivo debe incluir nota ⚙️ cuando warming_up=True."""
        load_fatigue = {
            "latest": {"tss": 50.0, "atl": 30.0, "ctl": 10.0, "tsb": -20.0},
            "weekly": {"current_tss": 300.0},
            "ranges": {"tsb_low": -22.0, "tsb_high": -5.0, "atl_high": 40.0, "tsb_abs_floor": -35.0},
            "recommendation": "Mantén carga aeróbica controlada.",
            "action": "carga estable",
            "warming_up": True,
            "warming_up_days_remaining": 11,
            "days_with_load": 10,
        }
        snapshot = {
            "body_battery": {"summary": "sin datos"},
            "hrv": {"summary": "sin datos"},
            "sleep": {"summary": "sin datos"},
            "load_fatigue": load_fatigue,
            "trainings": [],
        }
        out = _build_proactive_status_markdown(snapshot)
        assert "calibracion" in out.lower() or "calibración" in out.lower()
        assert "⚙️" in out

    def test_proactive_markdown_no_calibration_notice_when_not_warming_up(self):
        """Sin warming_up, la nota de calibración no debe aparecer."""
        load_fatigue = {
            "latest": {"tss": 50.0, "atl": 42.0, "ctl": 38.0, "tsb": -4.0},
            "weekly": {"current_tss": 310.0},
            "ranges": {"tsb_low": -12.0, "tsb_high": 4.0, "atl_high": 55.0, "tsb_abs_floor": -35.0},
            "recommendation": "Puedes mantener sesión de calidad.",
            "action": "buena disponibilidad",
            "warming_up": False,
            "warming_up_days_remaining": 0,
            "days_with_load": 30,
        }
        snapshot = {
            "body_battery": {"summary": "sin datos"},
            "hrv": {"summary": "sin datos"},
            "sleep": {"summary": "sin datos"},
            "load_fatigue": load_fatigue,
            "trainings": [],
        }
        out = _build_proactive_status_markdown(snapshot)
        assert "⚙️" not in out

    def test_load_trend_table_shows_warmup_note_when_ctl_low(self):
        """La tabla /carga debe mostrar nota cuando CTL de primera fila < 15."""
        today = date.today()
        series = []
        atl = ctl = 0.0
        for i in range(28, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            tss = 50.0 if i % 7 != 0 else 0.0
            atl = atl + (tss - atl) / 8
            ctl = ctl + (tss - ctl) / 42
            series.append({"date": d, "tss": round(tss, 1), "atl": round(atl, 1),
                           "ctl": round(ctl, 1), "tsb": round(ctl - atl, 1)})
        out = _build_load_trend_table(series, mode="weeks")
        # CTL empieza < 15 → debe aparecer la nota
        assert "⚙️" in out


class TestSportSpecificLoadProfiles:
    @staticmethod
    def _build_fake_activities(days: int, default_tss: float, high_tss_days: int = 0, high_tss: float = 0.0) -> list[dict]:
        """Genera actividades ficticias con trainingLoad diario para pruebas del modelo."""
        today = date.today()
        out: list[dict] = []
        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            tss = high_tss if i < high_tss_days else default_tss
            out.append(
                {
                    "activityId": 900000 + i,
                    "startTimeLocal": f"{d}T08:00:00.0",
                    "trainingLoad": float(tss),
                }
            )
        return out

    def test_running_profile_matches_tp_like_defaults(self, pmc_tss_from_fixture):
        profile = {"goals": {"primary": "running"}}
        activities = self._build_fake_activities(days=56, default_tss=55.0)

        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=profile,
            days_window=56,
        )

        assert out is not None
        assert out["model"]["sport"] == "running"
        assert out["model"]["atl_tau_days"] == 7
        assert out["model"]["ctl_tau_days"] == 42
        assert "tsb_abs_floor" not in out["ranges"]  # sin suelos fijos: decide la zona de Forma
        assert out["latest"]["atl"] > 0 and out["latest"]["ctl"] > 0

    def test_trail_profile_uses_tp_taus_and_deeper_tsb_floor(self, pmc_tss_from_fixture):
        activities = self._build_fake_activities(days=56, default_tss=55.0)
        running_profile = {"goals": {"primary": "running"}}
        trail_profile = {"goals": {"primary": "trail running"}}

        run_out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=running_profile,
            days_window=56,
        )
        trail_out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=trail_profile,
            days_window=56,
        )

        assert run_out is not None and trail_out is not None
        assert trail_out["model"]["sport"] == "trail running"
        assert trail_out["model"]["atl_tau_days"] == 7
        assert trail_out["model"]["ctl_tau_days"] == 42
        assert "tsb_abs_floor" not in trail_out["ranges"]
        # Mismas constantes (PMC de TP): misma carga, mismo ATL.
        assert float(trail_out["latest"]["atl"]) == float(run_out["latest"]["atl"])

    def test_triathlon_profile_uses_longer_ctl_and_flags_recent_overload(self, pmc_tss_from_fixture):
        # Bloque agudo reciente para simular sobrecarga en un atleta de triatlón.
        activities = self._build_fake_activities(
            days=56,
            default_tss=45.0,
            high_tss_days=10,
            high_tss=140.0,
        )
        tri_profile = {"goals": {"primary": "triatlón"}}

        out = _compute_load_fatigue_metrics(
            activities=activities,
            trend_payload={},
            profile=tri_profile,
            days_window=56,
        )

        assert out is not None
        assert out["model"]["sport"] == "triatlón"
        assert out["model"]["atl_tau_days"] == 7
        assert out["model"]["ctl_tau_days"] == 42
        assert "tsb_abs_floor" not in out["ranges"]
        assert out["status"] in {"fatigue_high", "overload"}


class TestLoadFatigueModel:
    @staticmethod
    def _trail_hr_details(hr: float, seconds: int = 3600, step: int = 10) -> str:
        return json.dumps([{"timeInSeconds": t, "heartRate": hr} for t in range(0, seconds, step)])

    def _slow_trail(self) -> dict:
        # 10:00/km: trail normal (no rápido).
        return {"type": "trail_running", "duration": 3600, "distance": 6000, "averageSpeed": 1.667}

    def test_estimate_tss_never_uses_garmin_training_load(self):
        """La carga de Garmin nunca es TSS: con FC y duración, Kairos estima igual que sin ella."""
        with_load, label = _estimate_session_tss({"trainingLoad": 95.0, "averageHR": 155, "duration": 3600})
        without_load, _ = _estimate_session_tss({"averageHR": 155, "duration": 3600})
        assert with_load == pytest.approx(without_load)
        assert with_load != pytest.approx(95.0)
        assert label == "hrTSS"

    def test_estimate_tss_without_duration_is_zero_not_garmin_load(self):
        act = {"trainingLoad": 999.0, "trainingStressScore": 999.0}
        tss, _ = _estimate_session_tss(act)
        assert tss == 0.0
        assert _infer_tss_model_tag(act) == "generic:no_duration"

    def test_estimate_tss_priority2_hr_based_z2(self):
        """Sin trainingLoad, Z2 (avg_hr ~140, hr_rest 50, hr_max 185) → TSS razonable."""
        act = {"averageHR": 140, "maxHR": 185, "duration": 3600}
        tss, _ = _estimate_session_tss(act)
        # Z2 1h → esperamos entre 50 y 80 TSS
        assert 50 <= tss <= 80, f"TSS Z2 1h inesperado: {tss}"

    def test_estimate_tss_priority2_hr_based_z4_higher_than_z2(self):
        """Z4 debe producir más TSS que Z2 para la misma duración."""
        z2, _ = _estimate_session_tss({"averageHR": 140, "maxHR": 185, "duration": 3600})
        z4, _ = _estimate_session_tss({"averageHR": 165, "maxHR": 185, "duration": 3600})
        assert z4 > z2

    def test_estimate_tss_priority2_uses_max_hr_from_activity(self):
        """max_hr de la actividad se usa como referencia; si no, se asume 185."""
        with_max, _ = _estimate_session_tss({"averageHR": 150, "maxHR": 175, "duration": 3600})
        without_max, _ = _estimate_session_tss({"averageHR": 150, "duration": 3600})
        # Con max_hr real del reloj (175) vs estimado (185): la %HRR difiere → TSS difiere
        assert with_max != without_max

    def test_estimate_tss_priority2_uses_profile_hr_when_activity_has_no_max(self):
        act = {"averageHR": 150, "duration": 3600}
        default_tss, _ = _estimate_session_tss(act)
        profile_tss, _ = _estimate_session_tss(act, hr_rest_bpm=60, hr_max_bpm=190)
        assert profile_tss != default_tss
        assert profile_tss < default_tss

    def test_estimate_tss_priority3_training_effect_fallback(self):
        """Sin HR ni trainingLoad, usa Training Effect aeróbico."""
        act = {"aerobicTrainingEffect": 3.0, "duration": 3600}
        tss, _ = _estimate_session_tss(act)
        # TE 3.0 → IF ~0.77 → 1h → ~59 TSS
        assert 45 <= tss <= 75, f"TSS con TE=3.0 inesperado: {tss}"

    def test_estimate_tss_priority4_default_if_no_data(self):
        """Sin ningún dato de intensidad, usa IF=0.68 → 1h → ~46 TSS."""
        act = {"duration": 3600}
        tss, _ = _estimate_session_tss(act)
        expected = 0.68 ** 2 * 100
        assert abs(tss - expected) < 1.0

    def test_estimate_tss_rowing_uses_gym_cardio_route_over_hr_zones(self):
        act = {"type": "rowing", "duration": 3600, "averageHR": 150, "maxHR": 185}
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 110,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
                {
                    "zoneNumber": 4,
                    "secsInZone": 0,
                    "minHeartRateIn": 152,
                    "maxHeartRateIn": 165,
                },
                {
                    "zoneNumber": 5,
                    "secsInZone": 0,
                    "minHeartRateIn": 166,
                    "maxHeartRateIn": 185,
                },
            ]
        )

        tss, label = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw, hr_rest_bpm=50, hr_max_bpm=185)

        assert label == "hrTSS"
        # Dedicated gym-cardio fallback (avg HR + profile anchors) should override zones in rowing.
        assert 76.0 <= tss <= 80.0

    def test_estimate_tss_strength_ignores_native_and_uses_calibrated_hr_formula(self):
        act = {
            "type": "strength_training",
            "duration": 3600,
            "trainingLoad": 33.35,
            "averageHR": 145,
            "maxHR": 180,
            "rpe": 9,
            "name": "Fuerza maxima 5x5",
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                }
            ]
        )

        tss, label = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        assert label == "hrTSS"
        assert abs(tss - 61.1) < 0.3

    def test_estimate_tss_strength_without_hr_returns_zero(self):
        act = {"type": "strength_training", "duration": 3600}
        tss, label = _estimate_session_tss(act)

        assert label == "hrTSS"
        assert tss == 0.0

    def test_estimate_tss_strength_uses_hr_only_even_with_rpe_and_text(self):
        act = {
            "type": "strength_training",
            "duration": 3600,
            "averageHR": 145,
            "maxHR": 180,
            "rpe": "7/10",
            "name": "Movilidad suave",
        }
        hr_zones_raw = json.dumps([
            {
                "zoneNumber": 1,
                "secsInZone": 60,
                "minHeartRateIn": 115,
                "maxHeartRateIn": 125,
            }
        ])

        tss, label = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        assert label == "hrTSS"
        assert abs(tss - 61.1) < 0.3

    def test_estimate_tss_strength_native_label_kept_even_without_duration(self):
        act = {
            "type": "strength_training",
            "trainingStressScore": 21.7,
        }

        tss, label = _estimate_session_tss(act)

        assert label == "hrTSS"
        assert tss == 0.0

    def test_estimate_tss_strength_light_session_keyword(self):
        act = {
            "type": "strength_training",
            "duration": 3600,
            "name": "Movilidad y tonificación ligera",
        }

        tss, label = _estimate_session_tss(act)

        assert label == "hrTSS"
        assert tss == 0.0

    def test_estimate_tss_strength_rpe_and_text_do_not_change_single_fallback(self):
        act = {
            "type": "strength_training",
            "duration": 3600,
            "rpe": 8,
            "name": "Movilidad suave de descarga",
        }

        tss, label = _estimate_session_tss(act)

        assert label == "hrTSS"
        assert tss == 0.0

    def test_estimate_tss_strength_supports_summary_dto_payload_shape(self):
        act = {
            "activityTypeDTO": {"typeKey": "strength_training"},
            "summaryDTO": {
                "duration": 2751.608,
                "averageHR": 79.0,
                "maxHR": 122.0,
            },
            "activityName": "Gimnasio. Trail - Estabilidad y core",
        }

        tss, label = _estimate_session_tss(act)

        assert label == "hrTSS"
        assert abs(tss - 26.23) < 0.5

    def test_estimate_tss_strength_prefers_lthr_model_when_threshold_available(self):
        act = {
            "activityTypeDTO": {"typeKey": "strength_training"},
            "summaryDTO": {
                "duration": 2751.608,
                "averageHR": 79.0,
                "maxHR": 122.0,
            },
            "activityName": "Gimnasio. Trail - Estabilidad y core",
            "activityId": 24301501111,
        }

        tss_lthr, label_lthr = _estimate_session_tss(
            act,
            hr_rest_bpm=40.714285714285715,
            hr_threshold_bpm=169.0,
        )
        tss_fallback, _ = _estimate_session_tss(
            act,
            hr_rest_bpm=40.714285714285715,
            hr_threshold_bpm=None,
        )

        assert label_lthr == "hrTSS"
        # LTHR branch should match calibrated target (~24.53 for this known sample).
        assert abs(tss_lthr - 24.53) < 0.6
        # Ensure the production route truly switched from the old HR-reserve fallback.
        assert abs(tss_lthr - tss_fallback) > 0.8

    def test_estimate_tss_strength_falls_back_to_hr_reserve_when_lthr_missing(self):
        act = {
            "activityTypeDTO": {"typeKey": "strength_training"},
            "summaryDTO": {
                "duration": 2751.608,
                "averageHR": 79.0,
                "maxHR": 122.0,
            },
            "activityName": "Gimnasio. Trail - Estabilidad y core",
        }

        tss_fallback, label = _estimate_session_tss(
            act,
            hr_rest_bpm=40.714285714285715,
            hr_threshold_bpm=None,
            hr_max_bpm=185.0,
        )

        assert label == "hrTSS"
        # Fallback branch (HR-reserve A) remains stable and non-zero.
        assert abs(tss_fallback - 28.08) < 0.6

    def test_strength_classification_labeled_sample_structured_first(self):
        labeled = [
            ({"rpe": 8, "name": "Movilidad suave"}, "heavy"),
            ({"rpe": 3, "name": "Fuerza maxima"}, "light"),
            ({"rpe": 5, "name": "Heavy day"}, "maintenance"),
            ({"name": "Fuerza maxima 5x5"}, "heavy"),
            ({"name": "Movilidad y tonificación"}, "light"),
            ({"name": "Gimnasio general"}, "general"),
        ]

        # Mini-set orientativo: usar como smoke test de regresión, no como validación cerrada.
        assert len(labeled) == 6

        ok = 0
        for payload, expected in labeled:
            act = {"type": "strength_training", **payload}
            cls = _classify_strength_session_with_confidence(act)
            if cls["session_kind"] == expected:
                ok += 1

        accuracy = ok / len(labeled)
        assert accuracy >= 1.0

    def test_strength_tss_invariant_to_text_when_rpe_present(self):
        base = {
            "type": "strength_training",
            "duration": 3600,
            "rpe": 8,
        }
        act_a = dict(base)
        act_a["name"] = "Movilidad suave"
        act_b = dict(base)
        act_b["name"] = "Fuerza maxima 1RM"

        tss_a, _ = _estimate_session_tss(act_a)
        tss_b, _ = _estimate_session_tss(act_b)

        assert abs(tss_a - tss_b) < 1e-6

    def test_walk_hike_tss_invariant_to_text_when_structured_signals_present(self):
        base = {
            "type": "walking",
            "duration": 3600,
            "distance": 6200,  # 6.2 km/h -> brisk por señal estructurada
            "elevationGain": 0,
        }
        act_a = dict(base)
        act_a["name"] = "Paseo suave"
        act_b = dict(base)
        act_b["name"] = "Mochila montaña power walking"

        tss_a, _ = _estimate_session_tss(act_a)
        tss_b, _ = _estimate_session_tss(act_b)

        assert abs(tss_a - tss_b) < 1e-6

    def test_walk_hike_classification_prefers_structured_over_text(self):
        act = {
            "type": "walking",
            "duration": 3600,
            "distance": 6200,
            "elevationGain": 0,
            "name": "Mochila montaña",
        }
        cls = _classify_walk_hike_session_with_confidence(act, hours=1.0)
        assert cls["band"] == "brisk"
        assert cls["source"] == "structured"
        assert cls["confidence"] == "high"

    def test_walk_hike_text_fallback_when_no_structured_signals(self):
        act = {
            "type": "walking",
            "duration": 3600,
            "name": "Power walking en montaña con mochila",
        }

        cls = _classify_walk_hike_session_with_confidence(act, hours=1.0)
        tss, label = _estimate_session_tss(act)

        assert cls["source"] == "text"
        assert cls["band"] in {"brisk", "heavy"}
        assert label == "TSS"
        assert tss is not None
        assert tss > 0

    def test_strength_classification_reports_signal_source(self):
        cls_rpe = _classify_strength_session_with_confidence(
            {"type": "strength_training", "rpe": 8, "name": "Movilidad suave"}
        )
        assert cls_rpe["session_kind"] == "heavy"
        assert cls_rpe["source"] == "rpe"
        assert cls_rpe["confidence"] == "high"

        cls_text = _classify_strength_session_with_confidence(
            {"type": "strength_training", "name": "Fuerza maxima 5x5"}
        )
        assert cls_text["session_kind"] == "heavy"
        assert cls_text["source"] == "text"
        assert cls_text["confidence"] == "low"

    def test_estimate_tss_running_prefers_threshold_pace_over_hr(self):
        act = {
            "type": "running",
            "duration": 3600,
            "distance": 10000,
            "averageHR": 160,
            "maxHR": 185,
        }
        tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)
        # TP-like canónico aplica leve ajuste de sesión sobre el baseline por ritmo.
        assert label == "TSS"
        assert abs(tss - 76.1) < 1.0

    def test_estimate_tss_running_prefers_effective_pace_when_available(self):
        act = {
            "type": "running",
            "duration": 3600,
            "distance": 10000,
            "averagePace": "6:00",
            "normalizedPace": "5:00",
        }
        tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)
        # IF=1.0 y calibración TP-like para running continuo.
        assert label == "TSS"
        assert abs(tss - 101.0) <= 1.0

    def test_estimate_tss_running_uses_distance_meters_for_pace_fallback(self):
        act = {
            "type": "running",
            "duration_seconds": 3600,
            "distance_meters": 10000,
        }
        tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)
        assert label == "TSS"
        # 10k en 1h => 6:00/km. Umbral 5:00/km => IF=0.833... => ~69.4 TSS
        assert abs(tss - 69.4) < 1.0

    def test_estimate_tss_running_uses_higher_if_ceiling_than_trail(self):
        running_act = {
            "type": "running",
            "duration": 3600,
            "averagePace": "3:20",  # 3:20 min/km
        }
        trail_act = {
            "type": "hiking",
            "duration": 3600,
            "averagePace": "3:20",  # 3:20 min/km
        }

        running_tss, running_label = _estimate_session_tss(
            running_act,
            running_threshold_pace_sec_per_km=300.0,
        )
        trail_tss, trail_label = _estimate_session_tss(
            trail_act,
            running_threshold_pace_sec_per_km=300.0,
        )


        def test_estimate_tss_running_interval_exam_always_on_increases_tss(self):
            act = {
                "type": "running",
                "duration_seconds": 5278.944,
                "distance_meters": 16168.79,
                "avg_speed_mps": 3.063,
                "max_speed_mps": 3.882,
                "lap_count": 20,
                "has_splits": True,
                "vigorous_intensity_minutes": 85,
                "workout_rpe": 80,
                "training_effect_label": "LACTATE_THRESHOLD",
                "name": "Fartlek cuestas",
                "description": "6 x 4' Z4 + recuperacion",
            }

            tss_examined, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)

            # Baseline (sin ajuste intervalico): IF=300/(5278.944/(16.16879)) ~= 0.919
            # El examen intervalico debe elevar la carga por encima de ese baseline.
            baseline_if = 300.0 / (5278.944 / 16.16879)
            baseline_tss = (5278.944 / 3600.0) * (baseline_if ** 2) * 100.0

            assert label == "TSS"
            assert tss_examined > baseline_tss + 1.0

        def test_estimate_tss_running_rodaje_not_artificially_inflated(self):
            act = {
                "type": "running",
                "duration": 3600,
                "distance": 10000,
                "avg_speed_mps": 3.03,
                "max_speed_mps": 3.33,
                "lap_count": 17,
                "has_splits": True,
                "workout_rpe": 30,
                "training_effect_label": "AEROBIC_BASE",
                "name": "Rodaje suave",
            }

            tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)
            assert label == "TSS"
            # 10k en 1h => 6:00/km. Umbral 5:00/km => ~69.4 TSS (sin inflado intervalico)
            assert abs(tss - 69.4) < 1.0
        # Running no trail: clamp IF a 1.30 + ganancia de sesión TP-like.
        assert running_label == "TSS"
        assert abs(running_tss - 170.7) < 1.0

        # Hike/walk usa bandas específicas (no clamp de running/trail).
        assert trail_label == "TSS"
        assert abs(trail_tss - 50.41) < 1.0

    def test_estimate_tss_running_fartlek_not_overinflated_vs_pace_baseline(self):
        act = {
            "type": "running",
            "duration_seconds": 4385.93,
            "distance_meters": 15115.87,
            "avg_speed_mps": 3.446,
            "max_speed_mps": 4.647,
            "lap_count": 27,
            "vigorous_intensity_minutes": 70,
            "workout_rpe": 80,
            "training_effect_label": "VO2MAX",
            "name": "Fartlek. 2x5' + 2x4' +4x2'",
        }

        tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)

        baseline_if = 300.0 / (4385.93 / 15.11587)
        baseline_tss = (4385.93 / 3600.0) * (baseline_if ** 2) * 100.0

        assert label == "TSS"
        # Debe mantenerse controlado frente al baseline por ritmo.
        assert tss >= baseline_tss
        assert tss <= baseline_tss + 22.0

    def test_estimate_tss_running_series_keeps_interval_uplift(self):
        act = {
            "type": "running",
            "duration_seconds": 3475.483,
            "distance_meters": 10776.09,
            "avg_speed_mps": 3.101,
            "max_speed_mps": 4.843,
            "lap_count": 38,
            "vigorous_intensity_minutes": 49,
            "workout_rpe": 60,
            "training_effect_label": "VO2MAX",
            "name": "Series en el C.A.R. 8x500 REC 1'30",
        }

        tss, label = _estimate_session_tss(act, running_threshold_pace_sec_per_km=300.0)

        baseline_if = 300.0 / (3475.483 / 10.77609)
        baseline_tss = (3475.483 / 3600.0) * (baseline_if ** 2) * 100.0

        assert label == "TSS"
        # Mantener uplift en series para no perder el ajuste del caso 8x500.
        assert tss > baseline_tss + 7.0

    def test_classify_running_session_rodaje(self):
        act = {
            "type": "running",
            "avg_speed_mps": 3.10,
            "max_speed_mps": 3.35,
            "lap_count": 10,
            "vigorous_intensity_minutes": 12,
            "workout_rpe": 30,
            "training_effect_label": "AEROBIC_BASE",
            "name": "Rodaje suave zona 2",
        }
        cls = _classify_running_session_with_confidence(act)
        assert cls["session_kind"] == "rodaje"
        assert cls["confidence"] in {"medium", "high"}

    def test_classify_running_session_fartlek(self):
        act = {
            "type": "running",
            "avg_speed_mps": 3.40,
            "max_speed_mps": 4.55,
            "lap_count": 22,
            "vigorous_intensity_minutes": 55,
            "workout_rpe": 75,
            "training_effect_label": "VO2MAX",
            "name": "Fartlek 6x3' rec 2'",
        }
        cls = _classify_running_session_with_confidence(act)
        assert cls["session_kind"] == "fartlek"
        assert cls["scores"]["fartlek"] >= cls["scores"]["series"]

    def test_classify_running_session_series(self):
        act = {
            "type": "running",
            "avg_speed_mps": 3.05,
            "max_speed_mps": 4.80,
            "lap_count": 34,
            "vigorous_intensity_minutes": 48,
            "workout_rpe": 65,
            "training_effect_label": "VO2MAX",
            "name": "Series 10x400 rec 1'",
            "description": "Trabajo de reps en pista",
        }
        cls = _classify_running_session_with_confidence(act)
        assert cls["session_kind"] == "series"
        assert cls["scores"]["series"] > cls["scores"]["rodaje"]

    def test_classify_running_session_rodaje_from_name_only(self):
        act = {
            "type": "running",
            "name": "Rodaje suave Z2 60'",
            "training_effect_label": "AEROBIC_BASE",
        }
        cls = _classify_running_session_with_confidence(act)
        assert cls["session_kind"] == "rodaje"

    def test_estimate_tss_trail_uses_pace_only_with_native_effective_signal(self):
        act_hr = {
            "type": "trail_running",
            "duration": 7200,
            "distance": 14000,
            "averageHR": 150,
            "maxHR": 180,
            "rpe": 9,
        }
        tss_hr, label_hr = _estimate_session_tss(act_hr, running_threshold_pace_sec_per_km=320.0)
        assert label_hr == "hrTSS"
        assert tss_hr > 0

        act_native_pace = {
            "type": "trail_running",
            "duration": 7200,
            "distance": 14000,
            "gradeAdjustedPace": "6:00",
            "averageHR": 150,
            "maxHR": 180,
        }
        tss_native, label_native = _estimate_session_tss(
            act_native_pace,
            running_threshold_pace_sec_per_km=320.0,
        )
        assert label_native == "TSS"
        assert tss_native > 0

        act_pace = {"type": "hiking", "duration": 7200, "distance": 14000}
        tss_pace, label_pace = _estimate_session_tss(act_pace, running_threshold_pace_sec_per_km=320.0)
        assert label_pace == "TSS"
        assert tss_pace > 0

        act_hike_hr = {
            "type": "hiking",
            "duration": 7200,
            "distance": 14000,
            "averageHR": 130,
            "maxHR": 165,
        }
        tss_hike_hr, label_hike_hr = _estimate_session_tss(act_hike_hr, running_threshold_pace_sec_per_km=320.0)
        assert label_hike_hr == "TSS"
        assert tss_hike_hr > 0

        act_rpe = {"type": "walking", "duration": 3600, "rpe": 6}
        tss_rpe, label_rpe = _estimate_session_tss(act_rpe)
        assert label_rpe == "TSS"
        assert tss_rpe > 0

    def test_estimate_tss_trail_uses_hr_zones_when_available(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
            ]
        )

        tss_zones, label_zones = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)
        tss_avg, label_avg = _estimate_session_tss(act)

        assert label_zones == "hrTSS"
        assert label_avg == "hrTSS"
        assert tss_zones > 0
        assert tss_zones < tss_avg

    def test_estimate_tss_trail_hr_zones_uses_tp_like_zone_mapping(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
            ]
        )

        tss_zones, label_zones = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        assert label_zones == "hrTSS"
        # 1h íntegra en Z1 (115-125) con mapeo TP-like por zonas de FC.
        assert abs(tss_zones - 27.04) < 0.05

        hike_act = {
            "type": "hiking",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
        }
        tss_hike_zones, label_hike_zones = _estimate_session_tss(hike_act, hr_zones_raw=hr_zones_raw)
        assert label_hike_zones == "TSS"
        # En hike/walk se usa banda específica y blend con zonas (sin factor trail).
        assert 45.0 <= tss_hike_zones <= 60.0

    def test_estimate_tss_trail_fast_pace_keeps_tp_like_zone_mapping(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
            "averagePaceSecPerKm": 330,
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
            ]
        )

        tss_zones, label_zones = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        assert label_zones == "hrTSS"
        # El ritmo no cambia el fallback zonal TP-like cuando no hay NGP nativo.
        assert abs(tss_zones - 27.04) < 0.05

    def test_estimate_tss_trail_pace_at_6min_keeps_tp_like_zone_mapping(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
            "averagePaceSecPerKm": 360,
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 115,
                    "maxHeartRateIn": 125,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 126,
                    "maxHeartRateIn": 138,
                },
                {
                    "zoneNumber": 3,
                    "secsInZone": 0,
                    "minHeartRateIn": 139,
                    "maxHeartRateIn": 151,
                },
            ]
        )

        tss_zones, label_zones = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        assert label_zones == "hrTSS"
        # Sin NGP nativo, el ritmo no altera el fallback zonal TP-like.
        assert abs(tss_zones - 27.04) < 0.05

    def test_estimate_tss_trail_low_zone_floor_keeps_raw_hr_zones(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
            "averagePaceSecPerKm": 480,
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 1,
                    "secsInZone": 3600,
                    "minHeartRateIn": 46,
                    "maxHeartRateIn": 141,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 142,
                    "maxHeartRateIn": 152,
                },
            ]
        )

        tss_zones, label_zones = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        act_fast = dict(act)
        act_fast["averagePaceSecPerKm"] = 330
        tss_raw_fast, label_raw_fast = _estimate_session_tss(act_fast, hr_zones_raw=hr_zones_raw)

        assert label_zones == "hrTSS"
        assert label_raw_fast == "hrTSS"
        # El umbral de zonas ya define el resultado: no hay calibración adicional.
        assert abs(tss_zones - tss_raw_fast) < 0.05

    def test_activity_analysis_block_shows_stored_tss_only(self):
        activity_raw = json.dumps(
            {
                "type": "trail_running",
                "duration": 3600,
                "distance": 10000,
                "averageHR": 170,
                "maxHR": 180,
                "name": "Trail test",
            }
        )

        out = _build_activity_analysis_block(
            activity_raw=activity_raw,
            stored_tss_row={"activity_id": 1, "tss": 123.4, "method": "hrTSS"},
        )

        assert "hrTSS (Kairos): 123.4" in out
        assert "hrTSS bruto zonas" not in out
        assert "hrTSS Kairos aplicado" not in out

    def test_activity_analysis_block_without_stored_tss_is_pending_not_estimated(self):
        activity_raw = json.dumps(
            {
                "type": "trail_running",
                "duration": 3600,
                "distance": 10000,
                "averageHR": 170,
                "maxHR": 180,
                "activityTrainingLoad": 250.0,
                "name": "Trail sin línea base",
            }
        )

        with patch("agent.trainer_agent._estimate_session_tss") as estimate_mock:
            out = _build_activity_analysis_block(activity_raw=activity_raw)

        estimate_mock.assert_not_called()
        assert "TSS (Kairos): pendiente de cálculo" in out
        assert "Carga Garmin (no TSS): 250.0" in out
        assert "Carga ALTA" not in out

    def test_estimate_tss_walking_easy_uses_dedicated_model(self):
        act = {
            "type": "walking",
            "duration": 3600,
            "name": "Paseo regenerativo suave",
        }
        hr_zones_raw = json.dumps(
            [
                {
                    "zoneNumber": 3,
                    "secsInZone": 3600,
                    "minHeartRateIn": 145,
                    "maxHeartRateIn": 155,
                },
                {
                    "zoneNumber": 2,
                    "secsInZone": 0,
                    "minHeartRateIn": 130,
                    "maxHeartRateIn": 144,
                },
                {
                    "zoneNumber": 1,
                    "secsInZone": 0,
                    "minHeartRateIn": 110,
                    "maxHeartRateIn": 129,
                },
            ]
        )

        tss, label = _estimate_session_tss(act, hr_zones_raw=hr_zones_raw)

        assert label == "TSS"
        assert 15.0 <= tss <= 25.0

    def test_estimate_tss_walking_power_band(self):
        act = {
            "type": "walking",
            "duration": 3600,
            "name": "Power walking ritmo vivo",
        }

        tss, label = _estimate_session_tss(act)

        assert label == "TSS"
        assert 25.0 <= tss <= 40.0

    def test_estimate_tss_elliptical_fallback_uses_profile_hr_max(self):
        act_elliptical = {
            "duration": 1890,
            "averageHR": 113,
            "maxHR": 124,
            "name": "Eliptica. Z1 suave",
        }
        act_generic = {
            "duration": 1890,
            "averageHR": 113,
            "maxHR": 124,
            "name": "Indoor suave",
        }

        tss_elliptical, label_elliptical = _estimate_session_tss(
            act_elliptical,
            hr_rest_bpm=50,
            hr_max_bpm=185,
        )
        tss_generic, _ = _estimate_session_tss(
            act_generic,
            hr_rest_bpm=50,
            hr_max_bpm=185,
        )

        assert label_elliptical == "hrTSS"
        assert 20.0 <= tss_elliptical <= 32.0
        assert tss_generic > tss_elliptical

    def test_estimate_tss_rowing_lthr_sets_gym_cardio_source_tag(self):
        act = {
            "type": "rowing",
            "duration": 3600,
            "averageHR": 145,
            "maxHR": 172,
        }

        tss, label = _estimate_session_tss(
            act,
            hr_rest_bpm=50,
            hr_max_bpm=190,
            hr_threshold_bpm=169,
        )
        model = _infer_tss_model_tag(act)

        assert label == "hrTSS"
        assert 60.0 <= tss <= 66.0
        assert model == "gym_cardio:lthr"

    def test_estimate_tss_rowing_fallback_sets_gym_cardio_source_tag(self):
        act = {
            "type": "rowing",
            "duration": 3600,
            "averageHR": 145,
        }

        tss, label = _estimate_session_tss(
            act,
            hr_rest_bpm=50,
            hr_max_bpm=190,
            hr_threshold_bpm=None,
        )
        model = _infer_tss_model_tag(act)

        assert label == "hrTSS"
        assert 68.0 <= tss <= 74.0
        assert model == "gym_cardio:fallback_hr"

    def test_estimate_tss_rowing_lthr_with_power_blends_mechanical_component(self):
        act_base = {
            "type": "rowing",
            "duration": 3600,
            "averageHR": 145,
        }
        act_power = {
            "type": "rowing",
            "duration": 3600,
            "averageHR": 145,
            "avgPower": 260,
        }

        tss_base, label_base = _estimate_session_tss(
            act_base,
            hr_rest_bpm=50,
            hr_max_bpm=190,
            hr_threshold_bpm=169,
        )
        tss_power, label_power = _estimate_session_tss(
            act_power,
            hr_rest_bpm=50,
            hr_max_bpm=190,
            hr_threshold_bpm=169,
        )

        model_base = _infer_tss_model_tag(act_base)
        model_power = _infer_tss_model_tag(act_power)

        assert label_base == "hrTSS"
        assert label_power == "hrTSS"
        assert model_base == "gym_cardio:lthr"
        assert model_power == "gym_cardio:lthr_plus_mech"
        assert tss_power > tss_base

    def test_estimate_tss_elliptical_lthr_with_cadence_blends_mechanical_component(self):
        act_base = {
            "type": "elliptical",
            "duration": 1800,
            "averageHR": 120,
        }
        act_cad = {
            "type": "elliptical",
            "duration": 1800,
            "averageHR": 120,
            "avgCadence": 78,
        }

        tss_base, label_base = _estimate_session_tss(
            act_base,
            hr_rest_bpm=50,
            hr_max_bpm=190,
            hr_threshold_bpm=169,
        )
        tss_cad, label_cad = _estimate_session_tss(
            act_cad,
            hr_rest_bpm=50,
            hr_max_bpm=190,
            hr_threshold_bpm=169,
        )

        model_base = _infer_tss_model_tag(act_base)
        model_cad = _infer_tss_model_tag(act_cad)

        assert label_base == "hrTSS"
        assert label_cad == "hrTSS"
        assert model_base == "gym_cardio:lthr"
        assert model_cad == "gym_cardio:lthr_plus_mech"
        assert tss_cad > tss_base

    def test_estimate_tss_elliptical_name_overrides_running_type_for_routing(self):
        act = {
            "type": "running",
            "name": "Eliptica. 60'",
            "duration": 3600,
            "averageHR": 136,
            "maxHR": 150,
        }

        tss, label = _estimate_session_tss(
            act,
            hr_rest_bpm=50,
            hr_max_bpm=190,
            hr_threshold_bpm=169,
        )
        model = _infer_tss_model_tag(act)

        assert label == "hrTSS"
        assert model.startswith("gym_cardio:")
        assert tss > 0.0

    def test_estimate_tss_elliptical_accented_name_overrides_running_type_for_routing(self):
        act = {
            "type": "running",
            "name": "Elíptica. 60'",
            "duration": 3600,
            "averageHR": 136,
            "maxHR": 150,
        }

        tss, label = _estimate_session_tss(
            act,
            hr_rest_bpm=50,
            hr_max_bpm=190,
            hr_threshold_bpm=169,
        )
        model = _infer_tss_model_tag(act)

        assert label == "hrTSS"
        assert model.startswith("gym_cardio:")
        assert tss > 0.0

    def test_estimate_tss_words_inside_description_do_not_route_to_gym_cardio(self):
        # Casos reales: 23966766696 ("remontar"), 22248446621 ("podremos"), 22915678572 ("volveremos").
        cases = [
            ("running", 'Fartlek. 8x45" cuestas', "Me he tenido que comer un gel para remontar."),
            ("running", "Fartlek. 4 x 4 km RCM REC 3'", "algo podremos hacer en la maratón"),
            ("trail_running", "Trail. Ruta del Cobre", "Volveremos con más energía y en elíptica no."),
        ]
        for act_type, name, description in cases:
            act = {
                "type": act_type,
                "name": name,
                "description": description,
                "duration": 3600,
                "averageHR": 150,
                "maxHR": 170,
            }
            _estimate_session_tss(act, hr_rest_bpm=50, hr_max_bpm=190, hr_threshold_bpm=169)
            assert not _infer_tss_model_tag(act).startswith("gym_cardio:"), name

    def test_trail_tp_zones_rate_by_hr_band_vs_tp_threshold(self):
        # TSS/h por tramo de FC/umbral TP (166): <85 % = 40.8, 85-95 % = 72.7, >= 100 % = 100.
        for hr, expected in ((130, 40.8), (150, 72.7), (170, 100.0)):
            act = self._slow_trail()
            tss, label = _estimate_session_tss(
                act,
                hr_rest_bpm=40,
                hr_threshold_bpm=169,
                hr_threshold_tp_bpm=166,
                activity_details_raw=self._trail_hr_details(hr),
            )
            assert label == "hrTSS"
            assert _infer_tss_model_tag(act) == "trail:tp_zones"
            assert tss == pytest.approx(expected, abs=0.3), hr

    def test_trail_tp_zones_ignores_resting_hr(self):
        values = []
        for hr_rest in (37, 50):
            tss, _ = _estimate_session_tss(
                self._slow_trail(),
                hr_rest_bpm=hr_rest,
                hr_threshold_bpm=169,
                hr_threshold_tp_bpm=166,
                activity_details_raw=self._trail_hr_details(135),
            )
            values.append(tss)
        assert values[0] == values[1]

    def test_trail_tp_zones_uses_tp_threshold_and_falls_back_to_lthr(self):
        # 143 lpm: 86 % de 166 (tramo 72.7) pero 84.6 % de 169 (tramo 40.8).
        details = self._trail_hr_details(143)
        tss_tp, _ = _estimate_session_tss(
            self._slow_trail(), hr_threshold_bpm=169, hr_threshold_tp_bpm=166, activity_details_raw=details
        )
        tss_lthr, _ = _estimate_session_tss(self._slow_trail(), hr_threshold_bpm=169, activity_details_raw=details)
        assert tss_tp == pytest.approx(72.7, abs=0.3)
        assert tss_lthr == pytest.approx(40.8, abs=0.3)

    def test_fast_trail_uses_running_rtss_pipeline(self):
        act = {"type": "trail_running", "duration": 3600, "distance": 11000, "averageSpeed": 3.06}
        with patch("agent.load_metrics._estimate_running_tss_from_activity_details_pipeline", return_value=88.0) as pipe:
            tss, label = _estimate_session_tss(
                act,
                hr_threshold_bpm=169,
                hr_threshold_tp_bpm=166,
                running_threshold_pace_sec_per_km=252,
                activity_details_raw=self._trail_hr_details(150),
            )
        assert pipe.called
        assert (tss, label) == (88.0, "TSS")
        assert _infer_tss_model_tag(act) == "trail:fast_rtss"

    def test_cycling_power_tss_uses_garmin_norm_power(self):
        # Caso real 28/07: avgPower 125, normPower 165, FTP 205, 1.58 h -> TP 101 TSS.
        act = {"type": "road_biking", "duration": 1.58 * 3600, "avgPower": 125.0, "normPower": 165.0}
        tss, label = _estimate_session_tss(act, ftp=205.0)
        assert label == "TSS"
        assert _infer_tss_model_tag(act) == "cycling:power_ftp"
        assert tss == pytest.approx(1.58 * (165 / 205) ** 2 * 100, abs=0.1)

    def test_cycling_without_power_uses_tp_hr_zones(self):
        act = {"type": "gravel_cycling", "duration": 3600, "averageHR": 120}
        tss, label = _estimate_session_tss(
            act, ftp=205.0, hr_threshold_bpm=169, hr_threshold_tp_bpm=166,
            activity_details_raw=self._trail_hr_details(120),
        )
        assert (label, _infer_tss_model_tag(act)) == ("hrTSS", "cycling:tp_zones")
        assert tss == pytest.approx(40.8, abs=0.3)

    def test_walk_tss_is_capped_by_tp_hrtss(self):
        # Caminata rápida (6.8 km/h, FC 100): el modelo metabólico no puede superar el hrTSS de TP.
        act = {"type": "hiking", "duration": 3600, "distance": 6800, "averageSpeed": 1.89, "averageHR": 100}
        with patch("agent.load_metrics._estimate_walk_hike_tss", return_value=(99.0, "TSS")):
            tss, label = _estimate_session_tss(
                act, hr_threshold_bpm=169, hr_threshold_tp_bpm=166,
                activity_details_raw=self._trail_hr_details(100),
            )
        assert (label, _infer_tss_model_tag(act)) == ("hrTSS", "walk_hike:tp_hr_cap")
        assert tss == pytest.approx(40.8, abs=0.3)

    def test_walk_tss_below_tp_hrtss_keeps_metabolic_model(self):
        act = {"type": "walking", "duration": 3600, "distance": 2500, "averageSpeed": 0.7, "averageHR": 60}
        with patch("agent.load_metrics._estimate_walk_hike_tss", return_value=(15.0, "TSS")):
            tss, _ = _estimate_session_tss(
                act, hr_threshold_bpm=169, hr_threshold_tp_bpm=166,
                activity_details_raw=self._trail_hr_details(60),
            )
        assert _infer_tss_model_tag(act) == "walk_hike:metabolico"
        assert tss == 15.0

    def test_estimate_tss_rowing_word_in_name_routes_to_gym_cardio(self):
        act = {
            "type": "running",
            "name": "Remo. 20' Z2",
            "duration": 1200,
            "averageHR": 130,
            "maxHR": 140,
        }
        _estimate_session_tss(act, hr_rest_bpm=50, hr_max_bpm=190, hr_threshold_bpm=169)
        assert _infer_tss_model_tag(act).startswith("gym_cardio:")

    def test_estimate_tss_trail_uses_embedded_hr_zones_payload(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
            "heartRateZones": [
                {"zoneNumber": 1, "secsInZone": 3600, "minHeartRateIn": 115, "maxHeartRateIn": 125},
                {"zoneNumber": 2, "secsInZone": 0, "minHeartRateIn": 126, "maxHeartRateIn": 138},
                {"zoneNumber": 3, "secsInZone": 0, "minHeartRateIn": 139, "maxHeartRateIn": 151},
            ],
        }

        tss_embedded, label_embedded = _estimate_session_tss(act)
        tss_avg_only, _ = _estimate_session_tss({
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 170,
            "maxHR": 180,
        })

        assert label_embedded == "hrTSS"
        assert tss_embedded > 0
        assert tss_embedded < tss_avg_only

    def test_estimate_tss_trail_prefers_stream_karvonen_when_coverage_is_good(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 160,
            "maxHR": 190,
        }
        hr_zones_raw = json.dumps(
            [
                {"zoneNumber": 1, "secsInZone": 3600, "minHeartRateIn": 115, "maxHeartRateIn": 125},
                {"zoneNumber": 2, "secsInZone": 0, "minHeartRateIn": 126, "maxHeartRateIn": 138},
            ]
        )
        details_raw = json.dumps(
            {
                "samples": [
                    {"seconds": idx * 30, "heartRate": 160}
                    for idx in range(120)
                ]
            }
        )

        tss_stream, label_stream = _estimate_session_tss(
            act,
            hr_zones_raw=hr_zones_raw,
            activity_details_raw=details_raw,
            hr_rest_bpm=50,
            hr_max_bpm=190,
        )
        tss_zones, _ = _estimate_session_tss(
            act,
            hr_zones_raw=hr_zones_raw,
            hr_rest_bpm=50,
            hr_max_bpm=190,
        )

        assert label_stream == "hrTSS"
        assert tss_stream < tss_zones
        assert abs(tss_stream - 61.73) < 0.2

    def test_estimate_tss_trail_stream_low_coverage_falls_back_to_zones(self):
        act = {
            "type": "trail_running",
            "duration": 3600,
            "averageHR": 160,
            "maxHR": 190,
        }
        hr_zones_raw = json.dumps(
            [
                {"zoneNumber": 1, "secsInZone": 3600, "minHeartRateIn": 115, "maxHeartRateIn": 125},
                {"zoneNumber": 2, "secsInZone": 0, "minHeartRateIn": 126, "maxHeartRateIn": 138},
            ]
        )
        details_raw = json.dumps(
            {
                "samples": [
                    {"seconds": 0, "heartRate": 160},
                    {"seconds": 10, "heartRate": 161},
                    {"seconds": 20, "heartRate": 159},
                ]
            }
        )

        tss_stream_low_cov, label_low_cov = _estimate_session_tss(
            act,
            hr_zones_raw=hr_zones_raw,
            activity_details_raw=details_raw,
            hr_rest_bpm=50,
            hr_max_bpm=190,
        )
        tss_zones, _ = _estimate_session_tss(
            act,
            hr_zones_raw=hr_zones_raw,
            hr_rest_bpm=50,
            hr_max_bpm=190,
        )

        assert label_low_cov == "hrTSS"
        assert abs(tss_stream_low_cov - tss_zones) < 0.05

    def test_estimate_tss_cycling_prefers_power_ftp_then_hr_zones_then_hr(self):
        act_pow = {
            "type": "cycling",
            "duration": 3600,
            "normalizedPower": 210,
            "averageHR": 160,
            "maxHR": 185,
        }
        tss_pow, label_pow = _estimate_session_tss(act_pow, ftp=250.0)
        assert label_pow == "TSS"
        assert abs(tss_pow - 70.6) < 1.0

        hr_zones_raw = json.dumps(
            [
                {"zoneNumber": 1, "secsInZone": 3600, "minHeartRateIn": 115, "maxHeartRateIn": 125},
                {"zoneNumber": 2, "secsInZone": 0, "minHeartRateIn": 126, "maxHeartRateIn": 138},
                {"zoneNumber": 3, "secsInZone": 0, "minHeartRateIn": 139, "maxHeartRateIn": 151},
            ]
        )

        # Sin FTP (aunque haya potencia), usa zonas FC.
        tss_no_ftp, label_no_ftp = _estimate_session_tss(act_pow, ftp=None, hr_zones_raw=hr_zones_raw)
        tss_no_ftp_hr_only, _ = _estimate_session_tss(act_pow, ftp=None)
        assert label_no_ftp == "hrTSS"
        assert tss_no_ftp > 0
        assert abs(tss_no_ftp - tss_no_ftp_hr_only) > 0.5

        # Sin potencia (aunque haya FTP), usa zonas FC.
        act_no_power = {"type": "cycling", "duration": 3600, "averageHR": 145, "maxHR": 175}
        tss_zones, label_zones = _estimate_session_tss(act_no_power, ftp=250.0, hr_zones_raw=hr_zones_raw)
        tss_no_zones_hr_only, _ = _estimate_session_tss(act_no_power, ftp=250.0)
        assert label_zones == "hrTSS"
        assert tss_zones > 0
        assert abs(tss_zones - tss_no_zones_hr_only) > 0.5

        # Sin zonas, fallback final a FC media.
        act_hr = {"type": "cycling", "duration": 3600, "averageHR": 145, "maxHR": 175}
        tss_hr, label_hr = _estimate_session_tss(act_hr, ftp=250.0)
        assert label_hr == "hrTSS"
        assert tss_hr > 0

    def test_estimate_tss_zero_duration_returns_zero(self):
        act = {"averageHR": 145, "duration": 0}
        tss, _ = _estimate_session_tss(act)
        assert tss == 0.0

    def test_estimate_tss_invalid_activity_returns_zero(self):
        tss1, _ = _estimate_session_tss(None)
        tss2, _ = _estimate_session_tss("not a dict")
        assert tss1 == 0.0
        assert tss2 == 0.0


class TestTssSourceTagging:


    def test_cycling_power_ftp_is_kairos_model(self):
        act = {"type": "gravel_cycling", "normalizedPower": 180, "duration": 3600, "activityTrainingLoad": 140.0}
        tss, label = _estimate_session_tss(act, ftp=260.0)
        assert label == "TSS"
        assert tss > 0.0
        assert _infer_tss_source_tag(act, label, ftp=260.0, hr_zones_raw=None) == "kairos_model"
        assert _infer_tss_model_tag(act) == "cycling:power_ftp"

    def test_cycling_hr_avg_without_ftp_is_kairos_model(self):
        act = {"type": "mountain_biking", "averageHR": 150, "duration": 3600}
        _tss, label = _estimate_session_tss(act, hr_rest_bpm=50, hr_max_bpm=190)
        assert _infer_tss_source_tag(act, label, ftp=None, hr_zones_raw=None) == "kairos_model"
        assert _infer_tss_model_tag(act) == "cycling:hr_avg"

    @pytest.mark.parametrize(
        ("act", "expected_model"),
        [
            (
                {
                    "type": "running",
                    "duration": 3600,
                    "distance": 12000.0,
                    "averageHR": 150,
                    "maxHR": 170,
                    "activityTrainingLoad": 250.0,
                },
                "running:tp_like_adjusted",
            ),
            (
                {
                    "type": "walking",
                    "duration": 3600,
                    "distance": 5500.0,
                    "averageHR": 105,
                    "activityTrainingLoad": 40.0,
                },
                "walk_hike:metabolico",
            ),
            (
                {
                    "type": "hiking",
                    "duration": 3 * 3600,
                    "distance": 12000.0,
                    "elevationGain": 900.0,
                    "averageHR": 125,
                    "activityTrainingLoad": 180.0,
                },
                "walk_hike:metabolico",
            ),
        ],
        ids=["running", "walking", "hiking"],
    )
    def test_native_load_present_but_kairos_computes_is_not_garmin_native(self, act, expected_model):
        tss, label = _estimate_session_tss(
            act,
            hr_rest_bpm=37.0,
            hr_max_bpm=190.0,
            hr_threshold_bpm=169.0,
            running_threshold_pace_sec_per_km=252.0,
        )
        assert tss > 0.0
        assert _infer_tss_source_tag(act, label, ftp=None, hr_zones_raw=None) == "kairos_model"
        assert _infer_tss_model_tag(act) == expected_model
        assert tss != pytest.approx(act["activityTrainingLoad"])

    def test_garmin_load_is_never_the_final_tss(self):
        # Antes: sin duración (o sin modelo para el deporte) se devolvía la carga de Garmin como TSS.
        act = {"type": "other", "duration": 0, "activityTrainingLoad": 42.0}
        tss, label = _estimate_session_tss(act)
        assert tss == 0.0
        assert _infer_tss_source_tag(act, label, ftp=None, hr_zones_raw=None) != "garmin_native"
        assert _infer_tss_model_tag(act) == "generic:no_duration"
        unknown_sport = {"type": "swimming", "duration": 3600, "averageHR": 140, "activityTrainingLoad": 42.0}
        tss2, _ = _estimate_session_tss(unknown_sport, hr_rest_bpm=50, hr_max_bpm=190)
        assert tss2 > 0 and tss2 != pytest.approx(42.0)
        assert _infer_tss_model_tag(unknown_sport).startswith("generic:")

    def test_tags_reset_between_estimates_on_same_dict(self):
        act = {"type": "other", "duration": 0, "activityTrainingLoad": 42.0}
        _estimate_session_tss(act)
        act["duration"] = 3600
        act["averageHR"] = 140
        del act["activityTrainingLoad"]
        _tss, label = _estimate_session_tss(act, hr_rest_bpm=50, hr_max_bpm=190)
        assert _infer_tss_source_tag(act, label, ftp=None, hr_zones_raw=None) != "garmin_native"

    def test_estimate_tss_double_session_same_day_accumulates(self):
        """Dos sesiones en el mismo día deben sumar sus TSS en el modelo."""
        morning = {"averageHR": 145, "maxHR": 185, "duration": 3600}
        afternoon = {"averageHR": 160, "maxHR": 185, "duration": 2700}
        tss_morning, _ = _estimate_session_tss(morning)
        tss_afternoon, _ = _estimate_session_tss(afternoon)
        # Ambas > 0 y distintas, y el modelo las sumará en tss_by_day
        assert tss_morning > 0 and tss_afternoon > 0
        assert tss_morning != tss_afternoon


# ─── Antes en ficheros sueltos (duración, caminar/senderismo) ───

def _hr_zones_payload_one_hour() -> str:
    zones = [
        {"zoneNumber": 1, "secsInZone": 900, "minHeartRateIn": 110, "maxHeartRateIn": 120},
        {"zoneNumber": 2, "secsInZone": 1200, "minHeartRateIn": 121, "maxHeartRateIn": 136},
        {"zoneNumber": 3, "secsInZone": 1500, "minHeartRateIn": 137, "maxHeartRateIn": 150},
    ]
    return json.dumps(zones)


def _activity_details_hr_indexed_payload(total_seconds: int = 900, hr_bpm: float = 150.0) -> str:
    descriptors = [
        {"key": "directTimestamp", "metricsIndex": 0},
        {"key": "directHeartRate", "metricsIndex": 1},
    ]
    rows = []
    for t in range(total_seconds + 1):
        rows.append({"metrics": [float(t), float(hr_bpm)]})
    payload = {"metricDescriptors": descriptors, "activityDetailMetrics": rows}
    return json.dumps(payload)


def test_trail_missing_duration_uses_zone_duration_before_native_tss():
    activity = {
        "activityType": "trail_running",
        "trainingStressScore": 300.0,
        "averageHR": 145.0,
        "maxHR": 176.0,
    }

    tss, label = estimate_session_tss(
        activity,
        ftp=None,
        running_threshold_pace_sec_per_km=330.0,
        hr_rest_bpm=50.0,
        hr_max_bpm=185.0,
        hr_zones_raw=_hr_zones_payload_one_hour(),
        activity_details_raw=None,
        use_trail_splits=False,
        hr_threshold_bpm=169.0,
    )

    assert label == "hrTSS"
    assert tss > 0
    assert abs(tss - 300.0) > 1e-6


def test_trail_missing_duration_uses_details_time_axis_before_native_tss():
    activity = {
        "activityType": "trail_running",
        "trainingStressScore": 250.0,
    }

    tss, label = estimate_session_tss(
        activity,
        ftp=None,
        running_threshold_pace_sec_per_km=330.0,
        hr_rest_bpm=50.0,
        hr_max_bpm=185.0,
        hr_zones_raw=None,
        activity_details_raw=_activity_details_hr_indexed_payload(total_seconds=900, hr_bpm=155.0),
        use_trail_splits=False,
        hr_threshold_bpm=169.0,
    )

    assert label == "hrTSS"
    assert tss > 0
    assert abs(tss - 250.0) > 1e-6


def test_details_duration_prefers_activity_level_summary_over_nested_laps():
    payload = {
        "summaryDTO": {"elapsedDuration": 36612},  # 10.170 h
        "laps": [
            {"duration": 2400},
            {"duration": 3000},
            {"duration": 4000},
        ],
        "segments": [
            {"meta": {"duration": 99999}},
        ],
    }

    secs = _extract_duration_seconds_from_activity_details_payload(json.dumps(payload))
    assert secs is not None
    assert abs(secs - 36612.0) < 1e-6


def test_details_duration_prefers_root_activity_field_over_nested_values():
    payload = {
        "durationInSeconds": 1800,
        "laps": [{"duration": 7200}],
    }

    secs = _extract_duration_seconds_from_activity_details_payload(json.dumps(payload))
    assert secs is not None
    assert abs(secs - 1800.0) < 1e-6


def test_resolve_duration_uses_hr_zones_when_activity_and_details_missing_duration():
    activity = {"activityType": "trail_running"}
    zones = json.dumps(
        [
            {"zoneNumber": 1, "secsInZone": 600, "minHeartRateIn": 110, "maxHeartRateIn": 120},
            {"zoneNumber": 2, "secsInZone": 1200, "minHeartRateIn": 121, "maxHeartRateIn": 136},
            {"zoneNumber": 3, "secsInZone": 300, "minHeartRateIn": 137, "maxHeartRateIn": 150},
        ]
    )

    hours = _resolve_activity_duration_hours(activity, hr_zones_raw=zones, activity_details_raw=None)
    assert abs(hours - (2100.0 / 3600.0)) < 1e-9


def test_resolve_duration_uses_last_hr_sample_for_garmin_indexed_details_payload():
    activity = {"activityType": "trail_running"}
    payload = {
        "metricDescriptors": [
            {"key": "directTimestamp", "metricsIndex": 0},
            {"key": "directHeartRate", "metricsIndex": 1},
        ],
        "activityDetailMetrics": [
            {"metrics": [0.0, 140.0]},
            {"metrics": [300.0, 143.0]},
            {"metrics": [600.0, 145.0]},
            {"metrics": [900.0, 146.0]},
            {"metrics": [1200.0, 147.0]},
        ],
    }

    hours = _resolve_activity_duration_hours(
        activity,
        hr_zones_raw=None,
        activity_details_raw=json.dumps(payload),
    )
    assert abs(hours - (1200.0 / 3600.0)) < 1e-9


def test_walk_hike_anchor_prefers_long_candidate_over_short_higher_norm(monkeypatch):
    activities = [
        {
            "type": "hiking",
            "id": 1,
            "_activity_details_raw": "short",
        },
        {
            "type": "hiking",
            "id": 2,
            "_activity_details_raw": "long",
        },
    ]

    def _fake_obs(details_raw: str):
        if details_raw == "short":
            # Higher normalized speed but only 40 min moving.
            return {"moving_seconds": 40 * 60.0, "normalized_equiv_speed_m_min": 130.0}
        if details_raw == "long":
            # Lower normalized speed but 100 min moving.
            return {"moving_seconds": 100 * 60.0, "normalized_equiv_speed_m_min": 110.0}
        return None

    monkeypatch.setattr(lm, "_compute_walk_hike_metabolic_observables", _fake_obs)

    threshold = lm._derive_walk_hike_threshold_speed_m_min_from_activities(activities)

    assert threshold is not None
    assert abs(threshold - 110.0) < 1e-9


def test_walk_hike_anchor_fallback_logs_when_no_long_candidate(monkeypatch, caplog):
    activities = [
        {
            "type": "hiking",
            "id": 99,
            "_activity_details_raw": "short-only",
        }
    ]

    def _fake_obs(details_raw: str):
        if details_raw == "short-only":
            return {"moving_seconds": 45 * 60.0, "normalized_equiv_speed_m_min": 123.0}
        return None

    monkeypatch.setattr(lm, "_compute_walk_hike_metabolic_observables", _fake_obs)

    with caplog.at_level("WARNING"):
        threshold = lm._derive_walk_hike_threshold_speed_m_min_from_activities(activities)

    assert threshold is not None
    assert abs(threshold - 123.0) < 1e-9
    assert any("walk_hike_anchor: selected fallback candidate below minimum moving time" in rec.message for rec in caplog.records)


def _veq(speed_m_min: float, grade: float) -> float:
    speed_mps = speed_m_min / 60.0
    vo2, src = lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, grade)
    return lm._walk_equivalent_flat_speed_from_vo2_m_min(vo2, src, speed_mps=speed_mps, grade=grade)


def test_walk_hike_model_has_no_blend_transition():
    speed_mps = 80.0 / 60.0
    _, src_low = lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, 0.02)
    _, src_high = lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, 0.04)

    assert src_low == "acsm"
    assert src_high == "minetti"


def test_walk_hike_equivalent_speed_monotonic_uphill():
    # Uphill monotonicity: for fixed speed, equivalent flat speed must not decrease
    # as positive grade increases.
    grades = [idx * 0.005 for idx in range(0, 91)]  # 0.0 .. 0.45
    values = [_veq(80.0, g) for g in grades]

    for i in range(1, len(values)):
        assert values[i] + 1e-9 >= values[i - 1], (
            f"non-monotonic at g={grades[i - 1]:.3f}->{grades[i]:.3f}: "
            f"{values[i - 1]:.3f}->{values[i]:.3f}"
        )
