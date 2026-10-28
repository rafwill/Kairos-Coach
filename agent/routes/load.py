"""
agent/routes/load.py
Rutas de carga: TSS de la semana o de un rango, actividades de la semana, tendencia de carga y estado de carga de hoy, TSS recientes por deporte; todo desde load_metrics_daily y activity_tss.
"""

import logging
import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from mcp import ClientSession

from agent.mcp.client import (
    call_tool,
)
from agent import load_metrics as _load_metrics
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _extract_activity_native_load,
    _is_cycling_activity,
    _is_running_non_trail_activity,
    _is_strength_activity,
    _is_trail_activity,
    _try_parse_json,
)
from agent.dates import (
    _extract_iso_date_from_text,
    _extract_iso_date_range_from_text,
    _parse_iso_date_safe,
)
from agent.tool_results import (
    _activity_id_int,
    _extract_activities_list,
    _parse_activities_response,
)
from agent.data_access import (
    _load_metrics_series,
    _load_metrics_view,
    _load_stored_activity_tss,
    _tss_unit_for_stored_row,
)
from agent.garmin_data import (
    _extract_activity_date_iso,
    _get_activity_name_es,
)

log = logging.getLogger(__name__)


async def _build_recent_tss_by_sport_markdown(mcp_session, user_message: str) -> str:
    """Devuelve el TSS guardado (activity_tss) de las últimas N actividades de uno o varios deportes."""
    sports, limit = _parse_recent_tss_sports_request(user_message)
    if not sports:
        return "No pude identificar el deporte solicitado (trail/running/fuerza/ciclismo)."

    activities: list[dict] = []
    end_d = date.today()
    start_d = end_d - timedelta(days=540)
    req_variants = [
        {
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
            "page": 0,
            "page_size": 500,
        },
        {
            "startdate": start_d.isoformat(),
            "enddate": end_d.isoformat(),
        },
    ]
    for args in req_variants:
        try:
            raw = await call_tool(mcp_session, "get_activities_by_date", args)
            parsed = _try_parse_json(raw)
            if parsed is None:
                parsed = raw
            acts = _extract_activities_list(parsed)
            if acts:
                activities = acts
                break
        except (TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, KeyError) as exc:
            log.debug("_build_recent_tss_by_sport_markdown: get_activities_by_date fallo con args=%s: %s", args, exc)
            continue

    def _sport_of(act: dict) -> str | None:
        act_type = act.get("type") or act.get("activityType") or ""
        if _is_trail_activity(act_type):
            return "trail"
        if _is_running_non_trail_activity(act_type):
            return "running"
        if _is_strength_activity(act_type):
            return "strength"
        if _is_cycling_activity(act_type):
            return "cycling"
        return None

    stored = _load_stored_activity_tss(start_d.isoformat())
    last_stored_iso = max((str(r.get("start_date") or "") for r in stored.values()), default="")

    rows: list[dict[str, Any]] = []
    for act in activities:
        if not isinstance(act, dict):
            continue
        sport = _sport_of(act)
        if sport not in sports:
            continue
        d_iso = _extract_activity_date_iso(act)
        if not d_iso:
            continue
        stored_row = stored.get(_activity_id_int(act))
        if stored_row is None and d_iso < last_stored_iso:
            continue  # anterior a la línea base: sin TSS guardado, no se inventa
        # Carga Garmin = activityTrainingLoad (la guardada en activity_tss.native_load). No usar
        # extract_training_load_tss: en bici con potencia devuelve el TSS de Garmin, no su carga.
        native_load = (
            stored_row.get("native_load") if stored_row is not None and stored_row.get("native_load") is not None
            else _extract_activity_native_load(act)
        )
        rows.append({
            "date": d_iso,
            "sport": sport,
            "name": str(act.get("name") or act.get("activityName") or "Actividad").strip() or "Actividad",
            "tss": float(stored_row["tss"]) if stored_row is not None else None,
            "native_load": float(native_load) if native_load is not None else None,
        })

    rows.sort(key=lambda r: (r["date"], r["name"].lower()), reverse=True)
    rows = rows[:limit]

    sport_labels = {"trail": "trail", "running": "running", "strength": "fuerza", "cycling": "ciclismo"}
    sports_label = ", ".join(sport_labels.get(s, s) for s in sports)

    if not rows:
        return "\n".join([
            "## 🧭 Resumen",
            f"No encontré actividades de {sports_label} con TSS calculado por Kairos.",
            "",
            "## 🎯 Próximo paso",
            "- Si acabas de sincronizar, espera a que Kairos calcule la carga y vuelve a preguntar.",
        ])

    calculated = [r for r in rows if r["tss"] is not None]
    pending = len(rows) - len(calculated)

    lines = [
        "## 🧭 Resumen",
        f"TSS calculado por Kairos de tus últimas {len(rows)} actividades de {sports_label}.",
        "",
        "## 📊 Métricas clave",
        "| Deporte | Actividades | TSS medio | TSS máximo |",
        "|---|---:|---:|---:|",
    ]
    for sport in sports:
        values = [r["tss"] for r in calculated if r["sport"] == sport]
        if values:
            lines.append(
                f"| {sport_labels.get(sport, sport)} | {len(values)} | {sum(values) / len(values):.1f} | {max(values):.1f} |"
            )
    lines.extend([
        "",
        "| Fecha | Deporte | Actividad | TSS | Carga Garmin |",
        "|---|---|---|---:|---:|",
    ])
    for r in rows:
        tss_txt = f"{r['tss']:.1f}" if r["tss"] is not None else "pendiente"
        load_txt = f"{r['native_load']:.1f}" if r["native_load"] is not None else "—"
        row_d = _parse_iso_date_safe(r["date"])
        date_txt = row_d.strftime("%d/%m/%Y") if row_d else str(r["date"])
        lines.append(f"| {date_txt} | {sport_labels.get(r['sport'], r['sport'])} | {r['name']} | {tss_txt} | {load_txt} |")

    lines.extend([
        "",
        "## ✅ Recomendación",
        "- La columna TSS es la carga de Kairos (la que alimenta ATL/CTL/TSB). La carga Garmin es solo informativa: no es TSS.",
    ])
    if pending:
        lines.append(f"- {pending} actividad(es) recientes aún sin TSS: se calculan en la próxima sincronización de carga.")
    lines.extend([
        "",
        "## 🎯 Próximo paso",
        "- Si quieres, te analizo en detalle cualquiera de estas sesiones: dime su fecha.",
        "- Fuente: TSS guardado por actividad (activity_tss) + listado de Garmin MCP.",
    ])
    return "\n".join(lines)


