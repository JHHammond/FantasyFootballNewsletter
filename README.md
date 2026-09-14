# The Commissioner's Desk

A weekly newspaper for your fantasy football league, written by AI from your
league's actual results.

Paste a league ID. Every week you get a real paper — recaps, power rankings,
awards, a fraud watch — in the voice of a sports columnist with strong opinions
about people's lineup decisions. Share the link into the group chat and let it
start fights.

No accounts, no passwords, free to use. Monetization is advertising inside the
paper, so the number that matters is how many people open one — which is why
there is no signup wall in front of the person who has to create it.

## How it works

    league ID  ->  provider  ->  normalized week  ->  Claude  ->  rendered paper
                   (Sleeper)      (models.py)                     (newspaper.py)

Two URLs per league:

    /l/<admin_token>    manage. Secret — treat it like a password.
    /p/<public_slug>    read. Share freely.

The prose lives in `ai_cache` as structured JSON, so editing a paper re-renders
from that JSON with no Claude call. That's why the inline editor is instant and
free, and why a lost storage bucket is survivable.

## Running it locally

    pip install -r requirements.txt
    DEMO_MODE=1 uvicorn web.app:app --reload --port 8000

Demo mode swaps Supabase for an in-memory store, so the whole product works —
including real generation from real league data — with no database. State
disappears on restart. The test suite runs against the same in-memory store,
which is what keeps demo mode honest.

    python -m pytest tests/ -q

## Deploying

`render.yaml` is a Render blueprint: a web service plus a Tuesday cron that
generates and emails the week for every league with auto-send on. See
[DEPLOY.md](DEPLOY.md) for the full walkthrough and [SETUP.md](SETUP.md) for the
database.

Run everything in `migrations/` in order. They are idempotent — safe to re-run,
and safe against a database that already has them.

## Adding a fantasy platform

Write one subclass of `FantasyProvider` (see `providers/base.py`) and register
it. Nothing else should need to change — if it does, something platform-specific
has leaked into the normalized model and belongs back in the adapter.
`providers/espn.py` is a documented stub showing what an ESPN adapter needs.

## Layout

    providers/     platform adapters + the normalized league model
    web/           FastAPI app, routes, database, email
    newspaper.py   the renderer — turns a week + prose into HTML
    writer.py      the prompts
    themes.py      tabloid / broadsheet / gameday
    storylines.py  what's interesting about a week, before any AI sees it
    migrations/    run in order; all idempotent

## Not affiliated

Not affiliated with or endorsed by the NFL, Sleeper, ESPN, Yahoo, or any fantasy
platform. Player names and statistics are factual information from public
sources.
