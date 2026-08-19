"""
The Commissioner's Desk — web app.

No accounts, by design. Monetization is ads inside the paper, so the number
that matters is how many people open one, and a signup wall stands in front of
the single person who has to create it while doing nothing for the ten who
read it.

Email does the two jobs an account would have done, but asks only after the
product has proven itself:

    recovery      — commissioner gives us an address, gets their manage link
                    re-sent. Magic links replace passwords entirely.
    distribution  — readers subscribe from inside the paper, so reach stops
                    depending on one person pasting a link every Monday.

Two URLs per league:

    /l/<admin_token>            manage. Secret. Bookmark it.
    /p/<public_slug>            read. Share freely.

Run locally:

    DEMO_MODE=1 uvicorn web.app:app --reload --port 8000
"""

from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from providers import (  # noqa: E402
    ProviderError,
    available_providers,
    get_provider,
)

from . import emailer, slugs  # noqa: E402
from .generate import generate_and_store, paper_name_for  # noqa: E402

# DEMO_MODE=1 swaps Supabase for an in-memory store, so the whole app can be
# clicked through — including real generation from real league data — before
# any database exists. State evaporates on restart. Never enable in production.
DEMO_MODE = os.getenv("DEMO_MODE") == "1"

if DEMO_MODE:
    from . import demo_db as db  # noqa: E402
else:
    from . import db  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
CURRENT_SEASON = int(os.getenv("CURRENT_SEASON", "2025"))
MAGIC_LINK_TTL_MINUTES = 30

