-- 062: dynasty is its own market, and the site knows which a league is (25 Oct 2026)
--
-- John: "For the portfolio, we need to do dynasty or redraft. Matter of fact
-- the whole site needs to understand it."
--
-- ONE ANSWER PER LEAGUE. Until now a league's type was guessed three ways:
-- the setup form (`format`, wrong for 349 Sleeper dynasty leagues because
-- "redraft" is the default), the shape of its draft (059), and nothing for
-- the trade feed. Now:
--
--   leagues.league_type   what the PLATFORM says: Sleeper's own
--                         settings.type (0 redraft, 1 keeper, 2 dynasty).
--                         Null where the platform doesn't say (ESPN, Yahoo).
--
-- and dynasty_league_ids() is the one place every market question asks:
-- the platform's word first; where there is none, the commissioner's
-- `format`, then a rookie-sized draft (059). Keeper counts as redraft: it is
-- priced on this season.
--
-- THE DYNASTY MARKET is built the same way as the redraft one, from its own
-- leagues: dynasty_adp() from dynasty STARTUP drafts (a full draft in a
-- dynasty league: ten-plus picks a team), dynasty_trade_exchanges() from
-- dynasty leagues' player-for-player trades. Dynasty drafts were never
-- collected before this (web/drafts.py skipped them); from now they are, and
-- scripts/backfill_league_types.py fetches the ones already skipped.
--
-- Re-runnable.

alter table public.leagues
    add column if not exists league_type text
        check (league_type in ('redraft', 'keeper', 'dynasty'));


create or replace function public.dynasty_league_ids(p_season integer)
returns setof uuid language sql stable as $$
    select id from public.leagues
     where league_type = 'dynasty'
        or (league_type is null and format = 'dynasty')
    union
    select d.league_id
      from public.draft_picks d
      join public.leagues l on l.id = d.league_id
     where d.season = p_season
       and l.league_type is null          -- the platform's word beats the draft's shape
     group by d.league_id
    having count(*)::numeric / nullif(max(coalesce(d.team_count, 12)), 0) <= 6;
$$;


-- A dynasty league whose draft this season was a full one: a startup.
create or replace function public.dynasty_startup_league_ids(p_season integer)
returns setof uuid language sql stable as $$
    select d.league_id
      from public.draft_picks d
     where d.season = p_season
       and d.league_id in (select public.dynasty_league_ids(p_season))
     group by d.league_id
    having count(*)::numeric / nullif(max(coalesce(d.team_count, 12)), 0) >= 10;
$$;


create or replace function public.dynasty_adp(
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
       and d.league_id in (select public.dynasty_startup_league_ids(p_season))
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
    'drafts', (select count(*) from public.dynasty_startup_league_ids(p_season)),
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


create or replace function public.dynasty_trade_exchanges(
    p_season integer, p_from_week integer default 1, p_to_week integer default 18)
returns json language sql stable as $$
with pure as (
    select t.league_id, t.trade_key, t.week,
           (t.traded_at at time zone 'UTC')::date as day
      from public.trades t
     where t.season = p_season
       and t.week between p_from_week and p_to_week
       and t.picks = '[]'::jsonb and t.faab = '[]'::jsonb
       and t.league_id in (select public.dynasty_league_ids(p_season))
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
    -- The league's MARKET, not what its setup form says: 349 dynasty
    -- leagues were labelled redraft there (062).
    'format', case when r.league_id in (select public.dynasty_league_ids(p_season))
                   then 'dynasty' else 'redraft' end,
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
