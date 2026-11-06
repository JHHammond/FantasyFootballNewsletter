-- 069: paying owners can take the sponsor out of their league's paper
-- (6 Nov 2026; PrizePicks agreed to it for paying customers)
--
-- Records the owner's choice only. Whether it applies is decided at render
-- time from the owner's plan (plans.Plan.ad_free), so a lapsed subscription
-- puts the sponsor back without this column changing, and renewing takes it
-- out again. Defaults to false: every paper carries the sponsor until its
-- owner opts out. Re-runnable.

alter table public.leagues
    add column if not exists ads_off boolean not null default false;

notify pgrst, 'reload schema';
