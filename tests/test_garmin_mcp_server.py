from __future__ import annotations

from pathlib import Path

import pytest
from garminconnect.exceptions import GarminConnectAuthenticationError
from garminconnect.exceptions import GarminConnectConnectionError
from garminconnect.exceptions import GarminConnectTooManyRequestsError

from garmin_mcp import server


class _FakeGarmin:
    def get_activities_by_date(self, start: str, end: str):
        return [
            {"startTimeLocal": f"{start}T06:00:00", "trainingLoad": 10.5},
            {"startTimeLocal": f"{start}T18:00:00", "activityTrainingLoad": 5.0},
            {"startTimeLocal": f"{end}T07:00:00", "tss": 20.0},
        ]

    def get_sleep_data(self, day: str):
        return {
            "dailySleepDTO": {
                "sleepTimeSeconds": 25200,
                "sleepScore": 82,
                "deepSleepSeconds": 3600,
                "lightSleepSeconds": 14400,
                "remSleepSeconds": 7200,
                "awakeSleepSeconds": 1000,
            }
        }

    def get_activity(self, activity_id: int):
        return {
            "activityId": activity_id,
            "aerobicTrainingEffect": 3.2,
            "anaerobicTrainingEffect": 1.1,
            "trainingEffectLabel": "IMPROVING",
        }

    def get_personal_record(self):
        return [{"record_type": "Fastest 5K", "value": "17:48"}]

    def get_hrv_data(self, day: str):
        # Forma real de Garmin: los valores del día van dentro de hrvSummary.
        if day == "2026-09-02":
            return None  # noche sin reloj
        return {
            "userProfilePk": 1,
            "hrvReadings": [{"hrvValue": 70}],
            "hrvSummary": {"calendarDate": day, "weeklyAvg": 60, "lastNightAvg": 55, "status": "BALANCED"},
        }


@pytest.fixture(autouse=True)
def _reset_client_cache_state():
    prev_client = server._client
    prev_identity = server._client_identity
    server._client = None
    server._client_identity = None
    try:
        yield
    finally:
        server._client = prev_client
        server._client_identity = prev_identity


class _ClientWithPersonalRecord:
    def __init__(self, response: object):
        self.response = response

    def get_personal_record(self):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response



def test_training_load_trend_aggregates_per_day(monkeypatch):
    monkeypatch.setattr(server, "_garmin_client", lambda: _FakeGarmin())

    out = server._training_load_trend({"start_date": "2026-09-01", "end_date": "2026-09-02"})

    assert out["start_date"] == "2026-09-01"
    assert out["end_date"] == "2026-09-02"
    assert out["days_with_data"] == 2
    assert out["trend"][0]["trainingLoad"] == 15.5
    assert out["trend"][1]["trainingLoad"] == 20.0


def test_hrv_trend_reads_values_from_hrv_summary(monkeypatch):
    # Antes buscaba lastNightAvg en la raíz del payload: 0 días con datos aunque Garmin los tuviera,
    # y el LLM acababa pidiendo get_hrv_data día a día hasta agotar las iteraciones.
    monkeypatch.setattr(server, "_garmin_client", lambda: _FakeGarmin())

    out = server._hrv_trend({"start_date": "2026-09-01", "end_date": "2026-09-03"})

    assert out["days_with_data"] == 2
    assert out["trend"][0] == {"date": "2026-09-01", "hrv": 55, "weekly_avg": 60, "status": "BALANCED"}
    assert out["trend"][1] == {"date": "2026-09-02", "hrv": None, "weekly_avg": None, "status": None}


def test_vo2max_trend_returns_every_estimate_in_one_range_call(monkeypatch):
    # Antes: un get_max_metrics cada 7 días (muchas muestras en días sin estimación) con el payload entero.
    calls = []

    class _Fake:
        def connectapi(self, path: str):
            calls.append(path)
            return [
                {"generic": {"calendarDate": "2026-09-05", "vo2MaxPreciseValue": 56.0, "vo2MaxValue": 56.0},
                 "cycling": None, "heatAltitudeAcclimation": {"heatTrend": "ACCLIMATIZED"}},
                {"generic": None, "cycling": {"calendarDate": "2026-09-02", "vo2MaxValue": 52.0}},
                {"generic": {"calendarDate": "2026-09-09", "vo2MaxPreciseValue": None}, "cycling": None},
            ]

    monkeypatch.setattr(server, "_garmin_client", lambda: _Fake())

    out = server._vo2max_trend({"start_date": "2026-09-10", "end_date": "2026-09-01"})

    assert calls == ["/metrics-service/metrics/maxmet/daily/2026-09-01/2026-09-10"]
    assert out["days_with_data"] == 2
    assert out["trend"] == [
        {"date": "2026-09-02", "running": None, "cycling": 52.0},
        {"date": "2026-09-05", "running": 56.0, "cycling": None},
    ]


