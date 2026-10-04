-- National Rankings, saved (4 Oct 2026).
--
-- power_national() ranks every team in the country from scratch: about
-- 23,500 teams, several seconds of work. Called from the website, Supabase
-- cut it off at the API's time limit, the page got an empty board, and every
-- league read "This league isn't on the board yet" after a long wait.
--
-- Now the board is worked out once and saved in power_board, and the page
-- reads only the rows it shows (the top 25, one league, the teams around
-- one team) through power_view(), which takes milliseconds.
--
-- refresh_power_board() rebuilds it. It is scheduled with pg_cron at the
-- bottom of this file (every 30 minutes), because a scheduled job has no API
-- time limit. It can also be run by hand in the SQL editor:
--     select public.refresh_power_board();
--
-- Needs 031 and 032. Re-runnable.

create table if not exists public.power_board (
    season       integer not null,
    league_id    uuid    not null,
    team_id      text    not null,
    rank         bigint  not null,
    score        numeric,
    ppg          numeric,
    w            bigint, l bigint, t bigint,
    ap_pct       numeric,
    win_pct      numeric,
    ppg_pct      numeric,
    team_count   integer,
    scoring_type text,
    provider     text,
    games        bigint,
    last_week    integer,
    lw           integer,
    lw_pts       numeric,
    lw_beat      bigint,
    lw_pool      bigint,
    own          boolean,
    primary key (season, league_id, team_id)
);
create index if not exists power_board_by_rank on public.power_board (season, rank);
alter table public.power_board enable row level security;

create table if not exists public.power_board_meta (
    season       integer primary key,
    teams        integer not null,
    through      integer,
    dist         integer[] not null,
    refreshed_at timestamptz not null default now()
);
alter table public.power_board_meta enable row level security;


-- Rebuild the saved board for a season (the latest season with data when
-- none is given). Returns the number of teams ranked.
create or replace function public.refresh_power_board(p_season integer default null)
returns integer language plpgsql as $$
declare
    s integer := coalesce(p_season, (select max(season) from public.team_weeks));
    n integer;
begin
    if s is null then
        return 0;
    end if;
    create temp table _pb on commit drop as select * from public.power_rows(s);
    delete from public.power_board where season = s;
    insert into public.power_board
        (season, league_id, team_id, rank, score, ppg, w, l, t, ap_pct, win_pct,
         ppg_pct, team_count, scoring_type, provider, games, last_week, lw,
         lw_pts, lw_beat, lw_pool, own)
    select s, league_id, team_id, rank, score, ppg, w, l, t, ap_pct, win_pct,
           ppg_pct, team_count, scoring_type, provider, games, last_week, lw,
           lw_pts, lw_beat, lw_pool, own
      from _pb;
    get diagnostics n = row_count;
    insert into public.power_board_meta (season, teams, through, dist, refreshed_at)
    values (
        s, n,
        (select max(last_week) from _pb),
        (select array_agg(coalesce(c.n, 0) order by b.b)
           from generate_series(0, 19) b(b)
           left join (select least(floor(coalesce(score, 0) / 5), 19)::int as b, count(*)::int as n
                        from _pb group by 1) c on c.b = b.b),
        now())
    on conflict (season) do update
       set teams = excluded.teams, through = excluded.through,
           dist = excluded.dist, refreshed_at = excluded.refreshed_at;
    return n;
end;
$$;


-- What the /rankings page needs, from the saved board: the size of the
-- board, the curve, the national top 25, every team in one league, and the
-- stretch of the board around one team (by rank; the page picks the four on
-- either side).
create or replace function public.power_view(p_season integer, p_league uuid,
                                              p_team text default null)
returns json language sql stable as $$
    with me as (
        select rank from public.power_board
         where season = p_season and league_id = p_league and team_id = p_team
    )
    select json_build_object(
        'teams', (select teams from public.power_board_meta where season = p_season),
        'through', (select through from public.power_board_meta where season = p_season),
        'dist', (select dist from public.power_board_meta where season = p_season),
        'refreshed_at', (select refreshed_at from public.power_board_meta where season = p_season),
        'top', (select coalesce(json_agg(row_to_json(b) order by b.rank, b.league_id, b.team_id collate "C"), '[]'::json)
                  from (select * from public.power_board where season = p_season
                         order by rank, league_id, team_id collate "C" limit 25) b),
        'league', (select coalesce(json_agg(row_to_json(b) order by b.rank, b.team_id collate "C"), '[]'::json)
                     from public.power_board b
                    where b.season = p_season and b.league_id = p_league),
        'near', (select coalesce(json_agg(row_to_json(b) order by b.rank, b.league_id, b.team_id collate "C"), '[]'::json)
                   from public.power_board b, me
                  where b.season = p_season and b.rank between me.rank - 12 and me.rank + 12)
    );
$$;


-- Keep it fresh: every 30 minutes, if pg_cron is available. Turn pg_cron on
-- under Database > Extensions first; without it this block does nothing and
-- the board only changes when refresh_power_board() is run by hand.
do $$
begin
    if exists (select 1 from pg_extension where extname = 'pg_cron') then
        perform cron.unschedule(jobid) from cron.job where jobname = 'refresh-power-board';
        perform cron.schedule('refresh-power-board', '*/30 * * * *',
                              'select public.refresh_power_board()');
    end if;
end $$;

select public.refresh_power_board();
