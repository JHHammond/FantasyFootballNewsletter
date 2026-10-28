-- 065: the trade market inside the API's time limit (28 Oct 2026). Re-runnable.
--
-- /market showed "Prices appear once enough trades have been collected" to
-- everybody, the morning the site passed 10,000 users. The market is built
-- in Python from five database calls, each of which adds up the whole
-- season across every league:
--
--   adp, dynasty_adp                    every draft pick in every league
--   trade_exchanges, dynasty_trade_exchanges   every trade of the season
--   trade_player_names                  every player in those trades
--
-- When any one of them runs past the API's ~8 s limit, the build gets
-- nothing and the page shows the empty state. The build is cached in the
-- web process, so a deploy (which empties the cache) is when it shows.
-- Same family as Rankings (033), Around (036) and Luck (064): whole-country
-- sums belong in a scheduled job with a saved result.
--
-- Each function's old body is kept under <name>_live. The name the website
-- calls now reads a saved copy (market_saved), refreshed every 15 minutes
-- by pg_cron with a 10-minute limit (042). If nothing is saved yet for a
-- season it falls back to the live body, so nothing is ever emptier than
-- before. trade_exchanges and dynasty_trade_exchanges save the whole season
-- and filter to the weeks asked for.
--
-- Run it all in the SQL editor. The last line fills the saved copies now,
-- which can take a minute or two; the set at the top gives it room.

set statement_timeout = '10min';

-- 1. keep the live bodies under _live (only the first time this runs)
do $$
begin
    if to_regprocedure('public.adp_live(integer, integer)') is null then
        alter function public.adp(integer, integer) rename to adp_live;
    end if;
    if to_regprocedure('public.dynasty_adp_live(integer, integer)') is null then
        alter function public.dynasty_adp(integer, integer) rename to dynasty_adp_live;
    end if;
    if to_regprocedure('public.trade_exchanges_live(integer, integer, integer)') is null then
        alter function public.trade_exchanges(integer, integer, integer) rename to trade_exchanges_live;
    end if;
    if to_regprocedure('public.dynasty_trade_exchanges_live(integer, integer, integer)') is null then
        alter function public.dynasty_trade_exchanges(integer, integer, integer) rename to dynasty_trade_exchanges_live;
    end if;
    if to_regprocedure('public.trade_player_names_live(integer)') is null then
        alter function public.trade_player_names(integer) rename to trade_player_names_live;
    end if;
end $$;

-- 2. the saved copies
create table if not exists public.market_saved (
    name         text    not null,     -- 'adp:20', 'trade_exchanges', ...
    season       integer not null,
    data         json    not null,
    refreshed_at timestamptz not null default now(),
    primary key (name, season)
);
alter table public.market_saved enable row level security;

create or replace function public._market_saved(p_name text, p_season integer)
returns json language sql stable as $$
    select data from public.market_saved where name = p_name and season = p_season;
$$;

-- 3. the names the website calls: the saved copy, or live if there isn't one
create or replace function public.adp(p_season integer, p_min_drafts integer default 20)
returns json language sql stable as $$
    select coalesce(public._market_saved('adp:' || p_min_drafts, p_season),
                    public.adp_live(p_season, p_min_drafts));
$$;

create or replace function public.dynasty_adp(p_season integer, p_min_drafts integer default 20)
returns json language sql stable as $$
    select coalesce(public._market_saved('dynasty_adp:' || p_min_drafts, p_season),
                    public.dynasty_adp_live(p_season, p_min_drafts));
$$;

create or replace function public.trade_player_names(p_season integer)
returns json language sql stable as $$
    select coalesce(public._market_saved('trade_player_names', p_season),
                    public.trade_player_names_live(p_season));
$$;

create or replace function public.trade_exchanges(
    p_season integer, p_from_week integer default 1, p_to_week integer default 18)
returns json language plpgsql stable as $$
declare
    saved json := public._market_saved('trade_exchanges', p_season);
