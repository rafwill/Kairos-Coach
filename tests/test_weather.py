"""
tests/test_weather.py
El tiempo de hoy donde entrena el atleta (Open-Meteo, 30/10, decisión del usuario). Sin red: transporte simulado.
Criterios fijados antes en docs/proximos_pasos.md (11 a).
"""

import asyncio
from datetime import date
from unittest.mock import patch

import httpx

from agent import weather as w

TODAY = date(2026, 10, 30)
GEO = {"results": [{"name": "Elche", "admin1": "Comunidad Valenciana", "latitude": 38.26, "longitude": -0.70}]}
FORECAST = {
    "daily": {"time": ["2026-10-30"], "temperature_2m_max": [24.3], "temperature_2m_min": [12.1],
              "apparent_temperature_max": [25.8]},
    "hourly": {"time": [f"2026-10-30T{h:02d}:00" for h in range(24)],
               "temperature_2m": [12 + h * 0.5 for h in range(24)]},
}


def _client(handler):
    real = httpx.AsyncClient

    def factory(**kwargs):
        kwargs.pop("verify", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)
    return factory


def _run(handler, location="Elche", coords=None):
    with patch("agent.weather.httpx.AsyncClient", _client(handler)):
        return asyncio.run(w.today_weather(location, coords, TODAY))


def test_parse_forecast_takes_today_and_three_hours():
    out = w.parse_forecast(FORECAST, TODAY)
    assert out == {"min": 12.1, "max": 24.3, "feels_max": 25.8, "hours": {7: 15.5, 12: 18.0, 19: 21.5},
                   "sky_hours": {}}
    assert w.parse_forecast({**FORECAST, "daily": {**FORECAST["daily"], "time": ["2026-10-29"]}}, TODAY) is None
    assert w.parse_forecast({"daily": {"time": ["2026-10-30"]}}, TODAY) is None


RAINY = {
    "daily": {**FORECAST["daily"], "weather_code": [80], "precipitation_sum": [4.2], "snowfall_sum": [0.0],
              "precipitation_probability_max": [85], "wind_speed_10m_max": [22.4]},
    "hourly": {**FORECAST["hourly"], "weather_code": [45 if h < 10 else 61 if h < 16 else 3 for h in range(24)]},
}


def test_rain_fog_and_wind_in_the_block():
    """11/11, petición del usuario: además de las temperaturas, lluvia, niebla, nieve… y viento."""
    out = w.parse_forecast(RAINY, TODAY)
    assert out["sky"] == ["niebla", "lluvia débil"] and out["precip_mm"] == 4.2 and out["precip_prob"] == 85.0
    assert out["sky_hours"] == {7: "niebla", 12: "lluvia débil", 19: "cubierto"}
    lines = w.markdown({**out, "place": "Madrid"})
    assert lines[2] == ("- 12–24 ºC (sensación térmica máxima 26 ºC) · 7:00 16 ºC, niebla · 12:00 18 ºC, lluvia débil"
                        " · 19:00 22 ºC, cubierto")
    assert lines[3] == ("- Entre 6:00 y 21:00: niebla, lluvia débil · precipitación 4,2 mm, probabilidad máxima 85 %"
                        " · viento máximo 22 km/h")
    # el código diario (80, «chubascos débiles») no manda: es el número WMO más alto, no lo más grave
    snowy = w.parse_forecast({"daily": {**RAINY["daily"], "snowfall_sum": [3.5]},
                              "hourly": {**RAINY["hourly"], "weather_code": [0] * 6 + [73] * 18}}, TODAY)
    line = w.markdown({**snowy, "place": "Navacerrada"})[3]
    assert line.startswith("- Entre 6:00 y 21:00: nieve ·") and "(nieve 3,5 cm)" in line
    night_fog = {**RAINY["hourly"], "weather_code": [45] * 5 + [1] * 17 + [45] * 2}  # niebla solo de madrugada
    assert w.parse_forecast({**RAINY, "hourly": night_fog}, TODAY).get("sky") is None
    unknown = {**RAINY["hourly"], "weather_code": [999] * 24}
    assert w.parse_forecast({**RAINY, "hourly": unknown}, TODAY).get("sky") is None


def test_the_forecast_asks_for_sky_rain_and_wind():
    def handler(request):
        if "geocoding" in request.url.host:
            return httpx.Response(200, json=GEO)
        assert "weather_code" in request.url.params["hourly"]
        assert "precipitation_sum" in request.url.params["daily"]
        return httpx.Response(200, json=RAINY)

    weather, _ = _run(handler)
    assert weather["sky"] == ["niebla", "lluvia débil"]


