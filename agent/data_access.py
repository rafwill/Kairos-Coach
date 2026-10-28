"""
agent/data_access.py
Lecturas y escrituras de Supabase que usa el chat: perfil, historial de la conversación, consumo de tokens, serie de carga (load_metrics_daily) y TSS guardados por actividad (activity_tss).
"""

import os
import logging


from agent import storage as _storage
from agent import load_metrics as _load_metrics
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _resolve_sport_model_cfg,
)

log = logging.getLogger(__name__)


def _load_user_profile() -> dict:
    """Carga el perfil del usuario (datos personales, objetivos, salud)."""
    return _storage.load_user_profile()


def _save_user_profile(profile: dict) -> None:
    """Guarda el perfil del usuario."""
    _storage.save_user_profile(profile)


def _load_session_context() -> dict:
    """Carga el contexto de sesiones (historial de mensajes y resúmenes)."""
    return _storage.load_session_context()


def _save_history_entry(role: str, content: str) -> None:
    """Añade una entrada al historial de conversación persistente."""
    _save_history_entries([(role, content)])


def _save_history_entries(entries: list[tuple[str, str]]) -> None:
    """Añade entradas al historial persistente con una sola lectura y escritura (pregunta y respuesta, 28/10).

    Si Supabase falla, se pierden esas entradas, no la respuesta: el 26/10 un "JWT expired" aquí cerró Kairos
    con la respuesta ya generada.
    """
    if not _session_memory_enabled():
        return
    try:
        _storage.save_history_entries(entries)
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de red o de Supabase
        log.warning("No se pudo guardar el historial (%s): %s", "/".join(role for role, _ in entries), exc)


def _load_session_summaries() -> list[dict]:
    """Carga los resúmenes de sesiones anteriores."""
    return _storage.load_session_summaries()


def _add_ia_tokens(tokens: int) -> None:
    """Suma el consumo de una llamada al LLM en ia_provider (26/10). Un fallo al apuntarlo nunca corta el chat."""
    try:
        _storage.add_ia_tokens(tokens)
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        log.debug("No se pudo apuntar el consumo de tokens: %s", exc)


def _session_memory_enabled() -> bool:
    """Memoria entre sesiones (historial y resumen del día en `session_context`). La apaga solo la batería
    (`KAIROS_SESSION_MEMORY=false`, 26/10): sus preguntas llenaban la memoria del atleta y el LLM las recibía
    como temas suyos; además, sin leerla, cada pasada no depende de la anterior."""
    raw = str(os.environ.get("KAIROS_SESSION_MEMORY", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


# Versión de la fórmula TSS. Incrementar cuando cambie _estimate_session_tss
# para forzar recálculo automático de la serie histórica en el próximo arranque.
_TSS_FORMULA_VERSION = _load_metrics.TSS_FORMULA_VERSION


def _tss_unit_for_stored_row(row: dict | None) -> str:
    """Unidad de TSS (rTSS, hrTSS o TSS) de una fila de activity_tss a partir de su modelo."""
    model = str((row or {}).get("model") or "").strip().lower()
    method = str((row or {}).get("method") or "").strip()
    if model.startswith("running:") or model == "trail:fast_rtss":
        return "rTSS"
    if model == "cycling:power_ftp" or model.startswith("walk_hike:metabolico"):
        return "TSS"
    if model or method.lower() == "hrtss":
        return "hrTSS" if (method.lower() == "hrtss" or not method) else method
    return method or "TSS"


def _load_stored_activity_tss(start_iso: str) -> dict[int, dict]:
    """TSS guardados en activity_tss desde start_iso; {} si no hay o no se pueden leer.

    Es la única fuente de TSS por actividad para el chat: nunca se recalcula al vuelo
    ni se sustituye por la carga nativa de Garmin.
    """
    try:
        stored = _storage.get_activity_tss_map(start_iso)
    except RuntimeError as exc:
        log.debug("_load_stored_activity_tss: %s", exc)
        stored = None
    return stored or {}


def _load_metrics_series(days: int = 120) -> list[dict]:
    """Serie diaria de `load_metrics_daily`, la única fuente de la carga (27/10: antes 11 sitios leían una copia
    guardada en el perfil, que podía quedarse vieja). Sin sesión o sin base de datos, lista vacía."""
    try:
        return _storage.get_load_metrics_series(days=days) or []
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        log.debug("load_metrics_daily no disponible: %s", exc)
        return []


def _load_metrics_view(profile: dict | None, series: list[dict] | None = None) -> dict:
    """Carga actual (latest, ranges, weekly, series…) calculada desde `load_metrics_daily`; {} sin serie."""
    series = _load_metrics_series() if series is None else series
    if not series:
        return {}
    model_cfg = _resolve_sport_model_cfg(profile)
    model_cfg["_sport"] = str(((profile or {}).get("goals") or {}).get("primary") or "running").strip().lower()
    return _load_metrics.load_fatigue_from_series(series, model_cfg) or {}
