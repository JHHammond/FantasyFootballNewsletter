-- 021: Around the Leagues (25 Sep 2026)
--
-- One row per team per FINISHED week, across every connected league. Filled
-- by the Tuesday job (every league, stats only, no Claude) and by writing a
-- paper for a finished week. Read by the staff page /staff/around.
--
-- Nothing else depends on this table: until it exists, the collector and the
-- page say so and every paper works as before. Re-runnable.

create table if not exists public.team_weeks (
    league_id           uuid        not null references public.leagues (id) on delete cascade,
    provider            text        not null,
    platform_league_id  text        not null,
    season              integer     not null,
    week                integer     not null,
    team_id             text        not null,
    team_name           text,
    manager             text,
    points              numeric(7,2) not null default 0,
    opponent_name       text,
    opponent_points     numeric(7,2),
    margin              numeric(7,2),
    result              text        not null,   -- W | L | T | BYE
    optimal_points      numeric(7,2),
    bench_left          numeric(7,2),
    empty_slots         integer     not null default 0,
    top_player          text,
    top_player_pos      text,
    top_player_points   numeric(7,2),
    best_bench_player   text,
    best_bench_points   numeric(7,2),
    team_count          integer,
    scoring_type        text,
    collected_at        timestamptz not null default now(),
    primary key (league_id, season, week, team_id)
);

create index if not exists team_weeks_week_points
    on public.team_weeks (season, week, points);

-- Server-only, like every other table: RLS on, no policies.
alter table public.team_weeks enable row level security;

-- Counts and averages for the page header, computed in the database rather
-- than by paging 14,000 rows a week through the API.
create or replace function public.team_weeks_summary(p_season integer, p_week integer)
returns table (teams bigint, leagues bigint, avg_points numeric, median_points numeric)
language sql stable as $$
    select count(*),
           count(distinct league_id),
           round(avg(points), 2),
           round((percentile_cont(0.5) within group (order by points))::numeric, 2)
      from public.team_weeks
     where season = p_season
       and (p_week is null or week = p_week)
       and result <> 'BYE'
       and points > 0;
$$;
