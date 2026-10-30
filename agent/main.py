"""
main.py
Punto de entrada del agente entrenador personal Kairos Coach.
Interfaz de conversación en terminal.
"""

import asyncio
import importlib
import threading
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler


# Forzar UTF-8 en Windows para mostrar correctamente acentos y ñ en terminal.
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleCP(65001)
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except (AttributeError, OSError):
        pass

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from dotenv import find_dotenv, load_dotenv, set_key
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table

# Cargar variables de entorno desde .env
load_dotenv(encoding="utf-8")


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _configure_logging() -> tuple[logging.Logger, bool]:
    """Configura logging con parámetros de entorno para producción."""
    level_name = (os.environ.get("KAIROS_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    log_file = (os.environ.get("KAIROS_LOG_FILE") or "agent.log").strip()
    # Por defecto, solo al fichero: en la terminal los logs se mezclaban con la conversación (24/10).
    log_stdout = _env_bool("KAIROS_LOG_STDOUT", False)

    handlers: list[logging.Handler] = []
    if log_file:
        # Rotativo: agent.log crecía sin límite (6,3 MB el 23/10).
        handlers.append(RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"))
    if log_stdout:
        handlers.append(logging.StreamHandler(sys.stdout))
    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )

    # Reduce ruido de librerías HTTP en producción.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    return logging.getLogger(__name__), _env_bool("KAIROS_DEBUG_CONSOLE", False)


# El logging se configura al arrancar Kairos (main), no al importar el módulo: importarlo (tests,
# herramientas) abría agent.log y escribía en él (24/10).
log = logging.getLogger(__name__)
_DEBUG_CONSOLE = False


def _debug_console(message: str) -> None:
    """Muestra trazas de consola solo si KAIROS_DEBUG_CONSOLE=true."""
    if _DEBUG_CONSOLE:
        console.print(message)

# Parchear SSL para usar el almacén de certificados del sistema (necesario con Zscaler)
try:
    import truststore
    truststore.inject_into_ssl()
except (ImportError, AttributeError, RuntimeError):
    pass

# Añadir el directorio raíz al path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent import diabetes as _diabetes
from agent import ia_provider as _ia_provider
from agent.mcp.client import garmin_mcp_session
from agent.trainer_agent import (
    TrainerAgent,
)
from agent import storage as _storage
from agent.storage import (
    activate_training_plan,
    check_supabase_connection,
    create_app_user,
    decrypt_password,
    encrypt_password,
    get_active_user,
    get_ia_provider,
    get_training_plan,
    is_zscaler_network as _detect_zscaler,
    list_training_plans,
    list_training_plan_sessions,
    load_athlete_knowledge,
    restore_session,
    save_athlete_knowledge,
    save_ia_provider,
    sign_in,
    sign_up,
    update_login_password,
    update_user_credentials,
)
from agent.routes.load import _build_load_trend_table
from agent.daily_engine import _session_intensity_label, _session_type_label
from agent.plans import _DAY_LETTERS, _format_days, _is_block_plan, _parse_day_limits, _parse_day_list, _plan_detail_markdown
from agent.data_access import _TSS_FORMULA_VERSION, _load_metrics_view, _load_user_profile, _save_user_profile
from agent.llm import probe_llm_key


async def _sync_from_garmin(agent, profile_raw: str | None = None) -> list[str]:
    """
    Siempre se ejecuta al arrancar.
    Obtiene nombre, edad, género, peso y altura desde Garmin Connect
    y actualiza el perfil local sin hacer ninguna pregunta.
    profile_raw: get_user_profile ya pedido al comprobar el acceso a Garmin (no se pide dos veces).
    """
    console.print("[dim]Sincronizando datos personales desde Garmin Connect...[/]")
    try:
        garmin_data = await agent.fetch_garmin_personal_data(profile_raw=profile_raw)
    except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
        log.debug("_sync_from_garmin: error leyendo datos MCP: %s", exc)
        garmin_data = {}

    if not garmin_data:
        console.print("[dim yellow]No se pudieron obtener datos personales de Garmin.[/]")
        return []

    profile = _load_user_profile()
    p = profile.setdefault("personal", {})

    updated = []
    labels = {"name": "nombre", "age": "edad", "gender": "género",
               "weight_kg": "peso", "height_cm": "altura"}
    for field, value in garmin_data.items():
        if p.get(field) != value:
            p[field] = value
            if field in labels:
                updated.append(labels[field])

    if not updated:
        console.print("[dim]✓ Datos de Garmin sin cambios.[/]")
        return []

    try:
        _save_user_profile(profile)
    except Exception as exc:
        log.warning("_sync_from_garmin: no se pudo guardar perfil en Supabase: %s", exc)
        console.print(
            "[yellow]⚠ No se pudo guardar el perfil en Supabase (timeout transitorio). "
            "Continuo con el arranque.[/]"
        )
        return []

    console.print(f"[green]✓[/] Garmin → perfil actualizado: [dim]{', '.join(updated)}[/]")
    return updated


def _is_first_time() -> bool:
    """
    Devuelve True si el usuario activo no ha completado el setup de objetivos.
    Se detecta por la ausencia de 'setup_complete'.
    """
    profile = _load_user_profile()
    return not profile.get("setup_complete", False)


def _validate_date(value: str) -> tuple[bool, str]:
    """Valida formato YYYY-MM-DD y que la fecha sea futura. Devuelve (valido, mensaje_error)."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return False, "Formato incorrecto. Usa YYYY-MM-DD (ej: 2026-07-02)"
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return False, "Fecha inválida (mes o día fuera de rango)"
    if d <= date.today():
        return False, f"La fecha debe ser futura (hoy es {date.today().isoformat()})"
    return True, ""


def _validate_time(value: str) -> tuple[bool, str]:
    """Valida formato H:MM:SS o HH:MM:SS. Devuelve (valido, mensaje_error)."""
    m = re.match(r"^(\d{1,3}):(\d{2}):(\d{2})$", value)
    if not m:
        return False, "Formato incorrecto. Usa H:MM:SS (ej: 9:30:00) o HH:MM:SS (ej: 13:45:00)"
    h, mn, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if mn > 59 or s > 59:
        return False, "Minutos y segundos deben estar entre 00 y 59"
    if h > 99:
        return False, "Horas deben ser menores de 100"
    return True, ""


def _validate_hours(value: str) -> tuple[bool, str]:
    """Valida horas de entrenamiento semanal (0.5 – 40). Devuelve (valido, mensaje_error)."""
    try:
        h = float(value.replace(",", "."))
    except ValueError:
        return False, "Introduce un número (ej: 8 o 10.5)"
    if h < 0.5:
        return False, "El mínimo es 0.5 horas/semana"
    if h > 40:
        return False, "El máximo es 40 horas/semana. ¿Seguro?"
    return True, ""


def _parse_running_threshold_pace_to_sec_per_km(value: str) -> float | None:
    """Convierte un ritmo umbral en formato min/km a segundos por km.

    Acepta formatos habituales como `4:15`, `04:15`, `4.15` y `4,15`.
    """
    raw = (value or "").strip()
    if not raw:
        return None

    colon_match = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if colon_match:
        mm = int(colon_match.group(1))
        ss = int(colon_match.group(2))
        if 0 <= ss < 60:
            pace = mm * 60 + ss
            return float(pace) if pace > 0 else None
        return None

    dot_or_comma_match = re.match(r"^(\d{1,2})[\.,](\d{2})$", raw)
    if dot_or_comma_match:
        mm = int(dot_or_comma_match.group(1))
        ss = int(dot_or_comma_match.group(2))
        if 0 <= ss < 60:
            pace = mm * 60 + ss
            return float(pace) if pace > 0 else None
        return None

    return None


def _set_running_threshold_pace(profile: dict, pace_raw: str) -> tuple[bool, str]:
    """Actualiza el ritmo umbral de running en el perfil.

    Returns:
        (ok, message) donde message es error o el valor formateado (`m:ss`).
    """
    pace_sec = _parse_running_threshold_pace_to_sec_per_km(pace_raw)
    if pace_sec is None:
        return False, "Formato no válido. Usa mm:ss (ej: 4:15)."

    if profile is None:
        profile = {}
    perf = profile.setdefault("performance", {})
    today_iso = date.today().isoformat()
    perf["running_threshold_pace_sec_per_km"] = round(float(pace_sec), 1)
    perf["running_threshold_pace"] = f"{int(pace_sec // 60)}:{int(pace_sec % 60):02d}"
    perf["running_threshold_pace_date"] = today_iso
    perf["performance_params_updated_at"] = today_iso
    # El historial por fecha manda en compute_load: el nuevo valor rige desde hoy, el pasado no cambia.
    history = [
        entry
        for entry in (perf.get("running_threshold_pace_history") or [])
        if isinstance(entry, dict) and entry.get("date") != today_iso
    ]
    history.append({"date": today_iso, "sec_per_km": round(float(pace_sec), 1)})
    perf["running_threshold_pace_history"] = sorted(history, key=lambda entry: str(entry.get("date")))
    return True, str(perf["running_threshold_pace"])


def _set_hr_threshold(profile: dict, bpm_raw: str) -> tuple[bool, str]:
    """FC umbral nueva tras un test (26/10): en Garmin la detecta el reloj y no se cambia desde la web, así que
    se carga a mano en Kairos. Un solo número para las dos referencias, el mismo que se pone en TrainingPeaks:
    la FC umbral de Kairos (`hr_threshold_bpm`: hrTSS de fuerza, elíptica y remo; contexto del LLM) y la entrada
    de hoy en `hr_threshold_tp_history` (hrTSS de trail y bici sin potencia). Rige desde hoy: el TSS ya guardado
    no cambia. Las zonas siguen siendo las del reloj.
    """
    try:
        bpm = int(bpm_raw)
    except (TypeError, ValueError):
        return False, "Formato no válido. Usa un entero en lpm (ej: /perfil umbral fc 169)."
    if not 120 <= bpm <= 230:
        return False, "Umbral de FC fuera de rango (120-230 lpm)."

    if profile is None:
        profile = {}
    perf = profile.setdefault("performance", {})
    today_iso = date.today().isoformat()
    history = [
        entry
        for entry in (perf.get("hr_threshold_tp_history") or [])
        if isinstance(entry, dict) and entry.get("date") != today_iso
    ]
    history.append({"date": today_iso, "bpm": bpm})
    perf["hr_threshold_tp_history"] = sorted(history, key=lambda entry: str(entry.get("date")))
    perf["hr_threshold_bpm"] = bpm
    perf["hr_threshold_date"] = today_iso
    perf["performance_params_updated_at"] = today_iso
    return True, str(bpm)


def _set_hr_profile_values(profile: dict, hr_rest_raw: str, hr_max_raw: str) -> tuple[bool, str]:
    """Actualiza FC de reposo y FC máxima en el perfil para modelos de carga."""
    try:
        hr_rest = int(hr_rest_raw)
        hr_max = int(hr_max_raw)
    except (TypeError, ValueError):
        return False, "Formato no válido. Usa enteros (ej: /perfil fc 48 186)."

    if hr_rest < 30 or hr_rest > 100:
        return False, "FC reposo fuera de rango (30-100 bpm)."
    if hr_max < 120 or hr_max > 240:
        return False, "FC máxima fuera de rango (120-240 bpm)."
    if hr_max <= hr_rest:
        return False, "FC máxima debe ser mayor que FC reposo."

    if profile is None:
        profile = {}
    perf = profile.setdefault("performance", {})
    today_iso = date.today().isoformat()
    perf["hr_rest_bpm"] = hr_rest
    perf["hr_max_bpm"] = hr_max
    perf["hr_profile_date"] = today_iso
    perf["performance_params_updated_at"] = today_iso
    return True, f"FC reposo={hr_rest} bpm · FC max={hr_max} bpm"


def _ensure_running_threshold_pace(profile: dict) -> dict:
    """Garantiza que el perfil tenga ritmo umbral de running configurado por el usuario.

    Se pregunta solo cuando falta el dato; se persiste en `performance`.
    """
    profile = profile or {}
    perf = profile.setdefault("performance", {})
    existing = perf.get("running_threshold_pace_sec_per_km")
    try:
        if existing is not None and float(existing) > 0:
            return profile
    except (TypeError, ValueError):
        pass

    console.print(Panel.fit(
        "[bold]Necesito tu ritmo umbral de carrera[/]\n"
        "Lo usaremos para calcular TSS de running (rodaje/fartlek/series).\n"
        "Formato: [bold]min/km[/bold] (ej: 4:15)",
        title="[bold blue]Kairos Coach — Umbral de running[/]",
        border_style="blue",
    ))

    while True:
        pace_raw = Prompt.ask("Ritmo umbral (min/km)", default="4:30").strip()
        ok, msg = _set_running_threshold_pace(profile, pace_raw)
        if not ok:
            console.print(f"[red]✗[/] {msg}")
            continue
        _save_user_profile(profile)
        console.print(
            "[green]✓[/] Umbral de running guardado: "
            f"[bold]{msg} min/km[/bold]"
        )
        return profile


def _iso_to_dmy(value: object, empty: str = "") -> str:
    """Fecha ISO (AAAA-MM-DD) del perfil en DD/MM/AAAA, como todo lo que ve el usuario; otro texto, tal cual."""
    raw = str(value or "").strip()
    if not raw:
        return empty
    try:
        return date.fromisoformat(raw[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return raw


def _build_initial_athlete_knowledge(profile: dict) -> str:
    """Genera una base de conocimiento inicial mínima a partir del perfil."""
    p = profile.get("personal", {})
    g = profile.get("goals", {})
    h = profile.get("health", {})
    injuries = ", ".join(h.get("injuries", [])) if h.get("injuries") else ""

    lines = [
        "# Base de Conocimiento del Atleta",
        "",
        "## Identidad deportiva",
        f"- Nombre: {p.get('name', '')}",
        f"- Deporte principal: {g.get('primary', '')}",
        "",
        "## Perfil fisiologico y biomarcadores",
        f"- Altura: {p.get('height_cm', '')}",
        f"- Peso: {p.get('weight_kg', '')}",
        "",
        "## Objetivo principal de carrera",
        f"- Evento objetivo: {g.get('target_race', '')}",
        f"- Fecha: {_iso_to_dmy(g.get('target_race_date'))}",
        f"- Tiempo objetivo: {g.get('target_time', '')}",
        "",
        "## Salud y limitaciones",
        f"- Patologias: {', '.join(h.get('pathologies') or []) or 'ninguna'}",
        f"- Diabetes: {_diabetes.label(h) or 'no'}",
        f"- Lesiones: {injuries}",
        f"- Notas: {h.get('notes', '')}",
        "",
        "## Reglas del coach",
        "- Priorizar seguridad y continuidad del entrenamiento.",
        "- Ajustar cargas según recuperación y contexto personal.",
    ]
    return "\n".join(lines).strip() + "\n"


def _build_enriched_athlete_knowledge(profile: dict, enrichment: dict) -> str:
    """Combina perfil + contexto MCP para persistir una KB inicial mas completa."""
    base = _build_initial_athlete_knowledge(profile).rstrip()
    personal = enrichment.get("personal", {}) if isinstance(enrichment, dict) else {}
    startup = enrichment.get("startup_48h", {}) if isinstance(enrichment, dict) else {}

    lines = [base, "", "## Enriquecimiento MCP (arranque)"]
    if personal:
        lines.append("- Datos personales confirmados por Garmin Connect:")
        for key in ("age", "gender", "weight_kg", "height_cm"):
            if personal.get(key) is not None and personal.get(key) != "":
                lines.append(f"  - {key}: {personal.get(key)}")
    else:
        lines.append("- Datos personales MCP: no disponibles en este arranque")

    lines.append("")
    lines.append("## Estado de las ultimas 48h")
    bb = (startup.get("body_battery") or {}).get("summary", "sin datos") if isinstance(startup, dict) else "sin datos"
    hrv = (startup.get("hrv") or {}).get("summary", "sin datos") if isinstance(startup, dict) else "sin datos"
    sleep = (startup.get("sleep") or {}).get("summary", "sin datos") if isinstance(startup, dict) else "sin datos"
    lines.append(f"- Body Battery: {bb}")
    lines.append(f"- HRV: {hrv}")
    lines.append(f"- Sueno: {sleep}")

    trainings = startup.get("trainings", []) if isinstance(startup, dict) else []
    if trainings:
        lines.append("- Entrenamientos recientes:")
        for item in trainings[:5]:
            day = item.get("date") or "fecha desconocida"
            name = item.get("name") or "Actividad"
            lines.append(f"  - {day}: {name}")
    else:
        lines.append("- Entrenamientos recientes: sin datos")

    lines.append("")
    lines.append("## Contexto crudo MCP (resumen)")
    try:
        raw_preview = json.dumps(enrichment, ensure_ascii=False)[:1200]
        lines.append(f"```json\n{raw_preview}\n```")
    except (TypeError, ValueError):
        lines.append("- No se pudo serializar el resumen MCP")

    return "\n".join(lines).strip() + "\n"


def _authenticate_or_register_user() -> tuple[str, dict, bool, str]:
    """Acceso con Supabase Auth (supabase/README.md): sesión guardada, inicio de sesión o cuenta nueva.

    Nada se lee de la base de datos antes de iniciar sesión. La contraseña de Kairos es la misma que la
    de Garmin Connect; se guarda cifrada (ENCRYPTION_KEY) para que el MCP entre en Garmin.

    Returns:
        (username, credentials, is_new_user, app_password)
    """
    console.print(Panel.fit(
        "[bold]Acceso a Kairos Coach[/]\n"
        "Entra con tu email y la misma contraseña que usas en Garmin Connect.",
        title="[bold blue]Identificación[/]",
        border_style="blue",
    ))

    while True:
        username = Prompt.ask("Usuario (email)").strip().lower()
        if not username:
            continue
        if "@" not in username:
            console.print("[yellow]⚠[/]  El usuario es tu email (el de tu cuenta de Kairos).")
            continue

        # ── Sesión guardada en este ordenador: sin contraseña ─────────────
        row = restore_session(username)
        if row:
            creds = dict(row.get("credentials") or {})
            encrypted_pw = creds.get("garmin_password_encrypted", "")
            password = decrypt_password(encrypted_pw) if encrypted_pw else None
            if password:
                console.print(f"[green]✓[/] Sesión recuperada · Accediendo como [bold]{username}[/]")
                return username, creds, False, password
            # La contraseña de Garmin no se puede descifrar aquí (otra ENCRYPTION_KEY): se vuelve a pedir.
            console.print("[yellow]⚠[/]  Sesión recuperada, pero necesito tu contraseña de Garmin Connect en este ordenador.")
            password = Prompt.ask("Contraseña (la misma que en Garmin Connect)", password=True).strip()
            creds["garmin_password_encrypted"] = encrypt_password(password)
            update_user_credentials(creds)
            return username, creds, False, password

        # ── Inicio de sesión ──────────────────────────────────────────────
        password = Prompt.ask("Contraseña (la misma que en Garmin Connect)", password=True).strip()
        result = sign_in(username, password)

        if not result.get("ok") and result.get("reason") == "invalid":
            # Mismo error para "contraseña incorrecta" y "cuenta inexistente": se ofrece crearla.
            console.print(f"[red]✗[/] {result.get('error')}")
            if not Confirm.ask("¿Es tu primer acceso con este email? Crear tu cuenta de Kairos", default=False):
                continue
            confirm = Prompt.ask("Repite la contraseña", password=True).strip()
            if confirm != password:
                console.print("[red]✗[/] Las contraseñas no coinciden.")
                continue
            created = sign_up(username, password)
            if not created.get("ok"):
                console.print(f"[red]✗[/] {created.get('error')}")
                continue
            if created.get("needs_confirmation"):
                console.print(Panel.fit(
                    f"Te he enviado un correo a [bold]{username}[/] para confirmar la cuenta.\n"
                    "Abre el enlace y vuelve a entrar aquí con el mismo email y contraseña.\n"
                    "[dim]Si ya tenías datos en Kairos, se asocian solos al entrar.[/]",
                    title="[bold]Confirma tu email[/]",
                    border_style="yellow",
                ))
                continue
            result = sign_in(username, password)

        if not result.get("ok"):
            console.print(f"[red]✗[/] {result.get('error')}")
            continue

        row = result.get("app_user")
        if row:
            # Cuenta con datos (vinculada ahora o antes): se guarda la contraseña cifrada para este ordenador.
            creds = dict(row.get("credentials") or {})
            creds["garmin_password_encrypted"] = encrypt_password(password)
            update_user_credentials(creds)
            console.print(f"[green]✓[/] Sesión iniciada como [bold]{username}[/].")
            return username, creds, False, password

        # ── Cuenta nueva sin datos: se crea su usuario de Kairos ──────────
        garmin_email = Prompt.ask("Email de Garmin Connect", default=username).strip()
        creds = {
            "garmin_email": garmin_email,
            "garmin_password_encrypted": encrypt_password(password),
        }
        try:
            create_app_user(username, credentials=creds)
        except Exception as exc:  # noqa: BLE001 — error de red o de permisos: se informa y se reintenta
            console.print(f"[red]✗[/] No se pudo crear el usuario: {exc}")
            continue
        console.print(f"[green]✓[/] Usuario [bold]{username}[/] creado.")
        return username, creds, True, password


def _ensure_garmin_credentials(credentials: dict, app_password: str = "") -> dict:
    """Pone GARMIN_EMAIL y GARMIN_PASSWORD en el entorno para el proceso MCP."""
    credentials = dict(credentials or {})
    garmin_email = (credentials.get("garmin_email") or "").strip()

    if not garmin_email:
        garmin_email = Prompt.ask(
            "Email Garmin Connect",
            default=os.environ.get("GARMIN_EMAIL", ""),
        ).strip()
        credentials["garmin_email"] = garmin_email
        credentials["garmin_password_encrypted"] = encrypt_password(app_password)
        update_user_credentials(credentials)

    garmin_password = app_password or os.environ.get("GARMIN_PASSWORD", "")

    os.environ["GARMIN_EMAIL"] = garmin_email
    os.environ["GARMIN_PASSWORD"] = garmin_password
    return credentials


def _handle_garmin_password_change() -> str | None:
    """Flujo de recuperación cuando la contraseña de Garmin Connect ha cambiado."""
    console.print(Panel.fit(
        "[bold red]❌ Error de autenticación en Garmin Connect[/]\n\n"
        "Parece que tu contraseña de Garmin Connect ha cambiado.\n"
        "Introduce tu nueva contraseña para actualizar el acceso.\n"
        "[dim](Debe coincidir con tu contraseña actual en Garmin Connect)[/]",
        title="[bold]Contraseña desactualizada[/]",
        border_style="red",
    ))
    for _ in range(3):
        new_password = Prompt.ask(
            "Nueva contraseña de Garmin Connect", password=True
        ).strip()
        if len(new_password) < 6:
            console.print("[red]✗[/] Mínimo 6 caracteres.")
            continue
        result = update_login_password(new_password)
        if not result.get("ok"):
            console.print(f"[red]✗[/] {result.get('error')}")
            continue
        os.environ["GARMIN_PASSWORD"] = new_password
        console.print("[green]✓[/] Contraseña actualizada. Reconectando con Garmin Connect...")
        return new_password
    return None


def _ask_goals(profile: dict) -> None:
    """Pregunta y guarda nombre + campos de objetivos de entrenamiento."""
    p = profile.setdefault("personal", {})
    g = profile.setdefault("goals", {})

    # Nombre — Garmin no lo expone en su API
    console.print("\n[bold]Datos personales:[/]")
    user_name = Prompt.ask(
        "  Tu nombre [dim](ej: Rafael · Enter para omitir)[/]",
        default=p.get("name", ""),
    ).strip()
    if user_name:
        p["name"] = user_name

    console.print("\n[bold]Objetivos de entrenamiento:[/]")

    sport = Prompt.ask(
        "  Deporte principal [dim](Enter para omitir)[/]",
        choices=["running", "trail running", "triatlón", "ciclismo", "otro"],
        default=g.get("primary", "running") or "running",
        show_choices=True,
    )
    if sport.strip():
        g["primary"] = sport.strip()

    # Horas/semana con validación de rango
    while True:
        hours = Prompt.ask(
            "  Horas de entrenamiento por semana [dim](ej: 10 · Enter para omitir)[/]",
            default=str(g.get("weekly_training_hours", "")),
        )
        if not hours.strip():
            break
        ok, err = _validate_hours(hours.strip())
        if ok:
            g["weekly_training_hours"] = float(hours.strip().replace(",", "."))
            break
        console.print(f"  [red]✗[/] {err}")

    race = Prompt.ask(
        "  Próxima carrera/evento objetivo [dim](ej: Ultra PDA 55km · Enter para omitir)[/]",
        default=g.get("target_race", ""),
    )
    if race.strip():
        g["target_race"] = race.strip()

    # Fecha del evento con validación de formato y fecha futura
    while True:
        race_date = Prompt.ask(
            "  Fecha del evento [dim](YYYY-MM-DD · Enter para omitir)[/]",
            default=g.get("target_race_date", ""),
        )
        if not race_date.strip():
            break
        ok, err = _validate_date(race_date.strip())
        if ok:
            g["target_race_date"] = race_date.strip()
            break
        console.print(f"  [red]✗[/] {err}")

    # Tiempo objetivo con validación de formato
    while True:
        target_time = Prompt.ask(
            "  Tiempo objetivo [dim](H:MM:SS · ej: 9:30:00 · Enter para omitir)[/]",
            default=g.get("target_time", ""),
        )
        if not target_time.strip():
            break
        ok, err = _validate_time(target_time.strip())
        if ok:
            g["target_time"] = target_time.strip()
            break
        console.print(f"  [red]✗[/] {err}")


def _ask_diabetes_medication(h: dict) -> None:
    """Medicación de la diabetes (26/10): números de la lista separados por coma y, si hace falta, otra a mano.

    Se guarda en `health.diabetes_medication`; una lista vacía es "sin medicación (solo dieta)".
    """
    keys = list(_diabetes.MEDICATIONS)
    current = _diabetes.medication(h) or []
    console.print(f"  Has indicado [bold]{_diabetes.TYPES[_diabetes.detect_type(h)]}[/]. ¿Qué medicación tomas?")
    for i, key in enumerate(keys, 1):
        console.print(f"    {i}) {_diabetes.MEDICATIONS[key]}")
    default = ",".join(str(keys.index(m) + 1) for m in current if m in keys)
    while True:
        raw = Prompt.ask("  Números separados por coma [dim](Enter si ninguna)[/]", default=default)
        picks = [x.strip() for x in raw.split(",") if x.strip()]
        if all(x.isdigit() and 1 <= int(x) <= len(keys) for x in picks):
            break
        console.print(f"  [red]✗[/] Usa números del 1 al {len(keys)}.")
    meds = [keys[int(x) - 1] for x in dict.fromkeys(picks)]
    other = Prompt.ask("  Otra medicación [dim](opcional)[/]",
                       default=", ".join(m for m in current if m not in keys))
    meds += [x.strip() for x in other.split(",") if x.strip()]
    h["diabetes_medication"] = meds


def _ensure_health_profile(profile: dict) -> dict:
    """Perfiles anteriores al 26/10, una sola vez.

    1. Patologías separadas de lesiones: lo que en "lesiones" menciona diabetes pasa a `pathologies`, sin
       preguntar (otra enfermedad escrita allí se corrige con /perfil editar salud).
    2. Si las patologías mencionan diabetes, se pregunta la medicación. Un atleta sin diabetes nunca la ve.
    """
    profile = profile or {}
    h = profile.setdefault("health", {})
    if "pathologies" not in h:
        injuries = list(h.get("injuries") or [])
        h["pathologies"] = [i for i in injuries if _diabetes.is_diabetes(i)]
        h["injuries"] = [i for i in injuries if not _diabetes.is_diabetes(i)]
        _save_user_profile(profile)
        if h["pathologies"]:
            console.print(f"[green]✓[/] Separadas patologías ({', '.join(h['pathologies'])}) y lesiones "
                          f"({', '.join(h['injuries']) or 'ninguna'}) [dim](se cambia con /perfil editar salud)[/]")
    if _diabetes.detect_type(h) is None or _diabetes.medication(h) is not None:
        return profile
    console.print(Panel.fit(
        "[bold]Necesito tu medicación para la diabetes[/]\n"
        "Con ella decido qué precauciones aplicar en planes, análisis y consejos.",
        title="[bold blue]Kairos Coach — Salud[/]",
        border_style="blue",
    ))
    _ask_diabetes_medication(h)
    _save_user_profile(profile)
    console.print(f"[green]✓[/] Guardado: [bold]{_diabetes.label(h)}[/] [dim](se cambia con /perfil editar salud)[/]")
    return profile


def _ask_health(profile: dict) -> None:
    """Pregunta y guarda la salud (26/10): patologías (con la medicación si hay diabetes), lesiones y notas.

    Patologías y lesiones van por separado: sin patologías el atleta es sano aunque tenga una lesión.
    """
    h = profile.setdefault("health", {})
    console.print("\n[bold]Salud y condiciones médicas:[/]")

    pathologies_str = Prompt.ask(
        "  Enfermedades o patologías [dim](sep. con coma, ej: diabetes tipo 1, asma; Enter si ninguna)[/]",
        default=", ".join(h.get("pathologies", [])),
    )
    h["pathologies"] = [i.strip() for i in pathologies_str.split(",") if i.strip()]
    if _diabetes.detect_type({"pathologies": h["pathologies"]}):
        _ask_diabetes_medication(h)

    injuries_str = Prompt.ask(
        "  Lesiones [dim](sep. con coma, ej: periostitis, tendinitis; Enter si ninguna)[/]",
        default=", ".join(h.get("injuries", [])),
    )
    h["injuries"] = [i.strip() for i in injuries_str.split(",") if i.strip()]

    notes = Prompt.ask(
        "  Notas adicionales de salud [dim](opcional)[/]",
        default=h.get("notes", ""),
    )
    h["notes"] = notes.strip()

    # La diabetes también puede salir en las notas; sin diabetes, fuera la medicación que hubiera.
    if not _diabetes.detect_type(h):
        h.pop("diabetes_medication", None)
    elif _diabetes.medication(h) is None:
        _ask_diabetes_medication(h)


def _run_first_time_setup() -> None:
    """
    Solo se ejecuta la primera vez.
    Pregunta objetivos + salud y marca el perfil como configurado.
    """
    profile = _load_user_profile()
    p = profile.setdefault("personal", {})
    name = p.get("name", "atleta")

    console.print(Panel.fit(
        f"[bold]Bienvenido, {name}![/] Primera vez configurando Kairos Coach.\n"
        "Necesito conocer tus objetivos y condiciones de salud\n"
        "para personalizar todas las recomendaciones.\n"
        "[dim]Pulsa Enter para omitir cualquier campo.[/]",
        title="[bold green]Kairos Coach — Configuración inicial[/]",
        border_style="green",
    ))

    _ask_goals(profile)
    _ask_health(profile)
    profile["setup_complete"] = True
    _save_user_profile(profile)
    final_name = profile.get("personal", {}).get("name") or name
    console.print(f"\n[green]✓[/] ¡Todo listo, [bold]{final_name}[/]! Ya puedes empezar.\n")


def _show_profile() -> None:
    """Muestra el perfil actual del usuario."""
    profile = _load_user_profile()
    p = profile.get("personal", {})
    g = profile.get("goals", {})
    h = profile.get("health", {})

    if not p and not g and not h:
        console.print("[yellow]Perfil vacío.[/] Usa [bold]/perfil editar objetivo[/] o [bold]/perfil editar salud[/].")
        return

    lines = [
        f"[bold]Nombre:[/]          {p.get('name', '—')}",
        f"[bold]Edad:[/]            {p.get('age', '—')} años",
        f"[bold]Género:[/]          {p.get('gender', '—')}",
        f"[bold]Peso:[/]            {p.get('weight_kg', '—')} kg",
        f"[bold]Altura:[/]          {p.get('height_cm', '—')} cm",
        "",
        f"[bold]Deporte:[/]         {g.get('primary', '—')}",
        f"[bold]Horas/semana:[/]    {g.get('weekly_training_hours', '—')}",
        f"[bold]Evento objetivo:[/] {g.get('target_race', '—')}",
        f"[bold]Fecha evento:[/]    {_iso_to_dmy(g.get('target_race_date'), '—')}",
        f"[bold]Tiempo objetivo:[/] {g.get('target_time', '—')}",
    ]
    perf = profile.get("performance", {}) if isinstance(profile.get("performance"), dict) else {}
    threshold_pace = perf.get("running_threshold_pace") or "—"
    lines.append(f"[bold]Umbral running:[/]  {threshold_pace} min/km")
    pathologies = h.get("pathologies", [])
    lines.append(f"[bold]Patologías:[/]      {', '.join(pathologies) if pathologies else 'ninguna'}")
    if _diabetes.label(h):
        lines.append(f"[bold]Diabetes:[/]        {_diabetes.label(h)}")
    injuries = h.get("injuries", [])
    lines.append(f"[bold]Lesiones:[/]        {', '.join(injuries) if injuries else '—'}")
    if h.get("notes"):
        lines.append(f"[bold]Notas salud:[/]     {h['notes']}")

    console.print(Panel(
        "\n".join(lines),
        title="[bold blue]Tu perfil[/]",
        border_style="blue",
    ))
    console.print(
        "[dim]Editar: "
        "[bold]/perfil editar objetivo[/bold] — nombre, deporte, evento, tiempo objetivo • "
        "[bold]/perfil editar salud[/bold] — patologías, lesiones y notas[/dim]"
    )


_PLAN_COMMAND_WITHOUT_SLASH = re.compile(r"^plan\s+(ver|listar|activar|crear)\b", re.IGNORECASE)


def _with_plan_slash(user_input: str) -> str:
    """"plan ver 19bd7f1c" sin la barra es el comando /plan ver (30/10: iba a la ruta de planes como petición de
    planificación, y esta rehízo el plan de Gemini con el motor determinista)."""
    text = (user_input or "").strip()
    return "/" + text if _PLAN_COMMAND_WITHOUT_SLASH.match(text) else user_input


def _parse_plan_command(cmd: str) -> tuple[str | None, str | None]:
    """Parsea comandos /plan y devuelve (acción, argumento opcional)."""
    raw = (cmd or "").strip().lower()
    if not raw.startswith("/plan"):
        return None, None

    parts = raw.split()
    if len(parts) == 1:
        return "help", None

    action = parts[1]
    arg = " ".join(parts[2:]).strip() or None

    if action in {"help", "ayuda", "?"}:
        return "help", None
    if action in {"listar", "list", "ls"}:
        return "list", None
    if action in {"ver", "view", "show"}:
        return "view", arg
    if action in {"activar", "activate"}:
        return "activate", arg
    if action in {"crear", "create", "new"}:
        return "create", None
    return "help", None


def _show_plan_help() -> None:
    console.print(Panel.fit(
        "[bold]Comandos de planificación:[/]\n\n"
        "  [bold cyan]/plan listar[/bold cyan]          Lista planes (nº e id corto) y marca el activo\n"
        "  [bold cyan]/plan ver <nº o id>[/bold cyan]     Muestra el plan y sus sesiones (ej.: /plan ver 1)\n"
        "  [bold cyan]/plan activar <nº o id>[/bold cyan] Activa un plan y desactiva el anterior\n"
        "  [bold cyan]/plan crear[/bold cyan]           Tu LLM construye el plan (Kairos lo comprueba) y lo activa",
        title="[bold blue]Kairos Coach — Gestión de planes[/]",
        border_style="blue",
    ))


def _show_training_plans() -> None:
    try:
        plans = list_training_plans(include_archived=False)
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        console.print(f"[bold red]Error listando planes:[/] {exc}")
        return

    if not plans:
        console.print("[yellow]No hay planes registrados para este usuario.[/]")
        return

    table = Table(title="Planes de entrenamiento")
    table.add_column("Nº", justify="right")
    table.add_column("Activo", justify="center")
    table.add_column("ID")
    table.add_column("Título")
    table.add_column("Estado")
    table.add_column("Duración")
    table.add_column("Objetivo")

    for number, plan in enumerate(plans, 1):
        status = str(plan.get("status") or "").strip().lower()
        is_active = status == "active"
        plan_id = str(plan.get("id") or "")
        title = str(plan.get("title") or "Plan").strip()
        duration = str(plan.get("duration_weeks") or 0)
        objective = str(plan.get("objective") or "—").strip() or "—"
        table.add_row(str(number), "✓" if is_active else "", _plan_short_id(plan_id), title, status or "—",
                      f"{duration} sem", objective)

    console.print(table)
    console.print("[dim]/plan ver <nº> o /plan activar <nº> (también con el id corto)[/]")


def _show_training_plan(plan_ref: str) -> None:
    if not plan_ref:
        console.print("[yellow]Uso: /plan ver <nº o id> [semana <n> | todo] (el nº y el id corto salen en /plan listar)[/]")
        return
    # "/plan ver 1 semana 5" o "/plan ver 1 todo" (28/10): qué semanas se detallan en un plan por bloques.
    parts = plan_ref.split()
    plan_ref, options = parts[0], parts[1:]
    weeks: list[int] | None = None
    if options and options[0] == "todo":
        weeks = list(range(1, 60))
    elif len(options) >= 2 and options[0] == "semana" and options[1].isdigit():
        weeks = [int(options[1])]
    plan_id = _resolve_plan_id(plan_ref)
    if not plan_id:
        console.print(f"[yellow]'{plan_ref}' coincide con varios planes: escribe más caracteres del id o su nº.[/]")
        return

    try:
        plan = get_training_plan(plan_id)
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        console.print(f"[bold red]Error cargando plan:[/] {exc}")
        return

    if not plan:
        console.print(f"[yellow]No encontré el plan '{plan_ref}'. Mira /plan listar.[/]")
        return

    sessions = []
    try:
        sessions = list_training_plan_sessions(plan_id)
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        log.debug("_show_training_plan: no se pudieron cargar sesiones de %s: %s", plan_id, exc)
        sessions = []

    if sessions and _is_block_plan(plan):
        # Plan por bloques: semanas con fechas, fase, tiempo, km y TSS, y los días con sus tramos.
        console.print(f"[dim]ID {_plan_short_id(plan.get('id', ''))} · {plan.get('status', '—')}[/]")
        console.print(Markdown(_plan_detail_markdown(plan, sessions, _load_user_profile(), weeks)))
        return

    lines = [
        f"[bold]ID:[/] {_plan_short_id(plan.get('id', ''))}",
        f"[bold]Título:[/] {plan.get('title', '—')}",
        f"[bold]Estado:[/] {plan.get('status', '—')}",
        f"[bold]Objetivo:[/] {plan.get('objective', '—')}",
        f"[bold]Dificultad:[/] {plan.get('difficulty', '—')}",
        f"[bold]Duración:[/] {plan.get('duration_weeks', 0)} semanas",
        f"[bold]Fuente:[/] {plan.get('source', '—')}",
    ]

    if sessions:
        lines.append("")
        lines.append("[bold]Sesiones:[/]")
        profile = _load_user_profile()
        for s in sessions[:14]:
            lines.append(
                f"- Semana {s.get('week_index', 1)} · Día {s.get('day_index', 1)} · "
                f"{_session_type_label(s.get('session_type'))} · {s.get('duration_min') or 'n/d'} min · "
                f"{_session_intensity_label(s, profile)}"
            )
    else:
        lines.append("")
        lines.append("[dim]Sin sesiones registradas.[/]")

    console.print(Panel("\n".join(lines), title="[bold blue]Detalle de plan[/]", border_style="blue"))


def _activate_training_plan_cli(plan_ref: str, agent: TrainerAgent) -> None:
    if not plan_ref:
        console.print("[yellow]Uso: /plan activar <nº o id> (el nº y el id corto salen en /plan listar)[/]")
        return
    plan_id = _resolve_plan_id(plan_ref)
    if not plan_id:
        console.print(f"[yellow]'{plan_ref}' coincide con varios planes: escribe más caracteres del id o su nº.[/]")
        return

    try:
        activated = activate_training_plan(plan_id, change_reason="activated_from_cli")
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        console.print(f"[bold red]Error activando plan:[/] {exc}")
        return

    if not activated:
        console.print(f"[yellow]No se pudo activar el plan '{plan_ref}'.[/]")
        return

    # Espejo backward-compatible en perfil local en memoria.
    profile = _load_user_profile()
    profile["training_plan"] = {
        "id": activated.get("id"),
        "title": activated.get("title"),
        "status": "active",
        "active": True,
        "source": activated.get("source") or "db",
    }
    _save_user_profile(profile)
    agent.user_profile = _load_user_profile()

    console.print(f"[green]✓[/] Plan activado: [bold]{activated.get('title', plan_id)}[/]")


_INTENSITY_CHOICES = {"1": "polarizada", "2": "piramidal", "3": ""}
_PROGRESSION_CHOICES = {"1": "3+1", "2": "2+1", "3": ""}


def _ask_plan_preferences(agent: TrainerAgent) -> None:
    """Lo que un entrenador pregunta antes de hacer un plan (29/10, petición del usuario): días, límites de tiempo,
    dobles, distribución de la intensidad y progresión de la carga. Se guarda en el perfil (goals.availability y
    goals.plan_preferences) y lo usan el LLM y las reglas del plan; la próxima vez sale como respuesta por defecto."""
    profile = getattr(agent, "user_profile", None) or _load_user_profile() or {}
    goals = profile.setdefault("goals", {})
    availability = dict(goals.get("availability") or {})
    prefs = dict(goals.get("plan_preferences") or {})
    days = _parse_day_list(Prompt.ask("¿Qué días puedes entrenar? (L,M,X,J,V,S,D, rangos como L-V, o «todos»)",
                                      default=_format_days(list(availability.get("training_days") or []))))
    # Día fijo de descanso (29/10): es el fallo más frecuente del LLM (semanas sin descanso); dicho por el atleta,
    # es un día sin entrenar más y el LLM lo recibe como tal.
    rest = _parse_day_list(Prompt.ask("¿Qué día(s) descansas siempre? (p. ej. V; Enter para que lo decida el entrenador)",
                                      default=_format_days(list(availability.get("rest_days") or [])) if
                                      availability.get("rest_days") else ""))
    limits_now = ", ".join(f"{_DAY_LETTERS[int(d)]} {m}" for d, m in sorted(
        (int(k), v) for k, v in (availability.get("max_minutes_per_day") or {}).items()))
    limits = _parse_day_limits(Prompt.ask("¿Tiempo máximo algún día? (p. ej. «L-V 75, S 180»; Enter si no hay límite)",
                                          default=limits_now))
    doubles = Confirm.ask("¿Puedes entrenar dos veces el mismo día (p. ej. rodaje por la mañana y gimnasio por la tarde)?",
                          default=bool(prefs.get("doubles")))
    intensity = Prompt.ask(
        "Distribución de la intensidad: 1 polarizada 80/20 (casi todo suave y lo intenso muy intenso), 2 piramidal "
        "(mucho suave, algo de tempo, poco muy intenso), 3 que decida el entrenador",
        choices=list(_INTENSITY_CHOICES),
        default=next((k for k, v in _INTENSITY_CHOICES.items() if v == prefs.get("intensity")), "1"))
    progression = Prompt.ask(
        "Progresión de la carga: 1 bloques 3+1 (tres semanas subiendo y una de descarga), 2 bloques 2+1 (más "
        "descarga), 3 que decida el entrenador",
        choices=list(_PROGRESSION_CHOICES),
        default=next((k for k, v in _PROGRESSION_CHOICES.items() if v == prefs.get("progression")), "1"))
    # Lo que el atleta dijo en el chat para siempre (plan_prefs, 30/10): se enseña y se mantiene salvo que diga que no.
    notes = [str(n) for n in (prefs.get("notes") or [])]
    if notes:
        console.print("Lo que me dijiste para tus planes:\n" + "\n".join(f"  - {n}" for n in notes))
        if not Confirm.ask("¿Lo sigo teniendo en cuenta?", default=True):
            notes = []
    availability.update({"training_days": [d for d in (days or range(1, 8)) if d not in rest] or list(range(1, 8)),
                         "rest_days": rest,
                         "max_minutes_per_day": {str(d): m for d, m in limits.items()}})
    goals["availability"] = availability
    goals["plan_preferences"] = {"doubles": bool(doubles), "intensity": _INTENSITY_CHOICES[intensity],
                                 "progression": _PROGRESSION_CHOICES[progression], "notes": notes}
    if hasattr(agent, "user_profile"):
        agent.user_profile = profile
    _save_user_profile(profile)


async def _create_training_plan_cli(agent: TrainerAgent) -> None:
    """Crea y activa un plan: lo construye el LLM del atleta y Kairos lo comprueba (28/10).

    Antes guardaba solo lo del formulario (título, objetivo, semanas) sin ninguna sesión: Kairos no tenía nada que
    leer y "¿qué me toca mañana?" acababa en "no puedo leer el plan". Ahora va por la misma ruta que el chat
    (`_route_planning`): estructura y sesiones a partir del objetivo del perfil, del volumen real y de lo que se le
    diga aquí, comprobadas y guardadas; si el modelo no consigue un plan válido, no se guarda ninguno (29/10).
    """
    goals = (_load_user_profile() or {}).get("goals", {}) or {}
    race = goals.get("target_race") or "sin objetivo (plan genérico de 8 semanas)"
    race_date = goals.get("target_race_date") or "—"
    console.print(Panel.fit(
        "Tu entrenador construye el plan por semanas a partir de tu objetivo, tu volumen reciente y tu estado de carga; "
        "Kairos lo comprueba (progresión, descargas, disponibilidad) y calcula ritmos, km y TSS.\n\n"
        f"[bold]Objetivo:[/] {race}\n[bold]Fecha:[/] {race_date}\n"
        f"[bold]Tiempo objetivo:[/] {goals.get('target_time') or '—'} · "
        f"[bold]Horas/semana:[/] {goals.get('weekly_training_hours') or '—'}\n\n"
        "[dim]Para cambiar el objetivo: /perfil editar objetivo[/]",
        title="[bold blue]Kairos Coach — Crear plan[/]",
        border_style="blue",
    ))
    _ask_plan_preferences(agent)
    notes = Prompt.ask(
        "¿Algo más que deba tener en cuenta? (lesiones, sesiones que no puedes hacer, superficie… Enter si nada)",
        default="",
    ).strip()
    message = "Crea un plan nuevo" + (f". A tener en cuenta: {notes}" if notes else "")
    with console.status(f"[dim]Tu entrenador ({getattr(agent, 'model', 'LLM')}) construye el plan; tarda unos minutos…[/]"):
        result = await agent._route_planning(message, force_new=True)
    reply = result[0] if isinstance(result, tuple) else result
    console.print(Markdown(str(reply or "")))


def _plan_short_id(plan_id: str) -> str:
    """Los 8 primeros caracteres del id: bastan para distinguir los planes de un atleta (el id completo es un UUID)."""
    return str(plan_id or "")[:8]


def _resolve_plan_id(ref: str) -> str | None:
    """Id completo de un plan a partir de su número en `/plan listar`, su id corto o el id completo (28/10)."""
    ref = (ref or "").strip().lower()
    if not ref:
        return None
    try:
        plans = list_training_plans(include_archived=False)
    except (RuntimeError, ValueError, TypeError, OSError, KeyError):
        return ref  # sin lista, se intenta con lo que se escribió
    if ref.isdigit() and 1 <= int(ref) <= len(plans):
        return str(plans[int(ref) - 1].get("id") or "")
    matches = [str(p.get("id") or "") for p in plans if str(p.get("id") or "").lower().startswith(ref)]
    return matches[0] if len(matches) == 1 else (ref if not matches else None)


def _show_load_trend_cli(agent: "TrainerAgent", cmd: str = "/carga") -> None:
    """Muestra la tabla de tendencia de carga/fatiga (semanal o mensual)."""
    pass

    parts = cmd.strip().lower().split()
    mode = "months" if len(parts) > 1 and parts[1] in {"meses", "mensual", "months", "month"} else "weeks"

    series = _load_metrics_view(agent.user_profile).get("series") or []

    if not series:
        console.print(Panel.fit(
            "Aún no hay datos de carga/fatiga calculados.\n\n"
            "Se calculan automáticamente al arrancar la sesión.\n"
            "Reinicia el agente o realiza una consulta de estado para generarlos.",
            title="[bold yellow]Kairos Coach — Carga/Fatiga[/]",
            border_style="yellow",
        ))
        return

    md_table = _build_load_trend_table(series, mode=mode)

    # Parsear la tabla Markdown y renderizarla con Rich Table para mejor visualización
    lines = md_table.splitlines()
    title_line = next((l.lstrip("# ").strip() for l in lines if l.startswith("#")), "Tendencia de carga")
    table_lines = [l for l in lines if l.startswith("|") and "---" not in l]
    legend_lines = [l.strip("_") for l in lines if l.startswith("_")]

    if not table_lines:
        console.print(Markdown(md_table))
        return

    headers = [h.strip() for h in table_lines[0].strip("|").split("|")]
    rich_table = Table(title=title_line, border_style="blue")
    for i, h in enumerate(headers):
        justify = "right" if i > 0 and i < len(headers) - 1 else "left"
        rich_table.add_column(h, justify=justify)

    status_colors = {
        "🟢": "green",
        "🟠": "yellow",
        "🔴": "red",
        "🟡": "yellow",
    }
    for row_line in table_lines[1:]:
        cells = [c.strip() for c in row_line.strip("|").split("|")]
        # Colorear la columna de estado según emoji
        colored = []
        for i, cell in enumerate(cells):
            if i == len(cells) - 1:
                color = next((v for k, v in status_colors.items() if k in cell), None)
                colored.append(f"[{color}]{cell}[/]" if color else cell)
            else:
                colored.append(cell)
        rich_table.add_row(*colored)

    console.print(rich_table)
    if legend_lines:
        console.print(f"[dim]{' · '.join(legend_lines)}[/]")


def _show_help() -> None:
    """Muestra ayuda completa: ejemplos, menú de comandos y guía de indicadores."""
    _show_menu()
    console.print(Panel(
        "[bold]Ejemplos de preguntas:[/]\n"
        "  \u00b7 \u00bfCómo estoy hoy? \u00bfPuedo entrenar fuerte?\n"
        "  \u00b7 Analízame el último entrenamiento\n"
        "  \u00b7 \u00bfCómo ha evolucionado mi VO\u2082máx en las últimas semanas?\n"
        "  \u00b7 Propónme un plan de entrenamiento para esta semana\n"
        "  \u00b7 \u00bfCuáles son mis récords personales?\n"
        "  \u00b7 \u00bfCómo he dormido últimamente?\n"
        "  \u00b7 \u00bfQué ritmo debería llevar en mi próxima carrera?\n"
        "  \u00b7 Analízame mi estado de forma general\n"
        "\n"
        "[bold]Comandos disponibles:[/] usa [bold cyan]/menu[/bold cyan] para verlos agrupados por categoría.\n\n"
        "[bold]Guía rápida de indicadores Garmin:[/]\n"
        "  [bold]Body Battery[/bold]       90-100 recuperado \u00b7 70-89 bien \u00b7 40-69 moderado \u00b7 <40 descansa\n"
        "  [bold]Training Readiness[/bold] >70 entrena fuerte \u00b7 40-70 suave \u00b7 <40 recuperación activa\n"
        "  [bold]HRV[/bold]               Caída >20% = fatiga, estrés o, con diabetes, mal control glucémico\n"
        "  [bold]Training Status[/bold]   Productive=en forma \u00b7 Peaking=pico \u00b7 Overreaching=alarma",
        title="[bold blue]Kairos Coach — Ayuda[/]",
        border_style="blue",
    ))


def _show_menu() -> None:
    """Muestra un menú claro y agrupado de comandos operativos del CLI."""
    console.print(Panel.fit(
        "[bold]Atajos principales[/]\n"
        "  [bold cyan]/menu[/bold cyan]  [dim](este menú)[/] · "
        "[bold cyan]/perfil[/bold cyan] · [bold cyan]/plan listar[/bold cyan] · "
        "[bold cyan]/carga[/bold cyan] · [bold cyan]/proveedor[/bold cyan] · [bold cyan]salir[/bold cyan]",
        title="[bold blue]Kairos Coach — Menú[/]",
        border_style="blue",
    ))

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Categoría", style="bold")
    table.add_column("Comando")
    table.add_column("Acción")

    table.add_row("Ver", "/perfil", "Ver perfil completo")
    table.add_row("Ver", "/plan listar", "Listar planes y ver cuál está activo")
    table.add_row("Ver", "/plan ver <id>", "Ver detalle y sesiones de un plan")
    table.add_row("Ver", "/carga", "Ver tabla semanal de carga/fatiga")
    table.add_row("Ver", "/carga meses", "Ver vista mensual de carga/fatiga")

    table.add_row("Editar", "/perfil editar objetivo", "Editar deporte, carrera y tiempo objetivo")
    table.add_row("Editar", "/perfil editar salud", "Editar patologías, lesiones (también las nuevas) y notas de salud")
    table.add_row("Editar", "/perfil editar", "Editar perfil completo")
    table.add_row("Editar", "/perfil umbral <mm:ss>", "Actualizar umbral de running (ej. 4:15)")
    table.add_row("Editar", "/perfil umbral fc <lpm>", "Actualizar FC umbral tras un test, la misma que en TrainingPeaks (ej. 169)")
    table.add_row("Editar", "/perfil fc <reposo> <max>", "Actualizar FC reposo/máxima (ej. 48 186)")

    table.add_row("Plan", "/plan crear", "Crear el plan con tu LLM y activarlo")
    table.add_row("Plan", "/plan activar <id>", "Activar plan por ID")

    table.add_row("Sistema", "/proveedor", "Cambiar de proveedor de IA, modelo o clave")
    table.add_row("Sistema", "/ayuda", "Mostrar ayuda extendida")
    table.add_row("Sistema", "salir", "Salir de Kairos (la sesión queda guardada)")

    console.print(table)
    console.print("[dim]Tip: si no recuerdas un comando, escribe /menu.[/]")


def _format_coach_markdown(response: str) -> str:
    """Normaliza la salida del coach a Markdown legible para terminal/email/Telegram.

    Si el modelo responde en texto plano, envuelve el contenido con un encabezado
    y convierte líneas sueltas en bullets para mejorar la lectura.
    """
    text = (response or "").strip()
    if not text:
        return "## 🧭 Resumen del Coach\n\n_No he podido generar contenido en esta respuesta._"

    markdown_markers = ("## ", "### ", "|", "**", "\n1. ", "- ", "* ")
    if any(marker in text for marker in markdown_markers):
        return text

    lines = [line.strip(" \t-•") for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        body = lines[0] if lines else text
        return f"## 🧭 Resumen del Coach\n\n{body}"

    bullet_block = "\n".join(f"- {line}" for line in lines)
    return f"## 🧭 Resumen del Coach\n\n{bullet_block}"


console = Console()


def _check_garmin_env() -> None:
    """Verifica que haya credenciales Garmin cargadas para el usuario activo."""
    missing = [v for v in ("GARMIN_EMAIL", "GARMIN_PASSWORD") if not os.environ.get(v)]
    if missing:
        for m in missing:
            console.print(f"[bold red]Error:[/] Falta [bold]{m}[/] para conectar con Garmin Connect")
        sys.exit(1)


# Valores de ejemplo de .env.example: no son una clave.
_PLACEHOLDER_VALUES = {"", "tu_clave", "nvapi-...", "sk-..."}


_KEY_COMMENT = "# Proveedor de IA de PROVIDER_API_KEY:"


def _has_key() -> bool:
    value = os.environ.get(_ia_provider.KEY_VAR, "")
    return bool(value) and value not in _PLACEHOLDER_VALUES


def _env_path() -> str:
    return find_dotenv(usecwd=True) or str(Path.cwd() / ".env")


def _save_api_key(config: dict, value: str) -> None:
    """Guarda la clave en PROVIDER_API_KEY del .env, con un comentario encima que dice de qué proveedor es (26/10)."""
    path = _env_path()
    set_key(path, _ia_provider.KEY_VAR, value, quote_mode="never", encoding="utf-8")
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if not ln.startswith(_KEY_COMMENT)]
    at = next(i for i, ln in enumerate(lines) if ln.startswith(f"{_ia_provider.KEY_VAR}="))
    lines.insert(at, f"{_KEY_COMMENT} {_ia_provider.label(config)}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[_ia_provider.KEY_VAR] = value


async def _ask_api_key(config: dict, reason: str) -> None:
    """Pide la clave del proveedor, la prueba con una llamada mínima y la guarda en .env (26/10).

    Si el proveedor la rechaza se vuelve a pedir; si no se puede comprobar (red, proxy, URL), se guarda igualmente.
    """
    env_var = _ia_provider.KEY_VAR
    k = _ia_provider.known(config["provider"])
    where = f"Consigue la clave en {_ia_provider.KNOWN[k][3]}\n" if k else ""
    console.print(Panel.fit(
        f"{reason}\n{where}[dim]Se guarda en el .env de este ordenador ({env_var}), nunca en la base de datos.[/]",
        title=f"[bold blue]Kairos Coach — Clave de {_ia_provider.label(config)}[/]",
        border_style="blue",
    ))
    while True:
        key = Prompt.ask("  Clave (API key)", password=True).strip()
        if not key:
            continue
        os.environ[env_var] = key
        status, detail = await probe_llm_key(config)
        if status == "auth":
            console.print(f"  [red]✗[/] El proveedor rechaza esa clave: {detail}")
            continue
        if status == "error":
            console.print(f"  [yellow]![/] No he podido comprobarla ({detail}); la guardo igualmente.")
        _save_api_key(config, key)
        console.print("  [green]✓[/] Clave guardada.")
        return


def _ask_ia_provider(current: dict | None = None) -> dict:
    """Pregunta el proveedor de IA del atleta (26/10), sin menú: el nombre y, si Kairos no lo conoce, la URL.

    Con un proveedor conocido se rellena la URL y se propone un modelo; con otro, se piden la URL de su API
    compatible con OpenAI y el modelo. Después, el modelo del chat (30/10), con Enter el mismo. Devuelve
    {provider, base_url, model, chat_model}; chat_model es None si es el mismo.
    """
    current = current or {}
    console.print(Panel.fit(
        "[bold]¿Cuál es tu proveedor de IA?[/]\n"
        "Kairos usa tu propia cuenta: cualquier proveedor con API compatible con OpenAI\n"
        "(p. ej. NVIDIA, OpenAI, Gemini, Anthropic, Groq, Mistral, OpenRouter, DeepSeek u otro).",
        title="[bold blue]Kairos Coach — Proveedor de IA[/]",
        border_style="blue",
    ))
    while True:
        name = Prompt.ask("  Proveedor", default=str(current.get("provider") or "")).strip()
        if name:
            break
    k = _ia_provider.known(name)
    if k:
        provider, base_url, default_model = k, _ia_provider.KNOWN[k][1], _ia_provider.KNOWN[k][2]
    else:
        provider = name
        while True:
            base_url = Prompt.ask("  URL de su API compatible con OpenAI [dim](ej: https://api.ejemplo.com/v1)[/]",
                                  default=str(current.get("base_url") or "")).strip()
            if base_url.startswith(("http://", "https://")):
                break
            console.print("  [red]✗[/] Tiene que empezar por http:// o https://")
        default_model = ""
    default_chat = _ia_provider.CHAT_MODEL.get(k or "", "")
    if current.get("provider") == provider and current.get("model"):
        default_model = str(current["model"])
        default_chat = str(current.get("chat_model") or "")
    while True:
        model = Prompt.ask("  Modelo [dim](planes y arranque)[/]", default=default_model).strip()
        if model:
            break
    chat_model = Prompt.ask("  Modelo del chat [dim](también releva al otro si falla)[/]",
                            default=default_chat or model).strip()
    return {"provider": provider, "base_url": base_url, "model": model,
            "chat_model": chat_model if chat_model and chat_model != model else None}


async def _ensure_ia_provider(network_probe: "asyncio.Future | None" = None) -> dict:
    """Proveedor de IA del atleta (26/10): se pregunta la primera vez y queda en ia_provider; la clave, en .env.

    Siguientes veces, sin preguntas; si este ordenador no tiene la clave, solo se pide la clave.
    network_probe: el sondeo de Zscaler lanzado al empezar el arranque (si no, se hace aquí).
    """
    console.print("[dim]Detectando entorno de red...[/]")
    on_vpn = await network_probe if network_probe is not None else _detect_zscaler()
    config = get_ia_provider()
    first_time = not _ia_provider.is_valid(config)
    if first_time:
        config = _ask_ia_provider()
    if not _has_key():
        await _ask_api_key(config, "Necesito tu clave para empezar." if first_time
                           else "Este ordenador no tiene la clave de tu proveedor de IA.")
    if first_time:
        save_ia_provider(config["provider"], config["base_url"], config["model"], config.get("chat_model"))

    network_line = "🏢  Red corporativa [dim](Zscaler detectado)[/dim]" if on_vpn else "🌐  Sin VPN corporativa"
    console.print(Panel.fit(
        f"{network_line}\n"
        f"[bold green]IA:[/] {_ia_provider.label(config)}  [dim](se cambia con /proveedor)[/dim]",
        title="[bold blue]Kairos Coach — Entorno detectado[/]",
        border_style="blue",
    ))
    return config


def _check_and_migrate_supabase() -> None:
    """Comprueba conectividad con Supabase y exige DB activa (modo DB-first)."""
    status = check_supabase_connection()

    if not status["configured"]:
        console.print("[bold red]Error:[/] Supabase no configurado. Define SUPABASE_URL y SUPABASE_ANON_KEY en .env")
        sys.exit(1)

    if status["connected"]:
        console.print("[green]✓[/] Supabase conectado — modo DB-first activo")
    else:
        console.print(
            f"[bold red]Error:[/] Supabase configurado pero no accesible\n"
            f"  [dim]Error: {status['error']}[/]"
        )
        sys.exit(1)


async def _switch_provider(agent: TrainerAgent, config: dict) -> dict:
    """/proveedor: cambia de proveedor de IA, modelo o clave con las mismas preguntas que la primera vez (26/10)."""
    _debug_console(f"\n[bold dimgray][debug] Tokens de esta sesión con {_ia_provider.label(config)}: "
                   f"{agent.total_prompt_tokens + agent.total_completion_tokens:,}[/]")
    new_config = _ask_ia_provider(config)
    # Una sola clave en el .env: con otro proveedor, la suya; con el mismo, solo si se pide.
    same_provider = new_config["provider"] == config["provider"]
    if not same_provider or not _has_key() or Confirm.ask("  ¿Cambiar también la clave?", default=False):
        await _ask_api_key(new_config, "Clave de tu proveedor de IA.")
    save_ia_provider(new_config["provider"], new_config["base_url"], new_config["model"], new_config.get("chat_model"))
    agent.set_provider(new_config)
    console.print(Panel.fit(
        f"[bold green]Proveedor de IA guardado[/]\n{_ia_provider.label(new_config)}",
        title="[bold blue]Kairos Coach — Proveedor de IA[/]",
        border_style="green",
    ))
    return new_config


def _handle_cli_command(user_input: str, agent: TrainerAgent) -> bool:
    """Comandos de la CLI (/perfil…, /carga, /plan…, /menu, /ayuda). True si la entrada era un comando."""
    cmd = user_input.strip().lower()
    if cmd in {"/perfil", "/profile"}:
        _show_profile()
        return True

    if cmd in {"/menu"}:
        _show_menu()
        return True

    if cmd in {"/ayuda", "/help", "/?"}:
        _show_help()
        return True

    if cmd in {"/perfil editar objetivo", "/perfil editar goal"}:
        profile = _load_user_profile()
        _ask_goals(profile)
        _save_user_profile(profile)
        agent.user_profile = _load_user_profile()
        console.print("[green]✓[/] Objetivos actualizados.")
        return True

    if cmd in {"/perfil editar salud", "/perfil editar health"}:
        profile = _load_user_profile()
        _ask_health(profile)
        _save_user_profile(profile)
        agent.user_profile = _load_user_profile()
        console.print("[green]✓[/] Datos de salud actualizados.")
        return True

    if cmd in {"/perfil editar", "/profile edit"}:
        profile = _load_user_profile()
        _ask_goals(profile)
        _ask_health(profile)
        _save_user_profile(profile)
        agent.user_profile = _load_user_profile()
        console.print("[green]✓[/] Perfil actualizado.")
        return True

    if cmd.startswith("/perfil umbral fc"):
        parts = user_input.strip().split()
        if len(parts) < 4:
            console.print("[yellow]Uso:[/] /perfil umbral fc <lpm> (ej: /perfil umbral fc 169)")
            return True
        profile = _load_user_profile()
        ok, msg = _set_hr_threshold(profile, parts[3].strip())
        if not ok:
            console.print(f"[red]✗[/] {msg}")
            return True
        _save_user_profile(profile)
        agent.user_profile = _load_user_profile()
        console.print(f"[green]✓[/] FC umbral actualizada a [bold]{msg} lpm[/bold] desde hoy (Kairos y referencia de TrainingPeaks).")
        return True

    if cmd.startswith("/perfil umbral"):
        raw_cmd = user_input.strip()
        parts = raw_cmd.split(maxsplit=2)
        if len(parts) < 3 or not parts[2].strip():
            console.print("[yellow]Uso:[/] /perfil umbral <mm:ss> (ej: /perfil umbral 4:15)")
            return True
        profile = _load_user_profile()
        ok, msg = _set_running_threshold_pace(profile, parts[2].strip())
        if not ok:
            console.print(f"[red]✗[/] {msg}")
            return True
        _save_user_profile(profile)
        agent.user_profile = _load_user_profile()
        console.print(f"[green]✓[/] Umbral running actualizado a [bold]{msg} min/km[/bold].")
        return True

    if cmd.startswith("/perfil fc"):
        raw_cmd = user_input.strip()
        parts = raw_cmd.split()
        if len(parts) < 4:
            console.print("[yellow]Uso:[/] /perfil fc <reposo> <max> (ej: /perfil fc 48 186)")
            return True
        profile = _load_user_profile()
        ok, msg = _set_hr_profile_values(profile, parts[2].strip(), parts[3].strip())
        if not ok:
            console.print(f"[red]✗[/] {msg}")
            return True
        _save_user_profile(profile)
        agent.user_profile = _load_user_profile()
        console.print(f"[green]✓[/] Perfil de FC actualizado: [bold]{msg}[/bold].")
        return True

    if cmd.startswith("/carga"):
        _show_load_trend_cli(agent, cmd)
        return True

    if cmd.startswith("/plan"):
        action, arg = _parse_plan_command(cmd)
        if action == "list":
            _show_training_plans()
        elif action == "view":
            _show_training_plan(arg or "")
        elif action == "activate":
            _activate_training_plan_cli(arg or "", agent)
        elif action == "create":
            return False  # asíncrono (usa el motor de planes): lo atiende el bucle principal
        else:
            _show_plan_help()
        return True
    return False


def _warm_imports() -> None:
    """Importa por adelantado lo que el arranque necesitará más tarde: el cliente MCP y el del LLM."""
    for name in ("mcp", "mcp.client.stdio", "mcp.shared.exceptions", "openai"):
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — si falla aquí, fallará igual (y con su error) al usarlo
            log.debug("Precarga de %s: %s", name, exc)


class _StartupTimer:
    """Marcas del arranque en agent.log (28/10, pendiente 8c): tiempo y consultas a Supabase de cada paso."""

    def __init__(self) -> None:
        self.t0 = self.last = time.perf_counter()
        _storage.reset_db_stats()
        _storage.open_read_cache()  # lecturas de Supabase del arranque; se cierra con done()

    def done(self) -> None:
        self.mark("resumen_inicial")
        _storage.close_read_cache()

    def mark(self, step: str) -> None:
        now = time.perf_counter()
        db = _storage.db_stats()
        log.info("[ARRANQUE] %s %.1fs (supabase=%d %.2fs) acumulado=%.1fs", step, now - self.last, db["calls"], db["seconds"],
                 now - self.t0)
        self.last = now
        _storage.reset_db_stats()


async def main() -> None:
    global log, _DEBUG_CONSOLE
    log, _DEBUG_CONSOLE = _configure_logging()
    timer = _StartupTimer()
    # openai y mcp (≈ 1,5 s de importar) se cargan en un hilo mientras el arranque espera a Supabase y al servidor
    # MCP; el código los importa al usarlos y, si el hilo aún no ha terminado, espera a que acabe (8c, 28/10).
    threading.Thread(target=_warm_imports, daemon=True, name="warm-imports").start()
    # El sondeo de Zscaler (~0,5 s, solo para el aviso del entorno) va en un hilo mientras se conecta (8c, 28/10).
    network_probe = asyncio.ensure_future(asyncio.to_thread(_detect_zscaler))

    # Fail-fast de infraestructura: evita pedir credenciales si la DB no está lista.
    _check_and_migrate_supabase()
    timer.mark("supabase")

    username, credentials, is_new_user, app_password = _authenticate_or_register_user()
    _ensure_garmin_credentials(credentials, app_password=app_password)
    _check_garmin_env()
    timer.mark("sesion")

    provider = await _ensure_ia_provider(network_probe)
    timer.mark("proveedor_ia")

    console.print(Panel.fit(
        f"[bold green]Kairos Coach[/] — Tu entrenador personal con IA\n"
        f"[dim]{_ia_provider.label(provider)}[/dim]\n"
        "[dim]Conectando con Garmin Connect...[/]",
        border_style="green",
    ))

    async with garmin_mcp_session() as session:
        agent = TrainerAgent(mcp_session=session, provider=provider)
        # Aviso cuando el proveedor da 429 o 503 y Kairos espera para reintentar (29/10).
        agent.llm_wait_notice = lambda text: console.print(f"[yellow]⏳ {text}[/]")
        timer.mark("mcp_y_agente")

        console.print("[dim]Cargando herramientas de Garmin...[/]")

        async def _garmin_user_profile() -> tuple[str | None, bool]:
            try:
                from agent.mcp.client import call_tool
                return await call_tool(session, "get_user_profile", {}), True
            except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, KeyError) as exc:
                log.debug("Verificación inicial de Garmin falló (no bloqueante): %s", exc)
                return None, False

        # Herramientas y zonas de FC (initialize) a la vez que el perfil de Garmin, que sirve para comprobar el
        # acceso y para los datos personales: antes, una cosa detrás de otra y el perfil pedido dos veces (8c).
        _, (test_raw, garmin_checked) = await asyncio.gather(agent.initialize(), _garmin_user_profile())
        timer.mark("initialize_y_perfil_garmin")
        # Las de Garmin (servidor MCP) y las internas de Kairos (kairos_*, sin servidor).
        n_internal = sum(1 for t in agent.tools_schema if str(((t or {}).get("function") or {}).get("name", "")).startswith("kairos_"))
        console.print(f"[green]✓[/] {len(agent.tools_schema)} herramientas disponibles "
                      f"({len(agent.tools_schema) - n_internal} de Garmin + {n_internal} de Kairos)\n")

        # ── Verificación de acceso a Garmin ───────────────────────────────
        # Detectar si la contraseña ha cambiado en Garmin Connect antes de continuar.
        garmin_auth_failed = False
        try:
            if not garmin_checked:
                raise RuntimeError("sin respuesta de get_user_profile")
            garmin_auth_failed = (
                test_raw is None
                or (isinstance(test_raw, str) and any(
                    kw in test_raw.lower()
                    for kw in ("401", "unauthorized", "invalid credentials", "login failed",
                               "authentication", "forbidden", "403")
                ))
            )
            if garmin_auth_failed:
                new_pw = _handle_garmin_password_change()
                if not new_pw:
                    console.print("[bold red]No se pudo actualizar la contraseña. Saliendo.[/]")
                    return
                app_password = new_pw
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            log.debug("Verificación inicial de Garmin falló (no bloqueante): %s", exc)
            pass  # No bloquear el arranque si el test de verificación falla
        timer.mark("verificacion_garmin")

        # Sincronizar datos personales desde Garmin
        profile_changes = await _sync_from_garmin(
            agent, profile_raw=test_raw if garmin_checked and not garmin_auth_failed else None
        )
        agent.user_profile = _load_user_profile()
        timer.mark("sync_garmin")

        # Umbral de running: se solicita una vez al usuario y se guarda por perfil.
        # A partir de aquí, el cálculo de TSS de carrera usa este valor persistido.
        agent.user_profile = _ensure_running_threshold_pace(agent.user_profile)

        # Si es usuario nuevo o aún no terminó setup, completar onboarding.
        is_first = _is_first_time()
        if is_new_user or is_first:
            _run_first_time_setup()
            agent.user_profile = _load_user_profile()
        # Perfiles anteriores (26/10): patologías separadas de lesiones y medicación si hay diabetes.
        agent.user_profile = _ensure_health_profile(agent.user_profile)

        # Inicializar/enriquecer base de conocimiento para onboarding de usuario nuevo.
        current_kb = (load_athlete_knowledge() or "").strip()
        if is_new_user:
            enrichment = await agent.build_onboarding_mcp_enrichment()
            seeded = _build_enriched_athlete_knowledge(agent.user_profile, enrichment)
            save_athlete_knowledge(seeded)
            console.print("[green]✓[/] Base de conocimiento inicial enriquecida y guardada.")
            agent.knowledge_chunks.append({"source": "db:athlete_knowledge", "text": seeded[:4000]})
            if "db:athlete_knowledge" not in agent.knowledge_sources:
                agent.knowledge_sources.append("db:athlete_knowledge")
        elif not current_kb:
            enrichment = await agent.build_onboarding_mcp_enrichment()
            seeded = _build_enriched_athlete_knowledge(agent.user_profile, enrichment)
            save_athlete_knowledge(seeded)
            console.print("[green]✓[/] Base de conocimiento creada desde perfil + datos MCP.")
            agent.knowledge_chunks.append({"source": "db:athlete_knowledge", "text": seeded[:4000]})
            if "db:athlete_knowledge" not in agent.knowledge_sources:
                agent.knowledge_sources.append("db:athlete_knowledge")

        timer.mark("perfil_y_conocimiento")
        active = get_active_user()
        console.print(
            f"[dim]Usuario activo: {active.get('username') or username}"
            f" ({active.get('user_id') or 'sin-id'})[/]"
        )

        # ── Cálculo de carga/fatiga previo al briefing ────────────────────────────────
        # Usuario nuevo: backfill completo desde Garmin (ventana histórica del modelo)
        # para poblar DB antes del primer flujo de coaching.
        # Usuario existente: cálculo incremental desde último registro persistido.
        # El resumen inicial empieza a la vez (8c, 28/10): pide a Garmin sus datos mientras se calcula la carga y
        # espera a que termine solo para leer la serie.
        load_task = asyncio.ensure_future(agent.compute_and_persist_load_metrics(force_full_recalc=is_new_user))
        skip_startup_status = str(os.environ.get("KAIROS_SKIP_STARTUP_STATUS", "")).strip().lower() in {"1", "true", "yes", "on"}
        status_task = None if skip_startup_status else asyncio.ensure_future(
            agent.build_startup_status_markdown(profile_changes=profile_changes, load_ready=load_task)
        )
        try:
            saved_formula_v = int(((agent.user_profile or {}).get("load_metrics") or {}).get("formula_version") or 0)
            if not is_new_user and saved_formula_v and saved_formula_v != _TSS_FORMULA_VERSION:
                console.print(
                    "[yellow]ℹ Detecté cambios en el modelo de carga/TSS. "
                    "Voy a recalcular histórico y este inicio puede tardar un poco más.[/]"
                )
            _load_start = time.time()

            async def _compute_with_counter() -> None:
                task = load_task
                while not task.done():
                    elapsed = int(time.time() - _load_start)
                    console.print(
                        f"\r[dim]⏳ Consultando Garmin... {elapsed}s[/]",
                        end="",
                    )
                    await asyncio.sleep(1)
                await task

            await _compute_with_counter()
            load_meta = getattr(agent, "_last_load_compute_meta", {}) or {}
            load_mode = str(load_meta.get("mode") or "")
            load_reason = str(load_meta.get("reason") or "").strip()
            if load_mode == "full_recalc":
                console.print(
                    "[yellow]ℹ Kairos tardó más porque recalculó el histórico de carga completo"
                    + (f" ({load_reason})" if load_reason else "")
                    + ".[/]"
                )
            elif load_mode == "incremental_refresh":
                console.print(
                    "[dim]ℹ Kairos refrescó días recientes para incorporar actividades nuevas"
                    + (f" ({load_reason})" if load_reason else "")
                    + ".[/]"
                )
            lm = _load_metrics_view(agent.user_profile)
            last = lm.get("latest") or {}
            if last.get("atl") or last.get("ctl"):
                console.print(
                    f"[green]✓[/] Carga/fatiga: "
                    f"CTL (Estado físico)={float(last.get('ctl', 0)):.1f} · "
                    f"ATL (Fatiga)={float(last.get('atl', 0)):.1f} · "
                    f"TSB (Forma)={float(last.get('tsb', 0)):.1f} "
                    f"([dim]{len(lm.get('series', []))} días[/dim])"
                )
            else:
                console.print("[dim yellow]⚠ Carga/fatiga: sin actividades suficientes para calcular[/]")
        except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as _load_exc:
            console.print(f"[dim yellow]⚠ Carga/fatiga no calculada: {_load_exc}[/]")

        timer.mark("carga")
        # Estado proactivo al arranque (especialmente para usuario existente).
        if status_task is None:
            console.print("[dim]Resumen inicial omitido por KAIROS_SKIP_STARTUP_STATUS.[/]")
        else:
            try:
                with console.status("[dim]Kairos está pensando y preparando tu resumen inicial...[/]"):
                    proactive_status = await status_task
                console.print(Markdown(proactive_status))
            except (RuntimeError, ValueError, TypeError, OSError, TimeoutError, json.JSONDecodeError, KeyError) as status_exc:
                console.print(f"[dim yellow]No se pudo generar el estado proactivo inicial: {status_exc}[/]")

        timer.done()
        _debug_console("[dim dimgray][debug] Inicio de la sesión. Tokens gastados: 0[/]")
        
        _debug_console(f"[dim dimgray][debug] Tokens consumidos hoy con tu proveedor de IA: {agent.today_ia_tokens():,}[/]")

        console.print(Rule("[dim]Escribe tu pregunta · usa [bold]/menu[/bold] para ver opciones · [bold]salir[/bold] para terminar[/]"))

        while True:
            try:
                user_input = Prompt.ask("\n[bold cyan]Tú[/]")
            except (KeyboardInterrupt, EOFError):
                break

            if user_input.strip().lower() in {"salir", "exit", "quit", "q"}:
                break

            # Comandos especiales
            user_input = _with_plan_slash(user_input)
            cmd = user_input.strip().lower()

            if cmd in {"/proveedor", "/provider"}:
                provider = await _switch_provider(agent, provider)
                continue

            if _parse_plan_command(cmd)[0] == "create":
                await _create_training_plan_cli(agent)
                continue

            if _handle_cli_command(user_input, agent):
                continue

            if not user_input.strip():
                continue

            try:
                with console.status("[bold green]Kairos Coach está consultando Garmin y analizando tus datos...[/]"):
                    response = await agent.chat(user_input)
                if getattr(agent, "llm_auth_failed", False):
                    # Clave caducada o revocada (26/10): se pide la nueva, se guarda en .env y se repite la pregunta.
                    del agent.conversation_history[-2:]
                    await _ask_api_key(provider, f"{_ia_provider.label(provider)} ha rechazado tu clave (puede haber caducado).")
                    agent.set_provider(provider)
                    with console.status("[bold green]Kairos Coach está consultando Garmin y analizando tus datos...[/]"):
                        response = await agent.chat(user_input)
                console.print("\n[bold green]Kairos Coach[/]")
                console.print(Markdown(_format_coach_markdown(response)))
                # Checkpoint incremental del resumen para no bloquear el cierre.
                agent.save_session_summary_checkpoint()
            except Exception as e:
                # Red general (28/10): un fallo en una pregunta (proveedor caído, error de MCP o de Supabase, un
                # bug) no cierra la sesión; queda en agent.log con su traza y se puede seguir preguntando.
                log.exception("Error en chat del agente")
                console.print(f"\n[bold red]Error en el Agente:[/] {e or type(e).__name__}")

        # Al salir de la sesión, guardar checkpoint ligero sin llamada al LLM.
        if agent.conversation_history:
            try:
                with console.status("[dim]Guardando checkpoint de sesión...[/]"):
                    agent.save_session_summary_checkpoint()
                console.print("[dim]✓ Sesión guardada en memoria[/]")
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                log.debug("No se pudo guardar checkpoint final de sesión: %s", exc)

        # Al salir de la sesión, mostrar resumen de tokens gastados
        p_tokens = agent.total_prompt_tokens
        c_tokens = agent.total_completion_tokens
        t_tokens = p_tokens + c_tokens
        _debug_console("\n[bold dimgray][debug] Fin de la sesión. Resumen de tokens gastados en esta sesión:[/]")
        _debug_console(f"[dim dimgray]        - Prompt/Entrada:    {p_tokens:,}[/]")
        _debug_console(f"[dim dimgray]        - Completion/Salida: {c_tokens:,}[/]")
        _debug_console(f"[dim dimgray]        - Total acumulado:   {t_tokens:,}[/]")
        
        _debug_console(f"[dim dimgray][debug] Tokens consumidos hoy con tu proveedor de IA: {agent.today_ia_tokens():,}[/]")

    console.print("\n[dim]Sesión finalizada. ¡Hasta el próximo entrenamiento![/]")


if __name__ == "__main__":
    asyncio.run(main())
