"""
agent/replies.py
Piezas comunes de las respuestas: aviso de Garmin no disponible al principio, nota de transparencia al pie y respuestas genéricas o sin acceso que hay que rescatar.
"""


def _is_generic_needs_more_info_reply(text: str) -> bool:
    """Detecta respuestas genéricas de "falta información" cuando ya hay contexto suficiente."""
    raw = (text or "").strip().lower()
    if not raw:
        return False
    markers = [
        "no puedo crear una planificación",
        "no puedo analizar",
        "no tengo suficiente información",
        "no tengo suficiente informacion",
        "sin más información",
        "proporciona más detalles",
        "por favor, proporciona más",
    ]
    return any(marker in raw for marker in markers)


def _is_no_access_reply(text: str) -> bool:
    """Detecta respuestas genéricas de falta de acceso a datos."""
    raw = (text or "").strip().lower()
    if not raw:
        return False
    markers = [
        "no tengo acceso",
        "no dispongo de acceso",
        "no puedo acceder",
    ]
    return any(marker in raw for marker in markers)


def _build_mcp_transparency_markdown(events: list[dict]) -> str:
    """Nota visible al pie cuando alguna consulta a Garmin (MCP) falló."""
    if not events:
        return ""

    grouped: dict[tuple[str, str], int] = {}
    reasons: dict[tuple[str, str], str] = {}
    for ev in events:
        tool = str(ev.get("tool") or "tool_desconocida").strip() or "tool_desconocida"
        mode = str(ev.get("mode") or "mcp_error").strip() or "mcp_error"
        key = (tool, mode)
        grouped[key] = grouped.get(key, 0) + 1
        if key not in reasons:
            reasons[key] = str(ev.get("reason") or "").strip()

    lines = ["", "## ℹ️ Transparencia de datos", "- Algunas consultas a Garmin (MCP) fallaron."]
    for (tool, mode), count in sorted(grouped.items(), key=lambda item: (-item[1], item[0][0])):
        reason = reasons.get((tool, mode))
        if reason:
            lines.append(f"- {tool}: {mode} x{count} ({reason})")
        else:
            lines.append(f"- {tool}: {mode} x{count}")
    lines.append("- Nota: estos datos pueden no reflejar el último estado en vivo de Garmin.")
    return "\n".join(lines)


def _build_mcp_error_warning_markdown(events: list[dict]) -> str:
    """Aviso al principio de la respuesta si alguna consulta a Garmin falló.

    Sin él, un fallo de Garmin se leía como dato real ("0 actividades", "sin datos").
    """
    failed = sorted({
        str(ev.get("tool") or "tool_desconocida").strip() or "tool_desconocida"
        for ev in (events or [])
        if str(ev.get("mode") or "") == "mcp_error"
    })
    if not failed:
        return ""
    return (
        f"> ⚠️ **No se pudo consultar Garmin** (falló: {', '.join(failed)}). "
        "Los datos que vienen de Garmin pueden estar incompletos: un \"0\" o \"sin actividades\" "
        "puede deberse a este error y no a que no haya datos. Vuelve a preguntar en unos minutos.\n\n"
    )
