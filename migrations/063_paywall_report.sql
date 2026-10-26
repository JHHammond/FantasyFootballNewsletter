-- 063: which locks people run into, and which ones make them pay (26 Oct 2026)
--
-- John: "tons of people are signing up. Very few are paying." The funnel
-- (060) counts sign-ups and papers; it can't say which paid feature anybody
-- wanted. These events can, and they ride in the same table:
--
--   gate_seen        a lock scrolled into view (once per page view)
--   gate_hit         somebody did something and ran into a limit
--   upgrade_clicked  somebody pressed a sign-up or upgrade button by a lock
--   checkout_started the server opened Stripe checkout from that lock
--   subscribed       Stripe says the plan is now paid (no lock: it can't know)
--   cancelled        Stripe says it isn't any more
--
-- props->>'where' names the lock (web/analytics.py GATES). No visitor is
-- identified, as with every other event. Re-runnable.

create or replace function public.gate_report(p_days integer default 28)
returns table (gate text, name text, events bigint)
language sql stable
set search_path = public
as $$
    select coalesce(nullif(e.props ->> 'where', ''), '(none)'), e.name, count(*)
      from public.events e
     where e.occurred_at >= now() - make_interval(days => greatest(p_days, 1))
       and e.name in ('gate_seen', 'gate_hit', 'upgrade_clicked',
                      'checkout_started', 'subscribed', 'cancelled')
     group by 1, 2
     order by 1, 2;
$$;

notify pgrst, 'reload schema';
