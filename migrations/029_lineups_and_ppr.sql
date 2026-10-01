-- 029: Lineups and standardized PPR for Around the Leagues (30 Sep 2026).
-- Needs 021 (team_weeks). Re-runnable.
--
-- lineup_players   one row per rostered player per team per finished week:
--                  where he sat (slot), whether he started, his points in
--                  that league, and his plain-PPR points. Matched players are
--                  keyed by Sleeper id, so one player is one key everywhere.
--                  About 200,000 rows a week, roughly 35 MB a week with its
--                  indexes; a full season is well under a gigabyte.
-- nfl_players      the name, position and team behind each player_key.
-- team_weeks       gains ppr_points (the lineup scored in plain PPR) and
--                  ppr_coverage (the share of starters that could be matched).
--
-- around_week / around_season do the arithmetic in the database and return
-- one JSON object each, so the page never pages 200,000 rows through the API.

alter table public.team_weeks add column if not exists ppr_points   numeric(7,2);
alter table public.team_weeks add column if not exists ppr_coverage numeric(4,3);

create table if not exists public.nfl_players (
    player_key  text        primary key,
    name        text        not null default '',
    position    text,
    nfl_team    text,
    updated_at  timestamptz not null default now()
);

create table if not exists public.lineup_players (
    league_id   uuid        not null references public.leagues (id) on delete cascade,
    season      smallint    not null,
    week        smallint    not null,
    team_id     text        not null,
    player_key  text        not null,
    slot        text        not null,
    started     boolean     not null,
    points      numeric(6,2) not null default 0,
    ppr_points  numeric(6,2),
    primary key (league_id, season, week, team_id, player_key)
);

create index if not exists lineup_players_week
    on public.lineup_players (season, week, player_key);

-- Server-only, like every other table: RLS on, no policies.
alter table public.nfl_players    enable row level security;
alter table public.lineup_players enable row level security;


-- Leagues whose lineups are already stored for a week, so a rerun skips them.
-- JSON rather than a set of rows: the API caps a set at 1,000 rows.
create or replace function public.lineup_league_ids(p_season integer, p_week integer)
returns json language sql stable as $$
    select coalesce(json_agg(distinct league_id), '[]'::json)
      from public.lineup_players
     where season = p_season and week = p_week;
$$;


-- One finished week, across every league.
--
--   std_pts    the standardized (plain PPR) score where we have it, the
--              league's own score where we don't.
--   in_sd      counts in cross-league comparisons: once any team in the week
--              is standardized, only standardized teams do; before that,
--              only leagues whose own scoring looks standard (in_norm).
--   in_norm    the league's own scoring looks standard (its own points within
--              30% of its PPR points; with no PPR yet, a league average of
--              40-200). Stats read from a league's OWN points (the format
--              lookups, bench points, blowouts) use only these, so a league
--              scoring defenders or yardage bonuses (a 1,052 average, seen
--              30 Sep) can't swamp them.
create or replace function public.around_week(
    p_season integer, p_week integer, p_min_starts integer default 150)
