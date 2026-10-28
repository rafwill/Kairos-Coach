"""
agent/kairos_tools.py
Tools internas de Kairos (kairos_*) que recibe el LLM: carga de load_metrics_daily, correlación entre métricas y resumen por deporte. Sus resultados no pasan por la poda de las de Garmin.
"""

import logging
import json
from datetime import date, timedelta


from agent.mcp.client import (
    call_tool,
)
from agent.tool_results import (
    _parse_activities_response,
)
from agent.data_access import (
    _load_metrics_series,
)
from agent.garmin_data import (
    _extract_activity_date_iso,
)

log = logging.getLogger(__name__)


def _kairos_load_trends(profile: dict, weeks_back: int = 8) -> str:
    """TSS, CTL (Estado físico), ATL (Fatiga) y TSB (Forma) de `load_metrics_daily` en una sola llamada (28/10).

    Antes devolvía una métrica por llamada y el LLM hacía cuatro vueltas para comparar periodos (≈ 25.000 tokens
    cada una). Columnas y filas, como las series de tendencia: diario de los últimos 14 días y semanal (lunes a
    domingo) de todo el periodo; en la semana, TSS es la suma y CTL/ATL/TSB el valor del último día con dato.
    """
    series = _load_metrics_series()
    if not series:
        return json.dumps({"error": "Sin datos históricos de carga/fatiga. Ejecuta una sesión para que el sistema los calcule.", "n": 0}, ensure_ascii=False)
    weeks_back = max(1, min(int(weeks_back), 52))
    cutoff = (date.today() - timedelta(days=weeks_back * 7)).isoformat()
    filtered = [r for r in series if str(r.get("date") or "") >= cutoff and r.get("date")]
    if not filtered:
        return json.dumps({"error": f"Sin datos en las últimas {weeks_back} semanas.", "n": 0}, ensure_ascii=False)
    metrics = ("tss", "ctl", "atl", "tsb")

    def _row(label: str, rows: list[dict], tss_sum: bool) -> list:
        last = rows[-1]
        tss = sum(float(r.get("tss") or 0.0) for r in rows) if tss_sum else float(last.get("tss") or 0.0)
        return [label, round(tss, 1)] + [round(float(last.get(m) or 0.0), 1) for m in metrics[1:]]

    today = date.today()
    first_monday = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks_back - 1)
    weekly = []
    for w in range(weeks_back):
        mon = first_monday + timedelta(weeks=w)
        sun = mon + timedelta(days=6)
        rows = [r for r in filtered if mon.isoformat() <= str(r["date"]) <= sun.isoformat()]
        if rows:
            weekly.append(_row(f"{mon.strftime('%d/%m')}–{sun.strftime('%d/%m/%Y')}", rows, tss_sum=True))
    daily = [_row(str(r["date"]), [r], tss_sum=False) for r in filtered[-14:]]
    return json.dumps({
        "weeks_back": weeks_back,
        "latest": dict(zip(("date",) + metrics, _row(str(filtered[-1]["date"]), [filtered[-1]], tss_sum=False))),
        "daily": {"columns": ["date", *metrics], "rows": daily},
        "weekly": {"columns": ["week", "tss_semana", "ctl_fin", "atl_fin", "tsb_fin"], "rows": weekly},
        "nota": "Fuente: load_metrics_daily (TSS de Kairos por actividad y CTL/ATL/TSB calculados con él). "
                "Una semana sin fila no tiene datos en la serie; una con tss_semana 0 no tuvo actividades.",
    }, ensure_ascii=False, separators=(",", ":"))


