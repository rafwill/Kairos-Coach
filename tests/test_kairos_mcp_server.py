from __future__ import annotations

import pytest
from garminconnect.exceptions import GarminConnectAuthenticationError
from garminconnect.exceptions import GarminConnectConnectionError
from garminconnect.exceptions import GarminConnectTooManyRequestsError

from agent import kairos_mcp_server as server


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
    assert len(tools) == 41
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
