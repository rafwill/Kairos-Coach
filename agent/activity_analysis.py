"""
agent/activity_analysis.py
Análisis de una actividad: localizarla (la última, la de un deporte, por fecha o por nombre), bloque de análisis precalculado en Python para el LLM y sección final según el plan.
"""

from __future__ import annotations

import logging
import math
import json
import re
from datetime import date

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # solo para anotaciones: mcp se importa al usarlo (8c)
    from mcp import ClientSession

from agent.mcp.client import (
    call_tool,
)
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _is_cycling_activity,
    _parse_hr_zones_list,
    _try_parse_json,
)
from agent.prompts import (
    _render_prompt,
)
from agent.dates import (
    _extract_iso_date_from_text,
    _seconds_to_hhmmss,
)
from agent.knowledge import (
    _tokenize_for_kb,
)
from agent.tool_results import (
    _extract_activities_list,
    _flatten_garmin_activity,
    _parse_activities_response,
)
from agent.data_access import (
    _load_metrics_series,
)
from agent.garmin_data import (
    _GARMIN_ZONE_NAMES_ES,
    _activity_matches_sport,
    _extract_activity_date_iso,
    _get_activity_name_es,
)
from agent.wellness import (
    _hrv_deviation_text,
)

log = logging.getLogger(__name__)


def _build_post_activity_section_spec(activity_date_iso: str, today_d: date | None = None) -> dict:
    """Define el enfoque de la sección final según antigüedad de la actividad.

    - Actividad reciente (<=2 días): recuperación inmediata y próximas sesiones.
    - Actividad histórica (>2 días): aprendizajes transferibles, sin plan temporal corto.
    """
    today_ref = today_d or date.today()
    is_recent = False
    try:
        act_d = date.fromisoformat(str(activity_date_iso or "")[:10])
        is_recent = (today_ref - act_d).days <= 2
    except ValueError:
        is_recent = False

    if is_recent:
        return {
            "header": "## 🔄 Recuperación y próximas sesiones",
            "section_name": "## 🔄 Recuperación y próximas sesiones",
            "guidance": _render_prompt("activity_analysis/final_recent"),
            "plan_context": "recent",
        }

    return {
        "header": "## 🧾 Aprendizajes para futuras sesiones similares",
        "section_name": "## 🧾 Aprendizajes para futuras sesiones similares",
        "guidance": _render_prompt("activity_analysis/final_historical"),
        "plan_context": "historical",
    }


_LAST_ACTIVITY_RE = re.compile(
    r"\b(última|ultima|último|ultimo|reciente|mas reciente|más reciente)\s*"
    r"(actividad|entreno|entrenamiento|salida|sesión|sesion|trail|carrera|running|bici)\b"
)


def _last_activity_sport(text: str) -> str | None:
    """Deporte que pide "mi último trail / mi última carrera / mi última salida en bici", o None."""
    if re.search(r"\btrail\b", text):
        return "trail"
    if re.search(r"\brunning\b|\bcarrera\b", text):
        return "running"
    if re.search(r"\bbici\b|\bciclismo\b|\bcycling\b", text):
        return "cycling"
    return None


async def _resolve_last_activity(mcp_session, user_message: str) -> tuple[str | None, int | None]:
    """(fecha ISO, activityId) de "mi última actividad", "mi último trail"… sin fecha; (None, None) si no aplica.

    La más reciente de las 20 últimas de Garmin, del deporte pedido. Con deporte y sin ninguna de él, (None, None):
    el LLM busca con sus tools. Sin deporte y sin Garmin, el último día con actividad de la serie de carga, y si
    no, hoy. Antes (hasta el 28/10) el texto de get_activities no se leía como JSON, así que nunca encontraba nada:
    "mi último trail" analizaba la primera actividad de hoy, fuera del deporte que fuera.
    """
    text = (user_message or "").lower()
    if not _LAST_ACTIVITY_RE.search(text):
        return None, None
    sport = _last_activity_sport(text)
    acts: list[dict] = []
    try:
        raw = await call_tool(mcp_session, "get_activities", {"start": "0", "limit": "20"})
        parsed = _try_parse_json(raw)
        acts = _extract_activities_list(parsed if parsed is not None else raw)
    except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, KeyError) as exc:
        log.debug("pre_fetch última actividad: get_activities falló: %s", exc)
    match = next((a for a in acts if sport is None or _activity_matches_sport(a, sport)), None)
    if match is not None:
        act_id = match.get("activityId") or match.get("id") or match.get("activity_id")
        iso = _extract_activity_date_iso(match)
        log.info("pre_fetch: última actividad %s → id=%s fecha=%s", sport or "", act_id, iso)
        return iso, (int(act_id) if act_id else None)
    if sport:
        log.info("pre_fetch: ninguna de %s entre las 20 últimas; sin precarga", sport)
        return None, None
    for row in reversed(_load_metrics_series()):
        if float(row.get("tss") or 0) > 0 or int(row.get("activities_count") or 0) > 0:
            return str(row.get("date") or ""), None
    return date.today().isoformat(), None


