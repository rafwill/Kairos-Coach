"""
agent/plan_llm.py
El LLM del atleta construye y revisa su plan (28/10, decisión del usuario). Las llamadas están aquí; los datos, la
conversión de los tramos y las reglas, en agent/plan_builder.py.

Construcción: una llamada para la estructura (semanas, fases, descargas, minutos, tirada larga, enfoque) y, con ella
aprobada, el detalle de los días por tandas de 4 semanas en paralelo. Cada respuesta pasa las reglas del código; si
alguna falla, el LLM recibe los fallos concretos y corrige (como mucho `_PLAN_ATTEMPTS` intentos por llamada).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, timedelta
from typing import Callable

from agent import plan_builder as _pb
from agent import run_plan as _run_plan
from agent import storage as _storage
from agent.daily_engine import _extract_activity_duration_minutes
from agent.data_access import _add_ia_tokens
from agent.dates import _safe_float
from agent.garmin_data import _extract_activity_date_iso
from agent.routes.load import _fetch_activities_for_load_calc
from agent.hr_profile import _resolve_hr_zones, _resolve_running_threshold_pace_for_date, _resolve_tp_hr_threshold_for_date
from agent.llm import _llm_timeout_seconds
from agent.plans import _resolve_training_constraints, _validate_structured_plan
from agent.prompts import _load_coaching_rules, _render_prompt

log = logging.getLogger(__name__)

_PLAN_ATTEMPTS = 3  # primera respuesta + 2 correcciones
_WEEKS_PER_CALL = 4
_PROGRESSION_ROUNDS = 2  # rondas para corregir la progresión con las sumas reales de los días


def _correction(errors: list[str]) -> str:
    return _render_prompt("plan/correction", fallos="\n".join(f"- {e}" for e in errors[:12]))


class PlanLLMMixin:
    """El LLM construye el plan (estructura y días) y el código lo comprueba y calcula ritmos, km y TSS."""

    def _plan_frame(self, load_context: dict, user_message: str = "", plan: dict | None = None) -> _pb.PlanFrame:
        """Datos del atleta para construir el plan: objetivo, umbrales, zonas, carga real y disponibilidad.

        Con `plan` (revisión), el inicio y la carrera son los del plan guardado.
        """
        profile = self.user_profile or {}
        goals = dict(profile.get("goals") or {})
        health = profile.get("health") or {}
        today = date.today()
        start = today - timedelta(days=today.weekday())
        pd = (plan or {}).get("plan_data") or {}
        if pd.get("start_date"):
            start = date.fromisoformat(str(pd["start_date"])[:10])
            goals.update({k: pd[k] for k in ("target_race", "target_race_date", "target_time") if pd.get(k) is not None})
        race = str(goals.get("target_race") or "").strip()
        target_time = str(goals.get("target_time") or "").strip()
        try:
            race_date = date.fromisoformat(str(goals.get("target_race_date") or "")[:10])
        except ValueError:
            race_date = None
        if not race or race_date is None or (race_date < today and not pd):
            # Sin objetivo (o ya pasado): bloque de 8 semanas que acaba con un test de 10K en domingo.
            race, race_date, target_time = "Test de 10K", start + timedelta(weeks=7, days=6), ""
        distance = _run_plan.race_distance_km(race)
        seconds = _run_plan.parse_target_time(target_time)
        threshold = _resolve_running_threshold_pace_for_date(profile, today.isoformat())
        zones, _ = _resolve_hr_zones(profile, "running")
        constraints = _resolve_training_constraints(goals, health, user_message)
        return _pb.PlanFrame(
            start=start, weeks=_pb.weeks_until(start, race_date), race=race, race_date=race_date, distance_km=distance,
            target_time=target_time, threshold_pace=threshold,
            race_pace=(seconds / distance) if seconds and distance else None,
            hr_zones=list(zones or []), hr_threshold_tp=_resolve_tp_hr_threshold_for_date(profile, today.isoformat()),
            weekly_hours=min(24.0, max(3.0, _safe_float(goals.get("weekly_training_hours"), 8.0))),
            recent_weekly_minutes=load_context.get("recent_weekly_minutes"),
            longest_recent_min=load_context.get("longest_recent_min"),
            load={k: load_context.get(k) for k in ("ctl", "atl", "tsb", "status") if load_context.get(k) is not None},
            available_days=list(constraints.get("available_days") or range(1, 8)),
            max_minutes_per_day={int(k): int(v) for k, v in (constraints.get("max_minutes_per_day") or {}).items()},
            min_rest_days=int(constraints.get("min_rest_days") or 1),
            max_quality=int(constraints.get("max_quality_sessions_per_week") or 2),
            primary_sport=str(goals.get("primary") or ""),
            note=str(user_message or "").strip(),
        )

    def _plan_system_message(self) -> dict:
        return {"role": "system", "content": _render_prompt("plan/system") + "\n\n" + _load_coaching_rules()
                + self._profile_context_block()}

    async def _plan_llm_call(self, messages: list[dict], label: str) -> str:
        t0 = time.perf_counter()
        response = await asyncio.wait_for(
            self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **({"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
                   if getattr(self, "provider", "") == "nvidia" else {}),
            ),
            timeout=_llm_timeout_seconds(),
        )
        usage = getattr(response, "usage", None)
        p_toks = getattr(usage, "prompt_tokens", 0) or 0
        c_toks = getattr(usage, "completion_tokens", 0) or 0
        self.total_prompt_tokens += p_toks
        self.total_completion_tokens += c_toks
        _add_ia_tokens(p_toks + c_toks)
        log.info("[PLAN] %s elapsed=%.1fs tokens=%d+%d", label, time.perf_counter() - t0, p_toks, c_toks)
        return str(response.choices[0].message.content or "")

    async def _plan_llm_json(self, messages: list[dict], check: Callable[[dict], list[str]],
                             label: str) -> tuple[dict | None, list[str], list[dict]]:
        """Pide el JSON, lo comprueba y devuelve los fallos al LLM hasta que pase o se acaben los intentos.

        Devuelve (datos o None, fallos, conversación con la última respuesta: para seguirla si hace falta).
        """
        errors: list[str] = []
        for attempt in range(1, _PLAN_ATTEMPTS + 1):
            text = await self._plan_llm_call(messages, f"{label} intento {attempt}")
            try:
                data = _pb.parse_json(text)
                errors = check(data)
            except ValueError as exc:
                data, errors = None, [str(exc)]
            if not errors:
                return data, [], messages + [{"role": "assistant", "content": text}]
            log.info("[PLAN] %s intento %d: %s", label, attempt, " | ".join(errors[:8]))
            messages = messages + [{"role": "assistant", "content": text}, {"role": "user", "content": _correction(errors)}]
        return None, errors, messages

    async def _build_plan_with_llm(self, user_message: str, load_context: dict) -> tuple[dict | None, list[dict], list[str]]:
        """Plan construido por el LLM: (plan, sesiones, []) o (None, [], fallos) si no consiguió uno válido."""
        f = self._plan_frame(load_context, user_message)
        datos = _pb.frame_text(f)
        system = self._plan_system_message()
        t0 = time.perf_counter()
        macro, errors, _ = await self._plan_llm_json(
            [system, {"role": "user", "content": _render_prompt("plan/macro", datos=datos, semanas=f.weeks,
                                                                 semana_carrera=f.race_week, dias_entreno=f.training_days)}],
            lambda m: _pb.check_macro(m, f), "estructura",
        )
        if macro is None:
            return None, [], [f"Estructura: {e}" for e in errors]
        estructura = _pb.macro_text(macro)
        planned = {int(w["week"]): w for w in macro["weeks"]}
        chunks = _pb.chunk_weeks(f.weeks, _WEEKS_PER_CALL)
        expanded: dict[int, list[dict]] = {}

        def _check_chunk(weeks: list[int]) -> Callable[[dict], list[str]]:
            def check(data: dict) -> list[str]:
                got = {int(_pb._num(w.get("week"))): w for w in (data.get("weeks") or []) if isinstance(w, dict)}
                if sorted(got) != weeks:
                    return [f"\"weeks\" debe tener exactamente las semanas {weeks}."]
                errs: list[str] = []
                for n in weeks:
                    sessions, week_errors = _pb.expand_week(got[n], f, planned[n])
                    errs += week_errors or _pb.check_week(n, sessions, planned[n], f)
                    expanded[n] = sessions
                return errs or _pb.check_quality_variety([s for n in weeks for s in expanded[n]])
            return check

        results = await asyncio.gather(*[
            self._plan_llm_json(
                [system, {"role": "user", "content": _render_prompt(
                    "plan/weeks", datos=datos, estructura=estructura, semanas=", ".join(str(w) for w in weeks),
                    reglas=_pb.rules_text(f))}],
                _check_chunk(weeks), f"semanas {weeks[0]}-{weeks[-1]}",
            )
            for weeks in chunks
        ])
        failed = [f"Semanas {weeks[0]}-{weeks[-1]}: {e}" for weeks, (data, errs, _) in zip(chunks, results) if data is None
                  for e in errs]
        # La progresión se comprueba también con lo que suman de verdad los días (la estructura es una estimación):
        # cada semana que la rompe vuelve a su tanda, que sigue su conversación con el fallo.
        conversations = {tuple(weeks): conv for weeks, (_, _, conv) in zip(chunks, results)}
        rounds = 0
        while not failed:
            by_chunk: dict[tuple, list[str]] = {}
            for e in _pb.check_macro(_pb.actual_macro(macro, expanded), f):
                n = _pb.week_of_error(e)
                chunk = next((tuple(w) for w in chunks if n in w), None)
                if chunk:
                    by_chunk.setdefault(chunk, []).append(e + " (suma real de tus días)")
            if not by_chunk:
                break
            if rounds == _PROGRESSION_ROUNDS:
                failed = [e for errs in by_chunk.values() for e in errs]
                break
            rounds += 1
            log.info("[PLAN] progresión real, ronda %d: %s", rounds, " | ".join(e for v in by_chunk.values() for e in v))
            retried = await asyncio.gather(*[
                self._plan_llm_json(conversations[chunk] + [{"role": "user", "content": _correction(errs)}],
                                    _check_chunk(list(chunk)), f"progresión semanas {chunk[0]}-{chunk[-1]}")
                for chunk, errs in by_chunk.items()
            ])
            for chunk, (data, errs, conv) in zip(by_chunk, retried):
                conversations[chunk] = conv
                if data is None:
                    failed += [f"Semanas {chunk[0]}-{chunk[-1]}: {e}" for e in errs]
        log.info("[PLAN] construcción elapsed=%.1fs fallos=%d", time.perf_counter() - t0, len(failed))
        if failed:
            return None, [], failed
        sessions = [s for n in sorted(expanded) for s in expanded[n]]
        race_label = f.race
        plan = {
            "title": str(macro.get("title") or f"Plan hacia {race_label}"),
            "description": str(macro.get("rationale") or ""),
            "objective": f"Preparación para {race_label}" + (f" con objetivo de {f.target_time}" if f.target_time else ""),
            "difficulty": "moderate",
            "duration_weeks": f.weeks,
            "status": "active",
            "source": "agent_llm_plan",
            "plan_data": {**_pb.plan_data(macro, sessions, f), "model": getattr(self, "model", ""),
                          "generation_note": (user_message or "")[:240],
                          "load_context": dict(load_context or {})},
        }
        final = _validate_structured_plan(plan, sessions, self.user_profile or {})
        if final:
            return None, [], final
        return plan, sessions, []

    async def _executed_vs_planned(self, sessions: list[dict], start: date) -> str:
        """Últimos 7 días: lo planificado y lo que hizo el atleta (actividades de Garmin con su TSS guardado)."""
        today = date.today()
        first = today - timedelta(days=7)
        try:
            acts = await _fetch_activities_for_load_calc(self.mcp_session, first.isoformat(), today.isoformat())
        except (TimeoutError, OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            log.debug("revisión del plan: actividades no disponibles: %s", exc)
            acts = []
        tss_map = _storage.get_activity_tss_map(first.isoformat()) or {}
        done: dict[str, list[str]] = {}
        for act in acts or []:
            act_id = act.get("activityId") or act.get("activity_id")
            tss = (tss_map.get(int(act_id)) or {}).get("tss") if act_id else None
            name = str(act.get("activityName") or act.get("name") or "actividad")
            done.setdefault(_extract_activity_date_iso(act) or "", []).append(
                f"{name} {_extract_activity_duration_minutes(act):.0f} min" + (f" (TSS {tss:.0f})" if tss else ""))
        by_key = {(int(s.get("week_index") or 0), int(s.get("day_index") or 0)): s for s in sessions}
        lines = []
        for i in range(7, 0, -1):
            d = today - timedelta(days=i)
            planned = by_key.get(((d - start).days // 7 + 1, d.isoweekday())) or {}
            title = (planned.get("structured_workout") or {}).get("title") or planned.get("session_type") or "sin plan"
            minutes = planned.get("duration_min")
            lines.append(f"- {_run_plan._DAY_NAMES[d.isoweekday()]} {d.strftime('%d/%m')}: planificado {title}"
                         + (f" {minutes} min" if minutes else "") + " · realizado: "
                         + ("; ".join(done.get(d.isoformat(), [])) or "nada"))
        return "\n".join(lines)

    async def _revise_plan_with_llm(self, user_message: str, plan: dict, sessions: list[dict],
                                    load_context: dict) -> tuple[list[dict] | None, str, list[str], list[dict]]:
        """Revisión del plan por el LLM con lo que cuenta el atleta y lo que ha hecho los últimos 7 días.

        Devuelve (sesiones nuevas, o None si no cambia nada; lo que el LLM le dice al atleta; fallos; mensajes, para
        los sensores). Solo cambia
        días desde hoy hasta el final de la semana siguiente: los pasados no se tocan.
        """
        f = self._plan_frame(load_context, "", plan=plan)
        pd = plan.get("plan_data") or {}
        today = date.today()
        current = (today - f.start).days // 7 + 1
        editable = [w for w in (current, current + 1) if 1 <= w <= f.weeks]
        if not editable:
            return None, "", ["El plan activo no tiene semanas desde hoy: crea uno nuevo con /plan crear."], []

        def _at(key: str, i: int, default):
            values = list(pd.get(key) or [])
            return values[i] if i < len(values) else default

        macro = {"weeks": [{"week": i + 1, "phase": ph, "deload": (i + 1) in set(pd.get("deload_weeks") or []),
                            "minutes": _at("planned_week_minutes", i, _at("week_minutes", i, 0)),
                            "long_min": _at("long_minutes", i, 0), "focus": _at("focus", i, "")}
                           for i, ph in enumerate(pd.get("phases") or [])]}
        planned = {int(w["week"]): w for w in macro["weeks"]}
        first_day = {w: (today.isoweekday() if w == current else 1) for w in editable}
        dias = json.dumps([{"week": w, "days": [d for d in _pb.compact_week(sessions, w) if int(d.get("day") or 0) >= first_day[w]]}
                           for w in editable], ensure_ascii=False)
        messages = [self._plan_system_message(), {"role": "user", "content": _render_prompt(
            "plan/revision", datos=_pb.frame_text(f), estructura=_pb.macro_text(macro),
            hoy=f"{_run_plan._DAY_NAMES[today.isoweekday()]} {today.strftime('%d/%m/%Y')} (semana {current} del plan)",
            realizado=await self._executed_vs_planned(sessions, f.start), dias=dias, mensaje=user_message)}]
        result: dict[int, list[dict]] = {}

        def check(data: dict) -> list[str]:
            result.clear()
            errs: list[str] = []
            for w in data.get("weeks") or []:
                n = int(_pb._num(w.get("week")))
                days = [d for d in (w.get("days") or []) if isinstance(d, dict)]
                if n not in editable:
                    errs.append(f"Semana {n}: solo puedes cambiar las semanas {editable}.")
                    continue
                if any(int(_pb._num(d.get("day"))) < first_day[n] for d in days):
                    errs.append(f"Semana {n}: no cambies días pasados (desde el día {first_day[n]}).")
                    continue
                changed = {int(_pb._num(d.get("day"))) for d in days}
                week = [s for s in sessions if int(s.get("week_index") or 0) == n and int(s.get("day_index") or 0) not in changed]
                new, day_errs = _pb.expand_week({"week": n, "days": days}, f, planned.get(n, {}),
                                                kept_minutes=sum(float(s.get("duration_min") or 0) for s in week))
                if day_errs:
                    errs += day_errs
                    continue
                week = sorted(week + new, key=lambda s: int(s.get("day_index") or 0))
                # Una revisión puede bajar el volumen (molestia, viaje); lo que no puede es subirlo más de un 10 %.
                errs += _pb.check_week(n, week, planned.get(n, {}), f, past_days=max(1, first_day[n] - 1))
                total = sum(float(s.get("duration_min") or 0) for s in week)
                cap = _pb._num(planned.get(n, {}).get("minutes")) * 1.10
                if cap and total > cap + 1:
                    errs.append(f"Semana {n}: suma {total:.0f} min; como mucho {cap:.0f} (estructura + 10 %).")
                result[n] = week
            return errs

        data, errors, conversation = await self._plan_llm_json(messages, check, "revisión")
        if data is None:
            return None, "", errors, messages
        reply = str(data.get("reply") or "").strip()
        if not result:
            return None, reply, [], messages
        new_sessions = [s for s in sessions if int(s.get("week_index") or 0) not in result]
        new_sessions += [s for n in sorted(result) for s in result[n]]
        new_sessions.sort(key=lambda s: (int(s.get("week_index") or 0), int(s.get("day_index") or 0)))
        # Lo que lee el atleta sale de los días guardados (con la duración final de los "auto"), no del borrador:
        # en vivo, el primer texto describía cambios que no eran los que quedaban (28/10).
        old = {(int(s.get("week_index") or 0), int(s.get("day_index") or 0)): (s.get("structured_workout") or {}).get("plan_day")
               for s in sessions}
        cambios = []
        for s in new_sessions:
            if old.get((s["week_index"], s["day_index"])) != (s.get("structured_workout") or {}).get("plan_day"):
                day = f.start + timedelta(weeks=s["week_index"] - 1, days=s["day_index"] - 1)
                cambios += _run_plan.session_lines(s, day, f.hr_zones)
        if cambios:
            text = await self._plan_llm_call(
                conversation + [{"role": "user", "content": _render_prompt("plan/revision_reply", cambios="\n".join(cambios))}],
                "revisión texto")
            reply = text.strip() or reply
            messages = messages + [{"role": "user", "content": "\n".join(cambios)}]  # referencia de los sensores
        return new_sessions, reply, [], messages
