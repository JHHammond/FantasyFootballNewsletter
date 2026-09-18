-- ============================================================================
-- Reset a league, for testing
--
-- Paste into the Supabase SQL editor, set the slug on the line below, then run
-- ONE of the blocks. They are ordered from gentlest to nuclear. Nothing here
-- runs by accident: every block is commented out, so you have to uncomment the
-- one you want.
--
-- There is no undo on any of this. It is a testing tool, not an admin panel.
--
-- WHICH BLOCK DO YOU WANT?
--
--   A  Forget the people        — the "Who's in the league" boxes go blank and
--                                 refill themselves from the league next time
--                                 you open the manage page.
--   B  Un-publish one week      — that week's paper disappears from the
--                                 archive and its 3-regenerations-a-week
--                                 allowance resets. What you want when you are
--                                 iterating on how the paper READS.
--   C  Un-publish every week    — same, for the whole season.
--   D  Delete the league        — everything: the papers, the lore, the
--                                 people, the subscribers, the admin link.
--                                 The league ID becomes connectable again from
--                                 a clean slate, which is the only way to test
--                                 the setup flow twice.
--
-- The HTML files already uploaded to storage are NOT removed by any of this.
-- They are orphaned, they cost nothing, and regenerating the same week
-- overwrites them in place. If you want them gone, delete the league's folder
-- in the Storage browser.
-- ============================================================================

-- STEP 0 — find the league. Run this on its own first; it changes nothing.

select id, league_name, public_slug, season, provider, platform_league_id
  from public.leagues
 order by created_at desc;


-- Put the public_slug of the league you are resetting here, and leave it
-- here — every block below reads it.

-- \set slug 'kevlarville-7f3a'   -- (psql only; in the SQL editor just edit the
--                                   string in whichever block you run)


-- ---------------------------------------------------------------------------
-- A — Forget the people
--
-- The rows come straight back, empty, the next time the manage page is opened
-- or a paper generates. Use this to test the seeding, or to start the names
-- and notes over.
-- ---------------------------------------------------------------------------

-- delete from public.managers
--  where league_id = (select id from public.leagues
--                      where public_slug = 'PUT-THE-SLUG-HERE');


-- ---------------------------------------------------------------------------
-- B — Un-publish one week
--
-- The regeneration cap lives on the newspaper row, so deleting it gives that
-- week a fresh three. This is the one to use while you are tuning the writing.
-- ---------------------------------------------------------------------------

-- delete from public.newspapers
--  where league_id = (select id from public.leagues
--                      where public_slug = 'PUT-THE-SLUG-HERE')
--    and week = 1
--    and season = 2025;


-- ---------------------------------------------------------------------------
-- C — Un-publish every week of a season
-- ---------------------------------------------------------------------------

-- delete from public.newspapers
--  where league_id = (select id from public.leagues
--                      where public_slug = 'PUT-THE-SLUG-HERE')
--    and season = 2025;


-- ---------------------------------------------------------------------------
-- D — Delete the league entirely
--
-- lore, newspapers, subscribers, magic_links and managers all cascade off
-- this one row, so this is the whole thing. The admin link stops working
-- immediately; if it is the only copy you have of that link, you are not
-- getting back in, which is the point.
-- ---------------------------------------------------------------------------

-- delete from public.leagues where public_slug = 'PUT-THE-SLUG-HERE';


-- ---------------------------------------------------------------------------
-- Afterwards
--
-- Nothing here changes the table shape, so PostgREST's schema cache is fine
-- and you do NOT need `notify pgrst, 'reload schema';`. That line is only for
-- migrations.
-- ---------------------------------------------------------------------------
