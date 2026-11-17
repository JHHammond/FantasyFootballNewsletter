-- ============================================================================
-- 070: the Modern theme (17 Nov 2026)
--
-- 007 limited leagues.theme to tabloid, broadsheet and gameday, so saving
-- "modern" failed the check and the setup page crashed. Widen the check.
-- Safe to re-run.
-- ============================================================================

alter table public.leagues drop constraint if exists leagues_theme_check;
alter table public.leagues add constraint leagues_theme_check
    check (theme in ('tabloid', 'broadsheet', 'gameday', 'modern'));
