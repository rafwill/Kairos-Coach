"""Comprueba la seguridad por fila (RLS) de Supabase: qué ve la clave anon sin sesión y qué ve tu sesión.

Uso:
    python tools/check_rls.py [--username U]     (o make check-rls)

- Sin sesión (solo la clave anon del .env): cuenta las filas visibles de cada tabla. Con supabase/rls.sql
  aplicado deben ser 0 en todas; si alguna tabla deja ver filas, está ABIERTA y el script sale con 1.
- Con tu sesión (la que guarda Kairos al entrar; usuario de --username o KAIROS_USERNAME): tu fila de
  app_user debe ser exactamente una y se cuentan tus filas en el resto.
- Solo lee. Explicación de las políticas y de las fases: supabase/README.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", encoding="utf-8")

TABLES = (
    "app_user", "user_profile", "session_context", "athlete_knowledge", "gemini_usage",
    "training_plan", "training_plan_session", "training_plan_version",
    "load_metrics_daily", "activity_tss", "wellness_daily",
)


def _count(client, table: str) -> int | str:
    try:
        res = client.table(table).select("*", count="exact").limit(1).execute()
        return int(res.count or 0)
    except Exception as exc:  # noqa: BLE001 — se informa por tabla
        return f"error: {str(getattr(exc, 'message', '') or exc)[:60]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--username", default=None)
    args = ap.parse_args()

    from supabase import create_client

    from agent import storage

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not key:
        print("Faltan SUPABASE_URL o SUPABASE_ANON_KEY en .env")
        return 2
    if storage.is_zscaler_network():
        import truststore

        truststore.inject_into_ssl()

    print("1) Sin sesión (clave anon a secas) — con RLS activo debe verse 0 en todas")
    anon = create_client(url, key)
    open_tables = []
    for table in TABLES:
        n = _count(anon, table)
        status = "cerrada" if n == 0 else ("ABIERTA" if isinstance(n, int) else "?")
        if status != "cerrada":
            open_tables.append(table)
        print(f"   {table:24s} {str(n):>8}  {status}")

    username = args.username or os.environ.get("KAIROS_USERNAME", "")
    print(f"\n2) Con tu sesión ({username or 'sin usuario'})")
    own_ok = None
    if username and storage.restore_session(username):
        sb = storage._require_supabase()
        for table in TABLES:
            print(f"   {table:24s} {str(_count(sb, table)):>8}")
        linked = sb.rpc("kairos_app_user_id").execute().data
        print(f"   tu cuenta vinculada a un app_user: {'sí' if linked else 'NO'}")
        if open_tables:
            # Fase 1 (RLS sin activar): la sesión aún ve lo mismo que cualquiera; no se juzga.
            own_ok = bool(linked)
            print("   (fase 1: sin políticas activas, tu sesión todavía ve todas las filas)")
        else:
            own_ok = bool(linked) and _count(sb, "app_user") == 1
            print(f"   tu fila de app_user: {'1, correcto' if own_ok else 'NO es exactamente 1'}")
    else:
        print("   Sin sesión guardada en este ordenador: entra una vez en Kairos (python -m agent.main).")

    print()
    if open_tables:
        print(f"ABIERTAS sin sesión: {', '.join(open_tables)}. Si ya ejecutaste supabase/rls.sql, revísalo "
              "(supabase/README.md); si no, es lo esperado en la fase 1.")
        return 1
    print("Todas las tablas cerradas sin sesión.")
    return 0 if own_ok in (None, True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
