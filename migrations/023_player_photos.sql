-- 023: The photo desk (25 Sep 2026)
--
-- Staff photos tied to a player, for one week or for any week. Any paper whose
-- featured player has one uses it instead of the platform headshot; a photo
-- the commissioner uploaded still wins over both.
--
-- The image lives in the papers bucket and papers LINK to it, so deleting a
-- photo here takes it out of every paper that used it.
--
-- Nothing depends on this table: until it exists, papers use headshots as
-- before. Re-runnable.

create table if not exists public.player_photos (
    id            uuid        primary key default gen_random_uuid(),
    season        integer     not null,
    week          integer,                 -- null = any week
    player_name   text        not null,
    image_url     text        not null,
    storage_path  text,
    caption       text,
    credit        text,
    created_at    timestamptz not null default now()
);

create index if not exists player_photos_season on public.player_photos (season, week);

-- Server-only, like every other table: RLS on, no policies.
alter table public.player_photos enable row level security;