async def _find_activity_id_by_date(mcp_session: ClientSession, target_date_iso: str) -> int | None:
    """Busca en actividades recientes el activity_id correspondiente a una fecha ISO."""
    # Intento rápido: endpoint específico de día.
    try:
        raw_day = await call_tool(mcp_session, "get_activities_fordate", {"date": target_date_iso})
        acts_day = _extract_activities_list(raw_day)
        if acts_day:
            for activity in acts_day:
                activity_id = activity.get("activityId") or activity.get("activity_id") or activity.get("id")
                try:
                    return int(activity_id)
                except (TypeError, ValueError):
                    continue
    except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
        log.debug("_find_activity_id_by_date: get_activities_fordate fallback: %s", exc)

    start = 0
    limit = 100
    max_pages = 30  # hasta 3000 actividades para cubrir historiales amplios

    for page_num in range(max_pages):
        raw = await call_tool(mcp_session, "get_activities", {"start": str(start), "limit": str(limit)})
        activities, has_more, next_start = _parse_activities_response(raw)

        if page_num == 0 and activities:
            sample = activities[0]
            # Debug exhaustivo: muestra TODOS los keys y los valores de fecha para diagnóstico
            all_keys = list(sample.keys())
            log.debug(f"Primera actividad keys: {all_keys}")
            date_fields = {k: sample.get(k) for k in all_keys if any(x in k.lower() for x in ("time", "date", "start", "timestamp", "calendar"))}
            act_id_debug = sample.get("activityId") or sample.get("id") or sample.get("activity_id")
            log.debug(f"activityId={act_id_debug} campos_fecha={date_fields}")

        for activity in activities:
            act_date = _extract_activity_date_iso(activity)
            act_id = activity.get("activityId") or activity.get("activity_id") or activity.get("id")
            if act_date:
                log.debug(f"Comparando actividad {act_id}: fecha_extraida={act_date} vs target={target_date_iso}")
            if act_date != target_date_iso:
                continue
            activity_id = activity.get("activityId") or activity.get("activity_id") or activity.get("id")
            try:
                return int(activity_id)
            except (TypeError, ValueError):
                continue

        if not has_more:
            break
        new_start = next_start if next_start > start else start + limit
        if new_start <= start:  # paginación rota: el servidor no avanza
            break
        start = new_start

    return None


async def _find_activity_id_by_name(mcp_session: ClientSession, name_hint: str) -> int | None:
    """Busca activity_id por nombre aproximado en actividades recientes."""
    hint = (name_hint or "").strip().lower()
    if not hint:
        return None

    stop_tokens = {
        "analiza", "analizar", "mi", "mis", "del", "de", "la", "el", "los", "las",
        "por", "para", "con", "una", "uno", "competicion", "competición", "actividad",
        "carrera", "quiero", "que", "hice", "hacer", "sobre",
    }
    hint_tokens = [t for t in _tokenize_for_kb(hint) if t not in stop_tokens]

    start = 0
    limit = 100
    max_pages = 30  # hasta 3000 actividades para cubrir historiales amplios

    for _ in range(max_pages):
        raw = await call_tool(mcp_session, "get_activities", {"start": str(start), "limit": str(limit)})
        activities, has_more, next_start_val = _parse_activities_response(raw)

        best_id = None
        best_score = -1
        for activity in activities:
            if not isinstance(activity, dict):
                continue
            name = str(activity.get("name", "")).strip().lower()
            if not name:
                continue

            score = -1
            # Coincidencias más fuertes primero
            if name == hint:
                score = 100
            elif name.startswith(hint):
                score = 90
            elif hint in name:
                score = 80
            elif all(tok in name for tok in hint.split() if tok):
                score = 70
            else:
                # Fallback robusto para texto libre del usuario:
                # puntuar por solape de tokens relevantes (ignorando ruido).
                if hint_tokens:
                    overlap = sum(1 for tok in hint_tokens if tok in name)
                    if overlap >= 2:
                        score = 60 + min(overlap * 5, 20)

            if score > best_score:
                activity_id = activity.get("activityId") or activity.get("activity_id") or activity.get("id")
                try:
                    best_id = int(activity_id)
                    best_score = score
                except (TypeError, ValueError):
                    continue

        if best_id is not None and best_score >= 70:
            return best_id

        if not has_more:
            break
        start = next_start_val if next_start_val > start else start + limit

    return None


