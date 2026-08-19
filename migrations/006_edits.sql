-- ============================================================================
-- Let the commissioner edit what Claude wrote
--
-- Run after 005_setup.sql. Safe to re-run.
--
-- The paper is rendered from `ai_cache`, which already holds exactly the
-- structure the renderer consumes. So editing is just changing that JSON and
-- re-rendering — no second API call, nothing regenerated, no cost.
--
-- Two columns make it safe:
--
--   ai_cache_original  — what Claude actually wrote, kept forever. Means
--                        "revert" is possible without re-generating, and the
--                        original stays available if a joke lands badly and
--                        needs walking back.
--   edited_at          — so regeneration can warn before discarding edits,
--                        rather than silently throwing away someone's work.
-- ============================================================================

alter table public.newspapers add column if not exists ai_cache_original jsonb;
alter table public.newspapers add column if not exists edited_at timestamptz;

-- Backfill: anything already generated is, by definition, unedited.
update public.newspapers
   set ai_cache_original = ai_cache
 where ai_cache_original is null
   and ai_cache is not null;
