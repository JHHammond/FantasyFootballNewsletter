-- 030: The editor's desk on the NFL wire page (30 Sep 2026).
--
-- A joke or an instruction from the editor, tied to one or more NFL players:
-- "Nobody expected Marcus Mariota to play well, so compliment every manager
-- who started him." It reaches the recap of every game where a named player
-- is on either roster, in every league, and nowhere else. Never required:
-- a paper without those players never sees it.
--
-- week null = every week until removed. Nothing depends on this table.
-- Re-runnable.

create table if not exists public.editor_bits (
    id          uuid        primary key default gen_random_uuid(),
    season      integer     not null,
    week        integer,
    kind        text        not null default 'joke',   -- joke | instruction
    players     text[]      not null,
    body        text        not null,
    created_at  timestamptz not null default now()
);

-- Server-only, like every other table: RLS on, no policies.
alter table public.editor_bits enable row level security;
