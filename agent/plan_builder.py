"""
agent/plan_builder.py
Plan que construye el LLM del atleta (28/10, decisión del usuario: Kairos es el entrenador; el código aporta los
datos y comprueba, el LLM decide).

El LLM escribe el plan en un formato compacto: estructura de semanas (fase, descarga, minutos, tirada larga, enfoque)
y, para cada día, su tipo y sus tramos (tiempo o distancia + zona). Este módulo:

- prepara los datos del atleta para el prompt (`PlanFrame`, `frame_text`): fechas de cada semana, zonas de ritmo de
  Friel sobre su umbral y ritmo de carrera, zonas de FC de Garmin, carga real, disponibilidad y salud;
- convierte los tramos en sesiones `kairos-workout-v1` (`expand_week`): rangos de ritmo, km y TSS planificado
  (rTSS de TrainingPeaks con ritmo; hrTSS de TP con FC). El LLM nunca calcula ritmos, km ni TSS;
- comprueba la estructura y cada semana (`check_macro`, `check_week`): progresión ≤ 10 %, descargas, afinamiento,
  disponibilidad, descanso, calidad, carrera en su fecha. Los fallos vuelven al LLM para que los corrija.

Sin dependencias del chat ni de la red: las llamadas al LLM están en agent/plan_llm.py.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from agent import run_plan as rp
from agent.load_metrics import TP_HRTSS_RATE_ABOVE_THRESHOLD, TP_HRTSS_RATES

# Tipo de día del formato compacto → session_type del plan guardado.
DAY_TYPES = {
    "rest": "rest", "easy": "running_z2", "recovery": "recovery", "long": "long_run", "quality": "running_quality",
    "strength": "strength", "race": "race_run", "cross": "cross_training",
}
PHASES = {"base": "Base", "build": "Construcción", "specific": "Específico", "taper": "Afinamiento", "race": "Carrera"}
STEP_KINDS = {"warmup", "steady", "work", "recovery", "cooldown"}
# Deportes de un día "cross" (30/10): van por FC (hrTSS de TP, como la bici sin potencia), sin ritmo ni km.
CROSS_SPORTS = {"cycling": "Bici", "rowing": "Remo", "elliptical": "Elíptica", "swimming": "Natación"}
AUTO_DAY_TYPES = {"easy", "recovery", "long"}  # días cuyo tramo continuo puede ir con "min": "auto"
_STEP_NAMES = {"warmup": "Calentamiento", "cooldown": "Vuelta a la calma", "work": "Trabajo", "recovery": "Recuperación",
               "steady": "Continuo"}
_HR_INTENSITY = {1: "recovery", 2: "endurance", 3: "tempo", 4: "threshold", 5: "vo2"}
_HARD_CLASSES = {"tempo", "threshold", "vo2"}  # Z3 o más (Friel Z3 = tempo)
_STRENGTH_TSS_PER_HOUR = 35.0  # el mismo que la fuerza del plan por bloques (run_plan.strength_session)


@dataclass
class PlanFrame:
    """Datos del atleta con los que el LLM construye el plan y con los que el código lo comprueba."""

    start: date  # lunes de la semana 1
    weeks: int
    race: str
    race_date: date
    distance_km: float | None
    target_time: str
    threshold_pace: float | None  # s/km
    race_pace: float | None  # s/km
    hr_zones: list = field(default_factory=list)  # [(nombre, lo, hi)] de Garmin
    hr_threshold_tp: float | None = None  # umbral con el que TP calcula su hrTSS
    weekly_hours: float = 8.0
    recent_weekly_minutes: float | None = None
    longest_recent_min: float | None = None
    load: dict = field(default_factory=dict)  # ctl, atl, tsb, status
    available_days: list = field(default_factory=lambda: list(range(1, 8)))
    max_minutes_per_day: dict = field(default_factory=dict)
    min_rest_days: int = 1
    max_quality: int = 2
    primary_sport: str = ""
    note: str = ""  # lo que el atleta pide al crear el plan
    # Preferencias del atleta (/plan crear, 29/10): dobles, distribución de la intensidad y progresión de la carga.
    doubles: bool = False
    intensity: str = ""  # "polarizada", "piramidal" o "" (decide el entrenador)
    max_load_weeks: int = 3  # semanas de carga seguidas antes de una descarga (3+1 o 2+1)
    max_hard_share: float = 0.20  # tiempo en Z3 o más por semana (decisión del usuario, 29/10)
    notes: list = field(default_factory=list)  # lo que el atleta dijo para siempre (plan_prefs, 30/10)
    # Deportes cruzados (/plan crear, 30/10): None = sin regla (decide el entrenador); [] = ninguno.
    cross_sports: list | None = None
    cross_max: int | None = None  # sesiones "cross" por semana como mucho
    # Entorno (30/10, pendiente 11 a): dónde entrena y el desnivel de sus rutas ({"road"|"trail": {km, m_per_km}}).
    location: str = ""
    terrain: dict = field(default_factory=dict)

    @property
    def paces(self) -> rp.Paces | None:
        if not self.threshold_pace:
            return None
        return rp.Paces(self.threshold_pace, self.race_pace or self.threshold_pace)

    @property
    def training_days(self) -> int:
        """Días de entrenamiento por semana: los disponibles menos el descanso mínimo."""
        return 7 - max(self.min_rest_days, 7 - len(self.available_days))

    @property
    def race_week(self) -> int:
        return (self.race_date - self.start).days // 7 + 1

    def monday(self, week: int) -> date:
        return self.start + timedelta(weeks=week - 1)


def weeks_until(start: date, race_date: date) -> int:
    race_monday = race_date - timedelta(days=race_date.weekday())
    return max(1, min(24, (race_monday - start).days // 7 + 1))


# ─── Datos para el prompt ─────────────────────────────────────────────────────

def frame_text(f: PlanFrame) -> str:
    """Bloque de datos del atleta para el LLM: todo lo que necesita, ya calculado (no hace cuentas)."""
    lines = ["## Carrera objetivo",
             f"- {f.race}: {rp._DAY_NAMES[f.race_date.isoweekday()]} {f.race_date.strftime('%d/%m/%Y')} "
             f"(semana {f.race_week} del plan)" + (f", distancia {f.distance_km:g} km" if f.distance_km else "")
             + (f", tiempo objetivo {f.target_time}" if f.target_time else "")]
    lines += ["", "## Semanas del plan (lunes a domingo)"]
    for w in range(1, f.weeks + 1):
        m = f.monday(w)
        lines.append(f"- Semana {w}: {m.strftime('%d/%m/%Y')} – {(m + timedelta(days=6)).strftime('%d/%m/%Y')}")
    p = f.paces
    lines += ["", "## Zonas que puedes usar en los tramos"]
    if p:
        lines.append(f"Ritmo umbral vigente: {rp.fmt_pace(f.threshold_pace)}/km. Zonas de ritmo de Friel (TrainingPeaks):")
        for key in rp.ZONE_ORDER:
            fast, slow = p.zone(key)
            lines.append(f"- {key} ({rp.PACE_ZONES[key][0]}): {rp.fmt_pace(fast)}–{rp.fmt_pace(slow)}/km")
        if f.race_pace:
            fast, slow = p.race_range()
            lines.append(f"- RP (ritmo de carrera): {rp.fmt_pace(fast)}–{rp.fmt_pace(slow)}/km")
    else:
        lines.append("Sin ritmo umbral en el perfil: usa solo zonas de FC.")
    if f.hr_zones:
        lines.append("Zonas de FC de Garmin:")
        for i, (name, lo, hi) in enumerate(f.hr_zones, 1):
            lines.append(f"- FC{i} ({name}): {lo}–{hi} lpm")
    lines += ["", "## Carga real del atleta"]
    if f.recent_weekly_minutes:
        lines.append(f"- Volumen sostenido: {f.recent_weekly_minutes:.0f} min/semana (media de las últimas 4 semanas)")
    if f.longest_recent_min:
        lines.append(f"- Sesión de carrera o trail más larga de las últimas 6 semanas: {f.longest_recent_min:.0f} min")
    load = f.load or {}
    if load.get("ctl") is not None:
        lines.append(f"- CTL {load['ctl']}, ATL {load.get('atl')}, TSB {load.get('tsb')}, estado {load.get('status') or 'n/d'}")
    lines.append(f"- Horas disponibles declaradas: {f.weekly_hours:g} h/semana")
    if f.location or f.terrain:
        lines += ["", "## Entorno"]
        if f.location:
            lines.append(f"- Entrena en: {f.location}")
        names = {"road": "carrera en asfalto", "trail": "trail"}
        for key in ("road", "trail"):
            if key in f.terrain:
                t = f.terrain[key]
                lines.append(f"- Desnivel positivo de sus salidas de {names[key]} (últimas 6 semanas): "
                             f"{t['m_per_km']:g} m/km en {t['km']:g} km")
    lines += ["", "## Disponibilidad"]
    names = [rp._DAY_NAMES[d] for d in sorted(f.available_days)]
    lines.append(f"- Días disponibles: {', '.join(names)}")
    unavailable = [rp._DAY_NAMES[d] for d in range(1, 8) if d not in f.available_days]
    if unavailable:
        lines.append(f"- Días sin entrenar (descanso obligatorio): {', '.join(unavailable)}")
    for d, cap in sorted(f.max_minutes_per_day.items()):
        lines.append(f"- {rp._DAY_NAMES[int(d)]}: como mucho {cap} min")
    lines.append(f"- Descanso: al menos {f.min_rest_days} día(s) por semana; calidad: como mucho {f.max_quality} sesiones por semana")
    if f.primary_sport:
        lines.append(f"- Deporte principal del atleta: {f.primary_sport}")
    lines += ["", "## Preferencias del atleta",
              "- Dobles: " + ("sí, puede entrenar dos veces el mismo día (p. ej. rodaje y gimnasio)" if f.doubles
                              else "no, una sesión por día"),
              "- Distribución de la intensidad: " + {
                  "polarizada": "polarizada (80/20: casi todo en Z1-Z2 y lo intenso en Z4-Z5, poco Z3)",
                  "piramidal": "piramidal (mucho Z1-Z2, menos Z3 y aún menos Z4-Z5)",
              }.get(f.intensity, "la decide el entrenador")
              + f"; en cualquier caso, como mucho un {f.max_hard_share * 100:.0f} % del tiempo semanal en Z3 o más",
              f"- Progresión de la carga: bloques de {f.max_load_weeks} semanas de carga + 1 de descarga"]
    if f.cross_sports is not None:
        lines.append("- Deportes cruzados dentro del plan: " + (
            ", ".join(CROSS_SPORTS[s].lower() for s in f.cross_sports)
            + (f"; como mucho {f.cross_max} sesiones por semana" if f.cross_max is not None else "")
            if f.cross_sports else "ninguno (solo carrera y fuerza)"))
    lines += [f"- Siempre (lo dijo el atleta): {n}" for n in f.notes]
    if f.note:
        lines += ["", "## Lo que pide el atleta", f.note]
    return "\n".join(lines)


def training_days_text(f: PlanFrame) -> str:
    """Dónde caben los minutos de la semana, para la estructura: días de entrenamiento y dobles."""
    text = f"{f.training_days} días por semana (el resto, descanso obligatorio)"
    return text + (", y el atleta puede doblar (dos sesiones el mismo día)" if f.doubles
                   else ", una sesión por día (el atleta no dobla), con rodajes de 90 min como mucho")


def rules_text(f: PlanFrame) -> str:
    """Las reglas que comprueba `check_week`, con los números del atleta: el LLM las tiene delante al escribir."""
    unavailable = [rp._DAY_NAMES[d].lower() for d in range(1, 8) if d not in f.available_days]
    lines = [f"- Como mucho un {f.max_hard_share * 100:.0f} % del tiempo de la semana (sin contar la fuerza) en Z3 o más, RP "
             "incluido (salvo la semana de la carrera).",
             ("- Un día puede tener dos sesiones (doble: repite el \"day\" con la segunda); el descanso no se dobla."
              if f.doubles else "- Una sesión por día (el atleta no dobla)."),
             f"- Cada semana: al menos {f.min_rest_days} día(s) \"rest\" (sin entrenar)"
             + (f", y el {', el '.join(unavailable)} siempre \"rest\"" if unavailable else "") + ".",
             f"- Como mucho {f.max_quality} sesiones \"quality\" por semana, nunca en días seguidos.",
             "- Una tirada larga (\"long\") por semana, salvo en la de la carrera.",
             f"- La carrera (\"race\") solo el {rp._DAY_NAMES[f.race_date.isoweekday()].lower()} "
             f"{f.race_date.strftime('%d/%m/%Y')}; después, solo descanso o recuperación.",
             "- Un rodaje con \"auto\" dura al menos 20 min y menos que la tirada larga de la semana: si la semana "
             "necesita más, añade un rodaje o alarga otras sesiones; si necesita menos, quita uno o pon sus minutos."]
    lines += [f"- El {rp._DAY_NAMES[int(d)].lower()}, como mucho {cap} min." for d, cap in sorted(f.max_minutes_per_day.items())]
    if f.cross_sports is not None:
        lines.append("- Sin sesiones \"cross\": el atleta no quiere otros deportes." if not f.cross_sports else
                     f"- \"cross\" solo de {', '.join(f.cross_sports)}"
                     + (f", como mucho {f.cross_max} por semana." if f.cross_max is not None else "."))
    return "\n".join(lines)


def macro_text(macro: dict) -> str:
    """La estructura aprobada, para los prompts del detalle de las semanas y de la revisión."""
    lines = []
    for w in macro.get("weeks") or []:
        lines.append(f"- Semana {w.get('week')}: {PHASES.get(str(w.get('phase')), w.get('phase'))}"
                     + (" · descarga" if w.get("deload") else "")
                     + f" · {w.get('minutes')} min · tirada larga {w.get('long_min')} min · {w.get('focus') or ''}")
    return "\n".join(lines)


def parse_json(text: str) -> dict:
    """El objeto JSON de la respuesta del LLM (con o sin bloque ```json). ValueError si no hay uno válido."""
    raw = str(text or "")
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("La respuesta no contiene un objeto JSON.")
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON no válido: {exc.msg} (línea {exc.lineno}).") from exc
    if not isinstance(data, dict):
        raise ValueError("La respuesta debe ser un objeto JSON.")
    return data


# ─── Estructura (macro) ───────────────────────────────────────────────────────

def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def check_macro(macro: dict, f: PlanFrame) -> list[str]:
    """Reglas de la estructura del plan (las mismas para cualquier modelo): ninguna se calibra con el resultado."""
    errors: list[str] = []
    weeks = macro.get("weeks")
    if not str(macro.get("title") or "").strip():
        errors.append("Falta \"title\".")
    if not isinstance(weeks, list) or [int(_num(w.get("week"))) for w in weeks if isinstance(w, dict)] != list(range(1, f.weeks + 1)):
        return errors + [f"\"weeks\" debe tener las semanas 1 a {f.weeks} en orden, una por semana."]
    goal_min = f.weekly_hours * 60
    last_load = f.recent_weekly_minutes or 0.0
    last_long = 0.0
    peak = 0.0
    streak = 0
    for w in weeks:
        n = int(w["week"])
        phase = str(w.get("phase") or "")
        minutes = _num(w.get("minutes"))
        long_min = _num(w.get("long_min"))
        deload = bool(w.get("deload"))
        if phase not in PHASES:
            errors.append(f"Semana {n}: fase \"{phase}\" no válida (usa {', '.join(PHASES)}).")
            continue
        if (phase == "race") != (n == f.race_week):
            errors.append(f"Semana {n}: la fase \"race\" es solo la semana {f.race_week}, la de la carrera.")
        if minutes <= 0:
            errors.append(f"Semana {n}: \"minutes\" debe ser mayor que 0.")
            continue
        if minutes > goal_min * 1.10:
            errors.append(f"Semana {n}: {minutes:.0f} min superan las horas disponibles ({goal_min:.0f} min + 10 %).")
        if long_min > minutes * 0.40:
            errors.append(f"Semana {n}: la tirada larga ({long_min:.0f} min) supera el 40 % de la semana ({minutes:.0f} min).")
        if phase in ("taper", "race"):
            if peak and minutes > peak * 0.85:
                errors.append(f"Semana {n}: el afinamiento debe bajar al menos un 15 % sobre la semana más alta "
                              f"({peak:.0f} min); como mucho {peak * 0.85:.0f} min.")
            continue
        if deload:
            if last_load and minutes > last_load * 0.85:
                errors.append(f"Semana {n}: la descarga debe bajar al menos un 15 % sobre la última semana de carga "
                              f"({last_load:.0f} min); como mucho {last_load * 0.85:.0f} min.")
            streak = 0
            continue
        if last_load and minutes > last_load * 1.10 + 1:
            what = "el volumen que sostiene el atleta" if n == 1 or not peak else "la semana de carga anterior"
            errors.append(f"Semana {n}: {minutes:.0f} min suben más de un 10 % sobre {what} ({last_load:.0f} min); "
                          f"como mucho {last_load * 1.10:.0f} min.")
        if last_long and long_min > last_long + 15:
            errors.append(f"Semana {n}: la tirada larga sube más de 15 min sobre la anterior de carga ({last_long:.0f} min).")
        streak += 1
        if streak > f.max_load_weeks:
            errors.append(f"Semana {n}: más de {f.max_load_weeks} semanas de carga seguidas; pon una descarga "
                          f"(bloques {f.max_load_weeks}+1, preferencia del atleta).")
            streak = 0
        last_load = minutes
        last_long = long_min
        peak = max(peak, minutes)
    return errors


# ─── Días y tramos ────────────────────────────────────────────────────────────

def _zone_key(raw: str) -> str:
    z = str(raw or "").strip().upper().replace(" ", "")
    if z == "RP":
        return "RP"
    if z.startswith("FC") and z[2:].isdigit():
        return z
    m = re.fullmatch(r"Z([1-5])([ABC]?)", z)
    return f"Z{m.group(1)}{m.group(2).lower()}" if m else z


def _expand_step(raw: dict, f: PlanFrame, errors: list[str], where: str) -> dict | None:
    if "repeat" in raw:
        reps = int(_num(raw.get("repeat")))
        inner = [s for s in (_expand_step(x, f, errors, where) for x in (raw.get("steps") or []) if isinstance(x, dict)) if s]
        if reps < 1 or not inner:
            errors.append(f"{where}: un bloque \"repeat\" necesita repeat ≥ 1 y sus \"steps\".")
            return None
        return rp.block(reps, *inner, name=str(raw.get("name") or "Series"))
    where = f"{where}, tramo {json.dumps(raw, ensure_ascii=False)}"  # el LLM ve qué tramo corregir
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in STEP_KINDS:
        errors.append(f"{where}: \"kind\" \"{kind}\" no válido (usa {', '.join(sorted(STEP_KINDS))}).")
        return None
    zone = _zone_key(raw.get("zone"))
    fixed_pace = None
    if raw.get("pace") not in (None, ""):
        # Ritmo concreto ("4x1000 a 4:00/km", 29/10): rango ±3 s, como el de carrera; la zona es la que le toca.
        fixed_pace = rp.parse_target_time(str(raw.get("pace")).replace("/km", "").strip())
        if not fixed_pace or not 150 <= fixed_pace <= 600:
            errors.append(f"{where}: \"pace\" va como \"m:ss\" por km (p. ej. \"4:00\").")
            return None
        if not f.threshold_pace:
            errors.append(f"{where}: sin ritmo umbral no se puede usar \"pace\"; usa zonas de FC (FC1 a FC5).")
            return None
        zone = rp.Paces(f.threshold_pace, f.threshold_pace).zone_of(fixed_pace)
    auto = str(raw.get("min") or "").strip().lower() == "auto"
    if auto:
        # La duración la pone el código para que la semana sume sus minutos (expand_week): el LLM no suma.
        if kind != "steady" or raw.get("m") or raw.get("km") or raw.get("sec"):
            errors.append(f"{where}: \"min\": \"auto\" solo va en un tramo \"steady\" sin distancia.")
            return None
        raw = {**raw, "min": 1}
    minutes = _num(raw.get("min")) + _num(raw.get("sec")) / 60
    meters = _num(raw.get("m")) + _num(raw.get("km")) * 1000
    if minutes <= 0 and meters <= 0:
        errors.append(f"{where}: el tramo no tiene duración (o es 0); dale \"min\", \"sec\", \"m\" o \"km\", o quítalo.")
        return None
    if minutes > 0 and meters > 0:
        errors.append(f"{where}: el tramo lleva tiempo y distancia; deja solo uno de los dos.")
        return None
    name = str(raw.get("name") or "") or None
    note = str(raw.get("note") or "")
    if zone.startswith("FC") and not re.fullmatch(r"FC[1-9]", zone) or "-" in zone:
        errors.append(f"{where}: una sola zona por tramo (p. ej. \"Z2\" o \"FC2\"), no un rango.")
        return None
    if zone.startswith("FC"):
        n = int(zone[2:])
        if not 1 <= n <= max(5, len(f.hr_zones)):
            errors.append(f"{where}: zona \"{zone}\" no existe (FC1 a FC5).")
            return None
        if meters:
            errors.append(f"{where}: un tramo por FC va por tiempo, no por distancia.")
            return None
        out = {"name": name or _STEP_NAMES[kind], "type": kind, "duration_min": round(minutes, 2), "reps": 1,
               "intensityClass": _HR_INTENSITY[n],
               "target": {"metric": "hr_zone", "zone": n, "zone_to": n, "intensityClass": _HR_INTENSITY[n]}}
        if note:
            out["note"] = note
        if auto:
            out["auto"] = True
        return out
    p = f.paces
    if p is None:
        errors.append(f"{where}: el atleta no tiene ritmo umbral; usa zonas de FC (FC1 a FC5).")
        return None
    if zone == "RP" and not f.race_pace:
        errors.append(f"{where}: no hay ritmo de carrera (falta tiempo objetivo o distancia); usa una zona de ritmo.")
        return None
    if zone != "RP" and zone not in rp.PACE_ZONES:
        errors.append(f"{where}: zona \"{raw.get('zone')}\" no válida (usa {', '.join(rp.ZONE_ORDER)}, RP o FC1-FC5).")
        return None
    out = rp.step(p, kind, zone, minutes or None, meters=meters or None, name=name, note=note)
    if fixed_pace:
        out["target"].update({"range": [fixed_pace - 3, fixed_pace + 3], "pace_fixed": True})
        if meters:
            out["duration_min"] = round(meters / 1000 * fixed_pace / 60, 2)
    if auto:
        out["auto"] = True
    return out


# Zona de ritmo equivalente a cada zona de FC, para estimar los km de un tramo de carrera por FC (29/10).
_HR_TO_PACE_ZONE = {1: "Z1", 2: "Z2", 3: "Z3", 4: "Z4", 5: "Z5a"}


def workout_totals(steps: list[dict], f: PlanFrame, running: bool = True) -> tuple[float, float, float]:
    """(minutos, km, TSS planificado): rTSS de TP con ritmo; hrTSS de TP (tabla por FC/umbral) con FC.

    En una sesión de carrera, los km de un tramo por FC se estiman con el ritmo medio de su zona de ritmo
    equivalente (29/10: antes no sumaban y una semana de rodajes por FC salía con 0 km); en bici o cross, no.
    """
    minutes = km = tss = 0.0
    for s, times in rp._leaf_steps(steps):
        dur = _num(s.get("duration_min")) * times
        target = s.get("target") or {}
        minutes += dur
        if target.get("metric") == "pace":
            mid = sum(target["range"]) / 2
            km += dur * 60 / mid
            tss += dur / 60 * (f.threshold_pace / mid) ** 2 * 100
        elif target.get("metric") == "hr_zone" and f.hr_zones and f.hr_threshold_tp:
            _, lo, hi = f.hr_zones[int(target["zone"]) - 1]
            ratio = (lo + hi) / 2 / f.hr_threshold_tp
            rate = next((r for upper, r in TP_HRTSS_RATES if ratio < upper), TP_HRTSS_RATE_ABOVE_THRESHOLD)
            tss += dur / 60 * rate
        if target.get("metric") == "hr_zone" and running and f.paces:
            fast, slow = f.paces.zone(_HR_TO_PACE_ZONE[min(5, int(target["zone"]))])
            km += dur * 60 / ((fast + slow) / 2)
    return round(minutes, 1), round(km, 1), round(tss, 1)


def _main_target(steps: list[dict]) -> dict:
    order = ["recovery", "endurance", "tempo", "threshold", "vo2"]
    leaves = [s for s, _ in rp._leaf_steps(steps) if s.get("type") not in ("warmup", "cooldown")] or \
        [s for s, _ in rp._leaf_steps(steps)]
    best = max(leaves, key=lambda s: (order.index(s.get("intensityClass") or "endurance"), _num(s.get("duration_min"))))
    return dict(best.get("target") or {})


def expand_day(day: dict, f: PlanFrame, week: int) -> tuple[dict | None, list[str]]:
    """Un día del formato compacto → fila de sesión con su structured_workout. Devuelve (sesión, fallos)."""
    errors: list[str] = []
    d = int(_num(day.get("day")))
    where = f"Semana {week}, día {d}"
    kind = str(day.get("type") or "").strip().lower()
    if kind not in DAY_TYPES:
        return None, [f"{where}: \"type\" \"{kind}\" no válido (usa {', '.join(DAY_TYPES)})."]
    title = str(day.get("title") or "").strip()
    if kind == "rest":
        wk = rp.rest_session()
        if title:
            wk["title"] = title
    elif kind == "cross" and (str(day.get("sport") or "") not in CROSS_SPORTS or not day.get("steps")):
        # 30/10: antes un "cross" sin tramos salía titulado "Fuerza" y con el TSS de la fuerza (35/h).
        return None, [f"{where}: un \"cross\" lleva \"sport\" ({', '.join(CROSS_SPORTS)}) y \"steps\" por FC "
                      "(FC1 a FC5), con warmup y cooldown."]
    elif kind == "strength" and not day.get("steps"):
        minutes = _num(day.get("minutes"))
        if minutes <= 0:
            return None, [f"{where}: una sesión de {kind} sin tramos necesita \"minutes\"."]
        note = str(day.get("note") or "")
        rpe = str(day.get("rpe") or "5-6")
        lo, hi = (int(_num(x, 5)) for x in (rpe.split("-") + [rpe])[:2])
        wk = {"schema": "kairos-workout-v1", "sessionType": DAY_TYPES[kind], "title": title or "Fuerza",
              "summary": {"duration_min": minutes, "distance_km": 0.0, "tss": round(minutes / 60 * _STRENGTH_TSS_PER_HOUR, 1),
                          "intensityClass": "tempo"},
              "targets": {"intensityClass": "tempo", "primary": {"metric": "rpe", "range": [lo, hi]}},
              "steps": [{"name": "Calentamiento", "type": "warmup", "duration_min": min(10.0, minutes / 4), "reps": 1,
                         "intensityClass": "endurance", "target": {"metric": "rpe", "range": [3, 4]}},
                        {"name": title or "Principal", "type": "steady", "duration_min": minutes - min(10.0, minutes / 4) - 5,
                         "reps": 1, "intensityClass": "tempo", "target": {"metric": "rpe", "range": [lo, hi]},
                         **({"note": note} if note else {})},
                        {"name": "Movilidad", "type": "cooldown", "duration_min": 5.0, "reps": 1, "intensityClass": "recovery",
                         "target": {"metric": "rpe", "range": [2, 3]}}],
              "notes": f"{title}: {note} (RPE {rpe})" if note else f"{title} (RPE {rpe})"}
    else:
        raw_steps = [s for s in (day.get("steps") or []) if isinstance(s, dict)]
        steps = [s for s in (_expand_step(s, f, errors, where) for s in raw_steps) if s]
        if errors:
            return None, errors
        if not steps:
            return None, [f"{where}: una sesión de {kind} necesita \"steps\"."]
        if steps[0].get("type") != "warmup" or steps[-1].get("type") != "cooldown":
            return None, [f"{where}: el primer tramo debe ser \"warmup\" y el último \"cooldown\"."]
        autos = [s for s in steps if s.get("auto")]
        nested_auto = any(c.get("auto") for s in steps if s.get("steps") for c, _ in rp._leaf_steps(s["steps"]))
        if nested_auto or len(autos) > 1 or (autos and kind not in AUTO_DAY_TYPES):
            return None, [f"{where}: \"min\": \"auto\" va en un solo tramo \"steady\" de un rodaje (easy, recovery) "
                          "o de la tirada larga (long), fuera de las series."]
        if kind == "cross" and any((s.get("target") or {}).get("metric") != "hr_zone" for s, _ in rp._leaf_steps(steps)):
            # Una zona de ritmo en bici o remo daría un rTSS de carrera: solo FC (hrTSS de TP, como la bici sin potencia).
            return None, [f"{where}: en un \"cross\" cada tramo va por FC (FC1 a FC5), sin zonas de ritmo, RP ni \"pace\"."]
        if kind == "cross":
            title = title or CROSS_SPORTS[str(day["sport"])]
        wk = {"schema": "kairos-workout-v1", "sessionType": DAY_TYPES[kind], "title": title or DAY_TYPES[kind],
              "secondary_label": f"RPE {day['rpe']}" if day.get("rpe") else "",
              "threshold_pace": f.threshold_pace,
              "steps": steps,
              **({"sport": str(day["sport"])} if kind == "cross" else {})}
    wk["plan_day"] = {k: v for k, v in day.items()}  # lo que escribió el LLM: la revisión parte de aquí
    session = {"week_index": week, "day_index": d, "session_type": wk["sessionType"], "structured_workout": wk}
    return refresh_session(session, f), []


def refresh_session(session: dict, f: PlanFrame) -> dict:
    """Duración, km, TSS, objetivo principal y descripción de una sesión a partir de sus tramos."""
    wk = session["structured_workout"]
    if "secondary_label" in wk:  # sesión con tramos de ritmo o FC
        steps = wk["steps"]
        minutes, km, tss = workout_totals(steps, f, running=wk["sessionType"] != "cross_training")
        main = _main_target(steps)
        wk["summary"] = {"duration_min": minutes, "distance_km": km, "tss": tss,
                         "intensityClass": main.get("intensityClass") or "endurance"}
        wk["targets"] = {"intensityClass": main.get("intensityClass") or "endurance", "primary": main,
                         "secondary": {"metric": "rpe", "label": wk["secondary_label"]}}
        wk["notes"] = " · ".join(rp.describe_steps(steps))
    rpe_label = ((wk.get("targets") or {}).get("secondary") or {}).get("label") or ""
    session.update({"duration_min": int(round(float(wk["summary"]["duration_min"]))), "intensity": rpe_label,
                    "exercises": [wk["title"]], "notes": wk["notes"]})
    return session


def _auto_step(session: dict) -> dict | None:
    return next((s for s in session["structured_workout"].get("steps") or [] if s.get("auto")), None)


def _fill_auto(n: int, sessions: list[dict], planned: dict, f: PlanFrame, kept_minutes: float) -> list[str]:
    """Da duración a los tramos "auto": la tirada larga dura lo previsto y los rodajes reparten lo que falta para que
    la semana sume sus minutos (con los días que no cambian, `kept_minutes`). Fuera de límites, fallo para el LLM."""
    autos = [s for s in sessions if _auto_step(s)]
    if not autos:
        return []
    target = _num(planned.get("minutes"))
    if not target:
        return [f"Semana {n}: sin minutos en la estructura no se puede usar \"auto\"; pon los minutos."]
    errors: list[str] = []
    for s in [s for s in autos if s["session_type"] == "long_run"]:
        long_min = _num(planned.get("long_min"))
        rest = float(s["structured_workout"]["summary"]["duration_min"]) - 1
        if long_min - rest < 10:
            errors.append(f"Semana {n}: la tirada larga ya dura {rest:.0f} min sin el tramo \"auto\" y la estructura dice "
                          f"{long_min:.0f}; quita \"auto\" o acorta los otros tramos.")
            continue
        _auto_step(s)["duration_min"] = float(round(long_min - rest))
        refresh_session(s, f)
    easy = [s for s in autos if s["session_type"] != "long_run"]
    if easy and not errors:
        fixed = kept_minutes + sum(float(s["structured_workout"]["summary"]["duration_min"]) for s in sessions) - len(easy)
        each = (target - fixed) / len(easy)  # minutos del tramo "auto" de cada rodaje
        others = max(float(s["structured_workout"]["summary"]["duration_min"]) - 1 for s in easy)
        longs = [float(s["structured_workout"]["summary"]["duration_min"]) for s in sessions if s["session_type"] == "long_run"]
        # Un rodaje dura al menos 20 min y nunca tanto como la tirada larga de la semana (sin tirada: 90 min).
        low, high = max(1.0, 20 - others), (longs[0] if longs else 90.0) - others - 1
        if not low <= each <= high:
            fix = ("quita un rodaje o pon sus minutos" if each < low else
                   "añade un rodaje" + (" (o dobla: un segundo rodaje el mismo día)" if f.doubles else "")
                   + " o alarga otras sesiones")
            errors.append(f"Semana {n}: para que la semana sume {target:.0f} min, cada rodaje \"auto\" duraría "
                          f"{each + others:.0f} min (entre 20 y {high + others:.0f}, menos que la tirada larga); {fix}.")
        else:
            for s in easy:
                _auto_step(s)["duration_min"] = float(round(each))
                refresh_session(s, f)
    if not errors:
        for s in autos:
            _auto_step(s).pop("auto")
    return errors


def expand_week(week: dict, f: PlanFrame, planned: dict | None = None,
                kept_minutes: float = 0.0) -> tuple[list[dict], list[str]]:
    """Los días de una semana → sesiones, con los tramos "auto" ya con su duración (ver `_fill_auto`)."""
    n = int(_num(week.get("week")))
    sessions: list[dict] = []
    errors: list[str] = []
    for day in week.get("days") or []:
        if not isinstance(day, dict):
            continue
        session, errs = expand_day(day, f, n)
        errors += errs
        if session:
            sessions.append(session)
    slots: dict[int, int] = {}
    for s in sessions:  # día doble (29/10): el orden en que las escribe el LLM; la primera es la principal
        slots[s["day_index"]] = slots.get(s["day_index"], 0) + 1
        s["structured_workout"]["slot"] = slots[s["day_index"]]
    if not errors:
        errors = _fill_auto(n, sessions, planned or {}, f, kept_minutes)
    return sessions, errors


def session_key(s: dict) -> tuple[int, int, int]:
    """(semana, día, orden): con días dobles, semana y día no bastan para identificar una sesión."""
    return (int(s.get("week_index") or 0), int(s.get("day_index") or 0),
            int(((s.get("structured_workout") or {}).get("slot")) or 1))


def hard_minutes(session: dict) -> float:
    """Minutos de una sesión en Z3 o más: zonas de ritmo Z3 a Z5c, RP o ritmo fijado igual o más rápido que Z3, FC3-FC5."""
    total = 0.0
    for s, times in rp._leaf_steps((session.get("structured_workout") or {}).get("steps") or []):
        if s.get("intensityClass") in _HARD_CLASSES:
            total += _num(s.get("duration_min")) * times
    return total


def check_week(n: int, sessions: list[dict], planned: dict, f: PlanFrame, past_days: int = 0) -> list[str]:
    """Reglas de una semana frente a su estructura, a la disponibilidad y a las preferencias del atleta.

    Un día puede tener dos sesiones (doble) si el atleta las admite. `past_days`: días ya pasados de la semana
    (revisión), que no se comprueban contra la estructura.
    """
    errors: list[str] = []
    by_day: dict[int, list[dict]] = {}
    for s in sessions:
        by_day.setdefault(int(s["day_index"]), []).append(s)
    if sorted(by_day) != list(range(1, 8)):
        return [f"Semana {n}: debe tener los 7 días (1 = lunes … 7 = domingo); tiene {sorted(by_day)}."]
    for d, day in by_day.items():
        name = rp._DAY_NAMES[d].lower()
        types = [s["session_type"] for s in day]
        if len(day) > (2 if f.doubles else 1):
            errors.append(f"Semana {n}: el {name} tiene {len(day)} sesiones; "
                          + ("como mucho 2 (doble)." if f.doubles else "el atleta no dobla: una por día."))
        if "rest" in types and len(day) > 1:
            errors.append(f"Semana {n}: el {name} es descanso; no lleva otra sesión.")
        if d not in f.available_days and types != ["rest"]:
            errors.append(f"Semana {n}: el {name} el atleta no entrena; debe ser \"rest\".")
        cap = f.max_minutes_per_day.get(d)
        if cap is not None and sum(s["duration_min"] for s in day) > cap:
            errors.append(f"Semana {n}: el {name} admite como mucho {cap} min (sumando sus sesiones).")
    rest = sum(1 for day in by_day.values() if [s["session_type"] for s in day] == ["rest"])
    if rest < f.min_rest_days:
        errors.append(f"Semana {n}: necesita al menos {f.min_rest_days} día(s) de descanso ({{\"type\": \"rest\"}}, sin tramos; "
                      "\"recovery\" es un rodaje regenerativo, no descanso).")
    quality_days = sorted(d for d, day in by_day.items() if any(s["session_type"] == "running_quality" for s in day))
    quality = sum(1 for s in sessions if s["session_type"] == "running_quality")
    if quality > f.max_quality:
        errors.append(f"Semana {n}: {quality} sesiones de calidad; como mucho {f.max_quality}.")
    if any(b - a < 2 for a, b in zip(quality_days, quality_days[1:])):
        errors.append(f"Semana {n}: dos sesiones de calidad en días seguidos; deja al menos un día entre ellas.")
    # Deportes cruzados (30/10): solo los que dijo el atleta y como mucho los que admite por semana.
    cross = [s for s in sessions if s["session_type"] == "cross_training"]
    if f.cross_sports is not None and cross:
        if not f.cross_sports:
            errors.append(f"Semana {n}: el atleta no quiere otros deportes en el plan; quita las sesiones \"cross\".")
        else:
            other = sorted({str(s["structured_workout"].get("sport")) for s in cross} - set(f.cross_sports))
            if other:
                errors.append(f"Semana {n}: \"cross\" de {', '.join(other)}; el atleta solo hace "
                              f"{', '.join(f.cross_sports)}.")
        if f.cross_max is not None and len(cross) > f.cross_max:
            errors.append(f"Semana {n}: {len(cross)} sesiones \"cross\"; como mucho {f.cross_max}.")
    races = [d for d, day in by_day.items() for s in day if s["session_type"] == "race_run"]
    if n == f.race_week:
        if races != [f.race_date.isoweekday()]:
            errors.append(f"Semana {n}: la carrera (\"race\") va el {rp._DAY_NAMES[f.race_date.isoweekday()].lower()} "
                          f"{f.race_date.strftime('%d/%m/%Y')}, una sola vez.")
        after = [d for d in range(f.race_date.isoweekday() + 1, 8)
                 if any(s["session_type"] not in ("rest", "recovery") for s in by_day[d])]
        if after:
            errors.append(f"Semana {n}: después de la carrera solo descanso o recuperación.")
    elif races:
        errors.append(f"Semana {n}: \"race\" solo en la semana de la carrera ({f.race_week}).")
    if n != f.race_week:
        # Distribución de la intensidad (decisión del usuario, 29/10): como mucho un 20 % del tiempo en Z3 o más.
        endurance = [s for s in sessions if s["session_type"] not in ("rest", "strength")]
        total_end = sum(float(s["structured_workout"]["summary"]["duration_min"]) for s in endurance)
        hard = sum(hard_minutes(s) for s in endurance)
        if total_end and hard > total_end * f.max_hard_share + 0.5:
            where = ", ".join(f"{rp._DAY_NAMES[s['day_index']].lower()} {hard_minutes(s):.0f}"
                              for s in sorted(endurance, key=session_key) if hard_minutes(s) >= 1)
            # Dónde y cuánto, ya calculado (el LLM no acierta a ojo: en vivo se quedaba a 2 min tras 4 intentos).
            excess = math.ceil(hard - total_end * f.max_hard_share)
            top = max(endurance, key=hard_minutes)
            errors.append(f"Semana {n}: {hard:.0f} de {total_end:.0f} min ({hard / total_end * 100:.1f} %) en Z3 o más (RP incluido: "
                          f"{where}); como mucho el {f.max_hard_share * 100:.0f} %, {total_end * f.max_hard_share:.0f} min. "
                          f"Quita al menos {excess} min intensos (p. ej. el {rp._DAY_NAMES[top['day_index']].lower()}: "
                          f"{hard_minutes(top):.0f} → {hard_minutes(top) - excess:.0f} min) o pásalos a Z2.")
    if past_days:
        return errors
    total = sum(float(s["structured_workout"]["summary"]["duration_min"]) for s in sessions)
    target = _num(planned.get("minutes"))
    # Semana de la carrera: la carrera es fija y su volumen lo limita el afinamiento (check_macro con las sumas reales).
    if target and n != f.race_week and abs(total - target) > target * 0.12:
        # Las sumas son del código: el LLM recibe cuánto dura cada día y cuánto quitar o añadir.
        per_day = ", ".join(f"{rp._DAY_NAMES[d][:2]} {sum(float(s['structured_workout']['summary']['duration_min']) for s in by_day[d]):.0f}"
                            for d in range(1, 8))
        verb = "quita" if total > target else "añade"
        errors.append(f"Semana {n}: los días suman {total:.0f} min ({per_day}) y la estructura dice {target:.0f} min "
                      f"(±12 %: entre {target * 0.88:.0f} y {target * 1.12:.0f}); {verb} unos {abs(total - target):.0f} min.")
    long_min = _num(planned.get("long_min"))
    longs = [s for s in sessions if s["session_type"] == "long_run"]
    if long_min and n != f.race_week:
        if len(longs) != 1:
            errors.append(f"Semana {n}: debe tener una tirada larga (\"long\").")
        elif abs(float(longs[0]["structured_workout"]["summary"]["duration_min"]) - long_min) > max(10.0, long_min * 0.15):
            got = float(longs[0]["structured_workout"]["summary"]["duration_min"])
            errors.append(f"Semana {n}: la tirada larga dura {got:.0f} min y la estructura dice {long_min:.0f} min; "
                          f"{'quita' if got > long_min else 'añade'} unos {abs(got - long_min):.0f} min.")
    return errors


def chunk_weeks(weeks: int, per_call: int) -> list[list[int]]:
    """Tandas de semanas para detallar en paralelo, repartidas por igual (13 → 4+3+3+3, no 4+4+4+1: una semana de
    carrera sola confundía al modelo, que escribía el plan entero)."""
    n = -(-weeks // per_call)
    sizes = [weeks // n + (1 if i < weeks % n else 0) for i in range(n)]
    out, first = [], 1
    for size in sizes:
        out.append(list(range(first, first + size)))
        first += size
    return out


def actual_macro(macro: dict, expanded: dict[int, list[dict]]) -> dict:
    """La estructura con lo que suman de verdad los días de cada semana (minutos y tirada larga)."""
    weeks = []
    for w in macro.get("weeks") or []:
        sessions = expanded.get(int(w["week"])) or []
        longs = [float(s["structured_workout"]["summary"]["duration_min"]) for s in sessions if s["session_type"] == "long_run"]
        weeks.append({**w, "minutes": round(sum(float(s["structured_workout"]["summary"]["duration_min"]) for s in sessions)),
                      "long_min": round(longs[0]) if longs else 0})
    return {**macro, "weeks": weeks}


def week_of_error(error: str) -> int | None:
    m = re.match(r"Semana (\d+)", error)
    return int(m.group(1)) if m else None


def check_quality_variety(sessions: list[dict]) -> list[str]:
    """Las sesiones de calidad progresan: en una tanda de semanas no se repite la misma una y otra vez."""
    notes = [s["notes"] for s in sessions if s["session_type"] == "running_quality"]
    if len(set(notes)) < min(3, len(notes)):
        return ["Las sesiones de calidad se repiten casi iguales; haz que progresen de una semana a otra (repeticiones, "
                "duración de los tramos, recuperación o zona)."]
    return []


def plan_data(macro: dict, sessions: list[dict], f: PlanFrame) -> dict:
    """plan_data del plan guardado: lo que necesitan /plan ver, el contexto del LLM y el motor diario."""
    weeks = macro["weeks"]
    week_minutes = []
    for w in range(1, f.weeks + 1):
        week_minutes.append(round(sum(float(s["structured_workout"]["summary"]["duration_min"])
                                      for s in sessions if s["week_index"] == w), 1))
    phases = [str(w.get("phase")) for w in weeks]
    return {
        "method": "bloques_con_descarga",
        "author": "llm",
        "phases": phases,
        "deload_weeks": [int(w["week"]) for w in weeks if w.get("deload")],
        "focus": [str(w.get("focus") or "") for w in weeks],
        "planned_week_minutes": [_num(w.get("minutes")) for w in weeks],
        "long_minutes": [_num(w.get("long_min")) for w in weeks],
        "week_minutes": week_minutes,
        "phase_weeks": {ph: phases.count(ph) for ph in PHASES},
        "race_distance_km": f.distance_km,
        "race_pace_sec_per_km": round(f.race_pace, 1) if f.race_pace else None,
        "threshold_pace_sec_per_km": round(f.threshold_pace, 1) if f.threshold_pace else None,
        "start_date": f.start.isoformat(),
        "target_race": f.race,
        "target_race_date": f.race_date.isoformat(),
        "target_time": f.target_time,
        "weekly_training_hours": f.weekly_hours,
        "rationale": str(macro.get("rationale") or ""),
    }


def merge_replan(old_pd: dict, old_sessions: list[dict], new_pd: dict, new_sessions: list[dict],
                 current_week: int) -> tuple[dict, list[dict]]:
    """Replanificar (30/10, pendiente 11 d2): la semana actual del plan viejo como semana 1 y las nuevas desde la 2.

    `new_pd` y `new_sessions` son el plan nuevo, que empieza el lunes que viene; el resultado empieza el lunes de
    esta semana, así que las listas por semana de plan_data llevan delante la de la semana actual.
    """
    kept = [dict(s, week_index=1) for s in old_sessions if int(s.get("week_index") or 0) == current_week]
    moved = [dict(s, week_index=int(s["week_index"]) + 1) for s in new_sessions]

    def first(key: str, default):
        values = list(old_pd.get(key) or [])
        return values[current_week - 1] if 0 < current_week <= len(values) else default

    pd = dict(new_pd)
    for key, default in (("phases", "base"), ("focus", ""), ("planned_week_minutes", 0), ("long_minutes", 0)):
        pd[key] = [first(key, default)] + list(new_pd.get(key) or [])
    pd["week_minutes"] = [round(sum(float(s.get("duration_min") or 0) for s in kept), 1)] + list(new_pd.get("week_minutes") or [])
    pd["deload_weeks"] = ([1] if current_week in set(old_pd.get("deload_weeks") or []) else []) + \
        [int(w) + 1 for w in new_pd.get("deload_weeks") or []]
    pd["phase_weeks"] = {ph: pd["phases"].count(ph) for ph in PHASES}
    start = date.fromisoformat(str(new_pd["start_date"])[:10]) - timedelta(weeks=1)
    pd["start_date"] = start.isoformat()
    return pd, sorted(kept + moved, key=session_key)


def compact_week(sessions: list[dict], week: int) -> list[dict]:
    """Días de una semana en el formato compacto (lo que escribió el LLM), para la revisión."""
    out = []
    for s in sorted((s for s in sessions if int(s.get("week_index") or 0) == week), key=lambda s: int(s.get("day_index") or 0)):
        day = dict(((s.get("structured_workout") or {}).get("plan_day")) or {})
        if not day:
            day = {"day": int(s.get("day_index") or 0), "type": "rest" if s.get("session_type") == "rest" else "easy",
                   "title": ((s.get("structured_workout") or {}).get("title")) or s.get("session_type")}
        out.append(day)
    return out
