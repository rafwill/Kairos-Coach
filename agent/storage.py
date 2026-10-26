"""
agent/storage.py
Capa de persistencia multiusuario (DB-first) para Kairos Coach.

Este módulo usa Supabase como fuente de verdad para:
- app_user (usuario de la app, vinculado a su cuenta de Supabase Auth)
- user_profile
- session_context
- athlete_knowledge
- ia_provider
"""

import json
import logging
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from supabase_auth import SyncSupportedStorage

log = logging.getLogger(__name__)


_zscaler_cache: bool | None = None
_sb = None
_sb_ready = False
_sb_token_check_at = 0.0  # hora (time.time) de la próxima comprobación de caducidad del token
_active_user_id: str | None = None
_active_username: str | None = None


def set_active_user(user_id: str | None, username: str | None = None) -> None:
    """Define el usuario activo para escopar lecturas/escrituras."""
    global _active_user_id, _active_username
    _active_user_id = (user_id or "").strip() or None
    _active_username = (username or "").strip().lower() or None


def get_active_user() -> dict:
    """Devuelve metadatos del usuario activo en esta ejecución."""
    return {
        "user_id": _active_user_id,
        "username": _active_username,
    }


def _require_active_user_id() -> str:
    if not _active_user_id:
        raise RuntimeError("No hay usuario activo en sesión")
    return _active_user_id


def is_zscaler_network() -> bool:
    """Detecta si el tráfico sale por proxy SSL corporativo Zscaler."""
    global _zscaler_cache
    if _zscaler_cache is not None:
        return _zscaler_cache

    try:
        with httpx.Client(timeout=4.0, follow_redirects=False, verify=True) as client:
            resp = client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={"x-goog-api-key": "probe"},
            )
            body = resp.text or ""
            _zscaler_cache = "zscaler" in body.lower()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        err = str(exc).lower()
        _zscaler_cache = (
            "zscaler" in err
            or "certificate_verify_failed" in err
            or "unable to get local issuer certificate" in err
        )

    return _zscaler_cache


def _refresh_token_if_expiring() -> None:
    """Renueva el token de la sesión si caduca en menos de un minuto, mirando la hora del reloj (26/10).

    El auto-refresco del cliente va con un temporizador en segundo plano: tras suspender el equipo, la primera
    consulta llegaba antes que la renovación y Supabase respondía "JWT expired" (PGRST303), que tumbaba Kairos.
    `get_session()` renueva si hace falta y avisa al cliente, que pone el token nuevo en las consultas.
    """
    global _sb_token_check_at
    now = time.time()
    if now < _sb_token_check_at:
        return
    try:
        session = _sb.auth.get_session()
    except Exception as exc:  # noqa: BLE001 — sin red o refresh token rechazado: la consulta fallará como antes
        log.warning("[storage] No se pudo renovar la sesión de Supabase: %s", exc)
        _sb_token_check_at = now + 30
        return
    expires_at = getattr(session, "expires_at", None) if session else None
    _sb_token_check_at = (expires_at - 60) if expires_at else now + 60


def _supabase():
    """Devuelve el cliente Supabase si está configurado, o None."""
    global _sb, _sb_ready
    if _sb_ready:
        if _sb:
            _refresh_token_if_expiring()
        return _sb
    _sb_ready = True

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()

    if not url or not key or "xxx" in url.lower():
        return None

    try:
        if is_zscaler_network():
            import truststore

            truststore.inject_into_ssl()
        from supabase import ClientOptions, create_client

        # La sesión de Supabase Auth se guarda en local: con ella las consultas llevan el token del
        # usuario y las políticas RLS le dejan ver solo sus filas (supabase/README.md).
        _sb = create_client(
            url,
            key,
            options=ClientOptions(storage=_FileSessionStorage(_session_file()), persist_session=True, auto_refresh_token=True),
        )
        return _sb
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        log.warning("[storage] Supabase no disponible: %s", exc)
        return None


def _require_supabase():
    sb = _supabase()
    if not sb:
        raise RuntimeError("Supabase no está configurado o no es accesible")
    return sb


def _is_missing_table_error(exc: Exception, table_name: str) -> bool:
    """Detecta errores de PostgREST por tabla ausente (schema cache/migration pendiente)."""
    code = ""
    message = ""
    payload = None
    try:
        payload = exc.args[0] if getattr(exc, "args", None) else None
    except (IndexError, TypeError):
        payload = None

    if isinstance(payload, dict):
        code = str(payload.get("code") or "")
        message = str(payload.get("message") or "")

    if not message:
        message = str(exc)

    msg = message.lower()
    tbl = str(table_name or "").lower()
    return bool(
        code == "PGRST205"
        or (
            ("schema cache" in msg or "could not find the table" in msg)
            and tbl in msg
        )
    )


