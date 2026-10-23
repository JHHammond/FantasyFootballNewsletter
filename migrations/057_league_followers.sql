-- 057: more than one account per league, for the trade market (23 Oct 2026)
--
-- A league's paper belongs to one account (leagues.user_id), and that stays
-- true. But the trade market's "Show my team" and "Get him" are about YOUR
-- team, and everybody in a league has one. When a second person connects a
-- league somebody else already has a paper for, they follow it instead of
-- being turned away: the market sees the league, with their own team.
--
-- team_id is the follower's own team (Sleeper tells us at connect time);
-- null means we don't know yet and the market asks them to pick.

create table if not exists public.league_followers (
    user_id    uuid not null references public.users (id) on delete cascade,
    league_id  uuid not null references public.leagues (id) on delete cascade,
    team_id    text,
    created_at timestamptz not null default now(),
    primary key (user_id, league_id)
);

create index if not exists league_followers_league on public.league_followers (league_id);

-- Server-side only, like every other table: the service key reads it.
alter table public.league_followers enable row level security;

notify pgrst, 'reload schema';
