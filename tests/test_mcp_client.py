"""
tests/test_mcp_client.py
Cobertura del cliente MCP (arranque del servidor, contratos, caché de contingencia).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.mcp import client as mcp_client
from agent.mcp import contracts as mcp_adapter


@pytest.fixture
def garmin_creds(monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "user@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")


def test_get_server_params_launches_garmin_mcp_with_same_python(garmin_creds):
    params = mcp_client._get_server_params()

    assert params.command == mcp_client.sys.executable
    assert params.args == ["-m", "garmin_mcp"]
    assert params.cwd == str(mcp_client.PROJECT_ROOT)
    assert params.env["GARMIN_EMAIL"] == "user@example.com"
    # Restos del MCP de terceros que garmin_mcp no lee: ya no se envían.
    assert "GARMIN_ENABLED_TOOLS" not in params.env
    assert "KAIROS_MCP_BACKEND_EFFECTIVE" not in params.env


def test_get_server_params_requires_garmin_credentials(monkeypatch):
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="GARMIN_EMAIL y GARMIN_PASSWORD"):
        mcp_client._get_server_params()


@pytest.mark.asyncio
async def test_garmin_mcp_session_opens_and_initializes(monkeypatch, tmp_path):
    calls: list[str] = []
    errlogs: list = []
    # El stderr del servidor va a un fichero, no a la terminal del usuario (24/10).
    monkeypatch.setenv("KAIROS_MCP_LOG_FILE", str(tmp_path / "garmin_mcp.log"))

    def fake_get_server_params():
        calls.append("params")
        return SimpleNamespace(command="python", args=["-m", "garmin_mcp"], env={})

    class DummyStdioCM:
        async def __aenter__(self):
            return ("read", "write")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummyClientSession:
        def __init__(self, _read, _write):
            self.initialized = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def initialize(self):
            self.initialized = True

    monkeypatch.setattr(mcp_client, "_get_server_params", fake_get_server_params)
    monkeypatch.setattr(mcp_client, "stdio_client", lambda _params, errlog=None: errlogs.append(errlog) or DummyStdioCM())
    monkeypatch.setattr(mcp_client, "ClientSession", DummyClientSession)

    async with mcp_client.garmin_mcp_session() as session:
        assert session.initialized is True

    assert calls == ["params"]
    assert errlogs and errlogs[0] is not mcp_client.sys.stderr
    assert errlogs[0].name.endswith("garmin_mcp.log")


@pytest.mark.asyncio
async def test_garmin_mcp_session_raises_when_params_fail(monkeypatch):
    def fake_get_server_params():
        raise ValueError("sin credenciales")

    monkeypatch.setattr(mcp_client, "_get_server_params", fake_get_server_params)

    with pytest.raises(ValueError, match="sin credenciales"):
        async with mcp_client.garmin_mcp_session():
            raise AssertionError("unreachable")


def test_normalize_tool_invocation_alias_personal_records():
    name, args = mcp_adapter.normalize_tool_invocation("get_personal_records", {})
    assert name == "get_personal_record"
    assert args == {}


def test_normalize_tool_invocation_expands_date_for_body_battery():
    name, args = mcp_adapter.normalize_tool_invocation("get_body_battery", {"date": "2026-09-03"})
    assert name == "get_body_battery"
    assert args["start_date"] == "2026-09-03"
    assert args["end_date"] == "2026-09-03"
    assert "date" not in args


def test_validate_min_input_contract_requires_range_for_body_composition():
    err = mcp_adapter.validate_min_input_contract("get_body_composition", {})
    assert err is not None
    assert "start_date + end_date" in err


def test_essentials_catalog_counts_and_uniqueness():
    assert len(mcp_client.GARMIN_ESSENTIAL_TOOLS) == 42
    assert len(mcp_client.KAIROS_INTERNAL_ESSENTIAL_TOOLS) == 3
    assert len(mcp_client.ALL_ESSENTIAL_TOOLS) == 45
    assert len(set(mcp_client.ALL_ESSENTIAL_TOOLS)) == 45


def test_essentials_catalog_contains_must_have_critical_tools():
    must_have = {
        "get_user_profile",
        "get_activities_by_date",
        "get_activity",
        "get_activity_details",
        "get_body_battery",
        "get_hrv_data",
        "get_sleep_summary",
        "get_training_load_trend",
        "get_personal_record",
        "kairos_load_trends",
    }
    catalog = set(mcp_client.ALL_ESSENTIAL_TOOLS)
    missing = must_have - catalog
    assert not missing, f"Faltan tools críticas en catálogo: {sorted(missing)}"


def test_contract_registry_covers_all_essential_tools():
    contracts = mcp_adapter.get_contract_registry()
    missing = set(mcp_client.ALL_ESSENTIAL_TOOLS) - set(contracts.keys())
    assert not missing, f"Faltan contratos v1 para: {sorted(missing)}"


def test_validate_min_input_contract_requires_activity_id_for_get_activity():
    err = mcp_adapter.validate_min_input_contract("get_activity", {})
    assert err is not None
    assert "activity_id" in err


def test_validate_min_input_contract_accepts_legacy_range_for_get_activities_by_date():
    err = mcp_adapter.validate_min_input_contract(
        "get_activities_by_date",
        {"startdate": "2026-09-01", "enddate": "2026-09-03"},
    )
    assert err is None


@pytest.mark.asyncio
async def test_call_tool_applies_alias_and_normalization():
    calls: list[tuple[str, dict]] = []

    class FakeSession:
        async def call_tool(self, name: str, arguments: dict):
            calls.append((name, arguments))
            return SimpleNamespace(content=[SimpleNamespace(text="ok")])

    out = await mcp_client.call_tool(FakeSession(), "get_personal_records", {})

    assert out == "ok"
    assert calls == [("get_personal_record", {})]


@pytest.mark.asyncio
async def test_call_tool_returns_contract_error_without_calling_session():
    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            raise AssertionError("must not be called")

    out = await mcp_client.call_tool(FakeSession(), "get_body_battery", {})
    assert "Error de contrato" in out


@pytest.mark.asyncio
async def test_call_tool_records_transparency_event_for_fastpath(monkeypatch):
    monkeypatch.setenv("KAIROS_MCP_ENABLE_FALLBACK", "true")
    monkeypatch.setattr(mcp_client, "resolve_local_fastpath_response", lambda *_args, **_kwargs: '{"ok":1}')
    monkeypatch.setattr(mcp_client, "cache_tool_response", lambda *_args, **_kwargs: None)

    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            raise AssertionError("must not be called when fastpath is available")

    mcp_client.reset_tool_transparency_events()
    out = await mcp_client.call_tool(FakeSession(), "get_training_load_trend", {"start_date": "2026-09-01", "end_date": "2026-09-03"})
    events = mcp_client.consume_tool_transparency_events()

    assert out == '{"ok":1}'
    assert len(events) == 1
    assert events[0]["tool"] == "get_training_load_trend"
    assert events[0]["mode"] == "fallback_fastpath"


@pytest.mark.asyncio
async def test_call_tool_records_transparency_event_for_cached_error_fallback(monkeypatch):
    monkeypatch.setenv("KAIROS_MCP_ENABLE_FALLBACK", "true")
    monkeypatch.setattr(mcp_client, "resolve_local_fastpath_response", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mcp_client, "resolve_cached_tool_response", lambda *_args, **_kwargs: '{"cached":true}')

    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            raise RuntimeError("network boom")

    mcp_client.reset_tool_transparency_events()
    out = await mcp_client.call_tool(FakeSession(), "get_user_profile", {})
    events = mcp_client.consume_tool_transparency_events()

    assert out == '{"cached":true}'
    assert len(events) == 1
    assert events[0]["tool"] == "get_user_profile"
    assert events[0]["mode"] == "fallback_cache_on_error"
    assert "RuntimeError" in events[0]["reason"]


def test_cache_tool_response_ignores_error_payload(monkeypatch):
    saved = {}

    monkeypatch.setattr(mcp_adapter, "_load_adapter_cache_from_profile", lambda: {})
    monkeypatch.setattr(mcp_adapter, "_save_adapter_cache_to_profile", lambda payload: saved.update(payload))

    mcp_adapter.cache_tool_response("get_user_profile", {}, "Error executing tool get_user_profile: boom")

    assert saved == {}


def test_resolve_cached_tool_response_ignores_cached_errors(monkeypatch):
    fake_cache = {
        "tools": {
            "get_user_profile": {
                mcp_adapter._build_cache_key("get_user_profile", {}): {
                    "response": "Error executing tool get_user_profile: boom",
                    "updated_at": "2026-09-03T00:00:00Z",
                }
            }
        }
    }
    monkeypatch.setattr(mcp_adapter, "_load_adapter_cache_from_profile", lambda: fake_cache)

    out = mcp_adapter.resolve_cached_tool_response("get_user_profile", {})
    assert out is None


def test_fastpath_never_returns_kairos_tss_as_garmin_training_load(monkeypatch):
    # get_training_load_trend es la carga nativa de Garmin: la contingencia no la sustituye por el
    # TSS de Kairos (antes devolvía load_metrics_daily.tss con el nombre trainingLoad).
    monkeypatch.setattr(
        mcp_adapter._storage,
        "get_load_metrics_series",
        lambda days: [{"date": "2026-09-02", "tss": 20.5}],
    )
    monkeypatch.setattr(mcp_adapter, "resolve_cached_tool_response", lambda *_args, **_kwargs: None)
    out = mcp_adapter.resolve_local_fastpath_response(
        "get_training_load_trend",
        {"start_date": "2026-09-01", "end_date": "2026-09-03"},
    )
    assert out is None


@pytest.mark.asyncio
async def test_call_tool_uses_local_fastpath_when_available(monkeypatch):
    monkeypatch.setenv("KAIROS_MCP_ENABLE_FALLBACK", "true")
    monkeypatch.setattr(
        mcp_client,
        "resolve_local_fastpath_response",
        lambda tool_name, arguments: "[{\"date\":\"2026-09-03\",\"trainingLoad\":12.3}]",
    )

    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            raise AssertionError("must not call MCP when fast-path exists")

    out = await mcp_client.call_tool(
        FakeSession(),
        "get_training_load_trend",
        {"start_date": "2026-09-03", "end_date": "2026-09-03"},
    )
    assert "trainingLoad" in out


@pytest.mark.asyncio
async def test_call_tool_returns_cached_response_on_runtime_error(monkeypatch):
    monkeypatch.setenv("KAIROS_MCP_ENABLE_FALLBACK", "true")
    monkeypatch.setattr(
        mcp_client,
        "resolve_local_fastpath_response",
        lambda tool_name, arguments: None,
    )
    monkeypatch.setattr(
        mcp_client,
        "resolve_cached_tool_response",
        lambda tool_name, arguments: '{"cached":true}',
    )

    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            raise RuntimeError("mcp down")

    out = await mcp_client.call_tool(FakeSession(), "get_user_profile", {})
    assert out == '{"cached":true}'


@pytest.mark.asyncio
async def test_call_tool_default_single_path_ignores_fastpath(monkeypatch):
    monkeypatch.delenv("KAIROS_MCP_ENABLE_FALLBACK", raising=False)
    monkeypatch.setattr(
        mcp_client,
        "resolve_local_fastpath_response",
        lambda tool_name, arguments: '{"should":"not-use"}',
    )

    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            return SimpleNamespace(content=[SimpleNamespace(text="direct")])

    out = await mcp_client.call_tool(FakeSession(), "get_user_profile", {})
    assert out == "direct"


@pytest.mark.asyncio
async def test_call_tool_default_single_path_returns_error_without_cache(monkeypatch):
    monkeypatch.delenv("KAIROS_MCP_ENABLE_FALLBACK", raising=False)
    monkeypatch.setattr(mcp_client, "resolve_cached_tool_response", lambda *_args, **_kwargs: '{"cached":true}')

    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            raise RuntimeError("mcp down")

    out = await mcp_client.call_tool(FakeSession(), "get_user_profile", {})
    assert "Error al llamar a 'get_user_profile': mcp down" == out
