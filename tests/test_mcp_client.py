"""
tests/test_mcp_client.py
Cobertura del cliente MCP (arranque del servidor, contratos y normalización).
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
    assert len(set(mcp_client.GARMIN_ESSENTIAL_TOOLS + mcp_client.KAIROS_INTERNAL_ESSENTIAL_TOOLS)) == 45


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
    catalog = set(mcp_client.GARMIN_ESSENTIAL_TOOLS + mcp_client.KAIROS_INTERNAL_ESSENTIAL_TOOLS)
    missing = must_have - catalog
    assert not missing, f"Faltan tools críticas en catálogo: {sorted(missing)}"


def test_contract_registry_covers_all_essential_tools():
    catalog = set(mcp_client.GARMIN_ESSENTIAL_TOOLS + mcp_client.KAIROS_INTERNAL_ESSENTIAL_TOOLS)
    missing = catalog - set(mcp_adapter.TOOL_CONTRACTS_V1)
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
async def test_call_tool_returns_error_text_when_the_server_fails():
    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            raise RuntimeError("mcp down")

    out = await mcp_client.call_tool(FakeSession(), "get_user_profile", {})
    assert "Error al llamar a 'get_user_profile': mcp down" == out


@pytest.mark.asyncio
async def test_call_tool_returns_error_text_on_protocol_error():
    # 28/10: McpError no es RuntimeError; antes llegaba hasta la CLI y cerraba Kairos.
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            raise McpError(ErrorData(code=-32001, message="Request timed out"))

    out = await mcp_client.call_tool(FakeSession(), "get_user_profile", {})
    assert out == "Error al llamar a 'get_user_profile': Request timed out"


# ─── Errores de Garmin, avisos de transparencia y cierre (28/10) ─────────────

def test_classify_mcp_error_text_kinds_and_retry_after():
    c = mcp_client._classify_mcp_error_text
    assert c("Rate limited (429), Retry-After: 30") == {"kind": "rate_limit", "http_code": "429", "retry_after_seconds": "30"}
    assert c("HTTP 403 Forbidden")["kind"] == "auth_or_session"
    assert c("Login failed: all login strategies exhausted")["kind"] == "auth_or_session"
    assert c("HTTP 403 forbidden, too many requests")["kind"] == "mixed_auth_and_rate_limit"
    assert c("timeout leyendo la respuesta") == {"kind": "unknown", "http_code": "", "retry_after_seconds": ""}


@pytest.mark.asyncio
async def test_tool_error_is_recorded_for_the_reply_and_consumed_once():
    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            return SimpleNamespace(content=[SimpleNamespace(text="HTTP 429 Too Many Requests")], isError=True)

    mcp_client.reset_tool_transparency_events()
    out = await mcp_client.call_tool(FakeSession(), "get_user_profile", {})
    assert out == "HTTP 429 Too Many Requests"  # el texto del error llega igual al llamador
    events = mcp_client.consume_tool_transparency_events()
    assert [(e["tool"], e["mode"]) for e in events] == [("get_user_profile", "mcp_error")]
    assert events[0]["reason"] == "kind=rate_limit;http=429;retry_after="
    assert mcp_client.consume_tool_transparency_events() == []  # se vacía al leerlo


@pytest.mark.asyncio
async def test_call_tool_without_content_says_no_data():
    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            return SimpleNamespace(content=[])

    assert await mcp_client.call_tool(FakeSession(), "get_user_profile", {}) == "Sin datos disponibles."


def test_benign_shutdown_errors_only_when_all_are_benign():
    import anyio

    benign = mcp_client._is_benign_mcp_shutdown_error
    assert benign(BaseExceptionGroup("cierre", [anyio.ClosedResourceError(), anyio.BrokenResourceError()]))
    assert benign(RuntimeError("Attempted to exit cancel scope in a different task than it was entered in"))
    assert not benign(BaseExceptionGroup("cierre", [anyio.ClosedResourceError(), ValueError("de verdad")]))
    assert not benign(OSError("pipe rota en mitad de una llamada"))


@pytest.mark.asyncio
async def test_list_available_tools_maps_schema():
    tool = SimpleNamespace(name="get_stats", description="Resumen diario", inputSchema={"type": "object"})

    class FakeSession:
        async def list_tools(self):
            return SimpleNamespace(tools=[tool])

    assert await mcp_client.list_available_tools(FakeSession()) == [
        {"name": "get_stats", "description": "Resumen diario", "input_schema": {"type": "object"}}
    ]


def test_server_params_use_zscaler_certificate_when_present(garmin_creds, monkeypatch, tmp_path):
    (tmp_path / "zscaler-ca.pem").write_text("cert", encoding="utf-8")
    monkeypatch.setattr(mcp_client, "PROJECT_ROOT", tmp_path)
    env = mcp_client._get_server_params().env
    pem = str(tmp_path / "zscaler-ca.pem")
    assert env["REQUESTS_CA_BUNDLE"] == env["CURL_CA_BUNDLE"] == env["SSL_CERT_FILE"] == pem


@pytest.mark.asyncio
async def test_failures_in_parallel_calls_reach_the_reply():
    # 28/10: con asyncio.gather cada llamada corre en una tarea con copia del contexto; el aviso se guardaba en
    # una lista nueva de la tarea y la respuesta no lo veía (sueño, datos del día y tools del LLM en paralelo).
    import asyncio

    class FakeSession:
        async def call_tool(self, name: str, _arguments: dict):
            return SimpleNamespace(content=[SimpleNamespace(text=f"Error executing tool {name}: HTTP 503")], isError=True)

    mcp_client.reset_tool_transparency_events()
    await asyncio.gather(*(mcp_client.call_tool(FakeSession(), "get_sleep_summary", {"date": d})
                           for d in ("2026-10-26", "2026-10-27", "2026-10-28")))
    assert len(mcp_client.consume_tool_transparency_events()) == 3


@pytest.mark.asyncio
async def test_client_side_failure_is_recorded_too():
    # 28/10: si el servidor no contesta, la ruta decía "sin datos" sin ningún aviso.
    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict):
            raise RuntimeError("Connection closed")

    mcp_client.reset_tool_transparency_events()
    await mcp_client.call_tool(FakeSession(), "get_user_profile", {})
    mcp_client.record_tool_failure("get_hrv_data", "sin respuesta en 8 s")
    events = mcp_client.consume_tool_transparency_events()
    assert [(e["tool"], e["mode"], e["reason"]) for e in events] == [
        ("get_user_profile", "mcp_error", "cliente=RuntimeError"),
        ("get_hrv_data", "mcp_error", "sin respuesta en 8 s"),
    ]
