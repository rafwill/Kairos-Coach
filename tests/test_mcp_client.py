"""
tests/test_mcp_client.py
Cobertura de cliente MCP local-only (backend frozen).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import mcp_client
from agent import mcp_adapter


@pytest.fixture
def garmin_creds(monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "user@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")


def test_get_configured_mcp_backend_default_frozen(monkeypatch):
    monkeypatch.delenv("MCP_BACKEND", raising=False)
    assert mcp_client.get_configured_mcp_backend() == "frozen"


def test_get_configured_mcp_backend_accepts_frozen(monkeypatch):
    monkeypatch.setenv("MCP_BACKEND", "frozen")
    assert mcp_client.get_configured_mcp_backend() == "frozen"


def test_get_configured_mcp_backend_non_frozen_forces_frozen(monkeypatch):
    monkeypatch.setenv("MCP_BACKEND", "upstream")
    assert mcp_client.get_configured_mcp_backend() == "frozen"


def test_get_configured_mcp_backend_invalid_forces_frozen(monkeypatch):
    monkeypatch.setenv("MCP_BACKEND", "bad-value")
    assert mcp_client.get_configured_mcp_backend() == "frozen"


def test_get_server_params_frozen_requires_local_command(monkeypatch, garmin_creds):
    monkeypatch.setattr(mcp_client, "_resolve_frozen_command", lambda: None)

    with pytest.raises(RuntimeError, match="launcher local del MCP propio"):
        mcp_client._get_server_params(essential_only=True, backend="frozen")


def test_get_server_params_frozen_uses_local_command(monkeypatch, garmin_creds):
    monkeypatch.setattr(mcp_client, "_resolve_frozen_command", lambda: "C:/bin/garmin-mcp-frozen.exe")

    params = mcp_client._get_server_params(essential_only=True, backend="frozen")

    assert params.command.endswith("garmin-mcp-frozen.exe")
    assert params.env["KAIROS_MCP_BACKEND_EFFECTIVE"] == "frozen"
    assert "GARMIN_ENABLED_TOOLS" in params.env


def test_get_server_params_ignores_upstream_and_uses_frozen(monkeypatch, garmin_creds):
    monkeypatch.setattr(mcp_client, "_resolve_frozen_command", lambda: "C:/bin/garmin-mcp-frozen.exe")

    params = mcp_client._get_server_params(essential_only=True, backend="upstream")

    assert params.command.endswith("garmin-mcp-frozen.exe")
    assert params.args == []
    assert params.env["KAIROS_MCP_BACKEND_EFFECTIVE"] == "frozen"


def test_resolve_frozen_command_prefers_local_wrapper(monkeypatch):
    monkeypatch.delenv("KAIROS_MCP_FROZEN_COMMAND", raising=False)
    resolved = mcp_client._resolve_frozen_command()
    assert resolved is not None
    assert resolved.endswith("garmin-mcp-frozen.cmd") or resolved.endswith("garmin-mcp-frozen.sh")


@pytest.mark.asyncio
async def test_garmin_mcp_session_opens_frozen_backend(monkeypatch):
    monkeypatch.setenv("MCP_BACKEND", "frozen")

    calls: list[str] = []

    def fake_get_server_params(*, essential_only: bool, backend: str | None = None):
        assert essential_only is True
        calls.append(backend or "")
        return SimpleNamespace(command="garmin-mcp", args=[], env={})

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
    monkeypatch.setattr(mcp_client, "stdio_client", lambda _params: DummyStdioCM())
    monkeypatch.setattr(mcp_client, "ClientSession", DummyClientSession)

    async with mcp_client.garmin_mcp_session(essential_only=True) as session:
        assert session.initialized is True

    assert calls == ["frozen"]
    assert mcp_client.os.environ.get("KAIROS_MCP_BACKEND_EFFECTIVE") == "frozen"


@pytest.mark.asyncio
async def test_garmin_mcp_session_raises_when_frozen_unavailable(monkeypatch):
    monkeypatch.setenv("MCP_BACKEND", "frozen")

    def fake_get_server_params(*, essential_only: bool, backend: str | None = None):
        assert essential_only is True
        assert backend == "frozen"
        raise RuntimeError("frozen not found")

    monkeypatch.setattr(mcp_client, "_get_server_params", fake_get_server_params)

    with pytest.raises(RuntimeError, match="frozen not found"):
        async with mcp_client.garmin_mcp_session(essential_only=True):
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
    assert len(mcp_client.GARMIN_ESSENTIAL_TOOLS) == 40
    assert len(mcp_client.KAIROS_INTERNAL_ESSENTIAL_TOOLS) == 3
    assert len(mcp_client.ALL_ESSENTIAL_TOOLS) == 43
    assert len(set(mcp_client.ALL_ESSENTIAL_TOOLS)) == 43


def test_essentials_catalog_contains_must_have_critical_tools():
    must_have = {
        "get_user_profile",
        "get_activities_by_date",
        "get_activity",
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
    monkeypatch.setenv("KAIROS_MCP_BACKEND_EFFECTIVE", "frozen")
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
    monkeypatch.setenv("KAIROS_MCP_BACKEND_EFFECTIVE", "frozen")
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


def test_resolve_local_fastpath_response_only_for_frozen(monkeypatch):
    monkeypatch.setattr(mcp_adapter._storage, "get_load_metrics_series", lambda days: [{"date": "2026-09-02", "tss": 42.0}])
    out = mcp_adapter.resolve_local_fastpath_response(
        "get_training_load_trend",
        {"start_date": "2026-09-01", "end_date": "2026-09-03"},
        backend_effective="upstream",
    )
    assert out is None


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

    out = mcp_adapter.resolve_cached_tool_response("get_user_profile", {}, backend_effective="frozen")
    assert out is None


def test_resolve_local_fastpath_response_builds_training_load_payload(monkeypatch):
    monkeypatch.setattr(
        mcp_adapter._storage,
        "get_load_metrics_series",
        lambda days: [
            {"date": "2026-09-01", "tss": 10.0},
            {"date": "2026-09-02", "tss": 20.5},
            {"date": "2026-09-05", "tss": 99.0},
        ],
    )
    out = mcp_adapter.resolve_local_fastpath_response(
        "get_training_load_trend",
        {"start_date": "2026-09-01", "end_date": "2026-09-03"},
        backend_effective="frozen",
    )
    assert out is not None
    assert '"date": "2026-09-01"' in out
    assert '"trainingLoad": 20.5' in out
    assert '"date": "2026-09-05"' not in out


@pytest.mark.asyncio
async def test_call_tool_uses_local_fastpath_when_available(monkeypatch):
    monkeypatch.setenv("KAIROS_MCP_BACKEND_EFFECTIVE", "frozen")
    monkeypatch.setattr(
        mcp_client,
        "resolve_local_fastpath_response",
        lambda tool_name, arguments, backend_effective: "[{\"date\":\"2026-09-03\",\"trainingLoad\":12.3}]",
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
async def test_call_tool_returns_cached_response_on_runtime_error_in_frozen(monkeypatch):
    monkeypatch.setenv("KAIROS_MCP_BACKEND_EFFECTIVE", "frozen")
    monkeypatch.setattr(
        mcp_client,
        "resolve_local_fastpath_response",
        lambda tool_name, arguments, backend_effective: None,
    )
    monkeypatch.setattr(
        mcp_client,
        "resolve_cached_tool_response",
        lambda tool_name, arguments, backend_effective: '{"cached":true}',
    )

    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            raise RuntimeError("mcp down")

    out = await mcp_client.call_tool(FakeSession(), "get_user_profile", {})
    assert out == '{"cached":true}'
