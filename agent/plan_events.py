"""
agent/plan_events.py
Eventos fijos del atleta en su plan (31/10, petición del usuario): un viaje, unas vacaciones o una salida en grupo que
el atleta cuenta a Kairos. El LLM del plan los respeta y reparte el resto de la semana alrededor; el código los
guarda en el perfil (`goals.plan_events`), los valida y comprueba que cada semana los cumple.

Dos tipos:
- "session": una sesión obligatoria un día concreto (salida en grupo, carrera de club…): deporte, duración e
  intensidad. El día lleva esa sesión (misma duración ±10 %), marcada con "event": "<id>".
- "travel": días con limitación (viaje): sin entrenar o con un máximo de minutos por día, y el material que tiene.

Sin dependencias del chat ni de la red: la extracción con el LLM está en agent/plan_llm.py.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from agent import run_plan as rp

KINDS = {"session": "salida o sesión fija", "travel": "viaje o días con limitación"}
SPORTS = {"running": "carrera", "trail": "trail", "cycling": "bici de carretera", "gravel": "gravel", "mtb": "MTB",
          "hiking": "marcha / senderismo", "rowing": "remo", "elliptical": "elíptica", "swimming": "natación",
          "other": "otro deporte"}
INTENSITIES = {"easy": "suave", "moderate": "moderada", "hard": "fuerte"}
RUNNING_SPORTS = {"running", "trail"}
RUNNING_TYPES = {"running_z2", "long_run", "running_quality", "recovery", "race_run"}
MAX_EVENT_MINUTES = 1440
_MAX_TEXT = 120


def _iso(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _int(value) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


_DATE_TOKEN = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?")


def parse_dates(text: str, today: date) -> tuple[str, str] | None:
    """Fecha o rango escritos a mano (`/plan evento`): "15/11", "2026-11-15", "20/11-23/11", "20/11 al 23/11".

    Sin año, el de hoy, o el siguiente si esa fecha ya ha pasado. None si no entiendo alguna.
    """
    tokens = _DATE_TOKEN.findall(str(text or ""))
    if not 1 <= len(tokens) <= 2:
        return None
    days: list[date] = []
    for tok in tokens:
        try:
            if "-" in tok:
                day = date.fromisoformat(tok)
            else:
                parts = [int(p) for p in tok.split("/")]
                year = parts[2] + (2000 if parts[2] < 100 else 0) if len(parts) == 3 else None
                floor = days[0] if days else today
                day = date(year or floor.year, parts[1], parts[0])
                if year is None and day < floor:
                    day = date(day.year + 1, parts[1], parts[0])
        except ValueError:
            return None
        days.append(day)
    start, end = days[0], days[-1]
    return (start.isoformat(), end.isoformat()) if end >= start else None


def parse_minutes(text: str) -> int | None:
    """Duración escrita a mano: "90", "90 min", "2h", "2h30", "1:30"."""
    raw = str(text or "").strip().lower().replace(",", ".")
    m = re.fullmatch(r"(\d+)\s*(?:h|horas?)\s*(\d{1,2})?\s*(?:min)?", raw) or re.fullmatch(r"(\d+):(\d{2})", raw)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2) or 0)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:min|m|minutos)?", raw)
    return int(round(float(m.group(1)))) if m else None


def normalize(raw: dict, today: date) -> tuple[dict | None, list[str], list[str]]:
    """Un evento tal como lo entiende el LLM → (evento, fallos, preguntas).

    Con fallos (fecha imposible, pasada…) o preguntas (falta un dato que no se inventa) no hay evento: Kairos dice qué
    pasa y no guarda nada hasta tenerlo.
    """
    errors: list[str] = []
    questions: list[str] = []
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return None, [f"Tipo de evento no válido: «{kind}»."], []
    start = _iso(raw.get("start"))
    end = _iso(raw.get("end") or raw.get("start"))
    if start is None:
        questions.append("¿Qué día es? (o desde qué día y hasta cuál)")
    elif end is None or end < start:
        errors.append("La fecha de fin no es válida.")
    elif end < today:
        errors.append(f"El {end.strftime('%d/%m/%Y')} ya ha pasado.")
    title = str(raw.get("title") or "").strip()[:80]
    event: dict = {"kind": kind, "start": start.isoformat() if start else "", "end": end.isoformat() if end else "",
                   "title": title}
    if kind == "session":
        if start and end and end != start:
            errors.append("Una salida o sesión es de un día; si son varios días, cuéntame cada uno.")
        sport = str(raw.get("sport") or "").strip().lower()
        if sport not in SPORTS:
            questions.append("¿Qué deporte es? (carrera, trail, bici, gravel, MTB, marcha, remo, elíptica, natación u otro)")
        minutes = _int(raw.get("duration_min"))
        if minutes is None or minutes <= 0:
            questions.append("¿Cuánto durará?")
        elif minutes > MAX_EVENT_MINUTES:
            errors.append("Una sesión no puede durar más de un día.")
        intensity = str(raw.get("intensity") or "").strip().lower()
        if intensity not in INTENSITIES:
            questions.append("¿A qué intensidad? (suave, moderada o fuerte)")
        event.update({"sport": sport, "duration_min": minutes or 0, "intensity": intensity})
        event["title"] = title or f"Salida ({SPORTS.get(sport, 'sesión')})"
    else:
        cap = _int(raw.get("max_minutes"))
        if cap is None or cap < 0:
            questions.append("¿Podrás entrenar algo esos días? ¿Cuántos minutos como mucho al día (0 si nada)?")
        event["max_minutes"] = cap if cap is not None and cap >= 0 else 0
        event["equipment"] = str(raw.get("equipment") or "").strip()[:_MAX_TEXT]
        event["title"] = title or "Viaje"
    if errors or questions:
        return None, errors, questions
    return event, [], []


# ─── Guardado en el perfil ────────────────────────────────────────────────────

def load(profile: dict) -> list[dict]:
    return [dict(e) for e in ((profile.get("goals") or {}).get("plan_events") or []) if isinstance(e, dict)]


def save(profile: dict, events: list[dict]) -> None:
    profile.setdefault("goals", {})["plan_events"] = sorted(events, key=lambda e: (e["start"], e["id"]))


def prune(events: list[dict], today: date) -> list[dict]:
    """Los eventos que ya han pasado se van solos."""
    return [e for e in events if (_iso(e.get("end")) or today) >= today]


def add(profile: dict, event: dict, today: date) -> dict:
    """Guarda el evento con su id (e1, e2…); devuelve el guardado. Los pasados se limpian."""
    events = prune(load(profile), today)
    used = {int(e["id"][1:]) for e in events if str(e.get("id", "")).startswith("e") and e["id"][1:].isdigit()}
    event = {**event, "id": f"e{max(used, default=0) + 1}"}
    save(profile, events + [event])
    return event


def remove(profile: dict, ids: list[str]) -> list[dict]:
    """Quita los eventos con esos ids; devuelve los quitados."""
    events = load(profile)
    gone = [e for e in events if e.get("id") in set(ids)]
    if gone:
        save(profile, [e for e in events if e.get("id") not in set(ids)])
    return gone


def for_plan(events: list[dict], start: date, race_date: date, today: date) -> list[dict]:
    """Los eventos que caen dentro del plan (del lunes de la semana 1 a la carrera) y aún no han pasado."""
    return [e for e in prune(events, today)
            if (_iso(e["end"]) or today) >= start and (_iso(e["start"]) or race_date) <= race_date]


# ─── Texto para el LLM y para el atleta ───────────────────────────────────────

def describe(e: dict) -> str:
    start, end = _iso(e["start"]), _iso(e["end"])
    when = (f"{rp._DAY_NAMES[start.isoweekday()]} {start.strftime('%d/%m/%Y')}" if start == end
            else f"del {start.strftime('%d/%m')} al {end.strftime('%d/%m/%Y')}")
    if e["kind"] == "session":
        return (f"{e['title']}: {when}, {SPORTS.get(e['sport'], e['sport'])}, {e['duration_min']} min, intensidad "
                f"{INTENSITIES.get(e['intensity'], e['intensity'])}")
    limit = "sin entrenar" if not e.get("max_minutes") else f"como mucho {e['max_minutes']} min al día"
    return f"{e['title']}: {when}, {limit}" + (f"; material: {e['equipment']}" if e.get("equipment") else "")


def markdown(events: list[dict]) -> str:
    if not events:
        return "No tienes eventos fijos guardados."
    return "\n".join(f"- `{e['id']}` {describe(e)}" for e in events)


def frame_lines(events: list[dict], monday) -> list[str]:
    """Bloque del prompt: los eventos con su id y su semana del plan (monday(n) → lunes de la semana n)."""
    if not events:
        return []
    lines = ["", "## Eventos fijos del atleta (obligatorios)"]
    for e in events:
        week = (_iso(e["start"]) - monday(1)).days // 7 + 1
        lines.append(f"- id {e['id']} · semana {max(1, week)} · {describe(e)}")
    return lines


def rules_lines(events: list[dict]) -> list[str]:
    if not events:
        return []
    return ["- Eventos fijos del atleta (bloque \"Eventos fijos\"): el día de una salida o sesión fija lleva esa sesión, con "
            "su deporte y su duración (±10 %), y \"event\": \"<id>\" en el día. Si es la tirada larga de la semana, "
            "\"type\": \"long\". Un viaje sin entrenar es \"rest\"; con un máximo, el día no lo supera y solo usa el "
            "material que tiene.",
            "- Un evento fijo no cuenta en el máximo de sesiones de calidad ni en el 20 % de Z3 o más, y el volumen de "
            "esa semana incluye sus minutos: reparte el resto de la semana alrededor."]


# ─── Comprobaciones ───────────────────────────────────────────────────────────

def event_id(session: dict) -> str:
    """El evento al que responde una sesión (lo escribe el LLM en su día), o ""."""
    return str(((session.get("structured_workout") or {}).get("plan_day") or {}).get("event") or "")


def _minutes(session: dict) -> float:
    try:
        return float(session.get("duration_min") or 0)
    except (TypeError, ValueError):
        return 0.0


def week_events(events: list[dict], monday: date, today: date) -> list[tuple[int, dict]]:
    """(día 1-7, evento) de una semana, sin los días ya pasados."""
    out = []
    for d in range(1, 8):
        day = monday + timedelta(days=d - 1)
        if day < today:
            continue
        out += [(d, e) for e in events if e["start"] <= day.isoformat() <= e["end"]]
    return out


def fixed_minutes(events: list[dict], monday: date, today: date) -> float:
    """Minutos de las sesiones fijas de una semana: el volumen de esa semana no puede quedarse por debajo."""
    return float(sum(e["duration_min"] for _, e in week_events(events, monday, today) if e["kind"] == "session"))


def fixed_long_minutes(events: list[dict], monday: date, today: date) -> float:
    """La sesión fija de carrera más larga de la semana: si es la tirada larga, las reglas de la tirada no la limitan."""
    return float(max((e["duration_min"] for _, e in week_events(events, monday, today)
                      if e["kind"] == "session" and e["sport"] in RUNNING_SPORTS), default=0))


def check_week(n: int, sessions: list[dict], monday: date, events: list[dict], today: date | None = None) -> list[str]:
    """Cada día con un evento fijo cumple lo que dijo el atleta (sesión con su duración y deporte, o los límites del viaje)."""
    today = today or date.today()
    by_day: dict[int, list[dict]] = {}
    for s in sessions:
        by_day.setdefault(int(s["day_index"]), []).append(s)
    errors: list[str] = []
    for d, e in week_events(events, monday, today):
        day = monday + timedelta(days=d - 1)
        name = f"{rp._DAY_NAMES[d].lower()} {day.strftime('%d/%m')}"
        todays = by_day.get(d, [])
        if e["kind"] == "travel":
            real = [s for s in todays if s["session_type"] != "rest"]
            cap = int(e.get("max_minutes") or 0)
            if cap == 0 and real:
                errors.append(f"Semana {n}: el {name} el atleta está de viaje ({e['title']}) y no puede entrenar: debe ser "
                              "\"rest\".")
            elif sum(_minutes(s) for s in real) > cap:
                errors.append(f"Semana {n}: el {name} el atleta está de viaje ({e['title']}) y puede entrenar como mucho "
                              f"{cap} min; el día suma {sum(_minutes(s) for s in real):.0f}.")
            continue
        mine = [s for s in todays if event_id(s) == e["id"]]
        if not mine:
            errors.append(f"Semana {n}: el {name} el atleta tiene fijado «{e['title']}» ({SPORTS.get(e['sport'], e['sport'])}, "
                          f"{e['duration_min']} min, intensidad {INTENSITIES.get(e['intensity'], e['intensity'])}): pon ese "
                          f"día una sesión con \"event\": \"{e['id']}\" y esa duración.")
            continue
        s = mine[0]
        if abs(_minutes(s) - e["duration_min"]) > max(10.0, e["duration_min"] * 0.10):
            errors.append(f"Semana {n}: el {name} «{e['title']}» dura {e['duration_min']} min y tu sesión {_minutes(s):.0f}; "
                          "tiene que durar lo que dijo el atleta (±10 %).")
        if e["sport"] in RUNNING_SPORTS:
            ok = s["session_type"] in RUNNING_TYPES
        elif e["sport"] == "other":
            ok = s["session_type"] != "rest"
        else:
            ok = s["session_type"] == "cross_training" and (s["structured_workout"].get("sport") == e["sport"])
        if not ok:
            errors.append(f"Semana {n}: el {name} «{e['title']}» es de {SPORTS.get(e['sport'], e['sport'])}: usa "
                          + ("una sesión de carrera (easy, long o quality)." if e["sport"] in RUNNING_SPORTS else
                             f"un día \"cross\" con \"sport\": \"{e['sport']}\"." if e["sport"] != "other" else
                             "una sesión de entrenamiento, no descanso."))
    return errors
