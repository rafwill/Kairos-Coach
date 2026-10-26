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
from datetime import date, timedelta

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
# "Calent." y "enfr." abreviados en tablas (24/10).
_PROGRESSIVE = re.compile(r"(?i)calent|vuelta a la calma|enfr|progresiv")
_ZONE_SPAN = re.compile(r"(?i)\bZ([1-5])\s*(?:[–\-→]|\ba\b|hasta)\s*Z([1-5])\b")


def _progressive_range_ok(before: str, after: str, lo: int, hi: int, zones: Zones) -> bool:
    """Tramo progresivo (criterio del 23/10): misma línea, marcado como progresivo y con "de Zx a Zy";
    la FC de inicio cae en la zona más baja nombrada y la de fin en la más alta.

    La marca puede ir detrás del rango, en su misma frase o paréntesis ("Z3–Z4 (146–168 lpm, progresivo de
    Z3 a Z4)", 26/10); entonces manda el "de Zx a Zy" de detrás."""
    tail = re.split(r"[)\n;]|\. ", after, maxsplit=1)[0]
    line = before.rsplit("\n", 1)[-1][-160:]
    spans = list(_ZONE_SPAN.finditer(line))
    if _PROGRESSIVE.search(tail) and _ZONE_SPAN.search(tail):
        spans, line = list(_ZONE_SPAN.finditer(tail)), tail
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


_HR_RANGE = re.compile(r"\d{3}\s*[–\-→]\s*\d{3}")
_RUNNING_LINE = re.compile(r"(?i)\brunning\b|\bcarrera\b|\bcorrer\b|\brodaje\b|\btrail\b")
_MEASURED_RANGE = re.compile(
    r"(?i)\b(?:se estabiliz\w*|estabiliz\w*|estuvo|estuviste|osci\w*|registr\w*|media de|promedio|pico|"
    r"alcanz\w*|llegaste|se mantuvo|mantuviste)\b"
)


def _range_sport(text: str, clause_start: int, clause: str) -> str:
    """Deporte de un rango (23–24/10): Garmin tiene zonas por deporte y una bici se juzga con las de ciclismo.

    Primero la frase del rango (una fila puede llevar el rodaje y una alternativa en bici en otra frase); si no
    nombra deporte, la línea de contexto anterior más cercana que lo nombre, hasta 12 (un encabezado "Cycling
    (LTHR 153)" sobre una tabla de zonas). Las líneas con su propio rango de FC son otras viñetas o filas, no
    contexto: una viñeta de bici no convierte en bici la siguiente. Por defecto, carrera.
    """
    previous = [ln for ln in text[:clause_start].splitlines()[-12:] if not _HR_RANGE.search(ln)]
    for chunk in [clause, *reversed(previous)]:
        if _CYCLING_LINE.search(chunk):
            return "cycling"
        if _RUNNING_LINE.search(chunk):
            return "running"
    return "running"


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
        # Un rango con otra unidad no es de FC aunque haya una zona cerca ("Z1–Z2 → agua 150–250 ml", 23/10).
        other_unit = re.match(r"(?i)\s*(?:ml|mg|g\b|min|km|m\b|kcal|w\b|rpm|pasos|%)", after)
        if not is_hr or "TSS" in before or other_unit:
            continue  # rangos de FC, no TSS, glucemia, hidratación ni otras cifras
        lo, hi = sorted((int(mr.group(1)), int(mr.group(2))))
        if not (100 <= lo <= 220):
            continue
        # Frase del rango: desde el último ". ", ";" o salto de línea. No se corta en "|": en una tabla de zonas
        # el deporte está en la primera celda de la fila ("| Cycling | 153 lpm | 105–124 |", 24/10).
        clause_start = max(coaching.rfind(sep, 0, mr.start()) for sep in (". ", ";", "\n")) + 1
        clause = coaching[clause_start: mr.end()]
        # Un rango que describe lo que pasó ("tu FC se estabilizó en Z4–Z5 (159–178)") no es una prescripción.
        if _MEASURED_RANGE.search(clause):
            continue
        sport_zones = zones_cycling if zones_cycling and _range_sport(coaching, clause_start, clause) == "cycling" else zones
        fits = any(z_lo - TOL_BPM <= lo and hi <= z_hi + TOL_BPM for _, z_lo, z_hi in sport_zones)
        if not fits:
            fits = _easy_range_ok(lo, hi, sport_zones) or _progressive_range_ok(
                coaching[: mr.start()], coaching[mr.end(): mr.end() + 80], lo, hi, sport_zones)
        if not fits:
            out.append(Finding(
                "S10",
                f"LLM: rango {lo}–{hi} lpm no cabe en ninguna zona (ni en Z1–Z2 ni es un tramo progresivo de Zx a Zy)",
                f"El rango {lo}–{hi} lpm no cabe en ninguna zona. Zonas: {_zones_hint(sport_zones)}. "
                "Usa una sola zona, Z1–Z2 si es un rodaje suave, o 'de Zx a Zy' si es un tramo progresivo.",
            ))
    return out


