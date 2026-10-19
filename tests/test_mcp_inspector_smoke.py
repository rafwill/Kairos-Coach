"""
tests/test_mcp_inspector_smoke.py

Smoke tests de integracion para validar el MCP local de Kairos via
@modelcontextprotocol/inspector (modo CLI).

Estas pruebas se ejecutan solo bajo demanda:
  - RUN_MCP_INSPECTOR_SMOKE=1

Prerequisitos:
  - npx disponible en PATH
  - GARMIN_EMAIL y GARMIN_PASSWORD definidos en entorno
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = REPO_ROOT / "tmp" / "mcp_inspector_smoke"
NPX_BIN = shutil.which("npx") or shutil.which("npx.cmd")

_RUN_SMOKE = str(os.environ.get("RUN_MCP_INSPECTOR_SMOKE", "")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = [
    pytest.mark.skipif(not _RUN_SMOKE, reason="Set RUN_MCP_INSPECTOR_SMOKE=1 para ejecutar estos tests."),
    pytest.mark.skipif(NPX_BIN is None, reason="npx no disponible en PATH."),
]


def _server_command_path() -> Path:
    if os.name == "nt":
        return REPO_ROOT / "tools" / "garmin-mcp-frozen.cmd"
    return REPO_ROOT / "tools" / "garmin-mcp-frozen.sh"


def _build_test_env() -> dict[str, str]:
    env = os.environ.copy()
    cert_path = REPO_ROOT / "zscaler-ca.pem"

    if cert_path.exists():
        cert_str = str(cert_path)
        env.setdefault("REQUESTS_CA_BUNDLE", cert_str)
        env.setdefault("SSL_CERT_FILE", cert_str)
        env.setdefault("CURL_CA_BUNDLE", cert_str)

    env.setdefault("MCP_BACKEND", "frozen")
    return env


def _run_inspector_cli(*, method: str, output_name: str, extra_args: list[str] | None = None) -> dict:
    env = _build_test_env()
    server_cmd = _server_command_path()
    assert server_cmd.exists(), f"No existe launcher MCP local: {server_cmd}"

    TMP_DIR.mkdir(parents=True, exist_ok=True)

    # Variables que el servidor MCP necesita recibir explicitamente al ser lanzado por Inspector.
    server_env_pairs: list[str] = []
    for key in (
        "GARMIN_EMAIL",
        "GARMIN_PASSWORD",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "CURL_CA_BUNDLE",
        "MCP_BACKEND",
    ):
        value = str(env.get(key, "")).strip()
        if value:
            server_env_pairs.extend(["-e", f"{key}={value}"])

    cmd = [
        str(NPX_BIN),
        "-y",
        "@modelcontextprotocol/inspector",
        "--cli",
        *server_env_pairs,
        str(server_cmd),
        "--method",
        method,
        "--format",
        "json",
    ]
    if extra_args:
        cmd.extend(extra_args)

    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )

    out_file = TMP_DIR / output_name
    err_file = TMP_DIR / f"{output_name}.stderr.txt"
    out_file.write_text(proc.stdout or "", encoding="utf-8")
    err_file.write_text(proc.stderr or "", encoding="utf-8")

    assert proc.returncode == 0, (
        f"Inspector fallo (rc={proc.returncode}) en metodo {method}. "
        f"Ver stdout={out_file} y stderr={err_file}."
    )

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Salida no JSON en {method}. Ver {out_file} / {err_file}."
        ) from exc

    return payload


@pytest.fixture(scope="session")
def tools_list_payload() -> dict:
    return _run_inspector_cli(
        method="tools/list",
        output_name="mcp_tools_list.json",
        extra_args=["--strict"],
    )


def test_01_env_setup_ready():
    """Prueba 1: entorno minimo para ejecutar smoke de Inspector."""
    env = _build_test_env()

    required = ["GARMIN_EMAIL", "GARMIN_PASSWORD"]
    missing = [key for key in required if not str(env.get(key, "")).strip()]
    assert not missing, f"Faltan variables requeridas: {missing}"

    cert_vars = ["REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"]
    for var in cert_vars:
        value = str(env.get(var, "")).strip()
        if value:
            assert Path(value).exists(), f"{var} apunta a ruta inexistente: {value}"


def test_02_initialize_smoke():
    """Prueba 2: initialize del servidor MCP local."""
    payload = _run_inspector_cli(
        method="initialize",
        output_name="mcp_initialize.json",
    )
    result = payload.get("result") or {}
    assert isinstance(result, dict), "initialize no devolvio objeto result"
    assert result.get("serverInfo"), "initialize no devolvio serverInfo"


def test_03_tools_list_strict_ok(tools_list_payload: dict):
    """Prueba 3: tools/list con --strict para gate de schema portability."""
    tools = (tools_list_payload.get("result") or {}).get("tools") or []
    assert isinstance(tools, list), "tools/list no devolvio lista de tools"
    assert tools, "tools/list devolvio lista vacia"


def test_04_no_missing_tool_descriptions(tools_list_payload: dict):
    """Prueba 4: validar que todas las tools tengan description no vacia."""
    tools = (tools_list_payload.get("result") or {}).get("tools") or []
    missing = [t.get("name", "<unknown>") for t in tools if not str(t.get("description", "")).strip()]
    assert not missing, f"Tools sin description: {missing}"


def test_05_get_user_profile_call_ok():
    """Prueba 5: tools/call get_user_profile."""
    payload = _run_inspector_cli(
        method="tools/call",
        output_name="mcp_get_user_profile.json",
        extra_args=["--tool-name", "get_user_profile", "--tool-args-json", "{}"],
    )
    assert "result" in payload, "tools/call get_user_profile no devolvio result"


def test_06_get_activities_call_ok():
    """Prueba 6: tools/call get_activities con start/limit."""
    payload = _run_inspector_cli(
        method="tools/call",
        output_name="mcp_get_activities.json",
        extra_args=[
            "--tool-name",
            "get_activities",
            "--tool-args-json",
            '{"start":0,"limit":5}',
        ],
    )
    assert "result" in payload, "tools/call get_activities no devolvio result"
