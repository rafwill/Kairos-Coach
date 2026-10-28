"""
agent/tool_results.py
Resultados de las tools de Garmin para el LLM: poda, series diarias en columnas y filas, récords, listas de actividades y esquema de function calling.
"""

import logging
import math
import json
from typing import Any


from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _is_cycling_activity,
    _parse_hr_zones_list,
    _try_parse_json,
)
from agent.dates import (
    _seconds_to_hhmmss,
    _seconds_to_mmss_or_hhmmss,
)

log = logging.getLogger(__name__)


# Herramientas esenciales para el agente entrenador
# Limitamos el número para no superar los límites de tokens del modelo
# Máximo de caracteres por resultado de herramienta para no exceder el límite de tokens
_MAX_TOOL_RESULT_CHARS = 3000


# Campos de los objetos Garmin que NO deben llegar al LLM:
# - Timestamps de inicio (prStartTimeGMT, startTimeLocal, etc.) → contienen la
#   HORA DEL DÍA en que empezó la actividad, NO la duración. El LLM los confunde
#   con el tiempo de carrera (ej. "17:48:52" es "las 17h48" no "17 horas").
# - IDs internos y metadatos sin valor analítico.
_GARMIN_STRIP_FIELDS = {
    # Timestamps (hora de inicio, NO duración)
    "prStartTimeGMT", "prStartTimeLocal",
    "startTimeGMT", "startTimeLocal", "startTimeUTC",
    "beginTimestamp", "calendarDate",
    # IDs y referencias internas (NO incluir activityId: el LLM lo necesita para llamar get_activity)
    "id", "userProfileId", "ownerId", "deviceId",
    "garminGUID", "uuid", "userId",
    # Metadatos de presentación sin valor para el análisis
    "displayName", "locationName", "countryCode", "timeZoneId",
}


def _strip_garmin_object(obj):
    """Prueba y poda un objeto Garmin de forma recursiva para conservar métricas anidadas
    importantes (como VO2Max, zonas de FC o cargas de entrenamiento) mientras elimina metadatos redundantes.
    """
    if isinstance(obj, list):
        # Limitar longitud de arrays anidados
        return [_strip_garmin_object(item) for item in obj[:4]]
    
    if isinstance(obj, dict):
        cleaned = {}
        # Simplificación de diccionarios pequeños de tipo de actividad/deporte
        if "typeKey" in obj and len(obj) < 10:
            return obj["typeKey"]
            
        for k, v in obj.items():
            if k in _GARMIN_STRIP_FIELDS:
                continue
            if "image" in k.lower() or "url" in k.lower():
                continue
            if k in {"userRoles", "privacy", "userPro", "hasVideo", "favorite", "atpActivity", "parent", "purposeful"}:
                continue
            
            cleaned_v = _strip_garmin_object(v)
            if cleaned_v is not None and cleaned_v != {} and cleaned_v != []:
                if k == "activityType" and isinstance(cleaned_v, dict) and "typeKey" in cleaned_v:
                    cleaned[k] = cleaned_v["typeKey"]
                elif k == "eventType" and isinstance(cleaned_v, dict) and "typeKey" in cleaned_v:
                    cleaned[k] = cleaned_v["typeKey"]
                else:
                    cleaned[k] = cleaned_v
        return cleaned
    
    return obj


# Metadatos de récords personales de Garmin (mapeado de typeId a categoría y formato)
_PR_METADATA = {
    1: {"tipo": "1K", "unidad": "tiempo"},
    2: {"tipo": "1 Milla", "unidad": "tiempo"},
    3: {"tipo": "5K", "unidad": "tiempo"},
    4: {"tipo": "10K", "unidad": "tiempo"},
    5: {"tipo": "Medio Maratón", "unidad": "tiempo"},
    6: {"tipo": "Maratón", "unidad": "tiempo"},
    7: {"tipo": "Carrera más larga", "unidad": "distancia_km"},
    8: {"tipo": "Ciclismo más largo", "unidad": "distancia_km"},
    9: {"tipo": "Ascenso máximo de ciclismo", "unidad": "elevacion_m"},
    11: {"tipo": "40K ciclismo", "unidad": "tiempo"},
    12: {"tipo": "Pasos máximos en un día", "unidad": "pasos"},
    13: {"tipo": "Pasos máximos en una semana", "unidad": "pasos"},
    14: {"tipo": "Pasos máximos en un mes", "unidad": "pasos"},
    15: {"tipo": "Racha récord de objetivo de pasos", "unidad": "dias"}, # Usamos ASCII/UTF-8 completo
    16: {"tipo": "Racha actual de objetivo de pasos", "unidad": "dias"},
    17: {"tipo": "Natación más larga", "unidad": "distancia_m_y_km"},
    18: {"tipo": "100m natación", "unidad": "tiempo"},
    20: {"tipo": "400m natación", "unidad": "tiempo"},
    22: {"tipo": "1000m natación", "unidad": "tiempo"},
    23: {"tipo": "1500m natación", "unidad": "tiempo"},
}


