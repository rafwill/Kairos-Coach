"""
agent/startup_cache.py
Caché del resumen inicial (31/10, petición del usuario): si se reentra en menos de 1 h y nada ha cambiado, se muestra
el resumen guardado en Supabase sin volver a consultar a Garmin ni al LLM. Funciones puras; la lectura y escritura
están en agent/storage.py y el flujo en agent/startup.py.
"""

from datetime import datetime, timedelta, timezone

MAX_AGE = timedelta(minutes=60)


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


def is_recent(row: dict | None, now: datetime | None = None) -> bool:
    """True si el resumen guardado tiene menos de MAX_AGE (y no es del futuro por un reloj desajustado)."""
    if not row:
        return False
    age = _age(row, now or datetime.now(timezone.utc))
    return age is not None and timedelta(0) <= age < MAX_AGE


def matches(row: dict | None, current: dict) -> bool:
    return bool(row) and (row.get("fingerprint") or {}) == current


def with_note(row: dict, now: datetime | None = None) -> str:
    """El resumen guardado con una línea que dice de cuándo es y cómo actualizarlo."""
    now = now or datetime.now(timezone.utc)
    age = _age(row, now) or timedelta(0)
    generated = now - age
    return (f"_🕒 Resumen de las {generated.astimezone().strftime('%H:%M')} (hace {int(age.total_seconds() // 60)} min): "
            "sin actividades ni sueño nuevos y con el mismo plan, no he vuelto a consultar a Garmin ni a tu entrenador. "
            "Escribe `/resumen` para actualizarlo._\n\n" + str(row.get("markdown") or ""))