def _is_statement_timeout_error(exc: Exception) -> bool:
    """Detecta timeout de sentencia en respuestas PostgREST/Supabase."""
    code = ""
    message = ""
    payload = None
    try:
        payload = exc.args[0] if getattr(exc, "args", None) else None
    except (IndexError, TypeError):
        payload = None

    if isinstance(payload, dict):
        code = str(payload.get("code") or "")
        message = str(payload.get("message") or "")

    if not message:
        message = str(exc)

    msg = message.lower()
    return code == "57014" or "statement timeout" in msg


def _normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def _sanitize_credentials_for_storage(credentials: dict | None) -> dict:
    """Elimina secretos en texto claro — solo se permite garmin_password_encrypted."""
    creds = dict(credentials or {})
    creds.pop("garmin_password", None)
    creds.pop("garmin_password_strategy", None)
    return creds


def _get_fernet():
    """Devuelve instancia Fernet usando ENCRYPTION_KEY del entorno."""
    from cryptography.fernet import Fernet
    key = (os.environ.get("ENCRYPTION_KEY") or "").strip().encode()
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY no configurada en .env. "
            "Genera una con: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key)


def encrypt_password(plaintext: str) -> str:
    """Cifra una contraseña con Fernet (AES-128-CBC + HMAC-SHA256)."""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_password(encrypted: str) -> str | None:
    """Descifra una contraseña. Devuelve None si falla (clave cambiada o corrupto)."""
    try:
        return _get_fernet().decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except Exception:
        return None


# ─── Sesión de Supabase Auth ───────────────────────────────────────────────────
# Cada usuario de Kairos tiene una cuenta de Supabase Auth (email + la misma contraseña que en
# Garmin Connect). Con sesión, las consultas llevan su token y las políticas RLS de
# supabase/rls.sql le dejan ver solo sus filas. Nada se lee de la base de datos antes de iniciar
# sesión. Detalle y puesta en marcha: supabase/README.md.


class _FileSessionStorage(SyncSupportedStorage):
    """Sesión de Supabase Auth en un fichero local, fuera del repo, para no pedir la contraseña en
    cada arranque (como los tokens de Garmin del MCP). Lo comparten Kairos y las herramientas."""

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get_item(self, key: str) -> str | None:
        return self._load().get(key)

    def set_item(self, key: str, value: str) -> None:
        data = self._load()
        data[key] = value
        self._save(data)

    def remove_item(self, key: str) -> None:
        data = self._load()
        if data.pop(key, None) is not None:
            self._save(data)


def _session_file() -> Path:
    custom = (os.environ.get("KAIROS_SESSION_FILE") or "").strip()
    return Path(custom) if custom else Path.home() / ".kairos" / "supabase_session.json"


def _auth_failure(exc: Exception) -> dict:
    """Traduce un error de Supabase Auth a un motivo que la CLI sabe tratar."""
    msg = str(getattr(exc, "message", "") or exc)
    low = msg.lower()
    if "invalid login credentials" in low:
        return {"ok": False, "reason": "invalid", "error": "Email o contraseña incorrectos (o la cuenta aún no existe)."}
    if "email not confirmed" in low:
        return {"ok": False, "reason": "unconfirmed", "error": "Email sin confirmar: abre el enlace del correo de Supabase."}
    if "kairos_claim_app_user" in low and ("does not exist" in low or "could not find" in low):
        return {"ok": False, "reason": "schema", "error": "Falta ejecutar supabase/schema.sql en Supabase (ver supabase/README.md)."}
    return {"ok": False, "reason": "error", "error": msg}


def _activate_linked_app_user(email: str) -> dict | None:
    """Vincula la cuenta de Auth con su app_user (por email, solo si está confirmado) y lo activa.

    Devuelve la fila de app_user, o None si la cuenta es nueva y aún no tiene app_user.
    """
    sb = _require_supabase()
    res = sb.rpc("kairos_claim_app_user").execute()
    app_user_id = res.data if isinstance(res.data, str) else None
    if not app_user_id:
        return None
    rows = sb.table("app_user").select("id,username,credentials").eq("id", app_user_id).limit(1).execute()
    row = rows.data[0] if rows.data else None
    if row:
        set_active_user(row["id"], _normalize_username(email))
    return row


def restore_session(email: str) -> dict | None:
    """Recupera la sesión guardada en este ordenador si es de ese email (la refresca si ha caducado)
    y activa su app_user. None si no hay sesión válida: entonces hay que iniciar sesión."""
    uname = _normalize_username(email)
    if not uname:
        return None
    try:
        sb = _require_supabase()
        session = sb.auth.get_session()
        if not session or _normalize_username(getattr(session.user, "email", "")) != uname:
            return None
        return _activate_linked_app_user(uname)
    except Exception as exc:  # noqa: BLE001 — sesión caducada, sin red o esquema sin migrar: se pide login
        log.info("[storage] No se pudo recuperar la sesión guardada: %s", exc)
        return None