def test_heart_rate_zones_returns_closed_zones_per_sport(monkeypatch):
    class _Fake:
        def connectapi(self, path: str):
            assert path == "/biometric-service/heartRateZones"
            return [
                {"trainingMethod": "HR_RESERVE", "restingHeartRateUsed": 40, "lactateThresholdHeartRateUsed": 166,
                 "zone1Floor": 115, "zone2Floor": 135, "zone3Floor": 146, "zone4Floor": 158, "zone5Floor": 169,
                 "maxHeartRateUsed": 179, "sport": "RUNNING"},
                {"trainingMethod": "HR_RESERVE", "sport": "SWIMMING", "zone1Floor": None},  # sin zonas: se omite
            ]

    monkeypatch.setattr(server, "_garmin_client", lambda: _Fake())

    out = server._heart_rate_zones({})

    assert len(out["sports"]) == 1
    running = out["sports"][0]
    assert running["sport"] == "running" and running["method"] == "HR_RESERVE" and running["max_hr"] == 179
    assert running["zones"] == [
        {"zone": 1, "min_bpm": 115, "max_bpm": 134}, {"zone": 2, "min_bpm": 135, "max_bpm": 145},
        {"zone": 3, "min_bpm": 146, "max_bpm": 157}, {"zone": 4, "min_bpm": 158, "max_bpm": 168},
        {"zone": 5, "min_bpm": 169, "max_bpm": 179},
    ]


def test_training_effect_reads_activity_fields(monkeypatch):
    monkeypatch.setattr(server, "_garmin_client", lambda: _FakeGarmin())

    out = server._training_effect({"activity_id": 12345})

    assert out["activity_id"] == 12345
    assert out["aerobicTrainingEffect"] == 3.2
    assert out["anaerobicTrainingEffect"] == 1.1


def test_training_effect_requires_activity_id(monkeypatch):
    monkeypatch.setattr(server, "_garmin_client", lambda: _FakeGarmin())

    out = server._training_effect({})

    assert "activity_id requerido" in out["message"]


def test_sleep_summary_extracts_main_fields(monkeypatch):
    monkeypatch.setattr(server, "_garmin_client", lambda: _FakeGarmin())

    out = server.get_sleep_summary("2026-09-02")

    assert out["date"] == "2026-09-02"
    assert out["sleepTimeSeconds"] == 25200
    assert out["sleepScore"] == 82


def test_dispatch_passthrough_personal_record(monkeypatch):
    monkeypatch.setattr(server, "_garmin_client", lambda: _FakeGarmin())

    out = server._dispatch("get_personal_record", {})

    assert isinstance(out, list)
    assert out[0]["record_type"] == "Fastest 5K"


def test_retry_once_on_authentication_error_then_success(monkeypatch):
    first = _ClientWithPersonalRecord(GarminConnectAuthenticationError("expired session"))
    second = _ClientWithPersonalRecord([{"ok": True}])
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return first if calls["count"] == 1 else second

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    out = server._dispatch("get_personal_record", {})

    assert out == [{"ok": True}]
    assert calls["count"] == 2


def test_retry_once_on_connection_error_401_then_success(monkeypatch):
    first = _ClientWithPersonalRecord(GarminConnectConnectionError("API Error 401: Unauthorized"))
    second = _ClientWithPersonalRecord([{"ok": "recovered"}])
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return first if calls["count"] == 1 else second

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    out = server._dispatch("get_personal_record", {})

    assert out == [{"ok": "recovered"}]
    assert calls["count"] == 2


