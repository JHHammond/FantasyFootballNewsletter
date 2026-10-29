-- 066: player boards with storylines (29 Oct 2026)
--
-- John: "Most started is pretty lame" and "Benched ... does not provide the
-- most value/storyline. People like storylines." Two boards replace them
-- on /around/players:
--
--   * BENCH LOSSES ('bench_losses'): how many times a player sat on the
--     bench of a team that lost a game its own bench would have won
--     (a lineup loss: optimal_points > opponent_points) by less than he
--     scored. "318 losses from the bench." Abandoned rosters (053) don't
--     count.
--   * UNDER THE RADAR ('radar'): started by fewer than a quarter of the
--     teams that had him, at least p_min_starts starts, ranked by how often
--     the teams that did start him won.
--
-- Every player card also carries 'bench_losses', so the full list can be
-- ranked by it. 'started' and 'benched' stay in the output (the front page
-- and older code read them); they are just no longer boards.
--
-- Re-runnable. Needs 055. After running:  notify pgrst, 'reload schema';

create or replace function public.around_week(
    p_season integer, p_week integer, p_min_starts integer default 150)
returns json language sql stable as $$
with base as (
    select b.*, case when b.std_ok then b.ppr_points else b.points end as std_pts
      from (
        select t.*,
               -- A standardized score we believe: present, and more than
               -- nothing. A lineup whose starters all matched Sleeper but
               -- whose week had no stat lines came back 0.00 beside a real
               -- 114.30, and that 0.00 was the worst score in America
               -- (21 Oct). Deliberately not compared with the league's own
               -- points: a league averaging 1,052 standardizes to an
               -- ordinary 130, and rescuing those is the whole point.
               (t.ppr_points is not null and t.ppr_points > 0) as std_ok
          from public.team_weeks t
         where t.season = p_season and t.week = p_week
           and t.result <> 'BYE' and t.points > 0) b
),
has_std as (select exists (select 1 from base where std_ok) as yes),
lg as (
    select league_id, avg(points) as raw,
           avg(ppr_points) filter (where std_ok) as ppr
      from base group by league_id
),
-- A league whose STANDARDIZED average is a fantasy score. A 4-team league
-- starting twenty players averaged 317 in plain PPR, which is not a lineup
-- anyone else is playing, and it owned every board (21 Oct).
okppr as (select league_id from lg where ppr between 40 and 200),
-- A league whose OWN points can be read: a normal average, and in step with
-- its PPR score where we have one. Scoring defenders or yardage bonuses (a
-- 1,052 average, 30 Sep) fails this, and so does the 317 above.
okl as (
    select league_id from lg
     where raw between 40 and 200
       and (ppr is null or raw / ppr between 0.7 and 1.3)
),
tw as (
    select b.*,
           case when h.yes
                then b.std_ok and b.league_id in (select league_id from okppr)
                else b.league_id in (select league_id from okl) end as in_sd,
           (b.league_id in (select league_id from okl)) as in_norm
      from base b, has_std h
),
sd as (select * from tw where in_sd),
norm as (select * from tw where in_norm),
lut as (
    select l.player_key, l.slot, l.started, l.points, l.ppr_points,
           l.league_id, l.team_id, tw.result,
           -- For Bench losses (066): what the game was lost by, and whether
           -- the team's own best lineup would have won it.
           tw.margin, (tw.result = 'L' and tw.optimal_points > tw.opponent_points) as lineup_loss,
           -- Took the field, by games and snaps (055). Null = we don't know.
           nw.played as did_play
      from public.lineup_players l
      join tw on tw.league_id = l.league_id and tw.team_id = l.team_id
      left join public.nfl_week_players nw
        on nw.season = p_season and nw.week = p_week and nw.player_key = l.player_key
     where l.season = p_season and l.week = p_week
),
-- Did this team FIELD a lineup? A score of 2.7 is not a bad week, it is
-- an abandoned roster: nine starters, one of whom was playing. Counting
-- the starters who actually scored is the only way to tell a terrible
-- week from a team nobody has opened since September.
played as (
    select league_id, team_id,
           count(*) filter (where started) as starters,
           count(*) filter (where started and (did_play is true
                                               or coalesce(ppr_points, points) > 0)) as played
      from lut group by league_id, team_id
),
card as (
    -- opponent_points is selected as a COLUMN and not only folded into the
    -- json below, because the two halls under it now filter on it: a win
    -- against nobody is not a win. It was inside `c` alone and the filter
    -- would not compile.
    select tw.league_id, tw.team_id, std_pts, points, margin, bench_left, result,
           opponent_points, pl.starters, pl.played,
           in_sd, in_norm, json_build_object(
        'league_id', tw.league_id, 'team_id', tw.team_id, 'team_name', team_name,
        'manager', manager, 'provider', provider, 'team_count', team_count,
        'scoring_type', scoring_type, 'week', week, 'result', result,
        'points', points, 'ppr_points', ppr_points,
        'opponent_points', opponent_points, 'margin', margin,
        'optimal_points', optimal_points, 'bench_left', bench_left,
        'empty_slots', empty_slots, 'top_player', top_player,
        'best_bench_player', best_bench_player,
        -- Printed, so a reader can see the lineup was a real one.
        -- "7 of 9 starters scored" is the context the number alone has
        -- never carried.
        'starters', pl.starters, 'played', pl.played) as c
      from tw left join played pl
        on pl.league_id = tw.league_id and pl.team_id = tw.team_id
),
top1 as (
    select distinct on (league_id, team_id) league_id, team_id, player_key, points
      from lut where started
     order by league_id, team_id, points desc, player_key collate "C"
),
ps as (
    select player_key,
           count(*)                                         as rostered,
           bool_or(did_play)                                as did_play,
           count(*) filter (where started)                  as started,
           count(*) filter (where not started and slot = 'BN') as benched,
           sum(case when started and result = 'W' then 1
                    when started and result = 'T' then 0.5 else 0 end) as wins,
           max(ppr_points)                                  as ppr,
           max(points)                                      as best,
           coalesce(sum(points) filter (where not started and slot = 'BN'), 0) as bench_total,
           avg(points) filter (where not started and slot = 'BN') as bench_avg,
           -- BENCH LOSSES (066): sat on the bench of a team that lost a game
           -- its own bench would have won, and scored more than it lost by.
           -- A roster nobody opened (053's rule) is not a benching.
           count(*) filter (where not started and slot = 'BN' and lineup_loss
                              and margin is not null and points > -margin
                              and (pl.played is null or pl.played * 2 >= pl.starters)) as bench_losses
      from lut left join played pl using (league_id, team_id)
     group by player_key
),
t1 as (select player_key, count(*) as teams from top1 group by player_key),
pj as (
    select ps.*, coalesce(np.name, ps.player_key) as name, np.position, np.nfl_team,
           coalesce(t1.teams, 0) as teams,
           round(ps.started::numeric / nullif(ps.rostered, 0), 3) as start_pct,
           round(ps.wins / nullif(ps.started, 0), 3)               as win_pct
      from ps left join public.nfl_players np on np.player_key = ps.player_key
              left join t1 on t1.player_key = ps.player_key
),
pcard as (
    select pj.*, json_build_object(
        'player_key', player_key, 'name', name, 'position', position,
        'nfl_team', nfl_team, 'rostered', rostered, 'started', started,
        'benched', benched, 'start_pct', start_pct, 'win_pct', win_pct,
        'wins', wins, 'ppr', ppr, 'best', best, 'teams', teams,
        'bench_total', round(bench_total, 2),
        'bench_avg', round(bench_avg, 2),
        'bench_losses', bench_losses,
        'did_play', did_play) as c
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
        'standardized', count(*) filter (where std_ok),
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
    -- A WALKOVER IS NOT A LUCKY WIN. Somebody has to have been there to
    -- lose: see this migration's header.
    'lucky', (select coalesce(json_agg(c order by std_pts, league_id, team_id collate "C"), '[]'::json)
                from (select * from card
                       where in_sd and result = 'W'
                         and opponent_points is not null and opponent_points > 0
                         and (played is null or played * 2 >= starters)
                       order by std_pts, league_id, team_id collate "C" limit 5) x),
    'halls', json_build_object(
        'highest', (select coalesce(json_agg(c order by std_pts desc, league_id, team_id collate "C"), '[]'::json)
                      from (select * from card where in_sd
                             order by std_pts desc, league_id, team_id collate "C" limit 5) x),
        -- A SCORE NOBODY PLAYED FOR IS NOT A LOW SCORE. Live, the five
        -- lowest in America were 2.7, 4.4, 4.8, 5.0 and 6.9, which is not
        -- a bad week in any league: it is five abandoned rosters, and they
        -- win this hall every week forever, because nobody can out-lose a
        -- team that is not there.
        'lowest', (select coalesce(json_agg(c order by std_pts, league_id, team_id collate "C"), '[]'::json)
                     from (select * from card
                            where in_sd and (played is null or played * 2 >= starters)
                            order by std_pts, league_id, team_id collate "C" limit 5) x),
        -- Same artifact, different hat: a 140-to-nothing is a forfeit
        -- with a scoreline, and it crowds out the real maulings.
        'blowouts', (select coalesce(json_agg(c order by margin desc, league_id, team_id collate "C"), '[]'::json)
                       from (select * from card
                              where in_norm and result = 'W' and margin is not null
                                and opponent_points is not null and opponent_points > 0
                              order by margin desc, league_id, team_id collate "C" limit 5) x),
        -- Same rule as 'lowest'. 163.7 on the bench beside a 2.7 with
        -- one of four starters playing is not a lineup decision, it is a
        -- roster nobody opened. It led the front page on 22 Oct.
        'bench', (select coalesce(json_agg(c order by bench_left desc, league_id, team_id collate "C"), '[]'::json)
                    from (select * from card where in_norm and bench_left is not null
                            and (played is null or played * 2 >= starters)
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
                               and (did_play is true or (did_play is null and coalesce(ppr, best, 0) <> 0))
                              order by win_pct desc, started desc, player_key collate "C" limit 10) x),
        'win_worst', (select coalesce(json_agg(c order by win_pct, started desc, player_key collate "C"), '[]'::json)
                        -- A player who scored nothing did not play. Hurts,
                        -- Lamar and Saquon at 0.0 topped this board on 22
                        -- Oct: that is starting a man on a bye, not a man
                        -- who cost you the week. A negative score did play,
                        -- and stays.
                        from (select * from pcard where started >= p_min_starts
                                and (did_play is true or (did_play is null and coalesce(ppr, best, 0) <> 0))
                               order by win_pct, started desc, player_key collate "C" limit 10) x),
        -- 066: two boards with a storyline in them (John, 29 Oct).
        -- Bench losses: the games a player lost you from your own bench.
        'bench_losses', (select coalesce(json_agg(c order by bench_losses desc, benched desc, player_key collate "C"), '[]'::json)
                           from (select * from pcard where bench_losses > 0
                                  order by bench_losses desc, benched desc, player_key collate "C" limit 10) x),
        -- Under the radar: most teams that had him benched him, and the few
        -- who started him won. Same floor of starts and the same "did he
        -- play" rule as the win-rate boards.
        'radar', (select coalesce(json_agg(c order by win_pct desc, started desc, player_key collate "C"), '[]'::json)
                    from (select * from pcard where started >= p_min_starts and start_pct < 0.25
                            and (did_play is true or (did_play is null and coalesce(ppr, best, 0) <> 0))
                           order by win_pct desc, started desc, player_key collate "C" limit 10) x),
        'flops', (select coalesce(json_agg(c order by started desc, player_key collate "C"), '[]'::json)
                    from (select * from pcard where started > 0 and ppr is not null and ppr < 5
                           order by started desc, player_key collate "C" limit 5) x),
        'sleepers', (select coalesce(json_agg(c order by ppr desc, player_key collate "C"), '[]'::json)
                       from (select * from pcard where rostered >= 100 and ppr is not null
                               and start_pct < 0.25
                              order by ppr desc, player_key collate "C" limit 5) x),
        -- Every player, for the expanded board and its search. Capped: the
        -- tail is players on a handful of rosters, and this rides in a
        -- cached JSON the page ships whole.
        'all', (select coalesce(json_agg(c order by rostered desc, player_key collate "C"), '[]'::json)
                  from (select * from pcard
                         order by rostered desc, player_key collate "C" limit 400) x),
        'min_starts', p_min_starts)
);
$$;
