-- 068: The Undefeated Watch (29 Oct 2026). Needs 029, 045, 050, 055, 062, 067.
-- Re-runnable.
--
-- John: "build something that tracks what 6-0 players do to try and
-- identify interesting trends and present the data." Analytical and
-- entertaining: what the unbeaten do differently, and, late in the season,
-- just how few are left.
--
-- undefeated_live(season) adds up the whole country in one JSON object:
-- the countdown (teams still perfect after each week), unbeaten against
-- winless on everything we hold (scoring, schedule, lineups, trades, the
-- wire, where their points came from, the draft slot and the opening
-- build), the players they share, and a card per unbeaten team once few
-- are left. The rules — who counts, which week, the abandoned-roster
-- filter — are written out at the top of web/undefeated.py, whose
-- compute() is this function's Python twin. Change one, change both, and
-- run `python -m scripts.check_undefeated_sql` (it loads this file).
--
-- SAVED, NOT LIVE. This reads every lineup of the season, so it is far past
-- the API's 8-second limit by mid-season (see saved-copies-and-timeouts.md).
-- refresh_undefeated() runs it from pg_cron with a 10-minute limit and keeps
-- the answer in undefeated_saved, recomputing only when the data underneath
-- has changed (a cheap fingerprint). The site reads the saved row and calls
-- the live function only before the first save.
--
-- Run it from the SQL editor in one go: the first line lifts the editor's
-- time limit, because the last line does the first save.

set statement_timeout = '10min';

create or replace function public.undefeated_live(p_season integer)
returns json language plpgsql volatile as $$
-- plpgsql with temporary tables, not one big SQL statement: as one query
-- the planner guessed 1-2 rows for every intermediate result and nested its
-- loops (four minutes on 150 leagues); with enable_nestloop off it took five
-- seconds just to PLAN. Each step is materialised and ANALYZEd, so every
-- later join is planned on real row counts. The tables go at commit.
declare
    out json;
begin
    perform set_config('client_min_messages', 'warning', true);
    drop table if exists pg_temp.dyn, pg_temp.allrows, pg_temp.tw, pg_temp.per_week, pg_temp.th, pg_temp.weeks_of, pg_temp.pool, pg_temp.rws, pg_temp.nowr, pg_temp.lwr, pg_temp.hist, pg_temp.fielded, pg_temp.gone, pg_temp.firstloss, pg_temp.closest, pg_temp.biggest, pg_temp.agg, pg_temp.names, pg_temp.tm, pg_temp.tg, pg_temp.trade_n, pg_temp.full_wire, pg_temp.wadds, pg_temp.faab_leagues, pg_temp.wire_n, pg_temp.dp, pg_temp.draft_leagues, pg_temp.tgot, pg_temp.starts, pg_temp.src, pg_temp.tf, pg_temp.roster, pg_temp.dteam, pg_temp.wteam;
-- ---------------------------------------------------------------- who counts
    create temp table dyn on commit drop as
    select public.dynasty_league_ids(p_season) as id;
    analyze dyn;
    create temp table allrows on commit drop as
    select * from public.team_weeks
     where season = p_season and week between 1 and 14;
    analyze allrows;
    create temp table tw on commit drop as
    select * from allrows where league_id not in (select id from dyn);
    analyze tw;
    create temp table per_week on commit drop as
    select week, count(distinct league_id) as n
      from tw where result in ('W', 'L', 'T') group by week;
    analyze per_week;
    create temp table th on commit drop as
    select max(week) as through from per_week
     where n * 10 >= (select max(n) from per_week) * 9;
    analyze th;
    create temp table weeks_of on commit drop as
    select t.league_id,
           count(distinct t.week)                                    as wk,
           count(distinct t.team_id) filter (where t.week = th.through) as nteams
      from tw t, th
     where t.week <= th.through
     group by t.league_id;
    analyze weeks_of;
    create temp table pool on commit drop as
    select w.league_id from weeks_of w, th
     where w.wk = th.through and w.nteams >= 4;
    analyze pool;
    create temp table rws on commit drop as
    select t.* from tw t, th
     where t.league_id in (select league_id from pool) and t.week <= th.through;
    analyze rws;
    create temp table nowr on commit drop as
    select r.* from rws r, th where r.week = th.through;
    analyze nowr;
-- Every score in each league-week, for the average and for all-play.
    create temp table lwr on commit drop as
    select league_id, week, team_id, points,
           avg(points)  over (partition by league_id, week)             as lg_avg,
           count(*)     over (partition by league_id, week)             as n,
           (rank()      over (partition by league_id, week order by points)) - 1 as lower,
           count(*)     over (partition by league_id, week, points)     as same
      from rws where result in ('W', 'L', 'T');
    analyze lwr;
    create temp table hist on commit drop as
    select r.*, l.lg_avg, l.n, l.lower, l.same
      from rws r
      join nowr n on n.league_id = r.league_id and n.team_id = r.team_id
      left join lwr l on l.league_id = r.league_id and l.week = r.week
                     and l.team_id = r.team_id;
    analyze hist;