def sign_in(email: str, password: str) -> dict:
    """Inicia sesión en Supabase Auth y activa el app_user vinculado.

    {"ok": True, "app_user": fila | None (cuenta nueva sin app_user)} o {"ok": False, "reason", "error"}.
    """
    uname = _normalize_username(email)
    if not uname or not password:
        return {"ok": False, "reason": "invalid", "error": "Credenciales incompletas"}
    try:
        sb = _require_supabase()
        sb.auth.sign_in_with_password({"email": uname, "password": password})
        return {"ok": True, "app_user": _activate_linked_app_user(uname), "error": None}
    except Exception as exc:  # noqa: BLE001 — los errores de Auth llegan con varias clases
        return _auth_failure(exc)


def sign_up(email: str, password: str) -> dict:
    """Crea la cuenta de Supabase Auth. Con confirmación de email (la configuración segura), no hay
    sesión hasta abrir el enlace del correo: {"ok": True, "needs_confirmation": True}."""
    uname = _normalize_username(email)
    if "@" not in uname:
        return {"ok": False, "reason": "invalid", "error": "El usuario debe ser un email"}
    if not password or len(password) < 6:
        return {"ok": False, "reason": "invalid", "error": "Contraseña demasiado corta (mínimo 6)"}
    try:
        sb = _require_supabase()
        res = sb.auth.sign_up({"email": uname, "password": password})
        return {"ok": True, "needs_confirmation": getattr(res, "session", None) is None, "error": None}
    except Exception as exc:  # noqa: BLE001
        return _auth_failure(exc)


def create_app_user(email: str, credentials: dict | None = None) -> dict:
    """Crea el app_user de una cuenta nueva (con sesión) vinculado a ella y lo activa."""
    uname = _normalize_username(email)
    sb = _require_supabase()
    session = sb.auth.get_session()
    if not session:
        raise RuntimeError("Hace falta iniciar sesión para crear el usuario")
    row = {
        "id": uuid4().hex,
        "username": uname,
        "auth_user_id": session.user.id,
        "credentials": _sanitize_credentials_for_storage(credentials),
    }
    sb.table("app_user").insert(row).execute()
    set_active_user(row["id"], uname)
    return row


def update_login_password(new_password: str) -> dict:
    """Cambia la contraseña de Supabase Auth (la misma que la de Garmin) y la cifrada en local."""
    if not new_password or len(new_password) < 6:
        return {"ok": False, "error": "Datos inválidos (mínimo 6 caracteres)"}
    try:
        sb = _require_supabase()
        sb.auth.update_user({"password": new_password})
        rows = sb.table("app_user").select("credentials").eq("id", _require_active_user_id()).limit(1).execute()
        creds = dict((rows.data[0] if rows.data else {}).get("credentials") or {})
        creds["garmin_password_encrypted"] = encrypt_password(new_password)
        update_user_credentials(creds)
        return {"ok": True, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(getattr(exc, "message", "") or exc)}


def sign_out() -> None:
    """Cierra la sesión y borra la guardada en este ordenador."""
    try:
        _require_supabase().auth.sign_out()
    except Exception as exc:  # noqa: BLE001
        log.info("[storage] sign_out: %s", exc)
    set_active_user(None)


def update_user_credentials(credentials: dict) -> None:
    """Actualiza credenciales auxiliares del usuario activo."""
    uid = _require_active_user_id()
    sb = _require_supabase()
    sb.table("app_user").update({"credentials": _sanitize_credentials_for_storage(credentials)}).eq("id", uid).execute()


def load_user_profile() -> dict:
    uid = _require_active_user_id()
    sb = _require_supabase()
    res = sb.table("user_profile").select("data").eq("app_user_id", uid).limit(1).execute()
    if not res.data:
        return {}
    return res.data[0].get("data") or {}


def save_user_profile(profile: dict) -> None:
    uid = _require_active_user_id()
    sb = _require_supabase()
    payload = {"app_user_id": uid, "data": profile or {}}

    # Supabase puede devolver timeout transitorio (57014) bajo carga; reintentamos
    # pocas veces para evitar fallo fatal de arranque.
    for attempt in range(3):
        try:
            sb.table("user_profile").upsert(payload).execute()
            return
        except Exception as exc:
            if not _is_statement_timeout_error(exc) or attempt == 2:
                raise
            delay_s = 0.25 * (attempt + 1)
            log.warning(
                "save_user_profile: statement timeout (intento %d/3), reintentando en %.2fs",
                attempt + 1,
                delay_s,
            )
            time.sleep(delay_s)


