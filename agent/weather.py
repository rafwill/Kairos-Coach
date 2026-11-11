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
DAILY_VARS = ("temperature_2m_max,temperature_2m_min,apparent_temperature_max,precipitation_sum,"
              "snowfall_sum,precipitation_probability_max,wind_speed_10m_max")
HOURLY_VARS = "temperature_2m,weather_code"
SKY_FROM, SKY_TO = 6, 21  # horas en las que se entrena: niebla o lluvia de madrugada no cuentan

# Códigos WMO de Open-Meteo → estado del cielo (11/11: antes solo temperaturas).
WMO = {0: "despejado", 1: "poco nuboso", 2: "intervalos nubosos", 3: "cubierto", 45: "niebla",
       48: "niebla con escarcha", 51: "llovizna débil", 53: "llovizna", 55: "llovizna intensa",
       56: "llovizna helada", 57: "llovizna helada intensa", 61: "lluvia débil", 63: "lluvia", 65: "lluvia fuerte",
       66: "lluvia helada", 67: "lluvia helada fuerte", 71: "nieve débil", 73: "nieve", 75: "nieve fuerte",
       77: "granizo menudo", 80: "chubascos débiles", 81: "chubascos", 82: "chubascos fuertes",
       85: "chubascos de nieve", 86: "chubascos de nieve fuertes", 95: "tormenta", 96: "tormenta con granizo",
       99: "tormenta con granizo fuerte"}


def _verify():
    """El certificado del proxy si el .env lo da; si no, el de siempre."""
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
    """Respuesta de Open-Meteo → {min, max, feels_max, precip_mm, snow_cm, precip_prob, wind_max, sky,
    hours: {7: t, …}, sky_hours: {7: estado, …}}; None si no trae hoy. Lo que no venga, no se pone.

    `sky`: lluvia, niebla, nieve, tormenta… que la previsión trae hora a hora entre SKY_FROM y SKY_TO, en orden y sin
    repetir. No del código diario: es el número WMO más alto, no lo más grave (Madrid, 11/11: «chubascos débiles» con
    36,6 mm y lluvia hora a hora)."""
    daily = (data or {}).get("daily") or {}
    if str((daily.get("time") or [""])[0]) != today.isoformat():
        return None
    try:
        out = {"min": float(daily["temperature_2m_min"][0]), "max": float(daily["temperature_2m_max"][0]),
               "feels_max": float(daily["apparent_temperature_max"][0])}
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    for key, var in (("precip_mm", "precipitation_sum"), ("snow_cm", "snowfall_sum"),
                     ("precip_prob", "precipitation_probability_max"), ("wind_max", "wind_speed_10m_max")):
        value = _first(daily.get(var))
        if value is not None:
            out[key] = value
    hourly = (data or {}).get("hourly") or {}
    times = hourly.get("time") or []
    temps = dict(zip(times, hourly.get("temperature_2m") or []))
    codes = dict(zip(times, hourly.get("weather_code") or []))
    out["hours"] = {h: float(temps[f"{today.isoformat()}T{h:02d}:00"]) for h in HOURS
                    if temps.get(f"{today.isoformat()}T{h:02d}:00") is not None}
    out["sky_hours"] = {h: _sky(codes.get(f"{today.isoformat()}T{h:02d}:00")) for h in out["hours"]
                        if _sky(codes.get(f"{today.isoformat()}T{h:02d}:00"))}
    sky = []
    for h in range(SKY_FROM, SKY_TO + 1):
        code = codes.get(f"{today.isoformat()}T{h:02d}:00")
        text = _sky(code) if code is not None and int(code) >= 45 else None
        if text and text not in sky:
            sky.append(text)
    if sky:
        out["sky"] = sky
    return out


def _first(values) -> float | None:
    """Primer valor numérico de una lista diaria; None si falta."""
    try:
        return float(values[0]) if values and values[0] is not None else None
    except (TypeError, ValueError):
        return None


def _sky(code) -> str | None:
    """Código WMO → texto; None si falta o no se conoce."""
    try:
        return WMO.get(int(code)) if code is not None else None
    except (TypeError, ValueError):
        return None


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
                "daily": DAILY_VARS, "hourly": HOURLY_VARS})
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
    sky_hours = weather.get("sky_hours") or {}
    hours = " · ".join(f"{h}:00 {t:.0f} ºC" + (f", {sky_hours[h]}" if sky_hours.get(h) else "")
                       for h, t in sorted((weather.get("hours") or {}).items()))
    lines = ["", f"## 🌡️ Tiempo hoy en {weather['place']}",
             f"- {weather['min']:.0f}–{weather['max']:.0f} ºC (sensación térmica máxima {weather['feels_max']:.0f} ºC)"
             + (f" · {hours}" if hours else "")]
    sky = []
    if weather.get("sky"):
        sky.append(f"Entre {SKY_FROM}:00 y {SKY_TO}:00: " + ", ".join(weather["sky"]))
    if weather.get("precip_mm") is not None:
        rain = f"precipitación {weather['precip_mm']:.1f} mm".replace(".", ",")
        if weather.get("snow_cm"):
            rain += f" (nieve {weather['snow_cm']:.1f} cm)".replace(".", ",")
        if weather.get("precip_prob") is not None:
            rain += f", probabilidad máxima {weather['precip_prob']:.0f} %"
        sky.append(rain)
    if weather.get("wind_max") is not None:
        sky.append(f"viento máximo {weather['wind_max']:.0f} km/h")
    if sky:
        lines.append("- " + " · ".join(sky))
    return lines + ["- Fuente: previsión de Open-Meteo para tu lugar de entrenamiento."]