returns json language sql stable as $$
with base as (
    select t.*, coalesce(t.ppr_points, t.points) as std_pts
      from public.team_weeks t
     where t.season = p_season and t.week = p_week
       and t.result <> 'BYE' and t.points > 0
),
has_std as (select exists (select 1 from base where ppr_points is not null) as yes),
lg as (
    select league_id, avg(points) as raw, avg(ppr_points) as ppr
      from base group by league_id
),
okl as (
    select league_id from lg
     where case when ppr is not null and ppr > 0 then raw / ppr between 0.7 and 1.3
                else raw between 40 and 200 end
),
tw as (
    select b.*,
           case when h.yes then b.ppr_points is not null
                else b.league_id in (select league_id from okl) end as in_sd,
           (b.league_id in (select league_id from okl)) as in_norm
      from base b, has_std h
),
sd as (select * from tw where in_sd),
norm as (select * from tw where in_norm),
card as (
    select league_id, team_id, std_pts, points, margin, bench_left, result,
           in_sd, in_norm, json_build_object(
        'league_id', league_id, 'team_id', team_id, 'team_name', team_name,
        'manager', manager, 'provider', provider, 'team_count', team_count,
        'scoring_type', scoring_type, 'week', week, 'result', result,
        'points', points, 'ppr_points', ppr_points,
        'opponent_points', opponent_points, 'margin', margin,
        'optimal_points', optimal_points, 'bench_left', bench_left,
        'empty_slots', empty_slots, 'top_player', top_player,
        'best_bench_player', best_bench_player) as c
      from tw
),
lut as (
    select l.player_key, l.slot, l.started, l.points, l.ppr_points,
           l.league_id, l.team_id, tw.result
      from public.lineup_players l
      join tw on tw.league_id = l.league_id and tw.team_id = l.team_id
     where l.season = p_season and l.week = p_week
),
top1 as (
    select distinct on (league_id, team_id) league_id, team_id, player_key, points
      from lut where started
     order by league_id, team_id, points desc, player_key collate "C"
),
ps as (
    select player_key,
           count(*)                                         as rostered,
           count(*) filter (where started)                  as started,
           count(*) filter (where not started and slot = 'BN') as benched,
           sum(case when started and result = 'W' then 1
                    when started and result = 'T' then 0.5 else 0 end) as wins,
           max(ppr_points)                                  as ppr,
           max(points)                                      as best,
           coalesce(sum(points) filter (where not started and slot = 'BN'), 0) as bench_total,
           avg(points) filter (where not started and slot = 'BN') as bench_avg
      from lut group by player_key
),
pj as (
    select ps.*, coalesce(np.name, ps.player_key) as name, np.position, np.nfl_team,
           round(ps.started::numeric / nullif(ps.rostered, 0), 3) as start_pct,
           round(ps.wins / nullif(ps.started, 0), 3)               as win_pct
      from ps left join public.nfl_players np on np.player_key = ps.player_key
),
pcard as (
    select pj.*, json_build_object(
        'player_key', player_key, 'name', name, 'position', position,
        'nfl_team', nfl_team, 'rostered', rostered, 'started', started,
        'benched', benched, 'start_pct', start_pct, 'win_pct', win_pct,
        'wins', wins, 'ppr', ppr, 'best', best,
        'bench_total', round(bench_total, 2),
        'bench_avg', round(bench_avg, 2)) as c
      from pj
),
grp as (
    select dim, key, count(*) as n,
           round(avg(std_pts) filter (where in_sd), 2)  as avg_ppr,
           round(avg(bench_left) filter (where in_norm), 2) as avg_bench,
           round(avg(case when empty_slots > 0 then 1.0 else 0 end), 4) as ghost_pct,
           round(avg(case when bench_left <= 0.005 then 1.0 else 0 end)
                 filter (where bench_left is not null), 4) as perfect_pct
      from (
        select 'provider' as dim, provider as key, * from tw
        union all
        select 'scoring', coalesce(scoring_type, 'unknown'), * from tw
        union all
        select 'size', case when team_count <= 8 then '8-'
                            when team_count <= 10 then '10'
                            when team_count <= 12 then '12'
                            when team_count <= 14 then '14'
                            else '16+' end, * from tw
      ) g
     group by dim, key
)
select json_build_object(
    'season', p_season, 'week', p_week,
    'summary', (select json_build_object(
        'teams', count(*), 'leagues', count(distinct league_id),
        'compared', count(*) filter (where in_sd),
        'custom_leagues', count(distinct league_id) filter (where not in_norm),
        'avg_raw', round(avg(points), 2),
        'median_raw', round((percentile_cont(0.5) within group (order by points))::numeric, 2),
        'avg_ppr', round(avg(std_pts) filter (where in_sd), 2),
        'median_ppr', round((percentile_cont(0.5) within group (order by std_pts)
                             filter (where in_sd))::numeric, 2),
        'standardized', count(ppr_points),
        'coverage', round(avg(ppr_coverage), 3),
        'lineup_teams', (select count(distinct (league_id, team_id)) from lut))
      from tw),
    'hist', (select coalesce(json_agg(json_build_object('lo', lo, 'n', n) order by lo), '[]'::json)
               from (select least(floor(std_pts / 10) * 10, 250)::int as lo, count(*) as n
                       from sd group by 1) h),
    'pct', json_build_object(
        'all', (select percentile_cont(array(select g / 100.0 from generate_series(0, 100) g)::float8[])
                         within group (order by std_pts) from sd),
        'ppr', (select percentile_cont(array(select g / 100.0 from generate_series(0, 100) g)::float8[])
                         within group (order by points) from norm where scoring_type = 'ppr'),
        'half_ppr', (select percentile_cont(array(select g / 100.0 from generate_series(0, 100) g)::float8[])
                         within group (order by points) from norm where scoring_type = 'half_ppr'),
        'std', (select percentile_cont(array(select g / 100.0 from generate_series(0, 100) g)::float8[])
                         within group (order by points) from norm where scoring_type = 'std'),
        'n_all', (select count(*) from sd),
        'n_ppr', (select count(*) from norm where scoring_type = 'ppr'),
        'n_half_ppr', (select count(*) from norm where scoring_type = 'half_ppr'),
        'n_std', (select count(*) from norm where scoring_type = 'std')),
    'lineup', (select json_build_object(
        'played', count(*),
        'losses', count(*) filter (where result = 'L'),
        'lineup_losses', count(*) filter (where result = 'L' and optimal_points > opponent_points),
        'with_optimal', count(*) filter (where bench_left is not null),
        'perfect', count(*) filter (where bench_left <= 0.005),
        'ghosts', count(*) filter (where empty_slots > 0),
        'avg_bench_left', round(avg(bench_left) filter (where in_norm), 2))
      from tw),
    'unlucky', (select coalesce(json_agg(c order by std_pts desc, league_id, team_id collate "C"), '[]'::json)
                  from (select * from card where in_sd and result = 'L'
                         order by std_pts desc, league_id, team_id collate "C" limit 5) x),
    'lucky', (select coalesce(json_agg(c order by std_pts, league_id, team_id collate "C"), '[]'::json)
                from (select * from card where in_sd and result = 'W'
                       order by std_pts, league_id, team_id collate "C" limit 5) x),
    'halls', json_build_object(
        'highest', (select coalesce(json_agg(c order by std_pts desc, league_id, team_id collate "C"), '[]'::json)
                      from (select * from card where in_sd
                             order by std_pts desc, league_id, team_id collate "C" limit 5) x),
        'lowest', (select coalesce(json_agg(c order by std_pts, league_id, team_id collate "C"), '[]'::json)
                     from (select * from card where in_sd
                            order by std_pts, league_id, team_id collate "C" limit 5) x),
        'blowouts', (select coalesce(json_agg(c order by margin desc, league_id, team_id collate "C"), '[]'::json)
                       from (select * from card where in_norm and result = 'W' and margin is not null
                              order by margin desc, league_id, team_id collate "C" limit 5) x),
        'bench', (select coalesce(json_agg(c order by bench_left desc, league_id, team_id collate "C"), '[]'::json)
                    from (select * from card where in_norm and bench_left is not null
                           order by bench_left desc, league_id, team_id collate "C" limit 5) x)),
    'groups', (select coalesce(json_agg(json_build_object(
                    'dim', dim, 'key', key, 'n', n, 'avg_ppr', avg_ppr,
                    'avg_bench', avg_bench, 'ghost_pct', ghost_pct,
                    'perfect_pct', perfect_pct) order by dim, key collate "C"), '[]'::json)
                 from grp),
    'players', json_build_object(
        'top_on', (select coalesce(json_agg(json_build_object(
                        'player_key', t.player_key, 'name', coalesce(np.name, t.player_key),
                        'position', np.position, 'teams', t.teams, 'best', t.best)
                        order by t.teams desc, t.player_key collate "C"), '[]'::json)
                     from (select player_key, count(*) as teams, max(points) as best
                             from top1 group by player_key
                            order by count(*) desc, player_key collate "C" limit 10) t
                     left join public.nfl_players np on np.player_key = t.player_key),
        'benched', (select coalesce(json_agg(c order by bench_total desc, player_key collate "C"), '[]'::json)
                      from (select * from pcard where benched > 0
                             order by bench_total desc, player_key collate "C" limit 10) x),
        'started', (select coalesce(json_agg(c order by started desc, player_key collate "C"), '[]'::json)
                      from (select * from pcard where started > 0
                             order by started desc, player_key collate "C" limit 10) x),
        'win_best', (select coalesce(json_agg(c order by win_pct desc, started desc, player_key collate "C"), '[]'::json)
                       from (select * from pcard where started >= p_min_starts
                              order by win_pct desc, started desc, player_key collate "C" limit 10) x),
        'win_worst', (select coalesce(json_agg(c order by win_pct, started desc, player_key collate "C"), '[]'::json)
                        from (select * from pcard where started >= p_min_starts
                               order by win_pct, started desc, player_key collate "C" limit 10) x),
        'flops', (select coalesce(json_agg(c order by started desc, player_key collate "C"), '[]'::json)
                    from (select * from pcard where started > 0 and ppr is not null and ppr < 5
                           order by started desc, player_key collate "C" limit 5) x),
        'sleepers', (select coalesce(json_agg(c order by ppr desc, player_key collate "C"), '[]'::json)
                       from (select * from pcard where rostered >= 100 and ppr is not null
                               and start_pct < 0.25
                              order by ppr desc, player_key collate "C" limit 5) x),
        'min_starts', p_min_starts)
);
$$;


