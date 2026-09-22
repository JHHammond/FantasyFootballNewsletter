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
from contextlib import asynccontextmanager
from urllib.parse import quote
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
    AuthRequired,
    ProviderError,
    available_providers,
    get_provider,
)

import plans  # noqa: E402
import printing  # noqa: E402
import themes  # noqa: E402

from . import auth, billing, emailer, images, legal, oauth, slugs  # noqa: E402
from .sanitize import clean_html, clean_image_url, clean_text  # noqa: E402
from .generate import (  # noqa: E402
    WriterError,
    MAX_LETTER_CHARS,
    generate_and_store,
    letter_from_html,
    managers_for_page,
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

def announce_missing_migrations():
    """Shout about an unapplied migration into the deploy log.

    /healthz has reported this since the first time it bit us — but it reports
    it in a 200 response body, deliberately, so that a half-migrated database
    doesn't get pulled out of rotation and take the working pages down with it.
    The consequence is that the host's own health probe logs a cheerful
    "GET /healthz 200 OK" and the warning inside it is never read by anybody.

    That is how migration 006 stayed missing through two deploys and surfaced
    as a 500 *after* fourteen seconds of Claude calls had already been paid
    for. The check was right. The channel was wrong. Startup logs are the
    thing that actually gets looked at after a deploy, so say it there.
    """
    if DEMO_MODE:
        return
    try:
        missing = db.schema_report()
    except Exception as exc:  # noqa: BLE001 — never block startup on a probe
        print(f"[schema] could not be checked: {exc}", flush=True)
        return

    if not missing:
        print("[schema] all migrations present", flush=True)
        return

    print("\n" + "!" * 70, flush=True)
    print(f"!! UNAPPLIED MIGRATIONS: {', '.join(missing)}", flush=True)
    print("!! Papers that already exist will serve. Generating a new one will",
          flush=True)
    print("!! fail, possibly AFTER the Claude calls have been made and paid",
          flush=True)
    print("!! for. Run these in the Supabase SQL editor, in order, then:",
          flush=True)
    print("!!     notify pgrst, 'reload schema';", flush=True)
    print("!! migrations/CHECK_SCHEMA.sql lists every one in a single query.",
          flush=True)
    print("!" * 70 + "\n", flush=True)


def announce_unusable_publisher_token() -> None:
    """Say so at boot if PUBLISHER_TOKEN can never work.

    The token is a URL PATH segment: /publisher/{token}. A token containing a
    slash splits the path, the route never matches, and the page 404s — which
    is exactly what a wrong token looks like, and exactly what an unset one
    looks like. Three different problems, one symptom, no way to tell them
    apart from outside.

    This happened. render.yaml asked Render to generate the value, Render
    generates standard base64, and standard base64 contains "/" and "+". The
    first value drawn happened to contain neither, which is a 25% outcome —
    the next rotation would have had a better than even chance of quietly
    breaking the page with nothing in any log to say why.

    render.yaml now asks for a URL-safe token instead. This is the belt to
    that braces, in the deploy log, where somebody is standing when it matters.
    """
    token = os.getenv("PUBLISHER_TOKEN", "")
    if not token:
        return          # off, deliberately. Not a fault.

    bad = [ch for ch in "/?#% " if ch in token]
    if bad:
        print("\n" + "!" * 70, flush=True)
        print("PUBLISHER_TOKEN contains " + ", ".join(repr(c) for c in bad)
              + " and cannot work in a URL.", flush=True)
        print("/publisher/<token> will 404 and look exactly like a wrong "
              "token.", flush=True)
        print("Replace it with:  python3 -c \"import secrets; "
              "print(secrets.token_urlsafe(32))\"", flush=True)
        print("!" * 70 + "\n", flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    announce_missing_migrations()
    announce_unusable_publisher_token()
    yield


app = FastAPI(title="The Commissioner's Desk", docs_url=None, redoc_url=None,
              lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


templates.env.globals["demo_mode"] = DEMO_MODE
# Templates build share links from this rather than request.base_url,
# which behind a proxy reports http:// and the internal hostname.
templates.env.globals["public_base"] = public_base_url
templates.env.globals["theme_choices"] = themes.choices


#: `|title` turns "espn" into "Espn", which looks like a typo to anybody who
#: has ever used the site it is naming.
PROVIDER_NAMES = {"espn": "ESPN", "sleeper": "Sleeper", "yahoo": "Yahoo"}


def provider_name(provider: str) -> str:
    key = (provider or "").strip().lower()
    return PROVIDER_NAMES.get(key, key.title() or "Your platform")


templates.env.filters["provider_name"] = provider_name

# What the paid plan includes, and whether this deployment can sell it. Both
# are globals so that no route has to remember to pass them and no template
# has to restate the price.
templates.env.globals["lock_reasons"] = plans.LOCK_REASONS
templates.env.globals["price_text"] = plans.PRICE_TEXT
templates.env.globals["season_price_text"] = plans.SEASON_PRICE_TEXT
templates.env.globals["season_pass_enabled"] = plans.season_pass_enabled
# A callable, not a value: the environment variables are read when it is
# called, so a deployment that configures Stripe does not need a restart to
# start showing the buttons, and a test can set them per-case.
templates.env.globals["billing_enabled"] = billing.configured
# Same reasoning: a callable, so a deployment that adds Google
# credentials shows the button without a code change.
templates.env.globals["google_enabled"] = oauth.configured


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

#: How many times a single week's paper may be REGENERATED after the first one.
#: Different in kind from the limits above: those are spend ceilings claimed
#: atomically in the database, this is a courtesy allowance that exists to be
#: SHOWN. A commissioner who wants a different lead story should be able to get
#: one a couple of times without being made to feel like they are getting away
#: with something — and should be able to see how many they have left before
#: they press the button, not after.
#:
#: The number now depends on the plan, and plans.py is where it lives. This
#: name is kept as the free-tier value because it is what the fallback paths
#: and the older tests mean by "the allowance".
REGENERATIONS_PER_WEEK = plans.regenerations_per_week(plans.PLANS[plans.FREE])


def regenerations_allowed(user: dict | None) -> int:
    """This account's regenerations per week."""
    return plans.regenerations_per_week(plans.plan_for(user))


def regenerations_used(paper: dict | None) -> int:
    """How many of the weekly allowance this paper has spent.

    generation_count counts renders, and the first render is the paper coming
    into existence rather than a regeneration — so the allowance is spent from
    the second one on. A row written before migration 011 has no count and is
    treated as having used none, which errs toward the commissioner.
    """
    if not paper:
        return 0
    return max(0, (paper.get("generation_count") or 1) - 1)


def regenerations_left(paper: dict | None, user: dict | None = None) -> int:
    return max(0, regenerations_allowed(user) - regenerations_used(paper))
LEAGUE_CREATES_PER_HOUR = 5
SUBSCRIBES_PER_HOUR = 20
RECOVERIES_PER_HOUR = 5
UPLOADS_PER_HOUR = 30               # per IP

#: Opening Stripe's checkout page. Low, because a person subscribes roughly
#: once, and because this route CREATES A STRIPE CUSTOMER the first time each
#: account uses it.
#:
#: Stripe's own card-testing guidance names "limit the number of customers
#: that can be created by a single IP address" as a specific mitigation, and
#: an unbounded endpoint that mints customer records is the shape they are
#: describing. Checkout itself is well defended — Stripe applies rate limits,
#: CAPTCHAs and its own models to the hosted page — but that defends THEIR
#: page, not this route.
CHECKOUTS_PER_HOUR = 8              # per IP
CHECKOUTS_PER_USER_PER_DAY = 12
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
    # And what they're allowed, so a lock icon never has to guess. Defaults to
    # the viewer's own plan; a league page overrides it with the OWNER's,
    # because the person holding the manage link is not always the subscriber.
    context.setdefault("plan", plans.plan_for(context.get("user")))
    # Which themes this plan may pick, derived from the plan rather than
    # restated in the template — a hardcoded list in a form is exactly how a
    # picker ends up offering something the server then refuses.
    context.setdefault("allowed_themes", [
        t["key"] for t in themes.choices()
        if plans.allows_theme(context["plan"], t["key"])
    ])
    # The header's plan link is about the person looking, never the league's
    # owner, so it cannot come from `plan` above.
    context.setdefault("viewer_paid", plans.is_paid(context.get("user")))
    context.setdefault("offer_upgrade", _offer_upgrade(request, context.get("user")))
    return templates.TemplateResponse(request, template, context)


def _offer_upgrade(request: Request, user: dict | None) -> bool:
    """Show the upgrade pop-up on this page?

    Only straight after signing up (the ?welcome=1 the signup routes redirect
    to), only to somebody on the free plan, and only when checkout can actually
    take their money. It is decided from the VIEWER's own plan, never from a
    league owner's, because the button subscribes whoever presses it.

    The query parameter being forgeable does not matter: all it can do is show
    a free account an offer it could have found on its own account page.
    """
    if request.query_params.get("welcome") != "1" or not user:
        return False
    return plans.billing_enabled() and not plans.is_paid(user)


def league_owner(league: dict | None) -> dict | None:
    """The account a league belongs to, if it belongs to one.

    A league made from a manage link has no owner, which is not an error — it
    is how every league from before accounts works, and it means the free
    plan.
    """
    user_id = (league or {}).get("user_id")
    if not user_id:
        return None
    try:
        return db.user_by_id(user_id)
    except Exception:  # noqa: BLE001 — see current_user
        return None


def out_of_leagues(user: dict | None) -> bool:
    """Has this account used up the leagues its plan allows?

    Only asked when a NEW league is about to be created. Adopting a league
    that already exists — picking up something made before signing up, or
    recovering one whose manage link was lost — is never refused: the row is
    already there, the person is already its owner in every sense that
    matters, and refusing would strand them with a paper they cannot reach.
    """
    if not user:
        return False        # token-only creation, the accountless path
    try:
        count = len(db.leagues_for_user(user["id"]))
    except Exception:  # noqa: BLE001
        return False        # never block a sale on a database blip
    return not plans.allows_another_league(plans.plan_for(user), count)


def league_plan(league: dict | None) -> plans.Plan:
    """What this league's paper is allowed to do.

    The plan follows the OWNER, not whoever is looking. A commissioner who
    shares their manage link with a league-mate has shared the subscription's
    features along with it, which is intended: one member pays and the paper
    is better for everybody.
    """
    return plans.plan_for(league_owner(league))


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
# Only a reader link. A manage link (/l/...) pasted here by mistake would put
# edit-and-delete access to that league on the front page of the site.
if "/l/" in SAMPLE_PAPER_URL:
    print("!! SAMPLE_PAPER_URL looks like a MANAGE link (/l/...). Not showing "
          "it. Use the reader link, /p/...", flush=True)
    SAMPLE_PAPER_URL = ""


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

#: Platforms in the picker. `ready` is DERIVED from the provider registry
#: rather than restated here, because it was restated here and the two then
#: had to be kept in step by hand — the kind of duplication that shows up as a
#: platform that works but is still behind a waiting list.
_PLATFORM_COPY = [
    ("sleeper", "Sleeper",
     "Type your username and pick from your leagues."),
    ("espn", "ESPN",
     "Paste your league ID. ESPN has no way to look you up by name, and your "
     "league has to be viewable to the public \u2014 we show you how."),
    ("yahoo", "Yahoo",
     "Being built. Yahoo requires signing in with them first."),
]


def _platforms() -> list[dict]:
    ready = {p["name"] for p in available_providers() if p["implemented"]}
    return [{"key": key, "name": name, "ready": key in ready, "how": how}
            for key, name, how in _PLATFORM_COPY]


PLATFORMS = _platforms()


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

    # One league on the free plan. Checked here, where a NEW row is about to
    # be created, and not on the adopt-an-existing-league branches above.
    if out_of_leagues(user):
        return RedirectResponse(
            f"/connect/sleeper?error={quote(plans.LOCK_REASONS['leagues'])}",
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


@app.get("/connect/espn", response_class=HTMLResponse)
def connect_espn(request: Request, league_id: str = "", error: str = ""):
    _require_user(request)
    return _render(request, "connect_espn.html",
                   league_id=league_id, error=error)


@app.post("/connect/espn")
def connect_espn_add(request: Request, league_id: str = Form(...),
                     paper_name: str = Form("")):
    """Turn an ESPN league ID into a paper.

    No username lookup: ESPN publishes no directory, so the league ID out of
    the URL is the only handle anybody has. That makes the error messages the
    whole user experience of this page — a wrong ID and a private league are
    the two things that will happen, and they need different sentences.
    """
    user = _require_user(request)
    league_id = league_id.strip()

    if _rate_limited(f"create:{_client_ip(request)}", LEAGUE_CREATES_PER_HOUR):
        return RedirectResponse(
            "/connect/espn?error=That's+a+few+already.+Try+again+in+an+hour.",
            status_code=303)

    if not league_id.isdigit():
        return RedirectResponse(
            f"/connect/espn?league_id={quote(league_id[:40])}"
            f"&error=An+ESPN+league+ID+is+all+digits+-+it's+the+number+after+"
            f"leagueId=+in+your+league's+web+address.",
            status_code=303)

    adapter = get_provider("espn")
    try:
        info = adapter.get_league(league_id)
    except AuthRequired as exc:
        # The likeliest failure by far, and the one with a fix the person can
        # actually carry out. The page they land back on lists the six steps.
        return RedirectResponse(
            f"/connect/espn?league_id={quote(league_id)}&error={quote(str(exc))}",
            status_code=303)
    except (ProviderError, ValueError):
        return RedirectResponse(
            f"/connect/espn?league_id={quote(league_id)}"
            f"&error=Couldn't+find+that+league+on+ESPN.+Check+the+number+"
            f"against+your+league's+web+address.",
            status_code=303)

    existing = db.find_existing_league("espn", league_id, info.season)
    if existing:
        if existing.get("user_id") == user["id"]:
            return RedirectResponse(f"/l/{existing['admin_token']}",
                                    status_code=303)
        if not existing.get("user_id"):
            db.claim_league(existing["id"], user["id"])
            return RedirectResponse(
                f"/l/{existing['admin_token']}?notice=Picked+up+where+you+left+off.",
                status_code=303)
        return RedirectResponse(
            "/connect/espn?error=Another+account+already+has+a+paper+for+that+"
            "league.+If+that+is+you,+sign+in+with+that+email.",
            status_code=303)

    # One league on the free plan. Checked here, where a NEW row is about to
    # be created, and not on the adopt-an-existing-league branches above.
    if out_of_leagues(user):
        return RedirectResponse(
            f"/connect/espn?error={quote(plans.LOCK_REASONS['leagues'])}",
            status_code=303)

    league = db.create_league(
        provider="espn",
        platform_league_id=league_id,
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
        # A ready platform has its own route declared above this one, so
        # reaching here means the picker and the registry disagree. Send them
        # somewhere real rather than redirecting to this same path forever.
        return RedirectResponse("/connect", status_code=303)
    return _render(request, "connect_waitlist.html",
                   platform=known, sent=bool(sent))


@app.post("/connect/{platform}/notify")
def connect_notify(request: Request, platform: str):
    """Register interest. Which platform people ask for is the cheapest
    possible answer to what to build next.

    Accepts READY platforms too, which it did not used to. ESPN is why: it
    works for public leagues and does not work for private ones, and the
    button asking for private-league support sits on a page for a platform
    that is otherwise finished. "Ready" and "nothing left to ask for" turned
    out to be different things.
    """
    user = _require_user(request)
    known = next((p for p in PLATFORMS if p["key"] == platform), None)
    if not known:
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
    # ?welcome=1 is what raises the upgrade offer, once, on arrival.
    response = RedirectResponse("/connect?welcome=1", status_code=303)
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


# ---------------------------------------------------------------------------
# Sign in with Google
#
# The session model does not change: these two routes end by calling
# _set_session with a user id, exactly as a password login does. Everything
# downstream keys off users.id and never learns how somebody proved who they
# were.
# ---------------------------------------------------------------------------

@app.get("/auth/google")
def google_start(request: Request):
    """Send somebody to Google, remembering that we did.

    The state is signed with this app's own secret and stored in a cookie.
    Both have to come back and agree, which is what makes the callback ours:
    without it, the callback is a way to log a person into an account they do
    not own by sending them a link.
    """
    if not oauth.configured():
        raise HTTPException(status_code=404)
    if current_user(request):
        return RedirectResponse("/account", status_code=303)

    if _rate_limited(f"oauth:{_client_ip(request)}", LOGINS_PER_IP_PER_HOUR):
        return RedirectResponse(
            "/login?error=Too+many+attempts+from+here.+Try+again+later.",
            status_code=303)

    state = oauth.new_state()
    try:
        destination = oauth.consent_url(state, public_base_url())
    except oauth.OAuthError as exc:
        print(f"[oauth] cannot start sign-in: {exc}", flush=True)
        return RedirectResponse(
            "/login?error=Google+sign-in+isn't+available+right+now.",
            status_code=303)

    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(oauth.STATE_COOKIE, auth.sign_value(state),
                        **auth.state_cookie_kwargs())
    return response


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "",
                    error: str = ""):
    if not oauth.configured():
        raise HTTPException(status_code=404)

    def refuse(message: str):
        # The state cookie is spent either way. Leaving a used one around is
        # one more thing that can be replayed.
        response = RedirectResponse(f"/login?error={quote(message)}",
                                    status_code=303)
        response.delete_cookie(oauth.STATE_COOKIE, path="/")
        return response

    if error:
        # The ordinary case: somebody pressed cancel on the consent screen.
        return refuse("No problem — you can sign in with a password instead.")

    # THE STATE CHECK, which is the security of this endpoint.
    issued = auth.read_signed_value(request.cookies.get(oauth.STATE_COOKIE, ""))
    if not issued or not state or not hmac.compare_digest(issued, state):
        return refuse("That sign-in link didn't check out. Try again from "
                      "the top.")
    if not oauth.state_is_fresh(issued):
        return refuse("That sign-in took too long. Try again.")

    try:
        identity = oauth.identity_from_code(code, public_base_url())
    except oauth.OAuthError as exc:
        return refuse(str(exc))

    user, is_new = _account_for_google(identity)
    if user is None:
        return refuse(
            "There's already an account with that email address. Sign in "
            "with your password, and you can link Google afterwards.")

    db.update_user(user["id"], {"last_login_at": "now()"})

    # Somewhere useful: a brand-new account has no papers to look at.
    response = RedirectResponse("/connect?welcome=1" if is_new else "/account",
                                status_code=303)
    response.delete_cookie(oauth.STATE_COOKIE, path="/")
    return _set_session(response, user["id"])


def _account_for_google(identity) -> tuple[dict | None, bool]:
    """Which account this Google identity belongs to. (user, is_new)

    THREE CASES, and the middle one is the whole reason this function exists.

    1. We have seen this `sub` before. That is the same person; sign them in.
       Matched on sub rather than email because people change their address
       and Google keeps the same sub.

    2. There is a password account with this email address. Linking is only
       safe when GOOGLE HAS VERIFIED the address — otherwise an identity
       provider asserting an address it never checked becomes a way into
       somebody else's account. Google does verify, and says so in
       `email_verified`; that claim is checked, not assumed. If it is false,
       nothing is linked and the person is sent to the password form.

    3. Nobody has that address. New account, no password, ever.
    """
    existing = db.user_by_google_sub(identity.sub)
    if existing:
        return existing, False

    by_email = db.user_by_email(identity.email)
    if by_email:
        if not identity.email_verified:
            print(f"[oauth] refused to link an unverified address to "
                  f"{by_email['id']}", flush=True)
            return None, False
        db.link_google(by_email["id"], identity.sub, identity.email)
        return db.user_by_id(by_email["id"]), False

    created = db.create_google_user(identity.email, identity.sub,
                                    identity.name)
    if not created:
        # Lost a race with another signup on the same address.
        return None, False
    return created, True


@app.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, welcome: int = 0, notice: str = "",
            error: str = ""):
    user = _require_user(request)
    leagues = db.leagues_for_user(user["id"])
    for lg in leagues:
        lg["awards"] = db.get_awards(lg["id"])
        lg["people"] = db.get_managers(lg["id"])
    return _render(request, "account.html",
                   leagues=leagues, awards_ready=db.awards_table_ready(),
                   max_awards=MAX_CUSTOM_AWARDS,
                   welcome=bool(welcome), notice=notice, error=error)


# ---------------------------------------------------------------------------
# The league's own awards, managed from the account page
# ---------------------------------------------------------------------------

#: Per league. Enough for a league's running jokes; few enough that the
#: awards section is still a section and not the whole paper.
MAX_CUSTOM_AWARDS = 6


def _owned_league(user: dict, league_id: str) -> dict:
    lg = next((l for l in db.leagues_for_user(user["id"])
               if str(l["id"]) == str(league_id)), None)
    if not lg:
        raise HTTPException(status_code=404, detail="Not found.")
    return lg


def _award_fields(name, mode, criteria, winner, note) -> dict:
    mode = "auto" if mode == "auto" else "manual"
    return {
        "name": (name or "").strip()[:60],
        "mode": mode,
        "criteria": (criteria or "").strip()[:300] or None,
        "winner": (winner or "").strip()[:80] or None,
        "note": (note or "").strip()[:300] or None,
    }


@app.post("/account/awards/add")
def add_custom_award(request: Request, league_id: str = Form(...),
                     name: str = Form(""), mode: str = Form("manual"),
                     criteria: str = Form(""), winner: str = Form(""),
                     note: str = Form("")):
    user = _require_user(request)
    lg = _owned_league(user, league_id)
    fields = _award_fields(name, mode, criteria, winner, note)
    if not fields["name"]:
        return RedirectResponse("/account?error=Give+the+award+a+name.#awards",
                                status_code=303)
    if fields["mode"] == "auto" and not fields["criteria"]:
        return RedirectResponse("/account?error=Say+what+the+award+is+for,+"
                                "so+the+paper+can+pick+a+winner.#awards",
                                status_code=303)
    if len(db.get_awards(lg["id"])) >= MAX_CUSTOM_AWARDS:
        return RedirectResponse(f"/account?error=That's+{MAX_CUSTOM_AWARDS}+"
                                f"already+-+remove+one+first.#awards", status_code=303)
    db.add_award(lg["id"], fields)
    return RedirectResponse("/account?notice=Award+added.#awards", status_code=303)


@app.post("/account/awards/{award_id}/update")
def update_custom_award(request: Request, award_id: str,
                        league_id: str = Form(...), name: str = Form(""),
                        mode: str = Form("manual"), criteria: str = Form(""),
                        winner: str = Form(""), note: str = Form("")):
    user = _require_user(request)
    lg = _owned_league(user, league_id)
    fields = _award_fields(name, mode, criteria, winner, note)
    if not fields["name"]:
        return RedirectResponse("/account?error=Give+the+award+a+name.#awards",
                                status_code=303)
    db.update_award(lg["id"], award_id, fields)
    return RedirectResponse("/account?notice=Saved.#awards", status_code=303)


@app.post("/account/awards/{award_id}/delete")
def delete_custom_award(request: Request, award_id: str,
                        league_id: str = Form(...)):
    user = _require_user(request)
    lg = _owned_league(user, league_id)
    db.delete_award(lg["id"], award_id)
    return RedirectResponse("/account?notice=Award+removed.#awards", status_code=303)


# ---------------------------------------------------------------------------
# Money
#
# Four routes. Three of them are a redirect to Stripe or back; the fourth is
# the only thing in this application that can put somebody on the paid plan,
# and it will not act on a request it cannot prove came from Stripe.
# ---------------------------------------------------------------------------

@app.post("/billing/checkout")
def billing_checkout(request: Request, term: str = Form(plans.MONTHLY)):
    user = _require_user(request)

    if term not in plans.TERMS or (
            term == plans.SEASON and not plans.season_pass_enabled()):
        return RedirectResponse(
            "/account?error=That+plan+isn't+available.+Nothing+was+charged.",
            status_code=303)

    if plans.is_paid(user):
        return RedirectResponse("/account?notice=You're+already+subscribed.",
                                status_code=303)

    # Two keys, the same reasoning as generation: the IP is a claim, the
    # account id is a fact. Somebody who can present any IP they like still
    # cannot spin up customers faster than one account is allowed to.
    if (_rate_limited(f"checkout:{_client_ip(request)}", CHECKOUTS_PER_HOUR)
            or _rate_limited(f"checkout-user:{user['id']}",
                             CHECKOUTS_PER_USER_PER_DAY, window=DAY)):
        return RedirectResponse(
            "/account?error=That's+a+few+attempts+already.+Give+it+a+few+"
            "minutes,+and+nothing+has+been+charged.", status_code=303)

    try:
        url = billing.checkout_url(db, user, public_base_url(), term=term)
    except billing.BillingError as exc:
        print(f"[billing] checkout failed for {user['id']}: {exc}", flush=True)
        return RedirectResponse(
            "/account?error=Couldn't+open+the+payment+page.+Nothing+was+"
            "charged.+Try+again+in+a+minute.", status_code=303)

    # 303 so the browser follows with GET. Stripe's session URL is single-use
    # and belongs to this person, so it is never cached or shared.
    return RedirectResponse(url, status_code=303)


@app.post("/billing/portal")
def billing_portal(request: Request):
    """Stripe's own page, for changing a card or cancelling."""
    user = _require_user(request)
    try:
        url = billing.portal_url(db, user, public_base_url())
    except billing.BillingError as exc:
        print(f"[billing] portal failed for {user['id']}: {exc}", flush=True)
        return RedirectResponse(
            "/account?error=Couldn't+open+the+billing+page.+Try+again+in+a+"
            "minute.", status_code=303)
    return RedirectResponse(url, status_code=303)


@app.get("/billing/done", response_class=HTMLResponse)
def billing_done(request: Request, ok: int = 0):
    """Where Stripe sends the browser back to.

    GRANTS NOTHING. Landing here proves somebody visited a URL, and anybody
    can type a URL. The plan is set by the webhook and only by the webhook;
    this page re-reads the account and reports what it finds.

    Which is why it can honestly say "a moment" — if the webhook has not
    landed yet, this is a page that says so rather than a lie that says paid.
    """
    user = _require_user(request)
    return _render(
        request, "message.html",
        heading="Thanks" if plans.is_paid(user) else "Almost there",
        body=("You're on the paid plan. Every league on this account has the "
              "lot." if plans.is_paid(user) else
              "Stripe has your subscription and we're waiting to hear back "
              "from them — it usually takes a few seconds. Refresh your "
              "account page in a moment."),
        link_url="/account", link_label="Your papers")


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """The only door to the paid plan.

    Unsigned, wrongly signed, or replayed past Stripe's tolerance — all
    rejected, and rejected BEFORE the body is parsed as anything meaningful.
    The signature covers the raw bytes, so the raw bytes are what gets
    checked; re-serialising parsed JSON would change them and fail.

    With no STRIPE_WEBHOOK_SECRET configured this route 404s, the same posture
    as the publisher page: an endpoint that cannot verify anything should not
    look like an endpoint that might.
    """
    if not billing.webhook_secret():
        raise HTTPException(status_code=404)

    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    try:
        event = billing.verify(payload, signature)
    except billing.BillingError as exc:
        # 400 tells Stripe not to retry. A bad signature will never become a
        # good one, and retrying it is noise in both dashboards.
        print(f"[billing] webhook rejected: {exc}", flush=True)
        raise HTTPException(status_code=400, detail="bad signature")

    try:
        result = billing.handle_event(db, event)
    except Exception as exc:  # noqa: BLE001
        # 500 so Stripe RETRIES. This is the one place in the app where
        # swallowing an error would quietly lose somebody's subscription, so
        # it is deliberately the one place that fails loudly.
        print(f"[billing] handling {getattr(event, 'type', '?')} failed: "
              f"{type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(status_code=500, detail="retry please")

    print(f"[billing] {getattr(event, 'type', '?')}: {result}", flush=True)
    return {"ok": True}


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
        # An account that has no password cannot have one reset. Mailing a
        # reset link to somebody who signed up with Google sends them to a
        # form for a credential they have never had, and the page they land
        # on cannot explain why. They are told what they actually did
        # instead — and the response to THIS page stays identical either way,
        # so it still reveals nothing about which addresses have accounts.
        if user and not user.get("password_hash"):
            emailer.send_google_account_reminder(address)
        elif user:
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

    # How many regenerations each week of THIS season has left, so the number
    # is on the page before the button is pressed rather than in the error
    # message afterwards.
    owner = league_owner(league)
    regenerations = {
        p["week"]: regenerations_left(p, owner)
        for p in papers if p.get("season") == league["season"]
    }

    return _render(
        request, "league.html",
        league=league,
        paper_name=paper_name_for(league),
        weeks=weeks,
        earlier_season=earlier_season,
        lore=db.get_lore(league["id"]),
        managers=managers_for_page(db, league, weeks),
        papers=papers,
        total_reads=sum(int(p.get("view_count") or 0) for p in papers),
        regenerations=regenerations,
        regenerations_per_week=regenerations_allowed(owner),
        plan=plans.plan_for(owner),
        owner=owner,
        subscriber_count=db.subscriber_count(league["id"]),
        # The "bookmark this page" warning is only true for a league with no
        # account behind it: an account holder can always get back in.
        is_new=bool(new) and not league.get("user_id"),
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


@app.post("/l/{token}/managers")
def save_managers(token: str,
                  handle: list[str] = Form([]),
                  display_name: list[str] = Form([]),
                  notes: list[str] = Form([])):
    """One save for the whole page of people.

    The three lists are positional: browsers submit fields in document order,
    and every box submits even when empty, so row N is
    (handle[N], display_name[N], notes[N]). If those lengths ever disagree the
    pairing is meaningless and writing it would put one person's lore under
    another person's name, so nothing is written at all.

    Only handles that already have a row can be written — save_manager filters
    on league_id and handle and updates, so a forged handle updates nothing.
    """
    league = _require_league(token)

    if not _save_managers(league, handle, display_name, notes):
        return RedirectResponse(
            f"/l/{token}?error=Something+went+wrong+saving+that.+"
            f"Try+again.", status_code=303)

    return RedirectResponse(f"/l/{token}?notice=Saved.", status_code=303)


def _save_managers(league: dict, handle: list, display_name: list,
                   notes: list) -> bool:
    """Write the per-person boxes. False, and nothing written, if the three
    positional lists disagree in length — see save_managers."""
    if not (len(handle) == len(display_name) == len(notes)):
        return False
    for who, name, note in zip(handle, display_name, notes):
        who = (who or "").strip()
        if who:
            db.save_manager(league["id"], who, name, note)
    return True


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

    # The plan decides, not the form. A greyed-out radio button is a courtesy
    # to somebody reading the page; this is the rule, and it holds against a
    # curl request with any field in it.
    plan = league_plan(league)

    db.update_league(league["id"], {
        "paper_name": paper_name.strip() or None,
        "commissioner_name": commissioner.strip(),
        "auto_send": auto_send == "on" and plan.auto_send,
        "format": format if format in ("redraft", "keeper", "dynasty") else "redraft",
        "tone": tone if tone in ("friendly", "standard", "brutal") else "standard",
        "theme": plans.resolve_theme(plan, themes.resolve(theme)),
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


@app.post("/l/{token}/delete")
def delete_league(request: Request, token: str, confirm: str = Form("")):
    """Remove a league and everything it ever printed.

    Gated on typing the league's name rather than on a browser confirm(). The
    manage link gets pasted into group chats and left open in tabs; a single
    mis-click should not be able to take down a season's archive, and a dialog
    is one mis-click. Typing the name is the smallest thing that cannot happen
    by accident.

    The papers are world-readable at URLs people have already shared, so this
    genuinely has to remove them rather than hide the league — a delete that
    leaves the editions up is not a delete.
    """
    league = _require_league(token)

    wanted = (league.get("league_name") or "").strip().lower()
    if confirm.strip().lower() != wanted:
        return RedirectResponse(
            f"/l/{token}?error=That+didn't+match+the+league+name,+so+"
            f"nothing+was+deleted.", status_code=303)

    db.delete_league(league["id"])

    return _render(request, "message.html",
                   heading="It's gone",
                   body=f"{paper_name_for(league)} and every edition of it have "
                        f"been deleted. The manage link no longer works.",
                   link_url="/", link_label="Start another one")


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

def _playable_weeks(league: dict) -> list[int]:
    """Weeks the platform has results for. Empty on any failure — setup
    still works without them, it just can't offer to generate yet."""
    try:
        return list(get_provider(league["provider"]).available_weeks(
            league["platform_league_id"], league["season"]) or [])
    except Exception as exc:  # noqa: BLE001
        print(f"[setup] couldn't list weeks: {type(exc).__name__}: {exc}",
              flush=True)
        return []


@app.get("/l/{token}/setup", response_class=HTMLResponse)
def setup_form(request: Request, token: str):
    """Three steps, one at a time: the people, the league, the paper — and
    the last button writes the first paper."""
    league = _require_league(token)
    weeks = _playable_weeks(league)
    return _render(request, "setup.html",
                   league=league, paper_name=paper_name_for(league),
                   plan=league_plan(league),
                   managers=managers_for_page(db, league, weeks),
                   latest_week=max(weeks) if weeks else None)


@app.post("/l/{token}/setup")
def save_setup(
    request: Request,
    token: str,
    format: str = Form("redraft"),
    tone: str = Form("standard"),
    theme: str = Form("tabloid"),
    founded_year: str = Form(""),
    stakes: str = Form(""),
    punishment: str = Form(""),
    lore: str = Form(""),
    handle: list[str] = Form([]),
    display_name: list[str] = Form([]),
    notes: list[str] = Form([]),
    then: str = Form(""),
    letter: str = Form(""),
    week: str = Form(""),
):
    league = _require_league(token)

    # The leaguemates step. A mismatch writes nothing and carries on: losing
    # the people boxes is not a reason to lose the rest of the setup.
    if handle:
        _save_managers(league, handle, display_name, notes)

    year = None
    if founded_year.strip().isdigit():
        candidate = int(founded_year.strip())
        if 1980 <= candidate <= 2100:
            year = candidate

    db.update_league(league["id"], {
        "format": format if format in ("redraft", "keeper", "dynasty") else "redraft",
        "tone": tone if tone in ("friendly", "standard", "brutal") else "standard",
        "theme": plans.resolve_theme(league_plan(league), themes.resolve(theme)),
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

    # "Generate my paper" — straight on to the first paper, through the same
    # door as the manage page's Generate button.
    if then == "generate" and week.strip().isdigit():
        fresh = _require_league(token)
        return _generate_response(request, fresh, int(week.strip()),
                                  letter=letter.strip()[:MAX_LETTER_CHARS])

    return RedirectResponse(f"/l/{token}?new=1", status_code=303)


@app.post("/l/{token}/skip-setup")
def skip_setup(request: Request, token: str, week: str = Form("")):
    """Skip the questions — and still get the paper straight away.

    Skipping means "don't make me fill this in", not "don't make me a
    paper". When a week has been played the first paper starts at once,
    through the same door as every other generation.
    """
    league = _require_league(token)
    db.update_league(league["id"], {"setup_complete": True})
    if week.strip().isdigit():
        return _generate_response(request, _require_league(token), int(week.strip()))
    return RedirectResponse(f"/l/{token}?new=1", status_code=303)


# ---------------------------------------------------------------------------
# Generation
#
# A plain `def`, not `async def`: FastAPI runs sync handlers in a threadpool,
# so this ~30-second call doesn't block the event loop and freeze everything.
# ---------------------------------------------------------------------------

@app.post("/l/{token}/generate")
def generate(request: Request, token: str, week: int = Form(...),
             confirm_overwrite: str = Form(""), letter: str = Form("")):
    league = _require_league(token)
    return _generate_response(request, league, week, confirm_overwrite,
                              letter=letter.strip()[:MAX_LETTER_CHARS])


def _generate_response(request: Request, league: dict, week: int,
                       confirm_overwrite: str = "", letter: str = ""):
    """Every check, the generation itself, and where to send the browser.

    Shared by the Generate button on the manage page and the last step of
    setup, so a new league's first paper goes through exactly the same rate
    limits, spend ceilings and plan rules as every other paper.
    """
    token = league["admin_token"]
    owner = league_owner(league)

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

    # Check the database can hold a paper BEFORE paying to write one.
    #
    # Migration 006 was missing through a deploy. The shape of that failure:
    # every Claude call succeeded, fourteen seconds passed, the paper was
    # written — and the insert then raised "column newspapers.ai_cache_original
    # does not exist" and threw all of it away. The user got a crash page and
    # the tokens were spent on nothing.
    #
    # Free after the first success — schema_blockers latches once the schema is
    # intact — and self-healing, since running the migration clears it on the
    # next request with no redeploy.
    blockers = db.schema_blockers()
    if blockers:
        print(f"[app] refusing to generate — unapplied migrations: "
              f"{', '.join(blockers)}", flush=True)
        return RedirectResponse(
            f"/l/{token}?error=The+site+isn't+finished+setting+up+its+"
            f"database,+so+a+new+paper+can't+be+saved+yet.+Nothing+was+"
            f"charged.+The+owner+has+been+told.",
            status_code=303)

    existing = db.get_paper(league["id"], league["season"], week)

    # The weekly regeneration allowance. Checked before anything is spent, and
    # only for a paper that already exists — the first generation of a week is
    # not a regeneration.
    allowed = regenerations_allowed(owner)
    if existing and regenerations_used(existing) >= allowed:
        # The upsell is only mentioned to somebody it would actually help.
        # Telling a paying customer who has used all three that they could pay
        # for more is the single most irritating sentence a product can print.
        more = ("" if plans.is_paid(owner)
                else "+" + quote(plans.LOCK_REASONS["generations"]))
        return RedirectResponse(
            f"/l/{token}?error=You've+used+all+"
            f"{allowed}+regenerations+for+week+{week}.+"
            f"You+can+still+edit+this+one+by+hand+-+open+it+and+change+"
            f"anything+you+like.{more}",
            status_code=303)

    # Regenerating throws away hand-edited prose. Ask first rather than
    # silently deleting someone's work.
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
        generate_and_store(db, league, week, letter=letter)
    except ProviderError as exc:
        return RedirectResponse(f"/l/{token}?error={exc}", status_code=303)
    except WriterError as exc:
        # Nothing was written at all. Say so in one sentence a person can act
        # on, rather than letting it fall through to a 500 — "something broke
        # on our end" is true and useless, and the fault is almost always
        # temporary or a missing key rather than anything the league did.
        print(f"[app] generation failed for league {league['id']}: {exc}",
              flush=True)
        return RedirectResponse(
            f"/l/{token}?error=The+writers+couldn't+be+reached+just+now.+"
            f"Nothing+was+charged+and+nothing+was+lost+-+try+again+in+a+"
            f"few+minutes.",
            status_code=303)
    except Exception:  # noqa: BLE001
        # Backstop for the save side: storage, the database, the renderer.
        # Broad on purpose — this route is the one moment the product
        # delivers, and a stack trace in the browser is the worst possible
        # ending to it. The traceback still goes to the log in full, which is
        # where it was useful anyway.
        import traceback
        print(f"[app] generation failed AFTER writing, league "
              f"{league['id']} week {week} — the Claude calls were paid for "
              f"and the result could not be saved:", flush=True)
        traceback.print_exc()
        return RedirectResponse(
            f"/l/{token}?error=The+paper+was+written+but+couldn't+be+saved.+"
            f"This+one's+on+us+-+it's+been+logged.+Try+again+in+a+few+minutes.",
            status_code=303)
    finally:
        _GENERATION_SLOTS.release()

    # Show them the paper. Waiting thirty seconds and being handed a URL to
    # click is a bad payoff for the one moment the product actually delivers.
    return RedirectResponse(f"/l/{token}/published/{week}", status_code=303)


@app.get("/l/{token}/letter/{week}")
def letter_for_week(token: str, week: int):
    """The commissioner's letter already in a week's paper, as plain text.

    For the pop-up before a regeneration, so a redo doesn't make him type it
    again. Read back from the paper as it stands now, so a letter he has
    since fixed in the editor comes back fixed.
    """
    league = _require_league(token)
    paper = db.get_paper(league["id"], league["season"], week) or {}
    cache = paper.get("ai_cache") or {}
    text = ""
    if cache.get("lead_by_commissioner"):
        text = letter_from_html(cache.get("lead_story") or "")
    return JSONResponse({"letter": text})


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
    if "pull_quote" in edits:
        # Plain text, not HTML: it renders inside quotation marks in display
        # type, so markup in it has nowhere sensible to go.
        edited["pull_quote"] = clean_text(edits["pull_quote"], 300)
    for key in ("lead_story", "fraud_watch"):
        if key in edits:
            edited[key] = clean_html(edits[key])

    # The extras. Plain text: they render escaped, inside fixed markup.
    if "letter_body" in edits or "letter_reply" in edits:
        letter = dict(ai.get("letter") or {})
        if "letter_body" in edits:
            letter["body"] = clean_text(edits["letter_body"], 1200)
        if "letter_reply" in edits:
            letter["reply"] = clean_text(edits["letter_reply"], 300)
        edited["letter"] = letter
    obit_edits = {k: v for k, v in edits.items() if k.startswith("obituary_body_")}
    if obit_edits:
        obits = [dict(o) for o in (ai.get("obituaries") or [])]
        for key, raw in obit_edits.items():
            idx = key[len("obituary_body_"):]
            if idx.isdigit() and int(idx) < len(obits):
                obits[int(idx)]["body"] = clean_text(raw, 800)
        edited["obituaries"] = obits
    line_edits = {k: v for k, v in edits.items() if k.startswith("line_pick_")}
    if line_edits:
        lines = [dict(l) for l in (ai.get("lines") or [])]
        for key, raw in line_edits.items():
            idx = key[len("line_pick_"):]
            if idx.isdigit() and int(idx) < len(lines):
                lines[int(idx)]["pick"] = clean_text(raw, 200)
        edited["lines"] = lines

    matchups = [dict(m) for m in (ai.get("matchup_content") or [])]
    awards = [dict(a) for a in (ai.get("awards") or [])]
    rankings = dict(ai.get("power_rankings_comments") or {})
    classifieds = [dict(c) for c in (ai.get("classifieds") or [])]

    for key, raw in edits.items():
        if key.startswith("classified_"):
            # classified_<field>_<index>
            rest = key[len("classified_"):]
            field, _, idx = rest.rpartition("_")
            if not idx.isdigit() or field not in ("heading", "body", "contact"):
                continue
            i = int(idx)
            if 0 <= i < len(classifieds):
                classifieds[i][field] = clean_text(
                    raw, 80 if field == "heading" else 220)

        elif key.startswith("matchup_headline_") or key.startswith("matchup_body_"):
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
    if classifieds:
        edited["classifieds"] = classifieds

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

    if not league_plan(league).photo_uploads:
        # 402 rather than 403: this is not "you may not", it is "not on this
        # plan", and the editor's JavaScript shows the message verbatim.
        return JSONResponse({"error": plans.LOCK_REASONS["photo_uploads"]},
                            status_code=402)

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


# ---------------------------------------------------------------------------
# THE PUBLISHER
#
# Every other authenticated surface in this app is a league: you hold a
# league's admin token, and it lets you do things to that league. This is the
# first surface that belongs to nobody's league. It writes one page that
# appears in every paper, so a league admin token must not open it — those are
# free to mint, and anyone can have one in thirty seconds.
#
# The key is PUBLISHER_TOKEN in the environment. Two properties matter:
#
#   UNSET MEANS OFF, NOT OPEN. If the variable is missing the routes 404. The
#   failure that this is guarding against is a deploy where the variable did
#   not get set and the page quietly let the first URL through.
#
#   COMPARED IN CONSTANT TIME. A plain == leaks the length of the shared
#   prefix through timing, which over enough requests recovers the token one
#   character at a time. compare_digest is the same comparison without the
#   early return.
# ---------------------------------------------------------------------------

#: Upload ceilings for the publisher. Far lower than a league's, because this
#: is one person working through five images on a Sunday night, and anything
#: much above that is a sign something has gone wrong rather than a sign of
#: heavy use.
PUBLISHER_UPLOADS_PER_HOUR = 40

#: What the page is about. A whole page of anything is a lot of scrolling, and
#: five is what John asked for; the cap is here so a mis-drop of a folder full
#: of images doesn't silently become a forty-ad page in everybody's paper.
MAX_PUBLISHER_ADS = 8


def _require_publisher(token: str) -> str:
    """Prove this is the publisher, or 404."""
    expected = os.getenv("PUBLISHER_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=404, detail="Not found.")
    if not hmac.compare_digest(str(token), expected):
        raise HTTPException(status_code=404, detail="Not found.")
    return token


def _publisher_week(week, season) -> tuple[int, int]:
    """Which week is being edited. Defaults to the one being played.

    Bounded rather than trusted: these arrive in a URL, and an unbounded week
    number is a key that writes rows nothing will ever read.
    """
    import nfl_week
    try:
        week_number = int(week) if week is not None else nfl_week.current_week()
    except (TypeError, ValueError):
        week_number = nfl_week.current_week()
    try:
        season_number = int(season) if season is not None else nfl_week.current_season()
    except (TypeError, ValueError):
        season_number = nfl_week.current_season()
    return max(1, min(22, week_number)), max(2000, min(2100, season_number))


@app.get("/publisher/{token}", response_class=HTMLResponse)
def publisher_page(request: Request, token: str, week: int = None,
                   season: int = None):
    """Where the week's classifieds page gets made."""
    _require_publisher(token)
    week_number, season_number = _publisher_week(week, season)

    import nfl_week
    from ads import estimate_page_fill

    week_ads = db.publisher_ads(season_number, week_number)
    return _render(
        request, "publisher.html",
        token=token,
        week=week_number,
        season=season_number,
        ads=week_ads,
        # How much of a printed sheet this mix will take. Shown because the
        # answer depends entirely on which images were uploaded — five
        # landscape memes half-fill a page, five phone screenshots are nearly
        # two — and "slightly over" is not a slightly cramped page, it is the
        # bottom ad of each column landing alone on a second sheet.
        fill=round(100 * estimate_page_fill(week_ads)),
        max_ads=MAX_PUBLISHER_ADS,
        # "No ads this week" and "the table isn't there" look identical on
        # this page and mean completely different things — one needs five
        # images, the other needs a migration.
        schema_ready=db.publisher_ads_table_ready(),
        this_week=nfl_week.current_week(),
        weeks=list(range(1, 19)),
    )


@app.post("/publisher/{token}/upload")
async def publisher_upload(request: Request, token: str,
                           photo: UploadFile = File(...),
                           week: int = Form(...), season: int = Form(...),
                           caption: str = Form(""), link_url: str = Form("")):
    """Add one ad to a week.

    Nothing the caller says about the file is trusted: the type comes from the
    bytes and the stored extension and content-type are both derived from it.
    Same rule as the league photo endpoint — see web/images.py.
    """
    _require_publisher(token)
    week_number, season_number = _publisher_week(week, season)

    if _rate_limited(f"pub-upload:{_client_ip(request)}",
                     PUBLISHER_UPLOADS_PER_HOUR):
        return JSONResponse({"error": "too many uploads this hour"},
                            status_code=429)

    if len(db.publisher_ads(season_number, week_number)) >= MAX_PUBLISHER_ADS:
        return JSONResponse(
            {"error": f"that's {MAX_PUBLISHER_ADS} ads already — "
                      f"delete one to add another"}, status_code=400)

    data = await photo.read()
    if len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse({"error": "over 8MB"}, status_code=413)

    kind = images.sniff(data)
    if kind is None:
        return JSONResponse({"error": images.describe_rejection(data)},
                            status_code=400)

    # Read before storing. A dimensionless ad still renders — the page falls
    # back to a 4:3 box — so this never blocks an upload.
    size = images.dimensions(data)
    width, height = size if size else (None, None)

    path, url = await run_in_threadpool(
        db.upload_publisher_image, f"ad.{kind.extension}", data, kind.mime,
        season_number, week_number)
    row = await run_in_threadpool(
        db.add_publisher_ad, season_number, week_number, url, path,
        width, height, caption, link_url)

    return JSONResponse({"ok": True, "ad": {
        "id": row.get("id"), "image_url": url,
        "width": width, "height": height,
        "caption": row.get("caption") or "",
    }})


@app.post("/publisher/{token}/delete")
async def publisher_delete(token: str, ad_id: str = Form(...)):
    _require_publisher(token)
    await run_in_threadpool(db.delete_publisher_ad, ad_id)
    return JSONResponse({"ok": True})


@app.post("/publisher/{token}/reorder")
async def publisher_reorder(request: Request, token: str):
    _require_publisher(token)
    body = await request.json()
    week_number, season_number = _publisher_week(body.get("week"),
                                                 body.get("season"))
    ids = [str(i) for i in (body.get("ids") or [])][:MAX_PUBLISHER_ADS]
    await run_in_threadpool(db.reorder_publisher_ads, season_number,
                            week_number, ids)
    return JSONResponse({"ok": True})


@app.get("/publisher/{token}/preview", response_class=HTMLResponse)
def publisher_preview(token: str, week: int = None, season: int = None):
    """The page exactly as it will appear in a paper.

    Rendered through the same function the paper uses, with the paper's own
    stylesheets — including the print rules, so the preview's own Print
    command shows what a reader's PDF will do. A preview built any other way
    is a preview of the preview.
    """
    _require_publisher(token)
    week_number, season_number = _publisher_week(week, season)
    ads_rows = db.publisher_ads(season_number, week_number)

    from ads import PUBLISHER_PAGE_CSS, render_publisher_page
    body = render_publisher_page(ads_rows)
    if not body:
        body = ('<p style="text-align:center;font-family:Georgia,serif;'
                'color:#6b6050;padding:60px 20px;">Nothing uploaded for week '
                f'{week_number} yet. The paper simply won\'t have this page.</p>')

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Classifieds preview &mdash; week {week_number}</title>
<style>
  body {{ margin: 0; background: #e8e2d5; font-family: Georgia, serif; }}
  .sheet {{ max-width: 860px; margin: 24px auto; background: #faf7f0;
            padding: 28px 36px 36px; box-shadow: 0 2px 18px rgba(0,0,0,.18); }}
  .section-title-full {{ font-size: 13px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 2px; border-top: 3px solid #111;
      border-bottom: 1px solid #111; padding: 5px 0; margin-bottom: 20px;
      text-align: center; font-family: "Barlow Condensed", Georgia, serif; }}
  {PUBLISHER_PAGE_CSS}
  {printing.BASE_PRINT_CSS}
  @media print {{ .sheet {{ box-shadow: none; margin: 0; max-width: none;
                            padding: 0; }} body {{ background: #fff; }} }}
</style></head>
<body><div class="sheet">{body}</div></body></html>""")


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
                   sections=legal.privacy_sections(),
                   contact_email=legal.contact_email(),
                   mailing_address=legal.mailing_address())


@app.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return _render(request, "legal.html",
                   heading="Terms",
                   updated=legal.LAST_UPDATED,
                   sections=legal.terms_sections(),
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

    # Presence only, never the value. A service that can serve every existing
    # paper but cannot write a new one looks completely healthy from outside,
    # and this is the one bit that distinguishes those two states.
    if not os.getenv("ANTHROPIC_API_KEY"):
        return JSONResponse({
            "ok": True,
            "warning": "ANTHROPIC_API_KEY is not set",
            "effect": "existing papers serve fine; generating a new one fails",
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
    # From `exc`, not print_exc(): Starlette calls this handler AFTER the
    # except block has finished, so print_exc() finds no exception in flight
    # and prints "NoneType: None" — which is exactly what the logs showed.
    print(f"UNHANDLED ERROR on {request.url.path}: "
          f"{type(exc).__name__}: {exc}", flush=True)
    traceback.print_exception(type(exc), exc, exc.__traceback__)

    return templates.TemplateResponse(
        request, "error.html",
        {"heading": "That didn't work.",
         "message": "Something broke on our end, not on yours. It's been "
                    "logged — try again in a minute."},
        status_code=500,
    )
