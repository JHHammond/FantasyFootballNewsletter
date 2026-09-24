-- ============================================================================
-- The Commissioner's Desk — accountless schema
--
-- Run this in a fresh Supabase project INSTEAD OF 000_bootstrap.sql.
-- If you already ran 000, this migrates you forward. Safe either way, and
-- safe to re-run.
--
-- ORDER MATTERS HERE. Postgres refuses to drop a column while a row-level
-- security policy references it, so every old policy has to go before
-- `user_id` can. Tables are created first (no-ops if they already exist) so
-- the policy drops have something to attach to on a brand-new database.
--
-- WHAT CHANGED AND WHY
--
-- There are no user accounts. Monetization is ads inside the paper, so the
-- number that matters is how many people open one — and a signup wall stands
-- in front of the single person who has to create it while doing nothing for
-- the ten who read it.
--
-- That changes the security model. With no logged-in user there is no
-- auth.uid() for row-level security to check, so RLS can't be the thing
-- protecting these rows. Instead:
--
--   * RLS is ENABLED with NO POLICIES. That denies everything by default, so
--     a leaked anon key grants access to precisely nothing.
--   * The FastAPI server holds the SERVICE ROLE key, bypasses RLS, and is the
--     only thing that ever touches the database.
--   * Authorization is by unguessable token: knowing a league's admin_token is
--     what proves you may edit it.
--
-- The service role key must never be sent to a browser.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- STEP 1 — Make sure the tables exist.
--
-- On a fresh project these create everything. On a project that ran 000 these
-- are no-ops, and the columns get reconciled in step 3.
-- ---------------------------------------------------------------------------