_PR_CATEGORY_TRANSLATIONS = {
    "fastest 1k": "1K más rápido",
    "fastest mile": "Milla más rápida",
    "fastest 5k": "5K más rápido",
    "fastest 10k": "10K más rápido",
    "fastest half marathon": "Media maratón más rápida",
    "fastest marathon": "Maratón más rápida",
    "longest run": "Carrera más larga",
    "longest ride": "Ciclismo más largo",
    "most elevation gain cycling": "Ascenso máximo en ciclismo",
    "fastest 40k cycling": "40K ciclismo más rápido",
    "most steps day": "Máximos pasos en un día",
    "most steps week": "Máximos pasos en una semana",
    "most steps month": "Máximos pasos en un mes",
    "longest daily goal streak": "Racha más larga de objetivo diario",
    "longest weekly goal streak": "Racha más larga de objetivo semanal",
    "longest pool swim": "Natación más larga en piscina",
    "fastest 100m pool swim": "100m piscina más rápido",
    "fastest 500m pool swim": "500m piscina más rápido",
    "fastest 1500m pool swim": "1500m piscina más rápido",
    "fastest 1 mile pool swim": "1 milla piscina más rápida",
}


def _translate_pr_category_es(category: str) -> str:
    """Traduce categorías comunes de PR de Garmin al español para mostrar al usuario."""
    text = (category or "").strip()
    if not text:
        return "Registro"
    lowered = text.lower()
    if lowered in _PR_CATEGORY_TRANSLATIONS:
        return _PR_CATEGORY_TRANSLATIONS[lowered]
    return text


def _compact_personal_records(data: list) -> str:
    """Convierte los récords personales de Garmin a un formato compacto y legible.
    Transforma el campo `value` (unidades raw de Garmin: segundos para tiempos, metros para distancias/alturas, pasos, días)
    al formato adecuado directamente en Python, facilitando la interpretación por el LLM.
    """
    results = []
    for record in data:
        if not isinstance(record, dict):
            continue
        type_id = record.get("typeId") if record.get("typeId") is not None else record.get("type_id")
        value = record.get("value")
        raw_value = record.get("raw_value") if record.get("raw_value") is not None else value
        record_type = record.get("record_type")
        meta = _PR_METADATA.get(type_id)
        
        if meta:
            tipo_name = meta["tipo"]
            unidad = meta["unidad"]
        else:
            tipo_name = _translate_pr_category_es(record_type) if record_type else f"typeId={type_id}"
            unidad = "valor"
            
        entry: dict = {
            "actividad": record.get("activityName") or record.get("activity_name") or "",
            "tipo": tipo_name,
            "deporte": record.get("activityType") or record.get("activity_type") or "",
            "categoria": tipo_name if meta else (_translate_pr_category_es(record_type) if record_type else tipo_name),
            "type_id": type_id,
            "fecha": record.get("date") or "",
        }
        
        if value is not None:
            try:
                if isinstance(value, str) and value.strip() and (":" in value or any(ch.isalpha() for ch in value)):
                    pretty = value.strip()
                    if unidad == "tiempo":
                        entry["tiempo"] = pretty
                    elif unidad in {"distancia_km", "distancia_m_y_km"}:
                        entry["distancia"] = pretty
                    elif unidad == "elevacion_m":
                        entry["elevacion"] = pretty
                    elif unidad == "pasos":
                        entry["pasos"] = pretty
                    elif unidad == "dias":
                        entry["racha"] = pretty
                    entry["valor"] = pretty
                    results.append(entry)
                    continue

                v_float = float(raw_value)
                if unidad == "tiempo":
                    entry["tiempo"] = _seconds_to_mmss_or_hhmmss(v_float)
                    entry["valor"] = entry["tiempo"]
                elif unidad == "distancia_km":
                    entry["distancia"] = f"{v_float / 1000:.2f} km"
                    entry["valor"] = entry["distancia"]
                elif unidad == "distancia_m_y_km":
                    if v_float >= 1000:
                        entry["distancia"] = f"{v_float / 1000:.2f} km"
                    else:
                        entry["distancia"] = f"{v_float:.0f} m"
                    entry["valor"] = entry["distancia"]
                elif unidad == "elevacion_m":
                    entry["elevacion"] = f"{v_float:.1f} m"
                    entry["valor"] = entry["elevacion"]
                elif unidad == "pasos":
                    entry["pasos"] = f"{int(round(v_float)):,}"
                    entry["valor"] = entry["pasos"]
                elif unidad == "dias":
                    entry["racha"] = f"{int(round(v_float))} días"
                    entry["valor"] = entry["racha"]
                else:
                    entry["valor"] = value
            except (ValueError, TypeError):
                entry["valor"] = value
                
        results.append(entry)
    return json.dumps(results, ensure_ascii=False, separators=(",", ":"))