def _kairos_correlate(profile: dict, metric_a: str, metric_b: str, weeks_back: int = 8) -> str:
    """Calcula la correlación de Pearson entre dos métricas de carga/fatiga."""
    valid = {"tss", "atl", "ctl", "tsb"}
    metric_a = str(metric_a or "tss").strip().lower()
    metric_b = str(metric_b or "tsb").strip().lower()
    if metric_a not in valid:
        return json.dumps({"error": f"Métrica A '{metric_a}' no válida."}, ensure_ascii=False)
    if metric_b not in valid:
        return json.dumps({"error": f"Métrica B '{metric_b}' no válida."}, ensure_ascii=False)
    if metric_a == metric_b:
        return json.dumps({"error": "Las dos métricas deben ser distintas."}, ensure_ascii=False)
    series = _load_metrics_series()
    if not series:
        return json.dumps({"error": "Sin datos históricos de carga/fatiga.", "n": 0}, ensure_ascii=False)
    weeks_back = max(2, min(int(weeks_back), 52))
    cutoff = (date.today() - timedelta(days=weeks_back * 7)).isoformat()
    filtered = [r for r in series if str(r.get("date") or "") >= cutoff]
    n = len(filtered)
    if n < 7:
        return json.dumps({"error": f"Datos insuficientes ({n} días). Necesitas ≥7 días de historial.", "n": n}, ensure_ascii=False)
    vals_a = [float(r.get(metric_a, 0.0)) for r in filtered]
    vals_b = [float(r.get(metric_b, 0.0)) for r in filtered]
    mean_a = sum(vals_a) / n
    mean_b = sum(vals_b) / n
    num = sum((a - mean_a) * (b - mean_b) for a, b in zip(vals_a, vals_b))
    denom_a = (sum((a - mean_a) ** 2 for a in vals_a)) ** 0.5
    denom_b = (sum((b - mean_b) ** 2 for b in vals_b)) ** 0.5
    if denom_a < 1e-9 or denom_b < 1e-9:
        return json.dumps({"error": "Una métrica no tiene variación suficiente.", "n": n}, ensure_ascii=False)
    r = max(-1.0, min(1.0, num / (denom_a * denom_b)))
    abs_r = abs(r)
    strength = "fuerte" if abs_r >= 0.7 else ("moderada" if abs_r >= 0.4 else ("débil" if abs_r >= 0.2 else "sin correlación significativa"))
    direction = "positiva" if r > 0 else "negativa"
    return json.dumps({
        "metric_a": metric_a, "metric_b": metric_b, "n_days": n, "weeks_back": weeks_back,
        "pearson_r": round(r, 3), "strength": strength, "direction": direction,
        "interpretation": f"Correlación {strength} {direction} (r={r:.3f}, N={n} días). Cuando {metric_a} sube, {metric_b} tiende a {'subir' if r > 0 else 'bajar'}.",
        "nota": f"Basado en {n} días ({weeks_back} semanas). {'Representativo.' if n >= 21 else 'Pocos datos, tomar con cautela.'}",
    }, ensure_ascii=False, separators=(",", ":"))


