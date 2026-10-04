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
-- Each week is measured in PPR when it was re-scored with 75% or more of its
-- starters matched (ppr_coverage). Otherwise the league's own points stand in,
-- if its scoring looks standard: the same test as Around the Leagues (029's
-- in_norm): within 30% of its PPR points, or with no PPR, a season average of
-- 40-200. Leagues outside that (defenders, yardage bonuses) are left off
-- rather than ranked wrongly. `own` marks a team measured on its own scoring.
-- (3 Oct: John's league had no usable PPR week and every team was left off.)

create or replace function public.power_rows(p_season integer)
returns table (
    league_id uuid, team_id text, provider text, team_count integer,
    scoring_type text, games bigint, w bigint, l bigint, t bigint,
    ppg numeric, ap_pct numeric, win_pct numeric, ppg_pct numeric,
    score numeric, rank bigint, last_week integer,
    lw integer, lw_pts numeric, lw_beat bigint, lw_pool bigint, own boolean
) language sql stable as $$
with lr as materialized (
    select * from public.luck_rows(p_season)
),
wk0 as (
    select tw.league_id, tw.team_id, tw.week, tw.points,
           case when tw.ppr_points is not null and coalesce(tw.ppr_coverage, 1) >= 0.75
                then tw.ppr_points end as ppr
      from public.team_weeks tw
     where tw.season = p_season and tw.result in ('W', 'L', 'T') and tw.points > 0
),
lg as (
    select wk0.league_id, avg(wk0.points) as raw, avg(wk0.ppr) as ppr
      from wk0 group by wk0.league_id
),
std as (
    select w.league_id, w.team_id, w.week,
           coalesce(w.ppr, w.points) as pts, (w.ppr is null) as own
      from wk0 w
      join lg on lg.league_id = w.league_id
     where w.ppr is not null
        or case when lg.ppr is not null and lg.ppr > 0 then lg.raw / lg.ppr between 0.7 and 1.3
                else lg.raw between 40 and 200 end
),
wk as (
    select std.*,
           percent_rank() over (partition by std.week order by std.pts) as wpct,
           rank() over (partition by std.week order by std.pts) - 1 as beat,
           count(*) over (partition by std.week) as pool
      from std
),
ppr as (
    select wk.league_id, wk.team_id, avg(wk.pts) as ppg, avg(wk.wpct) as nap,
           bool_or(wk.own) as own
      from wk
     group by wk.league_id, wk.team_id
),
-- "You'd have beaten 19,212 of 23,174 teams this week": the team's latest week.
lastwk as (
    select distinct on (wk.league_id, wk.team_id)
           wk.league_id, wk.team_id, wk.week as lw, wk.pts as lw_pts,
           wk.beat as lw_beat, wk.pool as lw_pool
      from wk
     order by wk.league_id, wk.team_id, wk.week desc
),
b as (
    select lr.league_id, lr.team_id, lr.provider, lr.team_count, lr.scoring_type,
           lr.games, lr.w, lr.l, lr.t, p.ppg, p.own,
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
       sc.last_week, sc.lw, sc.lw_pts, sc.lw_beat, sc.lw_pool, sc.own
  from sc;
$$;


-- The whole board, compact: one array per team, best first. The app caches
-- it for an hour (and clears it when a collection finishes). Columns, in
-- order: league_id, team_id, rank, score, ppg, w, l, t, ap_pct, win_pct,
-- ppg_pct, team_count, scoring_type, provider, games, and the latest week's
-- week, score, teams beaten and teams that week (lw, lw_pts, lw_beat, lw_pool),
-- and own (measured on the league's own scoring).
create or replace function public.power_national(p_season integer)
returns json language sql stable as $$
    select json_build_object(
        'teams', count(*),
        'through', max(r.last_week),
        'rows', coalesce(json_agg(json_build_array(
                    r.league_id, r.team_id, r.rank, r.score, r.ppg, r.w, r.l, r.t,
                    r.ap_pct, r.win_pct, r.ppg_pct, r.team_count, r.scoring_type,
                    r.provider, r.games, r.lw, r.lw_pts, r.lw_beat, r.lw_pool, r.own)
                 order by r.rank, r.league_id, r.team_id collate "C"), '[]'::json))
      from public.power_rows(p_season) r;
$$;
