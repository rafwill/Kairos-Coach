"""
agent/coaching.py
Capa de coaching: horizonte de la respuesta (hoy, mañana, la semana…) y bloque de calendario con día y fecha para el LLM.
"""

import re
from datetime import date, timedelta


from agent.prompts import (
    _render_prompt,
)
from agent.dates import (
    _WEEKDAY_NAMES_ES,
)


def _coaching_horizon(user_message: str) -> str:
    """Horizonte que pide el usuario, explícito para la capa de coaching del LLM."""
    text = (user_message or "").lower()
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    today = date.today()

    def _day(d: date) -> str:
        return f"{dias[d.weekday()]} {d.strftime('%d/%m')}"

    # "la semana que viene" no es esta semana ni "pasado mañana" es mañana (24/10: ambos caían en el caso corto).
    if re.search(r"semana que viene|pr[oó]xima semana|siguiente semana|semana pr[oó]xima", text):
        start = today + timedelta(days=7 - today.weekday())
        week = [start + timedelta(days=i) for i in range(7)]
        return _render_prompt(
            "coaching/horizon_next_week", inicio=_day(week[0]), fin=_day(week[-1]),
            dias=", ".join(_day(d) for d in week),
        )
    if "semana" in text:
        remaining = ", ".join(_day(today + timedelta(days=i)) for i in range(7 - today.weekday()))
        return _render_prompt(
            "coaching/horizon_week", hoy=_day(today), dias_restantes=remaining
        )
    if "pasado mañana" in text or "pasado manana" in text:
        return _render_prompt("coaching/horizon_day_after", dia=_day(today + timedelta(days=2)))
    if "mañana" in text or "manana" in text:
        return _render_prompt("coaching/horizon_tomorrow", dia=_day(today + timedelta(days=1)))
    return _render_prompt("coaching/horizon_today", dia=_day(today))


def _calendar_block(today: date | None = None) -> str:
    """Calendario para el LLM: ayer, hoy, mañana, pasado mañana y las dos semanas con su día y fecha.

    Antes solo recibía "hoy" y "ayer" en ISO y deducía él los días: "Mañana 26/10 (lunes)" un sábado 24/10
    (sensores S27 y S28, 24–26/10).
    """
    today = today or date.today()

    def _day(d: date) -> str:
        return f"{_WEEKDAY_NAMES_ES[d.weekday()]} {d.strftime('%d/%m/%Y')}"

    def _week(monday: date) -> str:
        return ", ".join(f"{_WEEKDAY_NAMES_ES[d.weekday()][:3]} {d.strftime('%d/%m')}"
                         for d in (monday + timedelta(days=i) for i in range(7)))

    monday = today - timedelta(days=today.weekday())
    return _render_prompt(
        "system/calendar",
        ayer=_day(today - timedelta(days=1)), hoy=_day(today), manana=_day(today + timedelta(days=1)),
        pasado_manana=_day(today + timedelta(days=2)), semana=_week(monday),
        semana_que_viene=_week(monday + timedelta(days=7)),
    )
