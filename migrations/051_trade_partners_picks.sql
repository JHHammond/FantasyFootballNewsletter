-- 051: "Drew Lock for —" was a draft pick (22 Oct 2026)
--
-- The player panel lists a man's latest deals as two bundles: what his
-- side got, and what it gave. Some of them printed with one side empty —
--
--     Drew Lock for —
--
-- which reads as a bug and is in fact the truth wearing no clothes. The
-- other side WAS something: a draft pick, or a lump of FAAB. We store
-- both, in trades.picks and trades.faab, and 047's trade_partners simply
-- never looked at them, because it reads trade_players and a pick is not
-- a player.
--
-- The market still cannot VALUE a pick — that is the next real piece of
-- work, and until it is done those trades stay out of the fit entirely.
-- But refusing to price a thing is no reason to pretend it was not in the
-- trade. "Drew Lock for a 2027 2nd" is an honest line, and it tells the
-- reader why that deal is missing from the numbers.
--
-- PRIVACY. picks is stored as (receiving team, label) pairs and faab as
-- (from, to, amount); the team names in both are a league's own content.
-- Only the LABEL and the AMOUNT leave this function. A 2027 2nd is
-- everybody's; who gave it up is not.

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
               and x.to_team is distinct from m.to_team) as gave,
           -- Labels only. The team that sent the pick stays in the league.
           (select coalesce(json_agg(elem ->> 1), '[]'::json)
              from jsonb_array_elements(t.picks) as elem
             where elem ->> 1 is not null)                  as picks,
           -- Amounts only, for the same reason.
           (select coalesce(json_agg((elem ->> 2)::numeric), '[]'::json)
              from jsonb_array_elements(t.faab) as elem
             where elem ->> 2 is not null)                  as faab
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
            'week', week, 'day', day, 'got', got, 'gave', gave,
            'picks', picks, 'faab', faab)), '[]'::json)
        from recent)
);
$$;
