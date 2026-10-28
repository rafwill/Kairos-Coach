"""
agent/prefetch.py
Precarga de la actividad antes del bucle con tools: si la pregunta habla de una actividad (fecha o "mi último
trail"), se piden a Garmin la actividad, el body battery, el sueño, la HRV y las zonas, y el LLM recibe el
análisis ya calculado en Python.
"""

import logging
import json
import asyncio
import re
from datetime import date, timedelta


from agent.mcp.client import (
    call_tool,
)
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _find_hr_zones_in_json,
    _parse_hr_zones_list,
)
from agent.prompts import (
    _render_prompt,
)
from agent.dates import (
    _extract_iso_date_from_text,
)
from agent.tool_results import (
    _activity_id_int,
    _compact_tool_result,
    _extract_activities_list,
)
from agent.data_access import (
    _load_stored_activity_tss,
)
from agent.garmin_data import (
    _GARMIN_ZONE_NAMES_ES,
)
from agent.daily_engine import (
    _get_active_training_plan,
)
from agent.activity_analysis import (
    _build_activity_analysis_block,
    _build_post_activity_section_spec,
    _find_activity_id_by_date,
    _resolve_last_activity,
)

log = logging.getLogger(__name__)


class PrefetchMixin:
    """Contexto de una actividad concreta para el LLM."""

    async def _prefetch_activity_context(self, user_message: str, messages: list) -> tuple[list, str, str, dict]:
        """Devuelve (mensajes, bloque de análisis, fecha de la actividad, datos de hoy ya pedidos a Garmin).

        Sin actividad, los mensajes como llegan y lo demás vacío. El bloque y la fecha los usan los rescates
        del bucle; los datos de hoy, el marco de G15 (no se vuelven a pedir).
        """
        _prefetch_cache: dict = {}

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

        return messages, analysis_block_for_rescue, prefetch_date_for_rescue, _prefetch_cache
