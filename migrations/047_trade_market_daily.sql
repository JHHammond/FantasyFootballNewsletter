-- 047: A daily market, and what a player gets traded for (21 Oct 2026)
--
-- 046 handed the solver one bundle-pair per trade and a WEEK. Six weeks of
-- football is six points, and six points is a flat line — which is both
-- dull and less true than the data we hold, because every trade carries
-- the moment it happened. So trade_exchanges() now returns that date too,
-- and the market is solved day by day on a trailing window.
--
-- A trade whose platform gave no timestamp comes back with a null date
-- rather than a guess; the caller places it in the middle of its week,
-- because it knows when the season started and SQL does not.
--
-- Also adds trade_partners(): who one player actually gets swapped for.
-- That is the question somebody staring at a price really has — "is this
-- a fair trade?" — and it is answered by what the rest of the country
-- did, not by the number.
--
-- PRIVACY. trade_partners returns PLAYERS and never teams. A trade's
-- team names are its league's own content; the fact that Ja'Marr Chase
-- goes for a first-round back is everybody's.

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
    -- json_agg, not array_agg: the two bundles are rarely the same size,
    -- and Postgres has no ragged two-dimensional array.
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


-- Who this player gets traded for, and his latest deals.
--
--   counterparts  the players who most often stand on the OTHER side of a
--                 trade from him: the going exchange rate, by frequency
--   alongside     the players most often bundled WITH him, which is a
--                 different and quieter fact
--   recent        his last few actual trades, each as the two bundles:
--                 what his side got, and what it gave
create or replace function public.trade_partners(
    p_season integer, p_player text, p_limit integer default 8)
returns json language sql stable as $$
with mine as (
    select p.league_id, p.trade_key, p.week, p.to_team
      from public.trade_players p
     where p.season = p_season and p.player_key = p_player
),
others as (
    select o.player_key, o.name, o.position, o.nfl_team,
           (o.to_team is distinct from m.to_team) as opposite
      from public.trade_players o
      join mine m on m.league_id = o.league_id and m.trade_key = o.trade_key
     where o.season = p_season and o.player_key <> p_player
),
tally as (
    select player_key,
           max(name) as name, max(position) as position, max(nfl_team) as nfl_team,
           count(*) filter (where opposite)     as against,
           count(*) filter (where not opposite) as with_him
      from others group by player_key
),
recent as (
    select t.week, (t.traded_at at time zone 'UTC')::date as day,
           (select coalesce(json_agg(json_build_object(
                       'name', x.name, 'position', x.position) order by x.player_key collate "C"), '[]'::json)
              from public.trade_players x
             where x.league_id = t.league_id and x.season = p_season
               and x.trade_key = t.trade_key
               and x.to_team is not distinct from m.to_team) as got,
           (select coalesce(json_agg(json_build_object(
                       'name', x.name, 'position', x.position) order by x.player_key collate "C"), '[]'::json)
              from public.trade_players x
             where x.league_id = t.league_id and x.season = p_season
               and x.trade_key = t.trade_key
               and x.to_team is distinct from m.to_team) as gave
      from public.trades t
      join mine m on m.league_id = t.league_id and m.trade_key = t.trade_key
     where t.season = p_season
     order by t.traded_at desc nulls last, t.week desc, t.trade_key collate "C"
     limit 6
)
select json_build_object(
    'player', p_player,
    'trades', (select count(*) from mine),
    'counterparts', (select coalesce(json_agg(json_build_object(
            'player_key', player_key, 'name', name, 'position', position,
            'nfl_team', nfl_team, 'n', against)
            order by against desc, player_key collate "C"), '[]'::json)
        from (select * from tally where against > 0
               order by against desc, player_key collate "C" limit p_limit) c),
    'alongside', (select coalesce(json_agg(json_build_object(
            'player_key', player_key, 'name', name, 'position', position,
            'n', with_him) order by with_him desc, player_key collate "C"), '[]'::json)
        from (select * from tally where with_him > 0
               order by with_him desc, player_key collate "C" limit 4) c),
    'recent', (select coalesce(json_agg(json_build_object(
            'week', week, 'day', day, 'got', got, 'gave', gave)), '[]'::json)
        from recent)
);
$$;
