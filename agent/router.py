"""
agent/router.py
Enrutado sin LLM (docs/Harness.md, G5): detectores de intención de cada ruta determinista y ToolRouter, que decide la ruta de una pregunta. Banco de frases en tests/test_tool_router.py.
"""

import re
import unicodedata


from agent.dates import (
    _extract_iso_date_from_text,
    _extract_weekday_index_from_text,
)
from agent.plans import (
    _has_goal_in_profile,
)
from agent.routes.load import (
    _parse_recent_tss_sports_request,
)
from agent.routes.period import (
    _is_period_distance_intent,
    _is_period_longest_intent,
    _is_period_totals_intent,
)
from agent.routes.sleep import (
    _is_sleep_night_intent,
    _resolve_sleep_window,
)


class ToolRouter:
    """Router determinista para intenciones críticas."""

    def route_key(self, user_message: str, history: list[dict], profile: dict) -> str | None:
        if _is_config_options_intent(user_message):
            return "config_options"
        if _is_plan_status_intent(user_message):
            return "plan_status"
        if _is_goal_status_intent(user_message):
            return "goal_status"
        # Periodo (km, la más larga) y sueño de anoche: Python, no el LLM (punto 3 de producción, 25/10).
        if (_is_period_distance_intent(user_message) or _is_period_totals_intent(user_message)
                or _is_period_longest_intent(user_message)):
            return "period_activities"
        if _is_sleep_night_intent(user_message):
            return "sleep_night"
        if _resolve_sleep_window(user_message) is not None:
            return "sleep_trend"
        if _is_week_tss_intent(user_message):
            return "week_tss"
        if _is_week_tss_followup_intent(user_message, history):
            return "week_tss"
        if _is_week_activities_intent(user_message):
            return "week_activities"
        if _is_load_trend_intent(user_message):
            return "load_trend"
        if _is_hr_threshold_query_intent(user_message):
            return "hr_threshold"
        if _is_running_threshold_query_intent(user_message):
            return "running_threshold"
        if _is_today_load_status_intent(user_message):
            return "today_load_status"
        if _is_activity_details_query_intent(user_message):
            return "activity_details"
        if _is_recent_tss_by_sport_intent(user_message):
            return "recent_tss_by_sport"
        # Priorizar PRs antes de la ruta factual genérica para evitar
        # respuestas de "sin acceso" en consultas de récords personales.
        if _is_personal_records_intent(user_message) or _is_personal_records_followup_intent(user_message, history):
            return "personal_records"
        if _is_mcp_factual_query_intent(user_message):
            return "mcp_factual"
        if _is_daily_readiness_intent(user_message):
            return "daily_readiness"
        if _is_planning_intent(user_message, history) and _has_goal_in_profile(profile):
            return "planning"
        return None


def _is_planning_intent(user_message: str, history: list[dict] | None = None) -> bool:
    """Detecta intención de planificación en la consulta del usuario.

    También detecta confirmaciones cortas ("sí", "vale", "ok") cuando el
    turno anterior del asistente proponía explícitamente crear un plan activo.
    """
    text = (user_message or "").strip().lower()
    if not text:
        return False

    # Follow-up afirmativo corto después de propuesta explícita de crear plan.
    affirmative_markers = {
        "si", "sí", "ok", "vale", "dale", "adelante", "perfecto", "de acuerdo",
    }
    compact_text = re.sub(r"\s+", " ", re.sub(r"[!?.,;:¡¿]", "", text)).strip()
    if compact_text in affirmative_markers and history:
        recent_assistant = [
            str(msg.get("content") or "").lower()
            for msg in (history or [])[-6:]
            if msg.get("role") == "assistant"
        ]
        creation_prompts = (
            "si quieres, te preparo un plan activo",
            "si quieres, te preparo un plan",
            "te preparo un plan activo",
            "te preparo un plan a partir de ese objetivo",
            "no tienes plan asignado ahora mismo",
        )
        if any(any(marker in content for marker in creation_prompts) for content in recent_assistant):
            return True

    # Palabras que indican CREAR o MODIFICAR un plan, no consultar stats.
    # 'semana' y 'bloque' se eliminaron: son demasiado genéricas y
    # provocan falsos positivos en consultas de estadisticas ('cuantos km esta semana').
    planning_markers = [
        "plan", "planifica", "planificación", "planificacion",
        "preparar", "preparación", "preparacion",
        "macro", "microciclo",
    ]
    if not any(marker in text for marker in planning_markers):
        return False
    # Guardia anti-falso-positivo: consultas de estado de objetivo no son planificación.
    # 'objetivo' solo clasifica como planning si va acompañado de un verbo de acción.
    if "objetivo" in text and not any(m in text for m in ("preparar", "planifica", "alcanzar", "lograr", "conseguir")):
        return "plan" in text or any(m in text for m in ("macro", "microciclo", "preparaci"))
    return True


