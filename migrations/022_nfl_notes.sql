-- 022: The NFL wire (25 Sep 2026)
--
-- Real-world football news for a given week, written by staff, that every
-- paper that week should know: "Drake London had 11 catches because Michael
-- Penix Jr. was back at quarterback." The writer only sees what happened in
-- the box score; this is the WHY.
--
-- A note reaches a league's paper only if it names a player who is on a roster
-- in that league (full name), unless `all_leagues` is set.
--
-- Nothing depends on this table: until it exists, papers are written exactly
-- as before. Re-runnable.

create table if not exists public.nfl_notes (
    id           uuid        primary key default gen_random_uuid(),
    season       integer     not null,
    week         integer     not null,
    note         text        not null,
    all_leagues  boolean     not null default false,
    created_at   timestamptz not null default now()
);

create index if not exists nfl_notes_week on public.nfl_notes (season, week);

-- Server-only, like every other table: RLS on, no policies.
alter table public.nfl_notes enable row level security;
