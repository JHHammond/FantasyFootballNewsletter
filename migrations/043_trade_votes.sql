-- 043: the league votes on who won each trade (20 Oct 2026). Re-runnable.
--
-- Same idea as 041 (votes on the lines): every two-team trade in a paper
-- gets "Who won the trade?" with a button for each side. Tallies only, no
-- names: `voter` is a random id the reader's browser makes up, so a person
-- can change their vote but can't stack the count from one browser.
--
-- `week` is the PAPER's week, `trade` is the trade's key (a short hash of
-- who got what, web/trade_picks.py), `side` is 'a' or 'b' in card order.
-- Nothing else depends on this table; the paper works without it.

create table if not exists public.trade_votes (
    league_id   uuid        not null references public.leagues (id) on delete cascade,
    season      integer     not null,
    week        integer     not null,
    trade       text        not null,
    voter       text        not null,
    side        text        not null check (side in ('a', 'b')),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    primary key (league_id, season, week, trade, voter)
);

-- Server-only, like every other table: RLS on, no policies.
alter table public.trade_votes enable row level security;
