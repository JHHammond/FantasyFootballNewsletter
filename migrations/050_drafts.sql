-- 050: Our own ADP, out of our own drafts (22 Oct 2026)
--
-- WHY. The trade market can only price a player somebody traded, and it
-- can only make him worth as much as the biggest bundle anybody actually
-- gave up for him. Both of those are fatal at the top of the board. A
-- genuine star is traded twice a season and is worth more than anything
-- anyone will offer, so the trades never reveal his value and the additive
-- model forbids it: value(star) = value(a) + value(b) caps him at whatever
-- a and b are worth. Measured on simulated trades the fit squeezes a
-- 4.27x top-to-median spread down to 3.64x; the live board came out at
-- 2.1x, with three tight ends in the top five.
--
-- The fix is not a better solver. It is a value for EVERY player before
-- any trade is read, on a scale with a real spread — and a draft is
-- exactly that. Fourteen hundred leagues drafting is thousands of people
-- revealing what they think a player is worth under scarcity, which is the
-- same wisdom-of-crowds argument that makes a prediction market work, and
-- it happens before a single game is played.
--
-- OUR OWN, not a licensed chart. Every adapter already exposes
-- draft_picks() — Sleeper, ESPN and Yahoo — so this costs nothing but the
-- collection, carries no licence, and is a number no other site holds.
--
-- NORMALISED TO TWELVE TEAMS. Pick 11 in a ten-team league and pick 11 in
-- a fourteen-team league are not the same pick: the first is the last of
-- round one, the second is the middle of it. Everything here is converted
-- to its twelve-team equivalent, (overall - 1) * 12 / team_count + 1, so a
-- league's size stops distorting the average.
--
-- KEYED LIKE EVERYTHING ELSE. player_key is resolved through the same PPR
-- matcher lineup_players and trade_players use, so a drafted player joins
-- to what he was traded for and what he went on to score. A draft pick
-- that would not resolve keeps its platform id and simply cannot be
-- joined; adp() reports that share, because it caps what the market can do.
--
-- PRIVACY: a pick records WHICH TEAM made it, because draft regrets needs
-- that, and team ids are a league's own content. adp() returns players and
-- counts only — never a league, a team or a manager.

create table if not exists public.draft_picks (
    league_id   uuid     not null references public.leagues (id) on delete cascade,
    season      smallint not null,
    --: resolved to a Sleeper id where the matcher managed it, else the
    --: platform's own, exactly as lineup_players keys him.
    player_key  text     not null,
    --: the pick's place in the draft, as the platform numbers it.
    overall     integer  not null,
    round       integer,
    --: the roster that made the pick. User content: staff only.
    team_id     text,
    --: how many teams were drafting, so `overall` can be normalised.
    team_count  integer,
    name        text     not null default '',
    position    text,
    nfl_team    text,
    primary key (league_id, season, player_key)
);

create index if not exists draft_picks_player
    on public.draft_picks (season, player_key);

create table if not exists public.draft_scans (
    league_id  uuid        not null references public.leagues (id) on delete cascade,
    season     smallint    not null,
    picks      integer     not null default 0,
    --: a league that drafted and one we never asked look identical in
    --: draft_picks; telling them apart is what makes the skip logic work.
    scanned_at timestamptz not null default now(),
    primary key (league_id, season)
);

alter table public.draft_picks enable row level security;
alter table public.draft_scans enable row level security;


-- Average draft position, and how much the country disagreed about it.
--
-- The spread matters as much as the average. A player every league took
-- within four picks of each other is a settled opinion; one who went 20th
-- in some rooms and 60th in others is contested, and the trade market
-- should be readier to move him. That is the rating deviation a Glicko
-- style model wants, taken from the drafts rather than invented.
--
-- Players are ordered by ADP, and `rank` is that order — which is the
-- scale the page prints, because "he costs the 1.04" means something to
-- anyone who has drafted a team and "he is worth 103.9" does not.
create or replace function public.adp(
    p_season integer, p_min_drafts integer default 20)
returns json language sql stable as $$
with picks as (
    select d.player_key,
           -- The twelve-team equivalent of this pick.
           ((d.overall - 1) * 12.0
             / nullif(coalesce(d.team_count, 12), 0)) + 1 as slot,
           d.name, d.position, d.nfl_team
      from public.draft_picks d
     where d.season = p_season
       and d.overall > 0
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
    -- What share of picks resolved to a Sleeper id. Caps everything.
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
