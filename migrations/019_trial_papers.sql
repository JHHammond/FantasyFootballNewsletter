-- 019: the free trial ledger (25 Sep 2026)
--
-- A free account gets three papers per REAL league — the Sleeper or ESPN
-- league itself, not our row for it. Keyed on the platform's own league id
-- and the season, so deleting a league and connecting it again, or doing it
-- from a second account, finds the same three weeks already used.
--
-- One row per week a free paper was written for. Regenerating a week does not
-- add a row. Deliberately NOT a foreign key to leagues: the whole point is
-- that it outlives the league row.
--
-- Everyone starts at zero when this ships. Re-runnable.

create table if not exists public.trial_papers (
    provider            text        not null,
    platform_league_id  text        not null,
    season              integer     not null,
    week                integer     not null,
    created_at          timestamptz not null default now(),
    primary key (provider, platform_league_id, season, week)
);

-- Server-only, like every other table: RLS on, no policies.
alter table public.trial_papers enable row level security;
