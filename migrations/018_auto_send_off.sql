-- 018: weekly delivery is ON for paid leagues unless the owner turns it off
-- (24 Sep 2026)
--
-- Until now `auto_send` was an opt-in checkbox that started unticked, so a
-- commissioner who paid for weekly delivery got nothing unless they found the
-- box. The weekly job now sends for every league whose owner is on an active
-- paid plan, and this column records the one thing it needs to know on top of
-- that: that the owner deliberately switched it off.
--
-- Defaults to false, so every paid league starts receiving. Re-runnable.

alter table public.leagues
    add column if not exists auto_send_off boolean not null default false;