def _is_plan_status_intent(user_message: str) -> bool:
    """Detecta preguntas sobre si existe un plan activo o cuál es ese plan."""
    text = (user_message or "").strip().lower()
    if not text or "plan" not in text:
        return False

    # Peticiones de creación/planificación: no son consultas de estado.
    creation_markers = [
        "planifica", "planificación", "planificacion", "crear", "créame", "creame",
        "hazme", "diseña", "disena", "prepara", "recomienda", "recomiendas",
        "ajusta", "ajusta", "modifica", "cambia", "actualiza",
    ]
    if any(marker in text for marker in creation_markers):
        return False

    status_markers = [
        "tengo", "hay", "existe", "asignado", "asignada",
        "mi plan", "ese plan", "cuál es", "cual es", "qué plan", "que plan",
    ]
    return any(marker in text for marker in status_markers)


def _is_goal_status_intent(user_message: str) -> bool:
    """Detecta preguntas por objetivo deportivo (sin pedir planificación)."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    objective_markers = (
        "objetivo",
        "próximo objetivo",
        "proximo objetivo",
        "carrera objetivo",
        "evento objetivo",
        "meta",
    )
    if not any(marker in text for marker in objective_markers):
        return False

    planning_markers = (
        "planifíc",
        "planifica",
        "planificación",
        "planificacion",
        "crear plan",
        "preparar plan",
        "diseña",
        "disena",
    )
    if any(marker in text for marker in planning_markers):
        return False

    return True


def _is_week_tss_intent(user_message: str) -> bool:
    """Detecta consultas de TSS semanal para responder por ruta determinista.

    Esta intención evita respuestas generativas ambiguas y fuerza una salida
    basada en semana natural (lunes→domingo), acumulada hasta hoy.
    """
    text_raw = (user_message or "").strip().lower()
    if not text_raw:
        return False

    # Normalización robusta para tolerar acentos y artefactos de encoding
    # (ej.: "cu├ánto") que pueden llegar desde capturas de consola/pipeline.
    text_ascii = "".join(
        ch for ch in unicodedata.normalize("NFD", text_raw) if unicodedata.category(ch) != "Mn"
    )
    text_norm = re.sub(r"[^a-z0-9 ]+", " ", text_ascii)
    text_norm = re.sub(r"\s+", " ", text_norm).strip()

    # Resumen de una semana o su día de más carga, aunque no diga "TSS" (24/10: "¿cómo fue mi semana del 05/10
    # al 11/10?" agotaba las 15 llamadas a tools; "¿qué día de esta semana tuve más carga?" lo resolvía el LLM
    # leyendo mal la serie).
    week_summary_markers = (
        "como fue mi semana", "como ha ido mi semana", "que tal mi semana", "que tal fue mi semana",
        "que tal ha ido mi semana", "resumen de mi semana", "resumen de la semana", "resumen semanal",
    )
    if any(m in text_norm for m in week_summary_markers):
        return True
    if "semana" in text_norm and re.search(r"\bdia\b.*\bmas\b.*\b(carga|tss)\b", text_norm):
        return True

    if "tss" not in text_norm:
        return False

    # Consulta explícita de datos/cifras/comparativa semanal: priorizar respuesta directa.
    data_markers = [
        "cuanto", "cuánto", "cual", "cuál", "cuales", "cuáles", "dime", "consulta", "datos", "acumulado", "llevo",
        "hice", "he hecho", "total",
        "compara", "comparame", "compárame", "comparativa", "diferencia", "porcentaje", "porcentual", "vs",
        "spike", "semana pasada", "anterior",
        "contribuye", "contribuido", "contribucion", "contribución",
        "actividad", "actividades", "sesion", "sesión", "estimado por sesion", "estimado por sesión",
    ]
    week_markers = [
        "esta semana", "semana", "semanal", "acumulado semanal", "week",
    ]
    has_week = any(marker in text_raw for marker in week_markers) or any(marker in text_norm for marker in week_markers)
    has_data = any(marker in text_raw for marker in data_markers) or any(marker in text_norm for marker in data_markers)
    return has_week and has_data


def _is_week_tss_followup_intent(user_message: str, history: list[dict] | None = None) -> bool:
    """Detecta follow-up semanal de TSS cuando el usuario omite la palabra TSS.

    Ejemplo: "Y para la semana del 13 de agosto de 2026?" tras una respuesta
    previa de TSS semanal.
    """
    text = (user_message or "").strip().lower()
    if not text:
        return False

    # No capturar consultas explícitas de tendencia de carga/fatiga.
    if _is_load_trend_intent(user_message):
        return False

    # Debe ser una consulta semanal explícita.
    week_markers = ("semana", "esta semana", "semana pasada", "semana del", "week")
    if not any(marker in text for marker in week_markers):
        return False

    # Si ya menciona actividades/entrenamientos, lo gestiona la ruta week_activities.
    activity_markers = ("actividades", "entrenamientos", "que hice", "qué hice", "que entrene", "qué entrené")
    if any(marker in text for marker in activity_markers):
        return False

    # Evitar desviar intenciones de planificación semanal.
    planning_markers = ("plan", "planifica", "planificación", "planificacion", "microciclo", "macro")
    if any(marker in text for marker in planning_markers):
        return False

    # Solo aplicar como follow-up si el contexto reciente era claramente TSS semanal. Se mira solo la
    # parte determinista de cada respuesta: el texto del LLM varía entre pasadas y desviaba la ruta
    # (23/10: "si el TSS acumulado…" en un consejo mandó "¿qué sesión me recomiendas esta semana?" a week_tss).
    recent_assistant = [
        (msg.get("content") or "").lower().split("interpretación de coaching", 1)[0]
        for msg in (history or [])[-6:]
        if msg.get("role") == "assistant"
    ]
    weekly_tss_markers = (
        "consulta de tss semanal",
        "tss acumulado",
        "| tss acumulado |",
        "semana natural",
    )
    return any(any(marker in content for marker in weekly_tss_markers) for content in recent_assistant)


def _is_week_activities_intent(user_message: str) -> bool:
    """Detecta consultas de actividades de una semana (actual o por fecha)."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    activity_markers = [
        "actividades",
        "entrenamientos",
        "que hice",
        "qué hice",
        "que entrene",
        "qué entrené",
    ]
    week_markers = [
        "semana",
        "esta semana",
        "semana del",
    ]
    has_date = _extract_iso_date_from_text(user_message) is not None
    return any(m in text for m in activity_markers) and (any(w in text for w in week_markers) or has_date)