def test_retry_once_on_connection_error_403_then_success(monkeypatch):
    first = _ClientWithPersonalRecord(GarminConnectConnectionError("API Error 403: Forbidden"))
    second = _ClientWithPersonalRecord([{"ok": "recovered-403"}])
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return first if calls["count"] == 1 else second

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    out = server._dispatch("get_personal_record", {})

    assert out == [{"ok": "recovered-403"}]
    assert calls["count"] == 2


def test_connection_error_500_does_not_retry_or_invalidate_cache(monkeypatch):
    cached_client = _ClientWithPersonalRecord(GarminConnectConnectionError("API Error 500: Server exploded"))
    server._client = cached_client
    server._client_identity = ("user@example.com", "secret")
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return cached_client

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    with pytest.raises(GarminConnectConnectionError, match="API Error 500"):
        server._dispatch("get_personal_record", {})

    assert calls["count"] == 1
    assert server._client is cached_client
    assert server._client_identity == ("user@example.com", "secret")


def test_connection_error_429_does_not_retry_or_invalidate_cache(monkeypatch):
    cached_client = _ClientWithPersonalRecord(GarminConnectConnectionError("API Error 429: Rate limit"))
    server._client = cached_client
    server._client_identity = ("user@example.com", "secret")
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return cached_client

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    with pytest.raises(GarminConnectConnectionError, match="API Error 429"):
        server._dispatch("get_personal_record", {})

    assert calls["count"] == 1
    assert server._client is cached_client
    assert server._client_identity == ("user@example.com", "secret")


def test_too_many_requests_direct_does_not_retry_or_invalidate_cache(monkeypatch):
    cached_client = _ClientWithPersonalRecord(GarminConnectTooManyRequestsError("429 direct"))
    server._client = cached_client
    server._client_identity = ("user@example.com", "secret")
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return cached_client

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    with pytest.raises(GarminConnectTooManyRequestsError, match="429 direct"):
        server._dispatch("get_personal_record", {})

    assert calls["count"] == 1
    assert server._client is cached_client
    assert server._client_identity == ("user@example.com", "secret")


def test_successful_passthrough_keeps_previous_behavior(monkeypatch):
    cached_client = _ClientWithPersonalRecord([{"record_type": "Fastest 10K", "value": "37:10"}])
    server._client = cached_client
    server._client_identity = ("user@example.com", "secret")
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return cached_client

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    out = server._dispatch("get_personal_record", {})

    assert out == [{"record_type": "Fastest 10K", "value": "37:10"}]
    assert calls["count"] == 1
    assert server._client is cached_client
    assert server._client_identity == ("user@example.com", "secret")


def test_garmin_client_logs_in_with_per_user_tokenstore(monkeypatch, tmp_path):
    calls = []

    class _LoginGarmin:
        def __init__(self, email, password):
            self.email = email

        def login(self, tokenstore=None):
            calls.append(tokenstore)

    monkeypatch.setenv("GARMIN_EMAIL", "Atleta@Example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "x")
    monkeypatch.delenv("GARMINTOKENS", raising=False)
    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(server, "Garmin", _LoginGarmin)
    server._invalidate_client_cache()

    server._garmin_client()
    server._garmin_client()  # segundo uso en el mismo proceso: sin login nuevo

    assert len(calls) == 1
    store = calls[0]
    assert store.startswith(str(tmp_path / ".garminconnect" / "kairos"))
    # Misma carpeta para el mismo email aunque cambien mayúsculas.
    assert server._tokenstore_dir("atleta@example.com") == server._tokenstore_dir("ATLETA@example.com")
    server._invalidate_client_cache()


def test_rejected_saved_tokens_at_login_fall_back_to_password(monkeypatch, tmp_path):
    # Caso real (21/10): tras cambiar la contraseña, Garmin rechaza los tokens guardados
    # dentro de login() ("Failed to retrieve social profile").
    store = tmp_path / "tokens"
    store.mkdir()
    (store / "garmin_tokens.json").write_text("{}")
    monkeypatch.setenv("GARMIN_EMAIL", "atleta@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "nueva")
    monkeypatch.setenv("GARMINTOKENS", str(store))
    logins = []

    class _StaleTokensGarmin:
        def __init__(self, email, password):
            pass

        def login(self, tokenstore=None):
            had_tokens = (server.Path(tokenstore) / "garmin_tokens.json").exists()
            logins.append(had_tokens)
            if had_tokens:
                raise GarminConnectAuthenticationError("Failed to retrieve social profile")
            (server.Path(tokenstore) / "garmin_tokens.json").write_text('{"nuevo": true}')

    monkeypatch.setattr(server, "Garmin", _StaleTokensGarmin)
    server._invalidate_client_cache()

    assert server._garmin_client() is not None
    assert logins == [True, False]
    assert (store / "garmin_tokens.json").read_text() == '{"nuevo": true}'
    server._invalidate_client_cache()


def test_auth_error_clears_saved_tokens_before_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_EMAIL", "atleta@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "x")
    monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "tokens"))
    state = {"n": 0}

    class _AuthGarmin:
        def __init__(self, email, password):
            pass

        def login(self, tokenstore=None):
            (server.Path(tokenstore) / "garmin_tokens.json").write_text("{}")

        def get_stats(self, day):
            state["n"] += 1
            if state["n"] == 1:
                raise GarminConnectAuthenticationError("token caducado")
            return {"ok": True}

    monkeypatch.setattr(server, "Garmin", _AuthGarmin)
    server._invalidate_client_cache()
    server._garmin_client()
    assert (tmp_path / "tokens" / "garmin_tokens.json").exists()

    deleted = []
    real_rmtree = server.shutil.rmtree
    monkeypatch.setattr(server.shutil, "rmtree", lambda p, ignore_errors=False: (deleted.append(str(p)), real_rmtree(p, ignore_errors=ignore_errors)))
    assert server._invoke_with_auth_retry("get_stats", "2026-10-21") == {"ok": True}
    assert deleted == [str(tmp_path / "tokens")]
    server._invalidate_client_cache()