def _resolve_load_parameters_effective_date(profile: dict | None) -> date | None:
    """Devuelve la fecha más reciente de cambio de parámetros que afectan al TSS.

    Política: no recalcular histórico anterior a esta fecha para evitar
    alterar resultados ya consolidados con parámetros previos.
    """
    if not isinstance(profile, dict):
        return None

    perf = profile.get("performance") if isinstance(profile.get("performance"), dict) else {}
    candidates: list[Any] = [
        perf.get("performance_params_updated_at"),
        perf.get("running_threshold_pace_date"),
        perf.get("cycling_ftp_date"),
        perf.get("hr_zones_date"),
        perf.get("heart_rate_zones_date"),
        perf.get("hr_profile_date"),
        perf.get("hr_max_date"),
        perf.get("hr_rest_date"),
    ]

    parsed = [d for d in (_parse_iso_date_safe(v) for v in candidates) if d is not None]
    if not parsed:
        return None
    return max(parsed)


def _build_load_trend_table(series: list[dict], mode: str = "weeks") -> str:
    """Genera una tabla Markdown con la tendencia de carga/fatiga.

    Args:
        series: lista de dicts con {date, tss, atl, ctl, tsb} ordenada por fecha asc.
        mode: "weeks" (últimas 8 semanas) o "months" (últimos 3 meses).

    Returns:
        Tabla en Markdown con encabezado, filas por periodo y leyenda de estado.
    """
    if not series:
        return "Sin datos de carga/fatiga disponibles. Inicia una sesión para que el sistema los calcule."

    def _row_status(tsb: float, ctl: float) -> str:
        """Zona de Forma de la fila (regla única de Kairos: TSB en % del CTL)."""
        zone = _load_metrics.form_zone(tsb, ctl)
        return f"{zone['emoji']} {zone['label']} ({zone['form_pct']:+.0f} %)"

    def _fmt_date_range(start_iso: str, end_iso: str) -> str:
        try:
            s = datetime.fromisoformat(start_iso).strftime("%d/%m")
            e = datetime.fromisoformat(end_iso).strftime("%d/%m")
            return f"{s}–{e}"
        except (TypeError, ValueError):
            return f"{start_iso}–{end_iso}"

    def _fmt_month(iso: str) -> str:
        _MONTHS_SHORT = {
            "01": "Ene", "02": "Feb", "03": "Mar", "04": "Abr",
            "05": "May", "06": "Jun", "07": "Jul", "08": "Ago",
            "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dic",
        }
        parts = iso.split("-")
        if len(parts) >= 2:
            return f"{_MONTHS_SHORT.get(parts[1], parts[1])} {parts[0]}"
        return iso

    # Ordenar por fecha ascendente
    sorted_series = sorted(series, key=lambda x: str(x.get("date") or ""))

    if mode == "months":
        # Agregar por mes calendario (últimos 3 meses)
        buckets: dict[str, list[dict]] = {}
        for row in sorted_series:
            d_iso = str(row.get("date") or "")
            month_key = d_iso[:7]  # YYYY-MM
            buckets.setdefault(month_key, []).append(row)

        month_keys = sorted(buckets)[-3:]
        if not month_keys:
            return "Sin datos suficientes para vista mensual."

        header = (
            "| Mes | TSS total | CTL fin (Estado físico) | ATL fin (Fatiga) | TSB fin (Forma) | Zona (% del CTL) |\n"
            "|---|---:|---:|---:|---:|---|\n"
        )
        rows_md: list[str] = []
        for mk in month_keys:
            month_rows = buckets[mk]
            tss_total = round(sum(float(r.get("tss") or 0.0) for r in month_rows), 1)
            last = month_rows[-1]
            atl = float(last.get("atl") or 0.0)
            ctl = float(last.get("ctl") or 0.0)
            tsb = float(last.get("tsb") or 0.0)
            estado = _row_status(tsb, ctl)
            rows_md.append(
                f"| {_fmt_month(mk)} | {tss_total:.1f} | {ctl:.1f} | {atl:.1f} | {tsb:+.1f} | {estado} |"
            )

        return (
            "## 📅 Tendencia de carga mensual (últimos 3 meses)\n\n"
            + header
            + "\n".join(rows_md)
            + "\n\n"
            + "_TSS: carga de sesión · CTL (Estado físico): estado físico crónico · ATL (Fatiga): fatiga (7d) · TSB (Forma): CTL de ayer − ATL de ayer_\n"
            + f"_{_load_metrics.FORM_LEGEND}_"
        )

    # Vista semanal: últimas 8 semanas naturales lunes→domingo
    today = date.today()
    _week_mon = today - timedelta(days=today.weekday())  # lunes de esta semana
    weeks: list[tuple[str, str, list[dict]]] = []
    for w in range(7, -1, -1):
        mon = _week_mon - timedelta(weeks=w)
        sun = mon + timedelta(days=6)
        week_rows = [
            r for r in sorted_series
            if mon.isoformat() <= str(r.get("date") or "") <= sun.isoformat()
        ]
        weeks.append((mon.isoformat(), sun.isoformat(), week_rows))

    # Descartar semanas vacías al principio
    first_non_empty = next((i for i, (_, _, wr) in enumerate(weeks) if wr), 0)
    weeks = weeks[first_non_empty:]
    if not weeks:
        return "Sin datos suficientes para vista semanal."

    header = (
        "| Semana | TSS | CTL (Estado físico) | ATL (Fatiga) | TSB (Forma) | Zona (% del CTL) |\n"
        "|---|---:|---:|---:|---:|---|\n"
    )
    rows_md = []
    for start_iso, end_iso, week_rows in weeks:
        tss_sum = round(sum(float(r.get("tss") or 0.0) for r in week_rows), 1)
        if week_rows:
            last = week_rows[-1]
            atl = float(last.get("atl") or 0.0)
            ctl = float(last.get("ctl") or 0.0)
            tsb = float(last.get("tsb") or 0.0)
        else:
            atl = ctl = tsb = 0.0
        estado = _row_status(tsb, ctl)
        rows_md.append(
            f"| {_fmt_date_range(start_iso, end_iso)} | {tss_sum:.1f} | {ctl:.1f} | {atl:.1f} | {tsb:+.1f} | {estado} |"
        )

    # Nota de warm-up: si la primera semana con datos tiene CTL (Estado físico) < 15, el modelo aún se está calibrando
    first_ctl_values = [
        float(r.get("ctl") or 0.0)
        for (_, _, wr) in weeks
        for r in wr
        if float(r.get("ctl") or 0.0) > 0
    ]
    warmup_note = (
        "\n_⚙️ Las primeras semanas reflejan el arranque del modelo (CTL/Estado físico bajo), no necesariamente una sobrecarga real._"
        if first_ctl_values and first_ctl_values[0] < 15.0
        else ""
    )

    return (
        "## 📊 Tendencia de carga semanal (últimas 8 semanas)\n\n"
        + header
        + "\n".join(rows_md)
        + "\n\n"
        + "_TSS: carga de sesión · CTL (Estado físico): estado físico crónico · ATL (Fatiga): fatiga · TSB (Forma): CTL de ayer − ATL de ayer_\n"
        + f"_{_load_metrics.FORM_LEGEND}_"
        + warmup_note
    )


