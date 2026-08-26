-- ============================================================================
-- Paper themes
--
-- Run after 006_edits.sql. Safe to re-run.
--
-- The default look is a tabloid — loud, red, all-caps. That's right for a lot
-- of leagues and wrong for others, so the visual register is now a choice
-- alongside the tone of the writing.
-- ============================================================================

alter table public.leagues add column if not exists theme text not null default 'tabloid';

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'leagues_theme_check') then
        alter table public.leagues add constraint leagues_theme_check
            check (theme in ('tabloid', 'broadsheet', 'gameday'));
    end if;
end $$;
