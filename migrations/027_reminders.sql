-- 027: Weekly reminder emails to commissioners who aren't on a paid plan
-- (28 Sep 2026). Re-runnable.
--
-- email_optouts   anyone who clicked "unsubscribe" on a reminder. Checked
--                 before every send; one row per address, forever.
-- reminders_sent  one row per address per campaign ("2026-w3"), so running
--                 the reminder twice never mails anybody twice.

create table if not exists public.email_optouts (
    email       text        primary key,
    created_at  timestamptz not null default now()
);

create table if not exists public.reminders_sent (
    email       text        not null,
    campaign    text        not null,
    sent_at     timestamptz not null default now(),
    primary key (email, campaign)
);

-- Server-only, like every other table: RLS on, no policies.
alter table public.email_optouts  enable row level security;
alter table public.reminders_sent enable row level security;
