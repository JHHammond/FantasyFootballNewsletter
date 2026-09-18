-- ============================================================================
-- The people in the league
--
-- Run after 012_publisher_ads.sql. Safe to re-run.
--
-- WHY THIS EXISTS
--
-- Lore was one flat list per league: "Nick benches his best guy every week",
-- "anyone who starts a zero has to chug". That is the LEAGUE's lore, and it
-- works. What it cannot express is the thing that actually makes a paper feel
-- written by somebody who was there — what is true about one specific person,
-- every week, whether or not it triggered.
--
-- It also could not say anybody's NAME. The platforms hand over handles:
-- `alexvierheilig4`, `WillDavidson10`, `johnhenryhammond`. The paper has been
-- printing those in the middle of sentences all season because it had nothing
-- else, and "Alex commissions a mercy rule, still wins" is a different
-- sentence from "Alexvierheilig4 commissions a mercy rule, still wins".
--
-- So: one row per league mate, carrying a real name and the notes about them.
-- The existing `lore` table keeps doing what it does — the league-wide stuff.
--
-- HANDLE IS THE KEY, not a platform id. The paper is written from team and
-- owner names, and that is what the commissioner sees on this page, so that
-- is what the notes have to attach to. A platform id would be stabler and
-- would mean nothing to anybody filling the form in.
-- ============================================================================

create table if not exists public.managers (
    id            uuid primary key default gen_random_uuid(),
    league_id     uuid not null references public.leagues (id) on delete cascade,

    -- As the platform reports it. Populated from the week data every time a
    -- paper generates, so the list keeps up with a league that adds a team or
    -- somebody who renames themselves mid-season.
    handle        text not null,

    -- What to call them in the paper. Null means the paper keeps using the
    -- handle, which is what it does today.
    display_name  text,

    -- Lore about this person specifically.
    notes         text,

    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

-- One row per person per league. The upsert on generation depends on this.
create unique index if not exists managers_league_handle_idx
    on public.managers (league_id, handle);

-- Same posture as every other table: RLS on, zero policies, which denies
-- everything to the anon and authenticated keys. The server holds the
-- service_role key and is the only thing that touches this.
alter table public.managers enable row level security;

comment on table public.managers is
    'One row per league mate: their real name and the lore about them. '
    'League-wide lore stays in the lore table.';

-- Remember: PostgREST caches the table shape.
--   notify pgrst, 'reload schema';