create table if not exists public.leagues (
    id                  uuid primary key default gen_random_uuid(),
    provider            text not null default 'sleeper',
    platform_league_id  text not null,
    league_name         text not null,
    paper_name          text,
    commissioner_name   text,
    season              integer not null,
    public_slug         text,
    admin_token         text,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create table if not exists public.inside_jokes (
    id          uuid primary key default gen_random_uuid(),
    league_id   uuid not null references public.leagues (id) on delete cascade,
    joke        text not null,
    active      boolean not null default true,
    created_at  timestamptz not null default now()
);

create table if not exists public.newspapers (
    id            uuid primary key default gen_random_uuid(),
    league_id     uuid not null references public.leagues (id) on delete cascade,
    week          integer not null,
    season        integer not null,
    storage_path  text,
    public_url    text,
    ai_cache      jsonb,
    generated_at  timestamptz not null default now()
);


-- ---------------------------------------------------------------------------
-- STEP 2 — Drop every policy from the account-based schema.
--
-- These all reference leagues.user_id, directly or through a subquery, and
-- Postgres will refuse to drop that column while any of them exist. This is
-- the step that has to come before the column drops.
-- ---------------------------------------------------------------------------

drop policy if exists "leagues are visible to their owner" on public.leagues;
drop policy if exists "users insert their own leagues"     on public.leagues;
drop policy if exists "users update their own leagues"     on public.leagues;
drop policy if exists "users delete their own leagues"     on public.leagues;
drop policy if exists "jokes follow league ownership"      on public.inside_jokes;
drop policy if exists "newspapers follow league ownership" on public.newspapers;

-- Catch-all: if you added policies of your own, this removes anything still
-- attached to these three tables so the column drops below can't fail.
do $$
declare
    pol record;
begin
    for pol in
        select schemaname, tablename, policyname
          from pg_policies
         where schemaname = 'public'
           and tablename in ('leagues', 'inside_jokes', 'newspapers', 'lore')
    loop
        execute format('drop policy if exists %I on %I.%I',
                       pol.policyname, pol.schemaname, pol.tablename);
    end loop;
end $$;


-- ---------------------------------------------------------------------------
-- STEP 3 — Reconcile columns.
-- ---------------------------------------------------------------------------

alter table public.leagues add column if not exists provider text not null default 'sleeper';
alter table public.leagues add column if not exists platform_league_id text;
alter table public.leagues add column if not exists public_slug text;
alter table public.leagues add column if not exists admin_token text;
alter table public.leagues add column if not exists updated_at timestamptz not null default now();

-- Carry the old Sleeper ID over before the column goes.
do $$
begin
    if exists (select 1 from information_schema.columns
                where table_schema = 'public' and table_name = 'leagues'
                  and column_name = 'sleeper_league_id')
    then
        update public.leagues
           set platform_league_id = coalesce(platform_league_id, sleeper_league_id);
    end if;
end $$;

-- Now safe: the policies that depended on user_id are gone.
alter table public.leagues drop column if exists user_id;
alter table public.leagues drop column if exists sleeper_league_id;

-- HTML moved to the storage bucket; ~60KB an edition is what filled the
-- previous project's 500MB database.
alter table public.newspapers drop column if exists html_content;
alter table public.newspapers add column if not exists storage_path text;
alter table public.newspapers add column if not exists public_url text;


-- ---------------------------------------------------------------------------
-- STEP 4 — Backfill the new identifiers, then enforce them.
-- ---------------------------------------------------------------------------

update public.leagues
   set public_slug = coalesce(
           public_slug,
           regexp_replace(lower(league_name), '[^a-z0-9]+', '-', 'g')
           || '-' || substr(md5(random()::text), 1, 4)
       ),
       admin_token = coalesce(admin_token, replace(gen_random_uuid()::text, '-', ''))
 where public_slug is null or admin_token is null;

alter table public.leagues alter column public_slug set not null;
alter table public.leagues alter column admin_token set not null;
alter table public.leagues alter column platform_league_id set not null;

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'leagues_provider_check') then
        alter table public.leagues add constraint leagues_provider_check
            check (provider in ('sleeper', 'espn', 'yahoo'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'newspapers_week_check') then
        alter table public.newspapers add constraint newspapers_week_check
            check (week between 1 and 22);
    end if;
end $$;


-- ---------------------------------------------------------------------------
-- STEP 5 — Indexes.
-- ---------------------------------------------------------------------------

create unique index if not exists leagues_public_slug_idx on public.leagues (public_slug);
create unique index if not exists leagues_admin_token_idx on public.leagues (admin_token);

-- One paper per league per season. Also what lets the app notice that a league
-- is already set up instead of creating a duplicate.
create unique index if not exists leagues_provider_league_season_idx
    on public.leagues (provider, platform_league_id, season);

-- Drop the account-era unique index if it's still around.
drop index if exists public.leagues_user_provider_league_idx;
drop index if exists public.leagues_user_idx;

create index if not exists inside_jokes_league_active_idx
    on public.inside_jokes (league_id, active);

create unique index if not exists newspapers_league_week_season_idx
    on public.newspapers (league_id, season, week);

create index if not exists newspapers_league_idx
    on public.newspapers (league_id, season desc, week desc);


-- ---------------------------------------------------------------------------
-- STEP 6 — updated_at
-- ---------------------------------------------------------------------------

create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists leagues_touch_updated_at on public.leagues;
create trigger leagues_touch_updated_at
    before update on public.leagues
    for each row execute function public.touch_updated_at();


-- ---------------------------------------------------------------------------
-- STEP 7 — RLS on, no policies. Deny by default.
--
-- The server bypasses this with the service role key. The point is that
-- nothing else can read these tables: if the anon key ends up in a browser, a
-- crawler, or a screenshot, it opens nothing.
-- ---------------------------------------------------------------------------

alter table public.leagues      enable row level security;
alter table public.inside_jokes enable row level security;
alter table public.newspapers   enable row level security;


-- ---------------------------------------------------------------------------
-- STEP 8 — Storage: the `newspapers` bucket
--
-- Public read so a shared link opens for anyone. Writes come only from the
-- server via the service role key, which needs no policy — so there are none,
-- and nobody else can write.
--
-- Path convention: <public_slug>/<season>/week-NN.html
-- ---------------------------------------------------------------------------

insert into storage.buckets (id, name, public)
values ('newspapers', 'newspapers', true)
on conflict (id) do update set public = true;

-- No SELECT policy: a public bucket serves files by URL without one, and a
-- policy here would let anyone with the anon key LIST every league's paper.
-- See 017_no_bucket_listing.sql.
drop policy if exists "newspapers are publicly readable" on storage.objects;

-- Remove the per-user write policies from the account-based schema.
drop policy if exists "users write to their own newspaper folder" on storage.objects;
drop policy if exists "users update their own newspapers"          on storage.objects;
drop policy if exists "users delete their own newspapers"          on storage.objects;

-- ============================================================================
-- Next: run 003_email.sql, then 004_lore.sql.
-- Then Project Settings → API: copy the Project URL and the SERVICE ROLE key
-- into .env as SUPABASE_URL and SUPABASE_SERVICE_KEY. See SETUP.md.
-- ============================================================================