def test_all_tools_follow_mcp_style_guide():
    # Guía de estilo MCP de apiaddicts (tools/lint_mcp.py), sin red: título, descripción,
    # parámetros descritos, inputSchema de tipo object y solo lectura en todas las tools.
    import asyncio

    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 42
    for tool in tools:
        assert tool.title, tool.name
        assert tool.description and tool.description.strip(), tool.name
        assert tool.inputSchema.get("type") == "object", tool.name
        for prop_name, prop in (tool.inputSchema.get("properties") or {}).items():
            assert prop.get("description"), f"{tool.name}.{prop_name}"
        assert tool.annotations is not None and tool.annotations.readOnlyHint is True, tool.name
        assert tool.annotations.destructiveHint is False, tool.name


def test_every_tool_calls_garminconnect_with_its_real_signature(monkeypatch):
    # Contrato sin red: cada tool llama a garminconnect con argumentos que encajan con la firma
    # de la versión instalada. Habría detectado las 8 tools rotas del 22/10 (get_daily_steps,
    # get_training_status, get_vo2max_trend…) y detectará cambios de firma al actualizar la librería.
    import asyncio
    import inspect

    from garminconnect import Garmin

    calls: list[tuple[str, tuple, dict]] = []

    class _SignatureCheckingClient:
        def __getattr__(self, name):
            real = getattr(Garmin, name)

            def _call(*args, **kwargs):
                inspect.signature(real).bind(None, *args, **kwargs)  # TypeError si no casan
                calls.append((name, args, kwargs))
                return {}

            return _call

    monkeypatch.setattr(server, "_garmin_client", lambda: _SignatureCheckingClient())
    tools = asyncio.run(server.mcp.list_tools())
    failures = []
    for tool in tools:
        props = tool.inputSchema.get("properties") or {}
        args = {}
        if "activity_id" in props:
            args["activity_id"] = 123
        if "start_date" in props:
            args.update({"start_date": "2026-10-08", "end_date": "2026-10-21"})
        elif "date" in props:
            args["date"] = "2026-10-21"
        try:
            asyncio.run(server.mcp.call_tool(tool.name, args))
        except Exception as exc:  # noqa: BLE001 — se informa de todas a la vez
            failures.append(f"{tool.name}: {exc}")
    assert not failures, "\n".join(failures)
    assert len({name for name, _, _ in calls}) >= 30