-- The season so far: the week-by-week line, who is unbeaten, the streaks,
-- and luck (record against all-play record: how you'd have done playing
-- every team in your league every week).
create or replace function public.around_season(p_season integer)
returns json language sql stable as $$
with games as (
    select t.*, coalesce(t.ppr_points, t.points) as std_pts
      from public.team_weeks t
     where t.season = p_season and t.result in ('W', 'L', 'T')
),
last_week as (select max(week) as w from games),
latest as (
    -- Teams still playing: they have a real score in the latest week.
    select g.* from games g, last_week
     where g.week = last_week.w and g.points > 0
),
rec as (
    select g.league_id, g.team_id,
           count(*) filter (where g.result = 'W') as w,
           count(*) filter (where g.result = 'L') as l,
           count(*) filter (where g.result = 'T') as t,
           round(sum(g.std_pts), 2) as total
      from games g
      join latest a on a.league_id = g.league_id and a.team_id = g.team_id
     group by g.league_id, g.team_id
),
seq as (
    select g.league_id, g.team_id, g.result,
           row_number() over (partition by g.league_id, g.team_id order by g.week desc) as rn,
           first_value(g.result) over (partition by g.league_id, g.team_id order by g.week desc) as cur
      from games g
      join latest a on a.league_id = g.league_id and a.team_id = g.team_id
),
streak as (
    select league_id, team_id, max(cur) as kind,
           coalesce(min(rn) filter (where result <> cur), max(rn) + 1) - 1 as len
      from seq group by league_id, team_id
),
ap as (
    select league_id, team_id, week, result, points,
           rank() over (partition by league_id, week order by points) - 1 as lower,
           count(*) over (partition by league_id, week) as n,
           count(*) over (partition by league_id, week, points) as same
      from games
),
luck as (
    select ap.league_id, ap.team_id,
           sum(case when ap.result = 'W' then 1 when ap.result = 'T' then 0.5 else 0 end) as wins,
           round(sum((ap.lower + 0.5 * (ap.same - 1))::numeric / (ap.n - 1)), 2) as expected
      from ap
      join latest a on a.league_id = ap.league_id and a.team_id = ap.team_id
     where ap.n > 1
     group by ap.league_id, ap.team_id
),
board as (
    select a.league_id, a.team_id, json_build_object(
        'league_id', a.league_id, 'team_id', a.team_id, 'team_name', a.team_name,
        'manager', a.manager, 'provider', a.provider, 'team_count', a.team_count,
        'scoring_type', a.scoring_type,
        'w', r.w, 'l', r.l, 't', r.t, 'total', r.total,
        'streak', s.len, 'streak_kind', s.kind,
        'wins', lk.wins, 'expected', lk.expected,
        'luck', round(lk.wins - lk.expected, 2)) as c,
        s.len, s.kind, lk.wins - lk.expected as luck
      from latest a
      join rec r on r.league_id = a.league_id and r.team_id = a.team_id
      join streak s on s.league_id = a.league_id and s.team_id = a.team_id
      left join luck lk on lk.league_id = a.league_id and lk.team_id = a.team_id
),
wk_std as (select distinct week from games where ppr_points is not null),
wk as (
    -- A week with standardized scores averages only those, like around_week.
    select week, count(*) as teams,
           round(avg(std_pts) filter (where in_sd), 2) as avg_ppr,
           round((percentile_cont(0.5) within group (order by std_pts)
                  filter (where in_sd))::numeric, 2) as median_ppr,
           round(avg(case when empty_slots > 0 then 1.0 else 0 end), 4) as ghost_pct,
           round(count(*) filter (where result = 'L' and optimal_points > opponent_points)::numeric
                 / nullif(count(*) filter (where result = 'L'), 0), 4) as lineup_loss_pct,
           round(avg(case when bench_left <= 0.005 then 1.0 else 0 end)
                 filter (where bench_left is not null), 4) as perfect_pct
      from (select g.*, (g.week not in (select week from wk_std)
                         or g.ppr_points is not null) as in_sd
              from games g where g.points > 0) x
     group by week
)
select json_build_object(
    'season', p_season,
    'through', (select w from last_week),
    'weeks', (select coalesce(json_agg(json_build_object(
                  'week', week, 'teams', teams, 'avg_ppr', avg_ppr,
                  'median_ppr', median_ppr, 'ghost_pct', ghost_pct,
                  'lineup_loss_pct', lineup_loss_pct, 'perfect_pct', perfect_pct)
                  order by week), '[]'::json) from wk),
    'tracker', (select json_build_object(
        'teams', count(*),
        'unbeaten', count(*) filter (where l = 0 and t = 0),
        'winless', count(*) filter (where w = 0 and t = 0)) from rec),
    'win_streaks', (select coalesce(json_agg(c order by len desc, league_id, team_id collate "C"), '[]'::json)
                      from (select * from board where kind = 'W'
                             order by len desc, league_id, team_id collate "C" limit 5) x),
    'loss_streaks', (select coalesce(json_agg(c order by len desc, league_id, team_id collate "C"), '[]'::json)
                       from (select * from board where kind = 'L'
                              order by len desc, league_id, team_id collate "C" limit 5) x),
    'unluckiest', (select coalesce(json_agg(c order by luck, league_id, team_id collate "C"), '[]'::json)
                     from (select * from board where luck is not null
                            order by luck, league_id, team_id collate "C" limit 5) x),
    'luckiest', (select coalesce(json_agg(c order by luck desc, league_id, team_id collate "C"), '[]'::json)
                   from (select * from board where luck is not null
                          order by luck desc, league_id, team_id collate "C" limit 5) x)
);
$$;