def load_session_context() -> dict:
    uid = _require_active_user_id()
    sb = _require_supabase()
    res = (
        sb.table("session_context")
        .select("history,session_summaries")
        .eq("app_user_id", uid)
        .limit(1)
        .execute()
    )
    if not res.data:
        return {"history": [], "session_summaries": []}
    row = res.data[0]
    return {
        "history": row.get("history") or [],
        "session_summaries": row.get("session_summaries") or [],
    }


def save_session_context(ctx: dict) -> None:
    uid = _require_active_user_id()
    sb = _require_supabase()
    sb.table("session_context").upsert(
        {
            "app_user_id": uid,
            "history": ctx.get("history", []),
            "session_summaries": ctx.get("session_summaries", []),
        }
    ).execute()


def save_history_entry(role: str, content: str) -> None:
    ctx = load_session_context()
    ctx.setdefault("history", []).append({"role": role, "content": content})
    ctx["history"] = ctx["history"][-50:]
    save_session_context(ctx)


def load_session_summaries() -> list[dict]:
    return load_session_context().get("session_summaries", [])


def persist_session_summary_daily(summary: str) -> None:
    """Guarda/actualiza el resumen del día actual en session_summaries.

    Hace upsert por fecha para permitir checkpoints frecuentes sin inflar el historial.
    """
    ctx = load_session_context()
    summaries = list(ctx.get("session_summaries") or [])
    today_iso = date.today().isoformat()
    clipped = (summary or "")[:600]

    updated = False
    for item in reversed(summaries):
        if isinstance(item, dict) and str(item.get("date") or "") == today_iso:
            item["summary"] = clipped
            updated = True
            break

    if not updated:
        summaries.append({"date": today_iso, "summary": clipped})

    ctx["session_summaries"] = summaries[-10:]
    save_session_context(ctx)


def load_athlete_knowledge() -> str:
    uid = _require_active_user_id()
    sb = _require_supabase()
    res = sb.table("athlete_knowledge").select("content").eq("app_user_id", uid).limit(1).execute()
    if not res.data:
        return ""
    return (res.data[0].get("content") or "").strip()


def save_athlete_knowledge(content: str) -> None:
    uid = _require_active_user_id()
    sb = _require_supabase()
    sb.table("athlete_knowledge").upsert({"app_user_id": uid, "content": (content or "").strip()}).execute()


def _normalize_plan_status(status: str | None, default: str = "draft") -> str:
    raw = str(status or "").strip().lower()
    if raw in {"draft", "active", "inactive", "archived"}:
        return raw
    return default


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _build_training_plan_row(app_user_id: str, plan: dict) -> dict:
    status = _normalize_plan_status((plan or {}).get("status"), default="draft")
    if bool((plan or {}).get("active")):
        status = "active"
    return {
        "id": (plan or {}).get("id") or uuid4().hex,
        "app_user_id": app_user_id,
        "title": str((plan or {}).get("title") or (plan or {}).get("name") or "Plan").strip(),
        "description": str((plan or {}).get("description") or "").strip(),
        "objective": str((plan or {}).get("objective") or "").strip(),
        "difficulty": str((plan or {}).get("difficulty") or "moderate").strip().lower(),
        "duration_weeks": max(0, _to_int((plan or {}).get("duration_weeks"), 0)),
        "status": status,
        "source": str((plan or {}).get("source") or "agent").strip(),
        "plan_data": dict((plan or {}).get("plan_data") or {}),
    }


def _build_training_plan_session_rows(plan_id: str, sessions: list[dict] | None) -> list[dict]:
    rows: list[dict] = []
    for item in list(sessions or []):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": item.get("id") or uuid4().hex,
                "plan_id": plan_id,
                "week_index": max(1, _to_int(item.get("week_index"), 1)),
                "day_index": max(1, _to_int(item.get("day_index"), 1)),
                "session_type": str(item.get("session_type") or item.get("type") or "running").strip().lower(),
                "duration_min": _to_int(item.get("duration_min"), 0) if item.get("duration_min") is not None else None,
                "intensity": str(item.get("intensity") or "").strip() or None,
                "exercises": list(item.get("exercises") or []),
                "notes": str(item.get("notes") or "").strip(),
                "structured_workout": dict(item.get("structured_workout") or {}),
            }
        )
    return rows


def _insert_training_plan_session_rows(sb, session_rows: list[dict]) -> None:
    """Inserta sesiones de plan con compatibilidad hacia esquemas legacy.

    Si la columna `structured_workout` no existe en la tabla del entorno,
    reintenta sin ese campo para no romper persistencia.
    """
    if not session_rows:
        return
    try:
        sb.table("training_plan_session").insert(session_rows).execute()
        return
    except Exception:
        pass

    legacy_rows = []
    for row in session_rows:
        if not isinstance(row, dict):
            continue
        r = dict(row)
        r.pop("structured_workout", None)
        legacy_rows.append(r)
    if legacy_rows:
        sb.table("training_plan_session").insert(legacy_rows).execute()


