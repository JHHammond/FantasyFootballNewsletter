"""
The Commissioner's Desk — web app.

READERS NEVER SIGN IN. That is the load-bearing decision and it has not
changed: monetization is ad impressions from readers, so a wall in front of
them is the one change that would actually cost money. /p/ takes no session, no
cookie and no account, forever.

Commissioners do have accounts, added after the accountless version showed its
cost. The admin token was the only credential, it lived in a single URL, and
losing that URL was unrecoverable — re-submitting the league ID hit "this
league already has a paper", and recovery only worked for people who had
already saved an email, which happened on a page reached *after* the moment
things went wrong. A dead end sitting in the signup path.

So there are now two ways to manage a league, and both stay:

    the admin token   — still the credential. Every league made before accounts
                        works this way, and a bookmarked link opens with no
                        session. Opening one while signed in claims it.
    an account        — email and password, so losing the link is survivable
                        and several leagues live in one place.

Three URLs per league:

    /l/<admin_token>            manage. Secret. Bookmark it.
    /p/<public_slug>            read. Share freely. No sign-in, ever.
    /account                    every league you own.

Run locally:

    DEMO_MODE=1 uvicorn web.app:app --reload --port 8000
"""

from __future__ import annotations

import hmac
import os
import sys
import threading
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

from . import auth, emailer, images, legal, slugs  # noqa: E402
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
MAGIC_LINK_TTL_MINUTES = 30

# Error tracking, if it's configured. Optional import so the app runs with no
# account and no extra dependency — but without something like this, a bug that
# breaks generation for everyone looks identical to a quiet week.
if os.getenv("SENTRY_DSN"):
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=os.environ["SENTRY_DSN"],
            traces_sample_rate=0.0,   # errors, not performance
            send_default_pii=False,   # admin tokens live in URLs
        )
    except ImportError:
        print("SENTRY_DSN is set but sentry-sdk isn't installed. "
              "Add it to requirements.txt or unset the variable.")

app = FastAPI(title="The Commissioner's Desk", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


templates.env.globals["demo_mode"] = DEMO_MODE
# Templates build share links from this rather than request.base_url,
# which behind a proxy reports http:// and the internal hostname.
templates.env.globals["public_base"] = public_base_url
templates.env.globals["theme_choices"] = themes.choices


# ---------------------------------------------------------------------------
# Security headers
#
# The whole auth model is a secret in a URL, and browsers put the current URL
# in the Referer header of outbound requests. The paper carries third-party ad
# scripts, and the commissioner views that paper at /l/<token>/live-edit — so
# without this header, turning on ads hands the ad network a working admin
# credential for every league that loads it.
#
# no-referrer rather than same-origin: nothing here needs to know where a
# reader came from, and the failure mode of getting it wrong is losing control
# of every league in the database.
# ---------------------------------------------------------------------------

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # SAMEORIGIN, not DENY: /l/<token>/published frames the paper itself.
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")

    # Papers name real people and say cutting things about them. They are meant
    # to be shared by link, not to become the top search result for somebody's
    # actual name.
    if request.url.path.startswith("/p/") or request.url.path.startswith("/l/"):
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    return response


# ---------------------------------------------------------------------------
# Rate limiting
#
# Removing accounts removed the natural brake on abuse. Generation costs real
# Claude tokens and email costs deliverability reputation, so both need a cap.
#
# The counting lives in Postgres (migration 009), not in this process. It used
# to be a dict here, which was honest on one instance and said so — but it made
# every limit a property of the deployment rather than of the product. A second
# instance doubled them all; a restart reset them to zero. The one limit that
# exists to stop an Anthropic invoice should not be the one that forgets.
#
# Two keys, deliberately. IP is the only handle we have on an anonymous caller,
# but it is a claim, not a fact — see _client_ip. League ID is a fact: it comes
# from our own database via the admin token, so a per-league cap holds even
# against someone who can present any IP they like.
# ---------------------------------------------------------------------------

GENERATIONS_PER_HOUR = 10           # per IP
GENERATIONS_PER_LEAGUE_PER_DAY = 12  # per league — not spoofable
LEAGUE_CREATES_PER_HOUR = 5
SUBSCRIBES_PER_HOUR = 20
RECOVERIES_PER_HOUR = 5
UPLOADS_PER_HOUR = 30               # per IP
UPLOADS_PER_LEAGUE_PER_DAY = 60     # per league
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# Credential stuffing is the attack accounts invite, and it is run from many
# addresses against many accounts at once. Limiting only by IP stops nobody;
# limiting only by email lets one address grind through a user list. Both.
LOGINS_PER_IP_PER_HOUR = 20
LOGINS_PER_ACCOUNT_PER_HOUR = 8
SIGNUPS_PER_HOUR = 5
RESETS_PER_ACCOUNT_PER_DAY = 5
LOOKUPS_PER_HOUR = 30               # username -> leagues, per IP

DAY = 86400

#: Total papers generated in a rolling day, across everybody and every
#: instance. The backstop against a bug or an abuser turning into an Anthropic
#: invoice. Set a hard budget limit on the API key as well: this one lives in a
#: database you could in principle empty, and that one cannot.
MAX_PAPERS_PER_DAY = int(os.getenv("MAX_PAPERS_PER_DAY", "300"))

#: How many papers may be written at the same time. Unlike the limits above
#: this one is deliberately per-process, because what it protects is per
#: process: every route here is a sync def, so they share one bounded
#: threadpool, and a generation occupies a slot for ~30 seconds. Enough
#: simultaneous generations starve the pool and readers stop being served —
#: which is a good launch day, not an attack.
MAX_CONCURRENT_GENERATIONS = int(os.getenv("MAX_CONCURRENT_GENERATIONS", "4"))
_GENERATION_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_GENERATIONS)


