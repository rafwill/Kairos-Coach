begin;
set local lock_timeout = '5s';
set local statement_timeout = '120s';

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

alter table wellness_daily disable row level security;

create index if not exists idx_wellness_daily_user_date
    on wellness_daily (app_user_id, metric_date desc);

create or replace function _set_updated_at()
returns trigger
language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_wellness_daily_updated_at on wellness_daily;
create trigger trg_wellness_daily_updated_at
    before update on wellness_daily
    for each row execute function _set_updated_at();

commit;