# ─── S11: ritmo umbral vigente ──────────────────────────────────────────────

# Entre "umbral" y la cifra: si aparece una meta, la cifra no es el umbral ("el umbral (ritmo meta ≈ 3:55",
# "bajar el umbral a 3:55"; falsos positivos del 24/10).
_PACE_GOAL_WORDS = re.compile(r"(?i)\b(?:meta|objetivo|carrera|marat[oó]n|media|bajar|mejorar|llevar|al menos|necesit|predic)")


def find_wrong_threshold_pace(coaching: str, pace_today_s: float | None) -> list[Finding]:
    out: list[Finding] = []
    if not pace_today_s:
        return out
    vigente = f"{int(pace_today_s // 60)}:{int(pace_today_s % 60):02d}"
    for m in re.finditer(r"(?i)umbral([^\n]{0,40}?)(\d):(\d{2})\s*(?:–\s*\d:\d{2}\s*)?(?:min)?/km", coaching):
        if _PACE_GOAL_WORDS.search(m.group(1)):
            continue
        p = (m.group(2), m.group(3))
        sec = int(p[0]) * 60 + int(p[1])
        if abs(sec - pace_today_s) > TOL_PACE_S:
            out.append(Finding("S11", f"LLM: ritmo umbral {p[0]}:{p[1]} ≠ vigente {vigente}",
                               f"El ritmo umbral vigente es {vigente}/km, no {p[0]}:{p[1]}."))
    return out


NOTICE_HEADER = "⚠️ Comprobación de Kairos"


def without_notice(text: str) -> str:
    """El texto sin el aviso al pie de Kairos, que cita los fallos y no debe juzgarse como texto del LLM."""
    i = (text or "").find(NOTICE_HEADER)
    return text if i < 0 else text[:i]


def check_coaching_text(coaching: str, zones: Zones, zones_cycling: Zones | None, pace_today_s: float | None) -> list[Finding]:
    """S10–S12 sobre el texto del LLM, en el orden del juez."""
    coaching = without_notice(coaching)
    return (
        find_ctl_with_atl_max(coaching)
        + find_hr_ranges_outside_zones(coaching, zones, zones_cycling)
        + find_wrong_threshold_pace(coaching, pace_today_s)
    )


# ─── S14: cifras de carga citadas = las de los datos ────────────────────────

# Sigla, hasta 30 caracteres sin dígitos ni fin de frase (admite "CTL (Estado físico) 60.4") y la cifra entera;
# no cuentan porcentajes, días ni lpm.
_LOAD_FIGURE = re.compile(
    r"\b(CTL|ATL|TSB)\b([^\d\n%+\-−.]{0,30}?)([+\-−]?\d{1,3}(?:[.,]\d+)?)(?![\d.,]*\d)(?!\s*(?:%|d[ií]as\b|d\b|lpm))"
)
# Un umbral condicional ("si la ATL supera 55") no es una cifra citada.
_COMPARISON = re.compile(r"(?i)\b(?:supera|superar|baja de|bajar de|por encima de|por debajo de|m[aá]s de|menos de|hasta)\b|[<>≤≥]")
_NUMBER = re.compile(r"[+\-−]?\d+(?:[.,]\d+)?")


def _to_float(raw: str) -> float:
    return float(raw.replace("−", "-").replace(",", "."))


def find_unsupported_load_figures(text: str, reference: str) -> list[Finding]:
    """Cada CTL/ATL/TSB que cita el LLM aparece en los datos que recibió (con la tolerancia del redondeo).

    Solo las siglas: "fatiga" y "forma" se usan también como palabras corrientes.
    """
    if not reference:
        return []
    refs = [_to_float(n) for n in _NUMBER.findall(reference)]
    out: list[Finding] = []
    for m in _LOAD_FIGURE.finditer(text):
        if _COMPARISON.search(m.group(2)):
            continue
        raw = m.group(3)
        value = _to_float(raw)
        tol = 0.05 if re.search(r"[.,]", raw) else 0.5
        signed = raw[0] in "+-−"
        ok = any(abs(value - r) <= tol + 1e-9 or (not signed and abs(abs(value) - abs(r)) <= tol + 1e-9) for r in refs)
        if not ok:
            out.append(Finding("S14", f"LLM: {m.group(1)} {raw} no aparece en los datos",
                               f"La cifra {m.group(1)} {raw} no está en los datos que tienes: usa las del bloque de datos."))
    return out


