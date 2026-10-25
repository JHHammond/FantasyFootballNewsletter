-- 060: the live trade feed (24 Oct 2026)
--
-- The newest trades across every connected league, for /market/trades.
-- Same privacy as the player pages: players, pick LABELS and FAAB amounts;
-- the league only as its shape. Team names, manager names, the league id
-- and the platform's trade id never leave. Sides are anonymous, in the
-- platform's own team order; `format` lets the page filter dynasty out.
--
-- team_count and scoring_type fall back to the league's stored weeks when
-- the trade row didn't record them.

create index if not exists trades_recent on public.trades (season, traded_at desc);

create or replace function public.recent_trades(p_season integer, p_limit integer default 400)
returns json language sql stable as $$
with recent as (
    select t.league_id, t.trade_key, t.week, t.traded_at, t.provider,
           t.team_count, t.scoring_type, t.teams, t.picks, t.faab
      from public.trades t
     where t.season = p_season and t.traded_at is not null
     order by t.traded_at desc
     limit greatest(1, least(p_limit, 1000))
)
select coalesce(json_agg(json_build_object(
    'key', md5(r.league_id::text || ':' || r.trade_key),
    'week', r.week,
    'at', r.traded_at,
    'provider', r.provider,
    'team_count', coalesce(r.team_count, (select max(tw.team_count) from public.team_weeks tw
                                           where tw.league_id = r.league_id and tw.season = p_season)),
    'scoring_type', coalesce(r.scoring_type, (select max(tw.scoring_type) from public.team_weeks tw
                                               where tw.league_id = r.league_id and tw.season = p_season)),
    'format', l.format,
    'sides', (select coalesce(json_agg(json_build_object(
                  'players', (select coalesce(json_agg(json_build_object(
                                  'player_key', x.player_key, 'name', x.name, 'position', x.position)
                                  order by x.player_key collate "C"), '[]'::json)
                                from public.trade_players x
                               where x.league_id = r.league_id and x.season = p_season
                                 and x.trade_key = r.trade_key and x.to_team = tm.team),
                  'picks', (select coalesce(json_agg(e ->> 1), '[]'::json)
                              from jsonb_array_elements(r.picks) e
                             where e ->> 1 is not null and (e ->> 0) = tm.team),
                  'faab', (select coalesce(sum((e ->> 2)::numeric), 0)
                             from jsonb_array_elements(r.faab) e
                            where e ->> 2 is not null and (e ->> 1) = tm.team))
                  order by tm.ord), '[]'::json)
                from jsonb_array_elements_text(r.teams) with ordinality as tm(team, ord))
    ) order by r.traded_at desc), '[]'::json)
  from recent r
  left join public.leagues l on l.id = r.league_id;
$$;

notify pgrst, 'reload schema';
