-- 046: The trade market — reading the exchanges (21 Oct 2026)
--
-- 045 stores trades. This hands them to web/trade_market.py in the only
-- shape it wants: for each trade, the two bundles that changed hands.
--
-- Two filters, and both of them throw data away on purpose:
--
--   * PLAYER-ONLY. A trade carrying draft picks or FAAB is not an equation
--     we can write, because nobody knows what the pick is worth. Including
--     it would quietly corrupt the value of every player in it. Those
--     trades are left out here and counted in trade_volume(), and in a
--     dynasty-heavy population the share left out will be large. Valuing
--     picks is the obvious next piece of work.
--   * TWO-SIDED. "Who won?" has no simple answer in a three-way deal, and
--     neither does "what was it worth?" — a three-way is one equation in
--     three bundles, which the solver does not model.
--
-- A player with no to_team (a platform that reported a drop and no
-- destination) can't be placed on a side, so he is left out of the bundle
-- rather than guessed at.

create or replace function public.trade_exchanges(
    p_season integer, p_from_week integer default 1, p_to_week integer default 18)
returns json language sql stable as $$
with pure as (
    select t.league_id, t.trade_key, t.week
      from public.trades t
     where t.season = p_season
       and t.week between p_from_week and p_to_week
       and t.picks = '[]'::jsonb and t.faab = '[]'::jsonb
),
sides as (
    select p.league_id, p.trade_key, pure.week, p.to_team,
           array_agg(p.player_key order by p.player_key collate "C") as players
      from public.trade_players p
      join pure on pure.league_id = p.league_id and pure.trade_key = p.trade_key
     where p.season = p_season and p.to_team is not null
     group by p.league_id, p.trade_key, pure.week, p.to_team
),
two as (
    -- json_agg, not array_agg: the two bundles are rarely the same size,
    -- and Postgres has no ragged two-dimensional array.
    select week,
           json_agg(players order by to_team collate "C") as bundles,
           count(*) as n
      from sides
     group by league_id, trade_key, week
)
select coalesce(json_agg(json_build_object(
           'w', week, 'a', bundles->0, 'b', bundles->1)), '[]'::json)
  from two where n = 2;
$$;


-- Who the keys are, for printing a board. Traded players only, so this is
-- hundreds of rows and not thousands. nfl_players is the better source
-- (it is Sleeper's own spelling, filled by the lineup collector); the
-- trade's own copy is the fallback for a player who never made a lineup.
create or replace function public.trade_player_names(p_season integer)
returns json language sql stable as $$
select coalesce(json_object_agg(key, json_build_object(
           'name', name, 'position', position, 'nfl_team', nfl_team)), '{}'::json)
  from (
    select p.player_key as key,
           coalesce(max(np.name), max(p.name), p.player_key) as name,
           coalesce(max(np.position), max(p.position))       as position,
           coalesce(max(np.nfl_team), max(p.nfl_team))       as nfl_team
      from public.trade_players p
      left join public.nfl_players np on np.player_key = p.player_key
     where p.season = p_season
     group by p.player_key) x;
$$;
