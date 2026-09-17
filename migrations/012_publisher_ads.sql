-- ============================================================================
-- The classifieds page
--
-- Run after 011_generation_count.sql. Safe to re-run.
--
-- THIS IS THE FIRST GLOBAL TABLE IN THE APP.
--
-- Everything else is per-league: a league owns its papers, its lore, its
-- photos, and an admin_token is the proof you may touch them. Nothing has ever
-- belonged to *the publisher* — there was no publisher.
--
-- These rows are one page, written once a week by John, that appears in every
-- league's paper. So: no league_id, and nothing here derives permission from a
-- league's admin_token. The only key that opens it is PUBLISHER_TOKEN, held in
-- the environment, checked in one place (`_require_publisher` in web/app.py).
-- If that variable is unset the routes 404 — off, rather than open.
--
-- Keyed by (season, week) because the page is a week's page. A Week 2 paper
-- opened in December has to show Week 2's ads, not December's; the generator
-- also copies the resolved list into the paper's own stored content, so even
-- deleting a row here cannot rewrite a paper that already went out.
-- ============================================================================

create table if not exists public.publisher_ads (
    id           uuid primary key default gen_random_uuid(),

    season       integer not null,
    week         integer not null,

    -- Where it sits on the page. Ties break by created_at so a reorder that
    -- half-applied still produces a stable page rather than a random one.
    position     integer not null default 0,

    image_url    text    not null,
    -- Kept so the image can be removed from the bucket when the row goes.
    -- Without it, deleting an ad leaks the file forever.
    storage_path text,

    -- The image's real pixel size, read from its header at upload time. The
    -- page reserves the right shape before the image loads, which is what
    -- stops the layout jumping, and it is what decides whether an ad is wide
    -- enough to run as a banner across both columns.
    width        integer,
    height       integer,

    -- Shown under the ad in small caps, and used as the img alt text. Optional:
    -- a meme usually speaks for itself.
    caption      text,
    -- For a real advertiser, eventually. Nothing renders a link without one.
    link_url     text,

    created_at   timestamptz not null default now()
);

create index if not exists publisher_ads_week_idx
    on public.publisher_ads (season, week, position, created_at);

-- Same posture as every other table: RLS on, zero policies, which denies
-- everything to the anon and authenticated keys. The server holds the
-- service_role key and is the only thing that ever reads or writes this.
alter table public.publisher_ads enable row level security;

comment on table public.publisher_ads is
    'The weekly classifieds page. Global, not per-league. Written only by the '
    'holder of PUBLISHER_TOKEN.';

-- Remember: PostgREST caches the table shape.
--   notify pgrst, 'reload schema';