-- Who turned up (053/055): under half the starters taking the field in any
-- week makes an abandoned roster.
    create temp table fielded on commit drop as
    select l.league_id, l.team_id, l.week,
           count(*) as starters,
           count(*) filter (where nw.played is true
                                   or coalesce(l.ppr_points, l.points) > 0) as showed
      from public.lineup_players l
      cross join th
      left join public.nfl_week_players nw
        on nw.season = p_season and nw.week = l.week and nw.player_key = l.player_key
     where l.season = p_season and l.week between 1 and th.through and l.started
       and l.league_id in (select league_id from pool)
     group by l.league_id, l.team_id, l.week;
    analyze fielded;
    create temp table gone on commit drop as
    select distinct league_id, team_id from fielded
     where starters > 0 and showed * 2 < starters;
    analyze gone;
    create temp table firstloss on commit drop as
    select distinct on (league_id, team_id)
           league_id, team_id, week, points, ppr_points, opponent_points, margin
      from hist where result in ('L', 'T')
     order by league_id, team_id, week;
    analyze firstloss;
    create temp table closest on commit drop as
    select distinct on (league_id, team_id) league_id, team_id, margin, week
      from hist where result = 'W' and margin is not null
     order by league_id, team_id, margin, week;
    analyze closest;
    create temp table biggest on commit drop as
    select distinct on (league_id, team_id) league_id, team_id, margin
      from hist where result = 'W' and margin is not null
     order by league_id, team_id, margin desc, week;
    analyze biggest;
    create temp table agg on commit drop as
    select league_id, team_id,
           count(*) filter (where result = 'W') as w,
           count(*) filter (where result = 'L') as l,
           count(*) filter (where result = 'T') as t,
           avg(ppr_points) filter (where result in ('W', 'L', 'T') and ppr_points > 0) as ppg,
           coalesce(sum(points) filter (where result in ('W', 'L', 'T')), 0)         as pf,
           sum(opponent_points) filter (where result in ('W', 'L', 'T')
                                          and opponent_points is not null)          as pa,
           sum(lg_avg) filter (where result in ('W', 'L', 'T')
                                 and opponent_points is not null)                   as pa_den,
           avg((lower + 0.5 * (same - 1)) / (n - 1))
               filter (where result in ('W', 'L', 'T') and n > 1)                   as allplay,
           avg(bench_left) filter (where result in ('W', 'L', 'T') and bench_left is not null) as bench,
           avg(case when bench_left <= 0.005 then 1.0 else 0.0 end)
               filter (where result in ('W', 'L', 'T') and bench_left is not null)  as perfect
      from hist group by league_id, team_id;
    analyze agg;
    create temp table names on commit drop as
    select distinct league_id, team_id, team_name
      from hist where team_name is not null;
    analyze names;
    create temp table tm on commit drop as
    select n.league_id, n.team_id, n.team_name, n.manager, n.provider,
           n.team_count, n.scoring_type,
           a.w, a.l, a.t, a.ppg, a.pf, a.allplay, a.bench, a.perfect,
           case when a.pa_den > 0 then a.pa / a.pa_den - 1 end as pa_rel,
           f.week as first_loss, f.points as f_points, f.ppr_points as f_ppr,
           f.opponent_points as f_opp, f.margin as f_margin,
           c.margin as closest, c.week as closest_week, b.margin as biggest,
           (g.team_id is not null) as abandoned,
           rank() over (partition by n.league_id order by a.pf desc) as pf_rank
      from nowr n
      join agg a on a.league_id = n.league_id and a.team_id = n.team_id
      left join firstloss f on f.league_id = n.league_id and f.team_id = n.team_id
      left join closest c on c.league_id = n.league_id and c.team_id = n.team_id
      left join biggest b on b.league_id = n.league_id and b.team_id = n.team_id
      left join gone g on g.league_id = n.league_id and g.team_id = n.team_id;
    analyze tm;
    create temp table tg on commit drop as
    select tm.*,
           (first_loss is null and w >= 1)                              as is_u,
           (w = 0 and t = 0 and l >= 1 and not abandoned)               as is_w,
           (not abandoned or (first_loss is null and w >= 1))           as is_a
      from tm;
    analyze tg;