def _rate_limited(key: str, limit: int, window: int = 3600) -> bool:
    """True if this key has already used its allowance in the window.

    Thin wrapper so every call site reads the same as before. The claim is
    atomic inside the database — a check followed by a separate insert from
    here would let two simultaneous callers both pass a ceiling that had one
    slot left, which is exactly the moment a spend ceiling is for.
    """
    return not db.claim_rate_slot(key, limit, window)


def _global_budget_exceeded() -> bool:
    """Has this process written its daily allowance of papers?"""
    return _rate_limited("global:papers", MAX_PAPERS_PER_DAY, window=DAY)


#: X-Forwarded-For is a list the client can start. Proxies append, so the
#: rightmost entry is the address the edge actually saw and the leftmost is
#: whatever the caller typed. Counting from the right by the number of proxies
#: in front of us is the only reading that isn't a suggestion from the caller.
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "1"))


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        # Fewer entries than there are proxies in front of us means the header
        # didn't come through the path we expect, so it tells us nothing. The
        # socket address is the only thing left that nobody chose.
        if len(hops) >= TRUSTED_PROXY_HOPS >= 1:
            return hops[len(hops) - TRUSTED_PROXY_HOPS]
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_league(token: str) -> dict:
    """Load a league by admin token, or 404.

    404 rather than 403: a wrong guess should be indistinguishable from a
    league that doesn't exist.

    The token remains the credential. Accounts are an addition, not a
    replacement — every league made before they existed has no owner, and a
    bookmarked manage link has to keep working with no session at all.
    """
    league = db.league_by_admin_token(token)
    if not league:
        raise HTTPException(status_code=404, detail="No league found for that link.")
    return league


def _adopt_if_unowned(request: Request, league: dict) -> dict:
    """Signed-in visitor opening an ownerless league takes ownership of it.

    How a league made before signing up joins an account: open the link you
    already have while signed in, and it's yours. No migration, no import step.

    Only ever claims a league with NO owner. Holding the token of somebody
    else's league already grants full control, so this transfers nothing that
    wasn't already available — but silently reassigning an owned league would
    let a shared link quietly steal it.
    """
    if league.get("user_id"):
        return league
    user = current_user(request)
    if not user:
        return league
    try:
        db.claim_league(league["id"], user["id"])
    except Exception:  # noqa: BLE001 — convenience, never worth failing a page
        return league
    league = dict(league)
    league["user_id"] = user["id"]
    return league


