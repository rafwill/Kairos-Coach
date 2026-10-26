"""
agent/diabetes.py — diabetes del atleta y qué protocolo recibe el LLM (26/10).

La diabetes sale de lo que el atleta escribe como enfermedades o patologías (`health.pathologies` y `notes`):
si menciona diabetes tipo 1 o tipo 2, se le pregunta su medicación (`health.diabetes_medication`, lista de
claves de MEDICATIONS o texto libre). Sin patologías es un atleta sano: ni preguntas ni protocolo. Otra
patología la interpreta el LLM con lo que diga el perfil.

Protocolo (prompts/diabetes/): "insulina" si hay riesgo de hipoglucemia (DT1, insulina, sulfonilureas o
glinidas); "sin_insulina" para el resto de diabetes; ninguno sin diabetes.
"""

from __future__ import annotations

import re

TYPES = {"dt1": "Diabetes tipo 1", "dt2": "Diabetes tipo 2", "otra": "Diabetes"}
MEDICATIONS = {
    "insulina_basal": "Insulina lenta (basal)",
    "insulina_rapida": "Insulina rápida (bolos)",
    "bomba_insulina": "Bomba de insulina",
    "metformina": "Metformina",
    "sulfonilureas": "Sulfonilureas (gliclazida, glimepirida…)",
    "glinidas": "Glinidas (repaglinida)",
    "isglt2": "iSGLT2 (empagliflozina, dapagliflozina…)",
    "aglp1": "aGLP-1 (semaglutida, liraglutida…)",
    "idpp4": "iDPP-4 (sitagliptina…)",
}
_HYPO_RISK = {"insulina_basal", "insulina_rapida", "bomba_insulina", "sulfonilureas", "glinidas"}

_DT1 = re.compile(r"(?i)\bdt\s?1\b|\bdm\s?1\b|diabetes\s+(?:tipo\s*)?(?:1|i)\b|\btipo\s*(?:1|i)\b")
_DT2 = re.compile(r"(?i)\bdt\s?2\b|\bdm\s?2\b|diabetes\s+(?:tipo\s*)?(?:2|ii)\b|\btipo\s*(?:2|ii)\b")
_DIABETES = re.compile(r"(?i)(?<!pre)diab[eé]t|\blada\b")
_INSULIN = re.compile(r"(?i)insulin")


def _health_text(health: dict | None) -> str:
    # Lesiones también: los perfiles anteriores a la separación tenían las patologías ahí.
    health = health or {}
    items = [*(health.get("pathologies") or []), *(health.get("injuries") or [])]
    return " ".join([*(str(x) for x in items), str(health.get("notes") or "")])


def is_diabetes(item: str) -> bool:
    """Una entrada de la lista menciona diabetes (para separar patologías de lesiones en perfiles antiguos)."""
    return detect_type({"injuries": [item]}) is not None


def detect_type(health: dict | None) -> str | None:
    """"dt1", "dt2" u "otra" si las patologías del perfil mencionan diabetes; None si no."""
    text = _health_text(health)
    if _DT1.search(text):
        return "dt1"
    if _DT2.search(text):
        return "dt2"
    return "otra" if _DIABETES.search(text) else None


def medication(health: dict | None) -> list[str] | None:
    """Medicación declarada (claves o texto libre); [] = ninguna (solo dieta); None = aún no preguntada."""
    meds = (health or {}).get("diabetes_medication")
    return [str(m) for m in meds if str(m).strip()] if isinstance(meds, list) else None


def protocol(health: dict | None) -> str | None:
    """Protocolo que recibe el LLM: "insulina", "sin_insulina" o None (sin diabetes)."""
    tipo = detect_type(health)
    if tipo is None:
        return None
    meds = medication(health)
    if tipo == "dt1":
        return "insulina"
    if meds is None:
        # Medicación sin preguntar: la insulina escrita en el perfil basta para el protocolo con riesgo.
        return "insulina" if _INSULIN.search(_health_text(health)) else "sin_insulina"
    risky = any(m in _HYPO_RISK or _INSULIN.search(m) for m in meds)
    return "insulina" if risky else "sin_insulina"


def label(health: dict | None) -> str:
    """"Diabetes tipo 1 · Insulina lenta (basal), Metformina"; "" sin diabetes."""
    tipo = detect_type(health)
    if tipo is None:
        return ""
    meds = medication(health)
    if meds is None:
        return f"{TYPES[tipo]} · medicación sin indicar"
    return f"{TYPES[tipo]} · " + (", ".join(MEDICATIONS.get(m, m) for m in meds) or "sin medicación (solo dieta)")
