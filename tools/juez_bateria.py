"""Juez determinista de la batería de Kairos: compara cada respuesta con los datos reales.

Uso:
    python tools/juez_bateria.py .tmp/bateria/<salida>.md [--username U]

- Lee la transcripción de tools/run_bateria.py y la contrasta con Supabase (load_metrics_daily,
  activity_tss, perfil, planes) y, para los récords, con Garmin (solo lectura).
- Rúbrica fijada el 22/10/2026 antes de ver resultados (docs/proximos_pasos.md, CHANGELOG): comprobaciones
  globales G1–G5 en todas las respuestas y comprobaciones de datos por pregunta.
- Solo juzga hechos y coherencia; la calidad del consejo sigue siendo revisión humana.
- Genera <salida>.juez.md y <salida>.juez.json. Sale con 1 si alguna pregunta suspende.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", encoding="utf-8")

from agent import load_metrics as lm  # noqa: E402
from agent import sensors  # noqa: E402
from agent import storage  # noqa: E402
from agent import trainer_agent as ta  # noqa: E402

TOL = 0.1          # TSS, CTL, ATL, TSB (se muestran con un decimal)
TOL_BPM = sensors.TOL_BPM        # rangos de FC (definidas en agent/sensors.py)
TOL_PACE_S = sensors.TOL_PACE_S  # ritmos


@dataclass
class Verdict:
    n: int
    question: str
    seconds: float | None
    failures: list[str] = field(default_factory=list)
    env_failures: list[str] = field(default_factory=list)
    checks: int = 0

    def check(self, ok: bool, msg: str) -> None:
        self.checks += 1
        if not ok:
            self.failures.append(msg)

    def sense(self, findings: list[sensors.Finding]) -> None:
        """Una comprobación por sensor; cada hallazgo es un fallo con el mensaje del sensor."""
        self.checks += 1
        self.failures.extend(f.message for f in findings)

    @property
    def status(self) -> str:
        if self.env_failures:
            return "ENTORNO"
        return "OK" if not self.failures else "FALLO"


# ─── Lectura de la transcripción ─────────────────────────────────────────────

def load_transcript(path: Path) -> list[tuple[int, str, float | None, str]]:
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"\n### (\d+)\. (.+?)  ·  ([\d.]+|None) s\n", text)
    out = []
    for i in range(1, len(parts) - 3 + 1, 4):
        n, q, secs, body = int(parts[i]), parts[i + 1].strip(), parts[i + 2], parts[i + 3]
        body = body.split("\n---\n")[0]
        out.append((n, q, None if secs == "None" else float(secs), body))
    return out


def _num(s: str) -> float:
    return float(s.replace(",", ".").replace("+", ""))


def _dmy_to_iso(dmy: str, year: int | None = None) -> str:
    parts = dmy.split("/")
    if len(parts) == 3:
        return date(int(parts[2]), int(parts[1]), int(parts[0])).isoformat()
    return date(year or date.today().year, int(parts[1]), int(parts[0])).isoformat()


def _close(a: float, b: float, tol: float = TOL) -> bool:
    return abs(float(a) - float(b)) <= tol + 1e-9


# ─── Verdad de referencia ────────────────────────────────────────────────────

def _call_mcp_tool(name: str, args: dict | None = None):
    """Llama a una tool del servidor MCP (python -m garmin_mcp) por stdio y devuelve su JSON."""
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def _run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "garmin_mcp"], cwd=str(ROOT), env=dict(os.environ))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(name, args or {})
                return "".join(getattr(c, "text", "") for c in res.content)

    text = asyncio.run(_run())
    parsed = ta._try_parse_json(text)
    return parsed if parsed is not None else []

class Truth:
    def __init__(self, username: str, today: date | None = None):
        # Sesión de Supabase Auth guardada por Kairos (supabase/README.md): con RLS, sin ella no se ve nada.
        if not storage.restore_session(username):
            raise SystemExit(f"Sin sesión de {username} en este ordenador: entra una vez en Kairos (python -m agent.main) y vuelve a lanzar esto.")
        # "Hoy" es el día de la transcripción, para poder volver a juzgar una batería antigua (24/10).
        self.today = today or date.today()
        self.series = storage.get_load_metrics_series(days=400)
        self.by_date = {r["date"]: r for r in self.series}
        self.acts = list((storage.get_activity_tss_map("2000-01-01") or {}).values())
        self.profile = ta._load_user_profile()
        self.perf = (self.profile or {}).get("performance") or {}
        self.plan = storage.get_active_training_plan()
        lthr, _, _ = ta._resolve_hr_threshold_bpm(self.profile)
        self.lthr = float(lthr) if lthr else None
        # Zonas de carrera de Garmin guardadas en el perfil (las del reloj y TP); sin ellas, % del LTHR.
        self.zones, self.zones_source = ta._resolve_hr_zones(self.profile, "running")
        self.zones_cycling, _ = ta._resolve_hr_zones(self.profile, "cycling")
        self.pace_today = ta._resolve_running_threshold_pace_for_date(self.profile, self.today.isoformat())
        self._prs: set[str] | None = None

    def latest(self) -> dict:
        """Fila del día de la transcripción (o la anterior más cercana), no la última de la serie."""
        return self.row_on_or_before(self.today.isoformat()) or self.series[-1]

    def row_on_or_before(self, d_iso: str) -> dict | None:
        rows = [r for r in self.series if r["date"] <= d_iso]
        return rows[-1] if rows else None

    def tss_day_acts(self, d_iso: str) -> float:
        return round(sum(float(a["tss"] or 0) for a in self.acts if str(a["start_date"]) == d_iso), 1)

    def family(self, sport_key: str) -> str | None:
        if ta._is_trail_activity(sport_key):
            return "trail"
        if ta._is_running_non_trail_activity(sport_key):
            return "running"
        if ta._is_strength_activity(sport_key):
            return "fuerza"
        if ta._is_cycling_activity(sport_key):
            return "ciclismo"
        return None

    def pr_values(self) -> set[str]:
        """Marcas de Garmin formateadas como las muestra Kairos (mm:ss, hh:mm:ss o km)."""
        if self._prs is None:
            raw = _call_mcp_tool("get_personal_record")
            if isinstance(raw, dict):
                raw = [raw]
            vals: set[str] = set()
            for r in raw:
                v = r.get("value")
                if v is None:
                    continue
                v = float(v)
                s = int(round(v))
                vals.add(f"{s // 60:02d}:{s % 60:02d}")
                vals.add(f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}")
                vals.add(f"{v / 1000:.2f} km")
            self._prs = vals
        return self._prs


# ─── Comprobaciones globales (G1–G5) ────────────────────────────────────────

def global_checks(v: Verdict, body: str) -> None:
    v.check(bool(body.strip()) and "(sin respuesta)" not in body and "SIN RESPUESTA" not in body,
            "G1: respuesta vacía")
    v.check("Traceback" not in body and "No pude generar" not in body, "G1: error o coaching no generado")
    if "No se pudo consultar Garmin" in body:
        v.env_failures.append("G2: Garmin no accesible durante la pregunta")
    v.sense(sensors.find_iso_dates(body))
    v.sense(sensors.find_empty_data_lines(body))
    coaching = body.split("Interpretación de coaching", 1)[1] if "Interpretación de coaching" in body else ""
    v.sense(sensors.find_invented_user_turn(body, coaching))
    # S25 (diferencia de HRV) en toda la respuesta; S26 (etiqueta del TSS) del coaching frente a los datos de arriba.
    v.sense(sensors.find_wrong_hrv_deviation(body))
    v.sense(sensors.find_swapped_tss_label(coaching, body.split("Interpretación de coaching", 1)[0]))


# ─── Comprobaciones por pregunta ────────────────────────────────────────────

def check_state_and_coaching(v: Verdict, body: str, t: Truth) -> None:
    last = t.latest()
    m = re.search(r"Estado físico \(CTL\) ([\d.]+) · Fatiga \(ATL\) ([\d.]+)", body)
    v.check(bool(m), "estado: no aparece la línea CTL/ATL")
    if m:
        v.check(_close(_num(m.group(1)), last["ctl"]), f"CTL {m.group(1)} ≠ {last['ctl']:.1f}")
        v.check(_close(_num(m.group(2)), last["atl"]), f"ATL {m.group(2)} ≠ {last['atl']:.1f}")
    m = re.search(r"Forma \(TSB\) ([+-]?[\d.]+) = ([+-]?\d+) % de tu CTL → zona \S+ ([^(]+?) \(", body)
    v.check(bool(m), "estado: no aparece la zona de Forma")
    if m:
        zone = lm.form_zone(last["tsb"], last["ctl"])
        v.check(_close(_num(m.group(1)), last["tsb"]), f"TSB {m.group(1)} ≠ {last['tsb']:+.1f}")
        v.check(m.group(3).strip() == zone["label"], f"zona '{m.group(3).strip()}' ≠ '{zone['label']}'")
    v.check("Interpretación de coaching" in body, "coaching: falta el bloque del LLM")
    coaching = body.split("Interpretación de coaching", 1)[1] if "Interpretación de coaching" in body else ""
    # Sensores S10–S12 (agent/sensors.py): CTL ≠ máximo habitual de la ATL, rangos de FC en zonas, ritmo umbral
    v.sense(sensors.check_coaching_text(coaching, t.zones, t.zones_cycling, t.pace_today))


def check_trend(v: Verdict, body: str, t: Truth) -> None:
    rows = re.findall(r"^\s*(?:Hace \d sem\.|Sem\. pasada|Hoy)\s+(\d{2}/\d{2})\s+([\d.]+)\s+([\d.]+)\s+([+-][\d.]+)",
                      body, re.M)
    v.check(len(rows) >= 4, f"tendencia: {len(rows)} filas")
    for dm, atl, ctl, tsb in rows:
        ref = t.row_on_or_before(_dmy_to_iso(dm, t.today.year))
        if ref:
            v.check(_close(_num(atl), ref["atl"]) and _close(_num(ctl), ref["ctl"]) and _close(_num(tsb), ref["tsb"]),
                    f"tendencia {dm}: {atl}/{ctl}/{tsb} ≠ {ref['atl']:.1f}/{ref['ctl']:.1f}/{ref['tsb']:+.1f}")


def check_week_tss(v: Verdict, body: str, t: Truth) -> None:
    m = re.search(r"Semana natural\s+(\d{2}/\d{2}/\d{4}) → (\d{2}/\d{2}/\d{4})", body)
    v.check(bool(m), "semana: falta el rango")
    if not m:
        return
    start, end = _dmy_to_iso(m.group(1)), _dmy_to_iso(m.group(2))
    total = re.search(r"TSS acumulado\s+([\d.]+)", body)
    expected = round(sum(float(r["tss"]) for r in t.series if start <= r["date"] <= end), 1)
    v.check(bool(total) and _close(_num(total.group(1)), expected), f"TSS acumulado {total.group(1) if total else '?'} ≠ {expected}")
    for dm, val in re.findall(r"•\s+\w+\s+(\d{2}/\d{2}): ([\d.]+) \(", body):
        d_iso = _dmy_to_iso(dm, int(start[:4]))
        ref = t.by_date.get(d_iso)
        v.check(ref is not None and _close(_num(val), ref["tss"]), f"día {dm}: {val} ≠ {ref['tss'] if ref else '—'}")
    for dm, val in re.findall(r"•\s+(\d{2}/\d{2}): TSS ([\d.]+)", body):
        d_iso = _dmy_to_iso(dm, int(start[:4]))
        v.check(_close(_num(val), t.tss_day_acts(d_iso)), f"actividades {dm}: {val} ≠ {t.tss_day_acts(d_iso)}")
    for val in re.findall(r"· TSS ([\d.]+) \(", body):
        v.check(any(_close(_num(val), a["tss"]) for a in t.acts if start <= str(a["start_date"]) <= end),
                f"actividad con TSS {val} no está en activity_tss de la semana")
    ref = t.row_on_or_before(min(end, t.today.isoformat()))
    row = re.search(r"\(\d{2}/\d{2}\s*→[^\n]*?\s([\d.]+)(?: \([^)]*\))?\s+([\d.]+)\s+([\d.]+)\s+([+-][\d.]+)", body)
    if row and ref:
        v.check(_close(_num(row.group(2)), ref["ctl"]) and _close(_num(row.group(3)), ref["atl"])
                and _close(_num(row.group(4)), ref["tsb"]),
                f"fila semana CTL/ATL/TSB {row.group(2)}/{row.group(3)}/{row.group(4)} ≠ "
                f"{ref['ctl']:.1f}/{ref['atl']:.1f}/{ref['tsb']:+.1f}")


def check_activities_range(v: Verdict, body: str, t: Truth) -> None:
    m = re.search(r"Rango consultado\s+(\d{2}/\d{2}/\d{4}) → (\d{2}/\d{2}/\d{4})", body)
    v.check(bool(m), "actividades: falta el rango")
    if not m:
        return
    start, end = _dmy_to_iso(m.group(1)), _dmy_to_iso(m.group(2))
    ids = {int(x) for x in re.findall(r"\[id:(\d+)\]", body)}
    expected = {int(a["activity_id"]) for a in t.acts if start <= str(a["start_date"]) <= end}
    v.check(expected <= ids, f"faltan actividades con TSS guardado: {sorted(expected - ids)}")
    n = re.search(r"Actividades detectadas\s+(\d+)", body)
    v.check(bool(n) and int(n.group(1)) == len(ids), f"recuento {n.group(1) if n else '?'} ≠ {len(ids)} listadas")


def check_activity_analysis(v: Verdict, body: str, t: Truth, which: str) -> None:
    m = re.search(r"Fecha consultada\s+(\d{2}/\d{2}/\d{4})", body)
    v.check(bool(m), "análisis: falta la fecha")
    if not m:
        return
    d_iso = _dmy_to_iso(m.group(1))
    if which == "last":
        exp = max(str(a["start_date"]) for a in t.acts)
        v.check(d_iso == exp, f"última actividad: {d_iso} ≠ {exp}")
    elif which == "last_trail":
        exp = max(str(a["start_date"]) for a in t.acts if t.family(a["sport"]) == "trail")
        v.check(d_iso == exp, f"último trail: {d_iso} ≠ {exp}")
    elif which:
        v.check(d_iso == which, f"fecha consultada {d_iso} ≠ {which}")
    day_acts = [a for a in t.acts if str(a["start_date"]) == d_iso]
    tss = re.search(r"TSS \(actividad\)\s+([\d.]+)", body)
    v.check(bool(tss) and any(_close(_num(tss.group(1)), a["tss"]) for a in day_acts),
            f"TSS {tss.group(1) if tss else '?'} no coincide con activity_tss del {d_iso}")
    load = re.search(r"Carga Garmin \(no TSS\)\s+([\d.]+)", body)
    if load:
        v.check(any(a.get("native_load") is not None and _close(_num(load.group(1)), a["native_load"]) for a in day_acts),
                f"Carga Garmin {load.group(1)} ≠ native_load")


def check_activity_count(v: Verdict, body: str, t: Truth) -> None:
    m = re.search(r"Actividades con TSS\s+(\d+)", body)
    v.check(bool(m) and int(m.group(1)) == len(t.acts), f"total {m.group(1) if m else '?'} ≠ {len(t.acts)}")
    expected = Counter(ta._get_activity_name_es(str(a["sport"])) or str(a["sport"]) for a in t.acts)
    for label, n in expected.items():
        found = re.search(rf"^\s*{re.escape(label)}\s+(\d+)\s*$", body, re.M)
        v.check(bool(found) and int(found.group(1)) == n, f"{label}: {found.group(1) if found else 'falta'} ≠ {n}")


def check_threshold_pace(v: Verdict, body: str, t: Truth) -> None:
    m = re.search(r"ritmo umbral de carrera es (\d):(\d{2})", body)
    v.check(bool(m) and t.pace_today is not None and abs(int(m.group(1)) * 60 + int(m.group(2)) - t.pace_today) <= TOL_PACE_S,
            f"ritmo umbral {m.group(0) if m else '?'} ≠ vigente")
    shown = re.findall(r"Vigente desde (\d{2}/\d{2}/\d{4})\s+(\d):(\d{2})", body)
    hist = sorted((e["date"], float(e["sec_per_km"])) for e in self_or_empty(t.perf.get("running_threshold_pace_history")))
    v.check(len(shown) == len(hist), f"historial: {len(shown)} filas ≠ {len(hist)}")
    for (dmy, mi, se), (d, sec) in zip(shown, hist):
        v.check(_dmy_to_iso(dmy) == d and abs(int(mi) * 60 + int(se) - sec) <= TOL_PACE_S, f"historial {dmy} {mi}:{se} ≠ {d} {sec}")


def self_or_empty(x):
    return x if isinstance(x, list) else []


def check_hr_threshold(v: Verdict, body: str, t: Truth) -> None:
    m = re.search(r"FC umbral \(LTHR\) es (\d+) lpm", body)
    v.check(bool(m) and t.lthr is not None and abs(int(m.group(1)) - t.lthr) <= TOL_BPM, f"FC umbral {m.group(1) if m else '?'} ≠ {t.lthr}")
    tp = ta._resolve_tp_hr_threshold_for_date(t.profile, t.today.isoformat())
    m2 = re.search(r"Referencia de TrainingPeaks para hrTSS\s+(\d+) lpm", body)
    if tp:
        v.check(bool(m2) and abs(int(m2.group(1)) - tp) <= TOL_BPM, f"referencia TP {m2.group(1) if m2 else '?'} ≠ {tp}")


def check_plan(v: Verdict, body: str, t: Truth) -> None:
    m = re.search(r"Plan activo\s+(Sí|Si|No)\b", body)
    v.check(bool(m), "plan: falta la fila 'Plan activo'")
    if m:
        v.check((m.group(1) != "No") == bool(t.plan), f"plan activo '{m.group(1)}' ≠ BD ({'sí' if t.plan else 'no'})")


def check_records(v: Verdict, body: str, t: Truth) -> None:
    vals = re.findall(r"^\s*(?:1K|1 Milla|5K|10K|Medio Maratón|Maratón|Carrera más larga)\s+(\d{2}:\d{2}(?::\d{2})?|[\d.]+ km)",
                      body, re.M)
    v.check(len(vals) >= 3, f"récords: {len(vals)} marcas")
    prs = t.pr_values()
    for val in vals:
        v.check(val in prs, f"marca {val} no coincide con Garmin")


def check_goal(v: Verdict, body: str, t: Truth) -> None:
    goals = (t.profile or {}).get("goals") or {}
    race = str(goals.get("target_race") or "")
    v.check(bool(race) and race in body, f"objetivo '{race}' no aparece")
    if goals.get("target_race_date"):
        dmy = datetime.fromisoformat(str(goals["target_race_date"])).strftime("%d/%m/%Y")
        v.check(dmy in body, f"fecha objetivo {dmy} no aparece")
    if goals.get("target_time"):
        v.check(str(goals["target_time"]) in body, f"tiempo objetivo {goals['target_time']} no aparece")


def check_recent_by_sport(v: Verdict, body: str, t: Truth, fam: str) -> None:
    rows = re.findall(rf"^\s*(\d{{2}}/\d{{2}}/\d{{4}})\s+{fam}\s+.*?\s([\d.]+)\s+([\d.]+)\s*$", body, re.M)
    pool = sorted((a for a in t.acts if t.family(str(a["sport"])) == fam), key=lambda a: str(a["start_date"]), reverse=True)
    v.check(len(rows) == min(20, len(pool)), f"{len(rows)} filas ≠ {min(20, len(pool))}")
    dates = [_dmy_to_iso(d) for d, _, _ in rows]
    v.check(dates == sorted(dates, reverse=True), "filas no ordenadas por fecha descendente")
    expected = [(str(a["start_date"]), round(float(a["tss"]), 1)) for a in pool[:20]]
    got = [(d, round(_num(tss), 1)) for d, (_, tss, _) in zip(dates, rows)]
    v.check(Counter(got) == Counter(expected), f"filas (fecha, TSS) distintas de activity_tss: {sorted(set(got) ^ set(expected))[:3]}")
    for (dmy, tss, load) in rows:
        cand = [a for a in pool if str(a["start_date"]) == _dmy_to_iso(dmy) and _close(_num(tss), a["tss"])]
        v.check(any(a.get("native_load") is not None and _close(_num(load), a["native_load"]) for a in cand),
                f"{dmy}: Carga Garmin {load} ≠ native_load")
    stats = re.search(rf"^\s*{fam}\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s*$", body, re.M)
    if stats and rows:
        vals = [_num(x) for _, x, _ in rows]
        v.check(_close(_num(stats.group(2)), sum(vals) / len(vals)) and _close(_num(stats.group(3)), max(vals)),
                f"media/máximo {stats.group(2)}/{stats.group(3)} ≠ {sum(vals) / len(vals):.1f}/{max(vals):.1f}")


# ─── Enrutado de preguntas a sus comprobaciones ──────────────────────────────

def specific_checks(v: Verdict, q: str, body: str, t: Truth) -> None:
    ql = q.lower()
    sports = {"trail": "trail", "running": "running", "fuerza": "fuerza", "ciclismo": "ciclismo"}
    if "ultimas actividades de" in ql or "últimas actividades de" in ql:
        fam = next((f for k, f in sports.items() if k in ql), None)
        if fam:
            check_recent_by_sport(v, body, t, fam)
    elif any(k in ql for k in ("forma física hoy", "entrenar fuerte", "qué tipo de sesión", "qué me toca hoy")):
        check_state_and_coaching(v, body, t)
    elif "tendencia de carga" in ql:
        check_trend(v, body, t)
    elif "cuánto tss" in ql or "y la semana del" in ql:
        check_week_tss(v, body, t)
    elif "qué actividades hice" in ql:
        check_activities_range(v, body, t)
    elif "analiza mi última actividad" in ql or "analiza mi ultima actividad" in ql:
        check_activity_analysis(v, body, t, "last_trail" if "trail" in ql else "last")
    elif "30 de agosto" in ql or "30/08" in ql:
        check_activity_analysis(v, body, t, f"{t.today.year}-08-30")
    elif "actividades tienes registradas" in ql:
        check_activity_count(v, body, t)
    elif "ritmo umbral" in ql:
        check_threshold_pace(v, body, t)
    elif "fc umbral" in ql:
        check_hr_threshold(v, body, t)
    elif "plan activo" in ql:
        check_plan(v, body, t)
    elif "récords" in ql or "records" in ql:
        check_records(v, body, t)
    elif "objetivo principal" in ql:
        check_goal(v, body, t)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("transcripcion", help="fichero .md generado por tools/run_bateria.py")
    ap.add_argument("--username", default=None)
    args = ap.parse_args()
    username = args.username or os.environ.get("KAIROS_USERNAME")
    if not username:
        ap.error("falta el usuario: --username o KAIROS_USERNAME en .env")
    path = Path(args.transcripcion)
    m = re.match(r"(\d{4})(\d{2})(\d{2})_", path.name)
    day = date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else datetime.fromtimestamp(path.stat().st_mtime).date()
    if day != date.today():
        print(f"⚠️  Transcripción del {day.strftime('%d/%m/%Y')}: se juzga con los datos de ese día (las actividades "
              "o el perfil pueden haber cambiado desde entonces).")
    truth = Truth(username, today=day)

    verdicts: list[Verdict] = []
    for n, q, secs, body in load_transcript(path):
        v = Verdict(n, q, secs)
        global_checks(v, body)
        try:
            specific_checks(v, q, body, truth)
        except Exception as exc:  # el juez nunca debe tumbarse por una respuesta inesperada
            v.failures.append(f"juez: no pude analizar la respuesta ({type(exc).__name__}: {exc})")
        verdicts.append(v)

    ok = sum(1 for v in verdicts if v.status == "OK")
    env = sum(1 for v in verdicts if v.status == "ENTORNO")
    lines = [f"# Juez de la batería — {path.name}", "",
             f"{ok}/{len(verdicts)} OK · {len(verdicts) - ok - env} FALLO · {env} ENTORNO (fallo de Garmin, no de Kairos)", "",
             "| # | Pregunta | Estado | Comprobaciones | Motivos |", "|---|---|---|---|---|"]
    for v in verdicts:
        reasons = "; ".join(v.env_failures + v.failures) or "—"
        lines.append(f"| {v.n} | {v.question} | {v.status} | {v.checks} | {reasons} |")
    out_md = path.with_suffix(".juez.md")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.with_suffix(".juez.json").write_text(json.dumps(
        [{"n": v.n, "pregunta": v.question, "estado": v.status, "comprobaciones": v.checks,
          "fallos": v.failures, "entorno": v.env_failures, "segundos": v.seconds} for v in verdicts],
        ensure_ascii=False, indent=1), encoding="utf-8")
    for v in verdicts:
        mark = {"OK": "✅", "FALLO": "❌", "ENTORNO": "⚠️"}[v.status]
        print(f"{mark} {v.n:2d} {v.question[:55]:55s} {v.checks:2d} comprob." + (f"  → {'; '.join(v.env_failures + v.failures)}" if v.status != "OK" else ""))
    print(f"\n{ok}/{len(verdicts)} OK · informe: {out_md}")
    return 0 if ok + env == len(verdicts) and env == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
