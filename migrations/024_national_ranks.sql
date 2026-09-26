-- 024: How you stack up (26 Sep 2026)
--
-- One call ranks a league against the country for a finished week: how many
-- teams scored more than each of this league's teams, and how many leagues
-- averaged more than this one. Computed in the database so a paper never
-- pages 14,000 rows through the API. Needs 021 (team_weeks). Re-runnable.

-- The league being ranked is LEFT OUT of the pool (p_league), so it is
-- ranked against everyone else whether or not its own week is collected yet;
-- the caller adds its own teams back in.
drop function if exists public.national_ranks(integer, integer, numeric[], numeric);

create or replace function public.national_ranks(
    p_season integer, p_week integer, p_points numeric[], p_avg numeric,
    p_league uuid)
returns json language sql stable as $$
    with pool as (
        select league_id, points from public.team_weeks
         where season = p_season and week = p_week
           and result <> 'BYE' and points > 0
           and league_id <> p_league
    ),
    lg as (select league_id, avg(points) as a from pool group by league_id)
    select json_build_object(
        'teams',   (select count(*) from pool),
        'leagues', (select count(*) from lg),
        'above',   coalesce((
            select json_agg((select count(*) from pool where pool.points > u.x)
                            order by u.ord)
              from unnest(p_points) with ordinality as u(x, ord)), '[]'::json),
        'leagues_above', (select count(*) from lg where lg.a > p_avg)
    );
$$;
