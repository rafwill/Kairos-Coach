"""
agent/routes/db_count.py
Ruta de conteo de actividades de la base de datos: cuántas actividades con TSS hay en activity_tss, por deporte y
el rango que cubren; sin LLM.
"""

import re


from agent.dates import (
    _parse_iso_date_safe,
)
from agent.data_access import (
    _load_stored_activity_tss,
)
from agent.garmin_data import (
    _get_activity_name_es,
)


def _is_db_count_question(user_message: str) -> bool:
    """"¿Cuántas actividades tienes registradas?" y parecidas (actividades de la base de datos, no de un periodo)."""
    _message_lc = (user_message or "").lower()
    _count_query = bool(re.search(
        r'\b(cu[aá]ntas?\s+actividades|total\s+actividades|actividades\s+registradas|actividades\s+en\s+(la\s+)?(base|bbdd|db)|cuantos\s+entrenos)\b',
        _message_lc
    ))
    if not _count_query and "actividades" in _message_lc and (
        "base de datos" in _message_lc or "db" in _message_lc or "bbdd" in _message_lc
    ):
        _count_query = True
    return _count_query


def _build_db_count_markdown() -> str:
    """Actividades de activity_tss (una fila por actividad con su TSS), no la serie diaria de 120 días."""
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
    return assistant_reply
