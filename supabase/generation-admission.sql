-- Additive migration for existing MANIFEST Supabase installations.
-- Run AFTER migration.sql in the SQL editor. No existing rows are changed.
begin;

create table if not exists public.generation_admissions (
  token text primary key,
  caller text not null,
  global_slot boolean not null,
  admitted_at timestamptz not null,
  expires_at timestamptz not null,
  consumed boolean not null default false,
  venture_id uuid
);
create index if not exists idx_admissions_caller
  on public.generation_admissions(caller, admitted_at);
alter table public.generation_admissions enable row level security;
revoke all on public.generation_admissions from public, anon, authenticated;
grant select, insert, update, delete on public.generation_admissions to service_role;

-- All quota decisions and commits share one transaction-scoped lock.
-- These are invoker functions: only service_role has table/function access.
create or replace function public.reserve_generation(
  p_token text, p_caller text, p_hour_cap integer, p_day_cap integer, p_global_cap integer
) returns text language plpgsql set search_path = public, pg_temp as $$
declare
  t timestamptz;
  hourly bigint;
  daily bigint;
  births bigint;
  pending bigint;
begin
  perform pg_advisory_xact_lock(72646101);
  t := clock_timestamp();
  delete from generation_admissions
    where (not consumed and expires_at <= t)
       or (consumed and admitted_at <= t - interval '24 hours');
  select count(*) filter (where admitted_at > t - interval '1 hour'), count(*)
    into hourly, daily from generation_admissions where caller = p_caller;
  if hourly >= p_hour_cap then return 'hour'; end if;
  if daily >= p_day_cap then return 'day'; end if;
  if p_global_cap is not null then
    select count(*) into births from ventures
      where created_at >= date_trunc('day', t at time zone 'UTC') at time zone 'UTC'
      and not exists (select 1 from generation_admissions a
        where a.venture_id = ventures.id and a.consumed and a.global_slot);
    -- Include all active global work, even work admitted just before midnight.
    select count(*) into pending from generation_admissions
      where global_slot and (not consumed
        or admitted_at >= date_trunc('day', t at time zone 'UTC') at time zone 'UTC');
    if births + pending >= p_global_cap then return 'global'; end if;
  end if;
  insert into generation_admissions(token, caller, global_slot, admitted_at, expires_at)
    values (p_token, p_caller, p_global_cap is not null, t, t + interval '5 minutes');
  return null;
end $$;

create or replace function public.release_generation(p_token text)
returns void language plpgsql set search_path = public, pg_temp as $$
begin
  perform pg_advisory_xact_lock(72646101);
  delete from generation_admissions where token = p_token and not consumed;
end $$;

create or replace function public.consume_refinement_admission(p_token text)
returns void language plpgsql set search_path = public, pg_temp as $$
begin
  perform pg_advisory_xact_lock(72646101);
  update generation_admissions set consumed = true, admitted_at = clock_timestamp()
    where token = p_token and not global_slot and not consumed
      and expires_at > clock_timestamp();
  if not found then raise exception 'Refinement admission expired.'; end if;
end $$;

create or replace function public.create_reserved_venture(p_token text, p_row jsonb)
returns jsonb language plpgsql set search_path = public, pg_temp as $$
declare
  admission generation_admissions;
  v ventures;
begin
  perform pg_advisory_xact_lock(72646101);
  select * into admission from generation_admissions where token = p_token;
  if found and admission.consumed and admission.venture_id is not null then
    select * into v from ventures where id = admission.venture_id;
    return to_jsonb(v);
  end if;
  if admission.token is null or admission.consumed or not admission.global_slot
      or admission.expires_at <= clock_timestamp() then
    raise exception 'Generation admission expired.';
  end if;
  -- Populate only a ventures record, not arbitrary SQL supplied by the client.
  v := jsonb_populate_record(null::ventures, jsonb_build_object(
    'id', gen_random_uuid(), 'created_at', clock_timestamp(), 'updated_at', clock_timestamp(),
    'signal_count', 0, 'buy_count', 0, 'build_count', 0, 'fund_count', 0,
    'tiers', '[]'::jsonb, 'roadmap', '[]'::jsonb, 'grounding', '{}'::jsonb,
    'image_prompts', '[]'::jsonb, 'images', '[]'::jsonb,
    'status', 'ready', 'images_status', 'failed', 'visibility', 'private'
  ) || p_row);
  insert into ventures select v.*;
  update generation_admissions
    set consumed = true, admitted_at = clock_timestamp(), venture_id = v.id
    where token = p_token;
  return to_jsonb(v);
end $$;

-- PostgreSQL grants function execution to PUBLIC by default. Remove it.
revoke all on function public.reserve_generation(text,text,integer,integer,integer)
  from public, anon, authenticated;
revoke all on function public.release_generation(text) from public, anon, authenticated;
revoke all on function public.consume_refinement_admission(text) from public, anon, authenticated;
revoke all on function public.create_reserved_venture(text,jsonb) from public, anon, authenticated;
grant execute on function public.reserve_generation(text,text,integer,integer,integer) to service_role;
grant execute on function public.release_generation(text) to service_role;
grant execute on function public.consume_refinement_admission(text) to service_role;
grant execute on function public.create_reserved_venture(text,jsonb) to service_role;
commit;
notify pgrst, 'reload schema';p out