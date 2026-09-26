-- 025: The commissioner's jokes (26 Sep 2026)
--
-- lore.always: a bit of lore marked "use it every week" goes to a recap that
-- MUST use it, instead of waiting for the week to give it a reason.
-- leagues.nicknames: "real name = nickname", one per line, used by the writer.
--
-- Nothing depends on these: until they exist, lore behaves as before and the
-- nicknames box says it needs this update. Re-runnable.

alter table public.lore    add column if not exists always    boolean not null default false;
alter table public.leagues add column if not exists nicknames text;
