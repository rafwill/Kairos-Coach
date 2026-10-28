"""
agent/llm.py
Cliente del proveedor de IA del atleta (cualquiera compatible con OpenAI), clave rechazada y tiempos máximos de las llamadas al LLM y a las tools.
"""

import os
import asyncio
import re
from typing import Any

from agent import ia_provider as _ia_provider


def _llm_timeout_seconds() -> float:
    """Timeout duro de llamadas LLM para evitar bloqueos prolongados."""
    raw = str(os.environ.get("KAIROS_LLM_TIMEOUT_SECONDS", "300")).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 25.0
    return max(5.0, min(300.0, value))


def _tool_timeout_seconds() -> float:
    """Timeout duro de tool-calls en el loop LLM para limitar latencia."""
    raw = str(os.environ.get("KAIROS_TOOL_TIMEOUT_SECONDS", "8")).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 8.0
    return max(2.0, min(60.0, value))


class LLMAuthError(RuntimeError):
    """El proveedor rechaza la clave (caducada o no válida): la CLI la pide de nuevo y la guarda en .env (26/10)."""


# 401 o mensaje de clave no válida. Un 403 a secas no cuenta: en la red con proxy es el proxy (Zscaler), no la
# clave. NVIDIA, en cambio, rechaza una clave mala con 403 "Authorization failed" (comprobado en vivo el 26/10).
_LLM_AUTH_ERROR = re.compile(
    r"(?i)\b401\b|unauthori[sz]ed|invalid[ _-]?api[ _-]?key|incorrect api key|api key not valid|api_key_invalid|"
    r"authentication|authori[sz]ation failed"
)


def _is_llm_auth_error(exc: BaseException) -> bool:
    return getattr(exc, "status_code", None) == 401 or bool(_LLM_AUTH_ERROR.search(str(exc)))


def _build_llm_client(config: dict) -> tuple[Any, str, str]:
    """Cliente, modelo y clave del proveedor del atleta (26/10): cualquier API compatible con OpenAI.

    `config` = fila de ia_provider (provider, base_url, model); la clave sale del .env (`PROVIDER_API_KEY`).
    `KAIROS_MODEL` cambia el modelo solo para esta ejecución (batería con --modelo).
    """
    from openai import AsyncOpenAI  # aquí y no arriba: importarlo cuesta ≈ 0,9 s y main lo precarga en un hilo (8c)

    api_key = os.environ[_ia_provider.KEY_VAR]
    client = AsyncOpenAI(base_url=config["base_url"], api_key=api_key)
    return client, os.environ.get("KAIROS_MODEL") or config["model"], api_key


async def probe_llm_key(config: dict) -> tuple[str, str]:
    """Prueba la clave con una llamada mínima (1 token): ("ok", ""), ("auth", motivo) o ("error", motivo).

    "error" es todo lo que no es la clave (red, proxy, URL o modelo): no se puede saber si la clave vale.
    """
    try:
        client, model, _ = _build_llm_client(config)
        await asyncio.wait_for(
            client.chat.completions.create(model=model, messages=[{"role": "user", "content": "ok"}], max_tokens=1),
            timeout=30,
        )
        return "ok", ""
    except Exception as exc:  # cualquier fallo del proveedor se clasifica, nunca se propaga
        return ("auth" if _is_llm_auth_error(exc) else "error"), str(exc)[:200]