def _format_load_fatigue_summary(load_metrics: dict | None) -> str:
    """Genera resumen textual corto para el bloque proactivo."""
    if not isinstance(load_metrics, dict) or not load_metrics.get("latest"):
        return "sin datos suficientes"
    latest = load_metrics.get("latest") or {}
    weekly = load_metrics.get("weekly") or {}
    action = str(load_metrics.get("action") or "carga estable")
    weekly_tss = weekly.get("current_tss_effective", weekly.get("current_tss", 0.0))
    try:
        return (
            f"TSS hoy {float(latest.get('tss', 0.0)):.1f} · "
            f"CTL (Estado físico) {float(latest.get('ctl', 0.0)):.1f} · "
            f"ATL (Fatiga) {float(latest.get('atl', 0.0)):.1f} · "
            f"TSB (Forma) {float(latest.get('tsb', 0.0)):.1f} · "
            f"Semana {float(weekly_tss):.1f} TSS ({action})"
        )
    except (TypeError, ValueError):
        return "sin datos suficientes"


def _fill_zero_days_with_activity_tss(
    tss_by_day: dict[str, float], activity_tss_by_day: dict[str, float], dates: list[date],
) -> list[str]:
    """Días que faltan en la serie diaria o están a 0 (sin cierre): se completan con la suma de `activity_tss`
    de ese día. Modifica `tss_by_day` y devuelve los días completados."""
    filled: list[str] = []
    for d in dates:
        d_iso = d.isoformat()
        fallback_tss = activity_tss_by_day.get(d_iso)
        if fallback_tss is None:
            continue
        existing_tss = tss_by_day.get(d_iso)
        if existing_tss is not None and float(existing_tss) > 0.0:
            continue
        tss_by_day[d_iso] = round(float(fallback_tss), 1)
        filled.append(d_iso)
    return filled


def _compute_effective_week_tss_from_series_and_activities(
    *,
    series: list[dict],
    activities: list[dict],
    week_dates: list[date],
) -> tuple[float, bool]:
    """Calcula TSS semanal efectivo con criterio unificado.

    Prioriza la serie canónica diaria y solo usa actividad para completar
    días sin cierre (ausentes o a cero).
    """
    tss_by_day: dict[str, float] = {}
    for row in series or []:
        d_iso = str((row or {}).get("date") or "")
        if not d_iso:
            continue
        tss_by_day[d_iso] = round(float((row or {}).get("tss") or 0.0), 1)

    # TSS por actividad: solo el guardado en activity_tss (no se recalcula ni se usa la carga nativa).
    week_start_iso = min((d.isoformat() for d in week_dates), default=date.today().isoformat())
    stored_tss = _load_stored_activity_tss(week_start_iso)

    activity_tss_by_day: dict[str, float] = {}
    for act in activities or []:
        if not isinstance(act, dict):
            continue
        d_iso = _extract_activity_date_iso(act)
        if not d_iso:
            continue
        stored_row = stored_tss.get(_activity_id_int(act))
        if stored_row is None:
            continue
        activity_tss_by_day[d_iso] = round(activity_tss_by_day.get(d_iso, 0.0) + float(stored_row.get("tss") or 0.0), 1)

    used_activity_fallback = bool(_fill_zero_days_with_activity_tss(tss_by_day, activity_tss_by_day, week_dates))

    total = round(sum(tss_by_day.get(d.isoformat(), 0.0) for d in week_dates), 1)
    return total, used_activity_fallback


