# Setting up a fresh Supabase project

Order matters — do these top to bottom.

## 1. Create the project

New project in Supabase. Any region, any name. Wait for it to finish
provisioning before continuing.

## 2. Run the schema

SQL Editor → New query → paste all of `migrations/000_bootstrap.sql` → Run.

That one file creates:

- the `leagues`, `inside_jokes`, and `newspapers` tables
- row-level security policies on all three (without these, the anon key can
  read every league in the database)
- the `newspapers` storage bucket, public-read, with write access locked to
  each user's own folder

You do **not** need `001_add_provider.sql`. That's only for upgrading an
existing pre-provider database — the bootstrap already includes those columns.

## 3. Environment variables

Project Settings → API. Copy into `.env`:

```
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_KEY=<the anon public key>
ANTHROPIC_API_KEY=<your existing key>
APP_URL=http://localhost:8501
```

Use the **anon public** key, not the service role key. The service role key
bypasses row-level security entirely — if it ever reaches a browser, every
league in your database is readable by anyone.

`APP_URL` is new. OAuth redirect used to be hardcoded to localhost, which broke
on deploy. Set it to your real URL in production.

## 4. Auth configuration

- **Authentication → URL Configuration**: add `http://localhost:8501` to the
  redirect allowlist, plus your deployed URL when you have one. Sign-in fails
  silently if the redirect isn't allowlisted.
- **Authentication → Providers**: enable Google if you want the OAuth button.
  Email/password works with no extra setup.

## 5. Verify

```bash
python verify_provider.py       # live Sleeper fetch, no DB involved
python -m pytest tests/ -v      # 43 tests, offline
streamlit run app.py            # sign up, add a league, generate a week
```

Generating a week should end with a downloadable paper *and* a new object in
Storage → newspapers. If the upload errors with "bucket not found", step 2
didn't finish.

## What changed about storage

Rendered HTML is ~60KB an edition. It used to go in a Postgres column, which is
what exhausted the last project's 500MB database. It now goes in the
`newspapers` storage bucket — a separate 1GB allowance on the free tier — and
the database keeps only the path, a public URL, and `ai_cache` (Claude's
output, a few KB).

The bucket is public-read on purpose: the Share button in the archive hands
someone a link that opens without an account. Anyone with the URL can read that
edition, which is the intent, but it does mean the link is the only thing
protecting it. Path names include UUIDs, so they aren't guessable.

## Rough capacity

At ~20KB of `ai_cache` per edition, 500MB of database holds on the order of
20,000 editions — about 1,400 leagues for a full 14-week season. Storage fills
first: 1GB at 60KB an edition is roughly 16,000 papers. Both are far enough out
that the next thing to worry about is the Claude API bill, not Supabase.