# Tools que devuelven una serie diaria dentro de un objeto ({..., "trend": [{date, ...}, ...]}).
# La poda genérica deja 4 elementos de cada lista anidada: con 15 días el LLM recibía 4 e inventaba el resto.
_DAILY_SERIES_TOOLS = {"get_hrv_trend": "trend", "get_training_load_trend": "trend", "get_vo2max_trend": "trend",
                       "get_rhr_trend": "trend"}


def _daily_series_summary(rows: list[dict], columns: list[str]) -> dict:
    """Por columna: n, mín, máx, media, primero y último (numéricas) o recuento por valor (texto)."""
    summary: dict[str, dict] = {}
    for col in columns:
        if col in {"date", "error"}:
            continue
        pairs = [(r.get("date"), r.get(col)) for r in rows if r.get(col) is not None]
        nums = [(d, v) for d, v in pairs if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums and len(nums) == len(pairs):
            values = [v for _, v in nums]
            summary[col] = {
                "n": len(values), "min": min(values), "max": max(values),
                "mean": round(sum(values) / len(values), 1),
                "first": {"date": nums[0][0], "value": nums[0][1]},
                "last": {"date": nums[-1][0], "value": nums[-1][1]},
            }
        elif pairs:
            counts: dict[str, int] = {}
            for _, v in pairs:
                counts[str(v)] = counts.get(str(v), 0) + 1
            summary[col] = {"n": len(pairs), "counts": counts}
    return summary


def _compact_daily_series(data: dict, key: str) -> str:
    """Serie diaria completa en formato columnas/filas. Si no cabe, quita los días más antiguos y lo dice."""
    rows = [r for r in (data.get(key) or []) if isinstance(r, dict)]
    columns = list(dict.fromkeys(k for r in rows for k in r))
    out = {k: v for k, v in data.items() if k != key}
    # Resumen calculado aquí (sobre todas las filas, también las que no quepan): el LLM contaba mal
    # días y muestras ("13/14" con 14 de 15). Se le pide citar estos valores en vez de contar.
    out["rows_total"] = len(rows)
    out["summary"] = _daily_series_summary(rows, columns)
    out["columns"] = columns
    out["rows"] = [[r.get(c) for c in columns] for r in rows]
    compact = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    omitted = 0
    while len(compact) > _MAX_TOOL_RESULT_CHARS and len(out["rows"]) > 1:
        out["rows"].pop(0)
        omitted += 1
        out["omitted_oldest_days"] = omitted
        compact = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    return compact


def _compact_tool_result(raw: str | None, tool_name: str = "") -> str:
    """
    Compacta el resultado de una herramienta para que quepa en el contexto.
    - get_personal_record(s): conversión específica de segundos a HH:MM:SS.
    - Arrays JSON: conserva hasta 8 elementos y elimina campos metadata.
    - Strings demasiado largos: trunca a _MAX_TOOL_RESULT_CHARS.
    """
    if not raw:
        return "(sin datos)"
    try:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Listas que el MCP entrega como objetos concatenados ("{...} {...}").
            data = _try_parse_json(raw)
            if data is None:
                raise
        # Procesado específico para récords personales
        if tool_name in {"get_personal_records", "get_personal_record"} and isinstance(data, list):
            return _compact_personal_records(data)
        # Procesado específico para body battery: extrae campos clave antes del truncado
        if tool_name == "get_body_battery" and isinstance(data, (dict, list)):
            _bb_list = data if isinstance(data, list) else [data]

            def _bb_score(item: dict) -> int:
                score = 0
                if item.get("body_battery_level") is not None or item.get("bodyBatteryLevel") is not None or item.get("bodyBatteryMostRecentValue") is not None or item.get("current") is not None:
                    score += 100
                if item.get("highestBodyBattery") is not None or item.get("highest") is not None or item.get("body_battery_highest") is not None:
                    score += 10
                if item.get("lowestBodyBattery") is not None or item.get("lowest") is not None or item.get("body_battery_lowest") is not None:
                    score += 10
                if item.get("charged") is not None or item.get("body_battery_charged") is not None or item.get("bodyBatteryCharged") is not None:
                    score += 1
                if item.get("drained") is not None or item.get("body_battery_drained") is not None or item.get("bodyBatteryDrained") is not None:
                    score += 1
                return score

            _bb_candidates = [x for x in _bb_list if isinstance(x, dict)]
            if _bb_candidates:
                _bb = max(_bb_candidates, key=_bb_score)
                _bb_out = {}
                for _k, _aliases in (
                    ("charged",  ["charged", "body_battery_charged", "bodyBatteryCharged"]),
                    ("drained",  ["drained", "body_battery_drained", "bodyBatteryDrained"]),
                    ("highest",  ["highestBodyBattery", "highest", "body_battery_highest"]),
                    ("lowest",   ["lowestBodyBattery", "lowest", "body_battery_lowest"]),
                    ("level",    ["body_battery_level", "bodyBatteryLevel", "bodyBatteryMostRecentValue", "bodyBatteryValue", "current", "value"]),
                ):
                    for _a in _aliases:
                        _v = _bb.get(_a)
                        if _v is not None:
                            _bb_out[_k] = _v
                            break
                if _bb_out:
                    return json.dumps(_bb_out, ensure_ascii=False, separators=(",", ":"))
        # Procesado específico para datos de sueño: extrae solo métricas clave antes del truncado
        # Procesado específico para HRV: extrae campos clave antes del truncado
        if tool_name in _DAILY_SERIES_TOOLS and isinstance(data, dict):
            return _compact_daily_series(data, _DAILY_SERIES_TOOLS[tool_name])
        if tool_name == "get_hrv_data" and isinstance(data, (dict, list)):
            _hd = data[0] if isinstance(data, list) and data else data
            # Garmin trae los valores del día dentro de hrvSummary; la raíz solo lleva lecturas sueltas.
            if isinstance(_hd, dict) and isinstance(_hd.get("hrvSummary"), dict):
                _hd = _hd["hrvSummary"]
            if isinstance(_hd, dict):
                _hrv_out = {}
                for _k, _aliases in (
                    ("lastNightAvg",  ["lastNightAvg", "last_night_avg_hrv_ms", "avgOvernightHrv", "avgHrv", "averageHrv", "lastNight"]),
                    ("weeklyAvg",     ["weeklyAvg", "weekly_avg_hrv_ms", "weeklyAvgHrv"]),
                    ("status",        ["status", "hrvStatus"]),
                    ("high5Min",      ["last_night_5min_high_hrv_ms", "highHrv", "high5Min"]),
                ):
                    for _a in _aliases:
                        _v = _hd.get(_a)
                        if _v is not None:
                            _hrv_out[_k] = _v
                            break
                if _hrv_out:
                    return json.dumps(_hrv_out, ensure_ascii=False, separators=(",", ":"))
        if tool_name in {"get_sleep_data", "get_sleep_summary"} and isinstance(data, (dict, list)):
            _sd = data[0] if isinstance(data, list) and data else data
            if isinstance(_sd, dict):
                _dto = _sd.get("dailySleepDTO") or _sd
                _score_nested = ((_dto.get("sleepScores") or {}).get("overall") or {})
                _sleep_out = {}
                for _k, _aliases in (
                    ("sleepTimeSeconds",  ["sleepTimeSeconds", "sleep_time_seconds", "ageTimeSeconds"]),
                    ("deepSleepSeconds",  ["deepSleepSeconds", "deep_sleep_seconds"]),
                    ("lightSleepSeconds", ["lightSleepSeconds", "light_sleep_seconds"]),
                    ("remSleepSeconds",   ["remSleepSeconds", "rem_sleep_seconds"]),
                    ("wakeSeconds",       ["awakeSleepSeconds", "wakeSeconds", "wake_seconds"]),
                ):
                    for _a in _aliases:
                        _v = _dto.get(_a)
                        if _v is not None:
                            _sleep_out[_k] = _v
                            break
                # Score: puede estar plano o anidado
                _score = (_dto.get("sleepScore") or _dto.get("sleepScoreValue")
                          or _score_nested.get("value")
                          or _sd.get("sleepScore"))
                if _score is not None:
                    _sleep_out["sleepScore"] = _score
                _quality = _dto.get("sleepQualityTypePK") or _dto.get("sleepQuality")
                if _quality is not None:
                    _sleep_out["sleepQuality"] = _quality
                return json.dumps(_sleep_out, ensure_ascii=False, separators=(",", ":"))
        # Añadir campos normalizados útiles para análisis de actividades
        if tool_name == "get_activity" and isinstance(data, dict):
            # Duración (segundos -> HH:MM:SS)
            act_type_raw = data.get("activityType") or data.get("type") or ""
            duration = data.get("duration") or data.get("movingDuration") or data.get("duration_seconds")
            distance = data.get("distance") or data.get("distance_meters")
            avg_hr   = data.get("avgHr") or data.get("avg_hr_bpm") or data.get("averageHR")
            max_hr   = data.get("maxHr") or data.get("max_hr_bpm") or data.get("maxHR")
            dur_s = None
            try:
                if duration is not None:
                    dur_s = float(duration)
                    data["duration_hhmmss"] = _seconds_to_hhmmss(dur_s)
                    data["duration_hours"]  = round(dur_s / 3600, 2)
            except (ValueError, TypeError):
                dur_s = None
            try:
                if distance is not None:
                    dist_km = float(distance) / 1000
                    data["distance_km"] = round(dist_km, 2)
                    if dur_s and dist_km > 0:
                        if _is_cycling_activity(act_type_raw):
                            speed_kmh = dist_km / (dur_s / 3600)
                            data["velocidad_media_kmh"] = round(speed_kmh, 1)
                            # Convertir velocidad máxima si viene en m/s
                            max_spd = data.get("maxSpeed") or data.get("max_speed_ms")
                            if max_spd is not None:
                                try:
                                    ms = float(max_spd)
                                    # Garmin devuelve maxSpeed en m/s
                                    data["velocidad_maxima_kmh"] = round(ms * 3.6, 1)
                                except (ValueError, TypeError):
                                    pass
                        else:
                            pace_s_per_km = dur_s / dist_km
                            pace_min = int(pace_s_per_km // 60)
                            pace_sec = int(pace_s_per_km % 60)
                            data["ritmo_medio_min_km"] = f"{pace_min}:{pace_sec:02d} min/km"
                # Eliminar campos de velocidad en m/s (confusos/irrelevantes):
                # running usa ritmo_medio_min_km, ciclismo usa velocidad_media_kmh.
                # Cubrimos todos los alias posibles de garmin-mcp (camelCase y snake_case).
                for _spd_k in (
                    "avgSpeed", "averageSpeed", "maxSpeed", "minSpeed",
                    "avg_speed", "average_speed", "max_speed", "min_speed",
                    "avg_speed_ms", "max_speed_ms",
                    "enhancedAvgSpeed", "enhancedMaxSpeed",
                    "enhanced_avg_speed", "enhanced_max_speed",
                    "movingSpeed", "moving_speed",
                    "speed",
                ):
                    data.pop(_spd_k, None)
                # Potencia: para actividades de carrera (sin potenciómetro físico tipo Stryd)
                # la potencia es una estimación interna de Garmin — se etiqueta como tal.
                # Para ciclismo la potencia proviene de un potenciómetro real → se deja sin etiquetar.
                if not _is_cycling_activity(act_type_raw):
                    for _pow_src, _pow_dst in (
                        ("avgPower",   "potencia_media_estimada_w"),
                        ("maxPower",   "potencia_maxima_estimada_w"),
                        ("avg_power",  "potencia_media_estimada_w"),
                        ("max_power",  "potencia_maxima_estimada_w"),
                    ):
                        _pv = data.pop(_pow_src, None)
                        if _pv is not None and _pow_dst not in data:
                            try:
                                data[_pow_dst] = round(float(_pv), 1)
                            except (ValueError, TypeError):
                                pass
            except (ValueError, TypeError):
                pass
            # Zonas de FC: prioridad 1 = datos reales del dispositivo; prioridad 2 = estimación gaussiana
            try:
                # Intento 1: datos reales (heartRateZones incluido en get_activity)
                _raw_hr_zones_str = json.dumps(
                    data.get("heartRateZones")
                    or data.get("hr_zones")
                    or data.get("hrZones")
                    or data.get("timeInHeartRateZones")
                    or data.get("heartRateTimeInZones")
                ) if any(data.get(k) for k in ("heartRateZones","hr_zones","hrZones",
                         "timeInHeartRateZones","heartRateTimeInZones")) else None
                _zones_parsed_compact = _parse_hr_zones_list(_raw_hr_zones_str) if _raw_hr_zones_str else None
                if _zones_parsed_compact:
                    _total_z_secs = sum(float(z.get("secsInZone") or 0) for z in _zones_parsed_compact)
                    if _total_z_secs > 0:
                        zonas_reales = {}
                        for z in sorted(_zones_parsed_compact, key=lambda x: int(x.get("zoneNumber") or 0)):
                            _zn  = int(z.get("zoneNumber") or 0)
                            _zs  = float(z.get("secsInZone") or 0)
                            _pct = round(_zs / _total_z_secs * 100, 1)
                            _lo  = z.get("minHeartRateIn") or "?"
                            _hi  = z.get("maxHeartRateIn") or "?"
                            _mins = round(_zs / 60, 0)
                            zonas_reales[f"Z{_zn}_{_lo}-{_hi}bpm"] = f"{_pct}% (~{int(_mins)} min)"
                        data["zonas_fc_reales"] = zonas_reales
                        data["nota_zonas"] = "Zonas reales desde el dispositivo Garmin (configuración del usuario)."
                elif avg_hr and max_hr:
                    # Intento 2: estimación gaussiana como fallback
                    fcmax = float(max_hr)
                    fcmed = float(avg_hr)
                    z_bounds = [
                        ("Z1_recuperacion", 0,    0.60),
                        ("Z2_base_aerobica", 0.60, 0.70),
                        ("Z3_umbral_aerobico", 0.70, 0.80),
                        ("Z4_umbral_anaerobico", 0.80, 0.90),
                        ("Z5_vo2max", 0.90, 1.10),
                    ]
                    sigma = 0.10 * fcmax
                    def normal_cdf(x, mu, s):
                        return 0.5 * (1 + math.erf((x - mu) / (s * math.sqrt(2))))
                    zone_pct = {}
                    total = 0.0
                    for name, lo_pct, hi_pct in z_bounds:
                        lo_bpm = lo_pct * fcmax
                        hi_bpm = hi_pct * fcmax
                        p = normal_cdf(hi_bpm, fcmed, sigma) - normal_cdf(lo_bpm, fcmed, sigma)
                        zone_pct[name] = round(max(p, 0) * 100, 1)
                        total += zone_pct[name]
                    if total > 0:
                        zone_pct = {k: round(v / total * 100, 1) for k, v in zone_pct.items()}
                    if dur_s:
                        for name, pct in zone_pct.items():
                            mins = round(dur_s * pct / 100 / 60, 0)
                            zone_pct[name] = f"{pct}% (~{int(mins)} min)"
                    data["zonas_fc_estimadas"] = zone_pct
                    data["nota_zonas"] = (
                        f"ESTIMACIÓN gaussiana (FC_media={int(fcmed)}bpm, FC_max={int(fcmax)}bpm). "
                        "Puede diferir de las zonas reales configuradas en Garmin."
                    )
            except (TypeError, ValueError, OverflowError):
                pass
            # Hidratacion estimada
            try:
                if dur_s:
                    dur_h = dur_s / 3600
                    hydration_low  = round(dur_h * 0.5, 1)
                    hydration_high = round(dur_h * 0.8, 1)
                    data["hidratacion_estimada_litros"] = f"{hydration_low}-{hydration_high}L (base; +25% si temp >25C)"
            except (TypeError, ValueError):
                pass
        if isinstance(data, list):
            if len(data) > 8:
                # Medición del metatooling (26/10): cuántas veces el LLM recibe una lista recortada.
                log.info("[TOOL] %s lista recortada: %d → 8 elementos", tool_name or "?", len(data))
            data = data[:8]  # máximo 8 elementos de arrays
            data = [
                _strip_garmin_object(item) if isinstance(item, dict) else item
                for item in data
            ]
        elif isinstance(data, dict):
            data = _strip_garmin_object(data)
        compact = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(compact) > _MAX_TOOL_RESULT_CHARS:
            log.info("[TOOL] %s resultado cortado: %d → %d caracteres", tool_name or "?", len(compact), _MAX_TOOL_RESULT_CHARS)
            compact = compact[:_MAX_TOOL_RESULT_CHARS] + "...(truncado)"
        return compact
    except (json.JSONDecodeError, TypeError):
        if len(raw) > _MAX_TOOL_RESULT_CHARS:
            log.info("[TOOL] %s resultado cortado: %d → %d caracteres", tool_name or "?", len(raw), _MAX_TOOL_RESULT_CHARS)
            return raw[:_MAX_TOOL_RESULT_CHARS] + "...(truncado)"
        return raw


def _build_tools_schema(tools: list[dict]) -> list[dict]:
    """Convierte las herramientas MCP al formato de function calling de OpenAI."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in tools
    ]


def _flatten_garmin_activity(act: dict) -> dict:
    """Sube a primer nivel los campos de summaryDTO/activityTypeDTO de get_activity (sin pisar los existentes)."""
    if not isinstance(act, dict):
        return act
    flat = dict(act)
    summary = act.get("summaryDTO")
    if isinstance(summary, dict):
        for key, value in summary.items():
            if flat.get(key) is None and value is not None:
                flat[key] = value
    type_dto = act.get("activityTypeDTO")
    if flat.get("activityType") is None and isinstance(type_dto, dict) and type_dto.get("typeKey"):
        flat["activityType"] = type_dto["typeKey"]
    return flat


def _is_activity_details_payload(raw: Any) -> bool:
    """True si es un payload JSON de get_activity_details; call_tool devuelve los errores como texto."""
    return isinstance(raw, str) and raw.lstrip().startswith("{")


def _activity_id_int(activity: dict) -> int | None:
    raw = activity.get("id") or activity.get("activityId") if isinstance(activity, dict) else None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _extract_activities_list(payload: Any) -> list[dict]:
    """Extrae una lista de actividades desde distintas formas de respuesta."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        candidates = payload.get("activities")
        if isinstance(candidates, list):
            return [x for x in candidates if isinstance(x, dict)]
        # Algunos wrappers compactan `get_activities` y devuelven una sola actividad.
        if payload.get("activityId") is not None or payload.get("activityName") is not None:
            return [payload]
    return []


def _is_no_data_result(raw_result: str | None) -> bool:
    """Detecta respuestas de herramientas que indican ausencia de datos."""
    if not raw_result:
        return True
    text = raw_result.strip().lower()
    return (
        "no" in text
        and "data" in text
        and ("found" in text or "available" in text)
    )


def _parse_activities_response(raw: str | None) -> tuple[list[dict], bool, int]:
    """Parsea la respuesta de get_activities en (activities, has_more, next_start).
    Soporta tanto array JSON directo como objeto {activities: [...]}
    """
    if not raw or not raw.strip():
        return [], False, 0
    stripped = raw.strip()

    def _extract_docs(text: str) -> list[Any]:
        """Extrae uno o varios documentos JSON de una cadena.

        Algunos backends de `get_activities` pueden devolver varios objetos JSON
        concatenados en el mismo payload en lugar de un array único. Este helper
        itera con `raw_decode` para recuperar todos los documentos válidos.
        """
        docs: list[Any] = []
        decoder = json.JSONDecoder()
        idx = 0
        n = len(text)
        while idx < n:
            while idx < n and text[idx].isspace():
                idx += 1
            if idx >= n:
                break
            try:
                obj, end = decoder.raw_decode(text, idx)
            except json.JSONDecodeError:
                break
            docs.append(obj)
            idx = end

        if docs:
            return docs

        # Fallback: algunos payloads llegan en líneas JSON independientes.
        line_docs: list[Any] = []
        for line in text.splitlines():
            l = line.strip()
            if not l:
                continue
            try:
                line_docs.append(json.loads(l))
            except json.JSONDecodeError:
                continue
        return line_docs

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        docs = _extract_docs(stripped)
        if not docs:
            return [], False, 0

        # Caso común: N objetos de actividad concatenados.
        if all(isinstance(doc, dict) for doc in docs):
            if all(("activityId" in doc) or ("id" in doc) or ("activity_id" in doc) for doc in docs):
                activities = [doc for doc in docs if isinstance(doc, dict)]
                log.debug("get_activities -> %d objetos JSON concatenados (actividad)", len(activities))
                return activities, False, 0

            # O un único documento objeto válido envuelto en docs.
            if len(docs) == 1:
                data = docs[0]
            else:
                # Mezcla heterogénea: intentar agregar actividades de cada objeto.
                activities: list[dict] = []
                has_more = False
                next_start = 0
                for doc in docs:
                    if not isinstance(doc, dict):
                        continue
                    block = doc.get("activities") or doc.get("activityList") or doc.get("list") or []
                    if isinstance(block, list):
                        activities.extend(a for a in block if isinstance(a, dict))
                    has_more = has_more or bool(doc.get("has_more") or doc.get("hasMore"))
                    try:
                        cand = int(doc.get("next_start") or doc.get("nextStart") or 0)
                    except (TypeError, ValueError):
                        cand = 0
                    next_start = max(next_start, cand)
                if activities:
                    log.debug(
                        "get_activities -> %d actividades agregadas desde %d docs concatenados",
                        len(activities),
                        len(docs),
                    )
                    return activities, has_more, next_start
                return [], False, 0
        else:
            # Lista/mixto de docs JSON: agrupar dicts de actividad.
            activities = [doc for doc in docs if isinstance(doc, dict) and (("activityId" in doc) or ("id" in doc) or ("activity_id" in doc))]
            if activities:
                log.debug("get_activities -> %d docs mixtos interpretados como actividades", len(activities))
                return activities, False, 0
            return [], False, 0

    # Formato lista directa: [{...}, {...}]
    if isinstance(data, list):
        activities = [a for a in data if isinstance(a, dict)]
        log.debug(f"get_activities -> lista directa con {len(activities)} actividades")
        return activities, False, 0

    # Formato objeto: {"activities": [...], "has_more": ..., "next_start": ...}
    if isinstance(data, dict):
        # Algunos servidores MCP devuelven las actividades bajo distintas claves
        activities = data.get("activities") or data.get("activityList") or data.get("list") or []
        if not isinstance(activities, list):
            activities = []
        activities = [a for a in activities if isinstance(a, dict)]
        has_more = bool(data.get("has_more") or data.get("hasMore"))
        next_start = int(data.get("next_start") or data.get("nextStart") or 0)
        log.debug(f"get_activities -> objeto con {len(activities)} actividades, has_more={has_more}")
        return activities, has_more, next_start

    return [], False, 0