async def _kairos_weekly_sport_breakdown(mcp_session, weeks_back: int = 4, sport_type: str = "") -> str:
    """Agrega actividades por deporte en las últimas N semanas."""
    weeks_back = max(1, min(int(weeks_back), 12))
    end_date = date.today()
    start_date = end_date - timedelta(days=weeks_back * 7)
    collected: list[dict] = []
    start_idx = 0
    limit = 100
    for _ in range(10):
        try:
            raw = await call_tool(mcp_session, "get_activities", {"start": str(start_idx), "limit": str(limit)})
            activities, has_more, next_start = _parse_activities_response(raw)
        except (TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, KeyError) as exc:
            log.debug("_kairos_weekly_sport_breakdown: get_activities falló: %s", exc)
            break
        past_window = False
        for act in activities:
            act_date_iso = _extract_activity_date_iso(act)
            if not act_date_iso:
                continue
            try:
                act_d = date.fromisoformat(act_date_iso)
            except ValueError:
                continue
            if act_d < start_date:
                past_window = True
                break
            if act_d <= end_date:
                collected.append(act)
        if not has_more or past_window:
            break
        start_idx = next_start if next_start > start_idx else start_idx + limit
    sport_filter = str(sport_type or "").strip().lower()
    breakdown: dict[str, dict] = {}
    for act in collected:
        sport = act.get("activityType") or act.get("type") or "Otro"
        if isinstance(sport, dict):
            sport = sport.get("typeKey") or "Otro"
        sport = str(sport).strip()
        sport_key = sport.replace("_", " ").capitalize()
        if sport_filter and sport_filter not in sport.lower():
            continue
        dur_s = float(act.get("duration") or act.get("movingDuration") or 0.0)
        dist_m = float(act.get("distance") or 0.0)
        if sport_key not in breakdown:
            breakdown[sport_key] = {"count": 0, "duration_h": 0.0, "distance_km": 0.0}
        breakdown[sport_key]["count"] += 1
        breakdown[sport_key]["duration_h"] += dur_s / 3600
        breakdown[sport_key]["distance_km"] += dist_m / 1000
    for k in breakdown:
        breakdown[k]["duration_h"] = round(breakdown[k]["duration_h"], 1)
        breakdown[k]["distance_km"] = round(breakdown[k]["distance_km"], 2)
    total_acts = sum(v["count"] for v in breakdown.values())
    total_hours = round(sum(v["duration_h"] for v in breakdown.values()), 1)
    return json.dumps({
        "period": f"{start_date.strftime('%d/%m/%Y')} – {end_date.strftime('%d/%m/%Y')}",
        "weeks": weeks_back, "total_activities": total_acts, "total_hours": total_hours,
        "by_sport": breakdown,
        "nota": f"Basado en {total_acts} actividades en las últimas {weeks_back} semanas.",
    }, ensure_ascii=False, separators=(",", ":"))


_KAIROS_INTERNAL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "kairos_load_trends",
            "description": (
                "Devuelve en una sola llamada TSS, CTL (Estado físico), ATL (Fatiga) y TSB (Forma) de Kairos: diario de "
                "los últimos 14 días y semanal (TSS sumado, CTL/ATL/TSB al final de la semana) de las semanas pedidas. "
                "ÚSALA como PRIMERA opción para CUALQUIER pregunta sobre carga, fatiga o forma: "
                "'¿cuál fue mi TSS ayer?', '¿cuánto TSS llevo esta semana?', '¿cómo está mi CTL (Estado físico)/ATL (Fatiga)/TSB (Forma)?', "
                "'evolución de carga', '¿estoy en sobreentrenamiento?'. "
                "IMPORTANTE: los endpoints de actividades Garmin NO devuelven TSS — esta tool es la única fuente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "weeks_back": {"type": "integer", "description": "Semanas hacia atrás (1–52, por defecto 8); para comparar meses, las que los cubran"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kairos_correlate",
            "description": "Calcula la correlación de Pearson entre dos métricas de carga/fatiga (TSS, CTL (Estado físico), ATL (Fatiga), TSB (Forma)). Úsalo para preguntas como '¿correlaciona mi carga con mi forma?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_a": {"type": "string", "enum": ["tss", "atl", "ctl", "tsb"], "description": "Primera métrica"},
                    "metric_b": {"type": "string", "enum": ["tss", "atl", "ctl", "tsb"], "description": "Segunda métrica (distinta de metric_a)"},
                    "weeks_back": {"type": "integer", "description": "Semanas de historial (2–52, por defecto 8)"},
                },
                "required": ["metric_a", "metric_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kairos_weekly_sport_breakdown",
            "description": "Devuelve el desglose de actividades por deporte (sesiones, horas, km) en las últimas N semanas. Úsalo para preguntas sobre distribución de carga entre disciplinas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "weeks_back": {"type": "integer", "description": "Semanas hacia atrás (1–12, por defecto 4)"},
                    "sport_type": {"type": "string", "description": "Filtrar por deporte (ej: 'running', 'cycling'). Vacío = todos."},
                },
                "required": [],
            },
        },
    },
]
