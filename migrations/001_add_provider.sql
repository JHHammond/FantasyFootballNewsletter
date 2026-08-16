-- Adds multi-platform support to the leagues table.
-- Safe to run more than once. Run this in the Supabase SQL editor.
--
-- Before: every league was assumed to be a Sleeper league, and its ID lived in
-- `sleeper_league_id`. After: `provider` names the platform and
-- `platform_league_id` holds that platform's ID. The old column is kept and
-- backfilled so nothing that still reads it breaks.

alter table leagues
  add column if not exists provider text not null default 'sleeper';

alter table leagues
  add column if not exists platform_league_id text;

-- Backfill existing rows.
update leagues
   set platform_league_id = sleeper_league_id
 where platform_league_id is null;

-- One league per platform per user.
create unique index if not exists leagues_user_provider_league_idx
  on leagues (user_id, provider, platform_league_id);

-- Only platforms we actually have an adapter for.
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'leagues_provider_check'
  ) then
    alter table leagues
      add constraint leagues_provider_check
      check (provider in ('sleeper', 'espn', 'yahoo'));
  end if;
end $$;

-- Note: `sleeper_league_id` is now redundant. Once you've confirmed nothing
-- reads it, drop it with:
--   alter table leagues drop column sleeper_league_id;
