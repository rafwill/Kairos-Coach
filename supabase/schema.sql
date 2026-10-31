-- =============================================================================
-- Kairos Coach — Supabase Schema (fichero único)
-- =============================================================================
-- Ejecutar en: Supabase Dashboard -> SQL Editor -> New query -> Run.
--
-- Seguridad: las políticas RLS por usuario están en supabase/rls.sql (fase 2, ver
-- supabase/README.md). Este fichero ya no desactiva RLS: si estaba activo, sigue activo.
--
-- Crea el esquema multiusuario completo en un proyecto nuevo y pone al día uno
-- ya existente: es idempotente (create ... if not exists, add column if not
-- exists, drop/create trigger), así que se puede ejecutar las veces que haga
-- falta sin perder datos. Sustituye a las antiguas migraciones 001–004.
-- Al cambiar el esquema, editar este fichero manteniendo esa propiedad.
-- =============================================================================

begin;
set local lock_timeout = '5s';
set local statement_timeout = '120s';

-- ─── Trigger helper ─────────────────────────────────────────────────────────
create or replace function _set_updated_at()
returns trigger
language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

-- ─── app_user ───────────────────────────────────────────────────────────────
create table if not exists app_user (
    id            text        primary key,
    username      text        not null unique,
    password_hash text        not null,
    credentials   jsonb       not null default '{}',
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

-- ─── Supabase Auth: vínculo y funciones de las políticas ────────────────────
-- Cada app_user se vincula a su cuenta de Supabase Auth (auth.users). Las políticas de
-- seguridad por fila (RLS) están en supabase/rls.sql y se explican en supabase/README.md.
-- Aquí solo lo que no cambia permisos: la columna del vínculo y las dos funciones.
alter table app_user add column if not exists auth_user_id uuid unique references auth.users(id) on delete set null;
-- La contraseña la guarda Supabase Auth; el hash propio de antes queda opcional (cuentas sin migrar).
alter table app_user alter column password_hash drop not null;

-- app_user.id del usuario con sesión (o null). La usan las políticas de rls.sql.
-- security definer: se ejecuta con los permisos del dueño y puede leer app_user aunque RLS esté activo.
create or replace function kairos_app_user_id()
returns text
language sql
stable
security definer
set search_path = public
as $$
    select id from public.app_user where auth_user_id = auth.uid()
$$;

-- Vincula la cuenta de Supabase Auth con su app_user anterior (mismo email, aún sin vincular) y
-- devuelve el app_user.id; null si no hay cuenta anterior (usuario nuevo). Solo con el email
-- confirmado: así nadie puede quedarse con los datos de otro registrándose con su email.
create or replace function kairos_claim_app_user()
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
    uid       uuid := auth.uid();
    mail      text;
    confirmed timestamptz;
    linked    text;
begin
    if uid is null then
        raise exception 'kairos_claim_app_user: sin sesión';
    end if;
    select id into linked from public.app_user where auth_user_id = uid;
    if linked is not null then
        return linked;
    end if;
    select lower(email), email_confirmed_at into mail, confirmed from auth.users where id = uid;
    if confirmed is null then
        raise exception 'kairos_claim_app_user: email sin confirmar';
    end if;
    update public.app_user
       set auth_user_id = uid
     where lower(username) = mail
       and auth_user_id is null
    returning id into linked;
    return linked;
end;
$$;

revoke all on function kairos_app_user_id() from public, anon;
revoke all on function kairos_claim_app_user() from public, anon;
grant execute on function kairos_app_user_id() to authenticated;
grant execute on function kairos_claim_app_user() to authenticated;

-- ─── user_profile ───────────────────────────────────────────────────────────
create table if not exists user_profile (
    app_user_id text        primary key references app_user(id) on delete cascade,
    data        jsonb       not null default '{}',
    updated_at  timestamptz not null default now()
);

-- ─── session_context ────────────────────────────────────────────────────────
create table if not exists session_context (
    app_user_id       text        primary key references app_user(id) on delete cascade,
    history           jsonb       not null default '[]',
    session_summaries jsonb       not null default '[]',
    updated_at        timestamptz not null default now()
);

-- ─── athlete_knowledge ──────────────────────────────────────────────────────
create table if not exists athlete_knowledge (
    app_user_id text        primary key references app_user(id) on delete cascade,
    content     text        not null default '',
    updated_at  timestamptz not null default now()
);

-- ─── ia_provider (26/10) ────────────────────────────────────────────────────
-- Proveedor de IA de cada atleta: el que él diga (URL compatible con OpenAI y modelo) y su consumo diario de
-- tokens (fecha ISO → tokens, últimos 90 días). La clave NUNCA va aquí: está en el .env de cada ordenador.
-- Sustituye a gemini_usage (consumo por clave de los 5 proveedores de prueba), que se elimina.
create table if not exists ia_provider (
    app_user_id  text        primary key references app_user(id) on delete cascade,
    provider     text        not null,
    base_url     text        not null,
    model        text        not null,
    daily_tokens jsonb       not null default '{}'::jsonb,
    updated_at   timestamptz not null default now()
);
drop table if exists gemini_usage;
-- Modelo del chat (30/10): el de `model` crea los planes y escribe el arranque; este responde en el chat y releva
-- al principal si da 429 o 503. Null = el mismo.
alter table ia_provider add column if not exists chat_model text;

-- ─── training_plan (fuente de verdad de planes) ────────────────────────────
create table if not exists training_plan (
    id             text        primary key,
    app_user_id    text        not null references app_user(id) on delete cascade,
    title          text        not null,
    description    text        not null default '',
    objective      text        not null default '',
    difficulty     text        not null default 'moderate',
    duration_weeks integer     not null default 0,
    status         text        not null default 'draft' check (status in ('draft', 'active', 'inactive', 'archived')),
    source         text        not null default 'agent',
    plan_data      jsonb       not null default '{}',
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create index if not exists idx_training_plan_user_created
    on training_plan (app_user_id, created_at desc);

create unique index if not exists uq_training_plan_single_active
    on training_plan (app_user_id)
    where status = 'active';

-- ─── training_plan_session ─────────────────────────────────────────────────
create table if not exists training_plan_session (
    id           text        primary key,
    plan_id      text        not null references training_plan(id) on delete cascade,
    week_index   integer     not null default 1,
    day_index    integer     not null default 1,
    session_type text        not null default 'running',
    duration_min integer,
    intensity    text,
    exercises    jsonb       not null default '[]',
    structured_workout jsonb not null default '{}',
    notes        text        not null default '',
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

-- Proyectos creados antes de que existiera la columna (antigua migración 002).
alter table training_plan_session
    add column if not exists structured_workout jsonb not null default '{}';

create index if not exists idx_training_plan_session_plan
    on training_plan_session (plan_id, week_index, day_index);

-- ─── training_plan_version ────────────────────────────────────────────────
create table if not exists training_plan_version (
    id             text        primary key,
    plan_id        text        not null references training_plan(id) on delete cascade,
    version_number integer     not null,
    snapshot       jsonb       not null default '{}',
    change_reason  text        not null default '',
    created_at     timestamptz not null default now(),
    unique (plan_id, version_number)
);

create index if not exists idx_training_plan_version_plan
    on training_plan_version (plan_id, version_number desc);

-- ─── load_metrics_daily ──────────────────────────────────────────────────────
-- Almacena TSS/ATL/CTL/TSB calculados por día por usuario.
-- Permite cálculo incremental: solo recalcular desde el último registro.
create table if not exists load_metrics_daily (
    app_user_id      text          not null references app_user(id) on delete cascade,
    metric_date      date          not null,
    tss              numeric(10,2) not null default 0,
    atl              numeric(10,2) not null default 0,
    ctl              numeric(10,2) not null default 0,
    tsb              numeric(10,2) not null default 0,
    activities_count integer       not null default 0,
    updated_at       timestamptz   not null default now(),
    primary key (app_user_id, metric_date)
);

create index if not exists idx_load_metrics_daily_user_date
    on load_metrics_daily (app_user_id, metric_date desc);

-- ─── activity_tss ────────────────────────────────────────────────────────────
-- TSS por actividad para trazabilidad y diagnóstico por sesión.
create table if not exists activity_tss (
    app_user_id          text          not null references app_user(id) on delete cascade,
    activity_id          bigint        not null,
    sport                text          not null,
    start_date           date          not null,
    tss                  numeric(10,3) not null default 0,
    tss_source           text          not null default 'unknown',
    method               text          not null default 'unknown',
    model                text          not null default 'unknown',
    native_load          numeric(10,3),
    formula_version      integer       not null default 0,
    hr_rest_used         numeric(8,3),
    lthr_used            numeric(8,3),
    ftp_used             numeric(10,3),
    threshold_pace_used  numeric(10,3),
    walk_threshold_used  numeric(8,3),
    computed_at          timestamptz   not null default now(),
    updated_at           timestamptz   not null default now(),
    primary key (app_user_id, activity_id)
);

create index if not exists idx_activity_tss_user_date
    on activity_tss (app_user_id, start_date desc);

-- ─── wellness_daily ─────────────────────────────────────────────────────────
-- Snapshot diario de bienestar por usuario (Body Battery + HRV).
create table if not exists wellness_daily (
    app_user_id            text          not null references app_user(id) on delete cascade,
    metric_date            date          not null,
    body_battery_level     numeric(6,2),
    bb_charged             numeric(6,2),
    bb_drained             numeric(6,2),
    bb_highest             numeric(6,2),
    bb_lowest              numeric(6,2),
    hrv_last_night_avg_ms  numeric(8,2),
    hrv_weekly_avg_ms      numeric(8,2),
    hrv_status             text,
    source_payload         jsonb         not null default '{}',
    pulled_at              timestamptz   not null default now(),
    updated_at             timestamptz   not null default now(),
    primary key (app_user_id, metric_date)
);

create index if not exists idx_wellness_daily_user_date
    on wellness_daily (app_user_id, metric_date desc);

-- ─── startup_summary (31/10) ────────────────────────────────────────────────
-- Último resumen del arranque: si se reentra en menos de 1 h y nada ha cambiado (actividades, sueño de hoy, plan,
-- fórmula de TSS), Kairos lo muestra sin volver a consultar a Garmin ni al LLM. `fingerprint` guarda esos datos.
create table if not exists startup_summary (
    app_user_id  text        primary key references app_user(id) on delete cascade,
    generated_at timestamptz not null default now(),
    fingerprint  jsonb       not null default '{}',
    markdown     text        not null default '',
    updated_at   timestamptz not null default now()
);

-- ─── Triggers updated_at ────────────────────────────────────────────────────
drop trigger if exists trg_user_profile_updated_at on user_profile;
create trigger trg_user_profile_updated_at
    before update on user_profile
    for each row execute function _set_updated_at();

drop trigger if exists trg_session_context_updated_at on session_context;
create trigger trg_session_context_updated_at
    before update on session_context
    for each row execute function _set_updated_at();

drop trigger if exists trg_athlete_knowledge_updated_at on athlete_knowledge;
create trigger trg_athlete_knowledge_updated_at
    before update on athlete_knowledge
    for each row execute function _set_updated_at();

drop trigger if exists trg_app_user_updated_at on app_user;
create trigger trg_app_user_updated_at
    before update on app_user
    for each row execute function _set_updated_at();

drop trigger if exists trg_ia_provider_updated_at on ia_provider;
create trigger trg_ia_provider_updated_at
    before update on ia_provider
    for each row execute function _set_updated_at();

drop trigger if exists trg_training_plan_updated_at on training_plan;
create trigger trg_training_plan_updated_at
    before update on training_plan
    for each row execute function _set_updated_at();

drop trigger if exists trg_training_plan_session_updated_at on training_plan_session;
create trigger trg_training_plan_session_updated_at
    before update on training_plan_session
    for each row execute function _set_updated_at();

drop trigger if exists trg_load_metrics_daily_updated_at on load_metrics_daily;
create trigger trg_load_metrics_daily_updated_at
    before update on load_metrics_daily
    for each row execute function _set_updated_at();

drop trigger if exists trg_activity_tss_updated_at on activity_tss;
create trigger trg_activity_tss_updated_at
    before update on activity_tss
    for each row execute function _set_updated_at();

drop trigger if exists trg_wellness_daily_updated_at on wellness_daily;
create trigger trg_wellness_daily_updated_at
    before update on wellness_daily
    for each row execute function _set_updated_at();

drop trigger if exists trg_startup_summary_updated_at on startup_summary;
create trigger trg_startup_summary_updated_at
    before update on startup_summary
    for each row execute function _set_updated_at();

commit;