def current_user(request: Request) -> dict | None:
    """The signed-in user, or None. Never raises — most pages work either way."""
    user_id = auth.read_session(request.cookies.get(auth.SESSION_COOKIE, ""))
    if not user_id:
        return None
    try:
        return db.user_by_id(user_id)
    except Exception:  # noqa: BLE001 — a database blip shouldn't 500 a page
        return None                     # that renders perfectly well logged out


def _render(request: Request, template: str, **context) -> HTMLResponse:
    # Every template can ask who's looking, so the header renders correctly
    # without each route having to remember to pass it.
    context.setdefault("user", current_user(request))
    return templates.TemplateResponse(request, template, context)


def _require_user(request: Request) -> dict:
    """For pages that only make sense signed in."""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to see that.")
    return user


def _may_manage(request: Request, league: dict) -> bool:
    """Does the person making this request control this league?

    Two ways in, deliberately. The admin token is still the credential — it is
    how every league created before accounts works, and how a bookmarked manage
    link keeps working with no session. Ownership is the second way, so losing
    the link stops being fatal.
    """
    if not league:
        return False
    user = current_user(request)
    return bool(user and league.get("user_id") == user["id"])


def _implemented_providers():
    return [p for p in available_providers() if p["implemented"]]


# ---------------------------------------------------------------------------
# Landing / league creation
# ---------------------------------------------------------------------------

#: A published paper to show people before asking them for anything. The
#: product is the writing, and asking someone to paste a league ID before
#: they've read a sentence of it is the largest avoidable drop-off on the site.
#: Generate one good paper, then put its public URL here.
SAMPLE_PAPER_URL = os.getenv("SAMPLE_PAPER_URL", "").strip()


# ---------------------------------------------------------------------------
# Connecting a platform
#
# "Paste the long number out of your league's URL" was the first thing the
# product ever asked anyone to do, and it is the worst step in it. Sleeper will
# resolve a username to an account and then list every league that account is
# in, so for the platform most people here actually use, the question becomes
# "what's your username" and the answer is a list to click.
#
# ESPN and Yahoo cannot do this. ESPN has no public directory; Yahoo answers
# nothing at all without OAuth. Rather than ship a button that fails, they
# collect an address and say so.
# ---------------------------------------------------------------------------

#: Platforms in the picker, and what each can currently do. `ready` is the
#: honest bit: false means the button leads to a waiting list, not a dead end.
PLATFORMS = [
    {"key": "sleeper", "name": "Sleeper", "ready": True,
     "how": "Type your username and pick from your leagues."},
    {"key": "espn", "name": "ESPN", "ready": False,
     "how": "Being built. ESPN has no way to look you up by name, so it will "
            "ask for a league ID."},
    {"key": "yahoo", "name": "Yahoo", "ready": False,
     "how": "Being built. Yahoo requires signing in with them first."},
]


@app.get("/connect", response_class=HTMLResponse)
def connect(request: Request, error: str = ""):
    _require_user(request)
    return _render(request, "connect.html", platforms=PLATFORMS, error=error)


@app.get("/connect/sleeper", response_class=HTMLResponse)
def connect_sleeper(request: Request, username: str = "", error: str = ""):
    _require_user(request)
    return _render(request, "connect_sleeper.html",
                   username=username, error=error, leagues=None)


@app.post("/connect/sleeper")
def connect_sleeper_lookup(request: Request, username: str = Form(...)):
    """Username -> the leagues that account is in this season."""
    user = _require_user(request)

    def fail(message: str, leagues=None):
        return _render(request, "connect_sleeper.html",
                       username=username.strip(), error=message, leagues=leagues)

    if _rate_limited(f"lookup:{_client_ip(request)}", LOOKUPS_PER_HOUR):
        return fail("That's a lot of lookups. Give it a few minutes.")

    adapter = get_provider("sleeper")
    try:
        account = adapter.find_user(username)
    except ProviderError as exc:
        return fail(f"Couldn't reach Sleeper. {exc}")

    if not account:
        return fail("No Sleeper account with that username. It's the one you "
                    "sign in with, not your team name.")

    import nfl_week
    season = nfl_week.current_season()
    try:
        leagues = adapter.user_leagues(account["user_id"], season)
    except ProviderError as exc:
        return fail(f"Found you, but couldn't list your leagues. {exc}")

    if not leagues:
        return fail(f"That account isn't in any {season} leagues on Sleeper. "
                    f"If your league is from a previous year, you can still "
                    f"add it by league ID below.")

    # Leagues this account already made are shown as already added rather than
    # silently failing when they click.
    mine = {l.get("platform_league_id") for l in db.leagues_for_user(user["id"])}
    return _render(request, "connect_sleeper.html",
                   username=account["username"], error="",
                   leagues=[{
                       "league_id": l.league_id,
                       "name": l.name,
                       "season": l.season,
                       "team_count": l.team_count,
                       "status": l.status,
                       "avatar_url": l.avatar_url,
                       "already": l.league_id in mine,
                   } for l in leagues])


