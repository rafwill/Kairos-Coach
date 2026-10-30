"""
agent/context.py
Contexto del LLM: prompt de sistema (perfil, estado de carga, umbrales, plan activo, calendario y base de conocimiento), mensajes de la pregunta y contexto de carga para el motor de planes.
"""

import logging
import json
from datetime import date, timedelta
from typing import Any


from agent import diabetes as _diabetes
from agent import load_metrics as _load_metrics
from agent import run_plan as _run_plan
from agent.plans import _is_block_plan
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _is_running_non_trail_activity,
    _resolve_running_threshold_pace_sec_per_km,
)
from agent.prompts import (
    _render_prompt,
)
from agent.dates import (
    _parse_iso_date_safe,
)
from agent.knowledge import (
    _build_athlete_knowledge_context,
)
from agent.data_access import (
    _load_metrics_series,
    _load_metrics_view,
    _load_session_summaries,
    _session_memory_enabled,
)
from agent.garmin_data import (
    _extract_activity_date_iso,
)
from agent.hr_profile import (
    _resolve_hr_threshold_bpm,
    _resolve_hr_zones,
    _resolve_running_threshold_pace_for_date,
    _resolve_tp_hr_threshold_for_date,
)
from agent.daily_engine import (
    _session_type_label,
    _extract_activity_duration_minutes,
    _get_active_training_plan,
    _get_planned_session_for_date,
    _get_planned_sessions_for_date,
    _session_intensity_label,
)
from agent.routes.load import (
    _fetch_activities_for_load_calc,
)
from agent.coaching import (
    _calendar_block,
)

log = logging.getLogger(__name__)


