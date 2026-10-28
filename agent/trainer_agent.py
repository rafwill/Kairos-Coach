"""
trainer_agent.py
Agente entrenador personal que combina OpenAI con las herramientas
de Garmin Connect a través del servidor MCP.
"""

import os
import time
import logging
import json
import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable

from openai import APIError
from mcp import ClientSession

from agent import sensors as _sensors
from agent import diabetes as _diabetes
from agent import ia_provider as _ia_provider
from agent import tool_groups as _tool_groups
from agent.sensors import strip_simulated_user_turn as _strip_simulated_user_turn
from agent.mcp.client import (
    list_available_tools,
    call_tool,
    reset_tool_transparency_events,
    consume_tool_transparency_events,
    record_tool_failure,
)
from agent import storage as _storage
from agent import load_metrics as _load_metrics
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _derive_walk_hike_threshold_speed_m_min_from_activities,
    _estimate_session_tss,
    _extract_activity_native_load,
    _extract_training_load_tss,
    _find_hr_zones_in_json,
    _has_activity_power_data,
    _infer_tss_model_tag,
    _infer_tss_source_tag,
    _is_cycling_activity,
    _is_hike_walk_activity,
    _is_running_non_trail_activity,
    _is_strength_activity,
    _is_trail_hike_walk_activity,
    _parse_hr_zones_list,
    _resolve_hr_profile_values,
    _resolve_running_threshold_pace_sec_per_km,
    _try_parse_json,
)
from agent.prompts import (
    _load_coaching_rules,
    _load_system_prompt,
    _render_prompt,
)
from agent.llm import (
    LLMAuthError,
    _build_llm_client,
    _is_llm_auth_error,
    _llm_timeout_seconds,
    _tool_timeout_seconds,
)
from agent.dates import (
    _extract_iso_date_from_text,
    _find_impossible_date,
    _normalize_date_args,
    _normalize_trend_date_range,
    _parse_iso_date_safe,
)
from agent.knowledge import (
    _build_athlete_knowledge_context,
    _load_athlete_knowledge_chunks,
)
from agent.tool_results import (
    _activity_id_int,
    _build_tools_schema,
    _compact_tool_result,
    _extract_activities_list,
    _is_activity_details_payload,
    _is_no_data_result,
)
from agent.data_access import (
    _TSS_FORMULA_VERSION,
    _add_ia_tokens,
    _load_metrics_series,
    _load_metrics_view,
    _load_session_context,
    _load_session_summaries,
    _load_stored_activity_tss,
    _load_user_profile,
    _save_history_entries,
    _save_user_profile,
    _session_memory_enabled,
)
from agent.garmin_data import (
    _GARMIN_ZONE_NAMES_ES,
    _extract_activity_date_iso,
    _get_activity_name_es,
    _is_activity_in_last_48h,
)
from agent.hr_profile import (
    _extract_cycling_ftp_watts,
    _extract_resting_hr_from_payload,
    _hydrate_profile_hr_anchors_if_missing,
    _normalize_garmin_hr_zones,
    _resolve_hr_threshold_bpm,
    _resolve_hr_zones,
    _resolve_running_threshold_pace_for_date,
    _resolve_tp_hr_threshold_for_date,
)
from agent.wellness import (
    _extract_body_battery_snapshot,
    _extract_hrv_snapshot,
    _format_body_battery_day,
    _format_hrv_day,
    _format_sleep_day,
)
from agent.kairos_tools import (
    _KAIROS_INTERNAL_TOOLS,
    _kairos_correlate,
    _kairos_load_trends,
    _kairos_weekly_sport_breakdown,
)
from agent.daily_engine import (
    _build_startup_plan_recommendation,
    _build_today_session_frame,
    _compute_daily_plan_adjustment,
    _compute_plan_execution_feedback,
    _extract_activity_duration_minutes,
    _get_active_training_plan,
    _get_planned_session_for_date,
    _normalize_storage_plan_row,
    _session_intensity_label,
)
from agent.plans import (
    _build_goal_plan_fallback,
    _build_structured_plan_markdown,
    _build_training_plan_status_markdown,
    _generate_structured_plan_payload,
    _summarize_plan_changes,
    _validate_structured_plan,
    _wants_new_plan_intent,
)
from agent.routes.load import (
    _build_current_week_tss_markdown,
    _build_load_trend_markdown,
    _build_recent_tss_by_sport_markdown,
    _build_today_load_status_markdown,
    _build_week_activities_markdown,
    _compute_effective_week_tss_from_series_and_activities,
    _fetch_activities_for_load_calc,
    _format_load_fatigue_summary,
    _resolve_load_parameters_effective_date,
)
from agent.routes.records import (
    _build_personal_records_markdown,
)
from agent.routes.profile import (
    _build_config_options_markdown,
    _build_goal_status_markdown,
    _build_hr_threshold_profile_markdown,
    _build_running_threshold_profile_markdown,
)
from agent.routes.period import (
    _build_period_activities_markdown,
)
from agent.routes.sleep import (
    _build_sleep_night_markdown,
    _build_sleep_trend_markdown,
    _resolve_sleep_night,
    _resolve_sleep_window,
)
from agent.activity_analysis import (
    _build_activity_analysis_block,
    _build_activity_candidates_payload,
    _build_post_activity_section_spec,
    _find_activity_id_by_date,
    _normalize_get_activity_args,
    _resolve_activity_id_from_query,
    _resolve_last_activity,
)
from agent.startup_status import (
    _build_proactive_status_markdown,
)
from agent.replies import (
    _build_mcp_error_warning_markdown,
    _build_mcp_transparency_markdown,
    _is_generic_needs_more_info_reply,
    _is_no_access_reply,
)
from agent.router import (
    ToolRouter,
    _detect_personal_records_sport_intent,
    _is_coaching_recommendation_intent,
    _is_personal_records_followup_intent,
    _is_personal_records_intent,
    _is_planning_intent,
    _is_post_activity_feedback_intent,
    _is_today_training_question,
    _is_tomorrow_workout_intent,
)
from agent.routes.day_data import (
    _build_mcp_factual_query_markdown,
    _build_recovery_fallback_snapshot,
)
from agent.coaching import (
    _calendar_block,
    _coaching_horizon,
)

log = logging.getLogger(__name__)


# ─── Funciones de persistencia ───────────────────────────────────────────────
# Thin wrappers sobre agent.storage para mantener compatibilidad con imports
# existentes en main.py y los tests. Toda la lógica de persistencia
# vive en agent/storage.py.


# ─── Rutas factuales por periodo y del sueño (punto 3 de producción, 25/10) ─────
# Preguntas que el LLM resolvía leyendo series y en las que se equivocaba (exploración del 24/10):
# kilómetros de un periodo, la actividad más larga de un mes y el sueño de anoche. Las responde Python.


# ─── Proveedores de LLM y su clave ──────────────────────────────────────────


# ─── Herramientas internas Kairos (kairos_*) ─────────────────────────────────
# Estas tools operan sobre datos ya almacenados en el perfil (load_metrics.series)
# o sobre actividades Garmin MCP, y se procesan en Python puro sin llamar al LLM.


# ─── Helpers para cálculo incremental de carga/fatiga ─────────────────────────


