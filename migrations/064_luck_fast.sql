-- 064: the Luck Index inside the API's time limit (27 Oct 2026). Re-runnable.
--
-- /luck/hands-3m3ke3ehdy showed "That didn't work." Timed in the SQL editor,
-- luck_league for that one league took 9.8 seconds; the API stops at about 8.
--
-- Why one league took as long as the whole country: luck_rows (031) serves
-- both, so every filter in it reads "(p_league is null or league_id =
-- p_league)". A SQL function is planned once for any value of p_league, and
-- that plan has to work for null too, so it can't use the (league_id, ...)
-- primary keys: it read every lineup in America and threw most of it away.
-- At week 3 that fit inside 8 seconds. At week 7 it does not.
--
-- Two changes:
--
--   * luck_rows runs the same query, word for word, through EXECUTE with the
--     season and league written in as constants. "null is null" is settled
--     before planning, so one league reads one league's rows through the
--     primary keys. The whole-country call (p_league null) is unchanged.
--
--   * The country (luck_national: percentiles, histogram, luckiest and
--     unluckiest) is worked out by a scheduled job and saved, like Rankings
--     (033) and Around (036). It had been timing out too, which the page hides
--     by leaving the national section off. The page reads the saved copy.
--     The job runs every 30 minutes with a 10-minute limit (see 042).
--
-- Run it all in the SQL editor. The last line fills the national copy now,
-- which takes a minute or so; the set at the top gives it room.

set statement_timeout = '10min';

create or replace function public.luck_rows(p_season integer, p_league uuid default null)
returns table (
    league_id uuid, team_id text, team_name text, manager text, provider text,
    team_count integer, scoring_type text,
    games bigint, w bigint, l bigint, t bigint,
    wins numeric, ap_real numeric, ap_adj numeric,
    schedule numeric, players numeric, total numeric,
    pf numeric, pa numeric, opp_luck numeric, player_dev numeric,
    close_w bigint, close_l bigint, gifts bigint, own_losses bigint,
    bench_avg numeric, has_players boolean, last_week integer
) language plpgsql stable as $fn$
begin
    -- %1$s is the season (an integer), %2$s the league as a quoted uuid or
    -- null::uuid. Both go in as constants so the planner can see them.
    return query execute format($q$
with g as (
    select t.league_id, t.team_id, t.week, t.team_name, t.manager, t.provider,
           t.team_count, t.scoring_type, t.points, t.opponent_points, t.result,
           t.optimal_points, t.bench_left,
           avg(t.points) over (partition by t.league_id, t.team_id) as team_avg
      from public.team_weeks t
     where t.season = %1$s and t.result in ('W', 'L', 'T') and t.points > 0
       and (%2$s is null or t.league_id = %2$s)
),
lavg as (select g.league_id, avg(g.points) as avg_pts from g group by g.league_id),
pavg as (
    select lp.league_id, lp.player_key, avg(lp.points) as avg_pts
      from public.lineup_players lp
     where lp.season = %1$s and lp.points > 0
       and (%2$s is null or lp.league_id = %2$s)
     group by lp.league_id, lp.player_key
),
dev as materialized (
    select lp.league_id, lp.team_id, lp.week, sum(lp.points - pa.avg_pts) as dev
      from public.lineup_players lp
      join pavg pa on pa.league_id = lp.league_id and pa.player_key = lp.player_key
     where lp.season = %1$s and lp.started
       and (%2$s is null or lp.league_id = %2$s)
     group by lp.league_id, lp.team_id, lp.week
),
gw as (
    select g.*, d.dev, g.points - coalesce(d.dev, 0) as adj,
           count(*) over (partition by g.league_id, g.week) as n
      from g
      left join dev d on d.league_id = g.league_id and d.team_id = g.team_id
                     and d.week = g.week
),
ap as materialized (
    select a.league_id, a.team_id, a.week,
           sum(case when b.points < a.points then 1.0
                    when b.points = a.points then 0.5 else 0 end) / (a.n - 1) as ap_real,
           sum(case when b.points < a.adj - 0.000001 then 1.0
                    when abs(b.points - a.adj) <= 0.000001 then 0.5 else 0 end) / (a.n - 1) as ap_adj
      from gw a
      join gw b on b.league_id = a.league_id and b.week = a.week and b.team_id <> a.team_id
     where a.n > 1
     group by a.league_id, a.team_id, a.week, a.n
),
opp as materialized (
    select distinct on (a.league_id, a.team_id, a.week)
           a.league_id, a.team_id, a.week, o.team_id as opp_id,
           o.optimal_points as opp_optimal, o.team_avg as opp_avg
      from g a
      join g o on o.league_id = a.league_id and o.week = a.week and o.team_id <> a.team_id
              and o.points = a.opponent_points and o.opponent_points = a.points
     order by a.league_id, a.team_id, a.week, o.team_id collate "C"
),
games as (
    select gw.*, ap.ap_real, ap.ap_adj, op.opp_avg,
           abs(gw.points - gw.opponent_points) < 0.05 * la.avg_pts as is_close,
           op.opp_optimal
      from gw
      join lavg la on la.league_id = gw.league_id
      left join ap on ap.league_id = gw.league_id and ap.team_id = gw.team_id and ap.week = gw.week
      left join opp op on op.league_id = gw.league_id and op.team_id = gw.team_id and op.week = gw.week
),
latest as (
    select distinct on (gm.league_id, gm.team_id) gm.league_id, gm.team_id, gm.team_name,
           gm.manager, gm.provider, gm.team_count, gm.scoring_type, gm.week
      from games gm
     order by gm.league_id, gm.team_id, gm.week desc
)
select (gm.league_id)::uuid,
       (gm.team_id)::text,
       (max(lt.team_name))::text,
       (max(lt.manager))::text,
       (max(lt.provider))::text,
       (max(lt.team_count))::integer,
       (max(lt.scoring_type))::text,
       (count(*))::bigint,
       (count(*) filter (where gm.result = 'W'))::bigint,
       (count(*) filter (where gm.result = 'L'))::bigint,
       (count(*) filter (where gm.result = 'T'))::bigint,
       (sum(case when gm.result = 'W' then 1 when gm.result = 'T' then 0.5 else 0 end)::numeric)::numeric,
       (round(sum(gm.ap_real)::numeric, 3))::numeric,
       (round(sum(gm.ap_adj)::numeric, 3))::numeric,
       (round((sum(case when gm.result = 'W' then 1 when gm.result = 'T' then 0.5 else 0 end)
              - sum(gm.ap_real))::numeric, 3))::numeric,
       (round((sum(gm.ap_real) - sum(gm.ap_adj))::numeric, 3))::numeric,
       (round((sum(case when gm.result = 'W' then 1 when gm.result = 'T' then 0.5 else 0 end)
              - sum(gm.ap_adj))::numeric, 3))::numeric,
       (round(avg(gm.points)::numeric, 2))::numeric,
       (round(avg(gm.opponent_points)::numeric, 2))::numeric,
       (round(avg(gm.opp_avg - gm.opponent_points)::numeric, 2))::numeric,
       (round(avg(gm.dev)::numeric, 2))::numeric,
       (count(*) filter (where gm.is_close and gm.result = 'W'))::bigint,
       (count(*) filter (where gm.is_close and gm.result = 'L'))::bigint,
       (count(*) filter (where gm.result = 'W' and gm.opp_optimal > gm.points))::bigint,
       (count(*) filter (where gm.result = 'L' and gm.optimal_points > gm.opponent_points))::bigint,
       (round(avg(gm.bench_left)::numeric, 2))::numeric,
       (bool_or(gm.dev is not null))::boolean,
       (max(gm.week))::integer
  from games gm
  join latest lt on lt.league_id = gm.league_id and lt.team_id = gm.team_id
 where gm.ap_real is not null
 group by gm.league_id, gm.team_id
$q$, p_season::integer,
     coalesce(quote_literal(p_league::text) || '::uuid', 'null::uuid'));
