"""
agent/routes/profile.py
Rutas del perfil: FC umbral, ritmo umbral, objetivo y opciones de configuración.
"""

import json
import asyncio
from datetime import date


from agent.mcp.client import (
    call_tool,
)
from agent.load_metrics import (  # una sola copia: la de load_metrics (27/10)
    _resolve_running_threshold_pace_sec_per_km,
    _try_parse_json,
)
from agent.dates import (
    _format_iso_date_es,
    _parse_iso_date_safe,
)
from agent.hr_profile import (
    _extract_hr_threshold_from_payload,
    _resolve_hr_threshold_bpm,
    _resolve_running_threshold_pace_for_date,
    _resolve_tp_hr_threshold_for_date,
)


def _build_goal_status_markdown(profile: dict) -> str:
    """Respuesta determinista del objetivo principal del atleta."""
    goals = (profile or {}).get("goals", {}) if isinstance(profile, dict) else {}
    race = str(goals.get("target_race") or "").strip()
    race_date = _format_iso_date_es(goals.get("target_race_date")) or "por definir"
    target_time = str(goals.get("target_time") or "").strip() or "por definir"
    weekly_hours = goals.get("weekly_training_hours") or "por definir"
    primary = str(goals.get("primary") or "").strip() or "por definir"

    if not race and race_date == "por definir" and target_time == "por definir":
        return "\n".join([
            "## 🧭 Resumen",
            "No tengo un objetivo deportivo definido en tu perfil.",
            "",
            "## 📊 Métricas clave",
            "| Métrica | Valor | Fuente |",
            "|---|---|---|",
            "| Objetivo principal | No definido | perfil.goals |",
            f"| Deporte principal | {primary} | perfil.goals |",
            "",
            "## ✅ Recomendación",
            "- Define carrera objetivo, fecha y tiempo para personalizar mejor tu planificación.",
            "",
            "## 🎯 Próximo paso",
            "- Usa `/perfil editar objetivo` para configurarlo ahora.",
            "- Fuente: respuesta determinista (lectura directa de perfil).",
        ])

    objective_text = race or "objetivo definido"
    return "\n".join([
        "## 🧭 Resumen",
        f"Tu próximo objetivo es: {objective_text}.",
        "",
        "## 📊 Métricas clave",
        "| Métrica | Valor | Fuente |",
        "|---|---|---|",
        f"| Objetivo principal | {objective_text} | perfil.goals |",
        f"| Fecha objetivo | {race_date} | perfil.goals |",
        f"| Tiempo objetivo | {target_time} | perfil.goals |",
        f"| Horas/semana | {weekly_hours} | perfil.goals |",
        f"| Deporte principal | {primary} | perfil.goals |",
        "",
        "## ✅ Recomendación",
        "- Mantén el foco del bloque actual alineado con este objetivo.",
        "",
        "## 🎯 Próximo paso",
        "- Si quieres, te resumo cómo va tu progreso semanal hacia ese objetivo.",
        "- Fuente: respuesta determinista (lectura directa de perfil).",
    ])


