"""
agent/dates.py
Fechas y formatos del chat: fechas escritas por el usuario (DD/MM, rangos, días de la semana), fechas imposibles y formatos de duración, distancia y número.
"""

import re
from datetime import date, datetime, timedelta
from typing import Any


def _seconds_to_hhmmss(seconds: float) -> str:
    """Convierte segundos a HH:MM:SS."""
    total = int(round(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _seconds_to_mmss_or_hhmmss(seconds: float) -> str:
    """Convierte segundos a MM:SS (<1h) o HH:MM:SS (>=1h)."""
    total = int(round(seconds))
    if total < 3600:
        m = total // 60
        s = total % 60
        return f"{m:02d}:{s:02d}"
    return _seconds_to_hhmmss(total)


def _parse_iso_date_safe(raw: Any) -> date | None:
    """Parsea fechas ISO (YYYY-MM-DD o datetime ISO) de forma tolerante."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _resolve_target_date_from_message(user_message: str) -> date:
    """Resuelve la fecha objetivo (hoy/ayer/anteayer/ISO) para consultas factuales."""
    text = (user_message or "").strip().lower()
    explicit_iso = _extract_iso_date_from_text(user_message)
    if explicit_iso:
        try:
            return date.fromisoformat(explicit_iso)
        except ValueError:
            pass

    if "anteayer" in text:
        return date.today() - timedelta(days=2)
    if "hoy" in text:
        return date.today()
    weekday_idx = _extract_weekday_index_from_text(text)
    if weekday_idx is not None:
        today_idx = date.today().isoweekday()
        # Devuelve el día de semana más reciente (incluye hoy si coincide).
        delta_days = (today_idx - weekday_idx) % 7
        return date.today() - timedelta(days=delta_days)
    if "ayer" in text:
        return date.today() - timedelta(days=1)
    return date.today()


def _extract_weekday_index_from_text(value: str) -> int | None:
    """Extrae día de semana desde lenguaje natural y devuelve ISO weekday (1=lunes..7=domingo)."""
    text = (value or "").strip().lower()
    if not text:
        return None

    patterns: list[tuple[str, int]] = [
        (r"\b(lunes|lun|monday|mon)\b", 1),
        (r"\b(martes|mar|tuesday|tue)\b", 2),
        (r"\b(mi[eé]rcoles|mie|mi[eé]|wednesday|wed)\b", 3),
        (r"\b(jueves|jue|thursday|thu)\b", 4),
        (r"\b(viernes|vie|friday|fri)\b", 5),
        (r"\b(s[áa]bado|sab|saturday|sat)\b", 6),
        (r"\b(domingo|dom|sunday|sun)\b", 7),
    ]
    for pattern, idx in patterns:
        if re.search(pattern, text):
            return idx
    return None


_EXPLICIT_DATE_RE = re.compile(r"(?<![\d/:.])(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?(?![\d/:])")


def _find_impossible_date(user_message: str) -> str | None:
    """Fecha DD/MM(/AAAA) con día y mes plausibles pero inexistente (31/02, 31/04…), o None.

    24/10: "¿qué actividades hice el 31/02/2026?" se respondía con los datos de hoy sin avisar.
    """
    for m in _EXPLICIT_DATE_RE.finditer(user_message or ""):
        day, month = int(m.group(1)), int(m.group(2))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            continue
        year_raw = m.group(3)
        year = date.today().year if not year_raw else (2000 + int(year_raw) if len(year_raw) == 2 else int(year_raw))
        try:
            date(year, month, day)
        except ValueError:
            return m.group(0)
    return None


def _format_iso_date_es(value: Any) -> str:
    """Convierte fechas ISO (YYYY-MM-DD o ISO datetime) a DD/MM/AAAA para usuario."""
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # Caso ISO date/datetime común
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        pass
    # Intento conservador con solo la parte de fecha
    if len(text) >= 10:
        try:
            return date.fromisoformat(text[:10]).strftime("%d/%m/%Y")
        except ValueError:
            return text
    return text


_MONTH_NAMES_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
                   "octubre", "noviembre", "diciembre")


def _fmt_km(meters: float) -> str:
    return f"{meters / 1000:.2f} km"


_WEEKDAY_NAMES_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _signed(value: float) -> str:
    """Diferencia con signo y sin "-0" ni "+0": si redondea a cero, "0"."""
    rounded = round(value)
    return f"{rounded:+d}" if rounded else "0"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_trend_date_range(tool_name: str, arguments: dict) -> dict:
    """Ajusta rangos de fechas para herramientas trend según límites MCP."""
    if not isinstance(arguments, dict):
        return {}

    max_days_by_tool = {
        "get_training_load_trend": 90,
        "get_vo2max_trend": 90,
        "get_hrv_trend": 30,
        "get_rhr_trend": 90,
        "get_stress_trend": 90,
    }
    max_days = max_days_by_tool.get(tool_name)
    if not max_days:
        return arguments

    args = dict(arguments)
    today = date.today()

    start_key = "start_date" if "start_date" in args else "startDate" if "startDate" in args else None
    end_key = "end_date" if "end_date" in args else "endDate" if "endDate" in args else None
    if not start_key and not end_key:
        return args

    def _to_date(value: Any) -> date | None:
        if not isinstance(value, str):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    end_date = _to_date(args.get(end_key)) if end_key else None
    start_date = _to_date(args.get(start_key)) if start_key else None

    if end_date is None or end_date > today:
        end_date = today
    if start_date is None or start_date > end_date:
        start_date = end_date - timedelta(days=max_days)

    if (end_date - start_date).days > max_days:
        start_date = end_date - timedelta(days=max_days)

    if start_key:
        args[start_key] = start_date.isoformat()
    if end_key:
        args[end_key] = end_date.isoformat()
    return args


# Palabras clave de fecha que algunos LLMs envían en lugar de fechas ISO
_TODAY_KEYWORDS = {"hoy", "today", "今日", "今天", "ahora", "now", "current", "actual", "este dia"}


_YESTERDAY_KEYWORDS = {"ayer", "yesterday", "昨日", "昨天"}


_MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def _normalize_date_args(arguments: dict) -> dict:
    """Normaliza parámetros de fecha de las llamadas a herramientas.

    Convierte palabras clave de fecha al formato ISO YYYY-MM-DD que requiere
    la API de Garmin Connect. Previene HTTP 404 cuando el LLM pasa 'hoy',
    'ayer', 'today', etc. como valor de fecha en lugar de la cadena ISO.
    """
    DATE_FIELDS = {"date", "startDate", "endDate", "start_date", "end_date"}
    today = date.today()
    yesterday = today - timedelta(days=1)

    result = {}
    for key, value in arguments.items():
        if key in DATE_FIELDS and isinstance(value, str):
            v_lower = value.strip().lower()
            if v_lower in _TODAY_KEYWORDS:
                result[key] = today.isoformat()
            elif v_lower in _YESTERDAY_KEYWORDS:
                result[key] = yesterday.isoformat()
            else:
                parsed_iso = _extract_iso_date_from_text(value)
                result[key] = parsed_iso if parsed_iso else value
        else:
            result[key] = value
    return result


def _extract_iso_date_from_text(value: str) -> str | None:
    """Extrae una fecha ISO YYYY-MM-DD desde texto libre en español/inglés."""
    if not isinstance(value, str):
        return None

    text = value.strip().lower()
    if not text:
        return None

    # Coincidencia exacta (el texto completo es la palabra clave)
    if text in _TODAY_KEYWORDS:
        return date.today().isoformat()
    if text in _YESTERDAY_KEYWORDS:
        return (date.today() - timedelta(days=1)).isoformat()

    # Palabra clave como token dentro de un mensaje más largo
    # ej: "Analiza mi actividad de ayer" → "ayer" está en el texto
    _words = set(re.split(r"\W+", text))
    if _words & _TODAY_KEYWORDS:
        return date.today().isoformat()
    if _words & _YESTERDAY_KEYWORDS:
        return (date.today() - timedelta(days=1)).isoformat()

    # yyyy-mm-dd
    m_iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m_iso:
        try:
            return date.fromisoformat(m_iso.group(1)).isoformat()
        except ValueError:
            pass

    # dd/mm/yyyy o dd-mm-yyyy
    m_slash = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text)
    if m_slash:
        d, mth, y = int(m_slash.group(1)), int(m_slash.group(2)), int(m_slash.group(3))
        try:
            return date(y, mth, d).isoformat()
        except ValueError:
            pass

    # dd/mm/yy o dd-mm-yy (año corto)
    m_slash_short = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2})\b", text)
    if m_slash_short:
        d = int(m_slash_short.group(1))
        mth = int(m_slash_short.group(2))
        yy = int(m_slash_short.group(3))
        # Ventana razonable para datos deportivos actuales.
        y = 2000 + yy if yy <= 69 else 1900 + yy
        try:
            return date(y, mth, d).isoformat()
        except ValueError:
            pass

    # dd/mm o dd-mm (sin año): asumir año actual
    m_day_month = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", text)
    if m_day_month:
        d = int(m_day_month.group(1))
        mth = int(m_day_month.group(2))
        y = date.today().year
        try:
            return date(y, mth, d).isoformat()
        except ValueError:
            pass

    # 2 de julio de 2026 / 2 julio 2026 / 2 de julio
    m_month = re.search(
        r"\b(\d{1,2})\s*(?:de\s+)?([a-záéíóúñ]+)\s*(?:de\s*)?(\d{4})?\b",
        text,
        flags=re.IGNORECASE,
    )
    if m_month:
        d = int(m_month.group(1))
        month_name = (
            m_month.group(2)
            .replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
        )
        mth = _MONTHS_ES.get(month_name)
        if mth:
            y = int(m_month.group(3)) if m_month.group(3) else date.today().year
            try:
                return date(y, mth, d).isoformat()
            except ValueError:
                return None

    return None


def _extract_iso_date_range_from_text(value: str, today_d: date | None = None) -> tuple[str, str] | None:
    """Extrae un rango ISO (inicio, fin) desde texto natural.

    Soporta: "del 27/07 al 02/08", "entre 2026-07-27 y 2026-08-02",
    con o sin año explícito.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None

    ref_year = (today_d or date.today()).year
    token_re = r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}-\d{2}-\d{2}"

    def _parse_token(token: str) -> date | None:
        tok = (token or "").strip()
        if not tok:
            return None
        if re.fullmatch(r"\d{1,2}[/-]\d{1,2}", tok):
            tok = f"{tok}/{ref_year}"
        iso = _extract_iso_date_from_text(tok)
        if not iso:
            return None
        try:
            return date.fromisoformat(iso)
        except ValueError:
            return None

    patterns = [
        rf"\b(?:semana\s+)?del\s+({token_re})\s+al\s+({token_re})\b",
        rf"\b(?:semana\s+)?del\s+({token_re})\s+a\s+({token_re})\b",
        rf"\bentre\s+(?:el\s+)?({token_re})\s+y\s+(?:el\s+)?({token_re})\b",
        rf"\bdesde\s+(?:el\s+)?({token_re})\s+hasta\s+(?:el\s+)?({token_re})\b",
    ]
    m = None
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            break
    if not m:
        return None

    start_raw = m.group(1)
    end_raw = m.group(2)
    start_d = _parse_token(start_raw)
    end_d = _parse_token(end_raw)
    if not start_d or not end_d:
        return None

    def _has_explicit_year(token: str) -> bool:
        tok = (token or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", tok):
            return True
        parts = re.split(r"[/-]", tok)
        return len(parts) == 3

    start_has_year = _has_explicit_year(start_raw)
    end_has_year = _has_explicit_year(end_raw)
    if end_d < start_d and not start_has_year and not end_has_year:
        try:
            end_d = date(start_d.year + 1, end_d.month, end_d.day)
        except ValueError:
            return None

    if end_d < start_d:
        return None

    return start_d.isoformat(), end_d.isoformat()