-- ------------------------------------------------- trades and the wire
    create temp table trade_n on commit drop as
    select nm.league_id, nm.team_id, count(distinct tr.trade_key) as trades
      from public.trades tr
      cross join th
      cross join lateral jsonb_array_elements_text(tr.teams) as x(name)
      join names nm on nm.league_id = tr.league_id and nm.team_name = x.name
     where tr.season = p_season and tr.week <= th.through
       and tr.league_id in (select league_id from pool)
     group by nm.league_id, nm.team_id;
    analyze trade_n;
    create temp table full_wire on commit drop as
    select s.league_id from public.waiver_scans s, th
     where s.season = p_season and s.week between 1 and th.through
       and s.league_id in (select league_id from pool)
     group by s.league_id, th.through
    having count(distinct s.week) = th.through;
    analyze full_wire;
    create temp table wadds on commit drop as
    select v.*, nm.team_id
      from public.waivers v
      cross join th
      join names nm on nm.league_id = v.league_id and nm.team_name = v.team_name
     where v.season = p_season and v.week <= th.through and v.action = 'add'
       and v.league_id in (select league_id from full_wire);
    analyze wadds;
    create temp table faab_leagues on commit drop as
    select distinct v.league_id from public.waivers v, th
     where v.season = p_season and v.week <= th.through and v.action = 'add'
       and v.status = 'complete' and coalesce(v.bid, 0) > 0
       and v.league_id in (select league_id from full_wire);
    analyze faab_leagues;
    create temp table wire_n on commit drop as
    select league_id, team_id,
           count(*) filter (where status = 'complete')                    as adds,
           count(*) filter (where status = 'failed')                      as failed,
           coalesce(sum(coalesce(bid, 0)) filter (where status = 'complete'), 0) as faab
      from wadds group by league_id, team_id;
    analyze wire_n;
-- ------------------------------------- where the starting points came from
    create temp table dp on commit drop as
    select d.* from public.draft_picks d
     where d.season = p_season and d.league_id in (select league_id from pool);
    analyze dp;
    create temp table draft_leagues on commit drop as
    select distinct league_id from dp;
    analyze draft_leagues;
-- Each player a team traded FOR, and the first week it had him. Joined by
-- team name, as the trade rows carry names, not ids.
    create temp table tgot on commit drop as
    select tp.league_id, nm.team_id, tp.player_key, min(tp.week) as wk
      from public.trade_players tp
      join names nm on nm.league_id = tp.league_id and nm.team_name = tp.to_team
     where tp.season = p_season and tp.league_id in (select league_id from pool)
     group by tp.league_id, nm.team_id, tp.player_key;
    analyze tgot;
    create temp table starts on commit drop as
    select l.league_id, l.team_id, l.week, l.player_key, coalesce(l.points, 0) as pts
      from public.lineup_players l
      cross join th
      join tg on tg.league_id = l.league_id and tg.team_id = l.team_id
     where l.season = p_season and l.week between 1 and th.through and l.started
       and l.league_id in (select league_id from draft_leagues);
    analyze starts;
    create temp table src on commit drop as
    select s.league_id, s.team_id,
           sum(s.pts) filter (where d.team_id = s.team_id)                      as drafted,
           sum(s.pts) filter (where d.team_id is distinct from s.team_id
                                and g.wk <= s.week)                              as traded,
           sum(s.pts)                                                            as total
      from starts s
      left join dp d on d.league_id = s.league_id and d.player_key = s.player_key
      left join tgot g on g.league_id = s.league_id and g.team_id = s.team_id
                      and g.player_key = s.player_key
     group by s.league_id, s.team_id;
    analyze src;
    create temp table tf on commit drop as
    select tg.*,
           coalesce(trn.trades, 0) as trades,
           case when tg.league_id in (select league_id from full_wire)
                then coalesce(wn.adds, 0) end   as adds,
           case when tg.league_id in (select league_id from full_wire)
                then coalesce(wn.failed, 0) end as failed,
           case when tg.league_id in (select league_id from faab_leagues)
                then coalesce(wn.faab, 0) end   as faab,
           case when s.total > 0 then coalesce(s.drafted, 0) / s.total end as s_drafted,
           case when s.total > 0 then coalesce(s.traded, 0) / s.total end  as s_traded,
           case when s.total > 0
                then (s.total - coalesce(s.drafted, 0) - coalesce(s.traded, 0)) / s.total end as s_wire
      from tg
      left join trade_n trn on trn.league_id = tg.league_id and trn.team_id = tg.team_id
      left join wire_n wn   on wn.league_id = tg.league_id and wn.team_id = tg.team_id
      left join src s       on s.league_id = tg.league_id and s.team_id = tg.team_id;
    analyze tf;
-- ------------------------------------------------------------------ players
    create temp table roster on commit drop as
    select l.league_id, l.team_id, l.player_key, tg.is_u, tg.is_w, tg.is_a
      from public.lineup_players l
      cross join th
      join tg on tg.league_id = l.league_id and tg.team_id = l.team_id
     where l.season = p_season and l.week = th.through;
    analyze roster;
    create temp table dteam on commit drop as
    select d.league_id, d.team_id, d.player_key, d.overall, d.team_count,
           tg.is_u, tg.is_w, tg.is_a
      from dp d join tg on tg.league_id = d.league_id and tg.team_id = d.team_id
     where d.team_id is not null;
    analyze dteam;
    create temp table wteam on commit drop as
    select distinct w.league_id, w.team_id, w.player_key, tg.is_u, tg.is_w, tg.is_a
      from wadds w join tg on tg.league_id = w.league_id and tg.team_id = w.team_id
     where w.status = 'complete';
    analyze wteam;
    if (select through from th) is null then
        return null;
    end if;
    with