app = FastAPI(title="The Commissioner's Desk", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["demo_mode"] = DEMO_MODE


# ---------------------------------------------------------------------------
# Rate limiting
#
# Removing accounts removed the natural brake on abuse. Generation costs real
# Claude tokens and email costs deliverability reputation, so both need a cap.
# In-memory is fine for one process; move to Redis when you run more.
# ---------------------------------------------------------------------------

_HITS: dict[str, list[float]] = defaultdict(list)
GENERATIONS_PER_HOUR = 10
LEAGUE_CREATES_PER_HOUR = 5
SUBSCRIBES_PER_HOUR = 20
RECOVERIES_PER_HOUR = 5


def _rate_limited(key: str, limit: int, window: int = 3600) -> bool:
    now = time.time()
    hits = [t for t in _HITS[key] if now - t < window]
    _HITS[key] = hits
    if len(hits) >= limit:
        return True
    hits.append(now)
    return False


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_league(token: str) -> dict:
    """Load a league by admin token, or 404.

    404 rather than 403: a wrong guess should be indistinguishable from a
    league that doesn't exist.
    """
    league = db.league_by_admin_token(token)
    if not league:
        raise HTTPException(status_code=404, detail="No league found for that link.")
    return league


def _render(request: Request, template: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, template, context)


def _implemented_providers():
    return [p for p in available_providers() if p["implemented"]]


# ---------------------------------------------------------------------------
# Landing / league creation
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _render(request, "index.html", providers=_implemented_providers())


@app.post("/leagues")
def create_league(
    request: Request,
    provider: str = Form("sleeper"),
    league_id: str = Form(...),
    commissioner: str = Form(""),
    paper_name: str = Form(""),
):
    """Create a league from its ID alone.

    Deliberately does NOT ask for the season. Every platform stores the season
    on the league itself, so asking is just an invitation to type the wrong one
    and then get told the week has no data — which is a confusing way to learn
    you answered a question that shouldn't have been asked.
    """
    def fail(message: str):
        return _render(request, "index.html",
                       providers=_implemented_providers(), error=message)

    if _rate_limited(f"create:{_client_ip(request)}", LEAGUE_CREATES_PER_HOUR):
        return fail("You've made a few of these already. Give it an hour.")

    league_id = league_id.strip()

    try:
        adapter = get_provider(provider)
        info = adapter.describe_league(league_id)
    except (ProviderError, ValueError) as exc:
        return fail(f"Couldn't reach {provider.title()}. {exc}")

    if not info:
        return fail(
            "Couldn't find that league. It should be the long number from your "
            "league's web URL — for example "
            "<code>sleeper.com/leagues/<strong>1234567890123456789</strong>/team</code>."
        )

    season = info.season

    # Already set up? Point at the paper, but never hand over the admin token.
    # Anyone can read a league ID off a URL; that can't be proof of ownership.
    existing = db.find_existing_league(provider, league_id, season)
    if existing:
        return fail(
            f"This league already has a paper — "
            f"<a href='/p/{existing['public_slug']}'>read it here</a>. "
            f"If it's yours and you lost the link, "
            f"<a href='/recover'>get it back</a>."
        )

    league = db.create_league(
        provider=provider,
        platform_league_id=league_id,
        league_name=info.name,
        paper_name=(paper_name.strip() or f"The {info.name} Times"),
        commissioner_name=commissioner.strip(),
        season=season,
        public_slug=slugs.public_slug(info.name),
        admin_token=slugs.admin_token(),
    )
    return RedirectResponse(f"/l/{league['admin_token']}/setup", status_code=303)


# ---------------------------------------------------------------------------
# Manage
# ---------------------------------------------------------------------------

@app.get("/l/{token}", response_class=HTMLResponse)
def manage(
    request: Request, token: str,
    new: int = 0, generated: int = 0, error: str = "", notice: str = "",
):
    league = _require_league(token)

    # Only offer weeks the platform actually has results for. Letting someone
    # pick week 1 of a league that hasn't drafted is how you get a confusing
    # error instead of a paper.
    try:
        weeks = get_provider(league["provider"]).available_weeks(
            league["platform_league_id"], league["season"])
    except Exception:
        weeks = []

    return _render(
        request, "league.html",
        league=league,
        paper_name=paper_name_for(league),
        weeks=weeks,
        lore=db.get_lore(league["id"]),
        papers=db.list_papers(league["id"]),
        subscriber_count=db.subscriber_count(league["id"]),
        is_new=bool(new),
        generated_week=generated or None,
        error=error,
        notice=notice,
    )


@app.post("/l/{token}/lore")
def add_lore(token: str, entry: str = Form(...)):
    league = _require_league(token)
    text = entry.strip()
    if text:
        db.add_lore(league["id"], text[:500])
    return RedirectResponse(f"/l/{token}", status_code=303)


@app.post("/l/{token}/lore/{lore_id}/remove")
def remove_lore(token: str, lore_id: str):
    league = _require_league(token)
    db.deactivate_lore(lore_id, league["id"])
    return RedirectResponse(f"/l/{token}", status_code=303)


@app.post("/l/{token}/settings")
def update_settings(
    token: str,
    paper_name: str = Form(""),
    commissioner: str = Form(""),
    auto_send: str = Form(""),
    format: str = Form("redraft"),
    tone: str = Form("standard"),
    stakes: str = Form(""),
    punishment: str = Form(""),
):
    league = _require_league(token)
    db.update_league(league["id"], {
        "paper_name": paper_name.strip() or None,
        "commissioner_name": commissioner.strip(),
        "auto_send": auto_send == "on",
        "format": format if format in ("redraft", "keeper", "dynasty") else "redraft",
        "tone": tone if tone in ("friendly", "standard", "brutal") else "standard",
        "stakes": stakes.strip()[:300] or None,
        "punishment": punishment.strip()[:300] or None,
    })
    return RedirectResponse(f"/l/{token}?notice=Saved.", status_code=303)


@app.post("/l/{token}/email")
def save_owner_email(token: str, email: str = Form(...)):
    """Commissioner asks us to email them their manage link.

    Transactional, not marketing — it's a direct response to them clicking a
    button, and it's the recovery mechanism that makes going accountless safe.
    """
    league = _require_league(token)
    address = email.strip().lower()
    if not address or "@" not in address:
        return RedirectResponse(f"/l/{token}?error=That+doesn't+look+like+an+email.",
                                status_code=303)

    db.update_league(league["id"], {"owner_email": address})
    result = emailer.send_manage_link(address, paper_name_for(league), league["admin_token"])

    if not result.ok:
        return RedirectResponse(
            f"/l/{token}?error=Couldn't+send+that+email.+Try+again+in+a+bit.",
            status_code=303)
    return RedirectResponse(f"/l/{token}?notice=Sent.+Check+your+inbox.",
                            status_code=303)


# ---------------------------------------------------------------------------
# Setup questions
#
# Deliberately asked AFTER the league exists, not before. Getting someone in
# with one field is the whole point; this page is skippable and everything on
# it is editable later.
#
# Every question here is something the platform API cannot tell us. Scoring,
# superflex, team count and roster slots all come back from Sleeper — asking
# for those again would be a form for no reason.
# ---------------------------------------------------------------------------

@app.get("/l/{token}/setup", response_class=HTMLResponse)
def setup_form(request: Request, token: str):
    league = _require_league(token)
    return _render(request, "setup.html",
                   league=league, paper_name=paper_name_for(league))


@app.post("/l/{token}/setup")
def save_setup(
    token: str,
    format: str = Form("redraft"),
    tone: str = Form("standard"),
    founded_year: str = Form(""),
    stakes: str = Form(""),
    punishment: str = Form(""),
    lore: str = Form(""),
):
    league = _require_league(token)

    year = None
    if founded_year.strip().isdigit():
        candidate = int(founded_year.strip())
        if 1980 <= candidate <= 2100:
            year = candidate

    db.update_league(league["id"], {
        "format": format if format in ("redraft", "keeper", "dynasty") else "redraft",
        "tone": tone if tone in ("friendly", "standard", "brutal") else "standard",
        "founded_year": year,
        "stakes": stakes.strip()[:300] or None,
        "punishment": punishment.strip()[:300] or None,
        "setup_complete": True,
    })

    # One entry per line, so people can paste a list rather than submit six times.
    for line in lore.splitlines():
        entry = line.strip().lstrip("-•*").strip()
        if entry:
            db.add_lore(league["id"], entry[:500])

    return RedirectResponse(f"/l/{token}?new=1", status_code=303)


@app.post("/l/{token}/skip-setup")
def skip_setup(token: str):
    league = _require_league(token)
    db.update_league(league["id"], {"setup_complete": True})
    return RedirectResponse(f"/l/{token}?new=1", status_code=303)


# ---------------------------------------------------------------------------
# Generation
#
# A plain `def`, not `async def`: FastAPI runs sync handlers in a threadpool,
# so this ~30-second call doesn't block the event loop and freeze everything.
# ---------------------------------------------------------------------------

@app.post("/l/{token}/generate")
def generate(request: Request, token: str, week: int = Form(...)):
    league = _require_league(token)

    if _rate_limited(f"gen:{_client_ip(request)}", GENERATIONS_PER_HOUR):
        return RedirectResponse(
            f"/l/{token}?error=That's+a+lot+of+papers+this+hour.+Try+later.",
            status_code=303)

    try:
        generate_and_store(db, league, week)
    except ProviderError as exc:
        return RedirectResponse(f"/l/{token}?error={exc}", status_code=303)

    # Show them the paper. Waiting thirty seconds and being handed a URL to
    # click is a bad payoff for the one moment the product actually delivers.
    return RedirectResponse(f"/l/{token}/published/{week}", status_code=303)


@app.get("/l/{token}/published/{week}", response_class=HTMLResponse)
def published(request: Request, token: str, week: int):
    """The paper itself, framed, with a copy-link button and a way back."""
    league = _require_league(token)
    paper = db.get_paper(league["id"], league["season"], week)
    if not paper:
        return RedirectResponse(f"/l/{token}?error=That+week+isn't+published+yet.",
                                status_code=303)

    return _render(
        request, "published.html",
        league=league,
        paper_name=paper_name_for(league),
        week=week,
        paper_url=f"/p/{league['public_slug']}/{league['season']}/week-{week}",
        subscriber_count=db.subscriber_count(league["id"]),
    )


# ---------------------------------------------------------------------------
# Public reading — no token, no account, nothing to sign up for
# ---------------------------------------------------------------------------

@app.get("/p/{slug}", response_class=HTMLResponse)
def league_papers(request: Request, slug: str, subscribed: int = 0, error: str = ""):
    league = db.league_by_public_slug(slug)
    if not league:
        raise HTTPException(status_code=404, detail="No paper at that address.")
    return _render(request, "archive.html",
                   league=league, paper_name=paper_name_for(league),
                   papers=db.list_papers(league["id"]),
                   subscribed=bool(subscribed), error=error)


@app.get("/p/{slug}/{season}/week-{week}", response_class=HTMLResponse)
def read_paper(slug: str, season: int, week: int):
    """Serve from our own domain rather than redirecting to the CDN.

    Keeping readers here is what makes the pages worth anything to an ad
    network, and means the shared URL carries the product's name.
    """
    league = db.league_by_public_slug(slug)
    if not league:
        raise HTTPException(status_code=404, detail="No paper at that address.")

    html = db.download_paper(db.storage_path(slug, season, week))
    if not html:
        raise HTTPException(status_code=404, detail="That week isn't out yet.")

    return HTMLResponse(
        content=html,
        # Editions never change once published.
        headers={"Cache-Control": "public, max-age=3600, s-maxage=86400"},
    )


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

@app.post("/p/{slug}/subscribe")
def subscribe(request: Request, slug: str, email: str = Form(...)):
    league = db.league_by_public_slug(slug)
    if not league:
        raise HTTPException(status_code=404, detail="No paper at that address.")

    if _rate_limited(f"sub:{_client_ip(request)}", SUBSCRIBES_PER_HOUR):
        return RedirectResponse(f"/p/{slug}?error=Slow+down+a+second.", status_code=303)

    address = email.strip().lower()
    if "@" not in address:
        return RedirectResponse(f"/p/{slug}?error=That+doesn't+look+like+an+email.",
                                status_code=303)

    row = db.subscribe(league["id"], address, source="reader")
    if row:
        # Double opt-in. Nothing is ever sent to an address that hasn't clicked
        # through — protects deliverability and stops people signing up others.
        emailer.send_confirm_subscription(
            address, paper_name_for(league), row["confirm_token"])

    # Same response whether or not they were already subscribed, so this
    # endpoint can't be used to check who's on the list.
    return RedirectResponse(f"/p/{slug}?subscribed=1", status_code=303)


@app.get("/subscribe/confirm/{token}", response_class=HTMLResponse)
def confirm_subscription(request: Request, token: str):
    row = db.confirm_subscription(token)
    if not row:
        return _render(request, "message.html",
                       heading="That link's no good",
                       body="It may have already been used. Try subscribing again "
                            "from the bottom of any edition.")
    league = row.get("leagues") or {}
    slug = league.get("public_slug")
    return _render(request, "message.html",
                   heading="You're in",
                   body="Next week's edition lands in your inbox the moment it's out.",
                   link_url=f"/p/{slug}" if slug else "/",
                   link_label="Read the back catalogue")


@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(request: Request, token: str):
    """One click, no confirmation step, no login. Required by CAN-SPAM, and
    the alternative is people hitting 'report spam' instead."""
    row = db.unsubscribe(token)
    if not row:
        return _render(request, "message.html",
                       heading="Already done",
                       body="That address isn't on the list.")
    return _render(request, "message.html",
                   heading="You're off the list",
                   body="No more emails. You can still read every edition on the web.")


# ---------------------------------------------------------------------------
# Magic-link recovery — this is the entirety of "authentication"
# ---------------------------------------------------------------------------

@app.get("/recover", response_class=HTMLResponse)
def recover_form(request: Request, sent: int = 0):
    return _render(request, "recover.html", sent=bool(sent))


@app.post("/recover")
def recover(request: Request, email: str = Form(...)):
    if _rate_limited(f"rec:{_client_ip(request)}", RECOVERIES_PER_HOUR):
        return RedirectResponse("/recover?sent=1", status_code=303)

    address = email.strip().lower()
    leagues = db.leagues_for_email(address) if "@" in address else []

    if leagues:
        token = slugs.admin_token()
        expires = datetime.now(timezone.utc) + timedelta(minutes=MAGIC_LINK_TTL_MINUTES)
        db.create_magic_link(address, token, expires.isoformat())
        emailer.send_magic_link(address, token, len(leagues))

    # Always the same response, even when we sent nothing. Otherwise this page
    # tells a stranger whether an address has an account here.
    return RedirectResponse("/recover?sent=1", status_code=303)


@app.get("/recover/{token}", response_class=HTMLResponse)
def use_magic_link(request: Request, token: str):
    email = db.consume_magic_link(token)
    if not email:
        return _render(request, "message.html",
                       heading="Link expired",
                       body="Magic links work once and last 30 minutes.",
                       link_url="/recover", link_label="Send me a new one")

    leagues = db.leagues_for_email(email)
    if not leagues:
        return _render(request, "message.html",
                       heading="Nothing here",
                       body="No papers are registered to that address.")
    if len(leagues) == 1:
        return RedirectResponse(f"/l/{leagues[0]['admin_token']}", status_code=303)

    return _render(request, "picker.html", leagues=leagues)


# ---------------------------------------------------------------------------
# Weekly auto-send, triggered by cron
# ---------------------------------------------------------------------------

@app.post("/tasks/weekly")
def run_weekly(request: Request, week: int = Form(...)):
    """Protected by a shared secret so this can be hit by any external cron.

    There's a CLI equivalent in web/tasks.py if your host gives you real cron.
    """
    expected = os.getenv("TASK_KEY")
    if not expected or request.headers.get("x-task-key") != expected:
        raise HTTPException(status_code=404, detail="Not found.")

    from .tasks import send_weekly
    return send_weekly(db, week)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.exception_handler(404)
def not_found(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        request, "error.html",
        {"message": getattr(exc, "detail", "Not found.")},
        status_code=404,
    )
