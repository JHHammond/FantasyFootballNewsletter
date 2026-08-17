-- ============================================================================
-- The Commissioner's Desk — accountless schema
--
-- Run this in a fresh Supabase project INSTEAD OF 000_bootstrap.sql.
-- (If you already ran 000, this migrates you forward — it's all IF NOT EXISTS
-- and IF EXISTS, so it's safe either way.)
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
-- The service role key must never be sent to a browser. It lives in the
-- server's environment.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- leagues
-- ---------------------------------------------------------------------------

create table if not exists public.leagues (
    id                  uuid primary key default gen_random_uuid(),

    provider            text not null default 'sleeper',
    platform_league_id  text not null,

    league_name         text not null,
    paper_name          text,
    commissioner_name   text,
    season              integer not null,

    -- Readable, shared publicly, appears in every paper URL.
    public_slug         text not null,
    -- Unguessable. The league's only credential. There is no reset.
    admin_token         text not null,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    constraint leagues_provider_check
        check (provider in ('sleeper', 'espn', 'yahoo'))
);

-- Migrating from the account-based schema: add the new columns, drop the old.
alter table public.leagues add column if not exists public_slug text;
alter table public.leagues add column if not exists admin_token text;
alter table public.leagues drop column if exists user_id;
alter table public.leagues drop column if exists sleeper_league_id;

-- Backfill anything created before these columns existed, so the NOT NULL and
-- UNIQUE constraints below can be applied without blowing up.
update public.leagues
   set public_slug = coalesce(
           public_slug,
           regexp_replace(lower(league_name), '[^a-z0-9]+', '-', 'g')
           || '-' || substr(md5(random()::text), 1, 4)
       ),
       admin_token = coalesce(admin_token, replace(gen_random_uuid()::text, '-', ''))
 where public_slug is null or admin_token is null;

create unique index if not exists leagues_public_slug_idx on public.leagues (public_slug);
create unique index if not exists leagues_admin_token_idx on public.leagues (admin_token);

-- One paper per league per season. This is also what lets the app detect
-- "someone already set this league up" instead of creating a duplicate.
create unique index if not exists leagues_provider_league_season_idx
    on public.leagues (provider, platform_league_id, season);

-- ---------------------------------------------------------------------------
-- inside_jokes — league lore, injected into the writer prompt every run
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
-- newspapers — metadata only. The HTML lives in the storage bucket.
-- ---------------------------------------------------------------------------

create table if not exists public.newspapers (
    id            uuid primary key default gen_random_uuid(),
    league_id     uuid not null references public.leagues (id) on delete cascade,
    week          integer not null,
    season        integer not null,

    storage_path  text,
    public_url    text,
    ai_cache      jsonb,

    generated_at  timestamptz not null default now(),

    constraint newspapers_week_check check (week between 1 and 22)
);

alter table public.newspapers drop column if exists html_content;

create unique index if not exists newspapers_league_week_season_idx
    on public.newspapers (league_id, season, week);

create index if not exists newspapers_league_idx
    on public.newspapers (league_id, season desc, week desc);

-- ---------------------------------------------------------------------------
-- updated_at
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

-- ============================================================================
-- Row Level Security: on, with no policies. Deny by default.
--
-- The server uses the service role key and bypasses this entirely. The point
-- is that nothing ELSE can read these tables — if the anon key ends up in a
-- browser, a crawler, or a screenshot, it opens nothing.
-- ============================================================================

alter table public.leagues      enable row level security;
alter table public.inside_jokes enable row level security;
alter table public.newspapers   enable row level security;

-- Drop any policies left over from the account-based schema.
drop policy if exists "leagues are visible to their owner"     on public.leagues;
drop policy if exists "users insert their own leagues"          on public.leagues;
drop policy if exists "users update their own leagues"          on public.leagues;
drop policy if exists "users delete their own leagues"          on public.leagues;
drop policy if exists "jokes follow league ownership"           on public.inside_jokes;
drop policy if exists "newspapers follow league ownership"      on public.newspapers;

-- ============================================================================
-- Storage: the `newspapers` bucket
--
-- Public read so a shared link opens for anyone. Writes come only from the
-- server via the service role key, so there are no insert/update policies —
-- the service role doesn't need one, and nobody else should be able to write.
--
-- Path convention: <public_slug>/<season>/week-NN.html
-- ============================================================================

insert into storage.buckets (id, name, public)
values ('newspapers', 'newspapers', true)
on conflict (id) do update set public = true;

drop policy if exists "newspapers are publicly readable" on storage.objects;
create policy "newspapers are publicly readable"
    on storage.objects for select
    using (bucket_id = 'newspapers');

-- Remove the per-user write policies from the account-based schema.
drop policy if exists "users write to their own newspaper folder" on storage.objects;
drop policy if exists "users update their own newspapers"          on storage.objects;
drop policy if exists "users delete their own newspapers"          on storage.objects;

-- ============================================================================
-- Next: Project Settings → API. Copy the Project URL and the SERVICE ROLE key
-- into .env as SUPABASE_URL and SUPABASE_SERVICE_KEY. See SETUP.md.
-- ============================================================================
