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
revoke all on public.manifest_requests from public, anon, authenticated;
grant select, insert, update on public.manifest_requests to service_role;
commit;
notify pgrst, 'reload schema';
