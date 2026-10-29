-- 067: Waiver claims, kept (29 Oct 2026). Needs 045. Re-runnable.
--
-- John: "We should begin gathering waivers to get some interesting
-- information for the site and socials."
--
-- The collector has been fetching every league's transactions feed since
-- 045, keeping the trades and throwing the rest away. The rest is the
-- waiver wire: every claim that went through, every claim that LOST (often
-- the better story: somebody bid, publicly, and did not get him), what was
-- dropped to make room, and the FAAB bid. Storing it costs no extra call to
-- any platform, because the feed is already in hand.
--
-- One row per player per move: a claim that adds Rico Dowdle and drops a
-- kicker is two rows with the same move_key, action 'add' and 'drop'.
--
--   kind     'waiver' (a processed claim) or 'free_agent' (a straight add).
--            Commissioner moves and trades are not kept here.
--   status   'complete' or 'failed'. ESPN drops failed claims at source;
--            Sleeper reports them.
--   bid      FAAB, where the league uses a budget. Null for waiver-priority
--            leagues and free-agent adds.
--
-- PRIVACY: team_name is the user's content, as in trade_players. A public
-- page may count these rows and never print the name.
--
-- waiver_scans records every league-week we have ASKED, including the ones
-- with no moves, for the same reason trade_scans does: a quiet league and a
-- league we never asked otherwise look identical, and the backfill could
-- not tell which weeks are done. A league-week missing from waiver_scans is
-- asked again by the next collection run, which is how weeks 1-8 fill in:
-- run the collector on the staff page for weeks 1-8 (or
-- `python -m scripts.backfill_waivers`) once this migration is in.

create table if not exists public.waivers (
    league_id    uuid        not null references public.leagues (id) on delete cascade,
    season       smallint    not null,
    week         smallint    not null,
    --: one roster move; the same key on every row of that move.
    move_key     text        not null,
    kind         text        not null check (kind in ('waiver', 'free_agent')),
    status       text        not null check (status in ('complete', 'failed')),
    action       text        not null check (action in ('add', 'drop')),
    --: resolved to a Sleeper id where the matcher managed it, else the
    --: platform's own, exactly as lineup_players keys him.
    player_key   text        not null,
    name         text        not null default '',
    position     text,
    nfl_team     text,
    --: the team that made the move. User content: staff only.
    team_name    text,
    bid          integer,
    moved_at     timestamptz,
    collected_at timestamptz not null default now(),
    primary key (league_id, season, move_key, action, player_key)
);

create index if not exists waivers_week   on public.waivers (season, week);
create index if not exists waivers_player on public.waivers (season, player_key, week);

create table if not exists public.waiver_scans (
    league_id  uuid        not null references public.leagues (id) on delete cascade,
    season     smallint    not null,
    week       smallint    not null,
    moves      integer     not null default 0,
    scanned_at timestamptz not null default now(),
    primary key (league_id, season, week)
);

create index if not exists waiver_scans_week on public.waiver_scans (season, week);

-- Server-only, like every other table: RLS on, no policies.
alter table public.waivers      enable row level security;
alter table public.waiver_scans enable row level security;


-- How much of the wire we hold, by week: for the staff note on the
-- Undefeated Watch, so a backfill can be checked rather than assumed.
create or replace function public.waiver_volume(p_season integer)
returns json language sql stable as $$
with scans as (
    select week, count(*) as leagues
      from public.waiver_scans where season = p_season group by week
),
moves as (
    select week,
           count(*) filter (where action = 'add' and status = 'complete') as adds,
           count(*) filter (where action = 'add' and status = 'failed')   as failed,
           coalesce(sum(bid) filter (where action = 'add' and status = 'complete'), 0) as faab
      from public.waivers where season = p_season group by week
)
select json_build_object(
    'weeks', coalesce((select json_agg(json_build_object(
                'week', s.week, 'leagues', s.leagues,
                'adds', coalesce(m.adds, 0), 'failed', coalesce(m.failed, 0),
                'faab', coalesce(m.faab, 0)) order by s.week)
              from scans s left join moves m on m.week = s.week), '[]'::json),
    'adds', (select count(*) from public.waivers
              where season = p_season and action = 'add' and status = 'complete'));
$$;

notify pgrst, 'reload schema';