def test_healthcheck_classifies_tool_results():
    import importlib.util

    spec = importlib.util.spec_from_file_location("healthcheck_mcp", Path(__file__).resolve().parent.parent / "tools" / "healthcheck_mcp.py")
    hc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hc)

    assert hc.classify(False, '{"totalSteps": 5557}') == "OK"
    assert hc.classify(False, "") == "VACÍA"
    assert hc.classify(False, "[]") == "VACÍA"
    assert hc.classify(False, '{"avg": null, "max": null}') == "VACÍA"
    assert hc.classify(True, "Garmin.get_daily_steps() missing 1 required positional argument: 'end'") == "ERROR"
    assert hc.classify(True, "Mobile login returned 429 — IP rate limited by Garmin") == "ENTORNO"
    assert hc.classify(True, "GarminConnectAuthenticationError: authentication failed") == "ENTORNO"


def test_server_package_does_not_import_the_host():
    # Arquitectura: garmin_mcp es un servidor MCP independiente del host (agent/). Kairos lo usa solo
    # por el protocolo MCP (stdio); si el servidor importara agent/, quedarían acoplados.
    import ast

    pkg = Path(__file__).resolve().parent.parent / "garmin_mcp"
    offenders = []
    for py in pkg.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{py.name}: {n}" for n in names if n == "agent" or n.startswith("agent.")]
    assert not offenders, offenders


def test_tool_catalog_readme_is_up_to_date():
    # garmin_mcp/README.md lleva un catálogo generado desde el servidor; si se cambia una tool sin
    # regenerarlo (python tools/gen_mcp_docs.py), este test falla.
    import asyncio
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_mcp_docs", Path(__file__).resolve().parent.parent / "tools" / "gen_mcp_docs.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    expected = gen.render(asyncio.run(server.mcp.list_tools()))
    assert gen.current_block(gen.README.read_text(encoding="utf-8")) == expected, (
        "garmin_mcp/README.md desfasado: ejecuta python tools/gen_mcp_docs.py"
    )


# ─── Llamadas en paralelo (24/10) ──────────────────────────────────────────

def test_tools_run_in_parallel_threads(monkeypatch):
    import asyncio
    import time as _time

    def slow_dispatch(name, args):
        _time.sleep(0.3)
        return {"tool": name}

    monkeypatch.setattr(server, "_dispatch", slow_dispatch)

    async def run():
        t0 = _time.perf_counter()
        await asyncio.gather(*(server.mcp.call_tool("get_activity", {"activity_id": i}) for i in range(3)))
        return _time.perf_counter() - t0

    elapsed = asyncio.run(run())
    # En serie serían ~0,9 s; en hilos, ~0,3 s.
    assert elapsed < 0.7, elapsed


def test_parallel_calls_never_exceed_the_garmin_limit(monkeypatch):
    import asyncio
    import threading
    import time as _time

    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def counting_dispatch(name, args):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        _time.sleep(0.1)
        with lock:
            state["now"] -= 1
        return {}

    monkeypatch.setattr(server, "_dispatch", counting_dispatch)
    monkeypatch.setattr(server, "_garmin_limiter", None)

    async def run():
        await asyncio.gather(*(server.mcp.call_tool("get_activity", {"activity_id": i}) for i in range(8)))

    asyncio.run(run())
    assert state["peak"] == server.GARMIN_MAX_CONCURRENT_CALLS


def test_two_auth_failures_on_the_same_client_log_in_once(monkeypatch):
    failed = object()
    logins = []

    def fake_locked():
        new = object()
        logins.append(new)
        server._client = new
        return new

    monkeypatch.setattr(server, "_garmin_client_locked", fake_locked)
    monkeypatch.setattr(server, "_invalidate_client_cache", lambda clear_tokens=False: setattr(server, "_client", None))
    monkeypatch.setattr(server, "_client", failed)

    first = server._relogin_after_auth_failure(failed)
    # El segundo hilo falló con el mismo cliente viejo: ya hay uno nuevo y no se vuelve a iniciar sesión.
    monkeypatch.setattr(server, "_garmin_client_locked", lambda: server._client)
    second = server._relogin_after_auth_failure(failed)

    assert len(logins) == 1 and first is second


def test_threaded_tools_keep_their_parameter_schema():
    import asyncio

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    props = tools["get_activity"].inputSchema["properties"]
    assert "activity_id" in props and props["activity_id"]["type"] == "integer"
    assert props["activity_id"]["description"].startswith("ID de la actividad")
