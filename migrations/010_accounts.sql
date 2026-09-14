-- 010: accounts.
--
-- The product shipped without them on purpose: monetization is ad impressions
-- from readers, and a signup wall in front of the one person who creates a
-- paper does nothing for the ten who read it. That reasoning still holds for
-- readers, and nothing here changes their experience — /p/ stays open to
-- anyone with a link, forever, with no session and no cookie.
--
-- What it did not survive is the commissioner's side. The admin token was the
-- only credential, it lived in one URL, and losing that URL was unrecoverable:
-- re-submitting the league ID hit "this league already has a paper", and
-- /recover only worked for people who had already saved an email — which
-- happened on a page you reached *after* the moment things went wrong. A dead
-- end sitting in the signup path.
--
-- So: accounts, with the admin token kept alongside rather than replaced.
-- Existing leagues keep working untouched, and a bookmarked manage link still
-- opens without a login.
--
-- Idempotent, like the rest of the chain.

-- --------------------------------------------------------------------------
-- 1. Users.
--
-- Email is the identity and is stored lower-cased — addresses are compared
-- case-insensitively by every mail provider that matters, and a UNIQUE index
-- on mixed case would happily accept John@ and john@ as two accounts.
-- --------------------------------------------------------------------------

create table if not exists public.users (
    id             uuid primary key default gen_random_uuid(),
    email          text        not null,
    password_hash  text        not null,
    created_at     timestamptz not null default now(),
    last_login_at  timestamptz,
    verified_at    timestamptz
);

-- Lower-cased uniqueness, enforced by the database rather than by remembering
-- to call .lower() at every call site.
create unique index if not exists users_email_key
    on public.users (lower(email));

alter table public.users enable row level security;
-- No policies, deliberately: deny-all to every browser-facing role. The server
-- holds the service key. Same posture as every other table here, and it
-- matters more for this one — it holds password hashes.


-- --------------------------------------------------------------------------
-- 2. Leagues belong to a user, or to nobody.
--
-- Nullable on purpose. Every league that exists today has no user, and those
-- must keep working through their admin token exactly as before. A null here
-- means "token-only", not "broken".
--
-- ON DELETE SET NULL rather than CASCADE: deleting an account should not
-- silently destroy papers that readers may still be linking to. The league
-- falls back to token-only access.
-- --------------------------------------------------------------------------

alter table public.leagues
    add column if not exists user_id uuid references public.users (id) on delete set null;

create index if not exists leagues_user_idx on public.leagues (user_id);


-- --------------------------------------------------------------------------
-- 3. Password resets reuse magic_links.
--
-- That table already does exactly this job: a single-use token with an expiry,
-- burned on use. A second near-identical table would be a second place for
-- single-use logic to drift and get subtly wrong.
--
-- `purpose` separates the two uses so a link mailed for recovery can never be
-- redeemed as a password reset. Existing rows predate accounts and are all
-- recovery links, which is what the default says.
-- --------------------------------------------------------------------------

alter table public.magic_links
    add column if not exists purpose text not null default 'recover';

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'magic_links_purpose_check') then
        alter table public.magic_links add constraint magic_links_purpose_check
            check (purpose in ('recover', 'reset', 'verify'));
    end if;
end $$;

create index if not exists magic_links_purpose_idx
    on public.magic_links (purpose, expires_at desc);