# ─── S15: la sesión respeta la decisión del motor diario ────────────────────

_SOFT_DECISIONS = {"rest": "descanso", "easy": "sesión suave", "reduce": "rodaje suave sin bloques intensos"}
_HIGH_ZONE = re.compile(r"\bZ[45]\b")
_NOT_A_PRESCRIPTION = re.compile(
    r"(?i)\b(?:si|evita|evitar|no|nada de|ni|sin|mañana|manana|pr[oó]xim|semana|cuando|vigila|alerta|señal)\b"
)


def find_intensity_against_decision(text: str, decision: str | None, zones: Zones) -> list[Finding]:
    """Con decisión de descanso, suave o reducir, el LLM no prescribe hoy Z4–Z5 ni FC desde el inicio de Z4.

    Solo para respuestas sobre hoy (la decisión es del día; un reparto semanal puede tener calidad otro día).
    No cuentan las líneas condicionales, de otros días o de alertas ("si…", "evita…", "mañana…"), ni rangos
    que no son de FC (ml de hidratación, cadencia): un rango cuenta si la línea dice lpm o FC.
    """
    label = _SOFT_DECISIONS.get(str(decision or "").strip().lower())
    if not label:
        return []
    z4_low = zones[3][1] if len(zones) >= 4 else None
    for line in text.splitlines():
        if _NOT_A_PRESCRIPTION.search(line):
            continue
        is_hr_line = re.search(r"(?i)\blpm\b|\bFC\b", line)
        high_range = z4_low is not None and is_hr_line and any(
            z4_low - TOL_BPM <= int(lo) <= 220 for lo, _ in re.findall(r"(\d{3})\s*[–\-→]\s*(\d{3})", line)
        )
        if _HIGH_ZONE.search(line) or high_range:
            return [Finding("S15", f"LLM: propone intensidad alta con decisión del motor = {decision}",
                            f"El motor de Kairos ha decidido hoy {label}: no propongas Z4–Z5 ni series; "
                            "ajusta la sesión de hoy a esa decisión.")]
    return []


# ─── S25: la diferencia de HRV que se afirma = la de las cifras de la línea ─

_HRV_CLAIM = re.compile(
    r"(?i)(>\s*|m[aá]s de\s+|un\s+)?(\d{1,2}(?:[.,]\d)?)\s*%\s*(por debajo|por encima|menos|m[aá]s|inferior|superior)"
)
_HRV_MS = re.compile(r"(\d{2,3}(?:[.,]\d)?)\s*ms\b")
_HRV_BASELINE = re.compile(r"(?i)media|base|referencia|habitual|promedio")
_CONDITIONAL_LINE = re.compile(r"(?i)^\W*si\b")


def _claim_holds(dev: float, claimed: float, below: bool, at_least: bool) -> bool:
    if (dev < 0) != below and abs(dev) >= 0.5:
        return False
    return abs(dev) + 0.5 >= claimed if at_least else abs(abs(dev) - claimed) <= 1.5


def find_wrong_hrv_deviation(text: str) -> list[Finding]:
    """Una línea con la HRV y su media en ms no afirma una diferencia en % que no sale de esas cifras.

    Caso real (24/10): "anoche 58 ms (media 7 d 59 ms) → > 10 % por debajo de su media" (es −1,7 %).
    Conservador: basta con que una pareja de cifras de la línea la justifique (el orden "60 ms vs. ~67 ms de
    referencia" no siempre es medida → media). No cuentan los umbrales de alerta sin cifra medida ("si la HRV
    baja > 10 % de tu media (59 ms)…": una sola cifra).
    """
    out: list[Finding] = []
    for line in text.splitlines():
        if not re.search(r"(?i)\bhrv\b", line) or _CONDITIONAL_LINE.search(line) or not _HRV_BASELINE.search(line):
            continue
        claim = _HRV_CLAIM.search(line)
        values = [_to_float(v) for v in _HRV_MS.findall(line)]
        if not claim or len(values) < 2:
            continue
        claimed = _to_float(claim.group(2))
        below = claim.group(3).lower() in ("por debajo", "menos", "inferior")
        at_least = bool(claim.group(1)) and not claim.group(1).strip().lower().startswith("un")
        pairs = [(x, b) for i, x in enumerate(values) for j, b in enumerate(values) if i != j and b > 0]
        if any(_claim_holds(100.0 * (x - b) / b, claimed, below, at_least) for x, b in pairs):
            continue
        measured, base = values[0], values[1]
        dev = 100.0 * (measured - base) / base
        out.append(Finding("S25", f"LLM: HRV {measured:g} ms frente a {base:g} ms no es "
                                  f"{claim.group(0).strip()} (es {dev:+.1f} %)",
                           f"La HRV de {measured:g} ms frente a {base:g} ms es un {dev:+.1f} %: "
                           "corrige esa diferencia y lo que deduces de ella."))
    return out


