-- 031: The Luck Index (30 Sep 2026). Needs 021 (team_weeks) and 029
-- (lineup_players). Re-runnable.
--
-- Luck is measured in WINS: the wins a team has, minus the wins it earned.
--
--   earned      Each week, the share of its league it would have beaten if it
--               played everybody (all-play), with its starters' scores set to
--               their own season averages in that league. Summed over weeks.
--   schedule    actual wins - all-play wins on the real scores: who you drew,
--               and when (your opponent's big week, your good week wasted).
--   players     all-play wins on real scores - all-play wins with starters at
--               their averages: your guys going off, or going missing.
--   total       schedule + players = actual wins - earned.
--
-- Also returned, to explain it, not added to it:
--   opp_luck    per game, opponent's season average - what they scored on you
--   close       record in games decided by under 5% of the league's average
--   gifts       wins where the opponent's own bench would have beaten you
--   own_losses  losses your own bench would have won (skill, not luck)
--
-- Starters' averages count weeks they scored, in this league's scoring, so a
-- different scoring system never matters. With no lineups stored, player
-- luck is 0 and has_players is false.

create or replace function public.luck_rows(p_season integer, p_league uuid default null)
returns table (
    league_id uuid, team_id text, team_name text, manager text, provider text,
    team_count integer, scoring_type text,
    games bigint, w bigint, l bigint, t bigint,
    wins numeric, ap_real numeric, ap_adj numeric,
    schedule numeric, players numeric, total numeric,
    pf numeric, pa numeric, opp_luck numeric, player_dev numeric,
    close_w bigint, close_l bigint, gifts bigint, own_losses bigint,
    bench_avg numeric, has_players boolean, last_week integer
) language sql stable as $$
with g as (
    select t.league_id, t.team_id, t.week, t.team_name, t.manager, t.provider,
           t.team_count, t.scoring_type, t.points, t.opponent_points, t.result,
           t.optimal_points, t.bench_left,
           avg(t.points) over (partition by t.league_id, t.team_id) as team_avg
      from public.team_weeks t
     where t.season = p_season and t.result in ('W', 'L', 'T') and t.points > 0
       and (p_league is null or t.league_id = p_league)
),
lavg as (select g.league_id, avg(g.points) as avg_pts from g group by g.league_id),
pavg as (
    select lp.league_id, lp.player_key, avg(lp.points) as avg_pts
      from public.lineup_players lp
     where lp.season = p_season and lp.points > 0
       and (p_league is null or lp.league_id = p_league)
     group by lp.league_id, lp.player_key
),
dev as materialized (
    select lp.league_id, lp.team_id, lp.week, sum(lp.points - pa.avg_pts) as dev
      from public.lineup_players lp
      join pavg pa on pa.league_id = lp.league_id and pa.player_key = lp.player_key
     where lp.season = p_season and lp.started
       and (p_league is null or lp.league_id = p_league)
     group by lp.league_id, lp.team_id, lp.week
),
gw as (
    select g.*, d.dev, g.points - coalesce(d.dev, 0) as adj,
           count(*) over (partition by g.league_id, g.week) as n
      from g
      left join dev d on d.league_id = g.league_id and d.team_id = g.team_id
                     and d.week = g.week
),
ap as materialized (
    select a.league_id, a.team_id, a.week,
           sum(case when b.points < a.points then 1.0
                    when b.points = a.points then 0.5 else 0 end) / (a.n - 1) as ap_real,
           sum(case when b.points < a.adj - 0.000001 then 1.0
                    when abs(b.points - a.adj) <= 0.000001 then 0.5 else 0 end) / (a.n - 1) as ap_adj
      from gw a
      join gw b on b.league_id = a.league_id and b.week = a.week and b.team_id <> a.team_id
     where a.n > 1
     group by a.league_id, a.team_id, a.week, a.n
),
opp as materialized (
    select distinct on (a.league_id, a.team_id, a.week)
           a.league_id, a.team_id, a.week, o.team_id as opp_id,
           o.optimal_points as opp_optimal, o.team_avg as opp_avg
      from g a
      join g o on o.league_id = a.league_id and o.week = a.week and o.team_id <> a.team_id
              and o.points = a.opponent_points and o.opponent_points = a.points
     order by a.league_id, a.team_id, a.week, o.team_id collate "C"
),
games as (
    select gw.*, ap.ap_real, ap.ap_adj, op.opp_avg,
           abs(gw.points - gw.opponent_points) < 0.05 * la.avg_pts as is_close,
           op.opp_optimal
      from gw
      join lavg la on la.league_id = gw.league_id
      left join ap on ap.league_id = gw.league_id and ap.team_id = gw.team_id and ap.week = gw.week
      left join opp op on op.league_id = gw.league_id and op.team_id = gw.team_id and op.week = gw.week
),
latest as (
    select distinct on (gm.league_id, gm.team_id) gm.league_id, gm.team_id, gm.team_name,
           gm.manager, gm.provider, gm.team_count, gm.scoring_type, gm.week
      from games gm
     order by gm.league_id, gm.team_id, gm.week desc
)
select gm.league_id, gm.team_id,
       max(lt.team_name), max(lt.manager), max(lt.provider), max(lt.team_count),
       max(lt.scoring_type),
       count(*),
       count(*) filter (where gm.result = 'W'),
       count(*) filter (where gm.result = 'L'),
       count(*) filter (where gm.result = 'T'),
       sum(case when gm.result = 'W' then 1 when gm.result = 'T' then 0.5 else 0 end)::numeric,
       round(sum(gm.ap_real)::numeric, 3),
       round(sum(gm.ap_adj)::numeric, 3),
       round((sum(case when gm.result = 'W' then 1 when gm.result = 'T' then 0.5 else 0 end)
              - sum(gm.ap_real))::numeric, 3),
       round((sum(gm.ap_real) - sum(gm.ap_adj))::numeric, 3),
       round((sum(case when gm.result = 'W' then 1 when gm.result = 'T' then 0.5 else 0 end)
              - sum(gm.ap_adj))::numeric, 3),
       round(avg(gm.points)::numeric, 2),
       round(avg(gm.opponent_points)::numeric, 2),
       round(avg(gm.opp_avg - gm.opponent_points)::numeric, 2),
       round(avg(gm.dev)::numeric, 2),
       count(*) filter (where gm.is_close and gm.result = 'W'),
       count(*) filter (where gm.is_close and gm.result = 'L'),
       count(*) filter (where gm.result = 'W' and gm.opp_optimal > gm.points),
       count(*) filter (where gm.result = 'L' and gm.optimal_points > gm.opponent_points),
       round(avg(gm.bench_left)::numeric, 2),
       bool_or(gm.dev is not null),
       max(gm.week)
  from games gm
  join latest lt on lt.league_id = gm.league_id and lt.team_id = gm.team_id
 where gm.ap_real is not null
 group by gm.league_id, gm.team_id;
$$;


-- One league, every team.
create or replace function public.luck_league(p_season integer, p_league uuid)
returns json language sql stable as $$
    select coalesce(json_agg(row_to_json(r) order by r.total desc, r.team_id collate "C"), '[]'::json)
      from public.luck_rows(p_season, p_league) r;
$$;


-- The whole country: percentiles of total luck (for "luckier than 96%"), a
-- histogram in half-win steps, how many are blessed and cursed, and the three
-- luckiest and unluckiest teams in America.
create or replace function public.luck_national(p_season integer)
returns json language sql stable as $$
    with r as materialized (select * from public.luck_rows(p_season, null)),
    c as (
        select r.*, json_build_object(
            'league_id', league_id, 'team_id', team_id, 'team_name', team_name,
            'manager', manager, 'provider', provider, 'team_count', team_count,
            'scoring_type', scoring_type, 'w', w, 'l', l, 't', t,
            'total', total, 'schedule', schedule, 'players', players) as card
          from r
    )
    select json_build_object(
        'teams', (select count(*) from r),
        'pct', (select percentile_cont(array(select g / 100.0 from generate_series(0, 100) g)::float8[])
                         within group (order by total) from r),
        'blessed', (select count(*) from r where total >= 1.5),
        'cursed', (select count(*) from r where total <= -1.5),
        'hist', (select coalesce(json_agg(json_build_object('lo', lo, 'n', n) order by lo), '[]'::json)
                   from (select greatest(-4.0, least(3.5, floor(total * 2) / 2.0)) as lo, count(*) as n
                           from r group by 1) h),
        'luckiest', (select coalesce(json_agg(card order by total desc, league_id, team_id collate "C"), '[]'::json)
                       from (select * from c order by total desc, league_id, team_id collate "C" limit 3) x),
        'unluckiest', (select coalesce(json_agg(card order by total, league_id, team_id collate "C"), '[]'::json)
                         from (select * from c order by total, league_id, team_id collate "C" limit 3) x));
$$;


-- One team's season, week by week and player by player: the raw material
-- for the Luck Report's cards (its unluckiest week, the opponent who played
-- out of his mind, the starter who went missing).
create or replace function public.luck_team(p_season integer, p_league uuid, p_team text)
returns json language sql stable as $$
with g as (
    select t.*, avg(t.points) over (partition by t.team_id) as team_avg
      from public.team_weeks t
     where t.season = p_season and t.league_id = p_league
       and t.result in ('W', 'L', 'T') and t.points > 0
),
pavg as (
    select lp.player_key, avg(lp.points) as avg_pts
      from public.lineup_players lp
     where lp.season = p_season and lp.league_id = p_league and lp.points > 0
     group by lp.player_key
),
dev as (
    select lp.team_id, lp.week, sum(lp.points - pa.avg_pts) as dev
      from public.lineup_players lp join pavg pa on pa.player_key = lp.player_key
     where lp.season = p_season and lp.league_id = p_league and lp.started
     group by lp.team_id, lp.week
),
gw as (
    select g.*, d.dev, g.points - coalesce(d.dev, 0) as adj,
           count(*) over (partition by g.week) as n,
           rank() over (partition by g.week order by g.points desc) as place
      from g left join dev d on d.team_id = g.team_id and d.week = g.week
),
mine as (select * from gw where gw.team_id = p_team),
ap as (
    select a.week,
           sum(case when b.points < a.points then 1.0
                    when b.points = a.points then 0.5 else 0 end) / (a.n - 1) as ap_real,
           sum(case when b.points < a.adj - 0.000001 then 1.0
                    when abs(b.points - a.adj) <= 0.000001 then 0.5 else 0 end) / (a.n - 1) as ap_adj
      from mine a join gw b on b.week = a.week and b.team_id <> a.team_id
     where a.n > 1
     group by a.week, a.n
),
opp as (
    select distinct on (a.week) a.week, o.team_id as opp_id, o.team_name as opp_name,
           o.team_avg as opp_avg
      from mine a
      join g o on o.week = a.week and o.team_id <> a.team_id
              and o.points = a.opponent_points and o.opponent_points = a.points
     order by a.week, o.team_id collate "C"
),
pl as (
    select lp.player_key, lp.week, lp.points, lp.points - pa.avg_pts as dev
      from public.lineup_players lp join pavg pa on pa.player_key = lp.player_key
     where lp.season = p_season and lp.league_id = p_league
       and lp.team_id = p_team and lp.started
)
select json_build_object(
    'weeks', (select coalesce(json_agg(json_build_object(
                  'week', m.week, 'points', m.points, 'opponent_points', m.opponent_points,
                  'result', m.result, 'place', m.place, 'n', m.n,
                  'opp_id', o.opp_id, 'opp_name', coalesce(o.opp_name, m.opponent_name),
                  'opp_avg', round(o.opp_avg::numeric, 2),
                  'ap_real', round(ap.ap_real::numeric, 3), 'ap_adj', round(ap.ap_adj::numeric, 3),
                  'dev', round(m.dev::numeric, 2)) order by m.week), '[]'::json)
                from mine m
                left join ap on ap.week = m.week
                left join opp o on o.week = m.week),
    'players', (select coalesce(json_agg(x order by x.dev_total, x.player_key collate "C"), '[]'::json)
                  from (select pl.player_key, coalesce(np.name, pl.player_key) as name,
                               np.position, count(*) as starts,
                               round(sum(pl.dev)::numeric, 2) as dev_total,
                               round(avg(pl.dev)::numeric, 2) as dev_avg,
                               round(min(pl.points)::numeric, 2) as worst,
                               round(max(pl.points)::numeric, 2) as best
                          from pl left join public.nfl_players np on np.player_key = pl.player_key
                         group by pl.player_key, np.name, np.position) x)
);
$$;


-- Finding your league by your name on the platform. Exact, case-insensitive
-- matches on manager or team name, latest week only: never a fuzzy search
-- over everybody's teams.
create or replace function public.luck_search(p_season integer, p_name text)
returns json language sql stable as $$
    with lw as (
        select t.league_id, max(t.week) as week from public.team_weeks t
         where t.season = p_season group by t.league_id
    )
    select coalesce(json_agg(json_build_object(
               'league_id', t.league_id, 'team_id', t.team_id,
               'team_name', t.team_name, 'manager', t.manager,
               'league_name', l.league_name, 'public_slug', l.public_slug,
               'provider', t.provider)
               order by l.league_name), '[]'::json)
      from public.team_weeks t
      join lw on lw.league_id = t.league_id and lw.week = t.week
      join public.leagues l on l.id = t.league_id
     where t.season = p_season
       and length(trim(p_name)) >= 2
       and (lower(t.manager) = lower(trim(p_name)) or lower(t.team_name) = lower(trim(p_name)));
$$;