begin
    if saved is null then
        return public.trade_exchanges_live(p_season, p_from_week, p_to_week);
    end if;
    return (select coalesce(json_agg(e), '[]'::json)
              from json_array_elements(saved) e
             where (e->>'w')::int between p_from_week and p_to_week);
end;
$$;

create or replace function public.dynasty_trade_exchanges(
    p_season integer, p_from_week integer default 1, p_to_week integer default 18)
returns json language plpgsql stable as $$
declare
    saved json := public._market_saved('dynasty_trade_exchanges', p_season);
begin
    if saved is null then
        return public.dynasty_trade_exchanges_live(p_season, p_from_week, p_to_week);
    end if;
    return (select coalesce(json_agg(e), '[]'::json)
              from json_array_elements(saved) e
             where (e->>'w')::int between p_from_week and p_to_week);
end;
$$;

-- 4. the refresh: each piece on its own, so one slow or broken piece never
-- stops the others. Returns what it saved and how long each took.
create or replace function public.refresh_market_saved(p_season integer default null)
returns json language plpgsql as $$
declare
    s integer := coalesce(p_season, (select max(season) from public.trades));
    out jsonb := '{}'::jsonb;
    t0 timestamptz;
    d json;
    item record;
begin
    if s is null then
        return '{}'::json;
    end if;
    for item in
        select * from (values
            ('adp:20',                  'select public.adp_live($1, 20)'),
            ('dynasty_adp:5',           'select public.dynasty_adp_live($1, 5)'),
            ('trade_exchanges',         'select public.trade_exchanges_live($1, 1, 18)'),
            ('dynasty_trade_exchanges', 'select public.dynasty_trade_exchanges_live($1, 1, 18)'),
            ('trade_player_names',      'select public.trade_player_names_live($1)')
        ) v(name, q)
    loop
        t0 := clock_timestamp();
        begin
            execute item.q into d using s;
            insert into public.market_saved (name, season, data, refreshed_at)
            values (item.name, s, coalesce(d, 'null'::json), now())
            on conflict (name, season) do update set data = excluded.data, refreshed_at = excluded.refreshed_at;
            out := out || jsonb_build_object(item.name,
                round(extract(epoch from clock_timestamp() - t0)::numeric, 1) || ' s');
        exception when others then
            out := out || jsonb_build_object(item.name, 'failed: ' || sqlerrm);
        end;
    end loop;
    return out::json;
end;
$$;

-- Service role only, like every other function (038).
revoke all on function public._market_saved(text, integer) from public, anon, authenticated;
revoke all on function public.refresh_market_saved(integer) from public, anon, authenticated;
revoke all on function public.adp(integer, integer) from public, anon, authenticated;
revoke all on function public.dynasty_adp(integer, integer) from public, anon, authenticated;
revoke all on function public.trade_player_names(integer) from public, anon, authenticated;
revoke all on function public.trade_exchanges(integer, integer, integer) from public, anon, authenticated;
revoke all on function public.dynasty_trade_exchanges(integer, integer, integer) from public, anon, authenticated;
grant execute on function public.adp(integer, integer) to service_role;
grant execute on function public.dynasty_adp(integer, integer) to service_role;
grant execute on function public.trade_player_names(integer) to service_role;
grant execute on function public.trade_exchanges(integer, integer, integer) to service_role;
grant execute on function public.dynasty_trade_exchanges(integer, integer, integer) to service_role;

-- 5. every 15 minutes, with room to finish
do $$
begin
    if exists (select 1 from pg_extension where extname = 'pg_cron') then
        perform cron.unschedule(jobid) from cron.job where jobname = 'refresh-market';
        perform cron.schedule('refresh-market', '5,20,35,50 * * * *',
            'set statement_timeout = ''10min''; select public.refresh_market_saved()');
    end if;
end $$;

-- 6. fill it now: each line says how long that piece took, or why it failed
select public.refresh_market_saved() as saved;
