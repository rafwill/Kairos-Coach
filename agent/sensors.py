"""
agent/sensors.py — sensores computacionales sobre el texto que escribe el LLM (docs/Harness.md, §5).

Funciones puras: reciben el texto y lo que necesitan del perfil (zonas, ritmo umbral) y devuelven
hallazgos. Los usan el juez de la batería (tools/juez_bateria.py) y Kairos en vivo, con una sola
definición de cada regla. Cada hallazgo lleva el mensaje del informe y una pista escrita para que el
LLM pueda corregirse con ella.

No importa nada del resto de `agent/`: así lo pueden usar el agente y las herramientas sin ciclos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

Zones = list[tuple[str, int, int]]  # (nombre, lpm mínimo, lpm máximo), de Z1 a Z5

TOL_BPM = 1      # rangos de FC
TOL_PACE_S = 2   # ritmos


@dataclass(frozen=True)
class Finding:
    sensor: str        # identificador de docs/Harness.md (S8, S10…) o del juez (G3, G4…)
    message: str       # para el informe del juez y el log
    hint: str = ""     # para el LLM, si se le pide que corrija


# ─── S8 / G5: turnos de usuario inventados ──────────────────────────────────

_SIMULATED_ROLE_LINE_RE = re.compile(
    r"^\s*(?:\*\*|__)?(?:usuario|user|tú)\s*(?::\s*(?:\*\*|__)?|(?:\*\*|__)\s*:)",
    re.IGNORECASE | re.MULTILINE,
)
_SIMULATED_ROLE_TOKEN_RE = re.compile(r"<\|im_start\|>\s*user|<\|user\|>", re.IGNORECASE)
_FIRST_PERSON_REQUEST_RE = re.compile(
    r"\b(?:elijo|prefiero|quiero que me|me gustar[ií]a que me|planif[ií]came|prep[aá]rame|cr[eé]ame|"
    r"s[ií],? (?:quiero|hazlo|por favor|adelante))\b",
    re.IGNORECASE,
)


def strip_simulated_user_turn(text: str) -> str:
    """Quita un turno del usuario que el LLM haya inventado al final de su respuesta (S8, corrige).

    Casos: una línea con rol explícito ("Usuario:", "<|im_start|>user"…) o una cola
    en primera persona pegada tras la última pregunta de cierre ("…?**elijo entrenar…").
    """
    if not text:
        return text
    cut = len(text)
    for rx in (_SIMULATED_ROLE_LINE_RE, _SIMULATED_ROLE_TOKEN_RE):
        m = rx.search(text)
        if m:
            cut = min(cut, m.start())
    text = text[:cut].rstrip()
    q = text.rfind("?")
    if q >= 0:
        head_end = q + 1
        while head_end < len(text) and text[head_end] in "*_":
            head_end += 1
        tail = text[head_end:].strip()
        if (
            tail
            and "?" not in tail
            and not re.search(r"^\s*(?:#|[-*•]\s|\d+[.)]\s)", tail, re.MULTILINE)
            and _FIRST_PERSON_REQUEST_RE.search(tail)
        ):
            text = text[:head_end].rstrip()
    return text


def find_invented_user_turn(body: str, coaching: str = "") -> list[Finding]:
    """G5: rol de usuario en cualquier parte de la respuesta y cola en primera persona en el coaching."""
    out: list[Finding] = []
    if re.search(r"(?mi)^\s*(\*\*)?(usuario|user|tú)\s*:", body):
        out.append(Finding("G5", "G5: turno de usuario inventado (rol)",
                           "No escribas turnos del usuario: termina en tu propia respuesta."))
    if coaching and strip_simulated_user_turn(coaching.strip()) != coaching.strip():
        out.append(Finding("G5", "G5: turno de usuario inventado (cola en primera persona)",
                           "Has añadido al final una frase como si la dijera el usuario; quítala."))
    return out


# ─── S13 / G3: fechas ISO ───────────────────────────────────────────────────

_ISO_DATE = re.compile(r"(?<![\d/])(20\d{2})-(\d{2})-(\d{2})(?![\d:T])")


def fix_iso_dates(text: str) -> str:
    """S13, corrige: AAAA-MM-DD → DD/MM/AAAA en el texto que ve el usuario."""
    return _ISO_DATE.sub(lambda m: f"{m.group(3)}/{m.group(2)}/{m.group(1)}", text or "")


def find_iso_dates(text: str) -> list[Finding]:
    """Fechas AAAA-MM-DD visibles para el usuario (deben ir en DD/MM/AAAA)."""
    iso = re.findall(r"(?<![\d/])20\d{2}-\d{2}-\d{2}(?![\d:T])", text)
    if not iso:
        return []
    shown = sorted(set(iso))[:3]
    return [Finding("G3", f"G3: fechas ISO visibles {shown}",
                    f"Escribe estas fechas en DD/MM/AAAA: {', '.join(shown)}.")]


# ─── G4: marcador "datos disponibles" sin valor ─────────────────────────────

_HONEST_NO_DATA = re.compile(r"(?i)\b(?:sin|no hay|no tengo|ning[uú]n)\b[^.\n]{0,15}datos disponibles")


def find_empty_data_lines(text: str) -> list[Finding]:
    """Líneas que dicen "datos disponibles" sin ninguna cifra (un valor que no llegó a mostrarse).

    Decir que falta el dato ("Sin datos disponibles para esta fecha") es lo correcto y no cuenta (23/10).
    """
    empties = [
        ln.strip() for ln in text.splitlines()
        if "datos disponibles" in ln and not re.search(r"\d", ln) and not _HONEST_NO_DATA.search(ln)
    ]
    if not empties:
        return []
    return [Finding("G4", f"G4: texto vacío ({empties[0][:60]})",
                    "Da el valor de cada métrica o di explícitamente que no hay dato.")]


# ─── S12: CTL con el máximo habitual de la ATL ──────────────────────────────

def find_ctl_with_atl_max(coaching: str) -> list[Finding]:
    out: list[Finding] = []
    for mm in re.finditer(r"CTL", coaching):
        window = coaching[mm.end(): mm.end() + 90]
        cut = re.search(r"ATL|Fatiga|\n", window)
        window = window[: cut.start()] if cut else window
        if re.search(r"m[áa]x(imo)?\.? habitual", window):
            out.append(Finding("S12", "LLM atribuye a la CTL el máximo habitual de la ATL",
                               "El máximo habitual es de la Fatiga (ATL), no del Estado físico (CTL)."))
    return out


# ─── S10: rangos de FC dentro de las zonas de Garmin ────────────────────────

_CYCLING_LINE = re.compile(r"(?i)ciclis|\bbici|cycling|rodillo")
_PROGRESSIVE = re.compile(r"(?i)calentamiento|vuelta a la calma|enfriamiento|progresiv")
_ZONE_SPAN = re.compile(r"(?i)\bZ([1-5])\s*(?:[–\-→]|\ba\b|hasta)\s*Z([1-5])\b")


def _progressive_range_ok(before: str, lo: int, hi: int, zones: Zones) -> bool:
    """Tramo progresivo (criterio del 23/10): misma línea, marcado como progresivo y con "de Zx a Zy";
    la FC de inicio cae en la zona más baja nombrada y la de fin en la más alta."""
    line = before.rsplit("\n", 1)[-1][-160:]
    spans = list(_ZONE_SPAN.finditer(line))
    if not _PROGRESSIVE.search(line) or not spans:
        return False
    za, zb = sorted((int(spans[-1].group(1)), int(spans[-1].group(2))))
    if za == zb or zb > len(zones):
        return False
    _, a_lo, a_hi = zones[za - 1]
    _, b_lo, b_hi = zones[zb - 1]
    return a_lo - TOL_BPM <= lo <= a_hi + TOL_BPM and b_lo - TOL_BPM <= hi <= b_hi + TOL_BPM


def _easy_range_ok(lo: int, hi: int, zones: Zones) -> bool:
    """Rodaje suave en Z1–Z2 (criterio del 23/10): las dos zonas están bajo VT1 y dan el mismo estímulo;
    lo que importa es no pasar del techo de Z2. De Z3 para arriba sigue mandando una sola zona."""
    if len(zones) < 2:
        return False
    return zones[0][1] - TOL_BPM <= lo and hi <= zones[1][2] + TOL_BPM


def _zones_hint(zones: Zones) -> str:
    return ", ".join(f"{name.split()[0]} {lo}–{hi}" for name, lo, hi in zones)


def find_hr_ranges_outside_zones(coaching: str, zones: Zones, zones_cycling: Zones | None = None) -> list[Finding]:
    """Cada rango de FC cabe en una zona de Garmin del deporte de su línea (bici → ciclismo).

    Se aceptan además un rango dentro de Z1–Z2 (rodaje suave) y un tramo progresivo "de Zx a Zy".
    """
    out: list[Finding] = []
    if not zones:
        return out
    for mr in re.finditer(r"(\d{3})\s*[–\-→]\s*(\d{3})", coaching):
        before = coaching[max(0, mr.start() - 25): mr.start()]
        after = coaching[mr.end(): mr.end() + 8]
        # \bVO2: un "vo" suelto casaba con "objetivo" ("objetivo 120–180 mg/dL" de glucemia).
        is_hr = "lpm" in after or re.search(r"(?i)(Z[1-5]|FC|lpm|recuper|resistencia|tempo|umbral|\bVO2)", before)
        if not is_hr or "TSS" in before or re.match(r"\s*mg", after):
            continue  # rangos de FC, no TSS, glucemia ni otras cifras
        lo, hi = sorted((int(mr.group(1)), int(mr.group(2))))
        if not (100 <= lo <= 220):
            continue
        # Garmin tiene zonas por deporte: una línea de bici se juzga con las de ciclismo (23/10).
        line_start = coaching.rfind("\n", 0, mr.start()) + 1
        line_end = coaching.find("\n", mr.end())
        line = coaching[line_start: line_end if line_end != -1 else len(coaching)]
        sport_zones = zones_cycling if zones_cycling and _CYCLING_LINE.search(line) else zones
        fits = any(z_lo - TOL_BPM <= lo and hi <= z_hi + TOL_BPM for _, z_lo, z_hi in sport_zones)
        if not fits:
            fits = _easy_range_ok(lo, hi, sport_zones) or _progressive_range_ok(coaching[: mr.start()], lo, hi, sport_zones)
        if not fits:
            out.append(Finding(
                "S10",
                f"LLM: rango {lo}–{hi} lpm no cabe en ninguna zona (ni en Z1–Z2 ni es un tramo progresivo de Zx a Zy)",
                f"El rango {lo}–{hi} lpm no cabe en ninguna zona. Zonas: {_zones_hint(sport_zones)}. "
                "Usa una sola zona, Z1–Z2 si es un rodaje suave, o 'de Zx a Zy' si es un tramo progresivo.",
            ))
    return out


# ─── S11: ritmo umbral vigente ──────────────────────────────────────────────

def find_wrong_threshold_pace(coaching: str, pace_today_s: float | None) -> list[Finding]:
    out: list[Finding] = []
    if not pace_today_s:
        return out
    vigente = f"{int(pace_today_s // 60)}:{int(pace_today_s % 60):02d}"
    for p in re.findall(r"(?i)umbral[^\n]{0,40}?(\d):(\d{2})\s*(?:–\s*\d:\d{2}\s*)?(?:min)?/km", coaching):
        sec = int(p[0]) * 60 + int(p[1])
        if abs(sec - pace_today_s) > TOL_PACE_S:
            out.append(Finding("S11", f"LLM: ritmo umbral {p[0]}:{p[1]} ≠ vigente {vigente}",
                               f"El ritmo umbral vigente es {vigente}/km, no {p[0]}:{p[1]}."))
    return out


def check_coaching_text(coaching: str, zones: Zones, zones_cycling: Zones | None, pace_today_s: float | None) -> list[Finding]:
    """S10–S12 sobre el texto del LLM, en el orden del juez."""
    return (
        find_ctl_with_atl_max(coaching)
        + find_hr_ranges_outside_zones(coaching, zones, zones_cycling)
        + find_wrong_threshold_pace(coaching, pace_today_s)
    )


def findings_to_retry(text: str, zones: Zones, zones_cycling: Zones | None, pace_today_s: float | None) -> list[Finding]:
    """Sensores en vivo que piden reintento al LLM (docs/Harness.md §6): S10–S12 y el marcador vacío (G4).

    S8 (turnos inventados) y S13 (fechas ISO) no están aquí porque se corrigen sin LLM.
    """
    return check_coaching_text(text, zones, zones_cycling, pace_today_s) + find_empty_data_lines(text)


def user_notice(finding: Finding) -> str:
    """Texto del hallazgo para el usuario, sin el prefijo interno ("LLM: ", "G4: ")."""
    return re.sub(r"^(?:LLM|G\d): ", "", finding.message)
