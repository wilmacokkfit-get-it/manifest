-- Additive MANIFEST Studio storage. Apply to the SAME configured Supabase
-- project. No venture, signal, owner, path, or render record is modified.
-- History and current cursor live in one JSONB row for atomic revision updates.
begin;
create table if not exists public.app_projects (
  id uuid primary key,
  owner_token text not null,
  idea text not null,
  venture_slug text,
  payload jsonb not null,
  revision bigint not null default 0 check (revision >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint app_projects_idea_limit check (char_length(idea) between 8 and 6000),
  constraint app_projects_history_limit check (
    jsonb_typeof(payload->'versions') = 'array'
    and jsonb_array_length(payload->'versions') <= 5
  )
);
create index if not exists app_projects_owner_updated
  on public.app_projects (owner_token, updated_at desc);
alter table public.app_projects enable row level security;
revoke all on public.app_projects from public, anon, authenticated;
grant select, insert, update on public.app_projects to service_role;
comment on table public.app_projects is
  'Private app source and history; server-side creator authorization only. Never returned by Floor or public venture APIs.';
commit;
