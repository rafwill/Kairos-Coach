-- =============================================================================
-- Kairos Coach — Seguridad por fila (RLS), fase 2
-- =============================================================================
-- Ejecutar en: Supabase Dashboard -> SQL Editor -> New query -> Run, DESPUÉS de:
--   1) ejecutar supabase/schema.sql (crea app_user.auth_user_id y las funciones), y
--   2) que cada usuario y cada ordenador haya entrado una vez con la versión de Kairos con
--      Supabase Auth (así su cuenta queda vinculada). Ver supabase/README.md.
--
-- Qué hace: activa RLS en las 12 tablas y crea las políticas. A partir de aquí la clave
-- "anon" sin sesión no ve ni modifica nada, y cada usuario con sesión solo ve sus filas.
-- Idempotente: se puede ejecutar varias veces. Marcha atrás: supabase/README.md, "Volver atrás".
-- =============================================================================

begin;
set local lock_timeout = '5s';

-- ─── Activar RLS ────────────────────────────────────────────────────────────
alter table app_user              enable row level security;
alter table user_profile          enable row level security;
alter table session_context       enable row level security;
alter table athlete_knowledge     enable row level security;
alter table ia_provider           enable row level security;
alter table training_plan         enable row level security;
alter table training_plan_session enable row level security;
alter table training_plan_version enable row level security;
alter table load_metrics_daily    enable row level security;
alter table activity_tss          enable row level security;
alter table wellness_daily        enable row level security;
alter table startup_summary       enable row level security;

-- ─── app_user: tu propia fila ───────────────────────────────────────────────
-- Leer y modificar solo la fila vinculada a tu cuenta. Crear solo la tuya, con tu propio
-- email como usuario (nadie puede reservar el email de otro). Sin política de borrado.
drop policy if exists kairos_app_user_select on app_user;
create policy kairos_app_user_select on app_user
    for select to authenticated
    using (auth_user_id = (select auth.uid()));

drop policy if exists kairos_app_user_update on app_user;
create policy kairos_app_user_update on app_user
    for update to authenticated
    using (auth_user_id = (select auth.uid()))
    with check (auth_user_id = (select auth.uid()));

drop policy if exists kairos_app_user_insert on app_user;
create policy kairos_app_user_insert on app_user
    for insert to authenticated
    with check (
        auth_user_id = (select auth.uid())
        and lower(username) = lower((select auth.jwt()) ->> 'email')
    );

-- ─── Tablas con app_user_id: solo las filas de tu app_user ──────────────────
-- "for all" = leer, crear, modificar y borrar. using filtra lo que ves; with check impide
-- escribir filas a nombre de otro usuario.
drop policy if exists kairos_own_rows on user_profile;
create policy kairos_own_rows on user_profile for all to authenticated
    using (app_user_id = (select kairos_app_user_id()))
    with check (app_user_id = (select kairos_app_user_id()));

drop policy if exists kairos_own_rows on session_context;
create policy kairos_own_rows on session_context for all to authenticated
    using (app_user_id = (select kairos_app_user_id()))
    with check (app_user_id = (select kairos_app_user_id()));

drop policy if exists kairos_own_rows on athlete_knowledge;
create policy kairos_own_rows on athlete_knowledge for all to authenticated
    using (app_user_id = (select kairos_app_user_id()))
    with check (app_user_id = (select kairos_app_user_id()));

drop policy if exists kairos_own_rows on ia_provider;
create policy kairos_own_rows on ia_provider for all to authenticated
    using (app_user_id = (select kairos_app_user_id()))
    with check (app_user_id = (select kairos_app_user_id()));

drop policy if exists kairos_own_rows on training_plan;
create policy kairos_own_rows on training_plan for all to authenticated
    using (app_user_id = (select kairos_app_user_id()))
    with check (app_user_id = (select kairos_app_user_id()));

drop policy if exists kairos_own_rows on load_metrics_daily;
create policy kairos_own_rows on load_metrics_daily for all to authenticated
    using (app_user_id = (select kairos_app_user_id()))
    with check (app_user_id = (select kairos_app_user_id()));

drop policy if exists kairos_own_rows on activity_tss;
create policy kairos_own_rows on activity_tss for all to authenticated
    using (app_user_id = (select kairos_app_user_id()))
    with check (app_user_id = (select kairos_app_user_id()));

drop policy if exists kairos_own_rows on wellness_daily;
create policy kairos_own_rows on wellness_daily for all to authenticated
    using (app_user_id = (select kairos_app_user_id()))
    with check (app_user_id = (select kairos_app_user_id()));

drop policy if exists kairos_own_rows on startup_summary;
create policy kairos_own_rows on startup_summary for all to authenticated
    using (app_user_id = (select kairos_app_user_id()))
    with check (app_user_id = (select kairos_app_user_id()));

-- ─── Tablas que cuelgan de un plan: las de tus planes ───────────────────────
drop policy if exists kairos_own_rows on training_plan_session;
create policy kairos_own_rows on training_plan_session for all to authenticated
    using (exists (
        select 1 from training_plan p
        where p.id = training_plan_session.plan_id and p.app_user_id = (select kairos_app_user_id())
    ))
    with check (exists (
        select 1 from training_plan p
        where p.id = training_plan_session.plan_id and p.app_user_id = (select kairos_app_user_id())
    ));

drop policy if exists kairos_own_rows on training_plan_version;
create policy kairos_own_rows on training_plan_version for all to authenticated
    using (exists (
        select 1 from training_plan p
        where p.id = training_plan_version.plan_id and p.app_user_id = (select kairos_app_user_id())
    ))
    with check (exists (
        select 1 from training_plan p
        where p.id = training_plan_version.plan_id and p.app_user_id = (select kairos_app_user_id())
    ));

commit;
