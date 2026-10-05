-- 033: each league mate's team name (5 Oct 2026). Needs 013. Re-runnable.
--
-- John: "when they are filling in their information about the leaguemates in
-- onboarding, they see team names instead of usernames. Lots of ESPN
-- usernames are generic ESPNFAN123673893." The handle stays the key (013
-- explains why); the team name rides alongside so the boxes on the setup and
-- manage pages say whose they are. Written whenever the week's data is in
-- hand (seeding on first look, and every paper), so a renamed team catches up
-- with the next paper.

alter table public.managers add column if not exists team_name text;
