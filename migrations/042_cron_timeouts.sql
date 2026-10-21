-- 042: give the two scheduled refreshes room to finish (13 Oct 2026). Re-runnable.
--
-- The Postgres log on 13 Oct showed "canceling statement due to statement
-- timeout" straight after each "cron job N starting" line, and
-- refresh_power_board() taking 70 seconds. Both jobs add up the whole
-- country, and both got slower as Tuesday's collection added Week 5.
--
-- A job that times out saves nothing, so it does all the same work again on
-- its next run: refresh-around every 10 minutes, refresh-power-board every
-- 30, all day. That is wasted CPU and disk writes, and the saved Around and
-- Rankings data stays stale.
--
-- The fix is to lift the time limit for the job's own session before it
-- runs. Setting statement_timeout inside the function does not help, because
-- the limit is fixed when the outer statement starts; it has to come first,
-- in the command pg_cron runs.

do $$
begin
    if exists (select 1 from pg_extension where extname = 'pg_cron') then
        perform cron.unschedule(jobid) from cron.job where jobname = 'refresh-around';
        perform cron.schedule('refresh-around', '*/10 * * * *',
            'set statement_timeout = ''10min''; select public.refresh_around()');
        perform cron.unschedule(jobid) from cron.job where jobname = 'refresh-power-board';
        perform cron.schedule('refresh-power-board', '*/30 * * * *',
            'set statement_timeout = ''10min''; select public.refresh_power_board()');
    end if;
end $$;

-- Check: both commands should now start with "set statement_timeout".
select jobid, jobname, schedule, command, active from cron.job order by jobid;
