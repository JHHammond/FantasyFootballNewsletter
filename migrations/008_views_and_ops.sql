-- 008: read counts.
--
-- Impressions are the entire business model and there was no way to count one.
-- Without this a commissioner can't tell a paper the whole league read from one
-- nobody opened, and there is no number to quote an advertiser.
--
-- Idempotent, like every migration in this chain: safe to run twice, safe to
-- run against a database that already has it.

-- --------------------------------------------------------------------------
-- 1. The counter itself.
-- --------------------------------------------------------------------------

alter table public.newspapers
    add column if not exists view_count bigint not null default 0;

alter table public.newspapers
    add column if not exists first_viewed_at timestamptz;

alter table public.newspapers
    add column if not exists last_viewed_at timestamptz;


-- --------------------------------------------------------------------------
-- 2. Incrementing it.
--
-- A read-modify-write from the application would drop views whenever two
-- readers opened the same paper at once, which for a link pasted into a group
-- chat is the normal case rather than the edge case. Doing it in one statement
-- inside the database makes the count correct under concurrency.
-- --------------------------------------------------------------------------

create or replace function bump_paper_views(
    p_league_id uuid,
    p_season    integer,
    p_week      integer
)
returns void
language sql
security definer
set search_path = public
as $$
    update public.newspapers
       set view_count      = view_count + 1,
           first_viewed_at = coalesce(first_viewed_at, now()),
           last_viewed_at  = now()
     where league_id = p_league_id
       and season    = p_season
       and week      = p_week;
$$;

-- The service role already bypasses RLS; this grant is here so the function
-- stays callable if a future migration introduces a narrower role.
grant execute on function bump_paper_views(uuid, integer, integer) to service_role;


-- --------------------------------------------------------------------------
-- 3. Reading it back.
-- --------------------------------------------------------------------------

create index if not exists newspapers_league_views_idx
    on public.newspapers (league_id, view_count desc);
