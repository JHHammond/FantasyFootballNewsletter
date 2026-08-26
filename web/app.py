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
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from providers import (  # noqa: E402
    ProviderError,
    available_providers,
    get_provider,
)

import themes  # noqa: E402

from . import emailer, slugs  # noqa: E402
from .sanitize import clean_html, clean_image_url, clean_text  # noqa: E402
from .generate import (  # noqa: E402
    generate_and_store,
    paper_name_for,
    public_base_url,
    render_and_store,
    render_editable,
)

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

# Bundled meme images. Papers used to reference these as "../memes/x.jpg",
# which only resolved when the HTML sat next to the folder on disk — every one
# of them was a broken image once papers moved to URLs.
_MEMES_DIR = BASE_DIR.parent / "memes"
if _MEMES_DIR.is_dir():
    app.mount("/memes", StaticFiles(directory=_MEMES_DIR), name="memes")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["demo_mode"] = DEMO_MODE
# Templates build share links from this rather than request.base_url,
# which behind a proxy reports http:// and the internal hostname.
templates.env.globals["public_base"] = public_base_url
templates.env.globals["theme_choices"] = themes.choices


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
    weeks: list[int] = []
    earlier_season = None
    try:
        adapter = get_provider(league["provider"])
        weeks = adapter.available_weeks(
            league["platform_league_id"], league["season"])

        # Nothing playable? The offseason case: the id they pasted is this
        # year's empty shell and last year's completed season is one hop back.
        if not weeks:
            earlier_season = _find_playable_previous_season(adapter, league)
    except Exception:
        pass

    return _render(
        request, "league.html",
        league=league,
        paper_name=paper_name_for(league),
        weeks=weeks,
        earlier_season=earlier_season,
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
    theme: str = Form("tabloid"),
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
        "theme": themes.resolve(theme),
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


def _find_playable_previous_season(adapter, league: dict):
    """The most recent earlier season of this league that has real results.

    Returns a dict the template can render, or None. Skips the league we're
    already pointed at.
    """
    try:
        chain = adapter.season_chain(league["platform_league_id"])
    except Exception:
        return None

    for entry in chain[1:]:
        try:
            weeks = adapter.available_weeks(entry.league_id, entry.season)
        except Exception:
            continue
        if weeks:
            return {
                "league_id": entry.league_id,
                "season": entry.season,
                "name": entry.name,
                "weeks": weeks,
            }
    return None


@app.post("/l/{token}/use-season")
def use_season(token: str, platform_league_id: str = Form(...), season: int = Form(...)):
    """Point this paper at an earlier season of the same league.

    Sleeper gives a rolled-over league a new id each year, so this is how
    somebody who signed up in the offseason gets to write up last season
    instead of staring at an empty shell.
    """
    league = _require_league(token)

    # Only accept an id that genuinely belongs to this league's own history —
    # otherwise this endpoint would let anyone repoint a paper at any league.
    try:
        adapter = get_provider(league["provider"])
        chain_ids = {e.league_id for e in adapter.season_chain(league["platform_league_id"])}
    except Exception:
        chain_ids = set()

    if str(platform_league_id) not in chain_ids:
        return RedirectResponse(
            f"/l/{token}?error=That+season+doesn't+belong+to+this+league.",
            status_code=303)

    db.update_league(league["id"], {
        "platform_league_id": str(platform_league_id),
        "season": int(season),
    })
    return RedirectResponse(f"/l/{token}?notice=Now+writing+up+the+{season}+season.",
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
    theme: str = Form("tabloid"),
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
        "theme": themes.resolve(theme),
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
def generate(request: Request, token: str, week: int = Form(...),
             confirm_overwrite: str = Form("")):
    league = _require_league(token)

    if _rate_limited(f"gen:{_client_ip(request)}", GENERATIONS_PER_HOUR):
        return RedirectResponse(
            f"/l/{token}?error=That's+a+lot+of+papers+this+hour.+Try+later.",
            status_code=303)

    # Regenerating throws away hand-edited prose. Ask first rather than
    # silently deleting someone's work.
    existing = db.get_paper(league["id"], league["season"], week)
    if existing and existing.get("edited_at") and confirm_overwrite != "yes":
        return RedirectResponse(
            f"/l/{token}?error=Week+{week}+has+your+edits+in+it.+"
            f"Regenerating+would+wipe+them+-+open+the+editor+and+use+"
            f"Restore+the+original+if+that's+what+you+want.",
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

    # Published papers are cached hard (they never change once published), so
    # without a version in the URL the iframe would keep showing the copy from
    # before an edit. The token is the last write time.
    version = str(paper.get("edited_at") or paper.get("generated_at") or "")
    version = "".join(ch for ch in version if ch.isalnum())[-14:] or "1"
    base = f"/p/{league['public_slug']}/{league['season']}/week-{week}"

    return _render(
        request, "published.html",
        league=league,
        paper_name=paper_name_for(league),
        week=week,
        paper_url=base,
        preview_url=f"{base}?v={version}",
        subscriber_count=db.subscriber_count(league["id"]),
        was_edited=bool(paper.get("edited_at")),
    )


# ---------------------------------------------------------------------------
# Editing
#
# The paper renders from `ai_cache`, which already holds exactly the structure
# the renderer wants. So editing means changing that JSON and re-rendering —
# no Claude call, no cost, about a second.
#
# Claude gets it right most of the time and lands a joke badly some of the
# time. A commissioner who can fix one line will ship the paper; one who can't
# will quietly stop using it.
# ---------------------------------------------------------------------------

def _editable_fields(ai: dict) -> dict:
    """Flatten ai_cache into (name, label, value, rows) groups the form renders."""
    groups = []

    groups.append(("Front page", [
        ("headline", "Headline", ai.get("headline", ""), 2),
        ("lead_story", "Lead story", ai.get("lead_story", ""), 10),
    ]))

    matchups = ai.get("matchup_content") or []
    for i, m in enumerate(matchups):
        label = f"{m.get('winner', '?')} def. {m.get('loser', '?')}"
        groups.append((f"Game story — {label}", [
            (f"matchup_headline_{i}", "Headline", m.get("headline", ""), 2),
            (f"matchup_teaser_{i}", "Front page teaser", m.get("teaser", ""), 2),
            (f"matchup_body_{i}", "Story", m.get("body", ""), 9),
        ]))

    awards = ai.get("awards") or []
    award_fields = []
    for i, a in enumerate(awards):
        award_fields.append((f"award_title_{i}", "Title", a.get("title", ""), 1))
        award_fields.append((f"award_body_{i}", "Write-up", a.get("body", ""), 5))
    if award_fields:
        groups.append(("Weekly awards", award_fields))

    groups.append(("Fraud watch", [
        ("fraud_watch", "Fraud watch", ai.get("fraud_watch", ""), 6),
    ]))

    rankings = ai.get("power_rankings_comments") or {}
    ranking_fields = []
    for i, (team, comment) in enumerate(rankings.items()):
        ranking_fields.append((f"ranking_value_{i}", team, comment, 2))
    if ranking_fields:
        groups.append(("Power rankings", ranking_fields))

    return groups


def _apply_edits(ai: dict, form) -> dict:
    """Fold submitted values back into an ai_cache-shaped dict."""
    edited = dict(ai)

    # Everything here ends up rendered into a page served from our own domain
    # to every reader, so nothing goes in unsanitized. See web/sanitize.py.
    if "headline" in form:
        edited["headline"] = clean_text(form["headline"], 200)
    for key in ("lead_story", "fraud_watch"):
        if key in form:
            edited[key] = clean_html(form[key])

    matchups = [dict(m) for m in (ai.get("matchup_content") or [])]
    for i, m in enumerate(matchups):
        if f"matchup_headline_{i}" in form:
            m["headline"] = clean_text(form[f"matchup_headline_{i}"], 200)
        if f"matchup_teaser_{i}" in form:
            m["teaser"] = clean_text(form[f"matchup_teaser_{i}"], 200)
        if f"matchup_body_{i}" in form:
            m["body"] = clean_html(form[f"matchup_body_{i}"])
    if matchups:
        edited["matchup_content"] = matchups

    awards = [dict(a) for a in (ai.get("awards") or [])]
    for i, a in enumerate(awards):
        if f"award_title_{i}" in form:
            a["title"] = clean_text(form[f"award_title_{i}"], 120)
        if f"award_body_{i}" in form:
            a["body"] = clean_html(form[f"award_body_{i}"])
    if awards:
        edited["awards"] = awards

    rankings = dict(ai.get("power_rankings_comments") or {})
    for i, team in enumerate(list(rankings)):
        name = f"ranking_value_{i}"
        if name in form:
            rankings[team] = clean_text(form[name], 200)
    if rankings:
        edited["power_rankings_comments"] = rankings

    return edited


@app.get("/l/{token}/edit/{week}", response_class=HTMLResponse)
def edit_form(request: Request, token: str, week: int, error: str = ""):
    league = _require_league(token)
    paper = db.get_paper(league["id"], league["season"], week)
    if not paper or not paper.get("ai_cache"):
        return RedirectResponse(f"/l/{token}?error=Nothing+to+edit+for+that+week.",
                                status_code=303)

    return _render(
        request, "edit.html",
        league=league,
        paper_name=paper_name_for(league),
        week=week,
        groups=_editable_fields(paper["ai_cache"]),
        was_edited=bool(paper.get("edited_at")),
        has_original=bool(paper.get("ai_cache_original")),
        error=error,
    )


@app.post("/l/{token}/edit/{week}")
async def save_edits(request: Request, token: str, week: int):
    league = _require_league(token)
    paper = db.get_paper(league["id"], league["season"], week)
    if not paper or not paper.get("ai_cache"):
        return RedirectResponse(f"/l/{token}?error=Nothing+to+edit+for+that+week.",
                                status_code=303)

    form = await request.form()
    edited = _apply_edits(paper["ai_cache"], form)

    # Re-rendering re-fetches the week's stats and rebuilds the HTML. No Claude
    # call. Off the event loop because it does network I/O.
    try:
        await run_in_threadpool(render_and_store, db, league, week, edited, is_edit=True)
    except ProviderError as exc:
        return RedirectResponse(f"/l/{token}/edit/{week}?error={exc}", status_code=303)

    return RedirectResponse(f"/l/{token}/published/{week}", status_code=303)


def _apply_inline_edits(ai: dict, edits: dict, images: dict,
                        widths: dict | None = None,
                        removed: list | None = None) -> dict:
    """Fold inline edits back into an ai_cache-shaped dict.

    Keys mirror the data-edit-key attributes the renderer emits. Anything
    unrecognised is ignored rather than trusted — the payload comes from a
    browser, and only the fields the paper actually renders should be writable.
    """
    edited = dict(ai)

    # The payload is innerHTML straight out of a browser. Sanitize hard.
    if "headline" in edits:
        edited["headline"] = clean_text(edits["headline"], 200)
    for key in ("lead_story", "fraud_watch"):
        if key in edits:
            edited[key] = clean_html(edits[key])

    matchups = [dict(m) for m in (ai.get("matchup_content") or [])]
    awards = [dict(a) for a in (ai.get("awards") or [])]
    rankings = dict(ai.get("power_rankings_comments") or {})

    for key, raw in edits.items():
        if key.startswith("matchup_headline_") or key.startswith("matchup_body_"):
            field, _, idx = key.rpartition("_")
            if not idx.isdigit():
                continue
            i = int(idx)
            if 0 <= i < len(matchups):
                if field.endswith("headline"):
                    matchups[i]["headline"] = clean_text(raw, 200)
                else:
                    matchups[i]["body"] = clean_html(raw)

        elif key.startswith("award_title_") or key.startswith("award_body_"):
            field, _, idx = key.rpartition("_")
            if not idx.isdigit():
                continue
            i = int(idx)
            if 0 <= i < len(awards):
                if field.endswith("title"):
                    awards[i]["title"] = clean_text(raw, 120)
                else:
                    awards[i]["body"] = clean_html(raw)

        elif key.startswith("ranking:"):
            team = key.split(":", 1)[1]
            # Only teams that already have a comment — no inventing entries.
            if team in rankings:
                rankings[team] = clean_text(raw, 200)

    if matchups:
        edited["matchup_content"] = matchups
    if awards:
        edited["awards"] = awards
    if rankings:
        edited["power_rankings_comments"] = rankings

    # Photos are stored as {"url", "width"}. Older rows hold a bare URL
    # string, so normalize on the way through.
    merged = {}
    for slot, raw in (ai.get("images") or {}).items():
        merged[slot] = {"url": raw, "width": None} if isinstance(raw, str) else dict(raw)

    for slot, url in (images or {}).items():
        safe_url = clean_image_url(url) if isinstance(url, str) else None
        if safe_url:
            key = str(slot)[:40]
            entry = merged.get(key) or {}
            entry["url"] = safe_url
            merged[key] = entry

    # An explicit removal drops the uploaded photo, which lets the slot fall
    # back to the automatic one rather than staying empty forever.
    for slot in (removed or []):
        merged.pop(str(slot)[:40], None)

    for slot, width in (widths or {}).items():
        key = str(slot)[:40]
        if key not in merged:
            continue
        try:
            value = float(width)
        except (TypeError, ValueError):
            continue
        # Clamp: a browser can send anything, and a 4000%-wide photo would
        # destroy the layout for every reader.
        merged[key]["width"] = max(10.0, min(100.0, value))

    # Always write back, including when empty. Only assigning a non-empty dict
    # meant removing the last photo silently left the original one in place.
    if merged:
        edited["images"] = merged
    else:
        edited.pop("images", None)

    return edited


@app.get("/l/{token}/live-edit/{week}", response_class=HTMLResponse)
async def live_edit(request: Request, token: str, week: int):
    """The paper itself, editable in place.

    Rendered on demand and never stored — the copy in the bucket is always
    built with editable=False, so readers can't get an editable page even if
    they somehow found this URL's output.
    """
    league = _require_league(token)
    paper = db.get_paper(league["id"], league["season"], week)
    if not paper or not paper.get("ai_cache"):
        return RedirectResponse(f"/l/{token}?error=Nothing+to+edit+for+that+week.",
                                status_code=303)

    try:
        html = await run_in_threadpool(render_editable, db, league, week,
                                       paper["ai_cache"])
    except ProviderError as exc:
        return RedirectResponse(f"/l/{token}?error={exc}", status_code=303)

    config = (
        f'<div id="ce-config" style="display:none"'
        f' data-save-url="/l/{token}/edit/{week}/inline"'
        f' data-upload-url="/l/{token}/upload-image"'
        f' data-back-url="/l/{token}/published/{week}"></div>'
        f'<link rel="stylesheet" href="/static/liveedit.css" />'
        f'<script src="/static/liveedit.js" defer></script>'
    )
    html = html.replace("</body>", config + "</body>")

    return HTMLResponse(content=html,
                        headers={"Cache-Control": "no-store"})


@app.post("/l/{token}/edit/{week}/inline")
async def save_inline_edits(request: Request, token: str, week: int):
    league = _require_league(token)
    paper = db.get_paper(league["id"], league["season"], week)
    if not paper or not paper.get("ai_cache"):
        return JSONResponse({"error": "nothing to edit"}, status_code=404)

    payload = await request.json()
    edited = _apply_inline_edits(
        paper["ai_cache"],
        payload.get("edits") or {},
        payload.get("images") or {},
        payload.get("widths") or {},
        payload.get("removed") or [],
    )

    try:
        await run_in_threadpool(render_and_store, db, league, week, edited, is_edit=True)
    except ProviderError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    return JSONResponse({"ok": True, "redirect": f"/l/{token}/published/{week}"})


@app.post("/l/{token}/upload-image")
async def upload_image(token: str, photo: UploadFile = File(...)):
    """Accept a photo for the paper. Returns the URL to point a slot at."""
    league = _require_league(token)

    content_type = (photo.content_type or "").lower()
    if not content_type.startswith("image/"):
        return JSONResponse({"error": "that isn't an image"}, status_code=400)

    data = await photo.read()
    if len(data) > 8 * 1024 * 1024:
        return JSONResponse({"error": "over 8MB"}, status_code=413)

    url = await run_in_threadpool(
        db.upload_image, league["public_slug"],
        photo.filename or "photo.jpg", data, content_type)
    return JSONResponse({"url": url})


@app.post("/l/{token}/edit/{week}/revert")
async def revert_edits(token: str, week: int):
    """Put Claude's original words back."""
    league = _require_league(token)
    paper = db.get_paper(league["id"], league["season"], week)
    original = (paper or {}).get("ai_cache_original")
    if not original:
        return RedirectResponse(f"/l/{token}/edit/{week}?error=No+original+on+file.",
                                status_code=303)

    try:
        await run_in_threadpool(render_and_store, db, league, week, original,
                                is_edit=False)
    except ProviderError as exc:
        return RedirectResponse(f"/l/{token}/edit/{week}?error={exc}", status_code=303)

    return RedirectResponse(f"/l/{token}/published/{week}", status_code=303)


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


@app.get("/demo-image/{name}")
def demo_image(name: str):
    """Serve photos uploaded in demo mode, where there's no storage bucket."""
    # Guard on the active backend rather than the env flag: this route exists
    # only because the in-memory store has nowhere else to put bytes. With
    # Supabase behind it, photos are served from the bucket's own CDN.
    store = getattr(db, "_IMAGES", None)
    if store is None:
        raise HTTPException(status_code=404, detail="Not found.")
    entry = store.get(name)
    if not entry:
        raise HTTPException(status_code=404, detail="Not found.")
    data, content_type = entry
    return Response(content=data, media_type=content_type)


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
