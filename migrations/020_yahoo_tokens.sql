-- 020: Yahoo sign-in tokens (25 Sep 2026)
--
-- Yahoo shows a league only to a signed-in member, every time -- including
-- when the Tuesday job runs with nobody around. So an account that connects
-- Yahoo leaves a refresh token here, and the job trades it for an hour-long
-- access token whenever it needs one.
--
-- Both tokens are ENCRYPTED by the app before they arrive (web/yahoo_auth.py),
-- so this table holds ciphertext. Read-only scope: nothing stored here can
-- change anybody's lineup. Deleting the account deletes the row.
--
-- Nothing else depends on this table: until it exists, Yahoo simply stays
-- unavailable and every other platform works as before. Re-runnable.

create table if not exists public.yahoo_tokens (
    user_id        uuid        primary key references public.users (id) on delete cascade,
    yahoo_guid     text,
    refresh_token  text        not null,
    access_token   text,
    expires_at     timestamptz,
    updated_at     timestamptz not null default now()
);

-- Server-only, like every other table: RLS on, no policies.
alter table public.yahoo_tokens enable row level security;
