"""
agent/prompts.py
Textos para el LLM (carpeta prompts/): carga y relleno de marcadores {{clave}}. El código solo compone y aporta los datos.
"""

import re
from pathlib import Path
from typing import Any


PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


_COACHING_RULES_MARKER = "{{REGLAS_DE_COACHING}}"


def _load_coaching_rules() -> str:
    """Reglas de entrenador comunes a todas las llamadas al LLM (fuente única: prompts/coaching_rules.md)."""
    return (PROMPTS_DIR / "coaching_rules.md").read_text(encoding="utf-8").strip()


def _load_system_prompt() -> str:
    """System prompt del bucle con herramientas, con las reglas de entrenador en su sitio."""
    base = (PROMPTS_DIR / "system_prompt.md").read_text(encoding="utf-8")
    return base.replace(_COACHING_RULES_MARKER, _load_coaching_rules())


_PROMPT_MARKER = re.compile(r"\{\{([A-Za-z_]+)\}\}")


def _render_prompt(name: str, **values: Any) -> str:
    """Carga prompts/<name>.md y sustituye sus marcadores {{clave}}.

    Exige exactamente las claves del fichero: un marcador sin valor o un valor sin marcador es un error.
    El texto sale sin saltos de línea al principio ni al final; los pone quien compone el mensaje.
    """
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip("\n")
    keys = set(_PROMPT_MARKER.findall(text))
    if keys != set(values):
        raise ValueError(f"prompts/{name}.md: marcadores {sorted(keys)} ≠ valores {sorted(values)}")
    return _PROMPT_MARKER.sub(lambda m: str(values[m.group(1)]), text)