def _build_load_trend_markdown(profile: dict, user_message: str) -> str:
    """Construye respuesta determinista de tendencia ATL/CTL/TSB sin LLM."""
    text = (user_message or "").strip().lower()
    m_semanas = re.search(r"(\d{1,3})\s*semanas?", text)
    m_dias = re.search(r"(\d{1,3})\s*d[ií]as", text)

    load_metrics = _load_metrics_view(profile)
    series = load_metrics.get("series") or []
    if not series:
        return "\n".join([
            "## 🧭 Resumen",
            "No hay serie de carga/fatiga disponible todavía.",
            "",
            "## 📊 Métricas clave",
            "No aplica en esta consulta.",
            "",
            "## ✅ Recomendación",
            "- Reinicia la sesión o consulta `/carga` para generar/mostrar la serie persistida.",
            "",
            "## 🎯 Próximo paso",
            "- Si quieres, comparo semana actual vs semana previa con TSS y spike.",
        ])

    ranges = load_metrics.get("ranges") or {}
    tsb_low = float(ranges.get("tsb_low") or -10.0)
    tsb_high = float(ranges.get("tsb_high") or 5.0)
    atl_high = float(ranges.get("atl_high") or 9999.0)

    # Índice por fecha para búsqueda rápida
    day_lookup: dict[str, dict] = {}
    for row in series:
        d_iso = str(row.get("date") or "")
        if d_iso:
            day_lookup[d_iso] = row

    today_d = date.today()
    today_row = day_lookup.get(today_d.isoformat()) or series[-1]
    today_atl = float(today_row.get("atl") or 0.0)
    today_ctl = float(today_row.get("ctl") or 0.0)
    today_tsb = float(today_row.get("tsb") or 0.0)
    going_to_fatigue = today_tsb < tsb_low or today_atl > atl_high
    status_text = "fatiga" if going_to_fatigue else "disponibilidad"
    recommendation = (
        "- Señal de fatiga: prioriza descarga/rodaje suave y reevalúa mañana."
        if going_to_fatigue
        else "- Señal de disponibilidad: puedes sostener carga aeróbica o calidad controlada."
    )

    if m_semanas:
        # Vista semana a semana: valores al cierre (domingo) de cada semana
        n_weeks = max(1, min(int(m_semanas.group(1)), 16))
        current_week_mon = today_d - timedelta(days=today_d.weekday())

        table_rows: list[tuple[str, str, float, float, float]] = []
        for w in range(n_weeks, 0, -1):
            week_mon = current_week_mon - timedelta(weeks=w)
            week_sun = week_mon + timedelta(days=6)
            # Buscar el último día con datos en esa semana (domingo → lunes)
            week_row = None
            actual_date = None
            for offset in range(7):
                candidate = week_sun - timedelta(days=offset)
                if candidate < week_mon:
                    break
                r = day_lookup.get(candidate.isoformat())
                if r:
                    week_row = r
                    actual_date = candidate
                    break
            if not week_row:
                continue
            label = "Sem. pasada" if w == 1 else f"Hace {w} sem."
            date_str = actual_date.strftime("%d/%m") if actual_date else week_sun.strftime("%d/%m")
            table_rows.append((
                label,
                date_str,
                float(week_row.get("atl") or 0.0),
                float(week_row.get("ctl") or 0.0),
                float(week_row.get("tsb") or 0.0),
            ))

        # Añadir fila de hoy
        table_rows.append((
            "**Hoy**",
            today_d.strftime("%d/%m"),
            today_atl, today_ctl, today_tsb,
        ))

        lines = [
            "## 🧭 Resumen",
            f"Evolución semana a semana — valores al cierre de cada semana (domingo) vs hoy: **{status_text}**.",
            "",
            "## 📊 Métricas clave",
            "| Período | Cierre sem. | ATL (Fatiga) | CTL (Estado físico) | TSB (Forma) |",
            "|---|:---:|---:|---:|---:|",
        ]
        for label, date_str, atl, ctl, tsb in table_rows:
            lines.append(f"| {label} | {date_str} | {atl:.1f} | {ctl:.1f} | {tsb:+.1f} |")

        lines += [
            "",
            f"_Rangos de referencia: TSB objetivo {tsb_low:.1f}..{tsb_high:.1f} · ATL alto > {atl_high:.1f}_",
            "",
            "## ✅ Recomendación",
            recommendation,
            "",
            "## 🎯 Próximo paso",
            "- Si quieres, te propongo la sesión de hoy según esta tendencia.",
            "- Fuente: respuesta determinista (serie load_metrics persistida, sin LLM).",
        ]
        return "\n".join(lines)

    # Vista por días: comparación primer día vs hoy
    days_back = 14
    if m_dias:
        try:
            days_back = int(m_dias.group(1))
        except ValueError:
            days_back = 14
    days_back = max(7, min(days_back, 120))

    tail = series[-days_back:]
    first = tail[0]
    first_date = str(first.get("date") or "")
    try:
        first_label = date.fromisoformat(first_date).strftime("%d/%m")
    except ValueError:
        first_label = "Inicio"
    last_label = today_d.strftime("%d/%m")

    def _delta(a: float, b: float) -> tuple[float, str]:
        d = round(b - a, 1)
        return (d, "↗") if d > 0.2 else (d, "↘") if d < -0.2 else (d, "→")

    atl0 = float(first.get("atl") or 0.0)
    ctl0 = float(first.get("ctl") or 0.0)
    tsb0 = float(first.get("tsb") or 0.0)
    d_atl, a_atl = _delta(atl0, today_atl)
    d_ctl, a_ctl = _delta(ctl0, today_ctl)
    d_tsb, a_tsb = _delta(tsb0, today_tsb)

    return "\n".join([
        "## 🧭 Resumen",
        f"Tendencia de los últimos {days_back} días: vas hacia **{status_text}**.",
        "",
        "## 📊 Métricas clave",
        f"| Métrica | {first_label} | {last_label} | Delta | Tendencia |",
        "|---|---:|---:|---:|:---:|",
        f"| ATL (Fatiga) | {atl0:.1f} | {today_atl:.1f} | {d_atl:+.1f} | {a_atl} |",
        f"| CTL (Estado físico) | {ctl0:.1f} | {today_ctl:.1f} | {d_ctl:+.1f} | {a_ctl} |",
        f"| TSB (Forma) | {tsb0:.1f} | {today_tsb:.1f} | {d_tsb:+.1f} | {a_tsb} |",
        "",
        f"_Rangos de referencia: TSB objetivo {tsb_low:.1f}..{tsb_high:.1f} · ATL alto > {atl_high:.1f}_",
        "",
        "## ✅ Recomendación",
        recommendation,
        "",
        "## 🎯 Próximo paso",
        "- Si quieres, te propongo la sesión de hoy según esta tendencia.",
        "- Fuente: respuesta determinista (serie load_metrics persistida, sin LLM).",
    ])


def _build_today_load_status_markdown(profile: dict) -> str:
    """Construye estado determinista de hoy con TSS/ATL/CTL/TSB y regla aplicada."""
    load_metrics = _load_metrics_view(profile)
    series = load_metrics.get("series") or []

    if not series:
        return "\n".join([
            "## 🧭 Resumen",
            "No hay serie de carga/fatiga disponible todavía.",
            "",
            "## 📊 Métricas clave",
            "No aplica en esta consulta.",
            "",
            "## ✅ Recomendación",
            "- Reinicia la sesión para generar la serie o consulta `/carga` para verificar disponibilidad.",
            "",
            "## 🎯 Próximo paso",
            "- Si quieres, comparo semana actual vs anterior y detector de spike >20%.",
        ])

    load_fatigue = load_metrics

    latest = (load_fatigue.get("latest") or series[-1] or {})
    latest_date = str(latest.get("date") or "")
    weekly = load_fatigue.get("weekly") or {}
    action = str(load_fatigue.get("action") or "carga estable")

    try:
        tss = float(latest.get("tss") or 0.0)
        atl = float(latest.get("atl") or 0.0)
        ctl = float(latest.get("ctl") or 0.0)
        tsb = float(latest.get("tsb") or 0.0)
        week_tss = float(weekly.get("current_tss") or 0.0)
    except (TypeError, ValueError):
        tss = atl = ctl = tsb = week_tss = 0.0

    try:
        date_label = datetime.fromisoformat(latest_date).strftime("%d/%m/%Y") if latest_date else "sin fecha"
    except ValueError:
        date_label = latest_date or "sin fecha"

    zone = _load_metrics.form_zone(tsb, ctl)
    rule_text = f"{zone['emoji']} {zone['label']} ({zone['form_pct']:+.0f} % del CTL) · {action}"

    return "\n".join([
        "## 🧭 Resumen",
        "Estado de carga/fatiga de hoy resuelto desde serie persistida y regla del modelo.",
        "",
        "## 📊 Métricas clave",
        "| Métrica | Valor | Fuente |",
        "|---|---:|---|",
        f"| Fecha de referencia | {date_label} | load_metrics_daily/series |",
        f"| TSS del día | {tss:.1f} | load_metrics(series) |",
        f"| ATL (Fatiga) | {atl:.1f} | modelo EWMA |",
        f"| CTL (Estado físico) | {ctl:.1f} | modelo EWMA |",
        f"| TSB (Forma) | {tsb:.1f} | CTL de ayer − ATL de ayer |",
        f"| TSS semanal actual | {week_tss:.1f} | weekly.current_tss |",
        f"| Zona de Forma | {rule_text} | zonas de Friel (% del CTL) |",
        "",
        "## ✅ Recomendación",
        f"- Regla vigente hoy: **{action}**.",
        "",
        "## 🎯 Próximo paso",
        "- Si quieres, traduzco esta regla a una propuesta concreta: calidad, rodaje suave o descanso.",
        "- Fuente: respuesta determinista (serie de carga y reglas del modelo, sin inferencias del LLM).",
    ])