def _format_activity_analysis_for_markdown(analysis_block: str) -> str:
    """Convierte el bloque técnico de análisis en Markdown legible para usuario."""
    if not analysis_block or not str(analysis_block).strip():
        return ""

    out: list[str] = []
    for raw_line in str(analysis_block).splitlines():
        line = raw_line.strip()
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue

        if line.startswith("===") and line.endswith("==="):
            title = line.strip("=").strip()
            out.append(f"### {title}")
            out.append("")
            continue

        if ":" in line:
            key, value = line.split(":", 1)
            k = key.strip()
            v = value.strip()
            if k and v:
                out.append(f"- {k}: {v}")
                continue

        out.append(f"- {line}")

    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def _build_activity_analysis_block(
    activity_raw: str,
    body_battery_raw: str | None = None,
    sleep_raw: str | None = None,
    hrv_raw: str | None = None,
    hr_zones_raw: str | None = None,
    stored_tss_row: dict | None = None,
) -> str:
    """Construye un bloque de análisis pre-computado en Python para inyectar al LLM.

    Calcula métricas derivadas (ritmo, zonas FC, hidratación, carga) directamente
    en Python para que el LLM solo aporte interpretación y coaching, no cálculos.
    """
    lines: list[str] = []

    # ── Parsear actividad ──────────────────────────────────────────────────
    try:
        act = json.loads(activity_raw) if activity_raw else {}
    except (TypeError, json.JSONDecodeError):
        act = {}
    act = _flatten_garmin_activity(act)  # get_activity trae las métricas dentro de summaryDTO

    name     = act.get("name") or act.get("activityName") or "Actividad"
    act_type = act.get("type") or act.get("activityType") or ""
    dur_s_raw = (act.get("duration") or act.get("duration_seconds")
                 or act.get("movingDuration") or act.get("moving_duration_seconds"))
    dist_m_raw = act.get("distance") or act.get("distance_meters")
    avg_hr   = act.get("avgHr") or act.get("avg_hr_bpm") or act.get("averageHR")
    max_hr   = act.get("maxHr") or act.get("max_hr_bpm") or act.get("maxHR")
    min_hr   = act.get("minHr") or act.get("min_hr_bpm") or act.get("minHR")
    calories = act.get("calories") or act.get("activeKilocalories") or act.get("activeCalories")
    elev_gain = act.get("elevationGain") or act.get("elevation_gain_meters") or act.get("totalAscent")
    elev_loss = act.get("elevationLoss") or act.get("elevation_loss_meters") or act.get("totalDescent")
    train_effect = act.get("trainingEffect") or act.get("aerobicTrainingEffect")
    train_load   = act.get("activityTrainingLoad") or act.get("trainingLoadScore")

    # ── Conversiones base ─────────────────────────────────────────────────
    try:
        dur_s = float(dur_s_raw) if dur_s_raw is not None else None
    except (ValueError, TypeError):
        dur_s = None
    try:
        dist_km = float(dist_m_raw) / 1000 if dist_m_raw is not None else None
    except (ValueError, TypeError):
        dist_km = None
    try:
        avg_hr_f = float(avg_hr) if avg_hr is not None else None
        max_hr_f = float(max_hr) if max_hr is not None else None
    except (ValueError, TypeError):
        avg_hr_f = max_hr_f = None

    # ── Sección 1: Resumen básico ──────────────────────────────────────────
    lines.append("=== RESUMEN DE ACTIVIDAD (calculado) ===")
    lines.append(f"Nombre: {name}")
    _deporte_es = _get_activity_name_es(act_type)
    if _deporte_es:
        lines.append(f"Deporte: {_deporte_es}")
    if dur_s:
        lines.append(f"Duracion: {_seconds_to_hhmmss(dur_s)}")
    if dist_km:
        lines.append(f"Distancia: {dist_km:.2f} km")
    if dur_s and dist_km and dist_km > 0:
        if _is_cycling_activity(act_type):
            speed_kmh = dist_km / (dur_s / 3600)
            lines.append(f"Velocidad media: {speed_kmh:.1f} km/h")
            # Velocidad máxima (Garmin devuelve avgSpeed/maxSpeed en m/s)
            max_spd_raw = act.get("maxSpeed") or act.get("max_speed_ms")
            if max_spd_raw is not None:
                try:
                    lines.append(f"Velocidad maxima: {float(max_spd_raw) * 3.6:.1f} km/h")
                except (ValueError, TypeError):
                    pass
        else:
            pace_s = dur_s / dist_km
            lines.append(f"Ritmo medio: {int(pace_s//60)}:{int(pace_s%60):02d} min/km")
    if avg_hr_f:
        lines.append(f"FC media: {avg_hr_f:.0f} bpm")
    if max_hr_f:
        lines.append(f"FC maxima: {max_hr_f:.0f} bpm")
    if min_hr is not None:
        lines.append(f"FC minima: {min_hr} bpm")
    if elev_gain:
        lines.append(f"Desnivel positivo: {float(elev_gain):.0f} m")
    if elev_loss:
        lines.append(f"Desnivel negativo: {float(elev_loss):.0f} m")
    if calories:
        lines.append(f"Calorias: {float(calories):.0f} kcal")
    # TSS de este entrenamiento: el guardado en activity_tss (único TSS por actividad).
    if stored_tss_row is not None:
        _tss_lbl = str(stored_tss_row.get("method") or "TSS")
        lines.append(f"{_tss_lbl} (Kairos): {float(stored_tss_row.get('tss') or 0.0):.1f}")
    else:
        lines.append("TSS (Kairos): pendiente de cálculo")

    # ── Sección 2: Zonas de FC ─────────────────────────────────────────────
    # Prioridad 1: datos reales del dispositivo (get_activity_hr_zones)
    # Prioridad 2: estimación gaussiana (fallback, solo cuando no hay datos reales)
    _zones_shown = False
    _zones_parsed = _parse_hr_zones_list(hr_zones_raw)
    if _zones_parsed:
        # Calcular total de segundos para los porcentajes
        _total_secs = sum(float(z.get("secsInZone") or 0) for z in _zones_parsed)
        # Si no hay segundos pero hay porcentajes directos, usarlos
        _has_pct_direct = all(z.get("pctDirect") is not None for z in _zones_parsed)
        if _total_secs > 0 or _has_pct_direct:
            lines.append("")
            lines.append("=== ZONAS DE FRECUENCIA CARDIACA (datos reales Garmin — Tiempo en Zonas) ===")
            if avg_hr_f and max_hr_f:
                lines.append(f"FCmax: {max_hr_f:.0f} bpm | FC media: {avg_hr_f:.0f} bpm")
            _sorted_zp = sorted(_zones_parsed, key=lambda x: int(x.get("zoneNumber") or 0))
            for _zi, z in enumerate(_sorted_zp):
                _z_secs = float(z.get("secsInZone") or 0)
                _z_pct_d = z.get("pctDirect")
                if _total_secs > 0:
                    _z_pct = _z_secs / (dur_s or _total_secs) * 100
                elif _z_pct_d is not None:
                    _z_pct = float(_z_pct_d)
                    _z_secs = (_z_pct / 100.0 * (dur_s or 0))
                else:
                    continue
                _z_mins = _z_secs / 60
                _z_num = int(z.get("zoneNumber") or 0)
                _z_lo = z.get("minHeartRateIn") or "?"
                _z_hi = z.get("maxHeartRateIn") or "?"
                # Calcular límite alto desde la siguiente zona si no está disponible
                if _z_hi == "?" and _z_lo != "?" and _zi + 1 < len(_sorted_zp):
                    _next_lo = _sorted_zp[_zi + 1].get("minHeartRateIn") or "?"
                    if _next_lo != "?":
                        _z_hi = str(int(float(_next_lo)) - 1)
                _z_name = (_GARMIN_ZONE_NAMES_ES.get(_z_num)
                           if not z.get("zoneName") or str(z.get("zoneName")).startswith("Z")
                           else z.get("zoneName"))
                if _z_lo != "?" and _z_hi != "?":
                    _hr_range = f"{_z_lo}–{_z_hi} bpm"
                elif _z_lo != "?":
                    _hr_range = f">{_z_lo} bpm"
                else:
                    _hr_range = ""
                lines.append(f"  Z{_z_num} · {_z_name:<14} · {_hr_range:<14} {_z_pct:5.1f}%  (~{_z_mins:.0f} min)")
            _zones_shown = True

    if not _zones_shown and avg_hr_f and max_hr_f and dur_s:
        lines.append("")
        lines.append("=== ZONAS DE FRECUENCIA CARDIACA (estimacion gaussiana — aproximada) ===")
        lines.append("AVISO: sin datos reales de zonas. Estimación basada en FC media y FCmax, puede diferir de las zonas reales configuradas en Garmin.")
        lines.append(f"FCmax observada: {max_hr_f:.0f} bpm | FC media: {avg_hr_f:.0f} bpm")
        sigma = 0.10 * max_hr_f
        zone_defs = [
            ("Z1 Recuperacion     (<60% FC)", 0.00 * max_hr_f, 0.60 * max_hr_f),
            ("Z2 Base aerobica (60-70% FC)",  0.60 * max_hr_f, 0.70 * max_hr_f),
            ("Z3 Umbral aerobico (70-80%FC)", 0.70 * max_hr_f, 0.80 * max_hr_f),
            ("Z4 Umbral anaer.  (80-90% FC)", 0.80 * max_hr_f, 0.90 * max_hr_f),
            ("Z5 VO2max          (>90% FC)",  0.90 * max_hr_f, 2.00 * max_hr_f),
        ]
        def ncdf(x, mu, s):
            return 0.5 * (1 + math.erf((x - mu) / (s * math.sqrt(2))))
        raw_pcts = []
        for _, lo, hi in zone_defs:
            p = ncdf(hi, avg_hr_f, sigma) - ncdf(lo, avg_hr_f, sigma)
            raw_pcts.append(max(p, 0))
        total_p = sum(raw_pcts) or 1.0
        for i, (zname, _, _) in enumerate(zone_defs):
            pct = raw_pcts[i] / total_p * 100
            mins = dur_s * raw_pcts[i] / total_p / 60
            lines.append(f"  {zname}: {pct:.1f}%  (~{mins:.0f} min)")

    # ── Sección 3: Efecto de entrenamiento y carga ────────────────────────
    # Extraer también efecto anaeróbico y label para formatearlos en Python
    anaer_effect  = act.get("anaerobicTrainingEffect") or act.get("anaerobic_training_effect")
    effect_label  = (act.get("activityTrainingEffectLabel") or act.get("trainingEffectLabel")
                     or act.get("training_effect_label"))
    _effect_labels_es = {
        "AEROBIC_BASE":   "construccion base aerobica",
        "RECOVERY":       "recuperacion",
        "TEMPO":          "mejora de ritmo/tempo",
        "THRESHOLD":      "trabajo de umbral",
        "OVERSTRESSING":  "sobrecarga (excesivo)",
        "NO_EFFECT":      "sin efecto significativo",
    }

    if train_effect or train_load:
        lines.append("")
        lines.append("=== CARGA Y EFECTO DE ENTRENAMIENTO ===")
        te_labels = {1: "recuperacion", 2: "mantenimiento", 3: "mejora", 4: "alto impacto", 5: "sobreextension/pico"}
        if train_effect is not None:
            te = float(train_effect)
            label = te_labels.get(min(int(te), 5), "")
            lines.append(f"Training Effect aerobico: {te:.1f}/5.0 ({label})")
        if anaer_effect is not None:
            lines.append(f"Training Effect anaerobico: {float(anaer_effect):.1f}/5.0")
        if effect_label:
            friendly = _effect_labels_es.get(str(effect_label), str(effect_label).replace("_", " ").lower())
            lines.append(f"Tipo de entrenamiento: {friendly}")
        if train_load is not None:
            # Carga nativa de Garmin: informativa, en su propia escala; no es TSS.
            lines.append(f"Carga Garmin (no TSS): {float(train_load):.1f}")

    # ── Sección 4: Hidratación estimada ───────────────────────────────────
    if dur_s:
        lines.append("")
        lines.append("=== HIDRATACION ESTIMADA ===")
        dur_h = dur_s / 3600
        low  = round(dur_h * 0.5, 1)
        high = round(dur_h * 0.8, 1)
        hot  = round(dur_h * 1.0, 1)
        dur_min_total = int(round(dur_s / 60))
        lines.append(f"Para {dur_min_total // 60} h {dur_min_total % 60:02d} min -> minimo {low}-{high}L (condiciones normales)")
        lines.append(f"Con calor/altitud -> hasta {hot}L")
        if dist_km and dist_km > 30:
            lines.append("  -> Ultra: añadir electrolitos cada 45-60 min ademas de agua")

    # ── Sección 5: Recuperacion pre-actividad ────────────────────────────
    if body_battery_raw and body_battery_raw != "(sin datos)":
        # El body_battery_raw viene como "BODY BATTERY del YYYY-MM-DD:\n[json]"
        # Parsear el JSON y formatear los campos útiles
        try:
            bb_json_str = body_battery_raw.split("\n", 1)[1].strip() if "\n" in body_battery_raw else body_battery_raw
            bb_data_list = json.loads(bb_json_str)
            if isinstance(bb_data_list, list) and bb_data_list:
                bb = bb_data_list[0]
            elif isinstance(bb_data_list, dict):
                bb = bb_data_list
            else:
                bb = {}
            charged = bb.get("charged") or bb.get("bodyBatteryCharged")
            drained  = bb.get("drained") or bb.get("bodyBatteryDrained")
            highest  = bb.get("highestBodyBattery") or bb.get("highest")
            lowest   = bb.get("lowestBodyBattery") or bb.get("lowest")
            lines.append("")
            lines.append("=== BODY BATTERY (dia de la actividad) ===")
            if highest is not None and lowest is not None:
                lines.append(f"Maximo del dia: {int(highest)} | Minimo del dia: {int(lowest)}")
            if charged is not None:
                lines.append(f"Recargado: +{int(charged)} puntos")
            if drained is not None:
                lines.append(f"Drenado: -{int(drained)} puntos")
            if charged is not None and drained is not None:
                net = int(charged) - int(drained)
                lines.append(f"Balance neto: {net:+d} puntos {'(deficit esperado en una ultra)' if net < -30 else ''}")
        except (json.JSONDecodeError, TypeError, ValueError, IndexError) as exc:
            log.debug("No se pudo parsear body battery para analisis de actividad: %s", exc)
            lines.append("")
            lines.append("=== BODY BATTERY (dia de la actividad) ===")
            lines.append(body_battery_raw[:200])

    if sleep_raw and sleep_raw != "(sin datos)":
        # El sleep_raw viene como "SUENO noche previa (YYYY-MM-DD):\n{json}"
        # Parsear dailySleepDTO y mostrar solo métricas útiles
        try:
            sleep_json_str = sleep_raw.split("\n", 1)[1].strip() if "\n" in sleep_raw else sleep_raw
            sd = json.loads(sleep_json_str)
            dto = sd.get("dailySleepDTO") or sd if isinstance(sd, dict) else {}
            sleep_secs  = dto.get("sleepTimeSeconds", 0)
            deep_secs   = dto.get("deepSleepSeconds", 0)
            light_secs  = dto.get("lightSleepSeconds", 0)
            rem_secs    = dto.get("remSleepSeconds", 0)
            wake_secs   = dto.get("wakeSeconds", 0) or dto.get("awakeSleepSeconds", 0)
            # Score: puede estar plano o anidado en sleepScores.overall.value
            _score_nested = ((dto.get("sleepScores") or {}).get("overall") or {})
            score = (dto.get("sleepScore") or dto.get("sleepScoreValue")
                     or _score_nested.get("value"))
            quality_map = {1: "Pobre", 2: "Regular", 3: "Buena", 4: "Excelente"}
            quality_num = dto.get("sleepQuality") or dto.get("sleepQualityTypePK")
            quality_str = quality_map.get(int(quality_num), str(quality_num)) if quality_num else None
            def fmt_mins(s):
                h, m = int(s) // 3600, (int(s) % 3600) // 60
                return f"{h}h {m:02d}min" if h else f"{m}min"
            lines.append("")
            lines.append("=== SUENO NOCHE PREVIA ===")
            lines.append(f"Duracion total: {fmt_mins(sleep_secs)}")
            if deep_secs:
                lines.append(f"Sueno profundo: {fmt_mins(deep_secs)}")
            if light_secs:
                lines.append(f"Sueno ligero: {fmt_mins(light_secs)}")
            if rem_secs:
                lines.append(f"REM: {fmt_mins(rem_secs)}")
            if wake_secs:
                lines.append(f"Despertares: {fmt_mins(wake_secs)}")
            if score:
                lines.append(f"Puntuacion Garmin: {score}/100")
            if quality_str:
                lines.append(f"Calidad: {quality_str}")
        except (json.JSONDecodeError, TypeError, ValueError, IndexError) as exc:
            log.debug("No se pudo parsear sueno para analisis de actividad: %s", exc)
            lines.append("")
            lines.append("=== SUENO NOCHE PREVIA ===")
            lines.append(sleep_raw[:300])

    if hrv_raw and hrv_raw != "(sin datos)":
        try:
            hrv_json_str = hrv_raw.split("\n", 1)[1].strip() if "\n" in hrv_raw else hrv_raw
            hd = json.loads(hrv_json_str)
            if isinstance(hd, dict):
                avg_hrv  = (hd.get("lastNightAvg") or hd.get("last_night_avg_hrv_ms")
                            or hd.get("avgOvernightHrv") or hd.get("avgHrv")
                            or hd.get("averageHrv") or hd.get("lastNight"))
                high_hrv = hd.get("high5Min") or hd.get("last_night_5min_high_hrv_ms") or hd.get("highHrv")
                weekly_hrv = hd.get("weeklyAvg") or hd.get("weekly_avg_hrv_ms")
                status_hrv = hd.get("status") or hd.get("hrvStatus")
                lines.append("")
                lines.append("=== HRV DIA ACTIVIDAD ===")
                if avg_hrv:
                    lines.append(f"HRV promedio noche: {float(avg_hrv):.0f} ms")
                if weekly_hrv:
                    lines.append(f"HRV media 7 dias: {float(weekly_hrv):.0f} ms")
                if _hrv_deviation_text(avg_hrv, weekly_hrv):
                    lines.append(f"HRV noche: {_hrv_deviation_text(avg_hrv, weekly_hrv)}")
                if high_hrv:
                    lines.append(f"HRV maximo 5min: {float(high_hrv):.0f} ms")
                if status_hrv:
                    lines.append(f"Estado HRV: {status_hrv}")
                if not avg_hrv:
                    lines.append(f"(raw compact: {hrv_json_str[:150]})")
        except (json.JSONDecodeError, TypeError, ValueError, IndexError) as exc:
            log.debug("No se pudo parsear HRV para analisis de actividad: %s", exc)
            lines.append("")
            lines.append("=== HRV DIA ACTIVIDAD ===")
            lines.append(hrv_raw[:200])

    # ── Recuperacion recomendada post-ultra ──────────────────────────────
    if train_load is not None or (dur_s and dur_s > 10800):
        lines.append("")
        lines.append("=== RECUPERACION RECOMENDADA ===")
        tl_val = float(train_load) if train_load is not None else 0
        dur_h2 = (dur_s or 0) / 3600
        if tl_val > 300 or dur_h2 > 8:
            lines.append("Carga extrema (ultra/maratón+): 10-14 días sin impacto, 3-4 semanas hasta intensidad")
        elif tl_val > 150 or dur_h2 > 3:
            lines.append("Carga alta: 3-5 días recuperacion activa, evitar intensidad 1 semana")
        else:
            lines.append("Carga media: 1-2 días recuperacion, retomar progresivamente")

    return "\n".join(lines)


