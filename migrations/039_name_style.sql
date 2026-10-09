-- 039: how the paper refers to the people in a league (9 Oct 2026). Re-runnable.
--
-- John: let the commissioner decide whether the paper calls leaguemates by
-- their team names or by the first names typed in on the leaguemates step.
-- 'team' is the default and how every paper read before this existed.
-- A person with no name typed falls back to their team name either way.
--
-- The app keeps working before this runs: a save that names the column is
-- retried without it, and a league row without it reads as 'team'.

alter table public.leagues
    add column if not exists name_style text not null default 'team';

do $$
begin
    if not exists (select 1 from pg_constraint
                   where conname = 'leagues_name_style_check') then
        alter table public.leagues
            add constraint leagues_name_style_check
            check (name_style in ('team', 'first'));
    end if;
end $$;
