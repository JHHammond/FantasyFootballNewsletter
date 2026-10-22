-- 049: Did the backfill actually get anything? (22 Oct 2026)
--
-- The market only spans the days we hold trades for, so the obvious move
-- is to collect the earlier weeks of the season. That works: the week
-- window is keyed to the week of the SEASON rather than to "now", so any
-- platform can be asked about week 2 in October, and trade_scans makes
-- re-running free for weeks already done.
--
-- What it cannot do is tell you whether a platform ANSWERED. And there is
-- a specific trap: a league scanned for a week is written to trade_scans
-- whether it yielded ten trades or none, so
--
--     "ESPN has pruned its transaction feed and gave us nothing"
--
-- is recorded identically to
--
--     "nobody in those leagues traded that week"
--
-- and neither will ever be retried. Sleeper's endpoint is genuinely
-- per-week and will serve week 1 in December; Yahoo hands back the whole
-- season at once; ESPN's mTransactions2 leans recent and may not reach
-- back at all. So the three platforms are expected to behave differently
-- and the only way to know is to look.
--
-- Hence this: scanned leagues and stored trades per PLATFORM per WEEK,
-- side by side. A row of zeroes across every week for one provider while
-- its neighbours fill up is the signature of a feed that does not go back,
-- and the fix for that is to delete those trade_scans rows rather than to
-- run the collection again.
--
-- PRIVACY: counts only. No league, team or manager leaves this function.

create or replace function public.trade_backfill(p_season integer)
returns json language sql stable as $$
with scans as (
    select l.provider, s.week,
           count(*)                   as leagues,
           coalesce(sum(s.trades), 0) as reported
      from public.trade_scans s
      join public.leagues l on l.id = s.league_id
     where s.season = p_season
     group by l.provider, s.week
),
stored as (
    select t.provider, t.week,
           count(*)                                               as trades,
           count(*) filter (where t.picks = '[]'::jsonb
                              and t.faab = '[]'::jsonb)           as usable,
           count(*) filter (where t.traded_at is not null)        as dated
      from public.trades t
     where t.season = p_season
     group by t.provider, t.week
),
grid as (
    select coalesce(sc.provider, st.provider) as provider,
           coalesce(sc.week, st.week)         as week,
           coalesce(sc.leagues, 0)            as leagues,
           coalesce(st.trades, 0)             as trades,
           coalesce(st.usable, 0)             as usable,
           coalesce(st.dated, 0)              as dated
      from scans sc
      full outer join stored st
        on st.provider = sc.provider and st.week = sc.week
)
select json_build_object(
    'season', p_season,
    'rows', (select coalesce(json_agg(json_build_object(
                 'provider', provider, 'week', week, 'leagues', leagues,
                 'trades', trades, 'usable', usable, 'dated', dated)
                 order by provider collate "C", week), '[]'::json) from grid),
    'providers', (select coalesce(json_agg(json_build_object(
                      'provider', provider,
                      'weeks', weeks, 'leagues', leagues, 'trades', trades,
                      'usable', usable, 'dated', dated,
                      -- The number worth reading: weeks this platform was
                      -- asked about and gave back nothing at all.
                      'empty_weeks', empty_weeks)
                      order by provider collate "C"), '[]'::json)
                    from (select provider,
                                 count(*)                                as weeks,
                                 sum(leagues)                            as leagues,
                                 sum(trades)                             as trades,
                                 sum(usable)                             as usable,
                                 sum(dated)                              as dated,
                                 count(*) filter (where trades = 0
                                                    and leagues > 0)     as empty_weeks
                            from grid group by provider) p),
    -- The span the chart can actually draw, which is the whole point.
    'first_day', (select min((traded_at at time zone 'UTC')::date)
                    from public.trades where season = p_season),
    'last_day',  (select max((traded_at at time zone 'UTC')::date)
                    from public.trades where season = p_season),
    'undated', (select count(*) from public.trades
                 where season = p_season and traded_at is null)
);
$$;
