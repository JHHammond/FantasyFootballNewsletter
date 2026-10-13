-- How many leagues keep coming back, and how many free trials end this week.
-- Read-only. Paste ONE query at a time into the Supabase SQL editor (it only
-- shows the last query's results). Change 2026 for a new season.
--
-- "Real league" = provider + platform league id, the same key the free trial
-- uses, so a league connected twice is counted once.


-- 1) WEEK BY WEEK: who made a paper, who was new, who came back.
--
-- Bucketed by WHEN the paper was made, not which week it's about, so a paid
-- league catching up on old weeks doesn't look like a return visit. A cycle
-- runs Friday to Thursday: week 5's games end Monday and its papers get made
-- Sunday to Wednesday, all inside one cycle.

with made as (
    select distinct
           l.provider, l.platform_league_id,
           floor(((n.generated_at at time zone 'America/New_York')::date
                  - date '2026-09-11') / 7.0)::int + 1 as cycle
    from public.newspapers n
    join public.leagues l on l.id = n.league_id
    where n.season = 2026
),
firsts as (
    select provider, platform_league_id, min(cycle) as first_cycle
    from made group by 1, 2
)
select m.cycle                                          as nfl_week,
       count(*)                                         as leagues_active,
       count(*) filter (where m.cycle = f.first_cycle)  as brand_new,
       count(*) filter (where m.cycle > f.first_cycle)  as came_back,
       count(*) filter (where exists (
           select 1 from made p
           where p.provider = m.provider
             and p.platform_league_id = m.platform_league_id
             and p.cycle = m.cycle - 1))                as also_active_week_before,
       round(100.0 * count(*) filter (where exists (
           select 1 from made p
           where p.provider = m.provider
             and p.platform_league_id = m.platform_league_id
             and p.cycle = m.cycle - 1))
           / nullif((select count(*) from made p where p.cycle = m.cycle - 1), 0))
                                                        as pct_of_last_week_retained
from made m
join firsts f using (provider, platform_league_id)
group by m.cycle
order by m.cycle;


-- 2) THE TRIAL: where every free league stands, and this week's last papers.
--
-- Set this_week to the NFL week whose papers are going out (week 5 on Tue
-- 13 Oct 2026). A free league on 2 of 3 papers whose 2nd was LAST week makes
-- its final free paper this week; the estimate applies the rate at which
-- past leagues on their 2nd paper came back the following week.

with params as (select 2026 as season, 5 as this_week),
paid as (
    select distinct l.provider, l.platform_league_id
    from public.leagues l
    join public.users u on u.id = l.user_id, params
    where l.season = params.season
      and (lower(u.plan) = 'staff'
           or (lower(u.plan) = 'paid'
               and (u.plan_status is null
                    or lower(u.plan_status) in ('active', 'trialing'))))
),
trial as (
    select t.provider, t.platform_league_id,
           count(*) filter (where t.week < params.this_week) as used_before,
           bool_or(t.week = params.this_week)                 as made_this_week,
           max(t.week) filter (where t.week < params.this_week) as last_week
    from public.trial_papers t, params
    where t.season = params.season
      and not exists (select 1 from paid p
                      where p.provider = t.provider
                        and p.platform_league_id = t.platform_league_id)
    group by 1, 2
),
second as (
    select provider, platform_league_id, week as second_week
    from (select t.*, row_number() over (
                 partition by provider, platform_league_id order by week) as rn
          from public.trial_papers t, params where t.season = params.season) x
    where rn = 2
),
rate as (
    select count(*) as sample,
           count(*) filter (where exists (
               select 1 from public.trial_papers t
               where t.season = params.season
                 and t.provider = s.provider
                 and t.platform_league_id = s.platform_league_id
                 and t.week = s.second_week + 1))::numeric
           / nullif(count(*), 0) as came_back
    from second s, params
    where s.second_week + 1 < params.this_week     -- only weeks already over
),
due as (   -- 2 papers used, the 2nd one last week: this week's is their last
    select t.* from trial t, params
    where t.used_before = 2 and t.last_week = params.this_week - 1
)
select
    (select count(*) from trial where used_before = 1)           as on_paper_1,
    (select count(*) from trial where used_before = 2)           as on_paper_2,
    (select count(*) from trial where used_before >= 3)          as trial_finished,
    (select count(*) from paid)                                  as paid_leagues,
    (select count(*) from due)                                   as last_paper_due_this_week,
    (select count(*) from due where made_this_week)              as already_made_it,
    round(100 * (select came_back from rate))                    as pct_who_came_back_after_paper_2,
    (select sample from rate)                                    as based_on_leagues,
    round((select count(*) from due)
          * coalesce((select came_back from rate), 0))           as expected_last_papers;
