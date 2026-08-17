# Setup

The app is FastAPI + Jinja templates. There are no user accounts.

## 1. Install

```bash
pip install -r requirements.txt
```

## 2. Supabase

New project → SQL Editor → paste all of **`migrations/002_accountless.sql`** → Run.

That's the only migration you need. It creates the tables, the storage bucket,
and turns row-level security on with no policies — see below for why.

Then Project Settings → API, and put these in `.env`:

```
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_SERVICE_KEY=<the service_role key>
ANTHROPIC_API_KEY=<your key>
CURRENT_SEASON=2025
```

**This time you want the service_role key, not anon.** That's the opposite of
the usual advice, and it's a direct consequence of dropping accounts.

With logins, the browser held the anon key and row-level security decided what
each user could see. With no logins there's no `auth.uid()` for RLS to check,
so RLS can't protect anything. Instead: RLS is enabled with *zero* policies
(deny everything), the server holds the service_role key and is the only thing
that ever talks to the database, and authorization happens by unguessable token.

Which means: **the service key must never reach a browser.** It stays in the
server's environment. Don't put it in a template, a client-side script, or a
public repo.

## 3. Run

```bash
uvicorn web.app:app --reload --port 8000
```

Open `http://localhost:8000`.

## 4. Try it

1. Paste league ID `1252396303246176256`, season 2025, hit **Create my paper**.
2. You land on the manage page. **Bookmark it** — that URL is the only
   credential this league has, and there's no reset.
3. Add two or three inside jokes.
4. Generate week 1. Takes ~30 seconds and spends real Claude tokens.
5. Open the share link in a private window. It should render with no login.

## The two URLs

| URL | Who has it | What it does |
|---|---|---|
| `/l/<admin_token>` | just the commissioner | generate, edit jokes, settings |
| `/p/<public_slug>` | the whole league | read every edition |

The admin token is 192 bits of entropy, so guessing isn't a realistic attack.
Losing it is — there's no email on file to recover with. The manage page says
so in a yellow box on first visit.

## Ads

Classified blocks render at the foot of every paper (`ads.py`). They're house
ads today, styled as period-appropriate classifieds rather than banner units —
a classified reads as part of the paper, whereas people have spent twenty years
learning to ignore anything shaped like a leaderboard.

Each slot still carries `data-ad-slot` and `data-ad-size` with standard IAB
dimensions, so handing inventory to an ad network later means pointing a script
at `[data-ad-slot]`. No redesign needed.

To customize per league, pass an `ads=[Ad(...)]` list into `build_edition()`.

## Rate limits

Generation costs money and there's no login gating it, so `web/app.py` caps
each IP at 10 generations and 5 league creations per hour. In-memory, which is
fine for one process — move it to Redis if you run more than one.

## Deploying

Any host that runs a Python process: Railway, Render, Fly.io. The start command
is:

```
uvicorn web.app:app --host 0.0.0.0 --port $PORT
```

Set the same environment variables there. No build step, no Node, no static
asset pipeline.

## Tests

```bash
python -m pytest tests/ -v      # 65 tests, no network, no database
python verify_provider.py       # live Sleeper fetch
```

## What happened to the Streamlit app

`legacy_streamlit_app.py` is the old front end, kept for reference. It's not
wired to anything anymore — it still expects accounts and the pre-accountless
schema. Delete it whenever.
