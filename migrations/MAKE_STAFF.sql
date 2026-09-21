-- ============================================================================
-- Put an account on the staff plan
--
-- Staff gets everything the paid plan has plus 100 regenerations a week per
-- paper, for testing. It is never billed and Stripe's webhook will not touch
-- it. Needs 014_plans.sql to have been run.
--
-- Change the email below to the one you sign in with, then run the whole file.
-- ============================================================================

update public.users
   set plan = 'staff', plan_updated_at = now()
 where email = lower('commissionersdesk@gmail.com');

-- Should show one row with plan = staff. No rows means the email didn't match
-- an account: check which address you signed up with.
select email, plan from public.users where plan = 'staff';


-- ----------------------------------------------------------------------------
-- To undo:
--
--   update public.users set plan = 'free' where email = lower('...');
-- ----------------------------------------------------------------------------
