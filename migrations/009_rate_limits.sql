-- 009: durable rate limits and the daily spend ceiling.
--
-- These lived in a Python dict in one process. That was honest on a single
-- Render instance and stated as such in a comment — "N instances means N times
-- every limit below" — but it made the ceiling a property of the deployment
-- rather than of the product. Scale to two instances and the daily cap doubles;
-- restart the process and it resets to zero. The one limit that exists to stop
-- an Anthropic invoice should not be the one that quietly forgets.
--
-- So the counter moves to the database, which is the only thing in this system
-- that every instance already agrees on.
--
-- Idempotent, like the rest of the chain.

-- --------------------------------------------------------------------------
-- 1. The events.
--
-- One row per allowed action. Counting rows in a window is a sliding window,
-- which is what the old dict did too — a fixed-window counter would let
-- someone burn a full allowance either side of a boundary.
-- --------------------------------------------------------------------------

create table if not exists public.rate_events (
    id          bigserial primary key,
    bucket      text        not null,
    occurred_at timestamptz not null default now()
);

-- The only query shape there is: rows for one bucket, newer than a cutoff.
create index if not exists rate_events_bucket_time_idx
    on public.rate_events (bucket, occurred_at desc);

alter table public.rate_events enable row level security;
-- No policies, deliberately. Deny-all to every browser-facing role; the server
-- holds the service key and bypasses RLS. Same posture as every other table.


-- --------------------------------------------------------------------------
-- 2. Claiming a slot.
--
-- Check-then-insert from the application is a race: two requests both read a
-- count under the limit and both insert, and the ceiling leaks by however many
-- callers arrived together. That is exactly the situation a spend ceiling
-- exists for, so the whole thing happens in one statement behind a per-bucket
-- advisory lock. Concurrent callers for the SAME bucket serialize; callers for
-- different buckets never touch each other.
--
-- Returns true if the caller may proceed.
-- --------------------------------------------------------------------------

create or replace function claim_rate_slot(
    p_bucket text,
    p_limit  integer,
    p_window interval
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
    used integer;
begin
    if p_limit <= 0 then
        return false;
    end if;

    -- Held until the transaction ends. hashtext collisions would only make two
    -- unrelated buckets wait for each other briefly, never miscount.
    perform pg_advisory_xact_lock(hashtext(p_bucket));

    -- Drop this bucket's expired rows while we hold the lock, so a busy bucket
    -- pays for its own cleanup and the table doesn't grow without bound.
    delete from public.rate_events
     where bucket = p_bucket
       and occurred_at < now() - p_window;

    select count(*) into used
      from public.rate_events
     where bucket = p_bucket
       and occurred_at > now() - p_window;

    if used >= p_limit then
        return false;
    end if;

    insert into public.rate_events (bucket) values (p_bucket);
    return true;
end;
$$;

grant execute on function claim_rate_slot(text, integer, interval) to service_role;


-- --------------------------------------------------------------------------
-- 3. Sweeping the buckets nobody visits again.
--
-- claim_rate_slot only cleans the bucket it was called for. An IP that showed
-- up once and never returned leaves its row behind forever, so the weekly job
-- clears anything older than the longest window in use.
-- --------------------------------------------------------------------------

create or replace function sweep_rate_events(p_older_than interval default interval '2 days')
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    removed integer;
begin
    delete from public.rate_events where occurred_at < now() - p_older_than;
    get diagnostics removed = row_count;
    return removed;
end;
$$;

grant execute on function sweep_rate_events(interval) to service_role;
