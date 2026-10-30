"""
agent/plan_prefs.py — lo que el atleta dice una vez y vale para siempre en sus planes (30/10, pendiente 11 c).

"Los martes no puedo" o "prefiero las series en pista", dicho al pedir, ajustar o replanificar un plan, se guarda en
el perfil antes de llamar al LLM. Un día que nunca puede pasa a descanso obligatorio (lo comprueban las reglas); una
preferencia de texto le llega al LLM en cada plan. Sin LLM; criterios fijados antes en docs/proximos_pasos.md (11 c).
Lo que es para una vez ("este martes no puedo", "mañana no puedo") no se guarda.
"""

from __future__ import annotations

import re

MAX_NOTES = 8
MAX_NOTE_CHARS = 200

_DAYS = {"lunes": 1, "martes": 2, "miércoles": 3, "miercoles": 3, "jueves": 4, "viernes": 5,
         "sábado": 6, "sabado": 6, "sábados": 6, "sabados": 6, "domingo": 7, "domingos": 7}
_DAY_WORD = "|".join(sorted(_DAYS, key=len, reverse=True))
_DAY_NAMES = {1: "lunes", 2: "martes", 3: "miércoles", 4: "jueves", 5: "viernes", 6: "sábados", 7: "domingos"}
# "no puedo", "nunca puedo", "no podré", "no tengo tiempo", "imposible", "no entreno": el día no está disponible.
_CANNOT = re.compile(r"\b(no|nunca)\s+(puedo|podré|podre|tengo\s+tiempo|entreno|voy\s+a\s+poder)\b|\bimposible\b")
_LASTING = re.compile(r"\b(nunca|ningún|ningun|siempre|a\s+partir\s+de\s+ahora|de\s+ahora\s+en\s+adelante)\b")
_ONE_OFF = re.compile(r"\b(este|esta|estos|que\s+viene|próximo|proximo|mañana|manana|hoy|pasado)\b")
# Días en plural ("los martes", "los martes y los jueves", "los martes ni jueves") o fines de semana.
_PLURAL_DAYS = re.compile(rf"\blos\s+((?:{_DAY_WORD})(?:\s*(?:,|y|ni|o)\s*(?:los\s+)?(?:{_DAY_WORD}))*)")
_ANY_DAY = re.compile(rf"\b({_DAY_WORD})\b")
_WEEKENDS = re.compile(r"\blos\s+fines\s+de\s+semana\b")
_PREFERENCE = re.compile(r"\b(prefiero|siempre|nunca|a\s+partir\s+de\s+ahora|de\s+ahora\s+en\s+adelante)\b")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.;!?\n]+", text or "") if s.strip()]


def detect(message: str) -> tuple[set[int], list[str]]:
    """(días que el atleta nunca puede entrenar, preferencias de texto que duran) de un mensaje."""
    days: set[int] = set()
    notes: list[str] = []
    for sentence in _sentences(message):
        low = sentence.lower()
        if _ONE_OFF.search(low):
            continue  # "este martes", "el martes que viene", "mañana": para una vez
        found: set[int] = set()
        if _CANNOT.search(low):
            for group in _PLURAL_DAYS.findall(low):
                found |= {_DAYS[d] for d in _ANY_DAY.findall(group)}
            if _WEEKENDS.search(low):
                found |= {6, 7}
            if _LASTING.search(low):
                found |= {_DAYS[d] for d in _ANY_DAY.findall(low)}
        if found:
            days |= found
        elif _PREFERENCE.search(low):
            notes.append(sentence[:MAX_NOTE_CHARS])
    return days, notes


def remember(profile: dict, message: str) -> list[str]:
    """Guarda en el perfil lo que dura del mensaje y devuelve qué se ha guardado ([] si nada), para decírselo."""
    days, notes = detect(message)
    goals = profile.setdefault("goals", {})
    saved: list[str] = []
    if days:
        availability = dict(goals.get("availability") or {})
        training = [int(d) for d in (availability.get("training_days") or range(1, 8))]
        rest = [int(d) for d in (availability.get("rest_days") or [])]
        new = sorted(d for d in days if d not in rest)
        if new:
            availability["training_days"] = [d for d in training if d not in days] or [d for d in range(1, 8) if d not in days]
            availability["rest_days"] = sorted(set(rest) | days)
            goals["availability"] = availability
            saved.append("Sin entrenar los " + ", ".join(_DAY_NAMES[d] for d in new) + " (descanso obligatorio).")
    if notes:
        prefs = dict(goals.get("plan_preferences") or {})
        current = [str(n) for n in (prefs.get("notes") or [])]
        added = [n for n in notes if n.lower() not in {c.lower() for c in current}]
        if added:
            prefs["notes"] = (current + added)[-MAX_NOTES:]
            goals["plan_preferences"] = prefs
            saved += [f"«{n}»" for n in added]
    return saved


def saved_markdown(saved: list[str]) -> str:
    """Encabezado de la respuesta cuando se ha guardado algo; vacío si no."""
    if not saved:
        return ""
    return ("## 📝 Lo tendré en cuenta en tus planes\n" + "\n".join(f"- {s}" for s in saved)
            + "\n- Lo repasas o lo quitas en `/plan crear`.\n\n")
