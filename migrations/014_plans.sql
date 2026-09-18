-- ============================================================================
-- The plan, on the person
--
-- Run after 013_managers.sql. Safe to re-run.
--
-- WHY IT HANGS OFF users AND NOT leagues
--
-- One member of a league pays and everything they own is covered. Not
-- necessarily the commissioner — the person who cares enough to pay is not
-- always the person who set the league up, and making the commissioner the
-- only possible buyer loses the sale in a lot of leagues.
--
-- A league with no account behind it (created from a manage link, which is
-- how every league from before accounts works) has no user, therefore no
-- plan, therefore the free tier. That is the correct answer and it needs no
-- special case anywhere.
--
-- STRIPE IS THE SOURCE OF TRUTH, and these columns are a cache of it. They
-- are written by the webhook and read on every request, because reading them
-- is free and asking Stripe is a network call on the critical path of every
-- page. If they ever disagree with Stripe, Stripe is right — plan_status is
-- checked against the live subscription whenever somebody opens their
-- account page.
-- ============================================================================

alter table public.users
    -- 'free' or 'paid'. Defaulted, so every existing account is on the free
    -- plan the moment this runs and nobody loses anything they had.
    add column if not exists plan text not null default 'free',

    -- Stripe's own word for the subscription: active, trialing, past_due,
    -- canceled, unpaid, incomplete, incomplete_expired, paused. Null for
    -- somebody who has never subscribed.
    --
    -- Both columns are checked. `plan` alone would keep a cancelled account
    -- on the paid tier forever if a webhook were ever missed; the status is
    -- what catches that.
    add column if not exists plan_status text,

    -- Stripe's customer and subscription. The customer is reused so that
    -- somebody who subscribes, cancels and comes back keeps one billing
    -- history instead of accumulating duplicate customers.
    add column if not exists stripe_customer_id text,
    add column if not exists stripe_subscription_id text,

    -- When the current paid period runs out. Stored for the account page
    -- ("paid through 14 October"), not used to decide access — the status is.
    --
    -- NOTE for anyone extending this: current_period_end moved off the
    -- Subscription object and onto the subscription ITEM in Stripe's
    -- 2025-03-31 API version. Reading it off the subscription now returns
    -- nothing, silently.
    add column if not exists plan_renews_at timestamptz,

    add column if not exists plan_updated_at timestamptz;

-- The webhook arrives knowing a Stripe customer id and nothing else, so this
-- is the lookup that has to be fast and has to be unique. Partial, because
-- every account that has never opened checkout has a null here and nulls are
-- not unique to each other in a plain unique index — but being explicit about
-- it documents the intent.
create unique index if not exists users_stripe_customer_idx
    on public.users (stripe_customer_id)
 where stripe_customer_id is not null;

comment on column public.users.plan is
    'free or paid. A cache of Stripe; plan_status is checked alongside it so '
    'a missed webhook cannot leave a cancelled account on the paid tier.';

-- Remember: PostgREST caches the table shape.
--   notify pgrst, 'reload schema';
