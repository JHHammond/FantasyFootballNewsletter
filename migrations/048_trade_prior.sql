-- 048: What the leagues' own usage says a player is worth (21 Oct 2026)
--
-- WHY THIS EXISTS. The trade market in web/trade_market.py cannot stand on
-- trades alone, and the reason is worth the paragraph. "Every bundle is
-- worth the other" is also satisfied by giving a few players who trade
-- evenly among themselves a big number and everybody else zero — then
-- almost every trade reads 0 = 0, which is a perfect fit and complete
-- nonsense. The solver prefers it to the truth. Pulling every player
-- toward *average* forbids that collapse but says nothing useful: measured
-- at real convergence it caps out near 0.90 rank correlation on a
-- realistic simulation and 0.28 when managers disagree a lot, at every
-- strength tried. The fit needs to be told where to start.
--
-- The licensed value charts were the obvious source and are the wrong one:
-- FantasyPros gates commercial use behind custom pricing and does not list
-- trade values at all; DynastyProcess redistributes FantasyPros' rankings
-- (which it cannot license onward) and its own chart is dynasty, where
-- rookie picks and age dominate; KeepTradeCut publishes no licence grant
-- and is dynasty too. Meanwhile the thing we actually want is already
-- here, in 14,000 teams a week: what the field rosters, starts and gets.
--
-- WHAT IT RETURNS. Raw ingredients per player, not a finished number. The
-- arithmetic — replacement level, the floor, normalising to an average
-- player — lives in web/trade_prior.py, where it is testable and has a
-- demo twin. SQL aggregates; Python decides.
--
--   rostered    team-weeks this player sat on a roster
--   started     team-weeks he was in a starting lineup: revealed demand,
--               aggregated over the whole country
--   delivered   standardized PPR points he actually put into those
--               starting lineups
--   per_start   delivered / started, his going rate when trusted
--
-- Only ppr_points is read, never a league's own points: the whole point is
-- one scale across every platform. A starter with no stat line contributes
-- a row with a null, which is excluded from delivered rather than counted
-- as a zero — see 044 for what counting those zeros did to the front page.
--
-- PRIVACY. Players only. No league or team ever leaves this function.

create or replace function public.trade_prior(
    p_season integer, p_from_week integer default 1, p_to_week integer default 18)
returns json language sql stable as $$
with rows as (
    select lp.player_key, lp.started, lp.ppr_points
      from public.lineup_players lp
     where lp.season = p_season
       and lp.week between p_from_week and p_to_week
),
tally as (
    select r.player_key,
           count(*)                                        as rostered,
           count(*) filter (where r.started)                as started,
           coalesce(sum(r.ppr_points) filter
                    (where r.started and r.ppr_points is not null), 0)::numeric(12,2)
                                                            as delivered,
           count(*) filter (where r.started and r.ppr_points is not null)
                                                            as scored
      from rows r
     group by r.player_key
)
select coalesce(json_object_agg(t.player_key, json_build_object(
           'position', np.position,
           'rostered', t.rostered,
           'started',  t.started,
           'delivered', t.delivered,
           'per_start', case when t.scored > 0
                             then round(t.delivered / t.scored, 2) end)), '{}'::json)
  from tally t
  left join public.nfl_players np on np.player_key = t.player_key
 where t.rostered > 0;
$$;
