-- 035: the homepage's "best PPR game" lookup (5 Oct 2026). Needs 029.
-- Re-runnable.
--
-- db.top_ppr_game() asks for the single highest started PPR score in a week.
-- The only index on lineup_players by week was (season, week, player_key),
-- so Postgres read every starter of the week and sorted them, on every
-- homepage refresh. With this it reads one row.
--
-- Run it from the SQL editor in one go: the first line lifts the editor's
-- time limit for this session, because building the index over a full
-- season of lineups can take longer than the default allows.

set statement_timeout = '10min';

create index if not exists lineup_players_week_ppr
    on public.lineup_players (season, week, ppr_points desc)
    where started and ppr_points is not null;