@app.post("/connect/sleeper/add")
def connect_sleeper_add(request: Request, league_id: str = Form(...),
                        paper_name: str = Form("")):
    """Turn a chosen league into a paper, owned by the signed-in account."""
    user = _require_user(request)

    if _rate_limited(f"create:{_client_ip(request)}", LEAGUE_CREATES_PER_HOUR):
        return RedirectResponse(
            "/connect/sleeper?error=That's+a+few+already.+Try+again+in+an+hour.",
            status_code=303)

    adapter = get_provider("sleeper")
    try:
        info = adapter.describe_league(league_id.strip())
    except (ProviderError, ValueError):
        info = None
    if not info:
        return RedirectResponse(
            "/connect/sleeper?error=Couldn't+read+that+league+from+Sleeper.",
            status_code=303)

    existing = db.find_existing_league("sleeper", league_id.strip(), info.season)
    if existing:
        # Already theirs: just open it.
        if existing.get("user_id") == user["id"]:
            return RedirectResponse(f"/l/{existing['admin_token']}", status_code=303)

        # Nobody owns it: adopt it. A league with no account attached is either
        # something this person made before signing up, or an abandoned attempt
        # — and refusing was a dead end, because the only way back in was an
        # admin token they no longer had. There is nothing here to protect:
        # anyone who can read the league ID off a URL could have created this
        # row themselves had it not existed.
        if not existing.get("user_id"):
            db.claim_league(existing["id"], user["id"])
            return RedirectResponse(
                f"/l/{existing['admin_token']}?notice=Picked+up+where+you+left+off.",
                status_code=303)

        # Someone else's. That one stays refused, and says what to do.
        return RedirectResponse(
            "/connect/sleeper?error=Another+account+already+has+a+paper+for+that+"
            "league.+If+that+is+you,+sign+in+with+that+email.",
            status_code=303)

    league = db.create_league(
        provider="sleeper",
        platform_league_id=league_id.strip(),
        league_name=info.name,
        paper_name=(paper_name.strip() or f"The {info.name} Times"),
        commissioner_name="",
        season=info.season,
        public_slug=slugs.public_slug(info.name),
        admin_token=slugs.admin_token(),
    )
    db.claim_league(league["id"], user["id"])
    return RedirectResponse(f"/l/{league['admin_token']}/setup", status_code=303)


@app.get("/connect/{platform}", response_class=HTMLResponse)
def connect_waitlist(request: Request, platform: str, sent: int = 0):
    _require_user(request)
    known = next((p for p in PLATFORMS if p["key"] == platform), None)
    if not known:
        raise HTTPException(status_code=404, detail="No such platform.")
    if known["ready"]:
        return RedirectResponse(f"/connect/{platform}", status_code=303)
    return _render(request, "connect_waitlist.html",
                   platform=known, sent=bool(sent))


@app.post("/connect/{platform}/notify")
def connect_notify(request: Request, platform: str):
    """Register interest. Which platform people ask for is the cheapest
    possible answer to what to build next."""
    user = _require_user(request)
    known = next((p for p in PLATFORMS if p["key"] == platform), None)
    if not known or known["ready"]:
        raise HTTPException(status_code=404, detail="No such platform.")

    print(f"PLATFORM INTEREST {platform} {user['email']}", flush=True)
    return RedirectResponse(f"/connect/{platform}?sent=1", status_code=303)


