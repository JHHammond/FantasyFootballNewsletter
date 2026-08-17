-- ============================================================================
-- Email capture, subscriptions, and magic-link recovery
--
-- Run AFTER 002_accountless.sql. Safe to re-run.
--
-- THE MODEL
--
-- Still no accounts, still no passwords. Email does two jobs:
--
--   1. Recovery. A commissioner who gives us their address can get their
--      manage link re-sent. That's what "account" was really for.
--   2. Distribution. Readers subscribe from inside the paper and get next
--      week's edition delivered, so reach stops depending on one person
--      remembering to paste a link into the group chat every Monday.
--
-- Both asks happen AFTER the product has proven itself, never before.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- leagues: owner email + auto-send preference
-- ---------------------------------------------------------------------------

alter table public.leagues add column if not exists owner_email text;
alter table public.leagues add column if not exists auto_send boolean not null default false;

-- Non-unique: one person may run several leagues off one address, and magic
-- link recovery is expected to return all of them.
create index if not exists leagues_owner_email_idx
    on public.leagues (lower(owner_email));

-- ---------------------------------------------------------------------------
-- subscribers
--
-- Double opt-in: a row is created unconfirmed and only receives mail once the
-- address has been verified. Costs some conversion, protects deliverability,
-- and stops anyone signing up an address they don't own.
-- ---------------------------------------------------------------------------

create table if not exists public.subscribers (
    id                  uuid primary key default gen_random_uuid(),
    league_id           uuid not null references public.leagues (id) on delete cascade,
    email               text not null,

    confirmed           boolean not null default false,
    confirm_token       text not null,
    -- Every marketing email carries this. One click, no login, no "are you
    -- sure" page. Required by CAN-SPAM and good practice regardless.
    unsubscribe_token   text not null,

    unsubscribed_at     timestamptz,
    confirmed_at        timestamptz,
    created_at          timestamptz not null default now(),

    -- 'reader' | 'archive' | 'commissioner' — which capture point worked.
    source              text not null default 'reader'
);

-- One subscription per address per league. Re-subscribing updates the row
-- rather than creating a duplicate.
create unique index if not exists subscribers_league_email_idx
    on public.subscribers (league_id, lower(email));

create unique index if not exists subscribers_confirm_token_idx
    on public.subscribers (confirm_token);

create unique index if not exists subscribers_unsubscribe_token_idx
    on public.subscribers (unsubscribe_token);

-- The query the weekly send runs: confirmed and not unsubscribed.
create index if not exists subscribers_active_idx
    on public.subscribers (league_id)
    where confirmed = true and unsubscribed_at is null;

-- ---------------------------------------------------------------------------
-- magic_links
--
-- Short-lived, single-use tokens that return a commissioner to their manage
-- page. This is the whole of "authentication" in this app.
-- ---------------------------------------------------------------------------

create table if not exists public.magic_links (
    id          uuid primary key default gen_random_uuid(),
    email       text not null,
    token       text not null,
    expires_at  timestamptz not null,
    used_at     timestamptz,
    created_at  timestamptz not null default now()
);

create unique index if not exists magic_links_token_idx on public.magic_links (token);
create index if not exists magic_links_email_idx on public.magic_links (lower(email));

-- ---------------------------------------------------------------------------
-- newspapers: track what has already been mailed
--
-- The weekly job must be safe to run twice. A cron that fires on a retry, or
-- two dynos both waking up, must not mail every subscriber a duplicate.
-- ---------------------------------------------------------------------------

alter table public.newspapers add column if not exists emailed_at timestamptz;

-- ---------------------------------------------------------------------------
-- RLS: same posture as everything else. Enabled, no policies, deny by default.
-- The server holds the service role key and is the only reader.
--
-- This matters more here than elsewhere: these tables hold email addresses.
-- ---------------------------------------------------------------------------

alter table public.subscribers enable row level security;
alter table public.magic_links enable row level security;

-- ---------------------------------------------------------------------------
-- Housekeeping: expired magic links are dead weight. Call periodically, or
-- schedule with pg_cron if you enable it.
-- ---------------------------------------------------------------------------

create or replace function public.purge_expired_magic_links()
returns integer language plpgsql as $$
declare
    removed integer;
begin
    delete from public.magic_links
     where expires_at < now() - interval '1 day';
    get diagnostics removed = row_count;
    return removed;
end;
$$;

-- ============================================================================
-- Next: add to .env
--   RESEND_API_KEY=re_...
--   EMAIL_FROM="The Commissioner's Desk <papers@yourdomain.com>"
--   MAILING_ADDRESS="Your Name, 123 Street, City ST 00000"   (CAN-SPAM)
--   BASE_URL=https://yourdomain.com
--   TASK_KEY=<random string, protects the weekly send endpoint>
-- ============================================================================
