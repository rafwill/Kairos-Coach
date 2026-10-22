"""Genera el catálogo de tools de garmin_mcp/README.md a partir del propio servidor (sin red).

Uso:
    python tools/gen_mcp_docs.py           # reescribe el bloque generado de garmin_mcp/README.md
    python tools/gen_mcp_docs.py --check   # sale con 1 si el README no está al día (lo usa la CI vía test)

Solo se reescribe lo que hay entre los marcadores BEGIN/END; el resto del README se edita a mano.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "garmin_mcp" / "README.md"
BEGIN = "<!-- BEGIN: catálogo generado por tools/gen_mcp_docs.py (no editar a mano) -->"
END = "<!-- END: catálogo generado -->"

CATEGORIES: list[tuple[str, list[str]]] = [
    ("Actividades", [
        "get_activities", "get_activities_by_date", "get_activities_fordate", "get_activity", "get_activity_details",
        "get_activity_splits", "get_activity_hr_in_timezones", "get_activity_power_in_timezones",
        "get_activity_exercise_sets", "get_training_effect",
    ]),
    ("Salud diaria", [
        "get_stats", "get_heart_rates_summary", "get_rhr_day", "get_stress_summary", "get_all_day_stress",
        "get_all_day_events", "get_body_battery", "get_respiration_summary", "get_spo2_data", "get_daily_steps",
        "get_hydration_data", "get_body_composition",
    ]),
    ("Sueño y recuperación", [
        "get_sleep_data", "get_sleep_summary", "get_hrv_data", "get_hrv_trend", "get_training_readiness",
        "get_morning_training_readiness",
    ]),
    ("Rendimiento", [
        "get_training_status", "get_training_load_trend", "get_vo2max_trend", "get_endurance_score",
        "get_fitnessage_data", "get_lactate_threshold", "get_heart_rate_zones", "get_cycling_ftp", "get_race_predictions",
        "get_personal_record",
    ]),
    ("Resúmenes semanales", ["get_weekly_steps", "get_weekly_intensity_minutes", "get_weekly_stress"]),
    ("Perfil", ["get_user_profile"]),
]


def _anchor(name: str) -> str:
    return name.replace("_", "-")


def _param_type(schema: dict) -> tuple[str, bool]:
    """Tipo legible y si admite null (parámetro opcional sin valor)."""
    if "anyOf" in schema:
        types = [s.get("type") for s in schema["anyOf"] if s.get("type")]
        nullable = "null" in types
        return " | ".join(t for t in types if t != "null") or "any", nullable
    return str(schema.get("type") or "any"), False


def _annotations(tool) -> str:
    a = tool.annotations
    if a is None:
        return "sin anotaciones"
    flags = []
    if a.readOnlyHint:
        flags.append("solo lectura")
    if a.destructiveHint is False:
        flags.append("no destructiva")
    if a.idempotentHint:
        flags.append("idempotente")
    if a.openWorldHint:
        flags.append("consulta un servicio externo (Garmin)")
    return ", ".join(flags)


def render(tools) -> str:
    by_name = {t.name: t for t in tools}
    listed = [n for _, names in CATEGORIES for n in names]
    missing = sorted(set(by_name) - set(listed))
    unknown = sorted(set(listed) - set(by_name))
    if missing or unknown:
        raise SystemExit(f"CATEGORIES desalineado con el servidor. Sin categoría: {missing}; inexistentes: {unknown}")

    out = [BEGIN, "", f"## Catálogo de tools ({len(tools)})", ""]
    out += ["| Categoría | Tools |", "|---|---|"]
    for cat, names in CATEGORIES:
        links = ", ".join(f"[`{n}`](#{_anchor(n)})" for n in names)
        out.append(f"| {cat} | {links} |")
    for cat, names in CATEGORIES:
        out += ["", f"### {cat}"]
        for n in names:
            t = by_name[n]
            out += ["", f'<a id="{_anchor(n)}"></a>', f"#### `{n}` — {t.title}", "", t.description or "", ""]
            props = (t.inputSchema or {}).get("properties") or {}
            required = set((t.inputSchema or {}).get("required") or [])
            if props:
                out += ["| Parámetro | Tipo | Por defecto | Descripción |", "|---|---|---|---|"]
                for pname, pschema in props.items():
                    ptype, nullable = _param_type(pschema)
                    if pname in required:
                        default = "obligatorio"
                    elif "default" in pschema and pschema["default"] is not None:
                        default = f"`{pschema['default']}`"
                    else:
                        default = "—"
                    desc = (pschema.get("description") or "").replace("|", "\\|")
                    out.append(f"| `{pname}` | {ptype}{' (opcional)' if nullable else ''} | {default} | {desc} |")
            else:
                out.append("Sin parámetros.")
            out += ["", f"*{_annotations(t)}*"]
    out += ["", END]
    return "\n".join(out)


def current_block(text: str) -> str:
    i, j = text.index(BEGIN), text.index(END) + len(END)
    return text[i:j]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="no escribe; sale con 1 si el README está desfasado")
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    from garmin_mcp import server

    block = render(asyncio.run(server.mcp.list_tools()))
    text = README.read_text(encoding="utf-8")
    if args.check:
        if current_block(text) != block:
            print("garmin_mcp/README.md está desfasado: ejecuta python tools/gen_mcp_docs.py")
            return 1
        print("garmin_mcp/README.md al día")
        return 0
    README.write_text(text.replace(current_block(text), block), encoding="utf-8")
    print(f"Catálogo regenerado en {README.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
