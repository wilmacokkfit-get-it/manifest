-- MANIFEST v2 — Supabase schema
-- Run once in the Supabase SQL editor (or via `supabase db push`).
-- The server talks to these tables with the SERVICE ROLE key only; RLS is on and
-- there are deliberately no anon policies, so nothing is readable from the browser.

create extension if not exists pgcrypto;

create table if not exists public.ventures (
  id               uuid primary key default gen_random_uuid(),
  slug             text unique not null,
  owner_token      text not null,
  idea_raw         text not null,
  idea_hash        text not null,
  provenance_code  text not null,
  name             text,
  tagline          text,
  description      text,
  category         text,
  accent           text,
  tiers            jsonb default '[]'::jsonb,
  roadmap          jsonb default '[]'::jsonb,
  grounding        jsonb default '{}'::jsonb,
  image_prompts    jsonb default '[]'::jsonb,
  images           jsonb default '[]'::jsonb,
  images_status    text default 'pending',
  visibility       text default 'private',
  status           text default 'generating',
  signal_count     integer default 0,
  buy_count        integer default 0,
  build_count      integer default 0,
  fund_count       integer default 0,
  promoted_at      timestamptz,
  released_at      timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create index if not exists idx_ventures_public
  on public.ventures (visibility, promoted_at, signal_count desc);
create index if not exists idx_ventures_created on public.ventures (created_at);

create table if not exists public.signals (
  id          uuid primary key default gen_random_uuid(),
  venture_id  uuid not null references public.ventures(id) on delete cascade,
  type        text not null check (type in ('buy','build','fund')),
  email       text not null,
  price       numeric,
  created_at  timestamptz not null default now(),
  unique (venture_id, type, email)
);

create table if not exists public.waitlist (
  id          uuid primary key default gen_random_uuid(),
  name        text,
  email       text unique not null,
  role        text,
  created_at  timestamptz not null default now()
);

-- Lock everything down. The service role bypasses RLS; nobody else gets in.
alter table public.ventures enable row level security;
alter table public.signals  enable row level security;
alter table public.waitlist enable row level security;

-- All renders use owner-checked app delivery, including released ventures.
-- Update only bucket visibility: existing objects and venture records stay intact.
-- A private bucket does not override permissive SELECT policies on
-- storage.objects. Do not grant anon/authenticated browser reads to renders.
-- Audit existing project policies separately; do not delete shared policies here.
-- See docs/supabase-image-privacy.md for a disposable live check and CDN caveats.
insert into storage.buckets (id, name, public)
values ('renders', 'renders', false)
on conflict (id) do update set public = false;


-- v2: the Path (chosen tier + step-by-step guide), asks (needs) and offers
alter table public.ventures add column if not exists path jsonb;

create table if not exists public.needs (
  id uuid primary key default gen_random_uuid(),
  venture_id uuid not null references public.ventures(id) on delete cascade,
  step_n integer,
  title text not null,
  detail text,
  status text not null default 'open',
  offer_count integer not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists needs_venture_idx on public.needs(venture_id);
create index if not exists needs_status_idx on public.needs(status);

create table if not exists public.offers (
  id uuid primary key default gen_random_uuid(),
  need_id uuid not null references public.needs(id) on delete cascade,
  email text not null,
  name text,
  message text,
  created_at timestamptz not null default now(),
  unique(need_id, email)
);

alter table public.needs enable row level security;
alter table public.offers enable row level security;

-- Bypassing RLS does not grant SQL table privileges. Grant only the backend
-- role access; no browser/anonymous roles are granted access here.
grant usage on schema public to service_role;
grant select, insert, update, delete
  on public.ventures, public.signals, public.waitlist, public.needs, public.offers
  to service_role;
