-- ============================================================================
-- The Commissioner's Desk — full schema bootstrap
--
-- Run this ONCE in a fresh Supabase project (SQL Editor → New query → paste →
-- Run). It creates every table, index, row-level security policy, and the
-- storage bucket the app needs. Safe to re-run; everything is IF NOT EXISTS.
--
-- If you run this, you do NOT need migrations/001_add_provider.sql — the
-- provider columns are already here. That file is only for upgrading an
-- existing pre-provider database.
--
-- Rendered newspaper HTML goes in the `newspapers` STORAGE BUCKET, not in a
-- Postgres column. Storage is a separate 1GB allowance on the free tier, so
-- editions never eat into the 500MB database limit — which is what filled the
-- last project.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- leagues
-- ---------------------------------------------------------------------------

create table if not exists public.leagues (
    id                  uuid primary key default gen_random_uuid(),
    user_id             uuid not null references auth.users (id) on delete cascade,

    -- Which platform, and that platform's own league ID.
    provider            text not null default 'sleeper',
    platform_league_id  text not null,

    league_name         text not null,
    paper_name          text,
    commissioner_name   text,
    season              integer not null,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    constraint leagues_provider_check
        check (provider in ('sleeper', 'espn', 'yahoo'))
);

-- A user shouldn't be able to add the same league twice.
create unique index if not exists leagues_user_provider_league_idx
    on public.leagues (user_id, provider, platform_league_id, season);

create index if not exists leagues_user_idx
    on public.leagues (user_id);

-- ---------------------------------------------------------------------------
-- inside_jokes
--
-- League lore, injected into the writer prompt on every generation. This is the
-- thing that makes one league's paper different from another's, so it's worth
-- keeping soft-deleted (active = false) rather than actually removing rows —
-- a joke that stops being funny in Week 4 may be the callback in Week 12.
-- ---------------------------------------------------------------------------

create table if not exists public.inside_jokes (
    id          uuid primary key default gen_random_uuid(),
    league_id   uuid not null references public.leagues (id) on delete cascade,
    joke        text not null,
    active      boolean not null default true,
    created_at  timestamptz not null default now()
);

create index if not exists inside_jokes_league_active_idx
    on public.inside_jokes (league_id, active);

-- ---------------------------------------------------------------------------
-- newspapers
--
-- One row per league per week. `storage_path` points at the rendered HTML in
-- the bucket; `ai_cache` holds Claude's output so an edition can be re-rendered
-- or re-examined without paying for generation twice.
--
-- ai_cache is jsonb, always. The old code inserted json.dumps(...) but updated
-- with a raw dict, leaving two different types in the same column.
-- ---------------------------------------------------------------------------

create table if not exists public.newspapers (
    id            uuid primary key default gen_random_uuid(),
    league_id     uuid not null references public.leagues (id) on delete cascade,
    week          integer not null,
    season        integer not null,

    storage_path  text,          -- e.g. "<user_id>/<league_id>/2025/week-01.html"
    public_url    text,          -- convenience: the CDN URL for sharing
    ai_cache      jsonb,

    generated_at  timestamptz not null default now(),

    constraint newspapers_week_check check (week between 1 and 22)
);

-- Regenerating a week replaces it rather than piling up duplicates.
create unique index if not exists newspapers_league_week_season_idx
    on public.newspapers (league_id, season, week);

create index if not exists newspapers_league_idx
    on public.newspapers (league_id, season desc, week desc);

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists leagues_touch_updated_at on public.leagues;
create trigger leagues_touch_updated_at
    before update on public.leagues
    for each row execute function public.touch_updated_at();

-- ============================================================================
-- Row Level Security
--
-- Without these, the anon key can read every league in the database. With them,
-- a user can only ever see rows that trace back to their own user_id.
-- ============================================================================

alter table public.leagues      enable row level security;
alter table public.inside_jokes enable row level security;
alter table public.newspapers   enable row level security;

-- --- leagues: you own it or you can't see it -------------------------------

drop policy if exists "leagues are visible to their owner" on public.leagues;
create policy "leagues are visible to their owner"
    on public.leagues for select
    using (auth.uid() = user_id);

drop policy if exists "users insert their own leagues" on public.leagues;
create policy "users insert their own leagues"
    on public.leagues for insert
    with check (auth.uid() = user_id);

drop policy if exists "users update their own leagues" on public.leagues;
create policy "users update their own leagues"
    on public.leagues for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists "users delete their own leagues" on public.leagues;
create policy "users delete their own leagues"
    on public.leagues for delete
    using (auth.uid() = user_id);

-- --- inside_jokes: scoped through the parent league -------------------------

drop policy if exists "jokes follow league ownership" on public.inside_jokes;
create policy "jokes follow league ownership"
    on public.inside_jokes for all
    using (
        exists (
            select 1 from public.leagues l
             where l.id = inside_jokes.league_id
               and l.user_id = auth.uid()
        )
    )
    with check (
        exists (
            select 1 from public.leagues l
             where l.id = inside_jokes.league_id
               and l.user_id = auth.uid()
        )
    );

-- --- newspapers: scoped through the parent league ---------------------------

drop policy if exists "newspapers follow league ownership" on public.newspapers;
create policy "newspapers follow league ownership"
    on public.newspapers for all
    using (
        exists (
            select 1 from public.leagues l
             where l.id = newspapers.league_id
               and l.user_id = auth.uid()
        )
    )
    with check (
        exists (
            select 1 from public.leagues l
             where l.id = newspapers.league_id
               and l.user_id = auth.uid()
        )
    );

-- ============================================================================
-- Storage: the `newspapers` bucket
--
-- Public read, because the entire point is handing a league a link they can
-- open without an account. Writes are restricted to the owning user's folder.
--
-- Path convention: <user_id>/<league_id>/<season>/week-NN.html
-- The first path segment being the user's UID is what makes the write policy
-- below enforceable.
-- ============================================================================

insert into storage.buckets (id, name, public)
values ('newspapers', 'newspapers', true)
on conflict (id) do update set public = true;

drop policy if exists "newspapers are publicly readable" on storage.objects;
create policy "newspapers are publicly readable"
    on storage.objects for select
    using (bucket_id = 'newspapers');

drop policy if exists "users write to their own newspaper folder" on storage.objects;
create policy "users write to their own newspaper folder"
    on storage.objects for insert
    to authenticated
    with check (
        bucket_id = 'newspapers'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "users update their own newspapers" on storage.objects;
create policy "users update their own newspapers"
    on storage.objects for update
    to authenticated
    using (
        bucket_id = 'newspapers'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "users delete their own newspapers" on storage.objects;
create policy "users delete their own newspapers"
    on storage.objects for delete
    to authenticated
    using (
        bucket_id = 'newspapers'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

-- ============================================================================
-- Done. Next steps:
--   1. Project Settings → API: copy the Project URL and the anon public key
--      into your .env as SUPABASE_URL and SUPABASE_KEY.
--   2. Authentication → URL Configuration: add your app's URL to the redirect
--      allowlist (http://localhost:8501 for local, plus your deployed URL).
--   3. Authentication → Providers: enable Google if you want the OAuth button.
-- ============================================================================