def _is_load_trend_intent(user_message: str) -> bool:
    """Detecta consultas de tendencia de carga/fatiga para ruta determinista."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    trend_markers = (
        "tendencia",
        "evolucion",
        "evolución",
        "ultimos",
        "últimos",
        "dias",
        "días",
        "semanas",
    )
    metric_markers = (
        "atl",
        "ctl",
        "tsb",
        "fatiga",
        "disponibilidad",
        "forma",
        "carga",
    )
    return any(m in text for m in trend_markers) and any(m in text for m in metric_markers)


def _is_mcp_factual_query_intent(user_message: str) -> bool:
    """Detecta consultas factuales que deben resolverse por MCP sin LLM."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    # Exclusiones: aquí no entran preguntas de planificación/coaching.
    coaching_markers = (
        "recomiendame el entrenamiento",
        "recomiéndame el entrenamiento",
        "recomiendame una sesion",
        "recomiéndame una sesión",
        "recomiendame una sesión",
        "plan",
        "ajusta",
        "ajusta",
        "deberia entrenar",
        "debería entrenar",
        "opinion",
        "opinión",
        "que te parece",
        "qué te parece",
        "analiza",
        "analisis",
        "análisis",
        "valoracion",
        "valoración",
    )
    if any(marker in text for marker in coaching_markers):
        return False

    # Exclusión: preguntas de evolución o de un periodo ("¿cómo ha evolucionado mi HRV en las
    # últimas dos semanas?"). Esta ruta responde con los datos de un día; las tendencias van al LLM,
    # que tiene get_hrv_trend, kairos_load_trends, etc.
    trend_markers = (
        "evoluci",
        "tendencia",
        "ha ido",
        "han ido",
        "ultimas",
        "últimas",
        "ultimos",
        "últimos",
        "este mes",
        "semanas",
        "meses",
        "historico",
        "histórico",
        "progres",
        # 24/10: "compara mi HRV de esta semana con la pasada" devolvía la tabla de un solo día.
        "compar",
        "frente a",
        "semana",
    )
    if any(marker in text for marker in trend_markers):
        return False

    factual_markers = (
        "tss",
        "hrv",
        "body battery",
        "sueno",
        "sueño",
        "dormi",
        "dormí",
        "sleep",
        "fc en reposo",
        "frecuencia cardiaca en reposo",
        "frecuencia cardíaca en reposo",
        "rhr",
        "pulso en reposo",
        "entrenamiento reciente",
        "actividad de ayer",
        "actividades de ayer",
        "que entrene ayer",
        "qué entrené ayer",
        "que entrene ayer",
        "que entrene hoy",
        "qué entrené hoy",
        "que entrene hoy",
        "que hice ayer",
        "qué hice ayer",
        "que hice hoy",
        "qué hice hoy",
        "entrenamiento de ayer",
        "entrenamiento de hoy",
        "datos de mi entrenamiento",
        "datos del entrenamiento",
        "resumen del entrenamiento",
        "resumen de mi entrenamiento",
        "detalles del entrenamiento",
    )
    return any(marker in text for marker in factual_markers)


