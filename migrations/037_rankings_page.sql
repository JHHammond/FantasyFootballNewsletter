-- 037: the full national board, a page at a time, filterable by league size
-- (7 Oct 2026). Needs 033. Re-runnable.
--
-- The /rankings page shows a short top 5; "See the whole board" opens every
-- ranked team in a scrolling list, 50 rows per request, and can narrow it to
-- one league size ("only 12-team leagues"). Reads the saved power_board, so
-- each call takes milliseconds.
--
-- power_page returns:
--   total  teams in the filter
--   sizes  [{size, n}] every league size on the board, for the filter chips
--   me     your team's place within the filter (null if it isn't in it)
--   rows   the page, each with pos = place within the filter

create index if not exists power_board_by_size
    on public.power_board (season, team_count, rank);

create or replace function public.power_page(p_season integer,
                                             p_size   integer default null,
                                             p_offset integer default 0,
                                             p_limit  integer default 50,
                                             p_league uuid    default null,
                                             p_team   text    default null)
returns json language sql stable as $$
    with f as (
        select * from public.power_board
         where season = p_season
           and (p_size is null or team_count = p_size)
    ),
    me as (
        select rank, league_id, team_id from f
         where league_id = p_league and team_id = p_team
    ),
    pg as (
        select f.*, (greatest(p_offset, 0)
                     + row_number() over (order by rank, league_id, team_id collate "C"))::int as pos
          from f
         order by rank, league_id, team_id collate "C"
         offset greatest(p_offset, 0) limit least(greatest(p_limit, 1), 200)
    )
    select json_build_object(
        'teams', (select teams from public.power_board_meta where season = p_season),
        'total', (select count(*) from f),
        'sizes', (select coalesce(json_agg(json_build_object('size', team_count, 'n', n)
                                           order by team_count), '[]'::json)
                    from (select team_count, count(*) as n from public.power_board
                           where season = p_season and team_count is not null
                           group by team_count) s),
        'me', (select 1 + count(*) from f, me
                where (f.rank, f.league_id::text, f.team_id collate "C")
                    < (me.rank, me.league_id::text, me.team_id collate "C")
               having (select count(*) from me) > 0),
        'rows', (select coalesce(json_agg(row_to_json(pg) order by pg.pos), '[]'::json) from pg)
    );
$$;

notify pgrst, 'reload schema';
