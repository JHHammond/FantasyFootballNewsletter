-- 028: A "paid_users" view in the Table Editor (28 Sep 2026). Re-runnable.
--
-- A VIEW, not a copy: nothing is moved out of `users`, nothing has to be kept
-- in sync, and it is always current — somebody who pays shows up the moment
-- Stripe says so, and somebody who cancels drops off the same way.
--
-- "Paid" means what the app means (plans.plan_for): plan 'paid' with a
-- Stripe status that is active or trialing (or not yet set), plus staff.
-- A 'paid' row whose card failed (past_due, canceled...) is NOT in here,
-- because the app no longer treats them as paying either.
--
-- SECURITY: views in Supabase are exposed through the API, and a normal view
-- runs as its owner and would skip the RLS that keeps `users` private. So it
-- runs as the caller (security_invoker) and the public API roles get nothing.

create or replace view public.paid_users
with (security_invoker = true) as
select
    u.email,
    u.plan,
    u.plan_status,
    u.plan_renews_at,
    u.created_at                               as signed_up_at,
    u.last_login_at,
    (select count(*) from public.leagues l
      where l.user_id = u.id)                  as leagues,
    u.stripe_customer_id,
    u.id
from public.users u
where lower(u.plan) = 'staff'
   or (lower(u.plan) = 'paid'
       and (u.plan_status is null
            or lower(u.plan_status) in ('active', 'trialing')))
order by u.created_at desc;

revoke all on public.paid_users from anon, authenticated;