_RECENT_TSS_SPORT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("trail", r"\btrail\b"),
    ("running", r"\brunning\b|\bcorrer\b|\bcarrera\b"),
    ("strength", r"\bfuerza\b|\bstrength\b|\bgym\b|\bpesas\b"),
    ("cycling", r"\bciclismo\b|\bcycling\b|\bbici\b|\bbike\b"),
)


def _parse_recent_tss_sports_request(user_message: str) -> tuple[list[str], int]:
    """Extrae deportes (uno o varios) y tamaño N para consultas tipo
    'TSS de mis 20 últimos entrenamientos de trail, fuerza y running'.
    """
    text = (user_message or "").strip().lower()
    if not text:
        return [], 20

    sports = [sport for sport, pattern in _RECENT_TSS_SPORT_PATTERNS if re.search(pattern, text)]

    n_match = re.search(r"\b(\d{1,3})\b", text)
    n = int(n_match.group(1)) if n_match else 20
    n = max(1, min(n, 50))
    return sports, n


def _resolve_week_window(user_message: str | None, today_d: date) -> tuple[date, date]:
    """Resuelve ventana semanal natural (lunes→domingo) desde el mensaje.

    - "semana pasada" / "semana anterior" / "semana previa" → semana anterior.
    - "hace N semanas" / "N semanas atrás" → N semanas hacia atrás.
    - Fecha explícita → semana que contiene esa fecha.
    - Sin referencia → semana actual (lunes→hoy).
    """
    target_d = today_d
    msg_lower = (user_message or "").lower() if isinstance(user_message, str) else ""

    # Rango explícito: priorizar siempre el intervalo literal pedido por usuario.
    if isinstance(user_message, str) and user_message.strip():
        date_range = _extract_iso_date_range_from_text(user_message, today_d=today_d)
        if date_range:
            start_iso, end_iso = date_range
            try:
                start_d = date.fromisoformat(start_iso)
                end_d = date.fromisoformat(end_iso)
                return start_d, min(end_d, today_d)
            except ValueError:
                pass

    # Si el usuario compara "esta semana" vs "semana pasada",
    # la semana consultada debe seguir siendo la actual.
    compares_current_vs_previous = (
        "esta semana" in msg_lower
        and re.search(r'\bsemana\s+(pasada|anterior|previa)\b', msg_lower)
    )

    # Semana pasada / anterior / previa
    if compares_current_vs_previous:
        target_d = today_d
    elif re.search(r'\bsemana\s+(pasada|anterior|previa)\b', msg_lower):
        target_d = today_d - timedelta(days=7)
    # Hace N semanas / N semanas atrás
    elif m_n := re.search(r'hace\s+(\d+)\s+semanas?|(\d+)\s+semanas?\s+atr[aá]s', msg_lower):
        n = int(next(g for g in m_n.groups() if g is not None))
        target_d = today_d - timedelta(days=7 * n)
    elif msg_lower:
        target_iso = _extract_iso_date_from_text(user_message)
        if target_iso:
            try:
                parsed = date.fromisoformat(target_iso)
                if parsed <= today_d:
                    target_d = parsed
            except ValueError:
                pass

    week_start = target_d - timedelta(days=target_d.weekday())
    week_end = week_start + timedelta(days=6)

    # Para la semana actual, mantener corte en hoy.
    current_week_start = today_d - timedelta(days=today_d.weekday())
    if week_start == current_week_start and week_end > today_d:
        week_end = today_d

    return week_start, week_end


