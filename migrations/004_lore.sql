-- ============================================================================
-- Rename inside_jokes -> lore
--
-- "Inside jokes" undersold what this field is for. Half of what makes a league
-- paper land isn't a joke at all — it's history. Who's still bitter about a
-- 2022 trade, who's never won anything, who drafted a kicker in the eighth
-- round. "Lore" gets people to type the good stuff.
--
-- Idempotent: renames the old table if it's there, creates the new one if it
-- isn't. Safe whether or not you've already run 002.
-- ============================================================================

do $$
begin
    if exists (select 1 from information_schema.tables
                where table_schema = 'public' and table_name = 'inside_jokes')
       and not exists (select 1 from information_schema.tables
                where table_schema = 'public' and table_name = 'lore')
    then
        alter table public.inside_jokes rename to lore;
        alter table public.lore rename column joke to entry;
    end if;
end $$;

create table if not exists public.lore (
    id          uuid primary key default gen_random_uuid(),
    league_id   uuid not null references public.leagues (id) on delete cascade,
    entry       text not null,
    active      boolean not null default true,
    created_at  timestamptz not null default now()
);

-- Index names follow the table through a rename, so give them the new name.
-- `ALTER INDEX IF EXISTS ... RENAME TO` only guards the SOURCE name — it still
-- errors if the destination already exists, which makes a second run fail. So
-- check both.
do $$
begin
    if exists (select 1 from pg_class where relname = 'inside_jokes_league_active_idx')
       and not exists (select 1 from pg_class where relname = 'lore_league_active_idx')
    then
        alter index inside_jokes_league_active_idx rename to lore_league_active_idx;
    end if;
end $$;

create index if not exists lore_league_active_idx on public.lore (league_id, active);

alter table public.lore enable row level security;

drop policy if exists "jokes follow league ownership" on public.lore;