end;
$fn$;


-- The country, from scratch: what luck_national used to be. Only the
-- scheduled refresh calls it.
create or replace function public.luck_national_live(p_season integer)
returns json language sql stable as $$
    with r as materialized (select * from public.luck_rows(p_season, null)),
    c as (
        select r.*, json_build_object(
            'league_id', league_id, 'team_id', team_id, 'team_name', team_name,
            'manager', manager, 'provider', provider, 'team_count', team_count,
            'scoring_type', scoring_type, 'w', w, 'l', l, 't', t,
            'total', total, 'schedule', schedule, 'players', players) as card
          from r
    )
    select json_build_object(
        'teams', (select count(*) from r),
        'pct', (select percentile_cont(array(select g / 100.0 from generate_series(0, 100) g)::float8[])
                         within group (order by total) from r),
        'blessed', (select count(*) from r where total >= 1.5),
        'cursed', (select count(*) from r where total <= -1.5),
        'hist', (select coalesce(json_agg(json_build_object('lo', lo, 'n', n) order by lo), '[]'::json)
                   from (select greatest(-4.0, least(3.5, floor(total * 2) / 2.0)) as lo, count(*) as n
                           from r group by 1) h),
        'luckiest', (select coalesce(json_agg(card order by total desc, league_id, team_id collate "C"), '[]'::json)
                       from (select * from c order by total desc, league_id, team_id collate "C" limit 3) x),
        'unluckiest', (select coalesce(json_agg(card order by total, league_id, team_id collate "C"), '[]'::json)
                         from (select * from c order by total, league_id, team_id collate "C" limit 3) x));
$$;


create table if not exists public.luck_national_saved (
    season       integer primary key,
    data         json not null,
    refreshed_at timestamptz not null default now()
);
alter table public.luck_national_saved enable row level security;


create or replace function public.refresh_luck_national(p_season integer default null)
returns integer language plpgsql as $$
declare
    s integer := coalesce(p_season, (select max(season) from public.team_weeks));
    d json;
begin
    if s is null then
        return 0;
    end if;
    d := public.luck_national_live(s);
    insert into public.luck_national_saved (season, data, refreshed_at)
    values (s, d, now())
    on conflict (season) do update set data = excluded.data, refreshed_at = excluded.refreshed_at;
    return coalesce((d ->> 'teams')::integer, 0);
end;
$$;


-- What the page calls: the saved copy, in milliseconds. Empty until the
-- first refresh, which the page treats as "no national comparison yet".
create or replace function public.luck_national(p_season integer)
returns json language sql stable as $$
    select coalesce((select data from public.luck_national_saved where season = p_season),
                    '{}'::json);
$$;


-- Service role only, like every other function (038).
revoke all on function public.luck_national_live(integer) from public, anon, authenticated;
revoke all on function public.refresh_luck_national(integer) from public, anon, authenticated;
revoke all on function public.luck_rows(integer, uuid) from public, anon, authenticated;
revoke all on function public.luck_national(integer) from public, anon, authenticated;
grant execute on function public.luck_rows(integer, uuid) to service_role;
grant execute on function public.luck_national(integer) to service_role;


do $$
begin
    if exists (select 1 from pg_extension where extname = 'pg_cron') then
        perform cron.unschedule(jobid) from cron.job where jobname = 'refresh-luck-national';
        perform cron.schedule('refresh-luck-national', '15,45 * * * *',
            'set statement_timeout = ''10min''; select public.refresh_luck_national()');
    end if;
end $$;

select public.refresh_luck_national() as teams_measured;
