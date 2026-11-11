"""
agent/startup_cache.py
Caché del resumen inicial (31/10, petición del usuario; 11/11, el mismo día en vez de 1 h):
- Nivel 1: si se reentra el mismo día y nada ha cambiado, se muestra el resumen guardado en Supabase sin volver a
  consultar a Garmin ni al LLM.
- Nivel 2: si algo cambió (p. ej. entrenó entre la entrada de la mañana y la de la tarde) pero el sueño de hoy es el
  mismo, se rehace el resumen con el LLM, pero la HRV de la noche y los datos de ayer salen de los guardados por la
  mañana (`morning`): no cambian en el día. El Body Battery de hoy (su último registro baja con el día) y las
  actividades se piden siempre.
Funciones puras; la lectura y escritura están en agent/storage.py y el flujo en agent/startup.py.
"""

from datetime import datetime, timedelta, timezone

# Lo de la mañana que no cambia en el día (nivel 2). El sueño de hoy no: se pide siempre para comprobar que es el
# mismo (si el reloj sincronizó tarde o hubo siesta, cambia y no se reutiliza nada).
MORNING_KEYS = ("hrv_today", "hrv_yday", "body_yday", "sleep_yday")


def fingerprint(*, today: str, activity_ids: list, sleep: str, plan_row: dict | None, formula_version: int,
                profile_changes: list[str] | None) -> dict:
    """Lo que, si cambia, obliga a rehacer el resumen: día, actividades de las últimas 48 h, sueño de hoy, plan
    activo (id y última modificación), fórmula de TSS y cambios del perfil."""
    return {
        "date": today,
        "activities": sorted(str(i) for i in activity_ids if i is not None),
        "sleep": str(sleep),
        "plan": [str(plan_row.get("id")), str(plan_row.get("updated_at"))] if plan_row else None,
        "formula": int(formula_version),
        "profile_changes": [str(c) for c in (profile_changes or [])],
    }


def _age(row: dict, now: datetime) -> timedelta | None:
    try:
        generated = datetime.fromisoformat(str(row.get("generated_at")))
    except (TypeError, ValueError):
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return now - generated


def is_today(row: dict | None, now: datetime | None = None) -> bool:
    """True si el resumen guardado es de hoy (hora local) y no del futuro por un reloj desajustado."""
    if not row:
        return False
    now = now or datetime.now(timezone.utc)
    age = _age(row, now)
    if age is None or age < timedelta(0):
        return False
    return (now - age).astimezone().date() == now.astimezone().date()


def reusable_morning(row: dict | None, current: dict) -> dict:
    """Los datos de la mañana del resumen guardado que valen para rehacerlo ahora (nivel 2): solo si son de hoy y el
    sueño de hoy no ha cambiado. {} si no."""
    if not row:
        return {}
    saved = row.get("fingerprint") or {}
    morning = row.get("morning") or {}
    today = current.get("date")
    if saved.get("date") != today or morning.get("date") != today or saved.get("sleep") != current.get("sleep"):
        return {}
    return {k: morning[k] for k in MORNING_KEYS if morning.get(k) is not None}


def matches(row: dict | None, current: dict) -> bool:
    return bool(row) and (row.get("fingerprint") or {}) == current


def with_note(row: dict, now: datetime | None = None) -> str:
    """El resumen guardado con una línea que dice de cuándo es y cómo actualizarlo."""
    now = now or datetime.now(timezone.utc)
    age = _age(row, now) or timedelta(0)
    generated = now - age
    minutes = int(age.total_seconds() // 60)
    ago = f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"
    return (f"_🕒 Resumen de las {generated.astimezone().strftime('%H:%M')} (hace {ago}): "
            "sin actividades ni sueño nuevos y con el mismo plan, no he vuelto a consultar a Garmin ni a tu entrenador "
            "(el Body Battery es el de esa hora). "
            "Escribe `/resumen` para actualizarlo._\n\n" + str(row.get("markdown") or ""))
