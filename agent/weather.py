"""
agent/weather.py — el tiempo de hoy donde entrena el atleta (30/10, decisión del usuario; pendiente 11 a).

Open-Meteo, gratuito y sin clave. El nombre del lugar (goals.training_location) se geocodifica una vez y las
coordenadas se guardan con el nombre del que salen; cada día solo se envían las coordenadas. Sin umbrales de calor:
los datos van al arranque y a la capa de coaching, y el LLM decide con la guía de prompts/coaching_rules.md.
Cualquier fallo devuelve None y el arranque sigue sin el bloque.
"""

from __future__ import annotations

import logging
import os
from datetime import date

import httpx

log = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 4.0
HOURS = (7, 12, 19)


def _verify():
    """El certificado del proxy corporativo si el .env lo da (Zscaler); si no, el de siempre."""
    return os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or True


def parse_geocode(data: dict, location: str) -> dict | None:
    """Primer resultado de la búsqueda → {for, name, lat, lon}; None si no hay."""
    results = (data or {}).get("results") or []
    if not results:
        return None
    r = results[0]
    name = str(r.get("name") or location)
    region = str(r.get("admin1") or "")
    return {"for": location, "name": f"{name} ({region})" if region and region != name else name,
            "lat": float(r["latitude"]), "lon": float(r["longitude"])}


def parse_forecast(data: dict, today: date) -> dict | None:
    """Respuesta de Open-Meteo → {min, max, feels_max, hours: {7: t, 12: t, 19: t}}; None si no trae hoy."""
    daily = (data or {}).get("daily") or {}
    if str((daily.get("time") or [""])[0]) != today.isoformat():
        return None
    try:
        out = {"min": float(daily["temperature_2m_min"][0]), "max": float(daily["temperature_2m_max"][0]),
               "feels_max": float(daily["apparent_temperature_max"][0])}
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    hourly = (data or {}).get("hourly") or {}
    temps = dict(zip(hourly.get("time") or [], hourly.get("temperature_2m") or []))
    out["hours"] = {h: float(temps[f"{today.isoformat()}T{h:02d}:00"]) for h in HOURS
                    if temps.get(f"{today.isoformat()}T{h:02d}:00") is not None}
    return out


async def today_weather(location: str, coords: dict | None, today: date) -> tuple[dict | None, dict | None]:
    """(tiempo de hoy con el nombre del lugar, o None; coordenadas nuevas si hubo que buscarlas, o None)."""
    location = (location or "").strip()
    if not location:
        return None, None
    new_coords = None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, verify=_verify()) as client:
            if not coords or coords.get("for") != location:
                resp = await client.get(GEOCODE_URL, params={"name": location, "count": 1, "language": "es"})
                resp.raise_for_status()
                coords = new_coords = parse_geocode(resp.json(), location)
                if not coords:
                    log.info("[TIEMPO] sin coordenadas para «%s»", location)
                    return None, None
            resp = await client.get(FORECAST_URL, params={
                "latitude": coords["lat"], "longitude": coords["lon"], "timezone": "auto", "forecast_days": 1,
                "daily": "temperature_2m_max,temperature_2m_min,apparent_temperature_max", "hourly": "temperature_2m"})
            resp.raise_for_status()
            weather = parse_forecast(resp.json(), today)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.info("[TIEMPO] sin previsión: %s: %s", type(exc).__name__, exc)
        return None, new_coords
    if weather is None:
        log.info("[TIEMPO] la previsión no trae el día de hoy")
        return None, new_coords
    return {**weather, "place": coords["name"]}, new_coords


def markdown(weather: dict | None) -> list[str]:
    """Bloque "🌡️ Tiempo hoy en …" del arranque; vacío sin datos."""
    if not weather:
        return []
    hours = " · ".join(f"{h}:00 {t:.0f} ºC" for h, t in sorted((weather.get("hours") or {}).items()))
    return ["", f"## 🌡️ Tiempo hoy en {weather['place']}",
            f"- {weather['min']:.0f}–{weather['max']:.0f} ºC (sensación térmica máxima {weather['feels_max']:.0f} ºC)"
            + (f" · {hours}" if hours else ""),
            "- Fuente: previsión de Open-Meteo para tu lugar de entrenamiento."]
