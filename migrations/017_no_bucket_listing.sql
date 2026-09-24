-- 017: stop the newspapers bucket from being listable (24 Sep 2026)
--
-- 002 added a SELECT policy on storage.objects for the newspapers bucket.
-- A PUBLIC bucket does not need it: files are served from their public URL
-- without any policy. What the policy DOES allow is listing — anyone holding
-- the project's anon key could ask Storage for every object in the bucket,
-- which is every paper and every uploaded photo, i.e. every league.
--
-- The server reads papers with the service role key, which ignores policies,
-- so nothing in the app depends on this. Re-runnable.

drop policy if exists "newspapers are publicly readable" on storage.objects;
