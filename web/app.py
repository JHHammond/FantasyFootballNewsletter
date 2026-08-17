"""
The Commissioner's Desk — web app.

No accounts, by design. Monetization is ads inside the paper, which means the
metric that matters is how many people open a paper, and a signup wall sits
directly in front of the one person who has to create it while doing nothing
for the ten who read it.

So instead of auth there are two URLs per league:

    /l/<admin_token>            manage the league. Secret. Bookmark it.
    /p/<public_slug>            read the papers. Share freely.

Run locally:

    uvicorn web.app:app --reload --port 8000
"""

from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# The generation pipeline lives in the project root, one level up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from providers import (  # noqa: E402
    ProviderError,
    available_providers,
    get_provider,
    load_week,
    week_to_legacy_games,
)
from storylines import get_weekly_storylines  # noqa: E402
from writer import generate_full_newspaper_content  # noqa: E402
from newspaper import (  # noqa: E402
    build_edition,
    build_power_rankings_from_matchups,
    render_html,
)

from . import db, slugs  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
CURRENT_SEASON = int(os.getenv("CURRENT_SEASON", "2025"))

app = FastAPI(title="The Commissioner's Desk", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------------------------------------------------------------------------
# Rate limiting
#
# Removing accounts removes the natural brake on abuse. Generation costs real
# Claude tokens, so an open endpoint that spends money on demand needs a guard.
# In-memory is fine for a single process; move to Redis when you run more.
# ---------------------------------------------------------------------------

_GENERATION_LOG: dict[str, list[float]] = defaultdict(list)
GENERATIONS_PER_HOUR = 10
LEAGUE_CREATES_PER_HOUR = 5


def _rate_limited(key: str, limit: int, window: int = 3600) -> bool:
    now = time.time()
    hits = [t for t in _GENERATION_LOG[key] if now - t < window]
    _GENERATION_LOG[key] = hits
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

    404 rather than 403 on a bad token: a wrong guess should be
    indistinguishable from a league that doesn't exist.
    """
    league = db.league_by_admin_token(token)
    if not league:
        raise HTTPException(status_code=404, detail="No league found for that link.")
    return league


def _paper_name(league: dict) -> str:
    return league.get("paper_name") or f"The {league['league_name']} Times"


def _render(request: Request, template: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, template, context)


# ---------------------------------------------------------------------------
# Landing / league creation
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _render(
        request, "index.html",
        providers=[p for p in available_providers() if p["implemented"]],
        season=CURRENT_SEASON,
    )


@app.post("/leagues")
def create_league(
    request: Request,
    provider: str = Form("sleeper"),
    league_id: str = Form(...),
    commissioner: str = Form(""),
    paper_name: str = Form(""),
    season: int = Form(CURRENT_SEASON),
):
    ip = _client_ip(request)
    if _rate_limited(f"create:{ip}", LEAGUE_CREATES_PER_HOUR):
        return _render(
            request, "index.html",
            providers=[p for p in available_providers() if p["implemented"]],
            season=season,
            error="You've created several leagues in the last hour. Try again later.",
        )

    league_id = league_id.strip()

    # Already set up? Don't create a duplicate — but don't hand the existing
    # league's admin token to whoever typed the ID either. Anyone can read a
    # Sleeper league ID off a URL; that can't be enough to seize control.
    existing = db.find_existing_league(provider, league_id, season)
    if existing:
        return _render(
            request, "index.html",
            providers=[p for p in available_providers() if p["implemented"]],
            season=season,
            error=(
                f"That league already has a paper: "
                f"<a href='/p/{existing['public_slug']}'>read it here</a>. "
                "If you set it up, use the manage link you bookmarked."
            ),
        )

    try:
        adapter = get_provider(provider)
        name = adapter.verify_league(league_id, season)
    except (ProviderError, ValueError) as exc:
        name = None
        detail = str(exc)
    else:
        detail = "Check the ID and try again."

    if not name:
        return _render(
            request, "index.html",
            providers=[p for p in available_providers() if p["implemented"]],
            season=season,
            error=f"Couldn't find that league. {detail}",
        )

    league = db.create_league(
        provider=provider,
        platform_league_id=league_id,
        league_name=name,
        paper_name=(paper_name.strip() or f"The {name} Times"),
        commissioner_name=commissioner.strip(),
        season=season,
        public_slug=slugs.public_slug(name),
        admin_token=slugs.admin_token(),
    )
    return RedirectResponse(f"/l/{league['admin_token']}?new=1", status_code=303)


# ---------------------------------------------------------------------------
# Manage a league
# ---------------------------------------------------------------------------

@app.get("/l/{token}", response_class=HTMLResponse)
def manage(request: Request, token: str, new: int = 0, generated: int = 0, error: str = ""):
    league = _require_league(token)
    return _render(
        request, "league.html",
        league=league,
        paper_name=_paper_name(league),
        jokes=db.get_jokes(league["id"]),
        papers=db.list_papers(league["id"]),
        is_new=bool(new),
        generated_week=generated or None,
        error=error,
    )


@app.post("/l/{token}/jokes")
def add_joke(token: str, joke: str = Form(...)):
    league = _require_league(token)
    text = joke.strip()
    if text:
        db.add_joke(league["id"], text[:500])
    return RedirectResponse(f"/l/{token}", status_code=303)


@app.post("/l/{token}/jokes/{joke_id}/remove")
def remove_joke(token: str, joke_id: str):
    league = _require_league(token)
    db.deactivate_joke(joke_id, league["id"])
    return RedirectResponse(f"/l/{token}", status_code=303)


@app.post("/l/{token}/settings")
def update_settings(token: str, paper_name: str = Form(""), commissioner: str = Form("")):
    league = _require_league(token)
    db.update_league(league["id"], {
        "paper_name": paper_name.strip() or None,
        "commissioner_name": commissioner.strip(),
    })
    return RedirectResponse(f"/l/{token}", status_code=303)


# ---------------------------------------------------------------------------
# Generation
#
# A plain `def` rather than `async def` on purpose: FastAPI runs sync handlers
# in a threadpool, so this ~30-second call doesn't block the event loop and
# freeze every other request.
# ---------------------------------------------------------------------------

@app.post("/l/{token}/generate")
def generate(request: Request, token: str, week: int = Form(...)):
    league = _require_league(token)
    ip = _client_ip(request)

    if _rate_limited(f"gen:{ip}", GENERATIONS_PER_HOUR):
        return RedirectResponse(
            f"/l/{token}?error=Too+many+generations+this+hour.+Try+again+later.",
            status_code=303,
        )

    season = league["season"]
    jokes = db.get_jokes(league["id"])
    jokes_text = "\n".join(f"- {j['joke']}" for j in jokes)
    paper_name = _paper_name(league)

    try:
        week_data = load_week(league["provider"], league["platform_league_id"], season, week)
    except ProviderError as exc:
        return RedirectResponse(f"/l/{token}?error={exc}", status_code=303)

    games = week_to_legacy_games(week_data)
    summary = get_weekly_storylines(games)

    ai_content = generate_full_newspaper_content(
        league_name=paper_name,
        week=week,
        games=games,
        summary=summary,
        commissioner_name=league.get("commissioner_name") or "",
        inside_jokes=jokes_text,
    )

    power_rankings = build_power_rankings_from_matchups(games)
    edition = build_edition(paper_name, week, summary, games, power_rankings, ai_content)
    edition["paper_name"] = paper_name
    html = render_html(edition)

    path, public_url = db.upload_paper(league["public_slug"], season, week, html)
    db.save_paper(league["id"], week, season, path, public_url, ai_content)

    return RedirectResponse(f"/l/{token}?generated={week}", status_code=303)


# ---------------------------------------------------------------------------
# Public reading — no token, no account, nothing to sign up for
# ---------------------------------------------------------------------------

@app.get("/p/{slug}", response_class=HTMLResponse)
def league_papers(request: Request, slug: str):
    league = db.league_by_public_slug(slug)
    if not league:
        raise HTTPException(status_code=404, detail="No paper found at that address.")
    return _render(
        request, "archive.html",
        league=league,
        paper_name=_paper_name(league),
        papers=db.list_papers(league["id"]),
    )


@app.get("/p/{slug}/{season}/week-{week}", response_class=HTMLResponse)
def read_paper(slug: str, season: int, week: int):
    """Serve the paper from our own domain rather than redirecting to the CDN.

    Keeping readers on this domain is what makes the pages worth anything to an
    ad network, and means the URL people share carries the product's name.
    """
    league = db.league_by_public_slug(slug)
    if not league:
        raise HTTPException(status_code=404, detail="No paper found at that address.")

    html = db.download_paper(db.storage_path(slug, season, week))
    if not html:
        raise HTTPException(status_code=404, detail="That week hasn't been published.")

    return HTMLResponse(
        content=html,
        # Editions never change once published, so let the browser and any CDN
        # in front of this hold onto them.
        headers={"Cache-Control": "public, max-age=3600, s-maxage=86400"},
    )


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