async def _build_current_week_tss_markdown(mcp_session, profile: dict, user_message: str | None = None) -> str:
    """Construye un resumen determinista de TSS semanal (semana natural).

    Incluye:
    - TSS por día de la semana natural solicitada.
    - Actividades reales Garmin registradas en ese rango (sin inferencias del LLM).
    """
    # Prioriza datos frescos de DB-first para evitar respuestas con snapshots desactualizados.
    # Usamos una ventana amplia para poder comparar semana actual vs previa.
    series = _load_metrics_series()
    if not series:
        return (
            "## 🧭 Resumen\n"
            "No hay serie de carga/fatiga disponible para esta semana.\n\n"
            "## 📊 Métricas clave\n"
            "| Métrica | Valor | Fuente |\n"
            "|---|---|---|\n"
            "| Serie diaria TSS | No disponible | load_metrics_daily |\n\n"
            "## ✅ Recomendación\n"
            "- Ejecutar sincronización/cálculo para poblar TSS diario.\n\n"
            "## 🎯 Próximo paso\n"
            "- Vuelve a consultar /carga semanal después de sincronizar."
        )

    today_d = date.today()
    week_start, week_end = _resolve_week_window(user_message, today_d)
    current_week_start = today_d - timedelta(days=today_d.weekday())
    weeks_ago = (current_week_start - week_start).days // 7

    # Etiqueta de la semana consultada
    if weeks_ago == 0:
        week_label = f"Esta semana ({week_start.strftime('%d/%m')} → {(week_start + timedelta(days=6)).strftime('%d/%m')})"
    elif weeks_ago == 1:
        week_label = f"Semana pasada ({week_start.strftime('%d/%m')} → {(week_start + timedelta(days=6)).strftime('%d/%m')})"
    else:
        week_label = f"Hace {weeks_ago} semanas ({week_start.strftime('%d/%m')} → {(week_start + timedelta(days=6)).strftime('%d/%m')})"

    # Semana de comparación: usar siempre la semana inmediatamente anterior
    # al rango consultado para mantener coherencia histórica.
    comp_start = week_start - timedelta(days=7)
    comp_end = week_start - timedelta(days=1)
    comp_label = f"Semana anterior ({comp_start.strftime('%d/%m')} → {comp_end.strftime('%d/%m')})"

    week_dates = [week_start + timedelta(days=i) for i in range((week_end - week_start).days + 1)]
    comp_dates = [comp_start + timedelta(days=i) for i in range((comp_end - comp_start).days + 1)]
    # Ventana unificada para leer serie/fetch Garmin sin invertir fechas cuando
    # se consulta una semana histórica (p. ej. "semana pasada").
    range_start = min(week_start, comp_start)
    range_end = max(week_end, comp_end)
    prev_week_dates = comp_dates

    tss_by_day: dict[str, float] = {}
    tss_source_by_day: dict[str, str] = {}
    for row in series:
        d_iso = str(row.get("date") or "")
        if not d_iso:
            continue
        try:
            d_obj = date.fromisoformat(d_iso)
        except ValueError:
            continue
        if range_start <= d_obj <= range_end:
            tss_by_day[d_iso] = round(float(row.get("tss") or 0.0), 1)
            tss_source_by_day[d_iso] = "load_metrics_daily"

    activities: list[dict] = []
    req_variants = [
        {
            "start_date": range_start.isoformat(),
            "end_date": range_end.isoformat(),
            "page": 0,
            "page_size": 200,
        },
        {
            "startdate": range_start.isoformat(),
            "enddate": range_end.isoformat(),
        },
    ]
    for args in req_variants:
        try:
            raw = await call_tool(mcp_session, "get_activities_by_date", args)
            parsed = _try_parse_json(raw)
            if parsed is None:
                parsed = raw
            acts = _extract_activities_list(parsed)
            if acts:
                activities = acts
                break
        except (TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, KeyError) as exc:
            log.debug("_build_current_week_tss_markdown: get_activities_by_date fallo con args=%s: %s", args, exc)
            continue

    stored_tss = _load_stored_activity_tss(range_start.isoformat())
    act_rows: list[tuple[date, str, str, float | None, str, str | None, str]] = []
    activity_tss_by_day: dict[str, float] = {}
    activity_type_tss_by_day: dict[str, dict[str, float]] = {}

    def _resolve_tss_bucket(activity: dict, stored_row: dict | None) -> str:
        # Unidad según el modelo guardado (rTSS running, hrTSS por FC, TSS potencia/metabólico).
        if stored_row is not None:
            return _tss_unit_for_stored_row(stored_row)
        return "hrTSS" if _is_strength_activity(activity.get("type") or activity.get("activityType") or "") else "TSS"

    for act in activities:
        if not isinstance(act, dict):
            continue
        d_iso = _extract_activity_date_iso(act)
        if not d_iso:
            continue
        try:
            d_obj = date.fromisoformat(d_iso)
        except ValueError:
            continue
        if not (range_start <= d_obj <= range_end):
            continue
        name = str(act.get("name") or act.get("activityName") or "Actividad").strip() or "Actividad"
        sport = _get_activity_name_es(act.get("type") or act.get("activityType") or "") or "Actividad"
        tss_source = "pendiente de cálculo"
        tss_label: str | None = None
        act_tss = None
        stored_row = stored_tss.get(_activity_id_int(act))
        if stored_row is not None:
            act_tss = float(stored_row.get("tss") or 0.0)
            tss_label = str(stored_row.get("method") or "")
            tss_source = "activity_tss"
        if week_start <= d_obj <= week_end:
            bucket = _resolve_tss_bucket(act, stored_row)
            display_bucket = bucket
            act_rows.append((
                d_obj,
                sport,
                name,
                round(float(act_tss), 1) if act_tss is not None else None,
                tss_source,
                tss_label,
                bucket,
                display_bucket,
            ))
        if act_tss is not None:
            activity_tss_by_day[d_iso] = round(activity_tss_by_day.get(d_iso, 0.0) + float(act_tss), 1)
            if week_start <= d_obj <= week_end:
                bucket = _resolve_tss_bucket(act, stored_row)
                per_day = activity_type_tss_by_day.setdefault(d_iso, {"rTSS": 0.0, "hrTSS": 0.0, "TSS": 0.0})
                per_day[bucket] = round(per_day.get(bucket, 0.0) + float(act_tss), 1)
    act_rows.sort(key=lambda x: (x[0], x[1].lower(), x[2].lower()))

    filled = _fill_zero_days_with_activity_tss(tss_by_day, activity_tss_by_day, prev_week_dates + week_dates)
    for d_iso in filled:
        tss_source_by_day[d_iso] = "activity_tss_fallback"
    used_activity_fallback = bool(filled)

    current_week_tss = round(sum(tss_by_day.get(d.isoformat(), 0.0) for d in week_dates), 1)
    previous_week_tss = round(sum(tss_by_day.get(d.isoformat(), 0.0) for d in prev_week_dates), 1)
    if previous_week_tss > 0.0:
        delta_pct = round(((current_week_tss - previous_week_tss) / previous_week_tss) * 100.0, 1)
    else:
        delta_pct = None

    # Normaliza desglose rTSS/hrTSS/TSS al TSS diario mostrado para mantener
    # consistencia entre "Desglose por tipo" y TSS semanal.
    day_type_breakdown: dict[str, dict[str, float]] = {}
    tss_breakdown = {"rTSS": 0.0, "hrTSS": 0.0, "TSS": 0.0}
    for d in week_dates:
        d_iso = d.isoformat()
        day_tss = float(tss_by_day.get(d_iso, 0.0) or 0.0)
        raw = activity_type_tss_by_day.get(d_iso, {"rTSS": 0.0, "hrTSS": 0.0, "TSS": 0.0})
        raw_r = float(raw.get("rTSS", 0.0) or 0.0)
        raw_h = float(raw.get("hrTSS", 0.0) or 0.0)
        raw_t = float(raw.get("TSS", 0.0) or 0.0)
        raw_total = raw_r + raw_h + raw_t

        if raw_total > 0.0:
            if day_tss > 0.0:
                scale = day_tss / raw_total
                comp = {
                    "rTSS": round(raw_r * scale, 1),
                    "hrTSS": round(raw_h * scale, 1),
                    "TSS": round(raw_t * scale, 1),
                }
                drift = round(day_tss - (comp["rTSS"] + comp["hrTSS"] + comp["TSS"]), 1)
                if abs(drift) > 0.0:
                    main_unit = max(comp, key=lambda k: comp[k])
                    comp[main_unit] = round(comp[main_unit] + drift, 1)
            else:
                comp = {"rTSS": round(raw_r, 1), "hrTSS": round(raw_h, 1), "TSS": round(raw_t, 1)}
        else:
            comp = {"rTSS": 0.0, "hrTSS": 0.0, "TSS": round(day_tss, 1)}

        day_type_breakdown[d_iso] = comp
        tss_breakdown["rTSS"] = round(tss_breakdown["rTSS"] + comp["rTSS"], 1)
        tss_breakdown["hrTSS"] = round(tss_breakdown["hrTSS"] + comp["hrTSS"], 1)
        tss_breakdown["TSS"] = round(tss_breakdown["TSS"] + comp["TSS"], 1)

    spike_alert = bool(previous_week_tss > 0.0 and current_week_tss > (previous_week_tss * 1.2))
    has_week_end_load_row = week_end.isoformat() in tss_by_day and tss_source_by_day.get(week_end.isoformat()) == "load_metrics_daily"
    is_current_week = week_end == today_d

    # CTL/ATL/TSB al cierre de cada semana — misma fuente que /carga
    def _week_end_load(end_d: date) -> tuple[float, float, float]:
        for row in reversed(series):
            d_iso = str(row.get("date") or "")
            if d_iso and d_iso <= end_d.isoformat():
                return float(row.get("ctl") or 0), float(row.get("atl") or 0), float(row.get("tsb") or 0)
        return 0.0, 0.0, 0.0

    def _wstatus(tsb: float, ctl: float) -> str:
        # Misma regla que /carga y el estado de arranque: zona de Forma en % del CTL.
        zone = _load_metrics.form_zone(tsb, ctl)
        return f"{zone['emoji']} {zone['label']} ({zone['form_pct']:+.0f} %)"

    w_ctl, w_atl, w_tsb = _week_end_load(week_end)
    c_ctl, c_atl, c_tsb = _week_end_load(comp_end)

    weekday_es = {0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"}

    comp_tss_str = f"{previous_week_tss:.1f}"
    if delta_pct is not None:
        comp_tss_str += f" ({delta_pct:+.1f}%{'  ⚠️ spike' if spike_alert else ''})"

    week_span = f"{week_start.strftime('%d/%m/%Y')} → {week_end.strftime('%d/%m/%Y')}"
    delta_pct_text = f"{delta_pct:+.1f}%" if delta_pct is not None else "N/A"
    spike_text = "SI" if spike_alert else "NO"

    lines = [
        "## 🧭 Resumen",
        "Datos de la serie Kairos — misma fuente que `/carga`.",
        "",
        "## 📊 Métricas clave",
        "| Métrica | Valor |",
        "|---|---:|",
        f"| Semana natural | {week_span} |",
        f"| TSS acumulado | {current_week_tss:.1f} |",
        f"| TSS semana previa | {previous_week_tss:.1f} |",
        f"| Diferencia porcentual | {delta_pct_text} |",
        f"| Spike >20% | {spike_text} |",
        "",
        "| Semana | TSS | CTL (Estado físico) | ATL (Fatiga) | TSB (Forma) | Zona (% del CTL) |",
        "|---|---:|---:|---:|---:|---|",
        f"| {week_label} | {current_week_tss:.1f} | {w_ctl:.1f} | {w_atl:.1f} | {w_tsb:+.1f} | {_wstatus(w_tsb, w_ctl)} |",
        f"| {comp_label} | {comp_tss_str} | {c_ctl:.1f} | {c_atl:.1f} | {c_tsb:+.1f} | {_wstatus(c_tsb, c_ctl)} |",
        "",
        "Desglose por tipo de TSS:",
        f"  - rTSS: {tss_breakdown['rTSS']:.1f}",
        f"  - hrTSS: {tss_breakdown['hrTSS']:.1f}",
        f"  - TSS (potencia o metabólico): {tss_breakdown['TSS']:.1f}",
        "",
        "Desglose diario:",
    ]

    for d in week_dates:
        d_iso = d.isoformat()
        comp = day_type_breakdown.get(d_iso, {"rTSS": 0.0, "hrTSS": 0.0, "TSS": 0.0})
        lines.append(
            f"  - {weekday_es.get(d.weekday(), d.strftime('%A'))} {d.strftime('%d/%m')}: {tss_by_day.get(d_iso, 0.0):.1f} "
            f"(rTSS {comp['rTSS']:.1f} · hrTSS {comp['hrTSS']:.1f} · TSS {comp['TSS']:.1f})"
        )
    # Día de más carga de la semana, calculado aquí (24/10: el LLM lo sacaba de la serie y se equivocaba).
    peak_day = max(week_dates, key=lambda d: tss_by_day.get(d.isoformat(), 0.0), default=None)
    if peak_day is not None and tss_by_day.get(peak_day.isoformat(), 0.0) > 0:
        lines.append(
            f"  - Día con más carga: {weekday_es.get(peak_day.weekday())} {peak_day.strftime('%d/%m')} "
            f"({tss_by_day[peak_day.isoformat()]:.1f} TSS)"
        )

    lines.append("")
    if act_rows:
        lines.append("Actividades:")
        rows_by_day: dict[str, list[tuple[date, str, str, float | None, str]]] = {}
        for d_obj, sport, name, tss_val, _tss_src, _tss_label, _tss_bucket, display_bucket in act_rows:
            d_iso = d_obj.isoformat()
            rows_by_day.setdefault(d_iso, []).append((d_obj, sport, name, tss_val, display_bucket))

        for d_iso in sorted(rows_by_day.keys()):
            d_obj = rows_by_day[d_iso][0][0]
            day_tss = tss_by_day.get(d_iso)
            if day_tss is not None:
                lines.append(f"- {d_obj.strftime('%d/%m')}: TSS {float(day_tss):.1f}")
            else:
                lines.append(f"- {d_obj.strftime('%d/%m')}: TSS sin datos")
            day_rows = rows_by_day[d_iso]
            show_activity_tss = len(day_rows) > 1

            # TSS por actividad = valor guardado en activity_tss, sin reescalar.
            for _d_obj, sport, name, act_tss, tss_bucket in day_rows:
                if show_activity_tss and act_tss is not None:
                    lines.append(f"  - {sport} — {name} · TSS {float(act_tss):.1f} ({tss_bucket})")
                elif act_tss is not None:
                    lines.append(f"  - {sport} — {name} ({tss_bucket})")
                else:
                    lines.append(f"  - {sport} — {name} · TSS pendiente de cálculo")
    else:
        lines.append("- Actividades fuente (Garmin): sin datos en el rango consultado.")

    lines.append("")
    lines.append("## ✅ Recomendación")
    if spike_alert:
        lines.append("- ⚠️ Spike semanal >20% detectado vs semana previa: reduce 15-25% la carga en próximos 2-3 días.")
    elif used_activity_fallback:
        lines.append("- Nota: faltaban cierres en `load_metrics_daily` para algún día; se usó fallback con TSS estimado desde actividades.")
    elif is_current_week and (not has_week_end_load_row) and any(x[0] == today_d for x in act_rows):
        lines.append("- Nota: hay actividad hoy, pero el cierre diario de TSS aún no está persistido en `load_metrics_daily`.")
    else:
        lines.append("- Mantén esta referencia como control de carga semanal real.")

    lines.append("")
    lines.append("## 🎯 Próximo paso")
    lines.append("- Revisa este acumulado antes de definir intensidad de la próxima sesión.")
    lines.append("- Fuente: respuesta determinista (sin inferencias del LLM para nombres/tipos de actividad).")
    return "\n".join(lines)


async def _build_week_activities_markdown(mcp_session, user_message: str | None = None) -> str:
    """Construye un resumen determinista de actividades por semana natural."""
    today_d = date.today()
    week_start, week_end = _resolve_week_window(user_message, today_d)
    log.info(
        "Kairos está consultando actividades semanales en Garmin (%s -> %s)",
        week_start.isoformat(),
        week_end.isoformat(),
    )

    activities: list[dict] = []
    req_variants = [
        {
            "start_date": week_start.isoformat(),
            "end_date": week_end.isoformat(),
            "page": 0,
            "page_size": 300,
        },
        {
            "startdate": week_start.isoformat(),
            "enddate": week_end.isoformat(),
        },
    ]
    for args in req_variants:
        try:
            raw = await call_tool(mcp_session, "get_activities_by_date", args)
            parsed = _try_parse_json(raw)
            if parsed is None:
                parsed = raw
            acts = _extract_activities_list(parsed)
            if acts:
                activities = acts
                break
        except (TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, KeyError) as exc:
            log.debug("_build_week_activities_markdown: get_activities_by_date fallo con args=%s: %s", args, exc)
            continue

    rows: list[tuple[date, str, str, int | None, str | None]] = []
    for act in activities:
        if not isinstance(act, dict):
            continue
        d_iso = _extract_activity_date_iso(act)
        if not d_iso:
            continue
        try:
            d_obj = date.fromisoformat(d_iso)
        except ValueError:
            continue
        if not (week_start <= d_obj <= week_end):
            continue
        name = str(act.get("name") or act.get("activityName") or "Actividad").strip() or "Actividad"
        sport = _get_activity_name_es(act.get("type") or act.get("activityType") or "") or "Actividad"
        act_id = None
        if act.get("activityId") is not None:
            act_id = str(act.get("activityId")).strip() or None
        dur = act.get("duration") or act.get("duration_seconds") or act.get("movingDuration")
        dur_min = None
        try:
            if dur is not None:
                dur_min = int(round(float(dur) / 60.0))
        except (TypeError, ValueError):
            dur_min = None
        rows.append((d_obj, sport, name, dur_min, act_id))

    rows.sort(key=lambda x: (x[0], x[1].lower(), x[2].lower()))

    lines = [
        "## 🧭 Resumen",
        "Consulta semanal de actividades resuelta con datos de Garmin.",
        "",
        "## 📊 Métricas clave",
        "| Métrica | Valor | Fuente |",
        "|---|---|---|",
        f"| Rango consultado | {week_start.strftime('%d/%m/%Y')} → {week_end.strftime('%d/%m/%Y')} | calendario ISO |",
        f"| Actividades detectadas | {len(rows)} | Garmin get_activities_by_date |",
        "",
        "Actividades de Garmin:",
    ]

    if rows:
        for d_obj, sport, name, dur_min, act_id in rows:
            id_suffix = f" [id:{act_id}]" if act_id else ""
            if dur_min is not None and dur_min > 0:
                lines.append(f"- {d_obj.strftime('%d/%m')}: {sport} — {name} ({dur_min} min){id_suffix}")
            else:
                lines.append(f"- {d_obj.strftime('%d/%m')}: {sport} — {name}{id_suffix}")
    else:
        lines.append("- Sin actividades en el rango consultado.")

    lines.append("")
    lines.append("## ✅ Recomendación")
    if rows:
        lines.append("- Usa este listado para validar continuidad y distribución semanal de sesiones.")
    else:
        lines.append("- No aplica en esta consulta.")
    lines.append("")
    lines.append("## 🎯 Próximo paso")
    lines.append("- Si quieres, te digo el TSS de esta misma semana y cómo se reparte por actividad.")
    lines.append("- Fuente: respuesta determinista (sin inferencias del LLM para nombres/tipos de actividad; IDs Garmin incluidos para trazabilidad).")
    return "\n".join(lines)


async def _fetch_activities_for_load_calc(
    mcp_session: ClientSession,
    start_date_iso: str,
    end_date_iso: str,
) -> list[dict]:
    """Obtiene actividades en el rango [start_date, end_date] usando paginación de get_activities.

    Usa el mismo mecanismo paginado que _find_activity_id_by_date (probado y funcional).
    Para cuando start_date esté lejos en el tiempo pagina hacia atrás hasta encontrar
    actividades más antiguas que start_date.
    """
    from datetime import date as _date
    try:
        start_d = _date.fromisoformat(start_date_iso)
        end_d   = _date.fromisoformat(end_date_iso)
    except ValueError:
        return []

    result: list[dict] = []
    seen_ids: set = set()
    start_idx = 0
    limit = 100
    max_pages = 50   # hasta 5000 actividades — suficiente para historiales de 120 días

    for _ in range(max_pages):
        raw = await call_tool(mcp_session, "get_activities", {"start": str(start_idx), "limit": str(limit)})
        activities, has_more, next_start = _parse_activities_response(raw)

        if not activities:
            break

        page_dates: list = []
        for act in activities:
            if not isinstance(act, dict):
                continue
            page_d_iso = _extract_activity_date_iso(act)
            if page_d_iso:
                try:
                    page_dates.append(_date.fromisoformat(page_d_iso))
                except ValueError:
                    pass
            act_id = act.get("activityId") or act.get("id") or act.get("activity_id")
            if act_id is not None and act_id in seen_ids:
                continue
            if act_id is not None:
                seen_ids.add(act_id)

            d_iso = _extract_activity_date_iso(act)
            if not d_iso:
                continue
            try:
                d_obj = _date.fromisoformat(d_iso)
            except ValueError:
                continue

            if d_obj > end_d:
                continue   # más reciente que el rango, seguir paginando
            if d_obj < start_d:
                # No cortar aquí: algunos servidores pueden devolver páginas con
                # orden no estrictamente descendente y podríamos saltarnos días recientes.
                continue
            result.append(act)

        if not has_more:
            # Con lista simple el parser no informa has_more: una página llena indica que puede
            # haber más, salvo que la página entera ya sea anterior al rango.
            if len(activities) < limit:
                break
            if page_dates and max(page_dates) < start_d:
                break
        new_start = next_start if next_start > start_idx else start_idx + limit
        if new_start <= start_idx:
            break
        start_idx = new_start

    log.info(
        "_fetch_activities_for_load_calc: %d actividades obtenidas [%s → %s]",
        len(result), start_date_iso, end_date_iso,
    )
    # Diagnóstico de campos (DEBUG): útil para verificar compatibilidad con garmin-mcp
    if result:
        sample = result[0]
        tss_fields  = {k: sample[k] for k in sample if any(x in k.lower() for x in ("load","tss","training","effect","stress"))}
        dur_fields  = {k: sample[k] for k in sample if any(x in k.lower() for x in ("duration","elapsed","moving"))}
        hr_fields   = {k: sample[k] for k in sample if any(x in k.lower() for x in ("hr","heart"))}
        log.debug("sample activity keys: %s", list(sample.keys())[:30])
        log.debug("sample tss_fields=%s  dur_fields=%s  hr_fields=%s", tss_fields, dur_fields, hr_fields)
    return result
