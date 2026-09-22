-- ============================================================================
-- The league's own awards
--
-- Run after 015_google.sql. Safe to re-run.
--
-- Alongside the paper's standing awards (Tony Snell, Kyle Pitts, Nick Foles,
-- Joe Burrow), a commissioner can add their own. Two kinds:
--
--   manual  the commissioner picks the winner, and a reason. The winner
--           stays until they change it, so a standing award only needs a new
--           name when it changes hands.
--   auto    the commissioner describes what it is for ("most points from a
--           kicker", "whoever lost to the worst team") and the writer picks
--           the winner from that week's numbers.
--
-- Without this migration the awards box on the account page says so and the
-- paper prints with its standing awards only. Nothing else depends on it.
-- ============================================================================

create table if not exists public.league_awards (
    id           uuid primary key default gen_random_uuid(),
    league_id    uuid not null references public.leagues (id) on delete cascade,
    name         text not null,
    mode         text not null default 'manual' check (mode in ('manual', 'auto')),
    criteria     text,
    winner       text,          -- manual: the team or person it goes to
    note         text,          -- manual: the commissioner's reason
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create index if not exists league_awards_league_idx
    on public.league_awards (league_id);

alter table public.league_awards enable row level security;

comment on table public.league_awards is
    'A league''s own awards: manual (commissioner picks) or auto (described, '
    'writer picks from the week).';

notify pgrst, 'reload schema';
