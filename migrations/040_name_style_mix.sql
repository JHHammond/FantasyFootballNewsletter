-- 040: a third way to name people in the paper, "mix" (9 Oct 2026). Needs 039.
-- Re-runnable.
--
-- John: a "mix it up" option that goes back and forth between team names and
-- first names. Only the check constraint from 039 changes.

alter table public.leagues drop constraint if exists leagues_name_style_check;
alter table public.leagues
    add constraint leagues_name_style_check
    check (name_style in ('team', 'first', 'mix'));