def _build_config_options_markdown() -> str:
    """Devuelve listado determinista de opciones configurables en perfil."""
    return "\n".join(
        [
            "## 🧭 Resumen",
            "Estas son las opciones que puedes configurar o consultar en Kairos.",
            "",
            "## 📊 Métricas clave",
            "Ver datos:",
            "- `/perfil`: ver tu perfil completo.",
            "- `/plan listar`: listar planes y ver el activo.",
            "- `/plan ver <id>`: ver detalle de un plan.",
            "- `/carga`: ver tabla semanal de carga/fatiga.",
            "- `/carga meses`: ver resumen mensual.",
            "",
            "Editar perfil:",
            "- `/perfil editar objetivo`: deporte, carrera y tiempo objetivo.",
            "- `/perfil editar salud`: patologías (y medicación si hay diabetes), lesiones y notas.",
            "- `/perfil editar`: edición completa.",
            "- `/perfil umbral <mm:ss>`: ritmo umbral running (ej: `/perfil umbral 4:15`).",
            "- `/perfil umbral fc <lpm>`: FC umbral tras un test, la misma que en TrainingPeaks (ej: `/perfil umbral fc 169`).",
            "- `/perfil fc <reposo> <max>`: FC reposo/máxima (ej: `/perfil fc 48 190`).",
            "",
            "Gestionar planes:",
            "- `/plan crear`: crear plan base.",
            "- `/plan activar <id>`: activar plan por ID.",
            "",
            "Sistema:",
            "- `/menu`: abrir menú resumido.",
            "- `/ayuda`: ver ayuda completa y ejemplos.",
            "- `/resumen`: rehacer el resumen inicial con datos nuevos de Garmin.",
            "- `/proveedor`: cambiar de proveedor de IA, modelo o clave.",
            "- `salir`: salir de Kairos (la sesión queda guardada).",
            "",
            "## ✅ Recomendación",
            "- Empieza por `/perfil` y luego ajusta umbrales con `/perfil umbral` y `/perfil fc`.",
            "",
            "## 🎯 Próximo paso",
            "- Si quieres, te guío paso a paso para actualizar uno ahora.",
            "",
            "- Fuente: respuesta determinista (listado de opciones configurables en perfil).",
        ]
    )


