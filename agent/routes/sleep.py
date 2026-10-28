"""
agent/routes/sleep.py
Rutas de sueño: una noche concreta y la tendencia de las últimas semanas (cifras calculadas aquí; el LLM solo las lee).
"""

import logging
import asyncio
import re
from datetime import date, timedelta
from typing import Any


from agent.mcp.client import (
    call_tool,
)
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _try_parse_json,
)
from agent.dates import (
    _MONTHS_ES,
    _WEEKDAY_NAMES_ES,
    _signed,
)
from agent.routes.period import (
    _PERIOD_MONTH_RE,
)

log = logging.getLogger(__name__)


_SLEEP_Q_RE = re.compile(r"dorm[ií]|he dormido|sue[nñ]o")


_SLEEP_TREND_RE = re.compile(
    r"semana|[uú]ltim|media|promedio|evoluci|tendencia|\bmes\b|meses|noches|empeor|mejor|"
    r"\bentre\b|\bdesde\b"
)


# Una decisión de entrenamiento ("dormí mal, ¿puedo entrenar?") no es esta ruta: va a la de estado del día o al LLM.
_SLEEP_DECISION_RE = re.compile(r"entren|puedo|sesi[oó]n|plan\b|recomiend|hrv|carga|bater[ií]a|estr[eé]s")


_SLEEP_WEEKDAYS = {"lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3, "viernes": 4,
                   "sabado": 5, "sábado": 5, "domingo": 6}


def _resolve_sleep_night(user_message: str, today: date | None = None) -> date | None:
    """Noche por la que pregunta, con la fecha de Garmin (el día en que te despiertas); None si no es una sola noche.

    "anoche", "hoy" o sin día → hoy; "ayer" → ayer; "anteayer" → antes de ayer; un día de la semana → el último
    que ha pasado (hoy incluido); una fecha DD/MM o "20 de octubre" → esa (nunca futura).
    """
    today = today or date.today()
    text = (user_message or "").lower()
    if not _SLEEP_Q_RE.search(text) or _SLEEP_TREND_RE.search(text) or _SLEEP_DECISION_RE.search(text):
        return None
    if re.search(r"anteayer|antes de ayer|antier", text):
        return today - timedelta(days=2)
    if re.search(r"\bayer\b", text):
        return today - timedelta(days=1)
    day_month = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", text)
    named = _PERIOD_MONTH_RE.search(text)
    named_day = re.search(r"\b(\d{1,2})\s+de\s+" + (named.group(1) if named else r"$^"), text) if named else None
    if day_month or named_day:
        if day_month:
            d, mth, yy = int(day_month.group(1)), int(day_month.group(2)), day_month.group(3)
        else:
            d, mth, yy = int(named_day.group(1)), _MONTHS_ES[named.group(1)], named.group(2)
        year = (int(yy) + (2000 if len(yy) == 2 else 0)) if yy else today.year
        try:
            night = date(year, mth, d)
        except ValueError:
            return None
        if night > today and not yy:
            night = night.replace(year=year - 1)
        return night if night <= today else None
    for name, idx in _SLEEP_WEEKDAYS.items():
        if re.search(rf"\b{name}\b", text):
            return today - timedelta(days=(today.weekday() - idx) % 7)
    return today


def _is_sleep_night_intent(user_message: str) -> bool:
    """"¿Cómo dormí anoche?", "¿qué tal dormí el martes?", "mi sueño del 20/10": una sola noche, sin decisión."""
    return _resolve_sleep_night(user_message) is not None


def _resolve_sleep_window(user_message: str) -> int | None:
    """Noches de una pregunta de tendencia de sueño ("¿cómo he dormido últimamente?"), o None si no lo es."""
    text = (user_message or "").lower()
    if not _SLEEP_Q_RE.search(text) or _SLEEP_DECISION_RE.search(text):
        return None
    if not (_SLEEP_TREND_RE.search(text) or re.search(r"[uú]ltimamente|estos d[ií]as|qu[eé] tal (?:voy|llevo)", text)):
        return None
    m = re.search(r"(\d{1,2})\s*(?:noches|d[ií]as)", text)
    if m:
        return max(3, min(30, int(m.group(1))))
    if re.search(r"\bmes\b|30 d|cuatro semanas|4 semanas", text):
        return 30
    if re.search(r"dos semanas|2 semanas|[uú]ltimas semanas|quincena|14", text):
        return 14
    return 7


