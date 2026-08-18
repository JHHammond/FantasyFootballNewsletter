-- ============================================================================
-- Setup questions: the things the platform API can't tell us
--
-- Run after 004_lore.sql. Safe to re-run.
--
-- The discipline here is to ask ONLY what we can't already read from Sleeper.
-- Scoring type, superflex, team count, roster slots and the league's real name
-- all come back from the API — asking for them again is a form for no reason.
-- What the API cannot know is why this league is funny, and that's all that's
-- below.
-- ============================================================================

-- redraft | keeper | dynasty
-- Changes what the columnist can even reference. In a dynasty league,
-- rookie picks, rebuilds and long-term trades are real material; in a redraft
-- league they don't exist and mentioning them makes the paper sound generic.
alter table public.leagues add column if not exists format text not null default 'redraft';

-- friendly | standard | brutal
-- The house voice is savage by default. Not every league wants that — some
-- have a boss or a spouse in them — so this is both a product feature and the
-- safety valve on a product whose whole job is insulting people.
alter table public.leagues add column if not exists tone text not null default 'standard';

-- How long the league has existed. A twelve-year-old league has history worth
-- referencing; a first-year league doesn't, and pretending otherwise reads
-- false.
alter table public.leagues add column if not exists founded_year integer;

-- What they play for: buy-in, a trophy, bragging rights.
alter table public.leagues add column if not exists stakes text;

-- The last-place punishment. Richest single vein of material in fantasy
-- football, and the API will never know about it.
alter table public.leagues add column if not exists punishment text;

-- Whether the commissioner has been through the setup questions, so we can
-- nudge once and then stop.
alter table public.leagues add column if not exists setup_complete boolean not null default false;

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'leagues_format_check') then
        alter table public.leagues add constraint leagues_format_check
            check (format in ('redraft', 'keeper', 'dynasty'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'leagues_tone_check') then
        alter table public.leagues add constraint leagues_tone_check
            check (tone in ('friendly', 'standard', 'brutal'));
    end if;
end $$;
