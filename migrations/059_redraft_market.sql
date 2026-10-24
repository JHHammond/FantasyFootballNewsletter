-- 059: a redraft market, with rookies priced like everybody else (23 Oct 2026)
--
-- John: Skyler Bell at WR15, Chris Brazzell at #29, Eli Stowers at #39.
-- Rookies were priced as if this were a dynasty market, for two reasons:
--
--   1. ADP. Drafts are collected only from leagues not set up as dynasty,
--      but 349 dynasty leagues are labelled "redraft" in setup, so their
--      ROOKIE drafts were averaged in: a rookie taken 1.03 in a five-round
--      rookie draft counted as the 1.03 overall.
--   2. Trades. A rookie in a dynasty league is worth years of production,
--      and those trades moved his one market.
--
-- A league is treated as dynasty when it says so, OR when the draft we
-- stored from it is a rookie draft: six picks a team or fewer (a redraft
-- draft is fifteen-plus rounds). Its draft leaves ADP and its trades leave
-- the market. Every other function keeps showing every trade.

create or replace function public.dynasty_league_ids(p_season integer)
returns setof uuid language sql stable as $$
    select id from public.leagues where format = 'dynasty'
    union
    select league_id
      from public.draft_picks
     where season = p_season
     group by league_id
    having count(*)::numeric / nullif(max(coalesce(team_count, 12)), 0) <= 6;
$$;


create or replace function public.adp(
    p_season integer, p_min_drafts integer default 20)
returns json language sql stable as $$
with picks as (
    select d.player_key,
           ((d.overall - 1) * 12.0
             / nullif(coalesce(d.team_count, 12), 0)) + 1 as slot,
           d.name, d.position, d.nfl_team
      from public.draft_picks d
     where d.season = p_season
       and d.overall > 0
       and d.league_id not in (select public.dynasty_league_ids(p_season))
),
tally as (
    select p.player_key,
           count(*)                                              as drafts,
           round(avg(p.slot)::numeric, 2)                        as adp,
           round((percentile_cont(0.5) within group
                  (order by p.slot))::numeric, 2)                as median,
           round((percentile_cont(0.10) within group
                  (order by p.slot))::numeric, 2)                as early,
           round((percentile_cont(0.90) within group
                  (order by p.slot))::numeric, 2)                as late,
           round(coalesce(stddev_samp(p.slot), 0)::numeric, 2)   as spread,
           max(p.name)     as raw_name,
           max(p.position) as raw_pos,
           max(p.nfl_team) as raw_team
      from picks p
     group by p.player_key
),
kept as (
    select t.*,
           coalesce(np.name, t.raw_name, t.player_key)  as name,
           coalesce(np.position, t.raw_pos)             as position,
           coalesce(np.nfl_team, t.raw_team)            as nfl_team,
           row_number() over (order by t.adp, t.player_key collate "C") as rank
      from tally t
      left join public.nfl_players np on np.player_key = t.player_key
     where t.drafts >= p_min_drafts
)
select json_build_object(
    'season', p_season,
    'min_drafts', p_min_drafts,
    'drafts', (select count(*) from public.draft_scans
                where season = p_season and picks > 0),
    'scanned', (select count(*) from public.draft_scans where season = p_season),
    'picks', (select count(*) from public.draft_picks where season = p_season),
    'resolved', (select count(*) from public.draft_picks
                  where season = p_season
                    and (player_key ~ '^[0-9]+$' or player_key ~ '^[A-Z]{2,3}$')),
    'players', (select coalesce(json_agg(json_build_object(
                    'player_key', player_key, 'name', name, 'position', position,
                    'nfl_team', nfl_team, 'drafts', drafts, 'adp', adp,
                    'median', median, 'early', early, 'late', late,
                    'spread', spread, 'rank', rank)
                    order by rank), '[]'::json) from kept)
);
$$;


create or replace function public.trade_exchanges(
    p_season integer, p_from_week integer default 1, p_to_week integer default 18)
returns json language sql stable as $$
with pure as (
    select t.league_id, t.trade_key, t.week,
           (t.traded_at at time zone 'UTC')::date as day
      from public.trades t
     where t.season = p_season
       and t.week between p_from_week and p_to_week
       and t.picks = '[]'::jsonb and t.faab = '[]'::jsonb
       and t.league_id not in (select public.dynasty_league_ids(p_season))
),
sides as (
    select p.league_id, p.trade_key, pure.week, pure.day, p.to_team,
           array_agg(p.player_key order by p.player_key collate "C") as players
      from public.trade_players p
      join pure on pure.league_id = p.league_id and pure.trade_key = p.trade_key
     where p.season = p_season and p.to_team is not null
     group by p.league_id, p.trade_key, pure.week, pure.day, p.to_team
),
two as (
    select week, day,
           json_agg(players order by to_team collate "C") as bundles,
           count(*) as n
      from sides
     group by league_id, trade_key, week, day
)
select coalesce(json_agg(json_build_object(
           'w', week, 'd', day, 'a', bundles->0, 'b', bundles->1)), '[]'::json)
  from two where n = 2;
$$;

notify pgrst, 'reload schema';
