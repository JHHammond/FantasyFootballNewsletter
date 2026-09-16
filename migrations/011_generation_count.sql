-- ============================================================================
-- Count how many times each paper has been generated
--
-- Run after 010_accounts.sql. Safe to re-run.
--
-- There has never been a per-week cap: the only ceilings were 12 papers per
-- league per DAY and the global daily budget. A commissioner who kept hitting
-- Generate could burn twelve papers' worth of Claude calls on one week and
-- have nothing tell them they were doing it.
--
-- Three regenerations a week is the allowance. The number that makes it usable
-- is the one shown on the page BEFORE the button is pressed — "2 left" is a
-- different product from finding out you are out of them.
--
-- Counts renders, so the first generation is 1 and regenerations_used is
-- count - 1. Existing rows backfill to 1: they have been generated at least
-- once, by definition, and nobody should lose an allowance to this migration.
-- ============================================================================

alter table public.newspapers
    add column if not exists generation_count integer not null default 1;

-- Anything already in the table was generated at least once.
update public.newspapers
   set generation_count = 1
 where generation_count is null
    or generation_count < 1;

comment on column public.newspapers.generation_count is
    'Total renders of this paper. Regenerations used = this minus one.';

-- Remember: PostgREST caches the table shape.
--   notify pgrst, 'reload schema';