# ---------------------------------------------------------------------------
# Accounts
#
# Readers are untouched by everything in this section. /p/ has no session, no
# cookie and no sign-in, because ad impressions come from readers and a wall in
# front of them would be the one change that actually costs money.
#
# This exists for the commissioner, whose only credential used to be a token in
# a URL that could be lost permanently.
# ---------------------------------------------------------------------------

def _set_session(response, user_id: str):
    response.set_cookie(auth.SESSION_COOKIE, auth.make_session(user_id),
                        **auth.cookie_kwargs())
    return response


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request, error: str = "", email: str = ""):
    if current_user(request):
        return RedirectResponse("/account", status_code=303)
    return _render(request, "signup.html", error=error, email=email)


@app.post("/signup")
def signup(request: Request, email: str = Form(...), password: str = Form(...),
           confirm: str = Form("")):
    def fail(message: str):
        return _render(request, "signup.html", error=message,
                       email=(email or "").strip())

    if _rate_limited(f"signup:{_client_ip(request)}", SIGNUPS_PER_HOUR):
        return fail("That's a few accounts already. Try again in an hour.")

    address = auth.clean_email(email)
    if not address:
        return fail("That doesn't look like an email address.")

    problem = auth.password_problem(password, address)
    if problem:
        return fail(problem)
    if password != confirm:
        return fail("Those two passwords don't match.")

    user = db.create_user(address, auth.hash_password(password))
    if not user:
        # The address is taken. Saying so out loud turns this form into a
        # checker for "does this person have an account here", so it doesn't —
        # it says what a real signup says and mails the existing owner instead.
        existing = db.user_by_email(address)
        if existing:
            emailer.send_account_exists(address)
        return _render(request, "message.html",
                       heading="Check your inbox",
                       body="If that address is new, you're signed up. If it "
                            "already had an account, we've sent a reminder.",
                       link_url="/login", link_label="Sign in")

    # Straight on to the next thing rather than an empty shelf. Signing up is
    # not the goal; having a paper is.
    response = RedirectResponse("/connect", status_code=303)
    return _set_session(response, user["id"])


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str = "", next: str = ""):
    if current_user(request):
        return RedirectResponse("/account", status_code=303)
    return _render(request, "login.html", error=error, next=next)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          next: str = Form("")):
    # One message for every failure. Distinguishing "no such account" from
    # "wrong password" hands an attacker a free list of which addresses are
    # worth attacking.
    wrong = "That email and password don't match."

    def fail():
        return _render(request, "login.html", error=wrong, next=next)

    address = auth.clean_email(email) or ""

    if _rate_limited(f"login-ip:{_client_ip(request)}", LOGINS_PER_IP_PER_HOUR):
        return _render(request, "login.html",
                       error="Too many attempts from here. Try again later.",
                       next=next)
    # Keyed on the account too: credential stuffing arrives from thousands of
    # addresses, so an IP limit alone protects nobody.
    if address and _rate_limited(f"login-acct:{address}",
                                 LOGINS_PER_ACCOUNT_PER_HOUR):
        return fail()

    user = db.user_by_email(address) if address else None
    if not user:
        # Spend the same time as a real verify would, so the response time
        # doesn't reveal whether the account exists.
        auth.verify_password(password, auth.hash_password("decoy"))
        return fail()

    if not auth.verify_password(password, user["password_hash"]):
        return fail()

    updates = {"last_login_at": "now()"}
    # Cost parameters may have been raised since this hash was made. This is
    # the only moment the plaintext is available to upgrade it.
    if auth.needs_rehash(user["password_hash"]):
        updates["password_hash"] = auth.hash_password(password)
    db.update_user(user["id"], updates)

    # Only ever redirect within this site: an open redirect turns the login
    # page into a convincing launchpad for somebody else's phishing.
    destination = next if next.startswith("/") and not next.startswith("//") else "/account"
    return _set_session(RedirectResponse(destination, status_code=303), user["id"])


@app.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, welcome: int = 0, notice: str = ""):
    user = _require_user(request)
    return _render(request, "account.html",
                   leagues=db.leagues_for_user(user["id"]),
                   welcome=bool(welcome), notice=notice)


