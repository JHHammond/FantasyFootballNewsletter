-- 061: the live feed showed only a handful of trades (24 Oct 2026)
--
-- 060 had two blind spots, either of which drops a trade from the feed:
--   * it skipped trades with no traded_at (a platform that doesn't say
--     when); now they sort by when we collected them instead;
--   * it took a trade's sides only from `teams`, so a trade stored without
--     its team list had no sides and the page discarded it; now the sides
--     are every team that received a player, a pick or FAAB, or gave a
--     player, in the stored order where there is one.
-- Re-runnable.

create index if not exists trades_recent_collected on public.trades (season, collected_at desc);

create or replace function public.recent_trades(p_season integer, p_limit integer default 400)
returns json language sql stable as $$
with recent as (
    select t.league_id, t.trade_key, t.week, coalesce(t.traded_at, t.collected_at) as at,
           t.provider, t.team_count, t.scoring_type, t.teams, t.picks, t.faab
      from public.trades t
     where t.season = p_season
     order by coalesce(t.traded_at, t.collected_at) desc
     limit greatest(1, least(p_limit, 1000))
)
select coalesce(json_agg(json_build_object(
    'key', md5(r.league_id::text || ':' || r.trade_key),
    'week', r.week,
    'at', r.at,
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
                  order by tm.ord, tm.team collate "C"), '[]'::json)
                from (select u.team, min(u.ord) as ord
                        from (select v.team, v.ord
                                from jsonb_array_elements_text(r.teams) with ordinality as v(team, ord)
                              union all
                              select x.to_team, 1000 from public.trade_players x
                               where x.league_id = r.league_id and x.season = p_season
                                 and x.trade_key = r.trade_key
                              union all
                              select x.from_team, 1000 from public.trade_players x
                               where x.league_id = r.league_id and x.season = p_season
                                 and x.trade_key = r.trade_key
                              union all
                              select e ->> 0, 1000 from jsonb_array_elements(r.picks) e
                              union all
                              select e ->> 1, 1000 from jsonb_array_elements(r.faab) e) u
                       where u.team is not null and u.team <> ''
                       group by u.team) tm)
    ) order by r.at desc), '[]'::json)
  from recent r
  left join public.leagues l on l.id = r.league_id;
$$;

notify pgrst, 'reload schema';
