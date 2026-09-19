-- ============================================================================
-- Signing in with Google
--
-- Run after 014_plans.sql. Safe to re-run.
--
-- TWO CHANGES, and the first one is the reason this needs a migration at all.
--
-- 1. password_hash stops being NOT NULL.
--
--    An account created through Google has no password and never will. The
--    column was declared `text not null` in 010, so without this the insert
--    fails — and it fails at the last step of a sign-in flow the person has
--    already completed, which is the worst possible place to discover it.
--
-- 2. google_sub, unique.
--
--    `sub` is Google's stable identifier for a person. It is NOT their email
--    address, and that distinction is the whole security of this feature:
--
--      - People change their email address. Google keeps the same sub.
--      - Two accounts can hold the same address over time; sub never repeats.
--      - Matching on the address alone is how an OAuth login becomes an
--        account takeover, because an identity provider that does not verify
--        addresses will happily assert somebody else's.
--
--    So sub is the key. The address is a label we store for display and for
--    the one carefully-guarded case where an existing password account gets
--    linked — and that case additionally requires Google's `email_verified`
--    claim to be true. See web/oauth.py.
-- ============================================================================

-- A Google account has no password. Existing rows keep theirs.
alter table public.users
    alter column password_hash drop not null;

alter table public.users
    -- Google's `sub` claim. Null for every account that has only ever used a
    -- password.
    add column if not exists google_sub text,

    -- What Google says their address is, kept separate from `email` so that
    -- somebody changing their Google address does not silently change the
    -- address this app emails.
    add column if not exists google_email text,

    add column if not exists google_linked_at timestamptz;

-- One account per Google identity. Partial, because every password-only
-- account has a null here.
create unique index if not exists users_google_sub_idx
    on public.users (google_sub)
 where google_sub is not null;

comment on column public.users.google_sub is
    'Google''s stable subject id. The join key for Google sign-in — never the '
    'email address, which people change and which an identity provider can '
    'assert without having verified.';

-- Remember: PostgREST caches the table shape.
--   notify pgrst, 'reload schema';
