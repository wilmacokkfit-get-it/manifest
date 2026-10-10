-- Additive migration, after migration.sql and generation-admission.sql.
-- Request identities are permanent, including failures: never reuse a request
-- ID for a second attempt. Completion is the atomic venture insert at venture_id.
begin;
create table if not exists public.manifest_requests (
  owner_token text not null,
  request_id text not null,
  fingerprint text not null,
  venture_id uuid not null unique,
  lease_until timestamptz not null,
  failed boolean not null default false,
  primary key (owner_token, request_id)
);
alter table public.manifest_requests enable row level security;
alter table public.manifest_requests
  add column if not exists retired boolean not null default false;
revoke all on public.manifest_requests from public, anon, authenticated;
grant select, insert, update on public.manifest_requests to service_role;

-- The same transaction lock as create_reserved_venture. A trigger also covers
-- unlimited-owner inserts (which do not have a quota admission). A boolean
-- tombstone, not lease/timestamp comparisons, is the final authority to save.
create or replace function public.fence_retired_manifest()
returns trigger language plpgsql set search_path = public, pg_temp as $$
begin
  perform pg_advisory_xact_lock(72646101);
  if exists (select 1 from manifest_requests
             where venture_id = new.id and retired) then
    raise exception 'Manifest request retired.';
  end if;
  return new;
end $$;
drop trigger if exists fence_retired_manifest on public.ventures;
create trigger fence_retired_manifest before insert on public.ventures
  for each row execute function public.fence_retired_manifest();

create or replace function public.retire_manifest_request(p_owner text, p_request_id text)
returns jsonb language plpgsql set search_path = public, pg_temp as $$
declare
  r manifest_requests;
begin
  perform pg_advisory_xact_lock(72646101);
  select * into r from manifest_requests
    where owner_token = p_owner and request_id = p_request_id for update;
  if not found then return null; end if;
  -- Saved results always win, including a lost commit response. Do not touch
  -- admissions: in particular, completed charges are never refunded.
  if not exists (select 1 from ventures where id = r.venture_id)
      and r.lease_until <= clock_timestamp() then
    update manifest_requests set retired = true
      where owner_token = p_owner and request_id = p_request_id returning * into r;
  end if;
  return to_jsonb(r);
end $$;
revoke all on function public.fence_retired_manifest() from public, anon, authenticated;
revoke all on function public.retire_manifest_request(text,text) from public, anon, authenticated;
grant execute on function public.fence_retired_manifest() to service_role;
grant execute on function public.retire_manifest_request(text,text) to service_role;
commit;
notify pgrst, 'reload schema';