def list_training_plans(include_archived: bool = False) -> list[dict]:
    uid = _require_active_user_id()
    sb = _require_supabase()
    query = sb.table("training_plan").select("*").eq("app_user_id", uid)
    if not include_archived:
        query = query.neq("status", "archived")
    try:
        res = query.order("created_at", desc=True).execute()
    except Exception as exc:
        if _is_missing_table_error(exc, "training_plan"):
            log.warning("[storage] training_plan no existe en Supabase; devolviendo lista vacía")
            return []
        raise
    return list(res.data or [])


def get_training_plan(plan_id: str) -> dict | None:
    uid = _require_active_user_id()
    if not plan_id:
        return None
    sb = _require_supabase()
    try:
        res = (
            sb.table("training_plan")
            .select("*")
            .eq("id", plan_id)
            .eq("app_user_id", uid)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        if _is_missing_table_error(exc, "training_plan"):
            log.warning("[storage] training_plan no existe en Supabase; devolviendo None")
            return None
        raise
    return res.data[0] if res.data else None


def get_active_training_plan() -> dict | None:
    uid = _require_active_user_id()
    sb = _require_supabase()
    try:
        res = (
            sb.table("training_plan")
            .select("*")
            .eq("app_user_id", uid)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
    except Exception as exc:
        if _is_missing_table_error(exc, "training_plan"):
            log.warning("[storage] training_plan no existe en Supabase; devolviendo None")
            return None
        raise
    return res.data[0] if res.data else None


def list_training_plan_sessions(plan_id: str) -> list[dict]:
    if not plan_id:
        return []
    sb = _require_supabase()
    try:
        res = (
            sb.table("training_plan_session")
            .select("*")
            .eq("plan_id", plan_id)
            .order("week_index")
            .order("day_index")
            .execute()
        )
    except Exception as exc:
        if _is_missing_table_error(exc, "training_plan_session"):
            log.warning("[storage] training_plan_session no existe en Supabase; devolviendo lista vacía")
            return []
        raise
    return list(res.data or [])


def _next_training_plan_version_number(plan_id: str) -> int:
    sb = _require_supabase()
    res = (
        sb.table("training_plan_version")
        .select("version_number")
        .eq("plan_id", plan_id)
        .order("version_number", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return 1
    return _to_int(res.data[0].get("version_number"), 0) + 1


def save_training_plan_version(plan_id: str, snapshot: dict, change_reason: str = "updated") -> dict:
    if not plan_id:
        raise ValueError("plan_id es obligatorio")
    sb = _require_supabase()
    version_number = _next_training_plan_version_number(plan_id)
    row = {
        "id": uuid4().hex,
        "plan_id": plan_id,
        "version_number": version_number,
        "snapshot": dict(snapshot or {}),
        "change_reason": str(change_reason or "updated").strip(),
    }
    sb.table("training_plan_version").insert(row).execute()
    return row


def create_training_plan(plan: dict, sessions: list[dict] | None = None, change_reason: str = "created") -> dict:
    uid = _require_active_user_id()
    sb = _require_supabase()

    plan_row = _build_training_plan_row(uid, plan or {})
    if plan_row["status"] == "active":
        sb.table("training_plan").update({"status": "inactive"}).eq("app_user_id", uid).eq("status", "active").execute()

    sb.table("training_plan").insert(plan_row).execute()

    session_rows = _build_training_plan_session_rows(plan_row["id"], sessions)
    if session_rows:
        _insert_training_plan_session_rows(sb, session_rows)

    snapshot = {
        "plan": plan_row,
        "sessions": session_rows,
    }
    save_training_plan_version(plan_row["id"], snapshot, change_reason=change_reason)
    return plan_row


def update_training_plan(
    plan_id: str,
    patch: dict,
    sessions: list[dict] | None = None,
    change_reason: str = "updated",
) -> dict:
    uid = _require_active_user_id()
    sb = _require_supabase()
    current = get_training_plan(plan_id)
    if not current:
        raise ValueError("Plan no encontrado")

    patch = dict(patch or {})
    if "status" in patch:
        patch["status"] = _normalize_plan_status(patch.get("status"), default=current.get("status") or "draft")
    if patch.get("active") is True:
        patch["status"] = "active"
    patch.pop("active", None)
    patch.pop("id", None)
    patch.pop("app_user_id", None)

    if patch.get("status") == "active":
        sb.table("training_plan").update({"status": "inactive"}).eq("app_user_id", uid).eq("status", "active").neq("id", plan_id).execute()

    if patch:
        sb.table("training_plan").update(patch).eq("id", plan_id).eq("app_user_id", uid).execute()

    if sessions is not None:
        sb.table("training_plan_session").delete().eq("plan_id", plan_id).execute()
        session_rows = _build_training_plan_session_rows(plan_id, sessions)
        if session_rows:
            _insert_training_plan_session_rows(sb, session_rows)

    plan_row = get_training_plan(plan_id) or current
    session_rows = list_training_plan_sessions(plan_id)
    snapshot = {
        "plan": plan_row,
        "sessions": session_rows,
    }
    save_training_plan_version(plan_id, snapshot, change_reason=change_reason)
    return plan_row


def activate_training_plan(plan_id: str, change_reason: str = "activated") -> dict:
    uid = _require_active_user_id()
    if not get_training_plan(plan_id):
        raise ValueError("Plan no encontrado")

    sb = _require_supabase()
    sb.table("training_plan").update({"status": "inactive"}).eq("app_user_id", uid).eq("status", "active").neq("id", plan_id).execute()
    sb.table("training_plan").update({"status": "active"}).eq("id", plan_id).eq("app_user_id", uid).execute()

    plan_row = get_training_plan(plan_id)
    session_rows = list_training_plan_sessions(plan_id)
    save_training_plan_version(
        plan_id,
        {
            "plan": plan_row or {},
            "sessions": session_rows,
        },
        change_reason=change_reason,
    )
    return plan_row or {}


_IA_TOKENS_DAYS = 90  # días de consumo que se conservan en ia_provider.daily_tokens


def get_ia_provider() -> dict | None:
    """Proveedor de IA del atleta (26/10): {provider, base_url, model, daily_tokens}; None si aún no lo ha dado."""
    uid = _require_active_user_id()
    sb = _require_supabase()
    try:
        res = sb.table("ia_provider").select("provider, base_url, model, daily_tokens").eq("app_user_id", uid).limit(1).execute()
    except Exception as exc:
        if _is_missing_table_error(exc, "ia_provider"):
            raise RuntimeError("Falta la tabla ia_provider en Supabase: ejecuta supabase/schema.sql y supabase/rls.sql.") from exc
        raise
    return res.data[0] if res.data else None


def save_ia_provider(provider: str, base_url: str, model: str) -> None:
    """Guarda (o cambia) el proveedor de IA del atleta; el consumo acumulado se conserva."""
    uid = _require_active_user_id()
    sb = _require_supabase()
    sb.table("ia_provider").upsert(
        {"app_user_id": uid, "provider": provider, "base_url": base_url, "model": model}
    ).execute()


def add_ia_tokens(tokens: int) -> int:
    """Suma tokens al consumo de hoy (ia_provider.daily_tokens, fecha ISO → tokens) y devuelve el total del día.

    Sin fila de proveedor no hay dónde apuntar: devuelve 0. Se conservan los últimos _IA_TOKENS_DAYS días.
    """
    row = get_ia_provider()
    if not row or tokens <= 0:
        return int(((row or {}).get("daily_tokens") or {}).get(date.today().isoformat(), 0) or 0)
    today = date.today().isoformat()
    daily = {k: int(v or 0) for k, v in (row.get("daily_tokens") or {}).items()}
    daily[today] = daily.get(today, 0) + int(tokens)
    daily = dict(sorted(daily.items())[-_IA_TOKENS_DAYS:])
    _require_supabase().table("ia_provider").update({"daily_tokens": daily}).eq(
        "app_user_id", _require_active_user_id()
    ).execute()
    return daily[today]


def check_supabase_connection() -> dict:
    """Comprueba conectividad con Supabase haciendo una query real."""
    global _sb, _sb_ready

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()

    if not url or not key or "xxx" in url.lower():
        return {"configured": False, "connected": False, "error": None}

    _sb_ready = False
    _sb = None
    sb = _supabase()
    if sb is None:
        return {"configured": True, "connected": False, "error": "No se pudo crear el cliente Supabase"}

    try:
        sb.table("app_user").select("id").limit(1).execute()
        return {"configured": True, "connected": True, "error": None}
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        return {"configured": True, "connected": False, "error": str(exc)}


# ─── load_metrics_daily ────────────────────────────────────────────────────────

def get_load_metrics_series(days: int = 120) -> list[dict]:
    """Carga la serie TSS/ATL (Fatiga)/CTL (Estado físico)/TSB (Forma) desde load_metrics_daily (últimos N días)."""
    from datetime import date, timedelta
    sb = _supabase()
    uid = _require_active_user_id()
    if not sb:
        return []
    try:
        start_date = (date.today() - timedelta(days=days)).isoformat()
        resp = (
            sb.table("load_metrics_daily")
            .select("metric_date, tss, atl, ctl, tsb, activities_count")
            .eq("app_user_id", uid)
            .gte("metric_date", start_date)
            .order("metric_date", desc=False)
            .execute()
        )
        return [
            {
                "date": str(r["metric_date"]),
                "tss": round(float(r.get("tss") or 0), 2),
                "atl": round(float(r.get("atl") or 0), 2),
                "ctl": round(float(r.get("ctl") or 0), 2),
                "tsb": round(float(r.get("tsb") or 0), 2),
                "activities_count": int(r.get("activities_count") or 0),
            }
            for r in (resp.data or [])
        ]
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        log.warning("get_load_metrics_series: %s", exc)
        return []


def get_load_metrics_last_date() -> str | None:
    """Devuelve la fecha ISO del último registro en load_metrics_daily, o None."""
    sb = _supabase()
    uid = _require_active_user_id()
    if not sb:
        return None
    try:
        resp = (
            sb.table("load_metrics_daily")
            .select("metric_date")
            .eq("app_user_id", uid)
            .order("metric_date", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return str(rows[0]["metric_date"]) if rows else None
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        log.warning("get_load_metrics_last_date: %s", exc)
        return None


def upsert_load_metrics_series(series: list[dict]) -> None:
    """Persiste (upsert) filas TSS/ATL (Fatiga)/CTL (Estado físico)/TSB (Forma) en load_metrics_daily."""
    sb = _supabase()
    uid = _require_active_user_id()
    if not sb or not series:
        return
    try:
        rows = [
            {
                "app_user_id": uid,
                "metric_date": row["date"],
                "tss": round(float(row.get("tss") or 0), 2),
                "atl": round(float(row.get("atl") or 0), 2),
                "ctl": round(float(row.get("ctl") or 0), 2),
                "tsb": round(float(row.get("tsb") or 0), 2),
                "activities_count": int(row.get("activities_count") or 0),
            }
            for row in series
            if row.get("date")
        ]
        if not rows:
            return
        # Upsert en lotes de 200 para evitar límites de payload
        for i in range(0, len(rows), 200):
            sb.table("load_metrics_daily").upsert(
                rows[i:i + 200], on_conflict="app_user_id,metric_date"
            ).execute()
        log.info("upsert_load_metrics_series: %d filas persistidas", len(rows))
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        log.warning("upsert_load_metrics_series: %s", exc)


def get_load_metrics_row(metric_date: str) -> dict | None:
    """Devuelve la fila de load_metrics_daily de un día concreto (semilla del EWMA), o None."""
    sb = _supabase()
    uid = _require_active_user_id()
    if not sb:
        return None
    try:
        resp = (
            sb.table("load_metrics_daily")
            .select("metric_date, tss, atl, ctl, tsb, activities_count")
            .eq("app_user_id", uid)
            .eq("metric_date", metric_date)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        r = rows[0]
        return {
            "date": str(r["metric_date"]),
            "tss": round(float(r.get("tss") or 0), 2),
            "atl": round(float(r.get("atl") or 0), 2),
            "ctl": round(float(r.get("ctl") or 0), 2),
            "tsb": round(float(r.get("tsb") or 0), 2),
            "activities_count": int(r.get("activities_count") or 0),
        }
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        log.warning("get_load_metrics_row: %s", exc)
        return None


def get_activity_tss_map(start_date: str) -> dict[int, dict] | None:
    """TSS ya guardados por actividad desde start_date.

    Devuelve {} si la tabla aún no existe (falta ejecutar supabase/schema.sql: no hay línea base) y
    None si la lectura falla por otro motivo; el llamador no debe recalcular el histórico
    en ese caso.
    """
    sb = _supabase()
    uid = _require_active_user_id()
    if not sb:
        return None
    try:
        resp = (
            sb.table("activity_tss")
            .select("activity_id, start_date, sport, tss, method, model, native_load")
            .eq("app_user_id", uid)
            .gte("start_date", start_date)
            .execute()
        )
        return {
            int(r["activity_id"]): {
                "activity_id": int(r["activity_id"]),
                "start_date": str(r.get("start_date") or ""),
                "sport": str(r.get("sport") or "unknown"),
                "tss": float(r.get("tss") or 0.0),
                "method": str(r.get("method") or "unknown"),
                "model": str(r.get("model") or "unknown"),
                "native_load": float(r["native_load"]) if r.get("native_load") is not None else None,
            }
            for r in (resp.data or [])
            if r.get("activity_id") is not None
        }
    except Exception as exc:  # postgrest APIError no hereda de los tipos habituales
        if _is_missing_table_error(exc, "activity_tss"):
            log.info("get_activity_tss_map: tabla activity_tss no disponible (falta ejecutar supabase/schema.sql)")
            return {}
        log.warning("get_activity_tss_map: %s", exc)
        return None


def upsert_activity_tss_rows(rows: list[dict]) -> None:
    """Persiste una fila por actividad con el TSS calculado para trazabilidad."""
    sb = _supabase()
    uid = _require_active_user_id()
    if not sb or not rows:
        return
    try:
        payload_rows: list[dict[str, Any]] = []
        for row in rows:
            activity_id = row.get("activity_id")
            start_date = row.get("start_date")
            if activity_id is None or not start_date:
                continue
            payload_rows.append(
                {
                    "app_user_id": uid,
                    "activity_id": int(activity_id),
                    "sport": str(row.get("sport") or "unknown"),
                    "start_date": str(start_date),
                    "tss": round(float(row.get("tss") or 0.0), 3),
                    "tss_source": str(row.get("tss_source") or "unknown"),
                    "method": str(row.get("method") or "unknown"),
                    "model": str(row.get("model") or "unknown"),
                    "native_load": (
                        round(float(row.get("native_load")), 3)
                        if row.get("native_load") is not None
                        else None
                    ),
                    "formula_version": int(row.get("formula_version") or 0),
                    "hr_rest_used": (
                        round(float(row.get("hr_rest_used")), 3)
                        if row.get("hr_rest_used") is not None
                        else None
                    ),
                    "lthr_used": (
                        round(float(row.get("lthr_used")), 3)
                        if row.get("lthr_used") is not None
                        else None
                    ),
                    "ftp_used": (
                        round(float(row.get("ftp_used")), 3)
                        if row.get("ftp_used") is not None
                        else None
                    ),
                    "threshold_pace_used": (
                        round(float(row.get("threshold_pace_used")), 3)
                        if row.get("threshold_pace_used") is not None
                        else None
                    ),
                    "walk_threshold_used": (
                        round(float(row.get("walk_threshold_used")), 3)
                        if row.get("walk_threshold_used") is not None
                        else None
                    ),
                    "computed_at": str(
                        row.get("computed_at")
                        or datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
                    ),
                }
            )

        if not payload_rows:
            return

        for i in range(0, len(payload_rows), 200):
            sb.table("activity_tss").upsert(
                payload_rows[i:i + 200], on_conflict="app_user_id,activity_id"
            ).execute()
        log.info("upsert_activity_tss_rows: %d filas persistidas", len(payload_rows))
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        log.warning("upsert_activity_tss_rows: %s", exc)


# ─── wellness_daily (Body Battery + HRV) ─────────────────────────────────────

def _to_float_or_none(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def get_wellness_daily(metric_date: str) -> dict | None:
    """Carga snapshot diario de wellness para el usuario activo."""
    sb = _supabase()
    uid = _require_active_user_id()
    if not sb or not metric_date:
        return None
    try:
        resp = (
            sb.table("wellness_daily")
            .select(
                "metric_date,body_battery_level,bb_charged,bb_drained,bb_highest,bb_lowest,"
                "hrv_last_night_avg_ms,hrv_weekly_avg_ms,hrv_status,pulled_at,updated_at,source_payload"
            )
            .eq("app_user_id", uid)
            .eq("metric_date", metric_date)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return dict(rows[0]) if rows else None
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        log.warning("get_wellness_daily: %s", exc)
        return None


def upsert_wellness_daily(
    metric_date: str,
    *,
    body_battery: dict | None = None,
    hrv: dict | None = None,
    source_payload: dict | None = None,
) -> None:
    """Persiste snapshot wellness (Body Battery/HRV) por fecha y usuario."""
    sb = _supabase()
    uid = _require_active_user_id()
    if not sb or not metric_date:
        return

    payload: dict[str, Any] = {
        "app_user_id": uid,
        "metric_date": metric_date,
        "pulled_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    }

    if isinstance(body_battery, dict):
        payload.update(
            {
                "body_battery_level": _to_float_or_none(body_battery.get("level")),
                "bb_charged": _to_float_or_none(body_battery.get("charged")),
                "bb_drained": _to_float_or_none(body_battery.get("drained")),
                "bb_highest": _to_float_or_none(body_battery.get("highest")),
                "bb_lowest": _to_float_or_none(body_battery.get("lowest")),
            }
        )

    if isinstance(hrv, dict):
        payload.update(
            {
                "hrv_last_night_avg_ms": _to_float_or_none(hrv.get("last_night_avg_ms")),
                "hrv_weekly_avg_ms": _to_float_or_none(hrv.get("weekly_avg_ms")),
                "hrv_status": (str(hrv.get("status") or "").strip() or None),
            }
        )

    if isinstance(source_payload, dict):
        payload["source_payload"] = source_payload

    # Evitar escrituras vacías: requiere al menos una métrica además de PK/timestamp.
    metric_keys = {
        "body_battery_level",
        "bb_charged",
        "bb_drained",
        "bb_highest",
        "bb_lowest",
        "hrv_last_night_avg_ms",
        "hrv_weekly_avg_ms",
        "hrv_status",
        "source_payload",
    }
    if not any(k in payload for k in metric_keys):
        return

    try:
        sb.table("wellness_daily").upsert(payload, on_conflict="app_user_id,metric_date").execute()
    except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
        log.warning("upsert_wellness_daily: %s", exc)