gset as (
    select 'u' as g, tf.* from tf where is_u
    union all select 'w', tf.* from tf where is_w
    union all select 'a', tf.* from tf where is_a
),
tape as (
    select g, json_build_object(
        'n', count(*),
        'ppg', round(avg(ppg), 2),
        'pa_rel', round(avg(pa_rel), 4),
        'allplay', round(avg(allplay), 4),
        'bench', round(avg(bench), 2),
        'perfect', round(avg(perfect), 4),
        'trades', round(avg(trades), 2),
        'adds', round(avg(adds), 2),
        'failed', round(avg(failed), 2),
        'faab', round(avg(faab), 2),
        'drafted', round(avg(s_drafted), 4),
        'traded', round(avg(s_traded), 4),
        'wire', round(avg(s_wire), 4),
        'with_wire', count(adds),
        'with_draft', count(s_drafted)) as j
      from gset group by g
),
-- ---------------------------------------------------------------- draft day
picks as (
    select d.league_id, d.team_id, d.overall, d.team_count,
           upper(coalesce(d.position, '')) as pos
      from dp d join tg on tg.league_id = d.league_id and tg.team_id = d.team_id
     where d.team_id is not null and d.overall > 0
),
firstpick as (
    select distinct on (league_id, team_id) league_id, team_id, overall,
           coalesce(nullif(team_count, 0), 12) as tc
      from picks order by league_id, team_id, overall
),
slot_of as (
    select league_id, team_id,
           least(12, greatest(1, round((overall - 1) * 12.0 / tc + 1, 0)))::int as slot
      from firstpick where overall <= tc
),
slots as (
    select s.slot, count(*) as n,
           count(*) filter (where tg.is_u) as u,
           count(*) filter (where tg.is_w) as w
      from slot_of s join tg on tg.league_id = s.league_id and tg.team_id = s.team_id
     where tg.is_a
     group by s.slot
),
shape as (
    select league_id, team_id, array_agg(pos order by overall) as p
      from picks group by league_id, team_id having count(*) >= 8
),
build_of as (
    select s.league_id, s.team_id, tg.is_u, tg.is_w,
           (p[1] = 'RB' and p[2] = 'RB')                                     as rb_rb,
           ((p[1] = 'RB' and p[2] = 'WR') or (p[1] = 'WR' and p[2] = 'RB'))  as rb_wr,
           (p[1] = 'WR' and p[2] = 'WR')                                     as wr_wr,
           not ('RB' = any (p[1:4]))                                          as zero_rb,
           coalesce(array_position(p, 'QB') <= 3, false)                      as early_qb,
           coalesce(array_position(p, 'QB') >= 8, true)                       as late_qb,
           coalesce(array_position(p, 'TE') <= 3, false)                      as early_te,
           p[1] as r1
      from shape s join tg on tg.league_id = s.league_id and tg.team_id = s.team_id
     where tg.is_a
),
nb as (
    select count(*) filter (where is_u) as u, count(*) filter (where is_w) as w,
           count(*) as a from build_of
),
flags as (
    select b.ord, b.key, x.*
      from (values (1, 'rb_rb'), (2, 'rb_wr'), (3, 'wr_wr'), (4, 'zero_rb'),
                   (5, 'early_qb'), (6, 'late_qb'), (7, 'early_te')) b(ord, key)
      cross join lateral (
        select count(*) as n, count(*) filter (where is_u) as u,
               count(*) filter (where is_w) as w
          from build_of
         where case b.key when 'rb_rb' then rb_rb when 'rb_wr' then rb_wr
                          when 'wr_wr' then wr_wr when 'zero_rb' then zero_rb
                          when 'early_qb' then early_qb when 'late_qb' then late_qb
                          else early_te end) x
),
firsts as (
    select b.ord, b.key, x.*
      from (values (1, 'QB'), (2, 'RB'), (3, 'WR'), (4, 'TE')) b(ord, key)
      cross join lateral (
        select count(*) as n, count(*) filter (where is_u) as u,
               count(*) filter (where is_w) as w
          from build_of where r1 = b.key) x
),
den_r as (
    select count(*) filter (where is_u) as u, count(*) filter (where is_w) as w,
           count(*) filter (where is_a) as a
      from (select distinct league_id, team_id, is_u, is_w, is_a from roster) x
),
den_d as (
    select count(*) filter (where is_u) as u, count(*) filter (where is_w) as w,
           count(*) filter (where is_a) as a
      from (select distinct league_id, team_id, is_u, is_w, is_a from dteam) x
),
den_w as (
    select count(*) filter (where is_u) as u, count(*) filter (where is_w) as w,
           count(*) filter (where is_a) as a
      from tg where league_id in (select league_id from full_wire)
),
holders as (
    select 'rostered' as board, player_key,
           count(*) filter (where is_a) as na, count(*) filter (where is_u) as nu,
           count(*) filter (where is_w) as nw, null::numeric as adp
      from roster group by player_key
    union all
    select 'drafted', player_key,
           count(*) filter (where is_a), count(*) filter (where is_u),
           count(*) filter (where is_w),
           round(avg((overall - 1) * 12.0 / coalesce(nullif(team_count, 0), 12) + 1)
                 filter (where is_a and overall > 0), 1)
      from dteam group by player_key
    union all
    select 'wire', player_key,
           count(*) filter (where is_a), count(*) filter (where is_u),
           count(*) filter (where is_w), null
      from wteam group by player_key
),
pl as (
    select h.*, coalesce(nullif(np.name, ''), h.player_key) as name, np.position, np.nfl_team,
           d.u as du, d.w as dw, d.a as da
      from holders h
      join public.nfl_players np on np.player_key = h.player_key
      join (select 'rostered' as board, * from den_r
            union all select 'drafted', * from den_d
            union all select 'wire', * from den_w) d on d.board = h.board
     where np.position in ('QB', 'RB', 'WR', 'TE') and h.na > 0
),
pcard as (
    select pl.*, json_strip_nulls(json_build_object(
        'player_key', player_key, 'name', name, 'position', position,
        'nfl_team', nfl_team, 'n_a', na, 'n_u', nu, 'n_w', nw,
        'share_u', round(nu::numeric / nullif(du, 0), 4),
        'share_w', round(nw::numeric / nullif(dw, 0), 4),
        'share_a', round(na::numeric / nullif(da, 0), 4),
        'lift', round((nu::numeric / nullif(du, 0)) / nullif(na::numeric / nullif(da, 0), 0), 2),
        'lift_w', round((nw::numeric / nullif(dw, 0)) / nullif(na::numeric / nullif(da, 0), 0), 2),
        'adp', adp)) as c
      from pl
),
-- ---------------------------------------------------- the wire, this week
hot_rows as (
    select v.* from public.waivers v, th
     where v.season = p_season and v.action = 'add' and v.week <= th.through + 1
       and v.league_id not in (select id from dyn)
),
hot_week as (select max(week) as wk from hot_rows),
hot as (
    select v.player_key,
           count(distinct (v.league_id, v.move_key)) filter (where v.status = 'complete') as adds,
           count(*) filter (where v.status = 'failed')                                 as failed,
           round(avg(v.bid) filter (where v.status = 'complete' and coalesce(v.bid, 0) > 0), 1) as avg_bid,
           count(*) filter (where v.status = 'complete' and coalesce(v.bid, 0) > 0)    as bids,
           max(v.name) as rname, max(v.position) as rpos, max(v.nfl_team) as rteam
      from hot_rows v, hot_week h where v.week = h.wk
     group by v.player_key
),
hotj as (
    select json_build_object(
        'player_key', h.player_key,
        'name', coalesce(nullif(np.name, ''), h.rname, h.player_key),
        'position', coalesce(np.position, h.rpos),
        'nfl_team', coalesce(np.nfl_team, h.rteam),
        'adds', h.adds, 'failed', h.failed, 'avg_bid', h.avg_bid, 'bids', h.bids) as c,
           h.adds, h.failed, h.player_key
      from hot h left join public.nfl_players np on np.player_key = h.player_key
     where h.adds > 0
),
-- ------------------------------------- the unbeaten draft, round by round
-- A team's k-th pick is its round k, whatever the platform numbered it.
board_picks as (
    select d.league_id, d.team_id, d.player_key, d.name as dname, d.position as dpos,
           case when upper(coalesce(d.position, '')) in ('QB', 'RB', 'WR', 'TE', 'K')
                     then upper(d.position)
                when upper(coalesce(d.position, '')) in ('DEF', 'DST', 'D/ST') then 'DEF'
                else 'OTHER' end as pos,
           tg.is_u,
           row_number() over (partition by d.league_id, d.team_id
                              order by d.overall, d.player_key collate "C") as rnd
      from dp d join tg on tg.league_id = d.league_id and tg.team_id = d.team_id
     where d.team_id is not null and d.overall > 0 and tg.is_a
),
board_rounds as (
    select rnd, count(*) filter (where is_u) as n_u, count(*) as n_a
      from board_picks where rnd <= 15 group by rnd
),
board_mix as (
    select rnd, pos, count(*) filter (where is_u) as u, count(*) as a
      from board_picks where rnd <= 15 group by rnd, pos
),
board_pl as (
    select b.rnd, b.player_key, count(*) filter (where b.is_u) as u, count(*) as a,
           max(nullif(b.dname, '')) as dname, max(b.dpos) as dpos
      from board_picks b where b.rnd <= 15 group by b.rnd, b.player_key
),
board_plr as (
    select x.*, row_number() over (partition by x.rnd
                                   order by x.u desc, x.a desc, x.player_key collate "C") as k
      from board_pl x where x.u >= 1
),
-- -------------------------------------------------------------- the cards
tcard as (
    select tf.*, json_build_object(
        'league_id', league_id, 'team_id', team_id, 'team_name', team_name,
        'manager', manager, 'provider', provider, 'team_count', team_count,
        'scoring_type', scoring_type, 'w', w, 'l', l, 't', t, 'pf_rank', pf_rank,
        'closest_week', closest_week, 'first_loss', first_loss,
        'ppg', round(ppg, 2), 'pf', round(pf, 2), 'allplay', round(allplay, 4),
        'closest', round(closest, 2), 'biggest', round(biggest, 2),
        'fell_points', round(f_points, 2), 'fell_ppr', round(f_ppr, 2),
        'fell_opp', round(f_opp, 2), 'fell_margin', round(f_margin, 2)) as c,
           round(coalesce(allplay, 0), 6) as ap6, round(coalesce(ppg, 0), 4) as ppg4,
           case when f_ppr is not null and f_ppr > 0 then f_ppr else coalesce(f_points, 0) end as f_std
      from tf
),
anyu as (select exists (select 1 from tg where is_u) as yes),
lastw as (select max(first_loss) as wk from tg)
select json_build_object(
    'season', p_season,
    'through', (select through from th),
    'summary', json_build_object(
        'leagues', (select count(*) from pool),
        'teams', (select count(*) from tg),
        'field', (select count(*) from tg where is_a),
        'unbeaten', (select count(*) from tg where is_u),
        'winless', (select count(*) from tg where is_w),
        'winless_all', (select count(*) from tg where w = 0 and t = 0 and l >= 1),
        'abandoned', (select count(*) from tg where abandoned),
        'dynasty_left', (select count(distinct league_id) from allrows
                          where league_id in (select id from dyn)),
        'incomplete_left', (select count(*) from weeks_of) - (select count(*) from pool),
        'wire_leagues', (select count(*) from full_wire),
        'draft_leagues', (select count(*) from draft_leagues),
        'extinct', (select case when a.yes then null else l.wk end from anyu a, lastw l)),
    'survival', (select coalesce(json_agg(json_build_object(
                    'week', k,
                    'alive', (select count(*) from tg where first_loss is null or first_loss > k),
                    'fell', (select count(*) from tg where first_loss = k),
                    'close', (select count(*) from tg where first_loss = k
                                and f_margin is not null and f_margin > -3.0)) order by k), '[]'::json)
                   from th, generate_series(1, th.through) k),
    'tape', (select json_build_object(
                'u', coalesce((select j from tape where g = 'u'), json_build_object('n', 0)),
                'w', coalesce((select j from tape where g = 'w'), json_build_object('n', 0)),
                'a', coalesce((select j from tape where g = 'a'), json_build_object('n', 0)))),
    'slots', (select json_agg(json_build_object(
                'slot', s, 'n', coalesce(x.n, 0), 'u', coalesce(x.u, 0), 'w', coalesce(x.w, 0),
                'rate', round(x.u::numeric / nullif(x.n, 0), 4),
                'rate_w', round(x.w::numeric / nullif(x.n, 0), 4)) order by s)
                from generate_series(1, 12) s left join slots x on x.slot = s),
    'builds', (select json_agg(json_build_object(
                'key', key, 'n', f.n, 'u', f.u, 'w', f.w,
                'share_u', round(f.u::numeric / nullif(nb.u, 0), 4),
                'share_w', round(f.w::numeric / nullif(nb.w, 0), 4),
                'share_a', round(f.n::numeric / nullif(nb.a, 0), 4),
                'rate', round(f.u::numeric / nullif(f.n, 0), 4)) order by ord)
                 from flags f, nb),
    'first_pick', (select json_agg(json_build_object(
                'key', key, 'n', f.n, 'u', f.u, 'w', f.w,
                'share_u', round(f.u::numeric / nullif(nb.u, 0), 4),
                'share_w', round(f.w::numeric / nullif(nb.w, 0), 4),
                'share_a', round(f.n::numeric / nullif(nb.a, 0), 4),
                'rate', round(f.u::numeric / nullif(f.n, 0), 4)) order by ord)
                 from firsts f, nb),
    'players', json_build_object(
        'rostered', (select json_build_object(
            'thread', (select coalesce(json_agg(c order by k1 desc, nu desc, player_key collate "C"), '[]'::json)
                         from (select c, nu, player_key, nu::numeric / na as k1 from pcard
                                where board = 'rostered' and nu >= 3 and na >= 30
                                  and du > 0 and nu::numeric / du >= 0.08
                                order by nu::numeric / na desc, nu desc, player_key collate "C" limit 12) x),
            'most', (select coalesce(json_agg(c order by nu desc, k1 desc, player_key collate "C"), '[]'::json)
                       from (select c, nu, player_key, nu::numeric / na as k1 from pcard
                              where board = 'rostered' and nu >= 1
                              order by nu desc, nu::numeric / na desc, player_key collate "C" limit 12) x),
            'kryptonite', (select coalesce(json_agg(c order by k1 desc, nw desc, player_key collate "C"), '[]'::json)
                             from (select c, nw, player_key, nw::numeric / na as k1 from pcard
                                    where board = 'rostered' and nw >= 3 and na >= 30
                                      and dw > 0 and nw::numeric / dw >= 0.08
                                    order by nw::numeric / na desc, nw desc, player_key collate "C" limit 8) x))),
        'drafted', (select json_build_object(
            'thread', (select coalesce(json_agg(c order by k1 desc, nu desc, player_key collate "C"), '[]'::json)
                         from (select c, nu, player_key, nu::numeric / na as k1 from pcard
                                where board = 'drafted' and nu >= 3 and na >= 30
                                  and du > 0 and nu::numeric / du >= 0.08
                                order by nu::numeric / na desc, nu desc, player_key collate "C" limit 12) x),
            'most', (select coalesce(json_agg(c order by nu desc, k1 desc, player_key collate "C"), '[]'::json)
                       from (select c, nu, player_key, nu::numeric / na as k1 from pcard
                              where board = 'drafted' and nu >= 1
                              order by nu desc, nu::numeric / na desc, player_key collate "C" limit 12) x),
            'kryptonite', (select coalesce(json_agg(c order by k1 desc, nw desc, player_key collate "C"), '[]'::json)
                             from (select c, nw, player_key, nw::numeric / na as k1 from pcard
                                    where board = 'drafted' and nw >= 3 and na >= 30
                                      and dw > 0 and nw::numeric / dw >= 0.08
                                    order by nw::numeric / na desc, nw desc, player_key collate "C" limit 8) x))),
        'wire', (select json_build_object(
            'thread', (select coalesce(json_agg(c order by k1 desc, nu desc, player_key collate "C"), '[]'::json)
                         from (select c, nu, player_key, nu::numeric / na as k1 from pcard
                                where board = 'wire' and nu >= 3 and na >= 30
                                  and du > 0 and nu::numeric / du >= 0.08
                                order by nu::numeric / na desc, nu desc, player_key collate "C" limit 12) x),
            'most', (select coalesce(json_agg(c order by nu desc, k1 desc, player_key collate "C"), '[]'::json)
                       from (select c, nu, player_key, nu::numeric / na as k1 from pcard
                              where board = 'wire' and nu >= 1
                              order by nu desc, nu::numeric / na desc, player_key collate "C" limit 12) x),
            'kryptonite', (select coalesce(json_agg(c order by k1 desc, nw desc, player_key collate "C"), '[]'::json)
                             from (select c, nw, player_key, nw::numeric / na as k1 from pcard
                                    where board = 'wire' and nw >= 3 and na >= 30
                                      and dw > 0 and nw::numeric / dw >= 0.08
                                    order by nw::numeric / na desc, nw desc, player_key collate "C" limit 8) x)))),
    'draft_board', (select coalesce(json_agg(json_build_object(
        'round', r.rnd, 'n_u', r.n_u, 'n_a', r.n_a,
        'mix_u', coalesce((select json_object_agg(m.pos, m.u) from board_mix m
                            where m.rnd = r.rnd and m.u > 0), '{}'::json),
        'mix_a', coalesce((select json_object_agg(m.pos, m.a) from board_mix m
                            where m.rnd = r.rnd and m.a > 0), '{}'::json),
        'players', coalesce((select json_agg(json_build_object(
                        'player_key', p.player_key,
                        'name', coalesce(nullif(np.name, ''), p.dname, p.player_key),
                        'position', coalesce(np.position, p.dpos),
                        'nfl_team', np.nfl_team, 'u', p.u, 'a', p.a) order by p.k)
                        from board_plr p
                        left join public.nfl_players np on np.player_key = p.player_key
                       where p.rnd = r.rnd and p.k <= 5), '[]'::json))
        order by r.rnd), '[]'::json) from board_rounds r),
    'hot', json_build_object(
        'week', (select wk from hot_week),
        'players', (select coalesce(json_agg(c order by adds desc, failed desc, player_key collate "C"), '[]'::json)
                      from (select * from hotj order by adds desc, failed desc,
                                                        player_key collate "C" limit 10) x)),
    'verdicts', json_build_object(
        'real', (select count(*) from tg where is_u and allplay >= 0.75),
        'middle', (select count(*) from tg where is_u and allplay >= 0.5 and allplay < 0.75),
        'pretender', (select count(*) from tg where is_u and allplay < 0.5),
        'led_league', (select count(*) from tg where is_u and pf_rank = 1)),
    'cards', (select coalesce(json_agg(c order by ap6 desc, ppg4 desc,
                                         league_id::text collate "C", team_id collate "C"), '[]'::json)
                from (select c, ap6, ppg4, league_id, team_id from tcard, anyu, lastw
                       where (anyu.yes and is_u) or (not anyu.yes and first_loss = lastw.wk)
                       order by ap6 desc, ppg4 desc, league_id::text collate "C", team_id collate "C"
                       limit 100) x),
    'pretenders', (select coalesce(json_agg(c order by ap6, ppg4,
                                              league_id::text collate "C", team_id collate "C"), '[]'::json)
                     from (select c, ap6, ppg4, league_id, team_id from tcard
                            where is_u and allplay is not null
                            order by ap6, ppg4, league_id::text collate "C", team_id collate "C"
                            limit 5) x),
    'fallen', json_build_object(
        'n', (select count(*) from tg, th where first_loss = th.through),
        'close', (select count(*) from tg, th where first_loss = th.through
                    and f_margin is not null and f_margin > -3.0),
        'cruel', (select coalesce(json_agg(c order by fs desc, league_id::text collate "C",
                                             team_id collate "C"), '[]'::json)
                    from (select c, round(f_std, 2) as fs, league_id, team_id from tcard, th
                           where first_loss = th.through
                           order by round(f_std, 2) desc, league_id::text collate "C",
                                    team_id collate "C" limit 3) x),
        'nearest', (select coalesce(json_agg(c order by fm desc, league_id::text collate "C",
                                               team_id collate "C"), '[]'::json)
                      from (select c, round(f_margin, 2) as fm, league_id, team_id from tcard, th
                             where first_loss = th.through and f_margin is not null
                             order by round(f_margin, 2) desc, league_id::text collate "C",
                                      team_id collate "C" limit 3) x)),
    'standings', (select coalesce(json_agg(json_build_object('w', w, 'l', l, 'n', n)
                                           order by w desc, l), '[]'::json)
                    from (select w, l, count(*) as n from tg where t = 0 group by w, l) x),
    'tied', (select count(*) from tg where t > 0)
)
    into out;
    return out;
