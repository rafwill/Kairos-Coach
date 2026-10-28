"""
agent/routes/period.py
Ruta de actividades de un periodo: totales (km, horas, actividades, desnivel, días sin entrenar) y la más larga, sumados en Python; detectores de esas preguntas.
"""

import re
from datetime import date, timedelta


from agent.mcp.client import (
    call_tool,
)
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _try_parse_json,
)
from agent.dates import (
    _MONTHS_ES,
    _MONTH_NAMES_ES,
    _fmt_km,
    _seconds_to_hhmmss,
)
from agent.tool_results import (
    _extract_activities_list,
)
from agent.garmin_data import (
    _activity_family,
    _extract_activity_date_iso,
)


_PERIOD_MONTH_RE = re.compile(
    r"\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b"
    r"(?:\s+(?:de|del)\s+(\d{4}))?"
)


def _resolve_activity_period(user_message: str, today: date | None = None) -> tuple[date, date, str] | None:
    """Periodo de una pregunta (inicio, fin, etiqueta), o None. El fin nunca pasa de hoy.

    "este mes", "el mes pasado", "este año", "esta semana", "la semana pasada" y un mes por su nombre
    ("septiembre", "septiembre de 2025"); un mes posterior al actual sin año es el del año pasado.
    """
    today = today or date.today()
    text = (user_message or "").lower()
    if "semana pasada" in text or "semana anterior" in text:
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6), f"la semana del {monday.strftime('%d/%m')} al {(monday + timedelta(days=6)).strftime('%d/%m')}"
    if "esta semana" in text:
        monday = today - timedelta(days=today.weekday())
        return monday, today, f"esta semana ({monday.strftime('%d/%m')} → {today.strftime('%d/%m')})"
    if "mes pasado" in text or "mes anterior" in text:
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, f"{_MONTH_NAMES_ES[end.month - 1]} de {end.year}"
    if "este mes" in text or "en lo que va de mes" in text:
        return today.replace(day=1), today, f"{_MONTH_NAMES_ES[today.month - 1]} de {today.year} (hasta hoy)"
    if "este año" in text or "en lo que va de año" in text:
        return date(today.year, 1, 1), today, f"{today.year} (hasta hoy)"
    m = _PERIOD_MONTH_RE.search(text)
    if m:
        month = _MONTHS_ES[m.group(1)]
        year = int(m.group(2)) if m.group(2) else (today.year if month <= today.month else today.year - 1)
        start = date(year, month, 1)
        end = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
        if start > today:
            return None
        return start, min(end, today), f"{_MONTH_NAMES_ES[month - 1]} de {year}" + (" (hasta hoy)" if end > today else "")
    return None


_SPORT_REQUESTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"\btrail\b", ("Trail",)),
    (r"\bcorr(?:id[oa]|er|o)\b|\bcarreras?\b|\brunning\b|\btiradas?\b|\brodajes?\b", ("Carrera", "Trail")),
    (r"\bbici\b|\bciclismo\b|\bpedale", ("Ciclismo",)),
    (r"\bnad(?:ado|ar)\b|\bnataci[oó]n\b", ("Natación",)),
    (r"\bcamin(?:ado|ar)\b|\bandado\b|\bsenderismo\b", ("Caminar / senderismo",)),
    (r"\bfuerza\b|\bpesas\b|\bgimnasio\b", ("Fuerza",)),
)


def _requested_families(user_message: str) -> tuple[str, ...] | None:
    text = (user_message or "").lower()
    for pattern, families in _SPORT_REQUESTS:
        if re.search(pattern, text):
            return families
    return None


_DISTANCE_Q_RE = re.compile(
    r"cu[aá]nt[oa]s?\s+(?:km|kil[oó]metros|distancia)|qu[eé]\s+distancia|kil[oó]metros\s+(?:he|llevo|hice)|"
    r"km\s+(?:he|llevo|hice)|distancia\s+total|total\s+de\s+(?:km|kil[oó]metros)"
)


# Totales del periodo que el LLM sumaba él (28/10): horas, actividades, desnivel, días entrenados o sin entrenar.
_TOTALS_Q_RE = re.compile(
    r"cu[aá]nt[oa]s?\s+(?:horas|tiempo|actividades|sesiones|entrenos|entrenamientos|salidas|desnivel)\b|"
    r"cu[aá]nto\s+(?:he\s+|hice\s+)?(?:entrenado|corrido|nadado|pedaleado|caminado|andado|rodado)\b|"
    r"horas\s+(?:he|llevo|hice)\b|tiempo\s+total|desnivel\s+(?:total|acumulado)|"
    r"d[ií]as\s+(?:he\s+|llevo\s+)?(?:entrenado|descansado|sin\s+entrenar|de\s+descanso)"
)


_LONGEST_Q_RE = re.compile(r"m[aá]s\s+larg[oa]s?|mayor\s+distancia|m[aá]s\s+kil[oó]metros")


_ACTIVITY_NOUN_RE = re.compile(r"actividad|salida|carrera|tirada|entreno|entrenamiento|sesi[oó]n|rodaje|ruta")


def _is_period_distance_intent(user_message: str) -> bool:
    text = (user_message or "").lower()
    return bool(_DISTANCE_Q_RE.search(text)) and _resolve_activity_period(text) is not None


def _is_period_totals_intent(user_message: str) -> bool:
    text = (user_message or "").lower()
    return bool(_TOTALS_Q_RE.search(text)) and _resolve_activity_period(text) is not None


