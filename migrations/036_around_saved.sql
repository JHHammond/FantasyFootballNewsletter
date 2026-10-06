-- 036: Around the Leagues, saved (6 Oct 2026). Needs 029. Re-runnable.
--
-- around_week() and around_season() add up every team and every starter in
-- the country. After Tuesday's backfill (about 5,500 more league-weeks) they
-- take longer than the API's time limit, so the /around page failed with
-- "Something broke on our end" whenever its cache was empty, e.g. after a
-- deploy.
--
-- Same fix as the National Rankings (033): the answers are worked out by a
-- scheduled job, which has no API time limit, and saved in around_saved.
-- The website reads the saved row in milliseconds and only falls back to
-- the live functions for a week the job hasn't reached yet.
--
-- refresh_around() only recomputes a week whose data changed since it was
-- last saved (a cheap row count), so running it every 10 minutes costs
-- almost nothing on quiet days. By hand:  select public.refresh_around();
--
-- Run it from the SQL editor in one go: the first line lifts the editor's
-- time limit, because the first save adds up the whole season.

set statement_timeout = '10min';

create table if not exists public.around_saved (
    season       integer not null,
    week         integer not null,          -- 0 = the whole season
    min_starts   integer not null default 150,
    fingerprint  text    not null,
    payload      json    not null,
    refreshed_at timestamptz not null default now(),
    primary key (season, week, min_starts)
);
alter table public.around_saved enable row level security;


create or replace function public.refresh_around(p_season integer default null,
                                                 p_min_starts integer default 150)
returns integer language plpgsql as $$
declare
    s       integer := coalesce(p_season, (select max(season) from public.team_weeks));
    r       record;
    changed integer := 0;
    fp_all  text := '';
begin
    if s is null then
        return 0;
    end if;
    for r in
        select week, count(*)::text || ':' || count(ppr_points)::text as fp
          from public.team_weeks
         where season = s
         group by week
         order by week
    loop
        fp_all := fp_all || r.week || '=' || r.fp || ';';
        if not exists (select 1 from public.around_saved
                        where season = s and week = r.week
                          and min_starts = p_min_starts and fingerprint = r.fp) then
            insert into public.around_saved (season, week, min_starts, fingerprint, payload, refreshed_at)
            values (s, r.week, p_min_starts, r.fp,
                    public.around_week(s, r.week, p_min_starts), now())
            on conflict (season, week, min_starts) do update
               set fingerprint = excluded.fingerprint, payload = excluded.payload,
                   refreshed_at = excluded.refreshed_at;
            changed := changed + 1;
        end if;
    end loop;
    if changed > 0 or not exists (select 1 from public.around_saved
                                   where season = s and week = 0
                                     and min_starts = p_min_starts
                                     and fingerprint = fp_all) then
        insert into public.around_saved (season, week, min_starts, fingerprint, payload, refreshed_at)
        values (s, 0, p_min_starts, fp_all, public.around_season(s), now())
        on conflict (season, week, min_starts) do update
           set fingerprint = excluded.fingerprint, payload = excluded.payload,
               refreshed_at = excluded.refreshed_at;
        changed := changed + 1;
    end if;
    return changed;
end;
$$;


-- Every 10 minutes, if pg_cron is on (it is: refresh-power-board uses it).
do $$
begin
    if exists (select 1 from pg_extension where extname = 'pg_cron') then
        perform cron.unschedule(jobid) from cron.job where jobname = 'refresh-around';
        perform cron.schedule('refresh-around', '*/10 * * * *',
                              'select public.refresh_around()');
    end if;
end $$;

select public.refresh_around();
