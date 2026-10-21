-- 045: Trades, kept (21 Oct 2026)
--
-- Until now a trade existed for exactly as long as it took to render a
-- paper: generate.py fetched the week's roster moves, printed them, and
-- threw them away. Nothing about trades survived anywhere, which is why
-- Around the Leagues has never had a word to say about them.
--
-- John wants a market: what a player is worth, read off who gets traded
-- for whom, charted over a season. The valuation is NOT decided yet and
-- nothing here attempts one — the first question is how many trades the
-- site actually sees in a week, and that cannot be answered without
-- collecting for a while. So this migration only stores what happened.
--
-- Three tables, because they answer three different questions:
--
--   trades          one row per trade: which league, which week, who was
--                   in it. The header.
--   trade_players   one row per player moving: who left whom for whom.
--                   This is the table a valuation will be built on, so it
--                   carries player_key resolved to a Sleeper id wherever
--                   the PPR matcher could manage it — the same key as
--                   lineup_players, so a player is ONE player across
--                   Sleeper, ESPN and Yahoo and joins to his scores.
--   trade_scans     which leagues we have looked at, and when. Without
--                   this there is no way to tell a league that made no
--                   trades from a league we never asked, so a re-run
--                   could never skip anything and the volume numbers
--                   would be meaningless ("38 trades" out of how many
--                   leagues?).
--
-- trade_key is web.trade_picks.trade_key(): a hash of who got what, so
-- the same trade keeps the same id on every collection, and so a stored
-- trade lines up with the votes already cast on it in migration 043.
--
-- Draft picks and FAAB ride on the header as jsonb rather than in
-- trade_players, because a pick is not a player and a dynasty trade is
-- often mostly picks. A valuation that ignores them will be wrong for
-- dynasty leagues, and it should be able to see that it is ignoring them.
--
-- Re-runnable. Nothing else reads these tables yet.

create table if not exists public.trades (
    league_id    uuid        not null references public.leagues (id) on delete cascade,
    season       smallint    not null,
    week         smallint    not null,
    trade_key    text        not null,
    provider     text        not null,
    team_count   integer,
    scoring_type text,
    --: team names in the platform's own order. User content: staff only.
    teams        jsonb       not null default '[]'::jsonb,
    --: (receiving team, label) pairs, and (from, to, amount).
    picks        jsonb       not null default '[]'::jsonb,
    faab         jsonb       not null default '[]'::jsonb,
    --: when the platform says it happened. Null where it doesn't say.
    traded_at    timestamptz,
    collected_at timestamptz not null default now(),
    primary key (league_id, season, trade_key)
);

create index if not exists trades_week on public.trades (season, week);

create table if not exists public.trade_players (
    league_id   uuid     not null references public.leagues (id) on delete cascade,
    season      smallint not null,
    week        smallint not null,
    trade_key   text     not null,
    --: a Sleeper id where the matcher resolved him, else the platform's own
    --: ("espn:3139477"), exactly as lineup_players keys him.
    player_key  text     not null,
    name        text     not null default '',
    position    text,
    nfl_team    text,
    --: the team that gave him up, and the team that got him. Both are user
    --: content; a public page may count these rows but never print them.
    from_team   text,
    to_team     text,
    primary key (league_id, season, trade_key, player_key)
);

create index if not exists trade_players_player
    on public.trade_players (season, player_key, week);

create table if not exists public.trade_scans (
    league_id  uuid        not null references public.leagues (id) on delete cascade,
    season     smallint    not null,
    week       smallint    not null,
    trades     integer     not null default 0,
    scanned_at timestamptz not null default now(),
    primary key (league_id, season, week)
);

create index if not exists trade_scans_week on public.trade_scans (season, week);

-- Server-only, like every other table: RLS on, no policies.
alter table public.trades        enable row level security;
alter table public.trade_players enable row level security;
alter table public.trade_scans   enable row level security;


-- How much there is to work with, by week. This is the whole point of the
-- first few collections: the shape of the valuation depends on whether a
-- week brings forty trades or four hundred.
--
--   weeks     per week: leagues scanned, trades, players moved, and how
--             many of those players resolved to a Sleeper id (an
--             unresolved player can't be joined to his scores, so a low
--             share here caps what any valuation can do)
--   players   distinct players traded in the season, and the busiest few
create or replace function public.trade_volume(p_season integer)
returns json language sql stable as $$
with wk as (
    select s.week,
           count(*)                                   as leagues,
           coalesce(sum(s.trades), 0)                 as trades
      from public.trade_scans s
     where s.season = p_season
     group by s.week
),
pl as (
    select week,
           count(*)                                            as players,
           count(*) filter (where player_key ~ '^[0-9]+$'
                               or player_key ~ '^[A-Z]{2,3}$') as resolved
      from public.trade_players
     where season = p_season
     group by week
),
busiest as (
    select player_key,
           max(name) as name,
           count(*)  as moves
      from public.trade_players
     where season = p_season
     group by player_key
     order by count(*) desc, player_key collate "C"
     limit 10
)
select json_build_object(
    'season', p_season,
    'weeks', (select coalesce(json_agg(json_build_object(
                  'week', wk.week, 'leagues', wk.leagues, 'trades', wk.trades,
                  'players', coalesce(pl.players, 0),
                  'resolved', coalesce(pl.resolved, 0))
                  order by wk.week), '[]'::json)
                from wk left join pl on pl.week = wk.week),
    'trades', (select count(*) from public.trades where season = p_season),
    'players', (select count(distinct player_key) from public.trade_players
                 where season = p_season),
    'busiest', (select coalesce(json_agg(json_build_object(
                    'player_key', player_key, 'name', name, 'moves', moves)
                    order by moves desc, player_key collate "C"), '[]'::json)
                  from busiest)
);
$$;
