"""
agent/ia_provider.py — el proveedor de IA del atleta (26/10).

Cada atleta dice cuál es su proveedor; Kairos no ofrece un menú cerrado. Si lo conoce (KNOWN), rellena la URL
de su API compatible con OpenAI y propone un modelo; si no, el atleta da la URL y el modelo. La configuración
(proveedor, URL, modelo y modelo del chat) y el consumo diario de tokens van en la tabla `ia_provider`; la clave,
siempre en el .env del ordenador, en `PROVIDER_API_KEY` (con un comentario que dice de qué proveedor es).

Dos modelos (30/10): `model` crea los planes y escribe la recomendación del arranque; `chat_model` (vacío = el
mismo) responde en el chat y releva a `model` si este da 429 o 503.
"""

from __future__ import annotations

import re

# nombre normalizado → (nombre visible, URL compatible con OpenAI, modelo propuesto, dónde conseguir la clave)
KNOWN: dict[str, tuple[str, str, str, str]] = {
    "nvidia": ("NVIDIA NIM", "https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-ultra-550b-a55b",
               "https://build.nvidia.com"),
    "openai": ("OpenAI", "https://api.openai.com/v1", "gpt-4.1-mini", "https://platform.openai.com/api-keys"),
    "gemini": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-3.6-flash",
               "https://aistudio.google.com"),
    "anthropic": ("Anthropic", "https://api.anthropic.com/v1/", "claude-sonnet-5-5", "https://console.anthropic.com"),
    "groq": ("Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "https://console.groq.com"),
    "mistral": ("Mistral", "https://api.mistral.ai/v1", "mistral-small-latest", "https://console.mistral.ai"),
    "cerebras": ("Cerebras", "https://api.cerebras.ai/v1", "llama-3.3-70b", "https://cloud.cerebras.ai"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", "openrouter/auto", "https://openrouter.ai/keys"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat", "https://platform.deepseek.com"),
}
# Modelo del chat que se propone (30/10, decisión del usuario): el principal queda para los planes y el arranque,
# y el del chat lo releva si falla. Gemini gratuito: Flash da 20 peticiones al día y Flash Lite, 500.
CHAT_MODEL: dict[str, str] = {"gemini": "gemini-3.5-flash-lite"}
_ALIASES = {
    "nvidia nim": "nvidia", "nim": "nvidia", "chatgpt": "openai", "gpt": "openai", "google": "gemini",
    "google gemini": "gemini", "google ai studio": "gemini", "claude": "anthropic", "mistral ai": "mistral",
}


def slug(name: str) -> str:
    """Nombre del proveedor normalizado: minúsculas, letras y números ("Mi Proveedor" → "mi_proveedor")."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def known(name: str) -> str | None:
    """Clave de KNOWN si Kairos conoce el proveedor por su nombre o un alias; None si no."""
    raw = (name or "").strip().lower()
    s = _ALIASES.get(raw) or slug(raw)
    return s if s in KNOWN else None


# Variable del .env con la clave, la misma para cualquier proveedor (decisión del usuario, 26/10); un comentario
# encima dice de qué proveedor es.
KEY_VAR = "PROVIDER_API_KEY"


def label(config: dict) -> str:
    """"NVIDIA NIM (nvidia/nemotron-…)" para mostrar; un proveedor desconocido, con el nombre que dio el atleta.

    Con modelo del chat distinto: "Google Gemini (gemini-3.6-flash; chat: gemini-3.5-flash-lite)".
    """
    provider = str(config.get("provider") or "")
    k = known(provider)
    chat = config.get("chat_model")
    models = f"{config.get('model')}; chat: {chat}" if chat and chat != config.get("model") else config.get("model")
    return f"{KNOWN[k][0] if k else provider} ({models})"


def is_valid(config: dict | None) -> bool:
    return bool(config) and all(str(config.get(k) or "").strip() for k in ("provider", "base_url", "model"))