class TrainerAgent:
    """
    Agente entrenador personal que usa OpenAI + Garmin MCP.
    Mantiene historial de conversación y llama herramientas de Garmin
    automáticamente según lo que necesite para responder al usuario.
    """

    # Preguntas del usuario en la sesión anterior (se rellena en initialize); tupla: no se comparte mutable.
    previous_session_questions: tuple[str, ...] = ()

    def __init__(self, mcp_session: ClientSession, provider: dict | None = None):
        self.mcp_session = mcp_session
        # Proveedor de IA del atleta (fila de ia_provider); sin él, el de la base de datos.
        self.set_provider(provider or _storage.get_ia_provider() or {})
        self.system_prompt = _load_system_prompt()
        self.user_profile = _load_user_profile()
        self.conversation_history: list[dict] = []
        self.tools_schema: list[dict] = []
        self.knowledge_chunks, self.knowledge_sources = _load_athlete_knowledge_chunks(
            os.environ.get("ATHLETE_KB_PATHS", "")
        )
        stored_kb = (_storage.load_athlete_knowledge() or "").strip()
        if stored_kb:
            self.knowledge_chunks.append({"source": "db:athlete_knowledge", "text": stored_kb[:4000]})
            if "db:athlete_knowledge" not in self.knowledge_sources:
                self.knowledge_sources.append("db:athlete_knowledge")
        
        # Variables para tracking de tokens de la sesión
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.tool_router = ToolRouter()

    def set_provider(self, config: dict) -> None:
        """Configura o cambia el proveedor de IA (fila de ia_provider: provider, base_url, model)."""
        if not _ia_provider.is_valid(config):
            raise ValueError("Falta el proveedor de IA del atleta (provider, base_url y model): entra en Kairos para darlo.")
        self.provider_config = dict(config)
        self.provider = str(config["provider"])
        self.client, self.model, self._api_key = _build_llm_client(config)
        self._watch_llm_auth()

        # Variables para tracking de tokens de la sesión
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def _watch_llm_auth(self) -> None:
        """Clave rechazada en cualquier llamada al LLM (26/10): marca `llm_auth_failed` y lanza LLMAuthError.

        LLMAuthError es un RuntimeError, que todas las capas (bucle con tools, coaching, rescates, sensores)
        ya capturan; la CLI ve la marca, pide la clave nueva y repite la pregunta. Antes, un 401 de la capa de
        coaching (openai.AuthenticationError) no lo capturaba nadie y cerraba la sesión.
        """
        self.llm_auth_failed = False
        completions = self.client.chat.completions
        create = completions.create

        async def _create(*args, **kwargs):
            try:
                return await create(*args, **kwargs)
            except LLMAuthError:
                raise
            except Exception as exc:
                if not _is_llm_auth_error(exc):
                    raise
                self.llm_auth_failed = True
                raise LLMAuthError(f"{self.provider}: el proveedor rechaza la clave ({exc})") from exc

        completions.create = _create

    async def initialize(self) -> None:
        """Carga las herramientas disponibles del MCP y restaura el historial reciente."""
        tools = await list_available_tools(self.mcp_session)
        self.tools_schema = _build_tools_schema(tools)
        # Añadir herramientas internas Kairos (no requieren MCP)
        self.tools_schema.extend(_KAIROS_INTERNAL_TOOLS)

        self._restore_previous_session()
        await self._refresh_hr_zones_from_garmin()

    async def _refresh_hr_zones_from_garmin(self) -> None:
        """Lee las zonas de FC de Garmin y las guarda en el perfil solo si cambian.

        Fuente de verdad de las zonas (reloj y TrainingPeaks). Una llamada por arranque; si Garmin falla,
        se siguen usando las guardadas. La versión anterior pasa a hr_zones_garmin_history con su fecha.
        """
        try:
            raw = await call_tool(self.mcp_session, "get_heart_rate_zones", {})
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError) as exc:
            log.info("Zonas de FC: Garmin no responde, se usan las guardadas (%s)", exc)
            return
        live = _normalize_garmin_hr_zones(raw)
        if not live or not isinstance(self.user_profile, dict):
            log.info("Zonas de FC: Garmin no devolvió zonas válidas; se usan las guardadas")
            return
        perf = self.user_profile.setdefault("performance", {})
        stored = perf.get("hr_zones_garmin") or {}
        if stored.get("sports") == live["sports"]:
            return
        today_iso = date.today().isoformat()
        if stored.get("sports"):
            history = perf.setdefault("hr_zones_garmin_history", [])
            history.append({"valid_from": stored.get("updated_at"), "valid_until": today_iso, "sports": stored["sports"]})
        perf["hr_zones_garmin"] = {"updated_at": today_iso, **live}
        try:
            _save_user_profile(self.user_profile)
            log.info("Zonas de FC: actualizadas desde Garmin (%s)", today_iso)
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            log.warning("Zonas de FC: no se pudieron guardar en el perfil: %s", exc)

    def _restore_previous_session(self) -> None:
        """De la sesión anterior solo se recuperan las preguntas del usuario (temas), no las respuestas:
        con ellas en el contexto el LLM repetía cifras viejas o erróneas sin volver a consultar las tools."""
        if not _session_memory_enabled():
            self.previous_session_questions = ()
            return
        ctx = _load_session_context()
        self.previous_session_questions = tuple(
            str(entry.get("content") or "").strip()
            for entry in ctx.get("history", [])[-6:]
            if entry.get("role") == "user" and str(entry.get("content") or "").strip()
        )

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

    async def fetch_garmin_personal_data(self, profile_raw: str | None = None) -> dict:
        """
        Obtiene datos personales del usuario directamente desde Garmin Connect.
        Estructura real de get_user_profile:
          { "userData": { "gender", "weight"(g), "height"(cm), "birthDate" }, ... }
        El nombre no está disponible en este endpoint.
        profile_raw: respuesta de get_user_profile ya pedida (el arranque la usa también para comprobar el acceso).
        """
        result = {}
        today = date.today().isoformat()

        # --- get_user_profile ---
        try:
            raw = profile_raw if profile_raw is not None else await call_tool(self.mcp_session, "get_user_profile", {})
            data = json.loads(raw) if raw and raw.strip().startswith("{") else {}
            if isinstance(data, dict):
                ud = data.get("userData", {})

                # Edad calculada desde birthDate (YYYY-MM-DD)
                birth = ud.get("birthDate") or data.get("birthDate")
                if birth:
                    try:
                        born = date.fromisoformat(str(birth))
                        today_d = date.today()
                        age = today_d.year - born.year - (
                            (today_d.month, today_d.day) < (born.month, born.day)
                        )
                        if 5 < age < 120:
                            result["age"] = age
                    except (ValueError, TypeError):
                        pass

                # Género
                gender = ud.get("gender") or data.get("gender", "")
                if gender:
                    result["gender"] = "hombre" if "MALE" in str(gender).upper() else "mujer"

                # Altura en cm
                height = ud.get("height") or data.get("height")
                if height:
                    try:
                        h = float(height)
                        if h > 50:
                            result["height_cm"] = int(round(h))
                    except (ValueError, TypeError):
                        pass

                # Peso: Garmin lo devuelve en gramos (ej: 67000.0 = 67 kg)
                weight = ud.get("weight") or data.get("weight")
                if weight:
                    try:
                        w = float(weight)
                        result["weight_kg"] = round(w / 1000, 1) if w > 300 else round(w, 1)
                    except (ValueError, TypeError):
                        pass
        except (TimeoutError, OSError, RuntimeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log.debug("fetch_garmin_personal_data: get_user_profile falló: %s", exc)

        # --- get_body_composition (peso más reciente si get_user_profile no lo devolvió) ---
        if "weight_kg" not in result:
            try:
                raw = await call_tool(
                    self.mcp_session,
                    "get_body_composition",
                    {"start_date": today, "end_date": today},
                )
                data = json.loads(raw) if raw and raw.strip().startswith(("{", "[")) else {}
                if isinstance(data, list) and data:
                    data = data[0]
                if isinstance(data, dict):
                    weight = data.get("weight") or data.get("weightKg") or data.get("value")
                    if weight:
                        try:
                            w = float(weight)
                            result["weight_kg"] = round(w / 1000, 1) if w > 300 else round(w, 1)
                        except (ValueError, TypeError):
                            pass
            except (TimeoutError, OSError, RuntimeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                log.debug("fetch_garmin_personal_data: get_body_composition falló: %s", exc)

        return result

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
        from datetime import date as _date, timedelta

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

    async def _build_plan_load_context(self) -> dict:
        """Estado real del atleta para el motor de planes: carga actual y volumen de las últimas semanas."""
        context: dict[str, Any] = {}
        series = _load_metrics_series()
        if series:
            load = _load_metrics_view(self.user_profile, series)
            latest = load.get("latest") or {}
            for key in ("ctl", "atl", "tsb"):
                if latest.get(key) is not None:
                    context[key] = round(float(latest[key]), 1)
            if load.get("status"):
                context["status"] = str(load["status"])
        today = date.today()
        try:
            acts = await _fetch_activities_for_load_calc(
                self.mcp_session, (today - timedelta(days=42)).isoformat(), today.isoformat()
            )
        except (TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, KeyError) as exc:
            log.debug("plan load context: actividades no disponibles: %s", exc)
            acts = []
        last_28_iso = (today - timedelta(days=28)).isoformat()
        minutes_28 = 0.0
        longest = 0.0
        for act in acts or []:
            d_iso = _extract_activity_date_iso(act) or ""
            minutes = _extract_activity_duration_minutes(act)
            if d_iso >= last_28_iso:
                minutes_28 += minutes
            act_type = act.get("type") or act.get("activityType") or act.get("activityTypeDTO") or ""
            if _is_running_non_trail_activity(act_type) or _load_metrics._is_trail_activity(act_type):
                longest = max(longest, minutes)
        if minutes_28 > 0:
            context["recent_weekly_minutes"] = round(minutes_28 / 4.0, 1)
        if longest > 0:
            context["longest_recent_min"] = round(longest, 1)
        return context

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

    async def collect_startup_snapshot_48h(self, prefetch_today: dict | None = None,
                                           load_ready: "asyncio.Future | None" = None) -> dict:
        """Recoge un snapshot operativo de 48h para briefing de arranque.

        load_ready: el cálculo de la carga del arranque, que corre a la vez (28/10, pendiente 8c). Las llamadas a
        Garmin no lo esperan; la lectura de la serie de carga, sí, para no leerla antes de que se guarde.
        """
        today_iso = date.today().isoformat()
        yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
        two_days_ago_iso = (date.today() - timedelta(days=2)).isoformat()
        _pf = prefetch_today or {}
        _t_snap = time.perf_counter()
        if not hasattr(self, "_session_wellness_cache") or not isinstance(getattr(self, "_session_wellness_cache", None), dict):
            self._session_wellness_cache = {}
        _session_wellness_cache: dict[str, dict[str, Any]] = self._session_wellness_cache

        async def _tool_json(tool_name: str, args: dict) -> Any:
            try:
                raw = await asyncio.wait_for(
                    call_tool(self.mcp_session, tool_name, args),
                    timeout=8.0,
                )
            except (TimeoutError, OSError) as exc:
                log.debug("collect_startup_snapshot_48h: fallo red en %s: %s", tool_name, exc)
                return None
            except RuntimeError as exc:
                log.debug("collect_startup_snapshot_48h: fallo runtime en %s: %s", tool_name, exc)
                return None
            except asyncio.TimeoutError:
                log.debug("collect_startup_snapshot_48h: timeout en %s", tool_name)
                return None
            parsed_raw = _try_parse_json(raw)
            if parsed_raw is not None:
                return parsed_raw
            compact = _compact_tool_result(raw, tool_name)
            parsed = _try_parse_json(compact)
            return parsed if parsed is not None else compact

        async def _cached_or_fetch(cache_key: str, tool_name: str, args: dict) -> Any:
            if cache_key in _pf:
                raw = _pf[cache_key]
                if raw is None:
                    return None
                parsed = _try_parse_json(raw)
                if parsed is not None:
                    return parsed
                compact = _compact_tool_result(raw, tool_name)
                return _try_parse_json(compact) or compact
            return await _tool_json(tool_name, args)

        def _safe_upsert_wellness(
            metric_date: str,
            *,
            body_payload: Any = None,
            hrv_payload: Any = None,
            source_payload: dict | None = None,
        ) -> None:
            body = _extract_body_battery_snapshot(body_payload, metric_date)
            hrv = _extract_hrv_snapshot(hrv_payload, metric_date)
            if not body and not hrv:
                return
            try:
                _storage.upsert_wellness_daily(
                    metric_date,
                    body_battery=body,
                    hrv=hrv,
                    source_payload=source_payload,
                )
            except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
                log.debug("collect_startup_snapshot_48h: wellness DB upsert fallo (%s): %s", metric_date, exc)

        async def _return(value: Any) -> Any:
            return value

        def _session_get(metric_date: str, metric_key: str) -> Any:
            day_bucket = _session_wellness_cache.get(metric_date) or {}
            return day_bucket.get(metric_key)

        def _session_set(metric_date: str, metric_key: str, payload: Any) -> None:
            day_bucket = _session_wellness_cache.setdefault(metric_date, {})
            day_bucket[metric_key] = payload

        # Los datos de hoy se piden siempre a Garmin (cambian durante el día); los de ayer, de la caché de la sesión.
        use_session_body_yday = (
            ("body_yday" not in _pf)
            and _session_get(yesterday_iso, "body") is not None
        )
        use_session_hrv_yday = (
            ("hrv_yday" not in _pf)
            and _session_get(yesterday_iso, "hrv") is not None
        )

        body_today_task: Awaitable[Any]
        if "body_today" in _pf:
            body_today_task = _cached_or_fetch("body_today", "get_body_battery", {"start_date": today_iso, "end_date": today_iso})
        else:
            body_today_task = _tool_json("get_body_battery", {"start_date": today_iso, "end_date": today_iso})

        body_yday_task = (
            _return(_session_get(yesterday_iso, "body"))
            if use_session_body_yday
            else _tool_json("get_body_battery", {"start_date": yesterday_iso, "end_date": yesterday_iso})
        )

        hrv_today_task: Awaitable[Any]
        if "hrv_today" in _pf:
            hrv_today_task = _cached_or_fetch("hrv_today", "get_hrv_data", {"date": today_iso})
        else:
            hrv_today_task = _tool_json("get_hrv_data", {"date": today_iso})

        hrv_yday_task = (
            _return(_session_get(yesterday_iso, "hrv"))
            if use_session_hrv_yday
            else _tool_json("get_hrv_data", {"date": yesterday_iso})
        )

        body_today, body_yday, hrv_today, hrv_yday, sleep_today, sleep_yday, activities_raw = (
            await asyncio.gather(
                body_today_task,
                body_yday_task,
                hrv_today_task,
                hrv_yday_task,
                _tool_json("get_sleep_summary", {"date": today_iso}),
                _tool_json("get_sleep_summary", {"date": yesterday_iso}),
                _tool_json(
                    "get_activities_by_date",
                    {
                        "start_date": two_days_ago_iso,
                        "end_date": today_iso,
                        "page": 0,
                        "page_size": 100,
                    },
                ),
            )
        )

        # Política por sesión: si MCP trae dato válido, persistirlo y cachearlo en memoria.
        # Si falla MCP, no usar DB histórica para cálculos actuales (evita reutilizar datos viejos).
        body_today_from_mcp = _extract_body_battery_snapshot(body_today, today_iso)
        hrv_today_from_mcp = _extract_hrv_snapshot(hrv_today, today_iso)
        body_yday_from_mcp = _extract_body_battery_snapshot(body_yday, yesterday_iso)
        hrv_yday_from_mcp = _extract_hrv_snapshot(hrv_yday, yesterday_iso)

        if body_today_from_mcp is not None:
            _session_set(today_iso, "body", body_today)
        if hrv_today_from_mcp is not None:
            _session_set(today_iso, "hrv", hrv_today)
        if body_yday_from_mcp is not None:
            _session_set(yesterday_iso, "body", body_yday)
        if hrv_yday_from_mcp is not None:
            _session_set(yesterday_iso, "hrv", hrv_yday)

        wellness_sources = {
            "body_today": "mcp" if body_today_from_mcp is not None else "mcp_unavailable",
            "hrv_today": "mcp" if hrv_today_from_mcp is not None else "mcp_unavailable",
            "body_yday": "session" if use_session_body_yday else "mcp",
            "hrv_yday": "session" if use_session_hrv_yday else "mcp",
        }
        if (not use_session_body_yday) and (body_yday_from_mcp is None):
            wellness_sources["body_yday"] = "mcp_unavailable"
        if (not use_session_hrv_yday) and (hrv_yday_from_mcp is None):
            wellness_sources["hrv_yday"] = "mcp_unavailable"

        # Persistir/actualizar wellness diario con datos nuevos de MCP o prefetch.
        if body_today_from_mcp is not None:
            _safe_upsert_wellness(
                today_iso,
                body_payload=body_today,
                hrv_payload=hrv_today if hrv_today_from_mcp is not None else None,
                source_payload={
                    "body": body_today,
                    "hrv": hrv_today if hrv_today_from_mcp is not None else None,
                },
            )
        elif hrv_today_from_mcp is not None:
            _safe_upsert_wellness(
                today_iso,
                hrv_payload=hrv_today,
                source_payload={"hrv": hrv_today},
            )

        if ((not use_session_body_yday and body_yday_from_mcp is not None) or
            (not use_session_hrv_yday and hrv_yday_from_mcp is not None)):
            _safe_upsert_wellness(
                yesterday_iso,
                body_payload=body_yday if (not use_session_body_yday and body_yday_from_mcp is not None) else None,
                hrv_payload=hrv_yday if (not use_session_hrv_yday and hrv_yday_from_mcp is not None) else None,
                source_payload={
                    "body": body_yday if (not use_session_body_yday and body_yday_from_mcp is not None) else None,
                    "hrv": hrv_yday if (not use_session_hrv_yday and hrv_yday_from_mcp is not None) else None,
                },
            )

        log.info("[SNAPSHOT] ⏱ gather_7_calls=%.1fs (cached: body=%s hrv=%s)",
                 time.perf_counter() - _t_snap,
                 "body_today" in _pf, "hrv_today" in _pf)
        activities_recent = _extract_activities_list(activities_raw)
        if not activities_recent:
            retry_recent_raw = await _tool_json(
                "get_activities_by_date",
                {
                    "start_date": yesterday_iso,
                    "end_date": today_iso,
                    "page": 0,
                    "page_size": 100,
                },
            )
            activities_recent = _extract_activities_list(retry_recent_raw)

        if not activities_recent:
            # Fallback para backends que no responden bien al filtro por fecha.
            fallback_recent_raw = await _tool_json("get_activities", {"start": "0", "limit": "20"})
            activities_recent = _extract_activities_list(fallback_recent_raw)

        recent_trainings: list[dict] = []
        for activity in activities_recent:
            if not _is_activity_in_last_48h(activity):
                continue
            day = _extract_activity_date_iso(activity) or ""
            recent_trainings.append(
                {
                    "date": day,
                    "name": activity.get("name") or activity.get("activityName") or activity.get("activityType") or "Actividad",
                    "activity_id": activity.get("activityId") or activity.get("id"),
                }
            )

        # Fase 3: cierre del bucle planificado vs ejecutado (dia N-1).
        plan_execution_feedback: dict = {}
        active_plan = _get_active_training_plan(getattr(self, "user_profile", {}) or {})
        if active_plan:
            yday_activities: list[dict] = []
            yday_raw = await _tool_json(
                "get_activities_by_date",
                {
                    "start_date": yesterday_iso,
                    "end_date": yesterday_iso,
                    "page": 0,
                    "page_size": 100,
                },
            )
            if isinstance(yday_raw, dict):
                yday_activities = _extract_activities_list(yday_raw.get("activities") or yday_raw)
            elif isinstance(yday_raw, list):
                yday_activities = _extract_activities_list(yday_raw)

            # Fallback local: filtrar el lote reciente por fecha exacta de ayer.
            if not yday_activities:
                for _act in activities_recent:
                    if _extract_activity_date_iso(_act) == yesterday_iso:
                        yday_activities.append(_act)

            feedback = _compute_plan_execution_feedback(
                plan=active_plan,
                activities_for_day=yday_activities,
                target_date_iso=yesterday_iso,
                profile=getattr(self, "user_profile", {}) if hasattr(self, "user_profile") else {},
            )
            if isinstance(feedback, dict):
                plan_execution_feedback = feedback

        # Carga: solo la serie de load_metrics_daily. Sin ella no hay estado de carga (27/10: antes se recalculaba
        # el TSS en vivo, sin el detalle de las actividades, o se usaba una copia del perfil que podía ser vieja).
        _load_debug: str = "sin serie en load_metrics_daily"
        load_fatigue = None
        if load_ready is not None:
            await asyncio.wait([load_ready])  # sin propagar su error: el arranque ya lo avisa por su cuenta
        canonical_series = _load_metrics_series()
        if canonical_series:
            load_fatigue = _load_metrics_view(getattr(self, "user_profile", {}), canonical_series) or None
            if load_fatigue:
                _load_debug = f"usando serie canónica de DB ({len(canonical_series)} días)"
                try:
                    today_d = date.today()
                    week_start = today_d - timedelta(days=today_d.weekday())
                    week_dates = [week_start + timedelta(days=i) for i in range((today_d - week_start).days + 1)]
                    week_activities_raw = await _tool_json(
                        "get_activities_by_date",
                        {
                            "start_date": week_start.isoformat(),
                            "end_date": today_iso,
                            "page": 0,
                            "page_size": 200,
                        },
                    )
                    week_activities = _extract_activities_list(week_activities_raw)
                    effective_tss, used_fallback = _compute_effective_week_tss_from_series_and_activities(
                        series=canonical_series,
                        activities=week_activities,
                        week_dates=week_dates,
                    )
                    load_fatigue.setdefault("weekly", {})
                    load_fatigue["weekly"]["current_tss_effective"] = effective_tss
                    load_fatigue["weekly"]["current_tss_effective_source"] = (
                        "load_metrics_daily+activity_fallback" if used_fallback else "load_metrics_daily"
                    )
                except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as _week_exc:
                    log.debug("collect_startup: no se pudo calcular current_tss_effective: %s", _week_exc)

        body_summary = (
            f"hoy={_format_body_battery_day(body_today, today_iso)} · "
            f"ayer={_format_body_battery_day(body_yday, yesterday_iso)}"
        )
        hrv_summary = (
            f"hoy={_format_hrv_day(hrv_today, today_iso)} · "
            f"ayer={_format_hrv_day(hrv_yday, yesterday_iso)}"
        )
        sleep_summary = (
            f"hoy={_format_sleep_day(sleep_today, today_iso)} · "
            f"ayer={_format_sleep_day(sleep_yday, yesterday_iso)}"
        )

        return {
            "window_hours": 48,
            "dates": {"today": today_iso, "yesterday": yesterday_iso},
            "wellness_sources": wellness_sources,
            "body_battery": {"today": body_today, "yesterday": body_yday, "summary": body_summary},
            "hrv": {"today": hrv_today, "yesterday": hrv_yday, "summary": hrv_summary},
            "sleep": {"today": sleep_today, "yesterday": sleep_yday, "summary": sleep_summary},
            "load_fatigue": load_fatigue or {},
            "load_debug": _load_debug,
            "plan_execution_feedback": plan_execution_feedback,
            "trainings": recent_trainings[:5],
        }

    async def build_startup_status_markdown(self, profile_changes: list[str] | None = None,
                                            load_ready: "asyncio.Future | None" = None) -> str:
        """Construye el mensaje proactivo mostrado al arrancar la sesion (load_ready: ver collect_startup_snapshot_48h)."""
        _t0 = time.perf_counter()
        log.info("[STARTUP] ▶ build_startup_status_markdown inicio")
        snapshot = await self.collect_startup_snapshot_48h(load_ready=load_ready)
        log.info("[STARTUP] ⏱ collect_snapshot=%.1fs", time.perf_counter() - _t0)
        snapshot["profile_changes"] = profile_changes or []
        active_plan = _get_active_training_plan(self.user_profile)
        snapshot["plan_assigned"] = bool(active_plan)
        if active_plan:
            snapshot["plan_recommendation"] = _build_startup_plan_recommendation(active_plan)
            snapshot["daily_plan_decision"] = _compute_daily_plan_adjustment(snapshot, active_plan) or {}
        md = _build_proactive_status_markdown(snapshot)
        log.info("[STARTUP] ✓ total=%.1fs", time.perf_counter() - _t0)
        return md

    async def build_onboarding_mcp_enrichment(self) -> dict:
        """Obtiene datos MCP utiles para enriquecer la base inicial del atleta."""
        personal = await self.fetch_garmin_personal_data()
        startup = await self.collect_startup_snapshot_48h()
        return {
            "personal": personal,
            "startup_48h": startup,
        }

    def today_ia_tokens(self) -> int:
        """Tokens consumidos hoy con el proveedor de IA del atleta (ia_provider); 0 si no se pueden leer."""
        try:
            row = _storage.get_ia_provider() or {}
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            log.debug("No se pudo leer el consumo de tokens: %s", exc)
            return 0
        return int((row.get("daily_tokens") or {}).get(date.today().isoformat(), 0) or 0)

    def _profile_context_block(self) -> str:
        """Bloque "Perfil del usuario" (datos personales, objetivo y salud) para cualquier prompt.

        Patologías y lesiones por separado (26/10). Si las patologías mencionan diabetes, detrás va su protocolo
        (prompts/diabetes/), según la medicación; sin patologías, el atleta es sano (aunque tenga lesiones) y se dice.
        """
        p = self.user_profile.get("personal", {})
        g = self.user_profile.get("goals", {})
        h = self.user_profile.get("health", {})
        lines = []
        if p.get("name"):          lines.append(f"- Nombre: {p['name']}")
        if p.get("age"):           lines.append(f"- Edad: {p['age']} años")
        if p.get("gender"):        lines.append(f"- Género: {p['gender']}")
        if p.get("weight_kg"):     lines.append(f"- Peso: {p['weight_kg']} kg")
        if p.get("height_cm"):     lines.append(f"- Altura: {p['height_cm']} cm")
        if g.get("primary"):       lines.append(f"- Deporte principal: {g['primary']}")
        if g.get("weekly_training_hours"): lines.append(f"- Horas de entrenamiento/semana: {g['weekly_training_hours']}")
        if g.get("target_race"):   lines.append(f"- Carrera/evento objetivo: {g['target_race']}")
        if g.get("target_race_date"):
            race_d = _parse_iso_date_safe(g.get("target_race_date"))
            if race_d is not None:
                days_left = (race_d - date.today()).days
                when = f"faltan {days_left} días" if days_left >= 0 else "YA PASÓ: pedir nuevo objetivo"
                lines.append(f"- Fecha del evento: {race_d.strftime('%d/%m/%Y')} ({when})")
            else:
                lines.append(f"- Fecha del evento: {g['target_race_date']}")
        if g.get("target_time"):   lines.append(f"- Tiempo objetivo: {g['target_time']}")
        pathologies = h.get("pathologies") or []
        diabetes = _diabetes.label(h)
        if pathologies:            lines.append(f"- Patologías: {', '.join(pathologies)}")
        elif lines and not diabetes:
            lines.append("- Patologías: ninguna declarada (atleta sano)")
        if diabetes:               lines.append(f"- Diabetes: {diabetes}")
        injuries = h.get("injuries", [])
        if injuries:               lines.append(f"- Lesiones: {', '.join(injuries)}")
        if h.get("notes"):         lines.append(f"- Notas de salud: {h['notes']}")
        protocol = {"insulina": "diabetes/insulina", "sin_insulina": "diabetes/sin_insulina"}.get(_diabetes.protocol(h) or "")
        protocol_block = ("\n\n" + _render_prompt(protocol) + "\n") if protocol else ""
        return (("\n\n## Perfil del usuario\n" + "\n".join(lines) + "\n") if lines else "") + protocol_block

    def _load_and_anchors_context_block(self) -> str:
        """Bloques "Estado de carga actual" y "Umbrales" desde el perfil (sin llamadas a Garmin).

        Si el perfil está incompleto o mal formado, se omite el bloque: nunca debe impedir la respuesta.
        """
        try:
            return self._build_load_and_anchors_context_block()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            log.debug("contexto de carga/umbrales no disponible: %s", exc)
            return ""

    def _build_load_and_anchors_context_block(self) -> str:
        out = ""
        series = _load_metrics_series()
        if series:
            load = _load_metrics_view(self.user_profile, series)
            latest = load.get("latest") or {}
            ranges = load.get("ranges") or {}
            weekly = load.get("weekly") or {}
            if latest:
                _zone_ctx = _load_metrics.form_zone(float(latest.get("tsb") or 0), float(latest.get("ctl") or 0))
                out += (
                    "\n\n## Estado de carga actual (Kairos)\n"
                    f"- Fecha: {latest.get('date')} · TSS del día {float(latest.get('tss') or 0):.1f}\n"
                    f"- Estado físico (CTL) {float(latest.get('ctl') or 0):.1f} · Fatiga (ATL) {float(latest.get('atl') or 0):.1f}"
                    f" · Forma (TSB) {float(latest.get('tsb') or 0):+.1f}\n"
                    f"- Zona de Forma (Friel, % del CTL): {_zone_ctx['emoji']} {_zone_ctx['label']} ({_zone_ctx['form_pct']:+.0f} %)"
                    f" · estado del motor diario: {load.get('status') or 'neutral'} ({load.get('action') or ''})\n"
                    f"- Rango habitual de Forma (informativo, no decide): {ranges.get('tsb_low')} a {ranges.get('tsb_high')}"
                    f" · ATL máximo habitual {ranges.get('atl_high')}\n"
                    f"- TSS de la semana en curso {float(weekly.get('current_tss') or 0):.1f}"
                    f" (semana previa {float(weekly.get('previous_tss') or 0):.1f})\n"
                )
        perf = (self.user_profile or {}).get("performance") or {}
        today_iso = date.today().isoformat()
        anchors = []
        hr_thr, _, _ = _resolve_hr_threshold_bpm(self.user_profile)
        if hr_thr:
            anchors.append(f"- Umbral de FC (LTHR): {int(round(float(hr_thr)))} lpm")
        for sport, label in (("running", "carrera"), ("cycling", "ciclismo")):
            zones, source = _resolve_hr_zones(self.user_profile, sport)
            if zones:
                anchors.append(
                    f"- Zonas de FC de {label} [{source}]: "
                    + " · ".join(f"{name} < {hi + 1}" if lo <= 0 else f"{name} {lo}–{hi}" for name, lo, hi in zones)
                    + " lpm"
                )
        tp_thr = _resolve_tp_hr_threshold_for_date(self.user_profile, today_iso)
        if tp_thr and (not hr_thr or int(tp_thr) != int(hr_thr)):
            anchors.append(f"- Umbral de FC de referencia de TrainingPeaks (hrTSS): {int(tp_thr)} lpm")
        pace = _resolve_running_threshold_pace_for_date(self.user_profile, today_iso) or _resolve_running_threshold_pace_sec_per_km(self.user_profile)
        if pace:
            anchors.append(f"- Ritmo umbral de carrera: {int(pace // 60)}:{int(pace % 60):02d} min/km")
        if perf.get("cycling_ftp"):
            anchors.append(f"- FTP de ciclismo: {int(float(perf['cycling_ftp']))} W")
        if anchors:
            out += "\n\n## Umbrales\n" + "\n".join(anchors) + "\n"
        return out

    def _plan_context_block(self) -> str:
        """Bloque "Plan activo" con la sesión de hoy y las dos siguientes (caché por sesión)."""
        if not hasattr(self, "_active_plan_cache"):
            try:
                self._active_plan_cache = _get_active_training_plan(self.user_profile)
            except (RuntimeError, ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
                log.debug("plan context: no disponible: %s", exc)
                self._active_plan_cache = None
        plan = self._active_plan_cache
        if not isinstance(plan, dict) or not plan:
            return "\n\n## Plan activo\n- No hay plan activo.\n"
        lines = [f"- {plan.get('title') or 'Plan activo'}"]
        for offset, label in ((0, "Hoy"), (1, "Mañana"), (2, "Pasado mañana")):
            d = date.today() + timedelta(days=offset)
            row = _get_planned_session_for_date(plan, d.isoformat())
            if not row:
                continue
            session_type = str(row.get("session_type") or "sesión")
            if session_type == "rest" or int(row.get("duration_min") or 0) <= 0:
                desc = "descanso"
            else:
                desc = f"{session_type}, {int(row.get('duration_min') or 0)} min, {_session_intensity_label(row, self.user_profile)}".strip(", ")
                notes = str(row.get("notes") or "").strip()
                if notes:
                    desc += f" — {notes[:160]}"
            lines.append(f"- {label} ({d.strftime('%d/%m/%Y')}): {desc}")
        return "\n\n## Plan activo\n" + "\n".join(lines) + "\n"

    def _build_system_prompt(self) -> str:
        """Construye el system prompt incluyendo la fecha actual y el perfil del usuario."""
        today_str = date.today().isoformat()
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        date_context = ("\n\n" + _render_prompt("system/date", hoy=today_str, ayer=yesterday_str)
                        + "\n\n" + _calendar_block() + "\n")
        profile_context = self._profile_context_block() + self._load_and_anchors_context_block() + self._plan_context_block()

        # Incluir resúmenes de sesiones anteriores para memoria a largo plazo
        memory_context = ""
        summaries = _load_session_summaries() if _session_memory_enabled() else []
        if summaries:
            recent = summaries[-3:]  # últimas 3 sesiones
            _MAX_SUMMARY = 350  # caracteres máximos por resumen
            texts = [(s["date"], str(s.get("summary") or "").strip()) for s in recent]
            lines = "\n".join(
                f"- **{d}**: {t[:_MAX_SUMMARY]}{'…' if len(t) > _MAX_SUMMARY else ''}"
                for d, t in texts
                if t
            )
            if lines:
                memory_context = "\n\n" + _render_prompt("system/memory", sesiones=lines) + "\n"

        kb_context = ""
        if self.knowledge_sources:
            kb_files = ", ".join(self.knowledge_sources)
            kb_context = "\n\n" + _render_prompt("system/knowledge", fuentes=kb_files) + "\n"

        return self.system_prompt + date_context + profile_context + memory_context + kb_context

    async def _handle_internal_tool(self, tool_name: str, arguments: dict) -> str:
        """Despacha herramientas internas kairos_* sin llamar al servidor MCP."""
        args = arguments if isinstance(arguments, dict) else {}
        if tool_name == _tool_groups.REQUEST_TOOL:
            group = str(args.get("grupo") or "")
            if group not in _tool_groups.GROUPS:
                return json.dumps({"error": f"Grupo '{group}' no existe.", "grupos": sorted(_tool_groups.GROUPS)},
                                  ensure_ascii=False)
            groups = getattr(self, "_turn_tool_groups", None)
            if groups is not None and groups:
                groups.add(group)
            log.info("[TOOL] el LLM pide el grupo %s", group)
            return json.dumps({"ok": True, "grupo": group, "herramientas": list(_tool_groups.GROUPS[group]),
                               "nota": "Ya las tienes disponibles: llámalas en tu siguiente paso."},
                              ensure_ascii=False)
        if tool_name == "kairos_load_trends":
            return _kairos_load_trends(self.user_profile, weeks_back=int(args.get("weeks_back") or 8))
        elif tool_name == "kairos_correlate":
            return _kairos_correlate(
                self.user_profile,
                metric_a=str(args.get("metric_a") or "tss"),
                metric_b=str(args.get("metric_b") or "tsb"),
                weeks_back=int(args.get("weeks_back") or 8),
            )
        elif tool_name == "kairos_weekly_sport_breakdown":
            return await _kairos_weekly_sport_breakdown(
                self.mcp_session,
                weeks_back=int(args.get("weeks_back") or 4),
                sport_type=str(args.get("sport_type") or ""),
            )
        else:
            return json.dumps({"error": f"Herramienta interna '{tool_name}' no reconocida."}, ensure_ascii=False)

    def _build_messages(self, user_message: str) -> list[dict]:
        """Construye el array de mensajes para la llamada al LLM.
        Limita el historial a los últimos 6 turnos (3 pares user/assistant)
        para mantener el contexto razonable sin consumir tokens innecesarios.
        """
        messages = [{"role": "system", "content": self._build_system_prompt()}]
        rag_context = _build_athlete_knowledge_context(user_message, self.knowledge_chunks)
        if rag_context:
            messages.append({"role": "system", "content": rag_context})
        previous = self.previous_session_questions
        if previous:
            messages.append({"role": "system", "content": _render_prompt(
                "system/previous_session", preguntas="\n".join(f"- {q[:200]}" for q in previous)
            )})
        # Solo los últimos 6 mensajes del historial de esta sesión (3 intercambios)
        messages.extend(self.conversation_history[-6:])
        messages.append({"role": "user", "content": user_message})
        return messages

    async def _finalize_chat_reply(
        self,
        user_message: str,
        assistant_reply: str,
        route: str,
    ) -> str:
        """Persiste el turno y emite hook post-mensaje de forma uniforme."""
        transparency_events = consume_tool_transparency_events()
        if transparency_events:
            assistant_reply = (
                _build_mcp_error_warning_markdown(transparency_events)
                + assistant_reply
                + _build_mcp_transparency_markdown(transparency_events)
            )

        self.conversation_history.append({"role": "user", "content": user_message})
        self.conversation_history.append({"role": "assistant", "content": assistant_reply})
        _save_history_entries([("user", user_message), ("assistant", assistant_reply)])
        self._log_turn_stats(route)
        return assistant_reply

    def _log_turn_stats(self, route: str) -> None:
        """Una línea por pregunta en agent.log: tiempo total y consultas a Supabase (28/10, pendiente 8)."""
        db = _storage.db_stats()
        started = getattr(self, "_turn_started", None)
        total = f"{time.perf_counter() - started:.1f}s" if started else "?"
        detail = ", ".join(f"{name}×{n}" for name, n in sorted(db["by_function"].items(), key=lambda kv: -kv[1]))
        log.info("[TURNO] ruta=%s total=%s supabase=%d consultas %.2fs, %d desde la caché (%s)", route, total, db["calls"],
                 db["seconds"], db["cache_hits"], detail)

    def _log_chat_error(self, stage: str, error: Exception, extra: dict | None = None) -> None:
        """Registra un error de una etapa del chat (planificación, LLM, tool…) sin interrumpir la respuesta."""
        log.warning(
            "chat error stage=%s provider=%s %s: %s%s",
            stage,
            getattr(self, "provider", "unknown"),
            type(error).__name__,
            error,
            f" extra={extra}" if extra else "",
        )

    def _retry_findings(self, text: str, reference: str = "", decision: str | None = None) -> list[_sensors.Finding]:
        """Sensores que piden reintento, con las zonas y el ritmo umbral vigentes del perfil."""
        zones, _ = _resolve_hr_zones(self.user_profile, "running")
        zones_cycling, _ = _resolve_hr_zones(self.user_profile, "cycling")
        pace = _resolve_running_threshold_pace_for_date(self.user_profile, date.today().isoformat())
        return _sensors.findings_to_retry(text, zones, zones_cycling, pace, reference=reference, decision=decision,
                                          today=date.today())

    def _has_insulin_diabetes(self) -> bool:
        """Diabetes con riesgo de hipoglucemia (DT1, insulina, sulfonilureas o glinidas): nota glucémica S16."""
        return _diabetes.protocol((self.user_profile or {}).get("health")) == "insulina"

    async def _apply_output_sensors(
        self, text: str, context_messages: list, kind: str, decision: str | None = None
    ) -> str:
        """Pasa los sensores al texto del LLM antes de enseñarlo (docs/Harness.md §6).

        Corrige sin LLM las fechas ISO y la nota glucémica; si otro sensor falla, devuelve al LLM las pistas
        en una sola llamada más (sin tools) y se queda con la versión con menos fallos. Lo que siga fallando
        se avisa al pie y queda en el log. `decision` es la del motor diario, solo si la respuesta es para hoy.
        """
        # Referencia para S14: todo lo que recibió el LLM (contexto, datos deterministas y resultados de tools).
        reference = "\n".join(str(m.get("content") or "") for m in context_messages if isinstance(m, dict))
        text = _sensors.fix_iso_dates(text)
        findings = self._retry_findings(text, reference, decision)
        if not findings or not text.strip():
            return _sensors.add_glucose_note(text, self._has_insulin_diabetes())
        log.info("[SENSOR] %s: %s", kind, " | ".join(f"{f.sensor} {f.message}" for f in findings))
        # El reintento ve los mensajes de sistema y de usuario y su propia respuesta, no los resultados de tools.
        base = [
            m for m in context_messages
            if isinstance(m, dict) and (m.get("role") in {"system", "user"} or (m.get("role") == "assistant" and not m.get("tool_calls")))
        ]
        retry_messages = base + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": _render_prompt(
                "system/sensor_correction", fallos="\n".join(f"- {f.hint or f.message}" for f in findings)
            )},
        ]
        try:
            _t0 = time.perf_counter()
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=retry_messages,
                    **({
                        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
                    } if getattr(self, "provider", "") == "nvidia" else {}),
                ),
                timeout=_llm_timeout_seconds(),
            )
            log.info("[SENSOR] %s reintento elapsed=%.1fs", kind, time.perf_counter() - _t0)
            if getattr(response, "usage", None):
                p_toks = getattr(response.usage, "prompt_tokens", 0) or 0
                c_toks = getattr(response.usage, "completion_tokens", 0) or 0
                self.total_prompt_tokens += p_toks
                self.total_completion_tokens += c_toks
                _add_ia_tokens(p_toks + c_toks)
            fixed = _strip_simulated_user_turn(str(response.choices[0].message.content or "").strip())
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, asyncio.TimeoutError, KeyError, IndexError,
                APIError) as exc:
            # APIError: caída, 429 o 503 del proveedor (28/10: antes se escapaba y cerraba Kairos).
            self._log_chat_error(stage=f"sensor_retry_{kind}", error=exc)
            fixed = ""
        if fixed:
            fixed = _sensors.fix_iso_dates(fixed)
            remaining = self._retry_findings(fixed, reference, decision)
            if len(remaining) < len(findings):
                text, findings = fixed, remaining
        text = _sensors.add_glucose_note(text, self._has_insulin_diabetes())
        if findings:
            log.warning("[SENSOR] %s sin corregir: %s", kind, " | ".join(f"{f.sensor} {f.message}" for f in findings))
            text += f"\n\n> {_sensors.NOTICE_HEADER}: revisa esto, no cuadra con tus datos:\n" + "\n".join(
                f"> - {_sensors.user_notice(f)}" for f in findings
            )
        return text

    async def _today_frame_for_loop(self, prefetch_today: dict | None = None) -> tuple[str, str | None]:
        """Marco de la sesión de hoy y decisión del motor para el bucle con tools (G15).

        Los mismos datos y el mismo motor que la ruta de estado del día. Si algo falla, el bucle sigue sin marco.
        """
        try:
            snapshot = await self.collect_startup_snapshot_48h(prefetch_today=prefetch_today)
            decision = _compute_daily_plan_adjustment(snapshot, _get_active_training_plan(self.user_profile)) or {}
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, KeyError) as exc:
            self._log_chat_error(stage="today_frame_for_loop", error=exc)
            return "", None
        frame = _build_today_session_frame(decision, self.user_profile)
        log.info("[G15] marco de hoy para el bucle: decision=%s", decision.get("decision") or "—")
        return frame, (decision.get("decision") if frame else None)

    async def _augment_with_llm_coaching_if_needed(
        self,
        user_message: str,
        deterministic_reply: str,
        route: str,
        daily_plan_decision: dict | None = None,
        always: bool = False,
        prompt: str | None = None,
        horizon: str | None = None,
    ) -> str:
        """Añade una interpretación de coaching por LLM cuando el usuario la pide (o siempre, con `always`).

        `prompt` sustituye las instrucciones de formato (por defecto, readiness o brief) y `horizon` el horizonte
        deducido de la pregunta (la tendencia de sueño habla de un periodo, no de hoy o mañana).

        `daily_plan_decision` es la del motor diario (ruta de estado del día). Si la respuesta es para hoy,
        el LLM recibe el marco de la sesión de hoy (G12) y el sensor S15 comprueba que lo respeta.

        Mantiene el bloque determinista como fuente de verdad y limita el LLM
        a interpretar/recomendar usando SOLO esos datos.
        """
        # La capa LLM se activa solo si el usuario pide explicitamente
        # interpretacion/recomendacion de coaching.
        if not always and not _is_coaching_recommendation_intent(user_message):
            return deterministic_reply

        try:
            is_readiness = route == "daily_readiness"
            horizon_text = horizon or _coaching_horizon(user_message)
            is_today = horizon is None and horizon_text.startswith("HOY")
            today_decision = (daily_plan_decision or {}).get("decision") if is_today else None
            session_frame = _build_today_session_frame(daily_plan_decision, self.user_profile) if is_today else ""
            coaching_messages = [
                {
                    "role": "system",
                    "content": (
                        _render_prompt("coaching/system")
                        + " " + _render_prompt(prompt or ("coaching/readiness" if is_readiness else "coaching/brief"))
                        + "\n\n" + _calendar_block()
                        # Reglas de entrenador: las mismas que el system prompt (prompts/coaching_rules.md).
                        + "\n\n" + _load_coaching_rules()
                        + self._profile_context_block()
                        + self._load_and_anchors_context_block()
                        + self._plan_context_block()
                    ),
                },
                {
                    "role": "user",
                    "content": _render_prompt(
                        "coaching/user",
                        pregunta=user_message,
                        ruta=route,
                        horizonte=horizon_text,
                        datos=deterministic_reply + ("\n\n" + session_frame if session_frame else ""),
                    ),
                },
            ]

            _t0_coaching = time.perf_counter()
            log.info("[LLM] ▶ llamada route=%s modelo=%s tipo=coaching", route, self.model)
            coaching_response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=coaching_messages,
                    **({
                        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
                    } if getattr(self, "provider", "") == "nvidia" else {}),
                ),
                timeout=_llm_timeout_seconds(),
            )
            log.info("[LLM] ⏱ coaching elapsed=%.1fs", time.perf_counter() - _t0_coaching)

            if getattr(coaching_response, "usage", None):
                u = coaching_response.usage
                p_toks = getattr(u, "prompt_tokens", 0) or 0
                c_toks = getattr(u, "completion_tokens", 0) or 0
                self.total_prompt_tokens += p_toks
                self.total_completion_tokens += c_toks
                _add_ia_tokens(p_toks + c_toks)

            coaching_text = _strip_simulated_user_turn(
                str((coaching_response.choices[0].message.content or "")).strip()
            )
            coaching_text = await self._apply_output_sensors(
                coaching_text, coaching_messages, "coaching", decision=today_decision
            )
            if not coaching_text:
                return (
                    deterministic_reply
                    + "\n\n## 🧠 Interpretación de coaching\n"
                    + "- No pude generar una recomendación de coaching en este intento (respuesta vacía del modelo)."
                    + "\n\n_Fase coaching por LLM solicitada; sin resultado utilizable en esta ejecución._"
                )

            return (
                deterministic_reply
                + "\n\n## 🧠 Interpretación de coaching\n"
                + coaching_text
                + "\n\n_Fase coaching por LLM sobre datos deterministas (sin recalcular métricas)._"
            )
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, asyncio.TimeoutError, json.JSONDecodeError, KeyError,
                APIError) as exc:
            # APIError: caída, 429 o 503 del proveedor (28/10: antes se escapaba y cerraba Kairos).
            log.warning("No se pudo completar capa LLM de coaching (route=%s): %s", route, exc or type(exc).__name__)
            return (
                deterministic_reply
                + "\n\n## 🧠 Interpretación de coaching\n"
                + "- No pude generar una recomendación de coaching en este intento (timeout/error del modelo)."
                + "\n\n_Fase coaching por LLM solicitada; mantengo solo el bloque factual como fuente de verdad._"
            )

    async def chat(self, user_message: str) -> str:
        """
        Procesa un mensaje del usuario y devuelve la respuesta del agente.
        Gestiona automáticamente las llamadas a herramientas de Garmin.

        Las lecturas de Supabase de la pregunta pasan por una caché que se abre aquí y se cierra al terminar,
        también si la pregunta falla (pendiente 8b, 28/10).
        """
        self._turn_started = time.perf_counter()
        _storage.reset_db_stats()
        _storage.open_read_cache()
        try:
            return await self._answer(user_message)
        finally:
            _storage.close_read_cache()

    async def _answer(self, user_message: str) -> str:
        """Responde una pregunta: rutas deterministas, rutas mixtas o bucle con tools (lo llama `chat`)."""
        if not hasattr(self, "tool_router") or not isinstance(getattr(self, "tool_router", None), ToolRouter):
            self.tool_router = ToolRouter()

        impossible = _find_impossible_date(user_message)
        if impossible:
            return await self._finalize_chat_reply(
                user_message,
                f"La fecha {impossible} no existe en el calendario. ¿Qué día querías consultar? (formato DD/MM/AAAA)",
                route="invalid_date",
            )

        reset_tool_transparency_events()
        _prefetch_cache: dict = {}  # poblado por el pre-fetch si user_date == hoy
        route_key = self.tool_router.route_key(
            user_message,
            self.conversation_history,
            self.user_profile,
        )

        # Ruta determinista para estado de plan: evita alucinaciones del LLM
        # cuando la pregunta es "¿tengo plan?" o "¿cuál es mi plan?".
        if route_key == "config_options":
            assistant_reply = _build_config_options_markdown()
            return await self._finalize_chat_reply(user_message, assistant_reply, route="config_options")

        # Ruta determinista para estado de plan: evita alucinaciones del LLM
        # cuando la pregunta es "¿tengo plan?" o "¿cuál es mi plan?".
        if route_key == "plan_status":
            assistant_reply = _build_training_plan_status_markdown(self.user_profile)
            return await self._finalize_chat_reply(user_message, assistant_reply, route="plan_status")

        if route_key == "goal_status":
            assistant_reply = _build_goal_status_markdown(self.user_profile)
            return await self._finalize_chat_reply(user_message, assistant_reply, route="goal_status")

        # Ruta determinista para TSS semanal: evita ambigüedades del LLM
        # y usa semana natural solicitada (o actual) con actividades reales de Garmin.
        if route_key == "week_tss":
            assistant_reply = await _build_current_week_tss_markdown(self.mcp_session, self.user_profile, user_message)
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="week_tss")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="week_tss")

        # Ruta determinista para actividades semanales: evita formatos inconsistentes
        # y fuerza salida homogénea user-friendly.
        if route_key == "week_activities":
            assistant_reply = await _build_week_activities_markdown(self.mcp_session, user_message)
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="week_activities")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="week_activities")

        # Ruta determinista para tendencia de carga/fatiga (ATL/CTL/TSB).
        # Evita timeouts del LLM en consultas de tendencia histórica.
        if route_key == "load_trend":
            assistant_reply = _build_load_trend_markdown(self.user_profile, user_message)
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="load_trend")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="load_trend")

        # Ruta determinista para FC umbral (LTHR): perfil primero y fallback MCP rápido.
        if route_key == "hr_threshold":
            latest_profile = _load_user_profile()
            if isinstance(latest_profile, dict) and latest_profile:
                self.user_profile = latest_profile
            assistant_reply = await _build_hr_threshold_profile_markdown(self.mcp_session, self.user_profile)
            return await self._finalize_chat_reply(user_message, assistant_reply, route="hr_threshold")

        # Ruta determinista para ritmo umbral de running.
        # Carga el perfil más reciente para evitar responder con valores obsoletos.
        if route_key == "running_threshold":
            latest_profile = _load_user_profile()
            if isinstance(latest_profile, dict) and latest_profile:
                self.user_profile = latest_profile
            assistant_reply = _build_running_threshold_profile_markdown(self.user_profile)
            return await self._finalize_chat_reply(user_message, assistant_reply, route="running_threshold")

        # Ruta determinista para estado de carga/fatiga de hoy con regla aplicada.
        # Evita que consultas de TSS/ATL/CTL/TSB caigan en factual diario genérico.
        if route_key == "today_load_status":
            assistant_reply = _build_today_load_status_markdown(self.user_profile)
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="today_load_status")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="today_load_status")

        if route_key == "period_activities":
            assistant_reply = await _build_period_activities_markdown(self.mcp_session, user_message)
            return await self._finalize_chat_reply(user_message, assistant_reply, route="period_activities")

        if route_key == "sleep_night":
            assistant_reply = await _build_sleep_night_markdown(self.mcp_session, _resolve_sleep_night(user_message))
            return await self._finalize_chat_reply(user_message, assistant_reply, route="sleep_night")

        # Tendencia de sueño: ruta mixta (tabla calculada en Python y, siempre, la lectura del LLM sobre ella).
        if route_key == "sleep_trend":
            window = _resolve_sleep_window(user_message) or 7
            assistant_reply = await _build_sleep_trend_markdown(self.mcp_session, window)
            assistant_reply = await self._augment_with_llm_coaching_if_needed(
                user_message, assistant_reply, route="sleep_trend", always=True, prompt="coaching/sleep_trend",
                horizon=f"las últimas {window} noches frente a las {window} anteriores",
            )
            return await self._finalize_chat_reply(user_message, assistant_reply, route="sleep_trend")

        # Ruta determinista para detalle de entrenamiento por día/fecha.
        # Evita timeouts del LLM en preguntas factuales como "entrenamiento del martes".
        if route_key == "activity_details":
            assistant_reply = await _build_mcp_factual_query_markdown(
                self.mcp_session,
                self.user_profile,
                user_message,
            )
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="activity_details")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="activity_details")

        if route_key == "recent_tss_by_sport":
            assistant_reply = await _build_recent_tss_by_sport_markdown(
                self.mcp_session,
                user_message,
            )
            return await self._finalize_chat_reply(user_message, assistant_reply, route="recent_tss_by_sport")

        # Ruta determinista para consultas de conteo de actividades en DB
        _message_lc = (user_message or "").lower()
        _count_query = bool(re.search(
            r'\b(cu[aá]ntas?\s+actividades|total\s+actividades|actividades\s+registradas|actividades\s+en\s+(la\s+)?(base|bbdd|db)|cuantos\s+entrenos)\b',
            _message_lc
        ))
        if not _count_query and "actividades" in _message_lc and (
            "base de datos" in _message_lc or "db" in _message_lc or "bbdd" in _message_lc
        ):
            _count_query = True
        if _count_query:
            # Fuente: activity_tss (una fila por actividad con su TSS), no la serie diaria de 120 días.
            _rows = list(_load_stored_activity_tss("2000-01-01").values())
            _by_sport: dict[str, int] = {}
            for _r in _rows:
                _label = _get_activity_name_es(str(_r.get("sport") or "")) or str(_r.get("sport") or "otro")
                _by_sport[_label] = _by_sport.get(_label, 0) + 1
            _dates = sorted(str(_r.get("start_date") or "") for _r in _rows if _r.get("start_date"))

            def _dmy(iso: str) -> str:
                _d = _parse_iso_date_safe(iso)
                return _d.strftime("%d/%m/%Y") if _d else iso

            _range = f"{_dmy(_dates[0])} → {_dmy(_dates[-1])}" if _dates else "sin datos"
            assistant_reply = "\n".join([
                "## 🧭 Resumen",
                f"Tengo registradas **{len(_rows)} actividades** con su TSS en la base de datos de Kairos.",
                "",
                "## 📊 Métricas clave",
                "| Dato | Valor |",
                "|---|---:|",
                f"| Actividades con TSS | {len(_rows)} |",
                f"| Días con entrenamiento | {len(set(_dates))} |",
                f"| Rango cubierto | {_range} |",
                *[f"| {sport} | {n} |" for sport, n in sorted(_by_sport.items(), key=lambda kv: -kv[1])],
                "",
                "## ✅ Recomendación",
                "- Es el histórico con el que Kairos calcula tu Estado físico, Fatiga y Forma (línea base desde la fecha inicial).",
                "",
                "## 🎯 Próximo paso",
                "- Si quieres, te listo los TSS de las últimas actividades de un deporte.",
                "- Fuente: respuesta determinista (activity_tss, sin LLM).",
            ])
            return await self._finalize_chat_reply(user_message, assistant_reply, route="db_count")

        # Ruta MCP-first para consultas factuales de métricas diarias.
        # Evita pasar por LLM cuando la pregunta se puede responder con
        # datos directos de Garmin Connect.
        if route_key == "mcp_factual":
            assistant_reply = await _build_mcp_factual_query_markdown(
                self.mcp_session,
                self.user_profile,
                user_message,
            )
            assistant_reply = await self._augment_with_llm_coaching_if_needed(user_message, assistant_reply, route="mcp_factual")
            return await self._finalize_chat_reply(user_message, assistant_reply, route="mcp_factual")

        # Ruta determinista para readiness diario: evita respuestas del LLM
        # con métricas inventadas y fuerza snapshot real de Garmin.
        if route_key == "daily_readiness":
            snapshot = await self.collect_startup_snapshot_48h(prefetch_today=_prefetch_cache)
            snapshot["profile_changes"] = []
            active_plan = _get_active_training_plan(self.user_profile)
            snapshot["plan_assigned"] = bool(active_plan)
            snapshot["daily_plan_decision"] = _compute_daily_plan_adjustment(snapshot, active_plan) or {}
            if active_plan:
                snapshot["plan_recommendation"] = _build_startup_plan_recommendation(active_plan)
            assistant_reply = _build_proactive_status_markdown(snapshot)
            assistant_reply = await self._augment_with_llm_coaching_if_needed(
                user_message, assistant_reply, route="daily_readiness",
                daily_plan_decision=snapshot.get("daily_plan_decision") or {},
            )
            return await self._finalize_chat_reply(user_message, assistant_reply, route="daily_readiness")

        # Ruta funcional de planificación: generación/actualización estructurada,
        # persistida y versionada en DB sin depender del LLM.
        if route_key == "planning":
            try:
                previous_plan_row = None
                previous_plan = None
                previous_sessions: list[dict] = []
                try:
                    previous_plan_row = _storage.get_active_training_plan()
                    previous_plan = _normalize_storage_plan_row(previous_plan_row)
                    if previous_plan and previous_plan.get("id"):
                        previous_sessions = _storage.list_training_plan_sessions(str(previous_plan.get("id")))
                except (RuntimeError, ValueError, TypeError, OSError, KeyError):
                    previous_plan = _get_active_training_plan(self.user_profile)
                    previous_sessions = []

                new_plan, new_sessions = _generate_structured_plan_payload(
                    self.user_profile,
                    user_message,
                    base_plan=previous_plan,
                    load_context=await self._build_plan_load_context(),
                )
                validation_errors = _validate_structured_plan(new_plan, new_sessions, self.user_profile)
                if validation_errors:
                    assistant_reply = (
                        "## ⚠️ No pude persistir el plan propuesto\n\n"
                        + "\n".join(f"- {err}" for err in validation_errors)
                        + "\n\nAjusta perfil/objetivo con `/perfil editar objetivo` y lo regenero."
                    )
                else:
                    wants_new = _wants_new_plan_intent(user_message)
                    if previous_plan and previous_plan.get("id") and not wants_new:
                        persisted = _storage.update_training_plan(
                            str(previous_plan.get("id")),
                            {
                                "title": new_plan.get("title"),
                                "description": new_plan.get("description"),
                                "objective": new_plan.get("objective"),
                                "difficulty": new_plan.get("difficulty"),
                                "duration_weeks": new_plan.get("duration_weeks"),
                                "status": "active",
                                "source": "agent_structured_plan",
                                "plan_data": dict(new_plan.get("plan_data") or {}),
                            },
                            sessions=new_sessions,
                            change_reason="agent_structured_adjustment",
                        )
                        persisted_plan = _normalize_storage_plan_row(persisted) or new_plan
                    else:
                        persisted = _storage.create_training_plan(
                            new_plan,
                            sessions=new_sessions,
                            change_reason="agent_structured_creation",
                        )
                        persisted_plan = _normalize_storage_plan_row(persisted) or new_plan

                    change_summary = _summarize_plan_changes(
                        previous_plan,
                        persisted_plan,
                        previous_sessions,
                        new_sessions,
                    )
                    assistant_reply = _build_structured_plan_markdown(
                        persisted_plan,
                        new_sessions,
                        change_summary,
                        profile=self.user_profile,
                    )

                    # Espejo backward-compatible en perfil.
                    persisted_plan.setdefault("target_race", (new_plan.get("plan_data") or {}).get("target_race"))
                    persisted_plan.setdefault("target_race_date", (new_plan.get("plan_data") or {}).get("target_race_date"))
                    self.user_profile["training_plan"] = persisted_plan
                    _save_user_profile(self.user_profile)
                    self.__dict__.pop("_active_plan_cache", None)  # el bloque "Plan activo" se recarga

                return await self._finalize_chat_reply(user_message, assistant_reply, route="planning")
            except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                log.warning("structured planning route failed, using goal fallback: %s", exc)
                self._log_chat_error(
                    stage="planning_route",
                    error=exc,
                    extra={"user_message": user_message},
                )
                assistant_reply = _build_goal_plan_fallback(self.user_profile)
                return await self._finalize_chat_reply(user_message, assistant_reply, route="planning_fallback")

        # Ruta directa para récords personales: evita respuestas de "sin acceso"
        # y asegura que se entreguen distancia + marca desde la primera respuesta.
        force_personal_records = route_key == "personal_records"
        if force_personal_records:
            try:
                available_tool_names = {
                    (item.get("function") or {}).get("name")
                    for item in (self.tools_schema or [])
                    if isinstance(item, dict)
                }
                records_tool = None
                if "get_personal_record" in available_tool_names:
                    records_tool = "get_personal_record"
                elif "get_personal_records" in available_tool_names:
                    records_tool = "get_personal_records"

                if records_tool:
                    records_raw = await call_tool(self.mcp_session, records_tool, {})
                    records_compact = _compact_tool_result(records_raw, records_tool)
                    if records_compact and records_compact != "(sin datos)" and not _is_no_data_result(records_raw):
                        records_sport = _detect_personal_records_sport_intent(user_message, self.conversation_history)
                        assistant_reply = _build_personal_records_markdown(records_compact, preferred_sport=records_sport)
                        return await self._finalize_chat_reply(user_message, assistant_reply, route="personal_records")
            except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                log.debug("personal records route falló: %s", exc)
                self._log_chat_error(
                    stage="personal_records_route",
                    error=exc,
                    extra={"user_message": user_message},
                )

        # Mensajes del LLM con su prompt de sistema (perfil, carga, plan…): solo las preguntas que llegan aquí los
        # usan; antes se construían también para las rutas deterministas, con una lectura de Supabase de más (8b).
        messages = self._build_messages(user_message)

        # Pre-fetch proactivo: si el usuario menciona una fecha explícita,
        # resolver y cargar la actividad + contexto completo ANTES del bucle LLM.
        analysis_block_for_rescue = ""
        prefetch_date_for_rescue = ""
        user_date = _extract_iso_date_from_text(user_message)
        is_week_range_query = bool(re.search(r"\b(semana|week)\b", (user_message or "").lower()))

        # "Mi última actividad / mi último trail…" sin fecha: la actividad concreta (28/10, `_resolve_last_activity`).
        last_activity_id = None
        if not user_date and not is_week_range_query:
            user_date, last_activity_id = await _resolve_last_activity(self.mcp_session, user_message)

        if user_date and not is_week_range_query:
            # Intento 1: get_activities_fordate para la fecha exacta
            pre_id = last_activity_id
            if pre_id is None:
                try:
                    _raw_date_acts = await call_tool(
                        self.mcp_session, "get_activities_fordate",
                        {"date": user_date},
                    )
                    _acts_day = _extract_activities_list(_raw_date_acts)
                    if _acts_day:
                        _first = _acts_day[0]
                        pre_id = (_first.get("id") or _first.get("activityId")
                                  or _first.get("activity_id"))
                        if pre_id:
                            pre_id = int(pre_id)
                except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as _e:
                    log.debug("pre_fetch get_activities_fordate fallback: %s", _e)

            # Intento 2: paginación por fecha si el anterior falló
            if pre_id is None:
                pre_id = await _find_activity_id_by_date(self.mcp_session, user_date)

            log.info("pre_fetch: user_date=%s pre_id=%s", user_date, pre_id)
            if pre_id is not None:
                raw_pre = await call_tool(self.mcp_session, "get_activity", {"activity_id": pre_id})
                pre_data = _compact_tool_result(raw_pre, "get_activity")
                # Duración total de la actividad — denominador correcto para % zonas (igual que Garmin Connect)
                _act_dur_s = None
                try:
                    _act_raw_j = json.loads(raw_pre) if raw_pre else {}
                    _dur_raw = (_act_raw_j.get("duration") or _act_raw_j.get("movingDuration")
                                or _act_raw_j.get("duration_seconds"))
                    if _dur_raw is not None:
                        _act_dur_s = float(_dur_raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
                context_parts = [f"ACTIVIDAD (activityId={pre_id}, fecha={user_date}):\n{pre_data}"]

                # Fetch paralelo: body_battery, sleep (2 fechas), hrv, training_load
                _night_before = (date.fromisoformat(user_date) - timedelta(days=1)).isoformat()
                _tl_end = date.today().isoformat()
                _tl_start = (date.today() - timedelta(weeks=4)).isoformat()

                async def _safe_prefetch(tool_name: str, args: dict) -> str | None:
                    try:
                        return await call_tool(self.mcp_session, tool_name, args)
                    except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError):
                        return None

                raw_bb, raw_sleep, raw_sleep_nb, raw_hrv, raw_tl = await asyncio.gather(
                    _safe_prefetch("get_body_battery", {"start_date": user_date, "end_date": user_date}),
                    _safe_prefetch("get_sleep_data", {"date": user_date}),
                    _safe_prefetch("get_sleep_data", {"date": _night_before}),
                    _safe_prefetch("get_hrv_data", {"date": user_date}),
                    _safe_prefetch("get_training_load_trend", {"start_date": _tl_start, "end_date": _tl_end}),
                )

                # body_battery
                if raw_bb is not None:
                    log.debug(f"body_battery raw({user_date}): {(raw_bb or '')[:200]}")
                    bb_data = _compact_tool_result(raw_bb, "get_body_battery")
                    log.debug(f"body_battery compact({user_date}): {bb_data[:120] if bb_data else 'None'}")
                    if bb_data and bb_data != "(sin datos)":
                        context_parts.append(f"BODY BATTERY del {user_date}:\n{bb_data}")

                # sleep: preferir user_date, fallback night_before (ambas ya disponibles)
                _sleep_added = False
                for _sleep_date, _raw_s in ((user_date, raw_sleep), (_night_before, raw_sleep_nb)):
                    if _raw_s is None:
                        continue
                    _sd = _compact_tool_result(_raw_s, "get_sleep_data")
                    log.info(f"sleep({_sleep_date}): {_sd[:120] if _sd else 'None'}")
                    if _sd and _sd != "(sin datos)":
                        context_parts.append(f"SUENO noche previa ({_sleep_date}):\n{_sd}")
                        _sleep_added = True
                        break
                if not _sleep_added:
                    log.info("sleep: no se encontraron datos en ninguna fecha")

                # hrv
                if raw_hrv is not None:
                    hrv_data = _compact_tool_result(raw_hrv, "get_hrv_data")
                    log.info(f"hrv({user_date}): {hrv_data[:120] if hrv_data else 'None'}")
                    if hrv_data and hrv_data != "(sin datos)":
                        context_parts.append(f"HRV del {user_date}:\n{hrv_data}")

                # training_load
                if raw_tl is not None:
                    tl_data = _compact_tool_result(raw_tl, "get_training_load_trend")
                    log.debug(f"training_load: {tl_data[:80] if tl_data else 'None'}")
                    if tl_data and tl_data != "(sin datos)":
                        context_parts.append(
                            "CARGA GARMIN (carga nativa de Garmin, NO es TSS; el TSS de Kairos va en el resumen):\n"
                            f"{tl_data}"
                        )

                # Guardar en caché para collect_startup_snapshot_48h (evita re-fetch si es hoy)
                if user_date == date.today().isoformat():
                    _prefetch_cache["body_today"] = raw_bb
                    _prefetch_cache["hrv_today"] = raw_hrv

                # ── Zonas reales de FC ──────────────────────────────────────────────
                # Estrategia 1: buscar en el raw de get_activity (ya disponible, sin llamada extra)
                raw_hr_zones = None
                try:
                    _act_data = json.loads(raw_pre) if raw_pre else {}
                    _zones_in_act = _find_hr_zones_in_json(_act_data)
                    if _zones_in_act:
                        raw_hr_zones = json.dumps(_zones_in_act)
                        log.info("hr_zones: encontradas %d zonas en get_activity", len(_zones_in_act))
                    else:
                        log.info("hr_zones: get_activity no contiene datos de zonas (requiere llamada específica)")
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass

                # Estrategia 2: get_activity_hr_in_timezones (nombre real en garminconnect / garmin-mcp)
                if not raw_hr_zones:
                    for _param in ({"activity_id": pre_id}, {"activityId": pre_id}):
                        try:
                            _raw = await call_tool(self.mcp_session, "get_activity_hr_in_timezones", _param)
                            log.debug("get_activity_hr_in_timezones(%s): %s", _param, (_raw or "")[:200])
                            if not _raw or "Unknown tool" in _raw or "unknown tool" in _raw.lower():
                                log.info("hr_zones: get_activity_hr_in_timezones no disponible en este servidor MCP")
                                break  # no reintentar con otros params si la herramienta no existe
                            if _raw.strip() not in ("null", "[]", "{}", "(sin datos)", ""):
                                _parsed = _parse_hr_zones_list(_raw)
                                if _parsed:
                                    raw_hr_zones = _raw
                                    log.info("hr_zones: %d zonas via get_activity_hr_in_timezones", len(_parsed))
                                    break
                        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError):
                            break

                if raw_hr_zones:
                    context_parts.append(f"ZONAS FC (datos reales):\n{raw_hr_zones}")
                else:
                    log.info("hr_zones: NO se encontraron datos reales de zonas — usando estimación gaussiana")

                # Si tenemos zonas reales, actualizar pre_data para reemplazar estimación gaussiana
                _zones_for_predata = _parse_hr_zones_list(raw_hr_zones)
                if _zones_for_predata:
                    try:
                        _pd = json.loads(pre_data)
                        if isinstance(_pd, dict):
                            _pd.pop("zonas_fc_estimadas", None)
                            _pd.pop("nota_zonas", None)
                            _total_z = sum(float(z.get("secsInZone") or 0) for z in _zones_for_predata)
                            # Garmin Connect divide por duración total, no por suma de zonas
                            _z_denom = _act_dur_s if _act_dur_s and _act_dur_s > 0 else _total_z
                            if _total_z > 0:
                                _zr = {}
                                for _z in sorted(_zones_for_predata, key=lambda x: int(x.get("zoneNumber") or 0)):
                                    _zn = int(_z.get("zoneNumber") or 0)
                                    _zs = float(_z.get("secsInZone") or 0)
                                    _pct = round(_zs / _z_denom * 100, 1)
                                    _lo = _z.get("minHeartRateIn") or "?"
                                    _hi = _z.get("maxHeartRateIn") or "?"
                                    _zname = _z.get("zoneName") or f"Z{_zn}"
                                    _zr[f"Z{_zn}_{_zname}_{_lo}-{_hi}bpm"] = f"{_pct:.1f}% (~{int(_zs/60)} min)"
                                _pd["zonas_fc_reales_garmin"] = _zr
                                _pd["nota_zonas"] = "Zonas reales de Garmin (Tiempo en Zonas del dispositivo)."
                            pre_data = json.dumps(_pd, ensure_ascii=False, separators=(",", ":"))
                            context_parts[0] = f"ACTIVIDAD (activityId={pre_id}, fecha={user_date}):\n{pre_data}"
                            log.info("pre_data: zonas_fc_reales_garmin inyectadas (%d zonas)", len(_zones_for_predata))
                    except (TypeError, ValueError, json.JSONDecodeError, KeyError) as _ze:
                        log.debug("pre_data zone update error: %s", _ze)

                # Construir bloque de análisis pre-computado en Python

                analysis_block = _build_activity_analysis_block(
                    activity_raw=raw_pre,
                    body_battery_raw=next((p for p in context_parts[1:] if "BODY BATTERY" in p), None),
                    sleep_raw=next((p for p in context_parts if "SUENO" in p), None),
                    hrv_raw=next((p for p in context_parts if "HRV" in p), None),
                    hr_zones_raw=raw_hr_zones,
                    stored_tss_row=(
                        _load_stored_activity_tss(str(user_date)).get(_activity_id_int({"activityId": pre_id}))
                        if pre_id is not None and user_date
                        else None
                    ),
                )
                analysis_block_for_rescue = analysis_block
                prefetch_date_for_rescue = user_date

                # Eliminar del array de mensajes cualquier respuesta previa del asistente
                # que sea un análisis de actividad — evita copiar floats crudos y formato viejo.
                # Incluye tanto los headers del bloque pre-computado como el texto libre
                # que el LLM genera, para limpiar también respuestas de sesiones anteriores.
                _ANALYSIS_MARKERS = (
                    # Headers del bloque pre-computado
                    "Resumen ejecutivo", "zonas de FC", "Distribución por zonas",
                    "Plan de recuperación", "Efecto de entrenamiento",
                    "Recomendaciones para la próxima", "Training load:",
                    "Body battery:", "Hidratación recomendada",
                    # Texto libre típico del LLM en español (respuestas de sesiones anteriores)
                    "velocidad media fue", "velocidad máxima fue",
                    "ritmo medio fue", "frecuencia cardíaca media fue",
                    "efecto de entrenamiento de", "zonas de frecuencia cardíaca",
                    "carga de entrenamiento (TSS)", "elevación ganada fue",
                    "tiempo de recuperación de",
                    # Identificadores de la actividad concreta
                    user_date, str(pre_id),
                )
                messages = [
                    msg for msg in messages
                    if not (
                        msg.get("role") == "assistant"
                        and any(m in (msg.get("content") or "") for m in _ANALYSIS_MARKERS)
                    )
                ]

                # Pre-computar texto de zonas para forzar salida exacta al LLM
                _zones_direct_text = None
                if _zones_for_predata:
                    _total_zd = sum(float(z.get("secsInZone") or 0) for z in _zones_for_predata)
                    # Garmin Connect divide por duración total de la actividad, no por suma de zonas
                    _zd_denom = _act_dur_s if _act_dur_s and _act_dur_s > 0 else _total_zd
                    if _total_zd > 0:
                        _sorted_zd = sorted(_zones_for_predata, key=lambda x: int(x.get("zoneNumber") or 0))
                        _zdlines = []
                        for _zdi, _z in enumerate(_sorted_zd):
                            _zn = int(_z.get("zoneNumber") or 0)
                            _zs = float(_z.get("secsInZone") or 0)
                            _pct = round(_zs / _zd_denom * 100, 1)
                            _mins = int(_zs / 60)
                            _zname = (_GARMIN_ZONE_NAMES_ES.get(_zn)
                                      if not _z.get("zoneName") or str(_z.get("zoneName")).startswith("Z")
                                      else _z.get("zoneName"))
                            _lo_d = _z.get("minHeartRateIn") or "?"
                            _hi_d = _z.get("maxHeartRateIn") or "?"
                            # Calcular límite alto desde la siguiente zona si no está disponible
                            if _hi_d == "?" and _lo_d != "?" and _zdi + 1 < len(_sorted_zd):
                                _next_lo = _sorted_zd[_zdi + 1].get("minHeartRateIn") or "?"
                                if _next_lo != "?":
                                    _hi_d = str(int(float(_next_lo)) - 1)
                            if _lo_d != "?" and _hi_d != "?":
                                _rng = f"{_lo_d}–{_hi_d} bpm"
                            elif _lo_d != "?":
                                _rng = f">{_lo_d} bpm"
                            else:
                                _rng = ""
                            _zdlines.append(
                                f"Z{_zn} · {_zname:<14} · {_rng:<14} {_pct:5.1f}% (~{_mins} min)"
                            )
                        _zones_direct_text = "\n".join(_zdlines)

                _zones_override = (
                    "\n" + _render_prompt("activity_analysis/zones", zonas=_zones_direct_text) + "\n"
                    if _zones_direct_text else ""
                )

                _post_activity_spec = _build_post_activity_section_spec(user_date)
                _post_header = str(_post_activity_spec.get("header") or "## 🔄 Recuperación y próximas sesiones")
                _post_section_name = str(_post_activity_spec.get("section_name") or _post_header)
                _post_guidance = str(_post_activity_spec.get("guidance") or "")
                _post_plan_mode = str(_post_activity_spec.get("plan_context") or "recent")

                # Contexto del plan de entrenamiento para la sección de recuperación
                _plan_obj = _get_active_training_plan(self.user_profile)
                if _post_plan_mode == "historical":
                    _plan_ctx = "\n" + _render_prompt("activity_analysis/plan_historical") + "\n"
                elif _plan_obj:
                    _plan_title = _plan_obj.get("title") or _plan_obj.get("name") or "Plan activo"
                    _plan_ctx = "\n" + _render_prompt("activity_analysis/plan_active", plan=_plan_title) + "\n"
                else:
                    _plan_ctx = "\n" + _render_prompt("activity_analysis/plan_none") + "\n"

                messages.insert(len(messages) - 1, {
                    "role": "system",
                    "content": _render_prompt(
                        "activity_analysis/system",
                        fecha=user_date,
                        datos=analysis_block,
                        zonas=_zones_override,
                        plan=_plan_ctx,
                        cabecera_final=_post_header,
                        seccion_final=_post_section_name,
                        guia_final=_post_guidance,
                    ),
                })
            else:
                log.debug(f"Pre-fetch {user_date}: no se encontro actividad")

        # G15 (28/10): qué entrenar hoy, preguntado de forma que no llega a la ruta de estado del día. El LLM
        # recibe la decisión del motor y el marco de la sesión, y S15 comprueba que lo respeta.
        loop_decision = None
        if not analysis_block_for_rescue and _is_today_training_question(user_message):
            frame, loop_decision = await self._today_frame_for_loop(_prefetch_cache)
            if frame:
                messages.insert(len(messages) - 1, {"role": "system", "content": frame})

        _MAX_TOOL_ITER = 15
        # Metatooling (26/10): solo los grupos de tools que pide la pregunta; el LLM puede pedir más.
        self._turn_tool_groups = _tool_groups.groups_for(user_message)
        log.info("[TOOL] grupos de la pregunta: %s", ",".join(sorted(self._turn_tool_groups)) or "todos")
        # Cortacircuitos (24/10): garminconnect espera hasta 30 s por petición y aquí se corta a los 8 s; con
        # Garmin lento, cada llamada nueva se encolaba tras la abandonada y agotaba también su tiempo (5 seguidas,
        # 144 s en una pregunta). Tras 2 timeouts en la misma respuesta no se llama más a Garmin.
        _MAX_TOOL_TIMEOUTS = 2
        tool_timeouts = 0
        iteration = 0
        while True:
            iteration += 1
            if iteration > _MAX_TOOL_ITER:
                log.debug(f"Límite de {_MAX_TOOL_ITER} iteraciones de herramientas alcanzado. Abortando.")
                assistant_reply = "[Lo siento, la consulta requirió demasiadas llamadas a herramientas. Por favor, reformula tu pregunta de forma más concreta.]"
                return await self._finalize_chat_reply(user_message, assistant_reply, route="max_tool_iterations")
            try:
                _t0 = time.perf_counter()
                turn_tools = _tool_groups.select(self.tools_schema, self._turn_tool_groups) if self.tools_schema else []
                log.info("[LLM] ▶ llamada iter=%d modelo=%s route=chat msgs=%d",
                         iteration, self.model, len(messages))
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=turn_tools or None,
                        tool_choice="auto" if turn_tools else None,
                        **({
                            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
                        } if getattr(self, "provider", "") == "nvidia" else {}),
                    ),
                    timeout=_llm_timeout_seconds(),
                )
                log.info("[LLM] ⏱ iter=%d elapsed=%.1fs", iteration, time.perf_counter() - _t0)
            except asyncio.TimeoutError as api_exc:
                log.warning("[LLM] ⏱ iter=%d TIMEOUT elapsed=%.1fs (límite=%.0fs)",
                            iteration, time.perf_counter() - _t0, _llm_timeout_seconds())
                self._log_chat_error(
                    stage="llm_completion_timeout",
                    error=api_exc,
                    extra={"user_message": user_message},
                )
                timeout_msg = (
                    "La consulta ha excedido el tiempo máximo de respuesta del modelo.\n\n"
                    "Prueba una de estas opciones:\n"
                    "- Reintentar la consulta\n"
                    "- Hacer una pregunta más concreta\n"
                    "- Cambiar de proveedor o de modelo con `/proveedor`"
                )
                return await self._finalize_chat_reply(user_message, timeout_msg, route="llm_timeout")
            except Exception as api_exc:
                err_str = str(api_exc)
                self._log_chat_error(
                    stage="llm_completion",
                    error=api_exc,
                    extra={"user_message": user_message},
                )

                if "413" in err_str or "tokens_limit_reached" in err_str or "Request body too large" in err_str:
                    msg = (
                        "La consulta es demasiado extensa para el modelo actual (límite de tokens del proveedor).\n\n"
                        "Prueba con una de estas opciones:\n"
                        "- Haz una pregunta más específica y acotada (ej: *¿Cómo estoy hoy?* en lugar de *analiza 8 semanas*)\n"
                        "- Divide el análisis en pasos: primero métricas de hoy, luego tendencias, luego plan\n"
                        "- Si no estás en red corporativa, reinicia el agente o usa /proveedor para cambiar a un modelo con contexto más grande"
                    )
                    return await self._finalize_chat_reply(user_message, msg, route="llm_request_too_large")
                if "410" in err_str or "end of life" in err_str.lower() or "no longer available" in err_str.lower():
                    msg = (
                        f"El modelo `{self.model}` ha llegado a su fin de vida y ya no está disponible.\n\n"
                        "Elige otro modelo con `/proveedor`."
                    )
                    return await self._finalize_chat_reply(user_message, msg, route="llm_model_eol")
                if (
                    "403" in err_str
                    or "forbidden" in err_str.lower()
                    or "zscaler" in err_str.lower()
                    or "access denied" in err_str.lower()
                ):
                    msg = (
                        "El proveedor del modelo rechazó la petición (403/Forbidden), probablemente por red corporativa o política del proxy.\n\n"
                        "Prueba una de estas opciones:\n"
                        "- Cambiar de proveedor o de modelo con `/proveedor`\n"
                        "- Reintentar desde una red sin bloqueo del proveedor actual\n"
                        "- Hacer una consulta factual de ruta determinista mientras tanto"
                    )
                    return await self._finalize_chat_reply(user_message, msg, route="llm_forbidden")
                raise

            # Track and log token usage
            if getattr(response, "usage", None):
                u = response.usage
                p_toks = getattr(u, "prompt_tokens", 0) or 0
                c_toks = getattr(u, "completion_tokens", 0) or 0
                self.total_prompt_tokens += p_toks
                self.total_completion_tokens += c_toks
                total_step_tokens = p_toks + c_toks
                # Medición del metatooling (26/10): tokens de cada vuelta y cuántas tools se mandaron.
                log.info("[LLM] ◀ iter=%d tools=%d tokens entrada=%d salida=%d",
                         iteration, len(turn_tools), p_toks, c_toks)
                _add_ia_tokens(total_step_tokens)

            message = response.choices[0].message

            # Debug: muestra si el modelo llama herramientas
            if message.tool_calls:
                # Qué pide el LLM en cada vuelta (28/10, pendiente 8a): lo que siempre pide en la primera se puede
                # precargar y ahorrar esa vuelta.
                log.info("[TOOL] iter=%d pide: %s", iteration,
                         "; ".join(f"{tc.function.name}({(tc.function.arguments or '')[:120]})" for tc in message.tool_calls))
            else:
                log.debug(f"Iteración {iteration}: respuesta directa (sin tool calls)")
                log.debug(f"finish_reason: {response.choices[0].finish_reason}")

            # Si el modelo quiere llamar herramientas de Garmin
            if message.tool_calls:
                messages.append(message)

                # Las llamadas de un mismo mensaje del LLM van en paralelo (24/10): el servidor MCP las atiende
                # en hilos con un tope de llamadas simultáneas a Garmin. Cada una devuelve su mensaje "tool" y
                # gather conserva el orden. El cortacircuitos de timeouts se aplica a los mensajes siguientes.
                async def _run_tool_call(tool_call) -> dict:
                    nonlocal tool_timeouts
                    tool_name = tool_call.function.name
                    # Los nombres de tools van en minúsculas; el modelo a veces escribe "Kairos_weekly_..." (24/10).
                    if tool_name and tool_name != tool_name.lower():
                        tool_name = tool_name.lower()
                    # Compatibilidad: algunas guías/prompts antiguos usan plural.
                    if tool_name == "get_personal_records":
                        tool_name = "get_personal_record"
                    try:
                        arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                    # json.loads("null") devuelve None → las tools MCP
                    # esperan un objeto, no null → convertir a {}
                    if arguments is None:
                        arguments = {}
                    # Normalizar fechas: convertir palabras como 'hoy'/'ayer' a ISO
                    arguments = _normalize_date_args(arguments)
                    if tool_name == "get_activity":
                        # Si la pregunta contiene una fecha explícita, SIEMPRE resolver por fecha,
                        # ignorando el activity_id que haya propuesto el modelo (puede ser de
                        # conversaciones anteriores o alucinado).
                        user_date = _extract_iso_date_from_text(user_message)
                        if user_date:
                            resolved_id = await _find_activity_id_by_date(self.mcp_session, user_date)
                            if resolved_id is not None:
                                log.debug(f"Fecha explicita {user_date} -> resolviendo a activity_id={resolved_id} (modelo propuso {arguments.get('activity_id', 'nada')})")
                                arguments = {"activity_id": resolved_id}
                            else:
                                log.debug(f"Fecha explicita {user_date} -> no se encontro actividad ese dia")
                                arguments = {}
                        else:
                            arguments = await _normalize_get_activity_args(
                                self.mcp_session,
                                arguments,
                                user_message=user_message,
                            )
                            if not (isinstance(arguments, dict) and arguments.get("activity_id")):
                                resolved_id = await _resolve_activity_id_from_query(self.mcp_session, user_message)
                                if resolved_id is not None:
                                    arguments = {"activity_id": resolved_id}
                    arguments = _normalize_trend_date_range(tool_name, arguments)


                    # Herramienta que no existe (24/10: el LLM llamó a "goals", que es una tabla citada en el
                    # prompt): no se manda al servidor, que lo devolvía como fallo de Garmin y se avisaba al
                    # usuario de que Garmin no respondía. Se le dice al LLM qué herramientas hay.
                    known_tools = {
                        (item.get("function") or {}).get("name")
                        for item in (self.tools_schema or []) if isinstance(item, dict)
                    }
                    if known_tools and tool_name not in known_tools and not tool_name.startswith("kairos_"):
                        log.warning("[LLM] herramienta inexistente pedida por el modelo: %s", tool_name)
                        return {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps({
                                "error": "unknown_tool",
                                "tool": tool_name,
                                "message": "Esa herramienta no existe. Usa solo las herramientas disponibles; "
                                           "el perfil, el objetivo y el plan ya están en el contexto.",
                            }, ensure_ascii=False),
                        }

                    group = _tool_groups.GROUP_OF.get(tool_name)
                    if group and self._turn_tool_groups and group not in self._turn_tool_groups:
                        log.info("[TOOL] %s fuera de los grupos enviados: se añade %s", tool_name, group)
                        self._turn_tool_groups.add(group)

                    if tool_timeouts >= _MAX_TOOL_TIMEOUTS and not tool_name.startswith("kairos_"):
                        return {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps({
                                "error": "garmin_slow",
                                "tool": tool_name,
                                "message": "Garmin no está respondiendo ahora. No pidas más datos a Garmin en esta "
                                           "respuesta: contesta con lo que ya tienes y di qué no has podido consultar.",
                            }, ensure_ascii=False),
                        }

                    log.debug(f"Ejecutando: {tool_name}({arguments})")
                    try:
                        if tool_name == "get_activity" and not (isinstance(arguments, dict) and arguments.get("activity_id")):
                            raw_result = await _build_activity_candidates_payload(self.mcp_session, user_message)
                        elif tool_name.startswith("kairos_"):
                            raw_result = await self._handle_internal_tool(tool_name, arguments)
                        else:
                            raw_result = await asyncio.wait_for(
                                call_tool(self.mcp_session, tool_name, arguments),
                                timeout=_tool_timeout_seconds(),
                            )
                    except asyncio.TimeoutError as tool_exc:
                        tool_timeouts += 1
                        record_tool_failure(tool_name, f"sin respuesta en {_tool_timeout_seconds():.0f} s")
                        self._log_chat_error(
                            stage="tool_call_timeout",
                            error=tool_exc,
                            extra={"tool_name": tool_name, "arguments": arguments},
                        )
                        raw_result = json.dumps(
                            {
                                "error": "tool_call_timeout",
                                "tool": tool_name,
                                "message": "La herramienta excedió el tiempo máximo permitido.",
                            },
                            ensure_ascii=False,
                        )
                    except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as tool_exc:
                        self._log_chat_error(
                            stage="tool_call",
                            error=tool_exc,
                            extra={"tool_name": tool_name, "arguments": arguments},
                        )
                        raw_result = json.dumps(
                            {
                                "error": "tool_call_failed",
                                "tool": tool_name,
                                "message": str(tool_exc),
                            },
                            ensure_ascii=False,
                        )

                    # Si no hay training_readiness, enriquecer contexto con métricas de recuperación
                    if (
                        tool_name in {"get_training_readiness", "get_morning_training_readiness"}
                        and _is_no_data_result(raw_result)
                    ):
                        requested_date = arguments.get("date") if isinstance(arguments, dict) else None
                        fallback_snapshot = await _build_recovery_fallback_snapshot(
                            self.mcp_session,
                            requested_date if isinstance(requested_date, str) else None,
                        )
                        if fallback_snapshot:
                            log.debug("Training readiness sin datos; usando snapshot alternativo de recuperación")
                            raw_result = fallback_snapshot

                    # Las tools de Kairos ya devuelven sus datos compactos: la poda genérica (listas a 4 elementos, los
                    # más antiguos) dejaba a kairos_load_trends sin las últimas semanas ni los últimos días (28/10).
                    tool_result = raw_result if str(tool_name).startswith("kairos_") else _compact_tool_result(raw_result, tool_name)
                    log.debug(f"Resultado ({len(raw_result or '')} -> {len(tool_result)} chars): {tool_result[:150]}")

                    return {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    }

                messages.extend(await asyncio.gather(*(_run_tool_call(tc) for tc in message.tool_calls)))

                # Continúa el loop para que el modelo procese los resultados
                continue

            # Respuesta final del agente
            assistant_reply = await self._apply_output_sensors(message.content or "", messages, "llm", decision=loop_decision)

            # Fallback para récords personales cuando el modelo responde
            # con "sin acceso" pese a tener herramientas MCP activas.
            if _is_no_access_reply(assistant_reply) and (
                _is_personal_records_intent(user_message)
                or _is_personal_records_followup_intent(user_message, self.conversation_history)
            ):
                try:
                    available_tool_names = {
                        (item.get("function") or {}).get("name")
                        for item in (self.tools_schema or [])
                        if isinstance(item, dict)
                    }
                    records_tool = None
                    if "get_personal_record" in available_tool_names:
                        records_tool = "get_personal_record"
                    elif "get_personal_records" in available_tool_names:
                        records_tool = "get_personal_records"

                    if records_tool:
                        records_raw = await call_tool(self.mcp_session, records_tool, {})
                        records_compact = _compact_tool_result(records_raw, records_tool)
                        if records_compact and records_compact != "(sin datos)" and not _is_no_data_result(records_raw):
                            records_sport = _detect_personal_records_sport_intent(user_message, self.conversation_history)
                            assistant_reply = _build_personal_records_markdown(records_compact, preferred_sport=records_sport)
                except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                    log.debug("records tool route no disponible: %s", exc)
                    self._log_chat_error(
                        stage="records_no_access_fallback",
                        error=exc,
                        extra={"user_message": user_message},
                    )

            # Fallback anti-respuesta genérica ("necesito más información"): reintento por LLM con el
            # snapshot para proponer una sesión útil. Nunca escribe en la base de datos: hasta el 24/10, con
            # objetivo en el perfil, creaba un plan "activo" vacío y desactivaba el plan real del atleta.
            if (
                _is_generic_needs_more_info_reply(assistant_reply)
                and (
                    _is_planning_intent(user_message, self.conversation_history)
                    or _is_tomorrow_workout_intent(user_message)
                    or _is_post_activity_feedback_intent(user_message)
                )
            ):
                rescue_messages: list[dict[str, str]]
                if _is_post_activity_feedback_intent(user_message) and analysis_block_for_rescue:
                    target_date = prefetch_date_for_rescue or user_date or "fecha consultada"
                    rescue_messages = [
                        {
                            "role": "system",
                            "content": (
                                _render_prompt("rescue/activity_feedback")
                                + "\n\n" + _calendar_block()
                                + "\n\n" + _load_coaching_rules()
                                + self._profile_context_block()
                                + self._plan_context_block()
                            ),
                        },
                        {
                            "role": "user",
                            "content": _render_prompt(
                                "rescue/activity_feedback_user",
                                pregunta=user_message,
                                fecha=target_date,
                                datos=analysis_block_for_rescue,
                            ),
                        },
                    ]
                else:
                    snapshot_ctx: dict[str, Any] = {}
                    try:
                        startup = await self.collect_startup_snapshot_48h()
                        load_summary = _format_load_fatigue_summary(startup.get("load_fatigue") or {})
                        snapshot_ctx = {
                            "today": (startup.get("dates") or {}).get("today"),
                            "body_battery": (startup.get("body_battery") or {}).get("summary"),
                            "hrv": (startup.get("hrv") or {}).get("summary"),
                            "sleep": (startup.get("sleep") or {}).get("summary"),
                            "load": load_summary,
                            "recent_trainings": (startup.get("trainings") or [])[:3],
                        }
                    except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                        log.debug("No se pudo obtener snapshot para fallback LLM de planificación: %s", exc)

                    rescue_messages = [
                        {
                            "role": "system",
                            "content": (
                                _render_prompt("rescue/next_session", horizonte=_coaching_horizon(user_message))
                                + "\n\n" + _calendar_block()
                                + "\n\n" + _load_coaching_rules()
                                + self._profile_context_block()
                                + self._load_and_anchors_context_block()
                                + self._plan_context_block()
                            ),
                        },
                        {
                            "role": "user",
                            "content": _render_prompt(
                                "rescue/next_session_user",
                                solicitud=user_message,
                                contexto=json.dumps(snapshot_ctx, ensure_ascii=False),
                            ),
                        },
                    ]
                try:
                    rescue_response = await self.client.chat.completions.create(
                        model=self.model,
                        messages=rescue_messages,
                    )
                    if getattr(rescue_response, "usage", None):
                        u = rescue_response.usage
                        p_toks = getattr(u, "prompt_tokens", 0) or 0
                        c_toks = getattr(u, "completion_tokens", 0) or 0
                        self.total_prompt_tokens += p_toks
                        self.total_completion_tokens += c_toks
                        _add_ia_tokens(p_toks + c_toks)
                    rescue_text = str((rescue_response.choices[0].message.content or "")).strip()
                    rescue_text = await self._apply_output_sensors(rescue_text, rescue_messages, "rescue")
                    if rescue_text:
                        assistant_reply = rescue_text
                except Exception as exc:
                    log.debug("Fallback LLM de planificación no disponible: %s", exc)

            return await self._finalize_chat_reply(user_message, assistant_reply, route="llm")

    def generate_session_summary_checkpoint(self) -> str:
        """Genera un resumen local y ligero para checkpoints frecuentes.

        No llama al LLM: evita latencia de red durante la sesión y en el cierre.
        """
        if not self.conversation_history:
            return ""

        # Solo las preguntas: las respuestas traen cifras que caducan (como en _restore_previous_session).
        user_msgs = [
            str(msg.get("content") or "").strip()
            for msg in self.conversation_history
            if msg.get("role") == "user" and str(msg.get("content") or "").strip()
        ]

        if not user_msgs:
            return ""

        return f"Temas recientes: {' | '.join(user_msgs[-4:])}."

    def save_session_summary_checkpoint(self) -> None:
        """Persiste un checkpoint diario del resumen de sesión (upsert por fecha)."""
        summary = self.generate_session_summary_checkpoint()
        if not summary or not _session_memory_enabled():
            return
        try:
            _storage.persist_session_summary_daily(summary)
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            log.debug("No se pudo persistir checkpoint diario de sesión: %s", exc)
