"""
agent/load_compute.py
Cálculo de la carga: TSS de las actividades nuevas (una vez, guardado en activity_tss), serie TSS/ATL/CTL/TSB en load_metrics_daily y FTP de ciclismo.
"""

import logging
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any


from agent.mcp.client import (
    call_tool,
)
from agent import storage as _storage
from agent import load_metrics as _load_metrics
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _derive_walk_hike_threshold_speed_m_min_from_activities,
    _estimate_session_tss,
    _extract_activity_native_load,
    _extract_training_load_tss,
    _has_activity_power_data,
    _infer_tss_model_tag,
    _infer_tss_source_tag,
    _is_cycling_activity,
    _is_hike_walk_activity,
    _is_running_non_trail_activity,
    _is_strength_activity,
    _is_trail_hike_walk_activity,
    _resolve_hr_profile_values,
    _resolve_running_threshold_pace_sec_per_km,
    _try_parse_json,
)
from agent.dates import (
    _parse_iso_date_safe,
)
from agent.tool_results import (
    _activity_id_int,
    _is_activity_details_payload,
)
from agent.data_access import (
    _TSS_FORMULA_VERSION,
    _save_user_profile,
)
from agent.garmin_data import (
    _extract_activity_date_iso,
)
from agent.hr_profile import (
    _extract_cycling_ftp_watts,
    _extract_resting_hr_from_payload,
    _hydrate_profile_hr_anchors_if_missing,
    _resolve_hr_threshold_bpm,
    _resolve_running_threshold_pace_for_date,
    _resolve_tp_hr_threshold_for_date,
)
from agent.routes.load import (
    _fetch_activities_for_load_calc,
    _resolve_load_parameters_effective_date,
)

log = logging.getLogger(__name__)