def test_first_time_geocodes_and_returns_the_coords_to_save():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if "geocoding" in request.url.host:
            assert request.url.params["name"] == "Elche"
            return httpx.Response(200, json=GEO)
        assert request.url.params["latitude"] == "38.26"  # cada día, solo las coordenadas
        return httpx.Response(200, json=FORECAST)

    weather, coords = _run(handler)
    assert calls == ["geocoding-api.open-meteo.com", "api.open-meteo.com"]
    assert coords == {"for": "Elche", "name": "Elche (Comunidad Valenciana)", "lat": 38.26, "lon": -0.70}
    assert weather["place"] == "Elche (Comunidad Valenciana)" and weather["max"] == 24.3


def test_saved_coords_skip_the_geocoding_unless_the_place_changed():
    coords = {"for": "Elche", "name": "Elche (Comunidad Valenciana)", "lat": 38.26, "lon": -0.70}
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(200, json=GEO if "geocoding" in request.url.host else FORECAST)

    weather, new = _run(handler, coords=coords)
    assert hosts == ["api.open-meteo.com"] and new is None and weather["min"] == 12.1
    hosts.clear()
    _run(handler, location="Alicante", coords=coords)  # otro lugar: vuelve a buscar
    assert hosts[0] == "geocoding-api.open-meteo.com"


def test_any_failure_gives_no_weather():
    assert _run(lambda r: httpx.Response(500)) == (None, None)
    assert _run(lambda r: httpx.Response(200, json={"results": []})) == (None, None)  # lugar desconocido

    def timeout(request):
        raise httpx.ConnectTimeout("lento", request=request)

    assert _run(timeout) == (None, None)
    assert _run(lambda r: httpx.Response(200, text="<html>proxy</html>")) == (None, None)
    assert asyncio.run(w.today_weather("", None, TODAY)) == (None, None)  # sin lugar, sin red


def test_block_in_the_startup_summary():
    weather = {**w.parse_forecast(FORECAST, TODAY), "place": "Elche (Comunidad Valenciana)"}
    lines = w.markdown(weather)
    assert lines[1] == "## 🌡️ Tiempo hoy en Elche (Comunidad Valenciana)"
    assert lines[2] == "- 12–24 ºC (sensación térmica máxima 26 ºC) · 7:00 16 ºC · 12:00 18 ºC · 19:00 22 ºC"
    assert w.markdown(None) == []
    from agent.startup_status import _build_proactive_status_markdown

    snapshot = {"body_battery": {}, "hrv": {}, "sleep": {}, "trainings": [], "plan_assigned": True,
                "today_session_lines": ["- **Viernes 30/10/2026 — Rodaje**"], "weather": weather}
    md = _build_proactive_status_markdown(snapshot)
    assert md.index("## 🗓️ Sesión de hoy") < md.index("## 🌡️ Tiempo hoy") < md.index("## ✅ Recomendación")


def test_startup_saves_new_coords_in_the_profile():
    from agent.trainer_agent import TrainerAgent

    agent = object.__new__(TrainerAgent)
    agent.user_profile = {"goals": {"training_location": "Elche"}}
    coords = {"for": "Elche", "name": "Elche", "lat": 38.26, "lon": -0.70}

    async def fake(location, saved, today):
        return {"place": "Elche"}, coords

    with patch("agent.startup._weather.today_weather", fake), patch("agent.startup._save_user_profile") as save:
        assert asyncio.run(agent._today_weather()) == {"place": "Elche"}
    save.assert_called_once()
    assert agent.user_profile["goals"]["training_location_coords"] == coords
    agent.user_profile = {"goals": {}}
    assert asyncio.run(agent._today_weather()) is None  # sin lugar: ni red ni bloque


def test_profile_place_command():
    from agent import main as m

    class _Agent:
        user_profile = {}

    profile = {"goals": {"training_location": "Elche", "training_location_coords": {"for": "Elche"}}}
    with patch.object(m, "_load_user_profile", return_value=profile), patch.object(m, "_save_user_profile") as save, \
         patch.object(m.console, "print"):
        assert m._handle_cli_command("/perfil lugar Santa Pola", _Agent())
    save.assert_called_once()
    assert profile["goals"]["training_location"] == "Santa Pola" and "training_location_coords" not in profile["goals"]
