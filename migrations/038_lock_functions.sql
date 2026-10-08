-- 038: only the server may call our database functions (8 Oct 2026).
-- Re-runnable. Run in the Supabase SQL editor.
--
-- 7 Oct security audit, finding #4. Postgres lets EVERYONE execute a new
-- function unless told otherwise, and Supabase's default privileges grant
-- anon and authenticated execute on new functions in public as well. Every
-- function in public is reachable through the API at /rest/v1/rpc/<name>,
-- so anybody holding the project's anon key could call them, including the
-- three that run with elevated rights:
--
--   bump_paper_views   inflate any paper's view count (the advertiser number)
--   claim_rate_slot    fill a rate-limit bucket and lock somebody out of login
--   sweep_rate_events  clear the rate limits
--
-- The anon key is not in any page we serve, so this was not reachable from
-- the site. It is closed anyway: the website talks to the database only
-- with the service_role key, and pg_cron runs as the owner, so neither needs
-- the public grants.

-- Everything that exists now.
revoke execute on all functions in schema public from public, anon, authenticated;
grant  execute on all functions in schema public to service_role;

-- Everything created later, by whoever runs migrations (postgres in the SQL
-- editor). Without this, the next migration's functions are open again.
-- Two statements because Postgres keeps two lists: Supabase's grant to anon
-- and authenticated is per schema, but "everyone may execute" (PUBLIC) is
-- global and can only be revoked globally. Tested: with only the first, a new
-- function was still callable by anon through PUBLIC.
alter default privileges for role postgres in schema public
    revoke execute on functions from public, anon, authenticated;
alter default privileges for role postgres
    revoke execute on functions from public;
alter default privileges for role postgres in schema public
    grant execute on functions to service_role;

-- The API reads its schema cache; tell it something changed.
notify pgrst, 'reload schema';

-- Check: should return no rows.
--   select p.proname, r.rolname
--     from pg_proc p join pg_namespace n on n.oid = p.pronamespace
--     cross join (values ('anon'), ('authenticated')) r(rolname)
--    where n.nspname = 'public' and has_function_privilege(r.rolname, p.oid, 'execute');