class ContextMixin:
    """Contexto del LLM: prompt de sistema (perfil, estado de carga, umbrales, plan activo, calendario y base de conocimiento), mensajes de la pregunta y contexto de carga para el motor de planes."""

    async def _build_plan_load_context(self) -> dict:
        """Estado real del atleta para el motor de planes: carga actual y volumen de las últimas semanas."""
        context: dict[str, Any] = {}
        series = _load_metrics_series()
        if series:
            load = _load_metrics_view(self.user_profile, series)
            latest = load.get("latest") or {}
            for key in ("ctl", "atl", "tsb"):
                if latest.get(key) is not None:
                    context[key] = round(float(latest[key]), 1)
            if load.get("status"):
                context["status"] = str(load["status"])
        today = date.today()
        try:
            acts = await _fetch_activities_for_load_calc(
                self.mcp_session, (today - timedelta(days=42)).isoformat(), today.isoformat()
            )
        except (TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, KeyError) as exc:
            log.debug("plan load context: actividades no disponibles: %s", exc)
            acts = []
        last_28_iso = (today - timedelta(days=28)).isoformat()
        minutes_28 = 0.0
        longest = 0.0
        for act in acts or []:
            d_iso = _extract_activity_date_iso(act) or ""
            minutes = _extract_activity_duration_minutes(act)
            act_type = act.get("type") or act.get("activityType") or act.get("activityTypeDTO") or ""
            # Caminatas y senderismo no cuentan (30/10): el plan nunca los pone e inflaban el punto de partida.
            if d_iso >= last_28_iso and not _load_metrics._is_hike_walk_activity(act_type):
                minutes_28 += minutes
            if _is_running_non_trail_activity(act_type) or _load_metrics._is_trail_activity(act_type):
                longest = max(longest, minutes)
        if minutes_28 > 0:
            context["recent_weekly_minutes"] = round(minutes_28 / 4.0, 1)
        if longest > 0:
            context["longest_recent_min"] = round(longest, 1)
        return context

    def _profile_context_block(self) -> str:
        """Bloque "Perfil del usuario" (datos personales, objetivo y salud) para cualquier prompt.

        Patologías y lesiones por separado (26/10). Si las patologías mencionan diabetes, detrás va su protocolo
        (prompts/diabetes/), según la medicación; sin patologías, el atleta es sano (aunque tenga lesiones) y se dice.
        """
        p = self.user_profile.get("personal", {})
        g = self.user_profile.get("goals", {})
        h = self.user_profile.get("health", {})
        lines = []
        if p.get("name"):          lines.append(f"- Nombre: {p['name']}")
        if p.get("age"):           lines.append(f"- Edad: {p['age']} años")
        if p.get("gender"):        lines.append(f"- Género: {p['gender']}")
        if p.get("weight_kg"):     lines.append(f"- Peso: {p['weight_kg']} kg")
        if p.get("height_cm"):     lines.append(f"- Altura: {p['height_cm']} cm")
        if g.get("primary"):       lines.append(f"- Deporte principal: {g['primary']}")
        if g.get("weekly_training_hours"): lines.append(f"- Horas de entrenamiento/semana: {g['weekly_training_hours']}")
        if g.get("target_race"):   lines.append(f"- Carrera/evento objetivo: {g['target_race']}")
        if g.get("target_race_date"):
            race_d = _parse_iso_date_safe(g.get("target_race_date"))
            if race_d is not None:
                days_left = (race_d - date.today()).days
                when = f"faltan {days_left} días" if days_left >= 0 else "YA PASÓ: pedir nuevo objetivo"
                lines.append(f"- Fecha del evento: {race_d.strftime('%d/%m/%Y')} ({when})")
            else:
                lines.append(f"- Fecha del evento: {g['target_race_date']}")
        if g.get("target_time"):   lines.append(f"- Tiempo objetivo: {g['target_time']}")
        pathologies = h.get("pathologies") or []
        diabetes = _diabetes.label(h)
        if pathologies:            lines.append(f"- Patologías: {', '.join(pathologies)}")
        elif lines and not diabetes:
            lines.append("- Patologías: ninguna declarada (atleta sano)")
        if diabetes:               lines.append(f"- Diabetes: {diabetes}")
        injuries = h.get("injuries", [])
        if injuries:               lines.append(f"- Lesiones: {', '.join(injuries)}")
        if h.get("notes"):         lines.append(f"- Notas de salud: {h['notes']}")
        protocol = {"insulina": "diabetes/insulina", "sin_insulina": "diabetes/sin_insulina"}.get(_diabetes.protocol(h) or "")
        protocol_block = ("\n\n" + _render_prompt(protocol) + "\n") if protocol else ""
        return (("\n\n## Perfil del usuario\n" + "\n".join(lines) + "\n") if lines else "") + protocol_block

    def _load_and_anchors_context_block(self) -> str:
        """Bloques "Estado de carga actual" y "Umbrales" desde el perfil (sin llamadas a Garmin).

        Si el perfil está incompleto o mal formado, se omite el bloque: nunca debe impedir la respuesta.
        """
        try:
            return self._build_load_and_anchors_context_block()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            log.debug("contexto de carga/umbrales no disponible: %s", exc)
            return ""

    def _build_load_and_anchors_context_block(self) -> str:
        out = ""
        series = _load_metrics_series()
        if series:
            load = _load_metrics_view(self.user_profile, series)
            latest = load.get("latest") or {}
            ranges = load.get("ranges") or {}
            weekly = load.get("weekly") or {}
            if latest:
                _zone_ctx = _load_metrics.form_zone(float(latest.get("tsb") or 0), float(latest.get("ctl") or 0))
                out += (
                    "\n\n## Estado de carga actual (Kairos)\n"
                    f"- Fecha: {latest.get('date')} · TSS del día {float(latest.get('tss') or 0):.1f}\n"
                    f"- Estado físico (CTL) {float(latest.get('ctl') or 0):.1f} · Fatiga (ATL) {float(latest.get('atl') or 0):.1f}"
                    f" · Forma (TSB) {float(latest.get('tsb') or 0):+.1f}\n"
                    f"- Zona de Forma (Friel, % del CTL): {_zone_ctx['emoji']} {_zone_ctx['label']} ({_zone_ctx['form_pct']:+.0f} %)"
                    f" · estado del motor diario: {load.get('status') or 'neutral'} ({load.get('action') or ''})\n"
                    f"- Rango habitual de Forma (informativo, no decide): {ranges.get('tsb_low')} a {ranges.get('tsb_high')}"
                    f" · ATL máximo habitual {ranges.get('atl_high')}\n"
                    f"- TSS de la semana en curso {float(weekly.get('current_tss') or 0):.1f}"
                    f" (semana previa {float(weekly.get('previous_tss') or 0):.1f})\n"
                )
        perf = (self.user_profile or {}).get("performance") or {}
        today_iso = date.today().isoformat()
        anchors = []
        hr_thr, _, _ = _resolve_hr_threshold_bpm(self.user_profile)
        if hr_thr:
            anchors.append(f"- Umbral de FC (LTHR): {int(round(float(hr_thr)))} lpm")
        for sport, label in (("running", "carrera"), ("cycling", "ciclismo")):
            zones, source = _resolve_hr_zones(self.user_profile, sport)
            if zones:
                anchors.append(
                    f"- Zonas de FC de {label} [{source}]: "
                    + " · ".join(f"{name} < {hi + 1}" if lo <= 0 else f"{name} {lo}–{hi}" for name, lo, hi in zones)
                    + " lpm"
                )
        tp_thr = _resolve_tp_hr_threshold_for_date(self.user_profile, today_iso)
        if tp_thr and (not hr_thr or int(tp_thr) != int(hr_thr)):
            anchors.append(f"- Umbral de FC de referencia de TrainingPeaks (hrTSS): {int(tp_thr)} lpm")
        pace = _resolve_running_threshold_pace_for_date(self.user_profile, today_iso) or _resolve_running_threshold_pace_sec_per_km(self.user_profile)
        if pace:
            anchors.append(f"- Ritmo umbral de carrera: {int(pace // 60)}:{int(pace % 60):02d} min/km")
        if perf.get("cycling_ftp"):
            anchors.append(f"- FTP de ciclismo: {int(float(perf['cycling_ftp']))} W")
        if anchors:
            out += "\n\n## Umbrales\n" + "\n".join(anchors) + "\n"
        return out

    def _plan_context_block(self) -> str:
        """Bloque "Plan activo" con la sesión de hoy y las dos siguientes (caché por sesión)."""
        if not hasattr(self, "_active_plan_cache"):
            try:
                self._active_plan_cache = _get_active_training_plan(self.user_profile)
            except (RuntimeError, ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
                log.debug("plan context: no disponible: %s", exc)
                self._active_plan_cache = None
        plan = self._active_plan_cache
        if not isinstance(plan, dict) or not plan:
            return "\n\n## Plan activo\n- No hay plan activo.\n"
        lines = [f"- {plan.get('title') or 'Plan activo'}"]
        if not plan.get("sessions"):
            # Planes creados antes del 28/10 con el formulario de /plan crear: sin sesiones que leer.
            lines.append("- El plan no tiene sesiones (se creó solo con el formulario): para tenerlas, `/plan crear` "
                         "lo genera de nuevo por semanas.")
            return "\n\n## Plan activo\n" + "\n".join(lines) + "\n"
        for offset, label in ((0, "Hoy"), (1, "Mañana"), (2, "Pasado mañana")):
            d = date.today() + timedelta(days=offset)
            row = _get_planned_session_for_date(plan, d.isoformat())
            if not row:
                continue
            session_type = str(row.get("session_type") or "sesión")
            if _is_block_plan(plan) and session_type != "rest":
                # Plan por bloques (28/10): la sesión con su título, duración, km, TSS y tramos (ritmo y FC); con
                # día doble (29/10), las dos.
                zones, _ = _resolve_hr_zones(self.user_profile, "running")
                for i, item in enumerate(_get_planned_sessions_for_date(plan, d.isoformat())):
                    detail = _run_plan.session_lines(item, d, zones)
                    lines.append(f"- {label}{' (doble)' if i else ''}: " + " · ".join(x.strip() for x in detail))
                continue
            if session_type == "rest" or int(row.get("duration_min") or 0) <= 0:
                desc = "descanso"
            else:
                desc = f"{_session_type_label(session_type)}, {int(row.get('duration_min') or 0)} min, {_session_intensity_label(row, self.user_profile)}".strip(", ")
                notes = str(row.get("notes") or "").strip()
                if notes:
                    desc += f" — {notes[:160]}"
            lines.append(f"- {label} ({d.strftime('%d/%m/%Y')}): {desc}")
        return "\n\n## Plan activo\n" + "\n".join(lines) + "\n"

    def _build_system_prompt(self) -> str:
        """Construye el system prompt incluyendo la fecha actual y el perfil del usuario."""
        today_str = date.today().isoformat()
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        date_context = ("\n\n" + _render_prompt("system/date", hoy=today_str, ayer=yesterday_str)
                        + "\n\n" + _calendar_block() + "\n")
        profile_context = self._profile_context_block() + self._load_and_anchors_context_block() + self._plan_context_block()

        # Incluir resúmenes de sesiones anteriores para memoria a largo plazo
        memory_context = ""
        summaries = _load_session_summaries() if _session_memory_enabled() else []
        if summaries:
            recent = summaries[-3:]  # últimas 3 sesiones
            _MAX_SUMMARY = 350  # caracteres máximos por resumen
            texts = [(s["date"], str(s.get("summary") or "").strip()) for s in recent]
            lines = "\n".join(
                f"- **{d}**: {t[:_MAX_SUMMARY]}{'…' if len(t) > _MAX_SUMMARY else ''}"
                for d, t in texts
                if t
            )
            if lines:
                memory_context = "\n\n" + _render_prompt("system/memory", sesiones=lines) + "\n"

        kb_context = ""
        if self.knowledge_sources:
            kb_files = ", ".join(self.knowledge_sources)
            kb_context = "\n\n" + _render_prompt("system/knowledge", fuentes=kb_files) + "\n"

        return self.system_prompt + date_context + profile_context + memory_context + kb_context

    def _build_messages(self, user_message: str) -> list[dict]:
        """Construye el array de mensajes para la llamada al LLM.
        Limita el historial a los últimos 6 turnos (3 pares user/assistant)
        para mantener el contexto razonable sin consumir tokens innecesarios.
        """
        messages = [{"role": "system", "content": self._build_system_prompt()}]
        rag_context = _build_athlete_knowledge_context(user_message, self.knowledge_chunks)
        if rag_context:
            messages.append({"role": "system", "content": rag_context})
        previous = self.previous_session_questions
        if previous:
            messages.append({"role": "system", "content": _render_prompt(
                "system/previous_session", preguntas="\n".join(f"- {q[:200]}" for q in previous)
            )})
        # Solo los últimos 6 mensajes del historial de esta sesión (3 intercambios)
        messages.extend(self.conversation_history[-6:])
        messages.append({"role": "user", "content": user_message})
        return messages