# ─── S26: la etiqueta del TSS (rTSS / hrTSS) = la de los datos ──────────────

_TSS_LABEL_AFTER = re.compile(r"\b(hrTSS|rTSS)\b[\s:=(]{0,3}(\d{1,3}[.,]\d)")
_TSS_LABEL_BEFORE = re.compile(r"(\d{1,3}[.,]\d)\s{0,2}(hrTSS|rTSS)\b")


def _tss_labels(text: str) -> list[tuple[str, str]]:
    pairs = [(m.group(1), m.group(2).replace(",", ".")) for m in _TSS_LABEL_AFTER.finditer(text)]
    return pairs + [(m.group(2), m.group(1).replace(",", ".")) for m in _TSS_LABEL_BEFORE.finditer(text)]


def find_swapped_tss_label(text: str, reference: str) -> list[Finding]:
    """Un TSS citado como hrTSS que en los datos es rTSS (o al revés). Kairos calcula cada TSS con el modelo de
    su deporte y le pone la etiqueta (rTSS en carrera, hrTSS por FC); el LLM la conserva.

    Solo cifras con decimal y pegadas a la etiqueta en los dos textos; una cifra sin etiqueta en los datos no
    se juzga, y llamarlo "TSS" a secas no es un error.
    """
    if not reference:
        return []
    ref = _tss_labels(reference)
    out: list[Finding] = []
    for label, value in dict.fromkeys(_tss_labels(text)):
        ref_labels = {lab for lab, v in ref if v == value}
        if ref_labels and label not in ref_labels:
            right = sorted(ref_labels)[0]
            out.append(Finding("S26", f"LLM: {label} {value} en los datos es {right} {value}",
                               f"El TSS {value} es {right} en tus datos, no {label}: conserva la etiqueta que trae."))
    return out


# ─── S27: "mañana", "hoy"… con el día o la fecha que les toca ───────────────

_RELATIVE_OFFSETS = {"anteayer": -2, "ayer": -1, "hoy": 0, "mañana": 1, "pasado mañana": 2}
_WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_WEEKDAY_INDEX = {**{d: i for i, d in enumerate(_WEEKDAYS)}, "miercoles": 2, "sabado": 5}
# Palabra relativa, separador corto (espacio, coma, paréntesis, dos puntos o guion) y un día de la semana, una
# fecha DD/MM(/AAAA) o los dos. "La mañana del 26/10", "por la mañana" o "cada mañana" son la parte del día.
_RELATIVE_DAY = re.compile(
    r"(?i)(?<![\wáéíóú])(?<!la )(?<!esta )(?<!cada )(?<!una )"
    r"(pasado mañana|anteayer|mañana|hoy|ayer)[ \t]*[,(:–\-]?[ \t]*"
    r"(?:(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b[ \t,]*)?"
    r"(?:(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/]))?"
)


def _nearest_date(day: int, month: int, today: date) -> date | None:
    """DD/MM sin año: el año que deja la fecha más cerca de hoy."""
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    return min(candidates, key=lambda d: abs((d - today).days)) if candidates else None