async def _build_hr_threshold_profile_markdown(mcp_session, profile: dict) -> str:
    """Respuesta determinista y rápida de FC umbral (LTHR)."""
    profile = profile if isinstance(profile, dict) else {}
    bpm, date_value, source = _resolve_hr_threshold_bpm(profile)
    if bpm is not None:
        date_d = _parse_iso_date_safe(date_value)
        date_txt = date_d.strftime("%d/%m/%Y") if date_d else date_value
        rows = [
            f"| FC umbral (LTHR) | {int(round(bpm))} lpm | {source} |",
            f"| Fecha de actualización | {date_txt} | perfil |",
        ]
        tp_thr = _resolve_tp_hr_threshold_for_date(profile, date.today().isoformat())
        if tp_thr:
            rows.append(f"| Referencia de TrainingPeaks para hrTSS | {int(tp_thr)} lpm | historial del perfil |")
        return "\n".join(
            [
                "## 🧭 Resumen",
                f"Tu FC umbral (LTHR) es {int(round(bpm))} lpm.",
                "",
                "## 📊 Métricas clave",
                "| Métrica | Valor | Fuente |",
                "|---|---|---|",
                *rows,
                "",
                "## ✅ Recomendación",
                "- Ancla el hrTSS de fuerza, elíptica y remo. El hrTSS de trail y de bici sin potencia usa el umbral de TrainingPeaks, "
                "y tus zonas de FC son las de Garmin (las del reloj).",
                "",
                "## 🎯 Próximo paso",
                "- Si cambia (nueva prueba de esfuerzo o cambio en TrainingPeaks), actualízala para que los cálculos nuevos la usen.",
                "- Fuente: respuesta determinista (lectura directa del perfil persistido).",
            ]
        )

    # Sin FC umbral en el perfil: se muestra la que detectó el reloj, sin guardarla. Se registra a mano con
    # /perfil umbral fc, que la pone a la vez en hr_threshold_bpm y en hr_threshold_tp_history (27/10: antes
    # esta ruta guardaba solo hr_threshold_bpm y el hrTSS de trail y bici seguía sin su referencia de TP).
    try:
        raw = await asyncio.wait_for(call_tool(mcp_session, "get_user_profile", {}), timeout=2.0)
        parsed = _try_parse_json(raw)
        payload = parsed if parsed is not None else raw
        live_bpm = _extract_hr_threshold_from_payload(payload)
    except (TimeoutError, asyncio.TimeoutError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
        live_bpm = None

    if live_bpm is not None:
        bpm_txt = int(round(live_bpm))
        return "\n".join(
            [
                "## 🧭 Resumen",
                f"No tienes la FC umbral registrada en Kairos; tu reloj Garmin tiene {bpm_txt} lpm.",
                "",
                "## 📊 Métricas clave",
                "| Métrica | Valor | Fuente |",
                "|---|---|---|",
                f"| FC umbral (LTHR) | {bpm_txt} lpm | Garmin (reloj) |",
                "",
                "## ✅ Recomendación",
                f"- Si es la misma que tienes en TrainingPeaks, regístrala con `/perfil umbral fc {bpm_txt}`: ancla el hrTSS "
                "de fuerza, elíptica y remo y la referencia de TrainingPeaks para el de trail y bici sin potencia.",
                "",
                "## 🎯 Próximo paso",
                "- Fuente: respuesta determinista (dato de Garmin, sin pasar por el LLM).",
            ]
        )

    return (
        "## 🧭 Resumen\n"
        "No tengo registrada tu FC umbral (LTHR) y Garmin no la devolvió.\n\n"
        "## 📊 Métricas clave\n"
        "| Métrica | Valor | Fuente |\n"
        "|---|---|---|\n"
        "| FC umbral (LTHR) | sin datos | perfil y Garmin |\n\n"
        "## ✅ Recomendación\n"
        "- Regístrala con `/perfil umbral fc <lpm>`, la misma que tengas en TrainingPeaks.\n\n"
        "## 🎯 Próximo paso\n"
        "- Fuente: respuesta determinista (perfil y Garmin, sin pasar por el LLM)."
    )


def _build_running_threshold_profile_markdown(profile: dict) -> str:
    """Construye respuesta determinista del ritmo umbral actual del perfil."""
    profile = profile if isinstance(profile, dict) else {}
    perf = profile.get("performance") if isinstance(profile.get("performance"), dict) else {}

    pace_sec = _resolve_running_threshold_pace_sec_per_km(profile)
    if not pace_sec or pace_sec <= 0:
        return (
            "## 🧭 Resumen\n"
            "No tengo un ritmo umbral de running configurado en tu perfil.\n\n"
            "## 📊 Métricas clave\n"
            "| Métrica | Valor | Fuente |\n"
            "|---|---|---|\n"
            "| Ritmo umbral running | sin datos | perfil persistido |\n\n"
            "## ✅ Recomendación\n"
            "- Configura tu umbral para mejorar recomendaciones y estimaciones de carga.\n\n"
            "## 🎯 Próximo paso\n"
            "- Puedes guardarlo con: /perfil umbral 4:15\n"
            "- Fuente: respuesta determinista (lectura directa del perfil persistido)."
        )

    def _fmt_pace(sec: float) -> str:
        return f"{int(sec // 60)}:{int(sec % 60):02d} min/km"

    today_iso = date.today().isoformat()
    current = _resolve_running_threshold_pace_for_date(profile, today_iso) or pace_sec
    pace_text = _fmt_pace(current)
    pace_date_d = _parse_iso_date_safe(perf.get("running_threshold_pace_date"))
    pace_date = pace_date_d.strftime("%d/%m/%Y") if pace_date_d else "sin fecha"
    source = "perfil persistido (comando /perfil umbral)"

    history = [
        e for e in (perf.get("running_threshold_pace_history") or [])
        if isinstance(e, dict) and _parse_iso_date_safe(e.get("date")) and e.get("sec_per_km")
    ]
    history_rows = [
        f"| Vigente desde {_parse_iso_date_safe(e['date']).strftime('%d/%m/%Y')} | {_fmt_pace(float(e['sec_per_km']))} | historial del perfil |"
        for e in sorted(history, key=lambda e: str(e["date"]))
    ]

    lines = [
        "## 🧭 Resumen",
        f"Tu ritmo umbral de carrera es {pace_text}.",
        "",
        "## 📊 Métricas clave",
        "| Métrica | Valor | Fuente |",
        "|---|---|---|",
        f"| Ritmo umbral running | {pace_text} | {source} |",
        *(history_rows or [f"| Fecha de actualización | {pace_date} | perfil persistido |"]),
        "",
        "## ✅ Recomendación",
        "- Usa este ritmo para calibrar sesiones de tempo y umbral. Cada actividad se calcula con el umbral vigente en su fecha.",
        "",
        "## 🎯 Próximo paso",
        "- Si quieres, te convierto este umbral en zonas de ritmo para entrenar.",
        "- Fuente: respuesta determinista (lectura directa del perfil persistido).",
    ]
    return "\n".join(lines)
