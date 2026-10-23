-- 056: every trade of one player, for his page (23 Oct 2026)
--
-- The player page lists every deal a player was in this season, newest
-- first: what the team that got him received, and what it gave up, with the
-- draft picks and FAAB on the side that received them. trade_partners (047,
-- 051) answered "who is he traded for" with six recent deals and a tally;
-- this is the whole list.
--
-- PRIVACY, as everywhere in the market: players, pick LABELS and FAAB
-- AMOUNTS leave this function, and the league only as its shape ("a
-- 12-team PPR league on Sleeper"). Team names, manager names, the league
-- id and the platform's trade id do not. Each deal gets an opaque key
-- (an md5 of league and trade) so the page has something stable to link
-- and count by.
--
-- "Got" is the side of the team that received him: him, anybody who came
-- with him, and any pick or FAAB sent to that team. "Gave" is everything
-- that went the other way. In a three-way trade "gave" is every player who
-- went to any other team; `teams` says how many were involved.
--
-- Re-runnable. Needs 045 and 051.

create or replace function public.player_trades(p_season integer, p_player text)
returns json language sql stable as $$
with mine as (
    select p.league_id, p.trade_key, p.to_team
      from public.trade_players p
     where p.season = p_season and p.player_key = p_player
),
deals as (
    select t.league_id, t.trade_key, t.week, t.traded_at, t.provider,
           t.team_count, t.scoring_type, t.picks, t.faab, m.to_team
      from public.trades t
      join mine m on m.league_id = t.league_id and m.trade_key = t.trade_key
     where t.season = p_season
)
select json_build_object(
    'player', p_player,
    'trades', coalesce((select json_agg(json_build_object(
        'key', md5(d.league_id::text || ':' || d.trade_key),
        'week', d.week,
        'day', (d.traded_at at time zone 'UTC')::date,
        'provider', d.provider,
        'team_count', d.team_count,
        'scoring_type', d.scoring_type,
        'teams', (select count(distinct x.to_team) from public.trade_players x
                   where x.league_id = d.league_id and x.season = p_season
                     and x.trade_key = d.trade_key),
        'with', (select coalesce(json_agg(json_build_object(
                        'player_key', x.player_key, 'name', x.name, 'position', x.position)
                        order by x.player_key collate "C"), '[]'::json)
                   from public.trade_players x
                  where x.league_id = d.league_id and x.season = p_season
                    and x.trade_key = d.trade_key and x.player_key <> p_player
                    and x.to_team is not distinct from d.to_team),
        'gave', (select coalesce(json_agg(json_build_object(
                        'player_key', x.player_key, 'name', x.name, 'position', x.position)
                        order by x.player_key collate "C"), '[]'::json)
                   from public.trade_players x
                  where x.league_id = d.league_id and x.season = p_season
                    and x.trade_key = d.trade_key
                    and x.to_team is distinct from d.to_team),
        -- picks are (receiving team, label); faab is (from, to, amount).
        'got_picks', (select coalesce(json_agg(e ->> 1), '[]'::json)
                        from jsonb_array_elements(d.picks) e
                       where e ->> 1 is not null and (e ->> 0) is not distinct from d.to_team),
        'gave_picks', (select coalesce(json_agg(e ->> 1), '[]'::json)
                         from jsonb_array_elements(d.picks) e
                        where e ->> 1 is not null and (e ->> 0) is distinct from d.to_team),
        'got_faab', (select coalesce(sum((e ->> 2)::numeric), 0)
                       from jsonb_array_elements(d.faab) e
                      where e ->> 2 is not null and (e ->> 1) is not distinct from d.to_team),
        'gave_faab', (select coalesce(sum((e ->> 2)::numeric), 0)
                        from jsonb_array_elements(d.faab) e
                       where e ->> 2 is not null and (e ->> 1) is distinct from d.to_team))
        order by d.traded_at desc nulls last, d.week desc, d.trade_key collate "C")
      from deals d), '[]'::json)
);
$$;