async def _normalize_get_activity_args(
    mcp_session: ClientSession,
    arguments: dict,
    user_message: str | None = None,
) -> dict:
    """Normaliza argumentos de get_activity.

    Acepta activity_id numérico o fechas en lenguaje natural/ISO y resuelve el
    ID automáticamente consultando get_activities cuando sea necesario.
    """
    if not isinstance(arguments, dict):
        arguments = {}

    args = dict(arguments)
    candidate = args.get("activity_id")
    if candidate is None:
        candidate = args.get("activityId")
    if candidate is None:
        candidate = args.get("id")
    if candidate is None:
        candidate = args.get("date")
    if candidate is None and isinstance(user_message, str) and user_message.strip():
        candidate = user_message.strip()

    # ID ya numérico
    if isinstance(candidate, (int, float)):
        return {"activity_id": int(candidate)}
    if isinstance(candidate, str) and candidate.strip().isdigit():
        return {"activity_id": int(candidate.strip())}

    # Intentar resolver fecha -> activity_id
    if isinstance(candidate, str):
        target_date = _extract_iso_date_from_text(candidate)
        if target_date:
            resolved_id = await _find_activity_id_by_date(mcp_session, target_date)
            if resolved_id is not None:
                return {"activity_id": resolved_id}
            # Si el usuario pidió una fecha concreta y no hay actividad ese día,
            # no caer a matching por nombre para evitar seleccionar otro entrenamiento.
            return {}
        # Si no es fecha, intentar resolver por nombre de actividad
        resolved_id = await _find_activity_id_by_name(mcp_session, candidate)
        if resolved_id is not None:
            return {"activity_id": resolved_id}

    # Fallback: mantener nombre esperado por la tool solo para valores numéricos.
    # Evita enviar texto libre al backend (fallo: invalid literal for int()).
    if "activity_id" in args:
        v = args["activity_id"]
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
            return {"activity_id": int(v)}
        return {}
    if "activityId" in args:
        v = args["activityId"]
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
            return {"activity_id": int(v)}
        return {}
    if "id" in args:
        v = args["id"]
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
            return {"activity_id": int(v)}
        return {}
    return {}


