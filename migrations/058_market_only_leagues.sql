-- 058: leagues connected only to see your team on the trade market (23 Oct 2026)
--
-- "Show my team" connects a league so the market can draw the viewer's own
-- roster. That league is not a paper: the Tuesday job must not write one
-- (it costs money), the weekly reminder must not nag about one, and it must
-- not use up the free plan's one paper. Connecting the same league again
-- the normal way, to make a paper, clears the flag.

alter table public.leagues
    add column if not exists market_only boolean not null default false;

notify pgrst, 'reload schema';
