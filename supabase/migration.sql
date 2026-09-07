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

-- Public bucket for product renders (URLs are shown on public venture pages).
insert into storage.buckets (id, name, public)
values ('renders', 'renders', true)
on conflict (id) do nothing;
