-- 002_add_structured_workout_to_training_plan_session.sql
-- Añade el payload estructurado por sesión para interoperabilidad de workouts.

begin;
set local lock_timeout = '5s';
set local statement_timeout = '120s';

alter table if exists training_plan_session
    add column if not exists structured_workout jsonb not null default '{}';

commit;
