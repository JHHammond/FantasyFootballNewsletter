-- ============================================================================
-- Which migrations have actually been applied?
--
-- Paste the whole file into the Supabase SQL editor and run it. One result
-- set, one row per migration, and a `status` column that says APPLIED or
-- MISSING. Read only — it creates nothing and changes nothing.
--
-- WHY THIS EXISTS
--
-- The symptom of an unapplied migration is a 500 halfway through something
-- that was working a second earlier:
--
--   column newspapers.ai_cache_original does not exist
--   Could not find the 'theme' column of 'leagues' in the schema cache
--
-- Each one only shows up when a user reaches the code path that touches that
-- column — so they arrive one at a time, days apart, each looking like a new
-- bug. This answers the whole question in one go.
--
-- AFTER RUNNING ANY MIGRATION, ALWAYS RUN:
--
--   notify pgrst, 'reload schema';
--
-- PostgREST caches the table shape. Without that line a freshly added column
-- still reports as missing, which looks exactly like the migration failing.
-- ============================================================================

with expected(ordering, migration, kind, obj, col, note) as (
    values
    (2,  '002_accountless', 'column',   'leagues',    'admin_token',
         'the accountless model: admin_token + public_slug'),
    (3,  '003_email',       'table',    'magic_links', null,
         'emailing a lost manage link'),
    (4,  '004_lore',        'table',    'lore',        null,
         'the running jokes — the whole point of the paper'),
    (5,  '005_setup',       'column',   'leagues',     'stakes',
         'the setup wizard fields'),
    (6,  '006_edits',       'column',   'newspapers',  'ai_cache_original',
         'editing + revert. Generation SUCCEEDS then fails on save without it'),
    (7,  '007_themes',      'column',   'leagues',     'theme',
         'per-league look. Setup 500s without it'),
    (8,  '008_views',       'column',   'newspapers',  'view_count',
         'read counts'),
    (9,  '009_rate_limits', 'function', 'claim_rate_slot', null,
         'the durable spend ceiling. Fails CLOSED — nothing generates without it'),
    (10, '010_accounts',    'table',    'users',       null,
         'email/password accounts'),
    (11, '011_generation_count', 'column', 'newspapers', 'generation_count',
         'the weekly regeneration allowance. Without it the count never moves'),
    (12, '012_publisher_ads', 'table', 'publisher_ads', null,
         'the classifieds page. The publisher page 500s and papers print without it'),
    (13, '013_managers', 'table', 'managers', null,
         'per-person lore and real names. Papers keep printing handles without it'),
    (14, '014_plans', 'column', 'users', 'plan',
         'the paywall. Everyone is on the free tier and no payment can be recorded')
)
select
    e.migration,
    case
        when e.kind = 'column' then
            case when exists (
                select 1 from information_schema.columns c
                 where c.table_schema = 'public'
                   and c.table_name   = e.obj
                   and c.column_name  = e.col
            ) then 'APPLIED' else 'MISSING' end
        when e.kind = 'table' then
            case when exists (
                select 1 from information_schema.tables t
                 where t.table_schema = 'public'
                   and t.table_name   = e.obj
            ) then 'APPLIED' else 'MISSING' end
        when e.kind = 'function' then
            case when exists (
                select 1 from pg_proc p
                 join pg_namespace n on n.oid = p.pronamespace
                 where n.nspname = 'public'
                   and p.proname = e.obj
            ) then 'APPLIED' else 'MISSING' end
    end                                     as status,
    e.kind || ' ' || e.obj
        || coalesce('.' || e.col, '')       as looked_for,
    e.note                                  as what_breaks_without_it
  from expected e
 order by e.ordering;
