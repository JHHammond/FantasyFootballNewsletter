-- 026: Player nicknames on the NFL wire (27 Sep 2026)
--
-- Staff-set, for every league: "Kenneth Walker" is "K9". A paper gets a
-- nickname only when that player is on one of its rosters (full-name match,
-- like the wire notes). Nothing depends on this table. Re-runnable.

create table if not exists public.player_nicknames (
    id           uuid        primary key default gen_random_uuid(),
    player_name  text        not null,
    nickname     text        not null,
    created_at   timestamptz not null default now()
);

-- Server-only, like every other table: RLS on, no policies.
alter table public.player_nicknames enable row level security;
