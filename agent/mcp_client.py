"""
mcp_client.py
Cliente que arranca el servidor MCP propio de Kairos como subproceso
y expone sus herramientas para que el agente las use.
"""

import os
import site
import shutil
import sys
import logging
import anyio
from contextvars import ContextVar
from datetime import UTC, datetime
from contextlib import asynccontextmanager
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from agent.mcp_adapter import (
    cache_tool_response,
    normalize_tool_invocation,
    resolve_local_fastpath_response,
    resolve_cached_tool_response,
    validate_min_input_contract,
)


log = logging.getLogger(__name__)
_MCP_TRANSPARENCY_EVENTS: ContextVar[list[dict] | None] = ContextVar(
    "mcp_transparency_events",
    default=None,
)


def reset_tool_transparency_events() -> None:
    """Resetea eventos de transparencia MCP del turno actual."""
    _MCP_TRANSPARENCY_EVENTS.set([])


def _record_tool_transparency_event(tool_name: str, mode: str, reason: str | None = None) -> None:
    """Registra que una tool se resolvió por ruta de contingencia/caché."""
    current = _MCP_TRANSPARENCY_EVENTS.get()
    events = list(current) if isinstance(current, list) else []
    events.append(
        {
            "tool": str(tool_name or "").strip(),
            "mode": str(mode or "").strip(),
            "reason": (str(reason).strip() if reason else ""),
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
    )
    _MCP_TRANSPARENCY_EVENTS.set(events)


def consume_tool_transparency_events() -> list[dict]:
    """Devuelve y limpia eventos de transparencia capturados en el turno."""
    current = _MCP_TRANSPARENCY_EVENTS.get()
    out = list(current) if isinstance(current, list) else []
    _MCP_TRANSPARENCY_EVENTS.set([])
    return out


def _iter_exception_group(exc: BaseException) -> list[BaseException]:
    """Aplana un BaseExceptionGroup para inspeccionar todas sus excepciones internas."""
    if isinstance(exc, BaseExceptionGroup):
        items: list[BaseException] = []
        for sub in exc.exceptions:
            items.extend(_iter_exception_group(sub))
        return items
    return [exc]


def _is_benign_mcp_shutdown_error(exc: BaseException) -> bool:
    """Identifica errores benignos de cierre en stdio/anyio que no deben romper salida."""
    leafs = _iter_exception_group(exc)
    if not leafs:
        return False

    allowed_types = (
        anyio.BrokenResourceError,
        anyio.ClosedResourceError,
        anyio.EndOfStream,
    )

    for err in leafs:
        if isinstance(err, allowed_types):
            continue
        msg = str(err).lower()
        if "attempted to exit cancel scope in a different task" in msg:
            continue
        return False
    return True

GARMIN_ESSENTIAL_TOOLS: tuple[str, ...] = (
    # Perfil personal del usuario
    "get_user_profile",
    # Actividades
    "get_activities",
    "get_activity",
    "get_activity_hr_in_timezones",
    "get_activities_by_date",
    "get_activities_fordate",
    "get_activity_splits",
    "get_activity_exercise_sets",
    "get_activity_power_in_timezones",
    # Salud diaria (versiones ligeras donde existen)
    "get_stats",
    "get_sleep_summary",
    "get_sleep_data",
    "get_heart_rates_summary",
    "get_stress_summary",
    "get_respiration_summary",
    "get_all_day_stress",
    "get_all_day_events",
    "get_body_battery",
    "get_rhr_day",
    "get_spo2_data",
    "get_hrv_data",
    "get_daily_steps",
    "get_hydration_data",
    # Composición corporal
    "get_body_composition",
    # Preparación y entrenamiento
    "get_training_readiness",
    "get_morning_training_readiness",
    "get_training_status",
    "get_training_load_trend",
    "get_training_effect",
    "get_hrv_trend",
    "get_vo2max_trend",
    # Rendimiento avanzado
    "get_endurance_score",
    "get_fitnessage_data",
    "get_lactate_threshold",
    "get_cycling_ftp",
    # Predicciones y récords personales
    "get_race_predictions",
    "get_personal_record",
    # Tendencias semanales
    "get_weekly_steps",
    "get_weekly_intensity_minutes",
    "get_weekly_stress",
)

KAIROS_INTERNAL_ESSENTIAL_TOOLS: tuple[str, ...] = (
    "kairos_load_trends",
    "kairos_correlate",
    "kairos_weekly_sport_breakdown",
)

ALL_ESSENTIAL_TOOLS: tuple[str, ...] = GARMIN_ESSENTIAL_TOOLS + KAIROS_INTERNAL_ESSENTIAL_TOOLS


def _resolve_command(command_name: str) -> str | None:
    """Resuelve un ejecutable por PATH o por la carpeta Scripts del Python activo."""
    found = shutil.which(command_name)
    if found:
        return found

    search_dirs: list[Path] = [Path(sys.executable).parent]

    user_base = site.getuserbase()
    if user_base:
        search_dirs.append(Path(user_base) / "Scripts")

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            py_tag = f"Python{sys.version_info.major}{sys.version_info.minor}"
            search_dirs.append(Path(appdata) / "Python" / py_tag / "Scripts")

    for scripts_dir in search_dirs:
        candidates = [scripts_dir / command_name]
        if os.name == "nt":
            candidates.append(scripts_dir / f"{command_name}.exe")
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return str(candidate)
    return None


def get_configured_mcp_backend() -> str:
    """Devuelve el backend MCP operativo (solo frozen en MCP propio)."""
    raw = (os.environ.get("MCP_BACKEND") or "frozen").strip().lower()
    if raw == "frozen":
        return "frozen"
    log.warning("MCP_BACKEND no válido (%s). Se usará backend local 'frozen'.", raw)
    return "frozen"


def _resolve_frozen_command() -> str | None:
    """Resuelve el ejecutable del MCP local propio."""
    project_root = Path(__file__).parent.parent

    override = (os.environ.get("KAIROS_MCP_FROZEN_COMMAND") or "").strip()
    if override:
        direct = Path(override)
        if direct.exists() and direct.is_file():
            return str(direct)
        resolved_override = _resolve_command(override)
        if resolved_override:
            return resolved_override

    # Wrapper local del repo para backend frozen (prioridad sobre PATH).
    local_candidates = []
    if os.name == "nt":
        local_candidates.extend(
            [
                project_root / "tools" / "garmin-mcp-frozen.cmd",
                project_root / "tools" / "garmin-mcp-frozen.bat",
            ]
        )
    else:
        local_candidates.extend(
            [
                project_root / "tools" / "garmin-mcp-frozen",
                project_root / "tools" / "garmin-mcp-frozen.sh",
            ]
        )

    for candidate in local_candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    # Convención por defecto para binario local congelado.
    return _resolve_command("garmin-mcp-frozen")


def _get_server_params(essential_only: bool = True, backend: str | None = None) -> StdioServerParameters:
    """Construye los parámetros de arranque del servidor MCP propio de Kairos."""
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")

    if not email or not password:
        raise ValueError(
            "Las variables GARMIN_EMAIL y GARMIN_PASSWORD son obligatorias. "
            "Copia .env.example a .env y rellena tus credenciales."
        )

    selected_backend = "frozen"

    # Herramientas esenciales para un agente entrenador personal.
    # Reduce el contexto de ~31k tokens (126 tools) a ~5k tokens (~40 tools).
    # Se puede sobreescribir con la variable GARMIN_ENABLED_TOOLS en .env.
    default_tools_csv = ",".join(GARMIN_ESSENTIAL_TOOLS)
    # Si essential_only=False y no hay override en .env, no se filtra (todas las herramientas)
    if essential_only:
        enabled_tools = os.environ.get("GARMIN_ENABLED_TOOLS", default_tools_csv)
    else:
        enabled_tools = os.environ.get("GARMIN_ENABLED_TOOLS", "")

    frozen_cmd = _resolve_frozen_command()
    if not frozen_cmd:
        raise RuntimeError(
            "No se encontró el launcher local del MCP propio.\n"
            "Define KAIROS_MCP_FROZEN_COMMAND o verifica tools/garmin-mcp-frozen.*"
        )
    command = frozen_cmd
    args = []

    # Certificado SSL de Zscaler — necesario en redes con proxy SSL corporativo.
    # Se exporta automáticamente desde el almacén de Windows con:
    #   Get-ChildItem Cert:\LocalMachine\Root | Where Subject -match Zscaler
    _project_root = Path(__file__).parent.parent
    _zscaler_pem = _project_root / "zscaler-ca.pem"
    ssl_overrides = {}
    if _zscaler_pem.exists():
        _pem_path = str(_zscaler_pem)
        ssl_overrides = {
            "REQUESTS_CA_BUNDLE": _pem_path,
            "CURL_CA_BUNDLE": _pem_path,
            "SSL_CERT_FILE": _pem_path,
        }

    return StdioServerParameters(
        command=command,
        args=args,
        env={
            **os.environ,
            "GARMIN_EMAIL": email,
            "GARMIN_PASSWORD": password,
            "KAIROS_MCP_BACKEND_EFFECTIVE": selected_backend,
            **({"GARMIN_ENABLED_TOOLS": enabled_tools} if enabled_tools else {}),
            **ssl_overrides,
        },
    )


@asynccontextmanager
async def garmin_mcp_session(essential_only: bool = True):
    """
    Context manager que inicia el servidor MCP propio y devuelve
    una sesión lista para llamar herramientas.

    Uso:
        async with garmin_mcp_session() as session:
            result = await session.call_tool("get_last_activity", {})
    """
    async def _open_and_yield(params: StdioServerParameters):
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        except BaseException as exc:
            if _is_benign_mcp_shutdown_error(exc):
                log.debug("Ignorando error benigno al cerrar sesión MCP: %s", exc)
                return
            raise

    params = _get_server_params(essential_only=essential_only, backend="frozen")
    os.environ["KAIROS_MCP_BACKEND_EFFECTIVE"] = "frozen"
    async for _session in _open_and_yield(params):
        yield _session


async def list_available_tools(session: ClientSession) -> list[dict]:
    """Devuelve la lista de herramientas disponibles en el MCP."""
    tools_response = await session.list_tools()
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema,
        }
        for tool in tools_response.tools
    ]


