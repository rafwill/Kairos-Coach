"""
agent/startup_status.py
Resumen inicial del arranque (48 h de body battery, HRV, sueño, entrenamientos, estado de carga y recomendación), determinista.
"""

import logging
import re
from datetime import date, datetime, timedelta


from agent import load_metrics as _load_metrics
from agent.dates import (
    _parse_iso_date_safe,
    _safe_float,
)
from agent.wellness import (
    _format_body_battery_day,
    _format_hrv_day,
    _format_sleep_day,
)
from agent.routes.load import (
    _format_load_fatigue_summary,
)

log = logging.getLogger(__name__)


def _build_proactive_status_markdown(snapshot: dict) -> str:
    """Genera un bloque Markdown con estado proactivo de últimas 48h."""
    unavailable_label = "No disponible en este momento"

    def _is_generic_ok_summary(text: str) -> bool:
        lowered = (text or "").strip().lower()
        return "hoy=ok" in lowered or "ayer=ok" in lowered or "hoy=no" in lowered or "ayer=no" in lowered

    def _normalize_wellness_text(text: str | None) -> str:
        t = str(text or "").strip().lower()
        if t in {"", "sin datos", "datos disponibles", "sin datos recientes"}:
            return unavailable_label
        if ("datos disponibles" in t or "sin datos" in t) and not re.search(r"\d", t):
            return unavailable_label
        return str(text or "").strip()

    def _to_ddmmyyyy(value: str) -> str:
        try:
            return datetime.fromisoformat(value).strftime("%d/%m/%Y")
        except (TypeError, ValueError):
            return value

    profile_changes = snapshot.get("profile_changes", []) or []
    plan_assigned = bool(snapshot.get("plan_assigned", False))
    plan_recommendation = str(snapshot.get("plan_recommendation") or "").strip()
    daily_plan_decision = snapshot.get("daily_plan_decision") or {}
    plan_execution_feedback = snapshot.get("plan_execution_feedback") or {}
    body_battery = snapshot.get("body_battery", {}) or {}
    hrv = snapshot.get("hrv", {}) or {}
    sleep = snapshot.get("sleep", {}) or {}
    wellness_sources = snapshot.get("wellness_sources", {}) or {}
    load_fatigue = snapshot.get("load_fatigue") or {}
    trainings = snapshot.get("trainings", []) or []
    dates = snapshot.get("dates", {}) or {}
    today_iso = str(dates.get("today") or date.today().isoformat())
    yesterday_iso = str(dates.get("yesterday") or (date.today() - timedelta(days=1)).isoformat())
    today_display = _to_ddmmyyyy(today_iso)
    yesterday_display = _to_ddmmyyyy(yesterday_iso)

    lines = [
        "## 🧭 Resumen",
        "Estado proactivo de las últimas 48h calculado con datos reales.",
        "",
        "## 📊 Métricas clave",
    ]

    if profile_changes:
        lines.append(f"- Perfil Garmin actualizado: {', '.join(profile_changes)}")
    else:
        lines.append("- Perfil Garmin sin cambios detectados")

    lines.append(f"- Fechas analizadas: hoy={today_display} · ayer={yesterday_display}")

    body_summary = body_battery.get("summary") or ""
    if body_battery.get("today") is not None or body_battery.get("yesterday") is not None:
        body_summary = (
            f"hoy={_format_body_battery_day(body_battery.get('today'), today_iso)} · "
            f"ayer={_format_body_battery_day(body_battery.get('yesterday'), yesterday_iso)}"
        )
    elif _is_generic_ok_summary(body_summary):
        body_summary = unavailable_label

    hrv_summary = hrv.get("summary") or ""
    if hrv.get("today") is not None or hrv.get("yesterday") is not None:
        # HRV y sueño miden la noche: "anoche" = la noche que termina hoy.
        hrv_summary = (
            f"anoche={_format_hrv_day(hrv.get('today'), today_iso)} · "
            f"noche anterior={_format_hrv_day(hrv.get('yesterday'), yesterday_iso)}"
        )
    elif _is_generic_ok_summary(hrv_summary):
        hrv_summary = unavailable_label

    sleep_summary = sleep.get("summary") or ""
    if sleep.get("today") is not None or sleep.get("yesterday") is not None:
        sleep_summary = (
            f"anoche={_format_sleep_day(sleep.get('today'), today_iso)} · "
            f"noche anterior={_format_sleep_day(sleep.get('yesterday'), yesterday_iso)}"
        )
    elif _is_generic_ok_summary(sleep_summary):
        sleep_summary = unavailable_label

    lines.append("- Body Battery: " + _normalize_wellness_text(body_summary))
    lines.append("- HRV: " + _normalize_wellness_text(hrv_summary))
    lines.append("- Sueño: " + _normalize_wellness_text(sleep_summary))
    missing_today = [
        label
        for label, fmt, payload in (
            ("Body Battery de hoy", _format_body_battery_day, body_battery.get("today")),
            ("HRV de anoche", _format_hrv_day, hrv.get("today")),
            ("sueño de anoche", _format_sleep_day, sleep.get("today")),
        )
        if fmt(payload, today_iso) == "sin datos"
    ]
    if missing_today:
        lines.append(
            f"- ⚠️ Garmin aún no tiene {', '.join(missing_today)}: sincroniza el reloj con la app "
            "de Garmin Connect y vuelve a arrancar Kairos."
        )
    if wellness_sources:
        log.debug("proactive status: wellness origen %s", wellness_sources)
    lines.append("- Carga/Fatiga (TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma)): " + _format_load_fatigue_summary(load_fatigue))

    if load_fatigue and load_fatigue.get("latest"):
        latest = load_fatigue.get("latest") or {}
        ranges = load_fatigue.get("ranges") or {}
        weekly = load_fatigue.get("weekly") or {}
        recommendation = str(load_fatigue.get("recommendation") or "").strip()
        weekly_tss = weekly.get("current_tss_effective", weekly.get("current_tss", 0.0))
        if latest:
            atl_now = float(latest.get("atl", 0.0))
            tsb_now = float(latest.get("tsb", 0.0))
            atl_high = float(ranges.get("atl_high", 0.0))
            tsb_low = float(ranges.get("tsb_low", 0.0))
            tsb_high = float(ranges.get("tsb_high", 0.0))
            atl_pos = "por encima de lo habitual" if atl_high and atl_now > atl_high else "dentro de lo habitual"
            zone = _load_metrics.form_zone(tsb_now, float(latest.get("ctl", 0.0)))
            lines.append(
                "  - Estado: "
                f"Estado físico (CTL) {float(latest.get('ctl', 0.0)):.1f} · "
                f"Fatiga (ATL) {atl_now:.1f} (máximo habitual {atl_high:.1f}; {atl_pos}) · "
                f"Forma (TSB) {tsb_now:+.1f} = {zone['form_pct']:+.0f} % de tu CTL → zona {zone['emoji']} {zone['label']} "
                f"(rango habitual {tsb_low:+.1f} a {tsb_high:+.1f}) · "
                f"TSS de la semana {float(weekly_tss):.1f}"
            )
        if recommendation:
            lines.append(f"  - Regla aplicada: {recommendation}")
        if load_fatigue.get("warming_up"):
            days_rem = int(load_fatigue.get("warming_up_days_remaining") or 0)
            weeks_rem = max(1, round(days_rem / 7))
            lines.append(
                f"  - ⚙️ Modelo en calibracion ({int(load_fatigue.get('days_with_load') or 0)} dias con datos). "
                f"Los rangos seran fiables en ~{weeks_rem} semana{'s' if weeks_rem != 1 else ''} mas."
            )
    elif not load_fatigue.get("latest"):
        # Sin datos calculados — mostrar diagnóstico para entender el motivo
        load_debug = str(snapshot.get("load_debug") or "").strip()
        if "sin actividades" in load_debug or "usuario nuevo" in load_debug:
            lines.append(
                "  ⚠️ Sin histórico de entrenamientos detectado — "
                "el modelo se calibrará en ~3 semanas una vez se registren actividades en Garmin."
            )
        elif load_debug and load_debug not in ("ok",):
            lines.append(f"  ⚠️ No se pudieron obtener actividades históricas · diagnóstico: {load_debug}")

    if trainings:
        lines.append("- Entrenamientos recientes:")
        for item in trainings[:3]:
            name = item.get("name") or "Actividad"
            day_d = _parse_iso_date_safe(item.get("date"))
            day = day_d.strftime("%d/%m/%Y") if day_d else (item.get("date") or "fecha desconocida")
            lines.append(f"  - {day}: {name}")
    else:
        lines.append("- Entrenamientos recientes: no se encontraron en las ultimas 48h")

    if isinstance(plan_execution_feedback, dict) and plan_execution_feedback.get("adherence_score") is not None:
        score = _safe_float(plan_execution_feedback.get("adherence_score"), 0.0)
        label = str(plan_execution_feedback.get("adherence_label") or "n/d")
        dev_pct = _safe_float(plan_execution_feedback.get("load_deviation_pct"), 0.0) * 100.0
        next_adj = str(plan_execution_feedback.get("next_session_adjustment") or "").strip()
        structured_summary = str(
            ((plan_execution_feedback.get("planned") or {}).get("structured_summary") or "")
        ).strip()
        lines.append(
            f"- Plan vs ejecutado (ayer): adherencia {score:.2f} ({label}) · desviacion carga {dev_pct:+.0f}%"
        )
        if structured_summary:
            lines.append(f"  - Plan de referencia: {structured_summary}")
        block_summary = ((plan_execution_feedback.get("block_feedback") or {}).get("summary") or {})
        if isinstance(block_summary, dict) and block_summary:
            done = int(block_summary.get("completed_blocks") or 0)
            partial = int(block_summary.get("partial_blocks") or 0)
            missed = int(block_summary.get("missed_blocks") or 0)
            total = int(block_summary.get("total_blocks") or max(1, done + partial + missed))
            time_dev_pct = _safe_float(block_summary.get("time_deviation_pct"), 0.0) * 100.0
            lines.append(
                f"  - Bloques: {done}/{total} completos · parciales {partial} · omitidos {missed} · desviación tiempo {time_dev_pct:+.0f}%"
            )
        if next_adj:
            lines.append(f"  - Ajuste sugerido por adherencia: {next_adj}")

    decision_for_user = ""
    decision = str((daily_plan_decision or {}).get("decision") or "").strip().lower()
    if decision:
        # Con plan, la decisión es sobre la sesión de hoy (29/10: "reducir" salía como "rodaje suave" junto a una
        # sesión resultante de 25' en Z3, y "mantener" como "calidad" aunque hoy tocara rodaje).
        decision_for_user = ({
            "maintain": "la sesión del plan, tal cual",
            "reduce": "la sesión del plan, con un 20-30 % menos",
            "easy": "rodaje suave en lugar de la sesión del plan",
            "rest": "descanso",
        } if plan_assigned else {
            "maintain": "calidad",
            "reduce": "rodaje suave",
            "easy": "rodaje suave",
            "rest": "descanso",
        }).get(decision, "rodaje suave")

    if decision_for_user:
        initial_recommendation = f"Elección recomendada hoy: {decision_for_user}."
    elif plan_assigned:
        initial_recommendation = (
            plan_recommendation
            or "Tienes un plan activo. ¿Quieres que adapte la sesion de hoy a ese plan?"
        )
    else:
        initial_recommendation = "No tienes plan asignado. ¿Que quieres hacer hoy?"

    today_session = [str(x) for x in (snapshot.get("today_session_lines") or [])]
    if plan_assigned and today_session:
        # Primero lo que toca hoy según el plan; después, la recomendación sobre esa sesión (29/10).
        lines.extend(["", "## 🗓️ Sesión de hoy", *today_session])
    lines.extend([
        "",
        "## ✅ Recomendación",
        f"- {initial_recommendation}",
    ])
    if decision_for_user and not plan_assigned:
        lines.append("- No tienes plan activo: la recomendación sale de tu estado de carga y recuperación (puedo crearte un plan hacia tu objetivo).")
    lines.extend([
    ])
    if isinstance(daily_plan_decision, dict) and daily_plan_decision.get("decision"):
        decision = str(daily_plan_decision.get("decision") or "").strip().lower()
        decision_label = {
            "maintain": "calidad",
            "reduce": "rodaje suave",
            "easy": "rodaje suave",
            "rest": "descanso",
        }.get(decision, decision or "n/d")
        reason = str(daily_plan_decision.get("reason") or "").strip()
        resulting = str(daily_plan_decision.get("resulting_session") or "").strip()
        log.debug("proactive status: decisión del motor diario %s", decision_label)
        if reason:
            lines.append(f"  - Motivo: {reason}")
        if resulting and not (plan_assigned and decision == "maintain"):  # mantener: la sesión de hoy, ya arriba
            lines.append(f"  - Sesión resultante: {resulting}")
    lines.extend([
        "",
        "## 🎯 Próximo paso",
        "- Si quieres, convierto esta recomendación en sesión concreta para hoy/mañana.",
        "- Fuente: respuesta determinista (sin inferencias numéricas del LLM).",
    ])
    return "\n".join(lines)
