-- 041: the league votes on next week's lines (12 Oct 2026). Re-runnable.
--
-- John: readers pick a side on each game in "Next Week's Lines", see the
-- split once they have, and the next paper finds out who was right. Tallies
-- only, no names: `voter` is a random id the reader's browser makes up, so a
-- person can change their pick but can't stack the count from one browser.
--
-- `week` is the PAPER's week (the lines are for the week after it), `game`
-- is the line's key (the two team ids), `side` is 'fav' or 'dog'.
-- Nothing else depends on this table; the paper works without it.

create table if not exists public.line_votes (
    league_id   uuid        not null references public.leagues (id) on delete cascade,
    season      integer     not null,
    week        integer     not null,
    game        text        not null,
    voter       text        not null,
    side        text        not null check (side in ('fav', 'dog')),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    primary key (league_id, season, week, game, voter)
);

-- Server-only, like every other table: RLS on, no policies.
alter table public.line_votes enable row level security;