def _is_today_load_status_intent(user_message: str) -> bool:
    """Detecta consultas de estado TSS/ATL/CTL/TSB con regla aplicada para hoy."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    today_markers = (" hoy", "hoy ", "de hoy", "today")
    load_markers = (
        "carga",
        "fatiga",
        "tss",
        "atl",
        "ctl",
        "tsb",
        "estado de carga",
        "estado carga",
    )
    rule_markers = (
        "regla aplicada",
        "regla",
        "criterio",
        "rule",
    )

    has_today = any(m in text for m in today_markers)
    has_load = any(m in text for m in load_markers)
    has_rule = any(m in text for m in rule_markers)
    return has_today and has_load and has_rule


def _is_coaching_recommendation_intent(user_message: str) -> bool:
    """Detecta si el usuario pide interpretación/recomendación sobre datos.

    Esta señal activa la capa híbrida: primero datos deterministas, luego
    interpretación de coaching por LLM sobre esos datos.
    """
    text = (user_message or "").strip().lower()
    if not text:
        return False

    markers = (
        "recomienda",
        "recomiendas",
        "recomend",
        "recomiendame",
        "recomiéndame",
        "me recomiendas",
        "que hago",
        "qué hago",
        "que me recomiendas",
        "qué me recomiendas",
        "conviene",
        "calidad",
        "rodaje suave",
        "descanso",
        "justifica",
        "interpret",
        "opinion",
        "opinión",
        "forma fisica",
        "forma física",
        "estado de forma",
        "como estoy de forma",
        "cómo estoy de forma",
        "puedo entrenar",
        "necesito recuperar",
        "me toca",
        "mañana",
        "esta semana dado",
    )
    return any(m in text for m in markers)


def _is_activity_details_query_intent(user_message: str) -> bool:
    """Detecta peticiones de detalle/métricas de una sesión concreta (hoy/ayer/fecha)."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    activity_markers = ("entrenamiento", "actividad", "sesion", "sesión")
    # "Última salida / carrera / rodaje / tirada" (24/10: "¿qué tal fue mi última salida en bici?" la resolvía el LLM
    # y se equivocaba de fecha; esta ruta ya sabe filtrar la última por deporte). Sueltas no cuentan: "¿calidad,
    # rodaje suave o descanso hoy?" es una pregunta de estado, no de una actividad.
    last_named_markers = (
        "ultima salida", "última salida", "ultima carrera", "última carrera",
        "ultimo rodaje", "último rodaje", "ultima tirada", "última tirada",
        # 28/10: "¿qué tal mi último trail?" y "feedback de mi último entreno" caían en la precarga de chat().
        "ultimo trail", "último trail", "ultimo entreno", "último entreno",
    )
    detail_markers = (
        "datos",
        "detalle",
        "detalles",
        "resumen",
        "examina",
        "examina",
        "examinar",
        "revisa",
        "revisar",
        "analiza",
        "analisis",
        "análisis",
        "metricas",
        "métricas",
        "cuales fueron",
        "cuáles fueron",
        "como fue",
        "cómo fue",
        "que tal",
        "qué tal",
        "feedback",
        "valora",
        "opinas",
    )
    has_month_name_date = re.search(
        r"\b\d{1,2}\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)(?:\s+de\s+\d{2,4})?\b",
        text,
    ) is not None

    has_day = (
        "hoy" in text
        or "ayer" in text
        or "anteayer" in text
        or has_month_name_date
        or re.search(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", text) is not None
        or _extract_iso_date_from_text(user_message) is not None
        or _extract_weekday_index_from_text(user_message) is not None
    )
    has_last_marker = any(
        m in text
        for m in (
            "ultima actividad",
            "última actividad",
            "ultima sesion",
            "última sesión",
            "ultimo entrenamiento",
            "último entrenamiento",
            *last_named_markers,
        )
    )
    has_temporal_reference = has_day or has_last_marker
    has_activity_word = any(m in text for m in activity_markers) or any(m in text for m in last_named_markers)
    has_detail_word = any(m in text for m in detail_markers)

    has_slash_date = re.search(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", text) is not None
    has_textual_day_month = re.search(r"\b\d{1,2}\s+de\s+[a-záéíóúñ]+(?:\s+de\s+\d{2,4})?\b", text) is not None

    # Si hay referencia explícita a fecha + actividad, tratarlo como detalle
    # aunque "cómo/como" llegue con codificación degradada.
    if has_activity_word and has_temporal_reference and (has_slash_date or has_textual_day_month):
        return True

    return has_temporal_reference and has_activity_word and has_detail_word


def _is_recent_tss_by_sport_intent(user_message: str) -> bool:
    """Detecta petición de TSS de las últimas N actividades/entrenamientos de uno o varios deportes."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    if "tss" not in text:
        return False
    if not re.search(r"\bultim|\búltim|\brecientes?\b", text):
        return False
    if not re.search(r"activ|entren|sesion|sesión", text):
        return False

    sports, _ = _parse_recent_tss_sports_request(user_message)
    return bool(sports)


def _is_running_threshold_query_intent(user_message: str) -> bool:
    """Detecta consultas sobre ritmo umbral de running guardado en perfil."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    # Si la consulta habla explícitamente de FC/HR umbral, no es ritmo umbral.
    hr_markers = (
        "fc umbral",
        "umbral fc",
        "frecuencia cardiaca umbral",
        "frecuencia cardíaca umbral",
        "heart rate threshold",
        "hr threshold",
        "lthr",
    )
    if any(marker in text for marker in hr_markers):
        return False

    markers = (
        "ritmo umbral",
        "umbral running",
        "umbral de running",
        "umbral de carrera",
        "pace umbral",
        "running threshold",
        "threshold pace",
    )
    return any(marker in text for marker in markers)


def _is_tomorrow_workout_intent(user_message: str) -> bool:
    """Detecta peticiones de propuesta de entrenamiento para mañana."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    planning_commands = (
        "programa",
        "planifica",
        "crea",
        "diseña",
        "ajusta",
        "modifica",
    )
    if any(marker in text for marker in planning_commands):
        return False

    tomorrow_markers = (
        "mañana",
        "manana",
        "tomorrow",
        "proxima sesion",
        "próxima sesión",
    )
    workout_markers = (
        "entrenamiento",
        "sesion",
        "sesión",
        "recomiendas",
        "propones",
        "que hago",
        "qué hago",
    )
    return any(marker in text for marker in tomorrow_markers) and any(marker in text for marker in workout_markers)


def _is_post_activity_feedback_intent(user_message: str) -> bool:
    """Detecta consultas de valoración/análisis de una actividad ya realizada."""
    text = (user_message or "").strip().lower()
    if not text:
        return False
    activity_markers = (
        "entrenamiento",
        "actividad",
        "sesion",
        "sesión",
    )
    feedback_markers = (
        "opinion",
        "opinión",
        "analiza",
        "análisis",
        "analisis",
        "que tal",
        "qué tal",
        "como ves",
        "cómo ves",
    )
    has_date = _extract_iso_date_from_text(user_message) is not None
    return has_date and any(marker in text for marker in activity_markers) and any(marker in text for marker in feedback_markers)


def _is_config_options_intent(user_message: str) -> bool:
    """Detecta preguntas sobre qué parámetros/opciones puede configurar el atleta."""
    text_raw = (user_message or "").strip()
    if not text_raw:
        return False

    text = text_raw.lower()
    text_ascii = "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )
    text_clean = re.sub(r"[^a-z0-9/ ]+", " ", text_ascii)
    text_clean = re.sub(r"\s+", " ", text_clean).strip()

    if text_clean in {"/menu", "menu"}:
        return True

    markers = (
        "que opciones puedo cambiar",
        "qué opciones puedo cambiar",
        "que opciones puedo configurar",
        "qué opciones puedo configurar",
        "que puedo cambiar",
        "qué puedo cambiar",
        "que puedo configurar",
        "qué puedo configurar",
        "que opciones tengo",
        "qué opciones tengo",
        "que parametros puedo cambiar",
        "qué parámetros puedo cambiar",
        "opciones de perfil",
    )
    if any(marker in text for marker in markers):
        return True

    # Variante robusta: detectar "opciones/parámetros" + verbo de acción.
    has_subject = any(tok in text_clean for tok in ("opciones", "parametros", "perfil"))
    has_action = any(tok in text_clean for tok in ("cambiar", "configurar", "editar", "modificar", "ajustar"))
    has_modal = any(tok in text_clean for tok in ("puedo", "puedes", "podria", "podrias"))
    return has_subject and (has_action or has_modal)


def _is_hr_threshold_query_intent(user_message: str) -> bool:
    """Detecta consultas de FC umbral (LTHR) para ruta determinista rápida."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    markers = (
        "fc umbral",
        "umbral fc",
        "umbral de fc",
        "umbral de frecuencia cardiaca",
        "umbral de frecuencia cardíaca",
        "frecuencia cardiaca umbral",
        "frecuencia cardíaca umbral",
        "heart rate threshold",
        "hr threshold",
        "lthr",
    )
    return any(marker in text for marker in markers)


def _is_daily_readiness_intent(user_message: str) -> bool:
    """Detecta consultas sobre estado de hoy y recomendación de entrenamiento.

    Se usa para forzar una respuesta determinista basada en snapshot real
    y evitar cifras inventadas por el LLM.
    """
    text = (user_message or "").strip().lower()
    if not text:
        return False

    explicit_markers = [
        "como estoy hoy para entrenar",
        "cómo estoy hoy para entrenar",
        "como estoy",
        "cómo estoy",
        "como me encuentro",
        "cómo me encuentro",
        "como me encuentro hoy",
        "cómo me encuentro hoy",
        "que me toca hoy",
        "qué me toca hoy",
        "me toca hoy",
        "que me recomiendas hoy",
        "qué me recomiendas hoy",
        "que tipo de sesion me recomiendas para esta semana",
        "qué tipo de sesión me recomiendas para esta semana",
        "tipo de sesion me recomiendas para esta semana",
        "tipo de sesión me recomiendas para esta semana",
        "que hago hoy",
        "qué hago hoy",
        "puedo entrenar hoy",
        "puedo entrenar mañana",
        "puedo entrenar fuerte",
        # 24/10: "¿puedo hacer series hoy?" lo resolvía el LLM sin la decisión del motor ni datos de hoy.
        "puedo hacer series",
        "puedo hacer calidad",
        "puedo hacer intensidad",
        "puedo meter series",
        "puedo meter calidad",
        "puedo apretar",
        "puedo correr hoy",
        "puedo correr mañana",
        "estoy para entrenar hoy",
        "estoy para entrenar mañana",
        # 28/10: estas iban al LLM libre sin la decisión del motor (misma pregunta, otro consejo).
        "listo para entrenar",
        "lista para entrenar",
        "entreno o descanso",
        "entreno hoy o descanso",
        "entrenar o descansar",
        "entreno hago hoy",
        "entrenamiento hago hoy",
        "sesion hago hoy",
        "sesión hago hoy",
        "estoy cansado",
        "estoy cansada",
        "estoy fatigado",
        "estoy fatigada",
        "necesito recuperar",
        "necesito descansar",
        "training readiness",
        "readiness hoy",
        "forma fisica",
        "forma física",
        "estado de forma",
        "como estoy de forma",
        "cómo estoy de forma",
        "ctl",
        "atl",
        "tsb",
    ]
    if any(marker in text for marker in explicit_markers):
        return True

    # Heurística robusta para prompts de recomendación semanal aunque
    # "sesión/situación" llegue con codificación degradada.
    if "recom" in text and "semana" in text and "estado" in text:
        return True

    # Heurística robusta para "forma física hoy".
    if "hoy" in text and "forma" in text:
        return True

    today_markers = (" hoy", "hoy ", " hoy?", "today")
    status_markers = (
        "como estoy",
        "cómo estoy",
        "como me encuentro",
        "cómo me encuentro",
        "estado",
        "recuperacion",
        "recuperación",
        "recovery",
        "body battery",
        "hrv",
        "sueno",
        "sueño",
        "fc en reposo",
        "frecuencia cardiaca en reposo",
        "frecuencia cardíaca en reposo",
    )
    if any(m in text for m in today_markers) and any(m in text for m in status_markers):
        return True

    return False


_TODAY_TRAINING_WORDS = re.compile(
    r"\b(entren\w*|sesi[oó]n|series|rodaje|tirada|correr|corro|calidad|intensidad|fuerte|suave|descans\w*|"
    r"recuper\w*|cansad[oa]|fatigad[oa]|apretar|recomiend\w*|conviene|deber[ií]a|hago|toca)\b"
)


# Lo que ya pasó hoy (análisis de una sesión hecha) no es "qué hago hoy".
_TODAY_PAST_WORDS = re.compile(r"\b(hice|he hecho|hecho|fue|hizo|he corrido|corr[ií]|he entrenado|entren[eé]|analiza\w*)\b")


def _is_today_training_question(user_message: str) -> bool:
    """Pregunta de qué entrenar hoy que va al bucle con tools (G15, 28/10).

    El LLM libre recibe entonces la decisión del motor diario y el marco de la sesión, como la capa de
    coaching: "¿Qué entreno hago hoy?" y "¿Qué hago hoy?" no pueden recibir consejos distintos.
    """
    text = (user_message or "").lower()
    if not re.search(r"\bhoy\b", text):
        return False
    return bool(_TODAY_TRAINING_WORDS.search(text)) and not _TODAY_PAST_WORDS.search(text)


def _is_personal_records_intent(user_message: str) -> bool:
    """Detecta intención de consultar récords personales de running."""
    text_raw = (user_message or "").strip()
    if not text_raw:
        return False

    text = text_raw.lower()
    text_ascii = "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )
    text_fuzzy = re.sub(r"[^a-z0-9 ]+", "", text_ascii)

    # Variante robusta por tokens para cubrir: "récords personales",
    # "records personales", "record personal", "marcas personales", etc.
    has_record_family = any(
        tok in text_ascii or tok in text_fuzzy
        for tok in ("record", "records", "rcord", "rcords", "marca", "marcas", "pr")
    )
    has_personal_family = any(tok in text_ascii or tok in text_fuzzy for tok in ("personal", "personales"))
    if has_record_family and has_personal_family:
        return True

    markers = [
        "record personal",
        "records personales",
        "récord personal",
        "récords personales",
        "mejores registros",
        "personal records",
        "pr de",
        "mejores marcas",
        "marcas personales",
    ]
    if any(marker in text for marker in markers):
        return True
    # Récord o mejor tiempo de una distancia (28/10): "¿mi récord de 10k?", "¿mi mejor tiempo en media maratón?",
    # "¿cuál fue mi mejor 10k?". Iban al LLM libre.
    distance = r"(?:\d+(?:[.,]\d+)?\s*(?:k|km|kms|kilometros)|maraton|media(?:\s+maraton)?|milla)\b"
    return bool(
        re.search(rf"\b(?:record|mejor\s+tiempo|mejor\s+marca|pb)\b.{{0,25}}?\b{distance}", text_ascii)
        or re.search(rf"\bmejor(?:es)?\s+{distance}", text_ascii)
    )


def _is_personal_records_followup_intent(user_message: str, history: list[dict]) -> bool:
    """Detecta follow-up tipo "en qué distancias son esas marcas"."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    followup_markers = [
        "esas marcas",
        "que distancias",
        "en que distancias",
        "qué distancias",
        "de que distancia",
        "de qué distancia",
    ]
    if not any(marker in text for marker in followup_markers):
        return False

    recent_assistant = [
        (msg.get("content") or "").lower()
        for msg in (history or [])[-6:]
        if msg.get("role") == "assistant"
    ]
    return any("mejores registros personales" in content for content in recent_assistant)


def _detect_personal_records_sport_intent(user_message: str, history: list[dict] | None = None) -> str:
    """Detecta el deporte objetivo para consulta de PRs: running o cycling."""
    text = (user_message or "").strip().lower()
    cycling_markers = ["ciclismo", "ciclista", "bici", "bike", "cycling"]
    running_markers = ["running", "correr", "carrera", "marat", "10k", "5k"]

    if any(marker in text for marker in cycling_markers):
        return "cycling"
    if any(marker in text for marker in running_markers):
        return "running"

    recent_assistant = [
        (msg.get("content") or "").lower()
        for msg in (history or [])[-6:]
        if msg.get("role") == "assistant"
    ]
    if any("registros personales en ciclismo" in content for content in recent_assistant):
        return "cycling"
    if any("registros personales en running" in content for content in recent_assistant):
        return "running"

    return "running"
