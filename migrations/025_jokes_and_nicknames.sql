-- 025: The commissioner's jokes (26 Sep 2026)
--
-- lore.always: a bit of lore marked "use it every week" goes to a recap that
-- MUST use it, instead of waiting for the week to give it a reason.
-- (leagues.nicknames was added here briefly and is no longer used: player
-- nicknames live on the NFL wire page instead, migration 026.)
--
-- Nothing depends on it: until it exists, lore behaves as before. Re-runnable.

alter table public.lore    add column if not exists always    boolean not null default false;