def _sleep_night_value(raw: Any) -> dict | None:
    """Payload de get_sleep_summary con sueño registrado, o None (sin sincronizar, error o 0 s)."""
    data = _try_parse_json(raw) if isinstance(raw, str) else None
    if not isinstance(data, dict):
        return None
    try:
        secs = float(data.get("sleepTimeSeconds") or 0)
    except (TypeError, ValueError):
        return None
    return data if secs > 0 else None


async def _fetch_sleep_nights(mcp_session, days: list[date]) -> dict[date, dict | None]:
    """Sueño de varias noches en paralelo, de 4 en 4 (el servidor atiende 4 llamadas a Garmin a la vez y Kairos
    corta cada llamada a los 8 s: un mes entero de golpe dejaría las últimas en cola hasta caducar)."""
    sem = asyncio.Semaphore(4)

    async def _one(d: date):
        async with sem:
            try:
                return _sleep_night_value(await call_tool(mcp_session, "get_sleep_summary", {"date": d.isoformat()}))
            except (TimeoutError, OSError, RuntimeError, ValueError, TypeError) as exc:
                log.debug("_fetch_sleep_nights: %s falló: %s", d, exc)
                return None

    values = await asyncio.gather(*(_one(d) for d in days))
    return dict(zip(days, values))


