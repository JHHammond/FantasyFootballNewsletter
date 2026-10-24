-- 060: a first-party event log, for the funnel.
--
-- Cloudflare Web Analytics counts page views. It can't say how many people who
-- opened /connect went on to add a league, or how many of those made a paper,
-- and that conversion is the number the product lives on. So the server writes
-- one row per step it can see for itself.
--
-- One row per event, no visitor identifier: no IP, no cookie, no user agent.
-- A league ID where there is one, so "papers made" can also be "leagues that
-- made one". Nothing here can be joined back to a person.
--
-- Not in db._EXPECTED_SCHEMA on purpose: that list gates paper generation,
-- and a missing analytics table must never stop a paper being written. Until
-- this runs, log_event fails quietly and the funnel table on /staff/stats
-- says it has nothing.
--
-- Idempotent, like the rest of the chain.

create table if not exists public.events (
    id          bigserial   primary key,
    name        text        not null,
    league_id   uuid        references public.leagues (id) on delete set null,
    props       jsonb       not null default '{}'::jsonb,
    occurred_at timestamptz not null default now()
);

create index if not exists events_time_name_idx
    on public.events (occurred_at desc, name);

alter table public.events enable row level security;
-- No policies. Same posture as every other table: deny-all to browser-facing
-- roles, and the server's service key bypasses RLS.


-- Weekly counts for /staff/stats. Weeks start Monday, Eastern, so a Tuesday
-- paper run lands in the same week as the Sunday games it covers.
--
-- p_weeks is how many weeks back, including the current one.
create or replace function public.events_weekly(p_weeks integer default 8)
returns table (week_start date, name text, events bigint, leagues bigint)
language sql stable
set search_path = public
as $$
    select (date_trunc('week', e.occurred_at at time zone 'America/New_York'))::date,
           e.name,
           count(*),
           count(distinct e.league_id)
      from public.events e
     where e.occurred_at >= date_trunc('week', now() at time zone 'America/New_York')
                            at time zone 'America/New_York'
                            - make_interval(weeks => greatest(p_weeks, 1) - 1)
     group by 1, 2
     order by 1 desc, 2;
$$;