end;
$$;


-- The saved copy: one row a season.
create table if not exists public.undefeated_saved (
    season       integer     primary key,
    fingerprint  text        not null,
    payload      json        not null,
    refreshed_at timestamptz not null default now()
);
alter table public.undefeated_saved enable row level security;


-- Recompute only when something underneath changed: the team rows (and how
-- many have PPR), the lineups' weeks, the trades, the waivers and the
-- drafts. Cheap counts, then the expensive build only when they move.
create or replace function public.refresh_undefeated(p_season integer default null)
returns integer language plpgsql as $$
declare
    s   integer := coalesce(p_season, (select max(season) from public.team_weeks));
    fp  text;
    out json;
begin
    if s is null then
        return 0;
    end if;
    fp := concat_ws(':',
        (select count(*) || '/' || count(ppr_points) || '/' || coalesce(max(week), 0)
           from public.team_weeks where season = s),
        (select count(*) from public.trades where season = s),
        (select count(*) from public.waivers where season = s),
        (select count(*) from public.waiver_scans where season = s),
        (select count(*) from public.draft_picks where season = s),
        (select count(*) from public.leagues where league_type is not null));
    if exists (select 1 from public.undefeated_saved where season = s and fingerprint = fp) then
        return 0;
    end if;
    out := public.undefeated_live(s);
    if out is null then
        return 0;
    end if;
    insert into public.undefeated_saved (season, fingerprint, payload, refreshed_at)
    values (s, fp, out, now())
    on conflict (season) do update
       set fingerprint = excluded.fingerprint, payload = excluded.payload,
           refreshed_at = excluded.refreshed_at;
    return 1;
end;
$$;


-- Every 30 minutes, at :25 and :55 so it doesn't land on the other jobs
-- (refresh-around every 10, luck at :15/:45, market :05/:20/:35/:50).
do $$
begin
    if exists (select 1 from pg_extension where extname = 'pg_cron') then
        perform cron.unschedule(jobid) from cron.job where jobname = 'refresh-undefeated';
        perform cron.schedule('refresh-undefeated', '25,55 * * * *',
            'set statement_timeout = ''10min''; select public.refresh_undefeated()');
    end if;
end $$;

notify pgrst, 'reload schema';

select public.refresh_undefeated();