async def call_tool(session: ClientSession, tool_name: str, arguments: dict) -> str:
    """
    Llama a una herramienta del MCP y devuelve el resultado como string.
    Maneja errores y los devuelve de forma legible al agente.
    """
    try:
        normalized_tool_name, normalized_args = normalize_tool_invocation(tool_name, arguments)
        contract_error = validate_min_input_contract(normalized_tool_name, normalized_args)
        if contract_error:
            return contract_error

        backend_effective = (os.environ.get("KAIROS_MCP_BACKEND_EFFECTIVE") or "").strip().lower()
        local_fastpath = resolve_local_fastpath_response(
            normalized_tool_name,
            normalized_args,
            backend_effective=backend_effective,
        )
        if local_fastpath is not None:
            _record_tool_transparency_event(
                normalized_tool_name,
                mode="fallback_fastpath",
                reason="resolved via local fastpath/cache in frozen backend",
            )
            cache_tool_response(normalized_tool_name, normalized_args, local_fastpath)
            return local_fastpath

        result = await session.call_tool(normalized_tool_name, normalized_args)
        # El contenido puede ser texto o JSON estructurado
        if result.content:
            parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
            response_text = "\n".join(parts)
            cache_tool_response(normalized_tool_name, normalized_args, response_text)
            return response_text
        return "Sin datos disponibles."
    except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, KeyError) as e:
        cached = resolve_cached_tool_response(
            normalized_tool_name if 'normalized_tool_name' in locals() else tool_name,
            normalized_args if 'normalized_args' in locals() else arguments,
            backend_effective=(os.environ.get("KAIROS_MCP_BACKEND_EFFECTIVE") or "").strip().lower(),
        )
        if cached is not None:
            _record_tool_transparency_event(
                normalized_tool_name if 'normalized_tool_name' in locals() else tool_name,
                mode="fallback_cache_on_error",
                reason=f"{type(e).__name__}: {e}",
            )
            return cached
        return f"Error al llamar a '{tool_name}': {str(e)}"
