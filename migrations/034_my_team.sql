-- Which team in a league is yours (4 Oct 2026).
--
-- John: "when I put in my Sleeper account, it should automatically know who
-- I am." Connecting through a Sleeper username tells us the account's
-- user_id; the league's rosters say which roster that user owns. That
-- roster_id (the team_id everywhere else) is saved here, and /rankings opens
-- on it instead of asking "Which one is you?".
--
-- Empty for leagues connected by league ID alone. Safe to re-run.

alter table public.leagues add column if not exists my_team_id text;

notify pgrst, 'reload schema';