def _sleep_hm(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    return f"{minutes // 60} h {minutes % 60:02d} min"


def _sleep_secs(night: dict, key: str) -> float:
    try:
        return float(night.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


_SLEEP_FOOTER = ["", "## 🎯 Próximo paso", "- Fuente: respuesta determinista (sueño de Garmin, sin LLM)."]


async def _build_sleep_night_markdown(mcp_session, night: date | None = None) -> str:
    """Sueño de una noche (Garmin la fecha el día en que te despiertas) y media de las 7 anteriores.

    Para anoche, si el reloj no ha sincronizado, se dice y se muestra la última noche registrada con su fecha.
    """
    today = date.today()
    night = night or today
    days = [night - timedelta(days=i) for i in range(8)]
    nights = await _fetch_sleep_nights(mcp_session, days)
    idx = 0
    if nights[night] is None:
        if night != today:
            return "\n".join(["## 🧭 Resumen",
                              f"Garmin no tiene sueño registrado de la noche que termina el "
                              f"{_WEEKDAY_NAMES_ES[night.weekday()]} {night.strftime('%d/%m/%Y')}.", *_SLEEP_FOOTER])
        idx = next((i for i, d in enumerate(days) if nights[d]), None)
        if idx is None:
            return "\n".join(["## 🧭 Resumen",
                              f"Garmin no tiene sueño registrado en los últimos {len(days)} días. "
                              "Sincroniza el reloj con Garmin Connect y vuelve a preguntar.", *_SLEEP_FOOTER])
    shown_day = days[idx]
    last = nights[shown_day]
    previous = [nights[d] for d in days[idx + 1:] if nights[d]]
    pending = []
    if night == today and idx > 0:
        pending = [f"Garmin aún no tiene el sueño de anoche (noche que termina el {today.strftime('%d/%m/%Y')}): "
                   "sincroniza el reloj con Garmin Connect. Esta es la última noche registrada.", ""]

    total = _sleep_secs(last, "sleepTimeSeconds")
    score = last.get("sleepScore")
    avg_secs = sum(_sleep_secs(n, "sleepTimeSeconds") for n in previous) / len(previous) if previous else None
    scores = [float(n["sleepScore"]) for n in previous if n.get("sleepScore") is not None]
    avg_score = sum(scores) / len(scores) if scores else None
    when = f"{_WEEKDAY_NAMES_ES[shown_day.weekday()]} {shown_day.strftime('%d/%m/%Y')}"
    if shown_day == today:
        head = f"Anoche (noche que termina el {when}) dormiste {_sleep_hm(total)}"
    else:
        head = f"La noche que terminó el {when} dormiste {_sleep_hm(total)}"
    lines = ["## 🧭 Resumen", *pending,
             head + (f", puntuación {float(score):.0f}/100" if score is not None else "") + "."]
    if avg_secs is not None:
        lines.append(f"Media de las {len(previous)} noches anteriores: {_sleep_hm(avg_secs)}"
                     + (f", puntuación {avg_score:.0f}/100" if avg_score is not None else "") + ".")
    lines += ["", "## 📊 Métricas clave", "| Fase | Tiempo | % del sueño |", "|---|---:|---:|"]
    for label, key in (("Profundo", "deepSleepSeconds"), ("Ligero", "lightSleepSeconds"), ("REM", "remSleepSeconds")):
        secs = _sleep_secs(last, key)
        lines.append(f"| {label} | {_sleep_hm(secs)} | {100 * secs / total:.0f} % |")
    lines.append(f"| Despierto | {_sleep_hm(_sleep_secs(last, 'awakeSleepSeconds'))} | — |")
    return "\n".join(lines + _SLEEP_FOOTER)


def _sleep_period_stats(nights: list[dict]) -> dict:
    """Medias de un periodo de noches con datos (horas, puntuación, profundo, REM) y noches por debajo de 6 h."""
    n = len(nights)
    if not n:
        return {"n": 0}
    scores = [float(x["sleepScore"]) for x in nights if x.get("sleepScore") is not None]
    total = [_sleep_secs(x, "sleepTimeSeconds") for x in nights]
    return {
        "n": n,
        "secs": sum(total) / n,
        "score": sum(scores) / len(scores) if scores else None,
        "deep_pct": 100 * sum(_sleep_secs(x, "deepSleepSeconds") for x in nights) / sum(total),
        "rem_pct": 100 * sum(_sleep_secs(x, "remSleepSeconds") for x in nights) / sum(total),
        "short": sum(1 for s in total if s < 6 * 3600),
    }


async def _build_sleep_trend_markdown(mcp_session, window: int) -> str:
    """Tendencia de sueño: las últimas `window` noches frente a las `window` anteriores, calculado en Python.

    La interpretación la añade la capa de coaching del LLM sobre esta tabla (ruta mixta).
    """
    today = date.today()
    days = [today - timedelta(days=i) for i in range(2 * window)]
    nights = await _fetch_sleep_nights(mcp_session, days)
    current_days, previous_days = days[:window], days[window:]
    current = [nights[d] for d in current_days if nights[d]]
    previous = [nights[d] for d in previous_days if nights[d]]
    cur, prev = _sleep_period_stats(current), _sleep_period_stats(previous)

    def _range(ds: list[date]) -> str:
        return f"{ds[-1].strftime('%d/%m')} → {ds[0].strftime('%d/%m')}"

    if not cur["n"]:
        return "\n".join(["## 🧭 Resumen",
                          f"Garmin no tiene sueño registrado en las últimas {window} noches ({_range(current_days)}).",
                          *_SLEEP_FOOTER])
    missing = window - cur["n"]
    lines = ["## 🧭 Resumen",
             f"Últimas {window} noches ({_range(current_days)}): media {_sleep_hm(cur['secs'])}"
             + (f", puntuación {cur['score']:.0f}/100" if cur["score"] is not None else "")
             + f"; {cur['short']} por debajo de 6 h."
             + (f" Sin datos en {missing} noche(s)." if missing else "")]
    if prev["n"]:
        diff_min = (cur["secs"] - prev["secs"]) / 60
        diff_score = (cur["score"] - prev["score"]) if cur["score"] is not None and prev["score"] is not None else None
        lines.append(f"Frente a las {window} anteriores ({_range(previous_days)}): {_signed(diff_min)} min de sueño"
                     + (f" y {_signed(diff_score)} puntos de puntuación" if diff_score is not None else "") + ".")

    def _row(label: str, s: dict) -> str:
        if not s["n"]:
            return f"| {label} | 0 | — | — | — | — | — |"
        score = f"{s['score']:.0f}" if s["score"] is not None else "—"
        return (f"| {label} | {s['n']} | {_sleep_hm(s['secs'])} | {score} | {s['deep_pct']:.0f} % | "
                f"{s['rem_pct']:.0f} % | {s['short']} |")

    lines += ["", "## 📊 Métricas clave",
              "| Periodo | Noches con datos | Sueño medio | Puntuación media | Profundo | REM | Noches < 6 h |",
              "|---|---:|---:|---:|---:|---:|---:|",
              _row(f"Últimas {window} ({_range(current_days)})", cur),
              _row(f"Anteriores {window} ({_range(previous_days)})", prev)]
    if window <= 14:
        lines += ["", "Noche a noche (fecha = día en que te despiertas):"]
        for d in current_days:
            n = nights[d]
            when = f"{_WEEKDAY_NAMES_ES[d.weekday()][:3]} {d.strftime('%d/%m')}"
            if n:
                score = n.get("sleepScore")
                lines.append(f"- {when}: {_sleep_hm(_sleep_secs(n, 'sleepTimeSeconds'))}"
                             + (f" · {float(score):.0f}/100" if score is not None else ""))
            else:
                lines.append(f"- {when}: sin datos")
    return "\n".join(lines + ["", "## 🎯 Próximo paso",
                              "- Fuente: sueño de Garmin calculado por Kairos (sin LLM); la interpretación va debajo."])