def find_wrong_relative_day(text: str, today: date | None) -> list[Finding]:
    """Una palabra relativa ("mañana", "pasado mañana", "hoy", "ayer") va con el día de la semana o la fecha que
    le toca respecto a hoy. Caso de la auditoría del 24/10: el LLM llamaba "mañana" a pasado mañana.

    Solo se juzga si junto a la palabra hay un día o una fecha; "mañana" a secas no se puede comprobar.
    """
    if today is None:
        return []
    out: list[Finding] = []
    seen: set[str] = set()
    for m in _RELATIVE_DAY.finditer(text):
        word, weekday, dd, mm, yy = m.group(1).lower(), m.group(2), m.group(3), m.group(4), m.group(5)
        if not weekday and not dd:
            continue
        expected = today + timedelta(days=_RELATIVE_OFFSETS[word])
        wrong = []
        if weekday and _WEEKDAY_INDEX[weekday.lower()] != expected.weekday():
            wrong.append(weekday.lower())
        if dd:
            if yy:
                year = int(yy) + (2000 if len(yy) == 2 else 0)
                try:
                    cited = date(year, int(mm), int(dd))
                except ValueError:
                    cited = None
            else:
                cited = _nearest_date(int(dd), int(mm), today)
            if cited and cited != expected:
                wrong.append(f"{int(dd):02d}/{int(mm):02d}")
        if not wrong:
            continue
        quote = m.group(0).strip()
        if quote in seen:
            continue
        seen.add(quote)
        right = f"{_WEEKDAYS[expected.weekday()]} {expected.strftime('%d/%m')}"
        out.append(Finding("S27", f"LLM: \"{quote}\" no cuadra: {word} es {right}",
                           f"Hoy es {_WEEKDAYS[today.weekday()]} {today.strftime('%d/%m')}: {word} es {right}, "
                           f"no {' '.join(wrong)}. Corrige el día o la palabra (mañana, pasado mañana…)."))
    return out


# ─── S16: nota de seguridad glucémica ───────────────────────────────────────

_INSULIN_DIABETES = re.compile(r"(?i)\bdt\s?1\b|diabetes\s+tipo\s*1|\btipo\s*1\b|\blada\b|insulin")
# Prescribe una sesión: su estructura o una propuesta explícita. "Rodaje" o "series" sueltos no bastan: salen en
# listas de actividades y tablas de zonas (24/10: la nota se añadía a "¿cuántos km he corrido este mes?").
_PRESCRIBES_SESSION = re.compile(
    r"(?i)calentamiento|parte principal|enfriamiento|vuelta a la calma|te propongo|te recomiendo|\bhaz\b|"
    r"sesi[oó]n (?:de hoy|de mañana|propuesta|recomendada|clave)|qu[eé] hacer"
)
_MENTIONS_GLUCOSE = re.compile(r"(?i)gluc|insulin|hipoglu|carbohidrat|hidratos")

GLUCOSE_NOTE = (
    "> 🩸 **Seguridad glucémica:** mide la glucemia antes de empezar (zona habitual 120–180 mg/dL; por debajo "
    "de 100, toma carbohidratos rápidos antes), lleva carbohidratos si la sesión pasa de 60 min y vigila la "
    "hipoglucemia retardada después, sobre todo si entrenas por la tarde o la noche."
)


def has_insulin_diabetes(health_texts: list[str]) -> bool:
    """Diabetes tratada con insulina en el perfil (mismos marcadores que prompts/coaching_rules.md)."""
    return any(_INSULIN_DIABETES.search(str(t or "")) for t in health_texts)


def add_glucose_note(text: str, insulin_diabetes: bool) -> str:
    """S16, corrige: si prescribe una sesión sin hablar de glucemia, añade la nota fija."""
    if insulin_diabetes and _PRESCRIBES_SESSION.search(text or "") and not _MENTIONS_GLUCOSE.search(text or ""):
        return text.rstrip() + "\n\n" + GLUCOSE_NOTE
    return text


def findings_to_retry(
    text: str,
    zones: Zones,
    zones_cycling: Zones | None,
    pace_today_s: float | None,
    reference: str = "",
    decision: str | None = None,
    today: date | None = None,
) -> list[Finding]:
    """Sensores en vivo que piden reintento al LLM (docs/Harness.md §6): S10–S12, S14, S15, S25–S27 y el marcador
    vacío (G4).

    S8 (turnos inventados), S13 (fechas ISO) y S16 (nota glucémica) no están aquí porque se corrigen sin LLM.
    """
    return (
        check_coaching_text(text, zones, zones_cycling, pace_today_s)
        + find_unsupported_load_figures(text, reference)
        + find_intensity_against_decision(text, decision, zones)
        + find_wrong_hrv_deviation(text)
        + find_swapped_tss_label(text, reference)
        + find_wrong_relative_day(text, today)
        + find_empty_data_lines(text)
    )


def user_notice(finding: Finding) -> str:
    """Texto del hallazgo para el usuario, sin el prefijo interno ("LLM: ", "G4: ")."""
    return re.sub(r"^(?:LLM|G\d): ", "", finding.message)
