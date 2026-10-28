"""
agent/routes/records.py
Ruta de récords personales: tabla de récords de Garmin por deporte.
"""


from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _try_parse_json,
)


def _build_personal_records_markdown(compact_records: str, preferred_sport: str = "running") -> str:
    """Renderiza récords personales en markdown legible para el usuario."""
    data = _try_parse_json(compact_records)
    if not isinstance(data, list) or not data:
        return "No encontré récords personales en Garmin Connect para este usuario."

    rows: list[tuple[str, str, str]] = []
    running_type_ids = {1, 2, 3, 4, 5, 6, 7}
    cycling_type_ids = {8, 9, 11}

    for item in data:
        if not isinstance(item, dict):
            continue
        categoria = (
            item.get("categoria")
            or item.get("tipo")
            or item.get("record_type")
            or "Registro"
        )
        valor = (
            item.get("valor")
            or item.get("tiempo")
            or item.get("distancia")
            or item.get("elevacion")
            or item.get("pasos")
            or item.get("racha")
            or item.get("value")
            or "n/d"
        )
        type_id = item.get("type_id") if item.get("type_id") is not None else item.get("typeId")

        deporte = str(item.get("deporte") or "").lower()
        categoria_lower = str(categoria).lower()
        if isinstance(type_id, int):
            is_running = type_id in running_type_ids
            is_cycling = type_id in cycling_type_ids
        else:
            is_running = (
                "run" in deporte
                or "carrera" in deporte
                or "marathon" in categoria_lower
                or "5k" in categoria_lower
                or "10k" in categoria_lower
                or "longest run" in categoria_lower
            )
            is_cycling = (
                "cycl" in deporte
                or "bike" in deporte
                or "ride" in categoria_lower
                or "cycling" in categoria_lower
            )

        sport = "running" if is_running else "cycling" if is_cycling else "other"
        rows.append((str(categoria), str(valor), sport))

    selected: list[tuple[str, str, str]]
    if preferred_sport == "cycling":
        selected = [r for r in rows if r[2] == "cycling"]
    elif preferred_sport == "running":
        selected = [r for r in rows if r[2] == "running"]
    else:
        selected = rows
    selected = selected[:10]

    if not selected:
        if preferred_sport == "cycling":
            return "No encontré récords personales de ciclismo en Garmin Connect para este usuario."
        if preferred_sport == "running":
            return "No encontré récords personales de running en Garmin Connect para este usuario."
        return "No encontré récords personales en Garmin Connect para este usuario."

    sport_label = "ciclismo" if preferred_sport == "cycling" else "running"

    lines = [
        "## 🧭 Resumen",
        f"Estos son tus mejores registros personales en {sport_label}.",
        "",
        "## 📊 Métricas clave",
        "| Métrica | Valor | Fuente |",
        "|---|---|---|",
    ]
    for categoria, valor, _ in selected:
        lines.append(f"| {categoria} | {valor} | Garmin personal records |")

    lines.extend([
        "",
        "## ✅ Recomendación",
        "- Usa estas marcas como referencia para definir ritmos y objetivos de bloque.",
        "",
        "## 🎯 Próximo paso",
        "- Si quieres, te propongo ritmos objetivo basados en estas marcas.",
    ])

    return "\n".join(lines)