async def _resolve_activity_id_from_query(mcp_session: ClientSession, user_message: str) -> int | None:
    """Resuelve un activity_id directamente desde la consulta del usuario."""
    if not isinstance(user_message, str) or not user_message.strip():
        return None

    target_date = _extract_iso_date_from_text(user_message)
    if target_date:
        by_date = await _find_activity_id_by_date(mcp_session, target_date)
        if by_date is not None:
            return by_date
        return None

    by_name = await _find_activity_id_by_name(mcp_session, user_message)
    return by_name


async def _build_activity_candidates_payload(mcp_session: ClientSession, user_message: str) -> str:
    """Devuelve candidatos de actividades para ayudar al modelo a recuperar activity_id."""
    target_date = _extract_iso_date_from_text(user_message) if isinstance(user_message, str) else None
    collected: list[dict] = []
    start = 0
    limit = 100
    max_pages = 20
    try:
        for _ in range(max_pages):
            raw = await call_tool(mcp_session, "get_activities", {"start": str(start), "limit": str(limit)})
            page_activities, has_more, next_start_val = _parse_activities_response(raw)
            collected.extend(page_activities)
            if not has_more:
                break
            start = next_start_val if next_start_val > start else start + limit
    except (TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, KeyError) as exc:
        payload = {
            "error": "missing_activity_id",
            "message": "No se pudo recuperar listado de actividades para resolver activity_id.",
            "detail": str(exc),
        }
        return json.dumps(payload, ensure_ascii=False)

    activities = collected
    if target_date:
        date_matches = [a for a in activities if _extract_activity_date_iso(a) == target_date]
        if not date_matches:
            # No hay actividad en esa fecha exacta: informar claramente sin mostrar otras fechas
            payload = {
                "error": "no_activity_on_date",
                "target_date": target_date,
                "message": (
                    f"No se encontró ninguna actividad registrada el {target_date} en Garmin Connect. "
                    "Informa al usuario que no hay actividad para esa fecha y pregúntale si quiere "
                    "ver las actividades más recientes disponibles."
                ),
            }
            return json.dumps(payload, ensure_ascii=False)
        activities = date_matches

    compact_candidates = []
    for activity in activities[:20]:
        if not isinstance(activity, dict):
            continue
        activity_id = activity.get("activityId") or activity.get("activity_id") or activity.get("id")
        try:
            activity_id = int(activity_id)
        except (TypeError, ValueError):
            continue
        compact_candidates.append(
            {
                "activity_id": activity_id,
                "date": _extract_activity_date_iso(activity) or "",
                "name": str(activity.get("name") or activity.get("activityName") or "Actividad").strip(),
            }
        )

    payload = {
        "error": "missing_activity_id",
        "query": user_message,
        "target_date": target_date,
        "hint": "Selecciona una actividad de la lista y vuelve a llamar get_activity con activity_id.",
        "candidates": compact_candidates,
    }
    return json.dumps(payload, ensure_ascii=False)
