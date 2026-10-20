-- =============================================================================
-- Migración 004: tabla activity_tss
-- Ejecutar en Supabase Dashboard → SQL Editor → New query → Run
-- =============================================================================

begin;

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
    computed_at          timestamptz   not null default now(),
    updated_at           timestamptz   not null default now(),
    primary key (app_user_id, activity_id)
);

alter table activity_tss disable row level security;

create index if not exists idx_activity_tss_user_date
    on activity_tss (app_user_id, start_date desc);

drop trigger if exists trg_activity_tss_updated_at on activity_tss;
create trigger trg_activity_tss_updated_at
    before update on activity_tss
    for each row execute function _set_updated_at();

commit;