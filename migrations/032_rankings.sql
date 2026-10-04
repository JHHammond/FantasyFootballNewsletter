-- 032: National Rankings (3 Oct 2026). Needs 031 (luck_rows). Re-runnable.
--
-- Every connected team ranked against every other, nationally.
-- (John, 3 Oct: "how your team ranks nationally ... points for, record, and
-- whatever else should be considered".)
--
-- The Power Score, out of 100:
--   50%  points: the team's points a game in plain PPR (team_weeks.ppr_points),
--        as a percentile of every ranked team. PPR so a standard league and a
--        6-point-passing league are measured with the same ruler.
--   30%  national all-play: each week, the share of every ranked team in the
--        country its PPR score beat; averaged over its weeks. The same ruler
--        as points, but week by week, so it rewards being good every week over
--        one huge week. (Was league all-play until 3 Oct: John asked whether a
--        league-relative number belonged in a national ranking. It didn't: it
--        paid teams for beating weak leagues.)
--   20%  record: actual win share, ties as half.
-- Ranked by score, then points a game.
--
-- A team is ranked only when at least one of its weeks was scored in PPR with
-- 75% or more of its starters matched (ppr_coverage). IDP and other leagues
-- we can't put on the PPR ruler are left off rather than ranked wrongly.

create or replace function public.power_rows(p_season integer)
returns table (
    league_id uuid, team_id text, provider text, team_count integer,
    scoring_type text, games bigint, w bigint, l bigint, t bigint,
    ppg numeric, ap_pct numeric, win_pct numeric, ppg_pct numeric,
    score numeric, rank bigint, last_week integer,
    lw integer, lw_pts numeric, lw_beat bigint, lw_pool bigint
) language sql stable as $$
with lr as materialized (
    select * from public.luck_rows(p_season)
),
wk as (
    select tw.league_id, tw.team_id, tw.week, tw.ppr_points,
           percent_rank() over (partition by tw.week order by tw.ppr_points) as wpct,
           rank() over (partition by tw.week order by tw.ppr_points) - 1 as beat,
           count(*) over (partition by tw.week) as pool
      from public.team_weeks tw
     where tw.season = p_season and tw.result in ('W', 'L', 'T') and tw.points > 0
       and tw.ppr_points is not null and coalesce(tw.ppr_coverage, 1) >= 0.75
),
ppr as (
    select wk.league_id, wk.team_id, avg(wk.ppr_points) as ppg, avg(wk.wpct) as nap
      from wk
     group by wk.league_id, wk.team_id
),
-- "You'd have beaten 19,212 of 23,174 teams this week": the team's latest week.
lastwk as (
    select distinct on (wk.league_id, wk.team_id)
           wk.league_id, wk.team_id, wk.week as lw, wk.ppr_points as lw_pts,
           wk.beat as lw_beat, wk.pool as lw_pool
      from wk
     order by wk.league_id, wk.team_id, wk.week desc
),
b as (
    select lr.league_id, lr.team_id, lr.provider, lr.team_count, lr.scoring_type,
           lr.games, lr.w, lr.l, lr.t, p.ppg,
           p.nap as ap_pct,
           lr.wins / lr.games as win_pct,
           lr.last_week, lw.lw, lw.lw_pts, lw.lw_beat, lw.lw_pool
      from lr
      join ppr p on p.league_id = lr.league_id and p.team_id = lr.team_id
      join lastwk lw on lw.league_id = lr.league_id and lw.team_id = lr.team_id
     where lr.games > 0
),
s as (
    select b.*, percent_rank() over (order by b.ppg) as ppg_pct from b
),
sc as (
    select s.*,
           round((100 * (0.5 * s.ppg_pct + 0.3 * s.ap_pct + 0.2 * s.win_pct))::numeric, 1) as score
      from s
)
select sc.league_id, sc.team_id, sc.provider, sc.team_count, sc.scoring_type,
       sc.games, sc.w, sc.l, sc.t,
       round(sc.ppg::numeric, 2), round(sc.ap_pct::numeric, 3),
       round(sc.win_pct::numeric, 3), round(sc.ppg_pct::numeric, 4),
       sc.score,
       rank() over (order by sc.score desc, sc.ppg desc),
       sc.last_week, sc.lw, sc.lw_pts, sc.lw_beat, sc.lw_pool
  from sc;
$$;


-- The whole board, compact: one array per team, best first. The app caches
-- it for an hour (and clears it when a collection finishes). Columns, in
-- order: league_id, team_id, rank, score, ppg, w, l, t, ap_pct, win_pct,
-- ppg_pct, team_count, scoring_type, provider, games, and the latest week's
-- week, PPR score, teams beaten and teams that week (lw, lw_pts, lw_beat, lw_pool).
create or replace function public.power_national(p_season integer)
returns json language sql stable as $$
    select json_build_object(
        'teams', count(*),
        'through', max(r.last_week),
        'rows', coalesce(json_agg(json_build_array(
                    r.league_id, r.team_id, r.rank, r.score, r.ppg, r.w, r.l, r.t,
                    r.ap_pct, r.win_pct, r.ppg_pct, r.team_count, r.scoring_type,
                    r.provider, r.games, r.lw, r.lw_pts, r.lw_beat, r.lw_pool)
                 order by r.rank, r.league_id, r.team_id collate "C"), '[]'::json))
      from public.power_rows(p_season) r;
$$;