# --- password reset, on the magic-link machinery that already exists --------

@app.get("/forgot", response_class=HTMLResponse)
def forgot_form(request: Request, sent: int = 0):
    return _render(request, "forgot.html", sent=bool(sent))


@app.post("/forgot")
def forgot(request: Request, email: str = Form(...)):
    address = auth.clean_email(email)
    if address and not _rate_limited(f"reset:{address}",
                                     RESETS_PER_ACCOUNT_PER_DAY, window=DAY):
        user = db.user_by_email(address)
        if user:
            token = slugs.admin_token()
            expires = datetime.now(timezone.utc) + timedelta(
                minutes=MAGIC_LINK_TTL_MINUTES)
            db.create_magic_link(address, token, expires.isoformat(),
                                 purpose="reset")
            emailer.send_password_reset(address, token)

    # Always the same answer, sent or not. Otherwise this page reports whether
    # an address has an account here.
    return RedirectResponse("/forgot?sent=1", status_code=303)


@app.get("/reset/{token}", response_class=HTMLResponse)
def reset_form(request: Request, token: str, error: str = ""):
    return _render(request, "reset.html", token=token, error=error)


@app.post("/reset/{token}")
def reset(request: Request, token: str, password: str = Form(...),
          confirm: str = Form("")):
    # Checked before the token is burned, so a rejected password doesn't cost
    # the user their one-shot link.
    address = db.peek_magic_link(token, purpose="reset")
    if not address:
        return _render(request, "message.html",
                       heading="That link has expired",
                       body="Reset links work once and last 30 minutes.",
                       link_url="/forgot", link_label="Send a new one")

    problem = auth.password_problem(password, address)
    if problem:
        return _render(request, "reset.html", token=token, error=problem)
    if password != confirm:
        return _render(request, "reset.html", token=token,
                       error="Those two passwords don't match.")

    if not db.consume_magic_link(token, purpose="reset"):
        return _render(request, "message.html",
                       heading="That link has expired",
                       body="Reset links work once and last 30 minutes.",
                       link_url="/forgot", link_label="Send a new one")

    user = db.user_by_email(address)
    if not user:
        return RedirectResponse("/login", status_code=303)

    db.update_user(user["id"], {"password_hash": auth.hash_password(password)})
    return _set_session(
        RedirectResponse("/account?notice=Password+changed.", status_code=303),
        user["id"])


@app.get("/", response_class=HTMLResponse)
def index(request: Request, error: str = ""):
    import nfl_week
    return _render(request, "index.html",
                   providers=_implemented_providers(),
                   sample_paper_url=SAMPLE_PAPER_URL,
                   current_week=nfl_week.completed_week(),
                   current_season=nfl_week.current_season(),
                   error=error)


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

    # Signed in? It's theirs, and losing the link stops mattering.
    user = current_user(request)
    if user:
        db.claim_league(league["id"], user["id"])

    return RedirectResponse(f"/l/{league['admin_token']}/setup", status_code=303)


# ---------------------------------------------------------------------------
# Manage
# ---------------------------------------------------------------------------

@app.get("/l/{token}", response_class=HTMLResponse)
def manage(
    request: Request, token: str,
    new: int = 0, generated: int = 0, error: str = "", notice: str = "",
):
    league = _adopt_if_unowned(request, _require_league(token))

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

    papers = db.list_papers(league["id"])

    return _render(
        request, "league.html",
        league=league,
        paper_name=paper_name_for(league),
        weeks=weeks,
        earlier_season=earlier_season,
        lore=db.get_lore(league["id"]),
        papers=papers,
        total_reads=sum(int(p.get("view_count") or 0) for p in papers),
        subscriber_count=db.subscriber_count(league["id"]),
        is_new=bool(new),
        generated_week=generated or None,
        error=error,
        notice=notice,
    )


#: Lore rides along in the prompt for every generation, forever. Unbounded, a
#: pasted chat export becomes thousands of rows that cost money and crowd out
#: the actual week. Forty is more than any league has.
MAX_LORE_ENTRIES = 40