class LoadMixin:
    """Cálculo de la carga: TSS de las actividades nuevas (una vez, guardado en activity_tss), serie TSS/ATL/CTL/TSB en load_metrics_daily y FTP de ciclismo."""

    async def _get_or_refresh_cycling_ftp(self, force_refresh: bool = False) -> float | None:
        """Obtiene FTP de ciclismo desde perfil o MCP y lo persiste en DB si aparece."""
        perf = self.user_profile.setdefault("performance", {}) if isinstance(self.user_profile, dict) else {}

        cached_ftp = None
        try:
            cached_ftp = float(perf.get("cycling_ftp") or 0) or None
        except (ValueError, TypeError):
            cached_ftp = None

        if cached_ftp and not force_refresh:
            return round(cached_ftp, 1)

        raw_ftp = await call_tool(self.mcp_session, "get_cycling_ftp", {})
        ftp_payload = _try_parse_json(raw_ftp)
        ftp_live = _extract_cycling_ftp_watts(ftp_payload if ftp_payload is not None else raw_ftp)

        if ftp_live:
            ftp_live = round(float(ftp_live), 1)
            ftp_changed = (cached_ftp is None) or (abs(float(cached_ftp) - float(ftp_live)) >= 0.1)
            perf["cycling_ftp"] = ftp_live
            if ftp_changed or not _parse_iso_date_safe(perf.get("cycling_ftp_date")):
                today_iso = date.today().isoformat()
                perf["cycling_ftp_date"] = today_iso
                perf["performance_params_updated_at"] = today_iso
            try:
                _save_user_profile(self.user_profile)
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                log.debug("No se pudo persistir cycling_ftp en perfil: %s", exc)
            return ftp_live

        return round(cached_ftp, 1) if cached_ftp else None

    async def compute_and_persist_load_metrics(
        self,
        force_full_recalc: bool = False,
        rebuild_from: str | None = None,
    ) -> None:
        """Calcula TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma) de forma incremental y los persiste en load_metrics_daily.

        Flujo:
        1. Lee la serie existente de DB (últimos 120 días).
        2. Si ya está al día, recarga el perfil y sale.
        3. Obtiene actividades de Garmin solo para los días nuevos (incremental).
        4. TSS por actividad: si ya está en activity_tss se reutiliza (nunca se recalcula hacia
           atrás); si es nueva se calcula una vez con hr_rest vigente en su fecha y se guarda.
        5. Re-corre el EWMA sembrando desde la fila de la víspera en DB y persiste (upsert).
        6. Actualiza self.user_profile["load_metrics"] con la serie completa.

        rebuild_from (ISO) reconstruye la línea base desde esa fecha: recalcula todos los TSS,
        recalibra el umbral de marcha y siembra el EWMA en 0. Solo para tools/rebuild_tss_baseline.py.
        """
        from datetime import date as _date

        async def _should_refresh_recent_load(existing_rows: list[dict]) -> tuple[bool, str, str | None]:
            """Verifica actividad reciente/no reflejada desde una fecha ancla dinámica.

            Regla de ancla:
            - Si existe un último día con entrenamiento guardado (activities_count>0 o tss>0),
              se usa ese día.
                        - Si no existe actividad registrada, usa el último día disponible en la serie DB.
            """
            def _resolve_last_trained_day(rows: list[dict]) -> str | None:
                trained_days: list[str] = []
                for row in rows or []:
                    d_iso = str(row.get("date") or "")
                    if not d_iso:
                        continue
                    db_day_count = int(row.get("activities_count") or 0)
                    db_day_tss = float(row.get("tss") or 0.0)
                    if db_day_count > 0 or db_day_tss > 0.0:
                        trained_days.append(d_iso)
                return max(trained_days) if trained_days else None

            def _resolve_last_series_day(rows: list[dict]) -> str | None:
                days = [
                    str(row.get("date") or "")
                    for row in (rows or [])
                    if str(row.get("date") or "")
                ]
                return max(days) if days else None

            today_local = _date.today()
            floor_window_day = today_local - timedelta(days=full_window_days)
            last_trained_iso = _resolve_last_trained_day(existing_rows)
            last_series_iso = _resolve_last_series_day(existing_rows)
            try:
                anchor_iso_seed = last_trained_iso or last_series_iso or today_local.isoformat()
                anchor_day = _date.fromisoformat(anchor_iso_seed)
            except ValueError:
                anchor_day = today_local
            if anchor_day > today_local:
                anchor_day = today_local
            if anchor_day < floor_window_day:
                anchor_day = floor_window_day
            anchor_iso = anchor_day.isoformat()

            db_by_day: dict[str, tuple[float, int]] = {}
            for row in existing_rows or []:
                d_iso = str(row.get("date") or "")
                if not d_iso:
                    continue
                prev_tss, prev_count = db_by_day.get(d_iso, (0.0, 0))
                db_by_day[d_iso] = (
                    max(prev_tss, float(row.get("tss") or 0.0)),
                    max(prev_count, int(row.get("activities_count") or 0)),
                )

            try:
                window_activities = await _fetch_activities_for_load_calc(
                    self.mcp_session,
                    anchor_iso,
                    today_local.isoformat(),
                )
            except (TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, KeyError) as exc:
                log.debug(
                    "compute_load: no se pudo verificar actividades desde %s: %s",
                    anchor_iso,
                    exc,
                )
                return False, "falló verificación incremental", None

            running_threshold_pace = _resolve_running_threshold_pace_sec_per_km(self.user_profile)
            hr_rest_bpm, hr_max_bpm = _resolve_hr_profile_values(self.user_profile)
            hr_threshold_bpm, _, _ = _resolve_hr_threshold_bpm(self.user_profile)
            cycling_ftp = _extract_cycling_ftp_watts(self.user_profile)

            garmin_by_day: dict[str, tuple[float, int]] = {}
            for act in window_activities or []:
                d_iso = _extract_activity_date_iso(act)
                if not d_iso or d_iso < anchor_iso:
                    continue
                act_tss = None
                est_tss, _ = _estimate_session_tss(
                    act,
                    ftp=cycling_ftp,
                    running_threshold_pace_sec_per_km=(
                        _resolve_running_threshold_pace_for_date(self.user_profile, d_iso) or running_threshold_pace
                    ),
                    hr_rest_bpm=hr_rest_bpm,
                    hr_max_bpm=hr_max_bpm,
                    hr_zones_raw=None,
                    hr_threshold_bpm=hr_threshold_bpm,
                )
                if est_tss > 0:
                    act_tss = float(est_tss)
                else:
                    native_tss = _extract_training_load_tss(act)
                    if native_tss is not None:
                        act_tss = float(native_tss)
                prev_load, prev_count = garmin_by_day.get(d_iso, (0.0, 0))
                garmin_by_day[d_iso] = (
                    prev_load + float(act_tss or 0.0),
                    prev_count + 1,
                )

            day_cursor = anchor_day
            while day_cursor <= today_local:
                day_iso = day_cursor.isoformat()
                db_day_tss, db_day_count = db_by_day.get(day_iso, (0.0, 0))
                garmin_day_load, garmin_day_count = garmin_by_day.get(day_iso, (0.0, 0))

                if garmin_day_count > db_day_count:
                    return True, (
                        f"actividades {day_iso} Garmin={garmin_day_count} > DB={db_day_count}"
                    ), day_iso

                if db_day_tss <= 0.0 and garmin_day_load > 0.0:
                    return True, (
                        f"carga {day_iso} en Garmin={garmin_day_load:.2f} con TSS DB={db_day_tss:.2f}"
                    ), day_iso

                day_cursor += timedelta(days=1)

            return False, f"DB ya refleja actividad/carga desde {anchor_iso}", None

        today = _date.today()
        full_window_days = 120
        full_start = today - timedelta(days=full_window_days)
        bypass_effective_clamp = bool(force_full_recalc)
        compute_mode = "incremental"
        compute_reason = ""

        # 1. Datos existentes en DB
        existing_series = _storage.get_load_metrics_series(days=full_window_days)
        last_date_str   = _storage.get_load_metrics_last_date()

        if not force_full_recalc and not rebuild_from and last_date_str:
            try:
                last_d = _date.fromisoformat(last_date_str)
            except ValueError:
                last_d = None
            if last_d and last_d >= today:
                # Auto-detectar serie corrupta: si tenemos suficientes días en DB pero
                # todos los CTL son 0, los datos fueron guardados con el bug del TSS=0.
                # En ese caso forzamos recálculo completo.
                stale_zeros = (
                    len(existing_series) > 5
                    and all(float(row.get("ctl") or 0) <= 0.01 for row in existing_series)
                )
                saved_formula_v = int(
                    (self.user_profile.get("load_metrics") or {}).get("formula_version") or 0
                )
                formula_changed = saved_formula_v != _TSS_FORMULA_VERSION
                if stale_zeros or formula_changed:
                    reason = "CTL=0" if stale_zeros else f"fórmula v{saved_formula_v}→v{_TSS_FORMULA_VERSION}"
                    log.info(
                        "compute_load: recalculando serie completa (%s)",
                        reason,
                    )
                    fetch_from = full_start.isoformat()
                    bypass_effective_clamp = True
                    compute_mode = "full_recalc"
                    compute_reason = reason
                else:
                    refresh_recent, refresh_reason, refresh_from = await _should_refresh_recent_load(existing_series)
                    if refresh_recent:
                        fetch_from = refresh_from or today.isoformat()
                        log.info(
                            "compute_load: último=%s pero se detectó actividad reciente no reflejada (%s) — refrescando desde %s",
                            last_date_str,
                            refresh_reason,
                            fetch_from,
                        )
                        compute_mode = "incremental_refresh"
                        compute_reason = refresh_reason
                    else:
                        log.info("compute_load: ya actualizado (último=%s) — recargando perfil", last_date_str)
                        self._last_load_compute_meta = {
                            "mode": "up_to_date",
                            "reason": "sin cambios recientes detectados",
                            "fetch_from": today.isoformat(),
                            "bypass_effective_clamp": False,
                            "formula_version": _TSS_FORMULA_VERSION,
                        }
                        self._apply_series_to_profile(existing_series, today)
                        return
            else:
                # Reprocessar desde el último día guardado (no last+1) para capturar
                # actividades que llegaron después de la última ejecución del mismo día.
                fetch_from = (last_d if last_d else full_start).isoformat()
                compute_mode = "incremental"
                compute_reason = "último cierre anterior a hoy"
        else:
            fetch_from = full_start.isoformat()
            log.info("compute_load: cálculo completo desde %s", fetch_from)
            compute_mode = "full_recalc" if force_full_recalc else "incremental"
            compute_reason = "forzado por arranque" if force_full_recalc else "sin último cierre"

        # Política de inmutabilidad histórica por cambio de parámetros:
        # si se actualiza umbral/FTP/zonas, no recalcular días anteriores.
        effective_d = _resolve_load_parameters_effective_date(self.user_profile)
        if effective_d and not bypass_effective_clamp:
            effective_iso = effective_d.isoformat()
            if fetch_from < effective_iso:
                recent_refresh_floor_iso = (today - timedelta(days=2)).isoformat()
                if fetch_from >= recent_refresh_floor_iso:
                    log.info(
                        "compute_load: parámetros actualizados el %s, pero se mantiene refresco reciente desde %s",
                        effective_iso,
                        fetch_from,
                    )
                else:
                    log.info(
                        "compute_load: parámetros actualizados el %s — se preserva histórico anterior",
                        effective_iso,
                    )
                    fetch_from = effective_iso

        if rebuild_from:
            fetch_from = str(rebuild_from)
            bypass_effective_clamp = True
            compute_mode = "baseline_rebuild"
            compute_reason = f"línea base desde {fetch_from}"

        log.info("compute_load: fetch incremental desde %s", fetch_from)

        # 1b. TSS ya guardados: nunca se recalculan (salvo reconstrucción de línea base).
        stored_tss: dict[int, dict] = {}
        if not rebuild_from:
            try:
                stored_map = _storage.get_activity_tss_map(fetch_from)
            except RuntimeError as exc:
                log.warning("compute_load: no se pudo leer activity_tss: %s", exc)
                stored_map = None
            if stored_map is None:
                log.warning("compute_load: activity_tss ilegible — se conserva la serie sin recalcular el histórico")
                self._apply_series_to_profile(existing_series, today)
                self._last_load_compute_meta = {
                    "mode": "skipped_activity_tss_unreadable",
                    "reason": "lectura de activity_tss fallida",
                    "fetch_from": fetch_from,
                    "bypass_effective_clamp": bool(bypass_effective_clamp),
                    "formula_version": _TSS_FORMULA_VERSION,
                }
                return
            stored_tss = stored_map

        # 2. FTP de ciclismo: primero perfil/DB; si falta, consultar MCP y persistir.
        cycling_ftp = None
        try:
            cycling_ftp = await self._get_or_refresh_cycling_ftp()
        except (TimeoutError, OSError, RuntimeError) as exc:
            log.debug("compute_load: no se pudo refrescar FTP ciclismo: %s", exc)
            cycling_ftp = None
        if cycling_ftp:
            log.info("compute_load: FTP ciclismo=%.0f W (usado para TSS por potencia)", cycling_ftp)
        else:
            log.info("compute_load: FTP ciclismo no disponible — usando estimación por FC")

        # 2b. Ritmo umbral de running: fuente de verdad = perfil persistido por usuario.
        running_threshold_pace = _resolve_running_threshold_pace_sec_per_km(self.user_profile)

        if running_threshold_pace:
            perf = self.user_profile.setdefault("performance", {})
            perf["running_threshold_pace_sec_per_km"] = round(float(running_threshold_pace), 1)
            perf["running_threshold_pace"] = f"{int(running_threshold_pace // 60)}:{int(running_threshold_pace % 60):02d}"
            if not _parse_iso_date_safe(perf.get("running_threshold_pace_date")):
                perf["running_threshold_pace_date"] = today.isoformat()
            threshold_pace_min_km = f"{int(running_threshold_pace // 60)}:{int(running_threshold_pace % 60):02d} min/km"
            log.info(
                "compute_load: ritmo umbral running=%s (usado para TSS por ritmo)",
                threshold_pace_min_km,
            )
        else:
            log.info("compute_load: ritmo umbral running no disponible — fallback por FC/RPE")

        # 3. Obtener actividades nuevas usando la función paginada probada
        new_activities = await _fetch_activities_for_load_calc(
            self.mcp_session, fetch_from, today.isoformat()
        )

        # 3b. Enriquecer actividades con detalle de get_activity para obtener
        # trainingStressScore y potencia.
        # - Recalc incremental: mantener ventana corta (rendimiento).
        # - Recalc forzado: enriquecer TODO el ciclismo del rango para recalcular
        #   TSS por potencia+FTP de forma consistente en el histórico.
        _ENRICH_DAYS = 14
        _enrich_cutoff = (today - timedelta(days=_ENRICH_DAYS)).isoformat()
        if force_full_recalc:
            _to_enrich = [
                a for a in new_activities
                if (
                    _is_cycling_activity(a.get("type") or a.get("activityType") or "")
                    or _is_running_non_trail_activity(a.get("type") or a.get("activityType") or "")
                )
            ]
        else:
            _to_enrich = [
                a for a in new_activities
                if (_extract_activity_date_iso(a) or "") >= _enrich_cutoff
            ]

        _to_enrich = [a for a in _to_enrich if _activity_id_int(a) not in stored_tss]
        if _to_enrich:
            log.info(
                "compute_load: enriqueciendo %d actividades con detalle (trainingStressScore/potencia)",
                len(_to_enrich),
            )
            for _act in _to_enrich:
                _act_id = _act.get("id") or _act.get("activityId")
                if not _act_id:
                    continue
                try:
                    _raw_d = await call_tool(
                        self.mcp_session, "get_activity", {"activity_id": int(_act_id)}
                    )
                    if _raw_d and _raw_d.strip():
                        _detail = json.loads(_raw_d) if _raw_d.strip()[0] in ("{", "[") else {}
                        if isinstance(_detail, list) and _detail:
                            _detail = _detail[0]
                        if isinstance(_detail, dict) and isinstance(_detail.get("activity"), dict):
                            _detail = _detail["activity"]
                        if isinstance(_detail, dict):
                            for _k in (
                                # Potencia/carga embebida (ciclismo)
                                "trainingStressScore",
                                "normalizedPower", "normalizedPowerWatts",
                                "normalized_power_watts",
                                "avgPower", "averagePower", "avg_power_watts",
                                "activityTrainingLoad",
                                # Señales de variabilidad para running quality
                                "lap_count", "lapCount",
                                "avg_speed_mps", "averageSpeedMps", "average_speed_mps",
                                "max_speed_mps", "maxSpeedMps", "max_speed_mps",
                                "workout_rpe", "workoutRpe",
                                "training_effect_label", "trainingEffectLabel",
                            ):
                                if _detail.get(_k) is not None:
                                    _act[_k] = _detail[_k]
                except (TimeoutError, OSError, RuntimeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    log.debug("compute_load: no se pudo enriquecer actividad %s: %s", _act_id, exc)

        # 4. TSS por día para las actividades nuevas
        hr_rest_bpm, hr_max_bpm, hr_threshold_bpm = await _hydrate_profile_hr_anchors_if_missing(
            self.mcp_session,
            self.user_profile,
        )
        tss_by_day:   dict[str, float] = {}
        count_by_day: dict[str, int]   = {}
        _hr_zones_cache: dict[str, str | None] = {}
        _activity_details_cache: dict[str, str | None] = {}
        activity_tss_rows: list[dict[str, Any]] = []
        _rhr_by_day: dict[str, float | None] = {}

        async def _hr_rest_for_date(d_iso: str) -> float | None:
            """hr_rest vigente en la fecha: media del RHR de los 14 días previos (perfil si no hay datos)."""
            values: list[float] = []
            d0 = _date.fromisoformat(d_iso)
            for back in range(1, 15):
                day_iso = (d0 - timedelta(days=back)).isoformat()
                if day_iso not in _rhr_by_day:
                    try:
                        raw_rhr = await call_tool(self.mcp_session, "get_rhr_day", {"date": day_iso})
                        parsed_rhr = _try_parse_json(raw_rhr)
                        _rhr_by_day[day_iso] = _extract_resting_hr_from_payload(
                            parsed_rhr if parsed_rhr is not None else raw_rhr
                        )
                    except (TimeoutError, OSError, RuntimeError, TypeError, ValueError) as exc:
                        log.debug("compute_load: RHR no disponible %s: %s", day_iso, exc)
                        _rhr_by_day[day_iso] = None
                value = _rhr_by_day[day_iso]
                if value:
                    values.append(float(value))
            if not values:
                return hr_rest_bpm
            return round(sum(values) / len(values), 3)

        # Walk/Hike threshold (Option A): anclaje del perfil calibrado en la línea base.
        # Solo se recalibra al reconstruir la línea base; sin anclaje se calibra en la ventana.
        perf_profile = self.user_profile.setdefault("performance", {}) if isinstance(self.user_profile, dict) else {}
        walk_hike_threshold_m_min: float | None = None
        if not rebuild_from:
            try:
                walk_hike_threshold_m_min = float(perf_profile.get("walk_hike_threshold_speed_m_min") or 0) or None
            except (TypeError, ValueError):
                walk_hike_threshold_m_min = None
        walk_hike_acts = [
            a
            for a in new_activities
            if _is_hike_walk_activity(a.get("type") or a.get("activityType") or "")
            and _activity_id_int(a) not in stored_tss
        ]
        if walk_hike_acts and walk_hike_threshold_m_min is not None:
            for _act in walk_hike_acts:
                _act["_walk_hike_threshold_speed_m_min"] = float(walk_hike_threshold_m_min)
        elif walk_hike_acts:
            for _act in walk_hike_acts:
                _act_id = _act.get("id") or _act.get("activityId")
                _act_id_key = str(_act_id) if _act_id is not None else ""
                if not _act_id_key:
                    continue
                if _act_id_key in _activity_details_cache:
                    _details = _activity_details_cache[_act_id_key]
                else:
                    try:
                        _details = await call_tool(
                            self.mcp_session,
                            "get_activity_details",
                            {"activity_id": int(_act_id)},
                        )
                    except (TimeoutError, OSError, RuntimeError, TypeError, ValueError) as exc:
                        log.debug("compute_load: no se pudo obtener detalle hike/walk %s: %s", _act_id, exc)
                        _details = None
                    if not _is_activity_details_payload(_details):
                        _details = None
                    _activity_details_cache[_act_id_key] = _details

                if _details:
                    _act["_activity_details_raw"] = _details

            walk_hike_threshold_m_min = _derive_walk_hike_threshold_speed_m_min_from_activities(walk_hike_acts)
            if walk_hike_threshold_m_min is not None:
                for _act in walk_hike_acts:
                    _act["_walk_hike_threshold_speed_m_min"] = float(walk_hike_threshold_m_min)
                log.info(
                    "compute_load: umbral marcha calibrado (Option A) = %.1f m/min (%d sesiones hike/walk)",
                    float(walk_hike_threshold_m_min),
                    len(walk_hike_acts),
                )
                if rebuild_from:
                    perf_profile["walk_hike_threshold_speed_m_min"] = round(float(walk_hike_threshold_m_min), 3)
                    perf_profile["walk_hike_threshold_date"] = today.isoformat()

        for act in new_activities:
            d_iso = _extract_activity_date_iso(act)
            if not d_iso:
                continue
            act_type = act.get("type") or act.get("activityType") or ""

            stored_row = stored_tss.get(_activity_id_int(act))
            if stored_row is not None:
                stored_value = float(stored_row.get("tss") or 0.0)
                if stored_value > 0:
                    tss_by_day[d_iso] = tss_by_day.get(d_iso, 0.0) + stored_value
                    count_by_day[d_iso] = count_by_day.get(d_iso, 0) + 1
                continue

            hr_zones_raw: str | None = None
            should_fetch_hr_zones = _is_trail_hike_walk_activity(act_type) or _is_strength_activity(act_type)
            if _is_cycling_activity(act_type):
                # En ciclismo, potencia+FTP es prioridad. Solo pedimos zonas si falta
                # potencia utilizable o no hay FTP del usuario para calcular TSS por potencia.
                should_fetch_hr_zones = should_fetch_hr_zones or (not cycling_ftp or not _has_activity_power_data(act))

            if should_fetch_hr_zones:
                act_id = act.get("id") or act.get("activityId")
                act_id_key = str(act_id) if act_id is not None else ""
                if act_id_key:
                    if act_id_key in _hr_zones_cache:
                        hr_zones_raw = _hr_zones_cache[act_id_key]
                    else:
                        try:
                            hr_zones_raw = await call_tool(
                                self.mcp_session,
                                "get_activity_hr_in_timezones",
                                {"activity_id": int(act_id)},
                            )
                        except (TimeoutError, OSError, RuntimeError, TypeError, ValueError) as exc:
                            log.debug("compute_load: no se pudieron obtener zonas FC de actividad %s: %s", act_id, exc)
                            hr_zones_raw = None
                        _hr_zones_cache[act_id_key] = hr_zones_raw
                if hr_zones_raw:
                    act["_hr_zones_raw"] = hr_zones_raw

            activity_details_raw: str | None = None
            needs_details = (
                _is_trail_hike_walk_activity(act_type)
                or _is_running_non_trail_activity(act_type)
                # Bici sin potencia utilizable: hrTSS por FC segundo a segundo.
                or (_is_cycling_activity(act_type) and not (cycling_ftp and _has_activity_power_data(act)))
            )
            if needs_details:
                act_id = act.get("id") or act.get("activityId")
                act_id_key = str(act_id) if act_id is not None else ""
                if act_id_key:
                    if act_id_key in _activity_details_cache:
                        activity_details_raw = _activity_details_cache[act_id_key]
                    else:
                        try:
                            activity_details_raw = await call_tool(
                                self.mcp_session,
                                "get_activity_details",
                                {"activity_id": int(act_id)},
                            )
                        except (TimeoutError, OSError, RuntimeError, TypeError, ValueError) as exc:
                            log.debug("compute_load: no se pudo obtener detalle de actividad %s: %s", act_id, exc)
                            activity_details_raw = None
                        if not _is_activity_details_payload(activity_details_raw):
                            activity_details_raw = None
                        _activity_details_cache[act_id_key] = activity_details_raw
                if activity_details_raw:
                    act["_activity_details_raw"] = activity_details_raw

            hr_rest_for_activity = await _hr_rest_for_date(d_iso)
            hr_threshold_tp = _resolve_tp_hr_threshold_for_date(self.user_profile, d_iso)
            threshold_pace_for_activity = (
                _resolve_running_threshold_pace_for_date(self.user_profile, d_iso) or running_threshold_pace
            )
            tss, tss_label = _estimate_session_tss(
                act,
                ftp=cycling_ftp,
                running_threshold_pace_sec_per_km=threshold_pace_for_activity,
                hr_rest_bpm=hr_rest_for_activity,
                hr_max_bpm=hr_max_bpm,
                hr_zones_raw=hr_zones_raw,
                activity_details_raw=activity_details_raw,
                hr_threshold_bpm=hr_threshold_bpm,
                hr_threshold_tp_bpm=hr_threshold_tp,
            )
            tss_source = _infer_tss_source_tag(
                activity=act,
                tss_label=tss_label,
                ftp=cycling_ftp,
                hr_zones_raw=hr_zones_raw,
            )
            model_tag = _infer_tss_model_tag(act)
            native_load = _extract_activity_native_load(act)
            act_id = act.get("id") or act.get("activityId")
            act_type = act.get("type") or act.get("activityType") or "unknown"
            if isinstance(act_type, dict):
                act_type = str(act_type.get("typeKey") or act_type.get("typeName") or "unknown")
            log.info(
                "compute_load: actividad id=%s fecha=%s tipo=%s tss=%.2f label=%s source=%s model=%s native_load=%s ftp=%s has_power=%s",
                act_id,
                d_iso,
                act_type,
                float(tss or 0.0),
                tss_label,
                tss_source,
                model_tag,
                f"{native_load:.3f}" if native_load is not None else "none",
                f"{cycling_ftp:.1f}" if cycling_ftp else "none",
                _has_activity_power_data(act),
            )

            if act_id is not None and d_iso:
                activity_tss_rows.append(
                    {
                        "activity_id": int(act_id),
                        "sport": str(act_type or "unknown"),
                        "start_date": d_iso,
                        "tss": round(float(tss or 0.0), 3),
                        "tss_source": str(tss_source or "unknown"),
                        "method": str(tss_label or "unknown"),
                        "model": str(model_tag or "unknown"),
                        "native_load": native_load,
                        "formula_version": int(_TSS_FORMULA_VERSION),
                        "hr_rest_used": hr_rest_for_activity,
                        "lthr_used": (
                            (hr_threshold_tp or hr_threshold_bpm)
                            if model_tag == "trail:tp_zones"
                            else hr_threshold_bpm
                        ),
                        "ftp_used": cycling_ftp,
                        "threshold_pace_used": threshold_pace_for_activity,
                        "walk_threshold_used": act.get("_walk_hike_threshold_speed_m_min"),
                        "computed_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
                    }
                )

            if tss > 0:
                tss_by_day[d_iso]   = tss_by_day.get(d_iso, 0.0) + tss
                count_by_day[d_iso] = count_by_day.get(d_iso, 0) + 1

        log.info("compute_load: %d días con TSS desde %s (actividades=%d)",
                 len(tss_by_day), fetch_from, len(new_activities))

        if activity_tss_rows:
            try:
                _storage.upsert_activity_tss_rows(activity_tss_rows)
            except Exception as exc:  # pragma: no cover - hard failure should never stop load metrics
                log.warning(
                    "compute_load: fallo al persistir activity_tss (%d filas): %s",
                    len(activity_tss_rows),
                    exc,
                )

        # 7. Semilla: ATL/CTL de la víspera del rango en DB (0 al reconstruir la línea base)
        atl_prev = 0.0
        ctl_prev = 0.0
        seed_row = None
        if not rebuild_from:
            seed_day_iso = (_date.fromisoformat(fetch_from) - timedelta(days=1)).isoformat()
            try:
                seed_row = _storage.get_load_metrics_row(seed_day_iso)
            except RuntimeError as exc:
                log.debug("compute_load: semilla no disponible para %s: %s", seed_day_iso, exc)
        if seed_row is not None:
            atl_prev = float(seed_row.get("atl") or 0.0)
            ctl_prev = float(seed_row.get("ctl") or 0.0)
            log.info("compute_load: semilla ATL=%.1f CTL=%.1f desde %s", atl_prev, ctl_prev, seed_row["date"])
        elif existing_series and not rebuild_from:
            for row in sorted(existing_series, key=lambda x: x["date"], reverse=True):
                if row["date"] < fetch_from:
                    atl_prev = float(row.get("atl") or 0.0)
                    ctl_prev = float(row.get("ctl") or 0.0)
                    log.info("compute_load: semilla ATL=%.1f CTL=%.1f desde %s", atl_prev, ctl_prev, row["date"])
                    break

        # 8. PMC día a día para el rango nuevo (CTL 42 / ATL 7, Forma con los valores de ayer, como TP)
        new_rows = _load_metrics.pmc_rows(
            tss_by_day, _date.fromisoformat(fetch_from), today, atl_prev, ctl_prev, count_by_day,
        )

        # 9. Persistir en DB
        _storage.upsert_load_metrics_series(new_rows)

        # 10. Recargar serie completa de DB y actualizar perfil
        full_series = _storage.get_load_metrics_series(days=full_window_days)
        self._apply_series_to_profile(full_series, today)
        log.info("compute_load: serie de %d días lista (hoy: TSS=%.1f ATL=%.1f CTL=%.1f TSB=%.1f)",
                 len(full_series),
                 float((full_series[-1] if full_series else {}).get("tss", 0)),
                 float((full_series[-1] if full_series else {}).get("atl", 0)),
                 float((full_series[-1] if full_series else {}).get("ctl", 0)),
                 float((full_series[-1] if full_series else {}).get("tsb", 0)))
        self._last_load_compute_meta = {
            "mode": compute_mode,
            "reason": compute_reason,
            "fetch_from": fetch_from,
            "bypass_effective_clamp": bool(bypass_effective_clamp),
            "formula_version": _TSS_FORMULA_VERSION,
        }

    def _apply_series_to_profile(self, series: list[dict], today) -> None:
        """Marca en el perfil la versión de la fórmula con la que se calculó la serie (dispara el recálculo si
        cambia). La serie vive solo en load_metrics_daily (27/10: antes se copiaba aquí con sus derivados)."""
        if not isinstance(self.user_profile, dict) or not series:
            return
        self.user_profile["load_metrics"] = {"formula_version": _TSS_FORMULA_VERSION}
        try:
            _save_user_profile(self.user_profile)
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            log.debug("No se pudo guardar formula_version en el perfil: %s", exc)