def _is_period_longest_intent(user_message: str) -> bool:
    text = (user_message or "").lower()
    return (bool(_LONGEST_Q_RE.search(text)) and bool(_ACTIVITY_NOUN_RE.search(text))
            and _resolve_activity_period(text) is not None)


async def _period_activities(mcp_session, start: date, end: date) -> list[dict]:
    """Todas las actividades del periodo: el servidor pagina (100 por defecto) y un año tiene más."""
    acts: list[dict] = []
    for page in range(20):
        raw = await call_tool(mcp_session, "get_activities_by_date", {
            "start_date": start.isoformat(), "end_date": end.isoformat(), "page": page, "page_size": 200})
        parsed = _try_parse_json(raw)
        acts += _extract_activities_list(parsed if parsed is not None else raw)
        if not (isinstance(parsed, dict) and parsed.get("has_more")):
            break
    out = []
    for act in acts:
        iso = _extract_activity_date_iso(act) if isinstance(act, dict) else None
        if iso and start.isoformat() <= iso <= end.isoformat():
            out.append(act)
    return sorted(out, key=lambda a: _extract_activity_date_iso(a) or "")


async def _build_period_activities_markdown(mcp_session, user_message: str) -> str:
    """Totales de un periodo (km, tiempo, actividades, desnivel, días con y sin actividad) o su actividad más
    larga, por deporte, con los datos de Garmin."""
    start, end, label = _resolve_activity_period(user_message)  # el router solo entra con periodo
    families = _requested_families(user_message)
    acts = [a for a in await _period_activities(mcp_session, start, end)
            if families is None or _activity_family(a) in families]
    sport_label = " y ".join(f.lower() for f in families) if families else "todas las actividades"
    footer = ["", "## 🎯 Próximo paso",
              "- Fuente: respuesta determinista (actividades de Garmin del periodo, sin LLM)."]
    if not acts:
        return "\n".join(["## 🧭 Resumen",
                          f"No hay actividades de {sport_label} en {label} ({start.strftime('%d/%m/%Y')} → {end.strftime('%d/%m/%Y')}).",
                          *footer])

    def _num(act, key):
        try:
            return float(act.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _line(act):
        iso = _extract_activity_date_iso(act) or ""
        when = date.fromisoformat(iso).strftime("%d/%m") if iso else "?"
        dist = _num(act, "distance")
        return (f"- {when} · {_activity_family(act)} · {str(act.get('activityName') or 'Actividad').strip()} · "
                f"{_fmt_km(dist) if dist > 0 else 'sin distancia'} · {_seconds_to_hhmmss(_num(act, 'duration'))}"
                + (f" · +{_num(act, 'elevationGain'):.0f} m" if _num(act, 'elevationGain') > 0 else ""))

    if (_is_period_longest_intent(user_message) and not _is_period_distance_intent(user_message)
            and not _is_period_totals_intent(user_message)):
        by_distance = max(acts, key=lambda a: (_num(a, "distance"), _num(a, "duration")))
        by_time = max(acts, key=lambda a: (_num(a, "duration"), _num(a, "distance")))
        lines = ["## 🧭 Resumen"]
        if _num(by_distance, "distance") > 0:
            lines.append(f"La actividad más larga de {sport_label} en {label}, por distancia:")
            lines.append(_line(by_distance))
        if by_time is not by_distance or _num(by_distance, "distance") <= 0:
            lines += ["", "Por tiempo:"]
            lines.append(_line(by_time))
        top = sorted(acts, key=lambda a: (_num(a, "distance"), _num(a, "duration")), reverse=True)[:5]
        lines += ["", "## 📊 Métricas clave", f"Las {len(top)} más largas por distancia de {len(acts)} actividades:"]
        lines += [_line(a) for a in top]
        return "\n".join(lines + footer)

    by_family: dict[str, list[dict]] = {}
    for act in acts:
        by_family.setdefault(_activity_family(act), []).append(act)
    total_dist = sum(_num(a, "distance") for a in acts)
    period_days = (end - start).days + 1
    active_days = len({_extract_activity_date_iso(a) for a in acts})
    n_acts = f"{len(acts)} actividad" + ("es" if len(acts) != 1 else "")
    lines = ["## 🧭 Resumen",
             f"{sport_label.capitalize()} en {label}: " + (f"{_fmt_km(total_dist)} en " if total_dist > 0 else "")
             + f"{n_acts} ({_seconds_to_hhmmss(sum(_num(a, 'duration') for a in acts))}).",
             f"Días con actividad: {active_days} de {period_days}; sin actividad: {period_days - active_days}.",
             "", "## 📊 Métricas clave",
             "| Deporte | Actividades | Distancia | Tiempo | Desnivel + |", "|---|---:|---:|---:|---:|"]
    for fam, items in sorted(by_family.items(), key=lambda kv: -sum(_num(a, "distance") for a in kv[1])):
        fam_dist = sum(_num(a, "distance") for a in items)
        lines.append(f"| {fam} | {len(items)} | {_fmt_km(fam_dist) if fam_dist > 0 else '—'} | "
                     f"{_seconds_to_hhmmss(sum(_num(a, 'duration') for a in items))} | "
                     f"{sum(_num(a, 'elevationGain') for a in items):.0f} m |")
    if len(by_family) > 1:
        lines.append(f"| **Total** | {len(acts)} | {_fmt_km(total_dist)} | "
                     f"{_seconds_to_hhmmss(sum(_num(a, 'duration') for a in acts))} | "
                     f"{sum(_num(a, 'elevationGain') for a in acts):.0f} m |")
    lines += ["", "Actividades:"] + [_line(a) for a in acts]
    return "\n".join(lines + footer)