@app.post("/l/{token}/lore")
def add_lore(token: str, entry: str = Form(...)):
    league = _require_league(token)
    text = entry.strip()
    if not text:
        return RedirectResponse(f"/l/{token}", status_code=303)
    if len(db.get_lore(league["id"])) >= MAX_LORE_ENTRIES:
        return RedirectResponse(
            f"/l/{token}?error=That's+{MAX_LORE_ENTRIES}+bits+of+lore+-+"
            f"the+cap.+Remove+one+to+add+another.", status_code=303)
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


@app.post("/l/{token}/rotate")
def rotate_admin_token(token: str):
    """Mint a new manage link and invalidate this one.

    A bearer token with no rotation path is one forwarded email away from being
    permanent. This is the "I shared my screen / posted the wrong link" button,
    and it's also what makes the token model defensible rather than merely
    convenient.
    """
    league = _require_league(token)
    fresh = slugs.admin_token()
    db.update_league(league["id"], {"admin_token": fresh})

    # If they've given us an address, send the new link there too — otherwise a
    # rotation from a phone leaves them with a link only in that browser.
    if league.get("owner_email"):
        emailer.send_manage_link(league["owner_email"], paper_name_for(league), fresh)

    return RedirectResponse(
        f"/l/{fresh}?notice=New+link+created.+The+old+one+no+longer+works+-+"
        f"bookmark+this+page.", status_code=303)


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

    # One entry per line, so people can paste a list rather than submit six
    # times — capped, because "paste a list" and "paste an entire group chat
    # export" look identical from here.
    added = 0
    for line in lore.splitlines():
        if added >= MAX_LORE_ENTRIES:
            break
        entry = line.strip().lstrip("-•*").strip()
        if entry:
            db.add_lore(league["id"], entry[:500])
            added += 1

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

    # The cap that holds even when the caller controls the IP.
    if _rate_limited(f"gen-league:{league['id']}",
                     GENERATIONS_PER_LEAGUE_PER_DAY, window=DAY):
        return RedirectResponse(
            f"/l/{token}?error=This+league+has+hit+its+daily+limit+of+"
            f"{GENERATIONS_PER_LEAGUE_PER_DAY}+papers.+Try+tomorrow.",
            status_code=303)

    if _global_budget_exceeded():
        return RedirectResponse(
            f"/l/{token}?error=The+presses+have+hit+today's+limit.+"
            f"Nothing+is+broken+-+try+again+tomorrow.",
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

    # Non-blocking: if every slot is busy, say so immediately rather than
    # queueing and holding a threadpool slot while we wait for one.
    if not _GENERATION_SLOTS.acquire(blocking=False):
        return RedirectResponse(
            f"/l/{token}?error=The+presses+are+busy+right+now.+"
            f"Give+it+a+minute+and+hit+generate+again.",
            status_code=303)
    try:
        generate_and_store(db, league, week)
    except ProviderError as exc:
        return RedirectResponse(f"/l/{token}?error={exc}", status_code=303)
    finally:
        _GENERATION_SLOTS.release()

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
async def upload_image(request: Request, token: str, photo: UploadFile = File(...)):
    """Accept a photo for the paper. Returns the URL to point a slot at.

    Nothing the caller says about the file is trusted. The type comes from the
    bytes, and both the stored extension and the stored content-type are
    derived from that — see web/images.py for why.
    """
    league = _require_league(token)

    # This endpoint writes megabytes to a storage bucket, so it needs the same
    # brakes as generation. It previously had none at all, which made an admin
    # token — free to mint — into a free anonymous file host.
    if _rate_limited(f"upload:{_client_ip(request)}", UPLOADS_PER_HOUR):
        return JSONResponse({"error": "too many photos this hour"}, status_code=429)
    if _rate_limited(f"upload-league:{league['id']}",
                     UPLOADS_PER_LEAGUE_PER_DAY, window=DAY):
        return JSONResponse(
            {"error": "this league has uploaded a lot of photos today"},
            status_code=429)

    data = await photo.read()
    if len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse({"error": "over 8MB"}, status_code=413)

    kind = images.sniff(data)
    if kind is None:
        return JSONResponse({"error": images.describe_rejection(data)},
                            status_code=400)

    url = await run_in_threadpool(
        db.upload_image, league["public_slug"],
        f"photo.{kind.extension}", data, kind.mime)
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

    # The number that matters. Counted here rather than in the browser so it
    # survives ad blockers, and fire-and-forget so a slow write never delays a
    # reader. Undercounts anything served from a CDN edge, which is the right
    # trade against blocking the page.
    db.record_view(league["id"], season, week)

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
    supplied = request.headers.get("x-task-key") or ""
    # compare_digest rather than !=, which returns on the first differing byte
    # and so leaks the key's prefix to anyone willing to time enough requests.
    if not expected or not hmac.compare_digest(supplied, expected):
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


# ---------------------------------------------------------------------------
# Legal
#
# Required by every ad network before they'll approve a site, and required on
# the merits because this collects email addresses.
# ---------------------------------------------------------------------------

@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return _render(request, "legal.html",
                   heading="Privacy",
                   updated=legal.LAST_UPDATED,
                   sections=legal.PRIVACY_SECTIONS,
                   contact_email=legal.contact_email(),
                   mailing_address=legal.mailing_address())


@app.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return _render(request, "legal.html",
                   heading="Terms",
                   updated=legal.LAST_UPDATED,
                   sections=legal.TERMS_SECTIONS,
                   contact_email=legal.contact_email(),
                   mailing_address=legal.mailing_address())


@app.get("/robots.txt")
def robots():
    """Keep crawlers off the papers and the manage pages.

    The paper is meant to travel as a link in a group chat, not to become the
    search result for a real person's name attached to an AI insult. The
    landing page is the part worth indexing.
    """
    body = (
        "User-agent: *\n"
        "Disallow: /p/\n"
        "Disallow: /l/\n"
        "Disallow: /recover\n"
        "Disallow: /subscribe/\n"
        "Disallow: /unsubscribe/\n"
        "Allow: /$\n"
        f"\nSitemap: {public_base_url()}/sitemap.xml\n"
    )
    return Response(content=body, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    """Only the pages we actually want found."""
    base = public_base_url()
    urls = "".join(
        f"<url><loc>{base}{path}</loc></url>"
        for path in ("/", "/privacy", "/terms")
    )
    return Response(
        content=('<?xml version="1.0" encoding="UTF-8"?>'
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                 f'{urls}</urlset>'),
        media_type="application/xml",
    )


@app.get("/healthz")
def healthz():
    """Alive AND able to do the job.

    An unconditional {"ok": true} meant Render kept an instance in rotation
    while it couldn't reach the database, and a deploy that broke the
    connection still reported green.
    """
    try:
        db.health_check()
    except Exception as exc:  # noqa: BLE001 — any failure is a failure
        return JSONResponse({"ok": False, "detail": str(exc)[:300]},
                            status_code=503)

    # Reported, not raised. A half-migrated database still serves every paper
    # that already exists, so pulling the service out of rotation over it would
    # take working pages down. But it is the single most likely reason for a
    # 500 on a fresh deploy, and it should be one request away from obvious
    # rather than buried in a stack trace.
    missing = db.schema_report()
    if missing:
        return JSONResponse({
            "ok": True,
            "warning": "unapplied migrations",
            "missing": missing,
            "fix": "run these in the Supabase SQL editor in order, then: "
                   "notify pgrst, 'reload schema';",
        })
    return {"ok": True}


@app.exception_handler(404)
def not_found(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        request, "error.html",
        {"message": getattr(exc, "detail", "Not found.")},
        status_code=404,
    )


@app.exception_handler(500)
def server_error(request: Request, exc: Exception):
    """A reader who tapped a link from a group chat should not get raw JSON.

    The traceback goes to stdout, which is where the host captures it. Saying
    "it's been logged" and meaning it matters: that line is the only thing
    pointing whoever hits this at somewhere useful.
    """
    import traceback
    print("UNHANDLED ERROR on", request.url.path, flush=True)
    traceback.print_exc()

    return templates.TemplateResponse(
        request, "error.html",
        {"heading": "That didn't work.",
         "message": "Something broke on our end, not on yours. It's been "
                    "logged — try again in a minute."},
        status_code=500,
    )
