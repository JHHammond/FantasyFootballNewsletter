"""
Supabase access layer.

There is no user authentication anywhere in this app, which changes the
security model completely:

  Before — the browser held an anon key and row-level security decided what
           each logged-in user could see.

  Now    — the browser holds nothing. This server holds a service-role key and
           is the only thing that ever talks to the database. RLS is enabled
           with no policies at all, so even if the anon key leaked it grants
           access to precisely nothing.

That means the service key must never reach a template or a client-side script.
It lives in the environment and stays on the server.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from supabase import Client, create_client

BUCKET = "newspapers"

_client: Optional[Client] = None


def describe_key(key: str) -> tuple[bool, str]:
    """Can this key bypass RLS? Returns (yes_it_can, what_it_actually_is).

    Worth checking, because the failure mode of getting this wrong is silent
    rather than loud. Every table has RLS on with no policies, so a publishable
    key doesn't get rejected — it gets an empty result. Reads return nothing,
    writes fail deep inside a request, and the health check passes throughout
    because "no rows" is a perfectly successful query.

    Recognising the key by shape costs one string comparison and turns that
    into an error at startup with the reason attached.
    """
    key = (key or "").strip()

    # Current formats are prefixed and unambiguous.
    if key.startswith("sb_secret_"):
        return True, "secret key"
    if key.startswith("sb_publishable_"):
        return False, "publishable key (the browser-safe one)"

    # Legacy JWTs carry the role in their payload.
    parts = key.split(".")
    if len(parts) == 3:
        import base64
        import json as _json
        try:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = _json.loads(base64.urlsafe_b64decode(padded))
            role = str(claims.get("role", "")).lower()
        except Exception:  # noqa: BLE001 — an unreadable JWT is just unknown
            return True, "unrecognised JWT"
        if role == "service_role":
            return True, "service_role key"
        if role:
            return False, f"{role} key"
        return True, "JWT with no role claim"

    # Anything else is a format we don't know. Don't block on it — a future
    # key format shouldn't take the site down — but don't claim it's fine.
    return True, "unrecognised format"


def client() -> Client:
    """The one Supabase client. Created lazily so imports don't need env vars."""
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        # Service role: this server is trusted, browsers never see this key.
        key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set. "
                "See SETUP.md."
            )

        privileged, what = describe_key(key)
        if not privileged:
            raise RuntimeError(
                f"SUPABASE_SERVICE_KEY is a {what}. This app needs the key "
                f"that bypasses Row Level Security, or every query returns "
                f"nothing and every write is refused. Supabase dashboard -> "
                f"Settings -> API Keys -> copy the SECRET key (sb_secret_...), "
                f"not the publishable one."
            )

        _client = _create(url, key)
    return _client


# ---------------------------------------------------------------------------
# The connection underneath
#
# THE BUG THIS FIXES: "httpcore.RemoteProtocolError: Server disconnected",
# raised from the Supabase client, turning a lore save into the "That didn't
# work" page. supabase-py opens ONE long-lived HTTP/2 connection and sends
# everything down it. Supabase's edge closes connections that sit idle, and
# the client doesn't notice until it tries to use one — so the first request
# after a quiet spell failed. Saving the lore sends one update per person, so
# that page was the one most likely to hit it.
#
# Two changes:
#   HTTP/1.1, with idle connections dropped after 20 seconds, before the other
#   end gives up on them. The pool checks a connection is still open before
#   reusing it, which HTTP/2's single shared stream does not.
#
#   One retry, for requests that are safe to send twice (reads, updates,
#   deletes), when the connection dies before an answer comes back. Not for
#   POST: an insert or a rate-limit claim that DID land would count twice.
# ---------------------------------------------------------------------------

#: Methods where sending the same request again changes nothing.
_RETRYABLE_METHODS = frozenset({"GET", "HEAD", "PATCH", "PUT", "DELETE"})

#: The connection went away under us, rather than the server answering no.
_STALE_CONNECTION_ERRORS = (httpx.RemoteProtocolError, httpx.ReadError,
                            httpx.WriteError)


class _RetryStaleConnection(httpx.HTTPTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return super().handle_request(request)
        except _STALE_CONNECTION_ERRORS as exc:
            if request.method not in _RETRYABLE_METHODS:
                raise
            print(f"[db] {type(exc).__name__} on {request.method} "
                  f"{request.url.path}; retrying once on a fresh connection",
                  flush=True)
            return super().handle_request(request)


def _http_client() -> httpx.Client:
    return httpx.Client(
        transport=_RetryStaleConnection(
            http2=False,
            limits=httpx.Limits(max_connections=20,
                                max_keepalive_connections=10,
                                keepalive_expiry=20),
        ),
        # Generous on reads: generation uploads the whole paper to storage.
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=True,
    )


def _create(url: str, key: str) -> Client:
    try:
        from supabase.lib.client_options import SyncClientOptions
        options = SyncClientOptions(httpx_client=_http_client())
    except (ImportError, TypeError):  # pragma: no cover - old supabase-py
        # A version too old to accept a client falls back to its own. The
        # retry is lost, nothing else is.
        print("[db] this supabase-py can't take a custom HTTP client; "
              "upgrade it (requirements.txt pins >=2.28)", flush=True)
        return create_client(url, key)
    return create_client(url, key, options=options)


# ---------------------------------------------------------------------------
# Leagues
# ---------------------------------------------------------------------------

def create_league(
    *,
    provider: str,
    platform_league_id: str,
    league_name: str,
    paper_name: str,
    commissioner_name: str,
    season: int,
    public_slug: str,
    admin_token: str,
) -> dict[str, Any]:
    row = {
        "provider": provider,
        "platform_league_id": platform_league_id,
        "league_name": league_name,
        "paper_name": paper_name,
        "commissioner_name": commissioner_name,
        "season": season,
        "public_slug": public_slug,
        "admin_token": admin_token,
    }
    res = client().table("leagues").insert(row).execute()
    if not res.data:
        raise RuntimeError("League insert returned no row")
    return res.data[0]


def league_by_admin_token(token: str) -> Optional[dict[str, Any]]:
    """Look up a league by its secret token. This is the authorization check."""
    if not token:
        return None
    res = client().table("leagues").select("*").eq("admin_token", token).limit(1).execute()
    return res.data[0] if res.data else None


def league_by_public_slug(slug: str) -> Optional[dict[str, Any]]:
    """Public lookup. Deliberately never selects admin_token."""
    if not slug:
        return None
    res = (
        client().table("leagues")
        .select("id, provider, platform_league_id, league_name, paper_name, "
                "commissioner_name, season, public_slug, created_at")
        .eq("public_slug", slug).limit(1).execute()
    )
    return res.data[0] if res.data else None


def find_existing_league(provider: str, platform_league_id: str, season: int):
    res = (
        client().table("leagues").select("*")
        .eq("provider", provider)
        .eq("platform_league_id", platform_league_id)
        .eq("season", season)
        .limit(1).execute()
    )
    return res.data[0] if res.data else None


def update_league(league_id: str, fields: dict[str, Any]) -> None:
    client().table("leagues").update(fields).eq("id", league_id).execute()


def delete_league(league_id: str) -> None:
    """Remove a league and everything hanging off it.

    lore, newspapers, subscribers, magic_links and managers all declare
    `on delete cascade` against this row, so one delete takes the lot. The
    published HTML in the bucket does not cascade — storage knows nothing about
    Postgres — so it is removed first, while the rows that say where it lives
    still exist.

    Storage is best-effort on purpose. An orphaned HTML file is a few kilobytes
    nobody can find a link to; a league that refuses to delete because the
    bucket hiccuped is a commissioner stuck with a league they asked to be rid
    of. The database delete is the one that has to happen.
    """
    try:
        paths = [p["storage_path"] for p in list_papers(league_id)
                 if p.get("storage_path")]
        if paths:
            client().storage.from_(BUCKET).remove(paths)
    except Exception as exc:  # noqa: BLE001 — see the note above
        print(f"[delete] league {league_id}: storage not cleaned: "
              f"{type(exc).__name__}: {exc}", flush=True)

    client().table("leagues").delete().eq("id", league_id).execute()


# ---------------------------------------------------------------------------
# Lore — the thing that makes one league's paper unlike another's
# ---------------------------------------------------------------------------

def get_lore(league_id: str) -> list[dict[str, Any]]:
    res = (
        client().table("lore").select("*")
        .eq("league_id", league_id).eq("active", True)
        .order("created_at").execute()
    )
    return res.data or []


def add_lore(league_id: str, text: str) -> None:
    client().table("lore").insert({"league_id": league_id, "entry": text}).execute()


def deactivate_lore(lore_id: str, league_id: str) -> None:
    # league_id in the filter so a stray ID from another league can't be touched.
    (
        client().table("lore").update({"active": False})
        .eq("id", lore_id).eq("league_id", league_id).execute()
    )


# ---------------------------------------------------------------------------
# Newspapers
# ---------------------------------------------------------------------------

def storage_path(public_slug: str, season: int, week: int) -> str:
    return f"{public_slug}/{season}/week-{int(week):02d}.html"


def upload_paper(public_slug: str, season: int, week: int, html: str) -> tuple[str, str]:
    """Put the rendered edition in the bucket. Returns (path, public_url)."""
    path = storage_path(public_slug, season, week)
    storage = client().storage.from_(BUCKET)
    payload = html.encode("utf-8")
    options = {"content-type": "text/html; charset=utf-8", "upsert": "true"}
    try:
        storage.upload(path, payload, options)
    except Exception:
        # Older supabase-py raises rather than upserting when the object exists.
        storage.update(path, payload, options)
    return path, storage.get_public_url(path)


def upload_image(public_slug: str, filename: str, data: bytes,
                 content_type: str = "image/jpeg") -> str:
    """Put a commissioner's photo in the bucket and return its public URL.

    Same bucket as the papers, under an images/ prefix, so a league's photos
    are removed along with its papers if it's ever deleted.
    """
    import secrets
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()[:5]
    path = f"{public_slug}/images/{secrets.token_urlsafe(12)}.{ext}"
    storage = client().storage.from_(BUCKET)
    options = {"content-type": content_type, "upsert": "true"}
    try:
        storage.upload(path, data, options)
    except Exception:
        storage.update(path, data, options)
    return storage.get_public_url(path)


def download_paper(path: str) -> Optional[str]:
    try:
        return client().storage.from_(BUCKET).download(path).decode("utf-8")
    except Exception:
        return None


def save_paper(
    league_id: str, week: int, season: int, storage_path_: str,
    public_url: str, ai_cache: Any, *, is_edit: bool = False,
) -> None:
    """Store a rendered paper.

    `is_edit=True` means a human changed the prose: keep whatever Claude
    originally wrote in ai_cache_original so revert stays possible, and stamp
    edited_at so regeneration can warn before discarding the work.

    `is_edit=False` is a fresh generation, which resets both.
    """
    row = {
        "league_id": league_id,
        "week": week,
        "season": season,
        "storage_path": storage_path_,
        "public_url": public_url,
        "ai_cache": ai_cache or None,   # jsonb column; pass the dict through
    }
    existing = (
        client().table("newspapers")
        .select("id, ai_cache_original, generation_count")
        .eq("league_id", league_id).eq("week", week).eq("season", season)
        .execute()
    )

    if is_edit:
        row["edited_at"] = "now()"
    else:
        row["ai_cache_original"] = ai_cache or None
        row["edited_at"] = None
        # When the prose was WRITTEN, so the weekly job can tell a paper made
        # on Friday (half the week's scores) from one made after Monday night.
        # It used to be set once, at first generation, and never moved.
        from datetime import datetime, timezone
        row["generated_at"] = datetime.now(timezone.utc).isoformat()

    if existing.data:
        # Never overwrite an original that's already recorded.
        if is_edit and not existing.data[0].get("ai_cache_original"):
            row["ai_cache_original"] = ai_cache or None

        # Count renders, not edits: an edit re-renders the page but writes no
        # prose and costs nothing, so it must not spend a regeneration.
        #
        # Read-modify-write rather than an atomic RPC, deliberately. This is a
        # courtesy allowance shown to one commissioner, not a spend ceiling —
        # the ceilings are GENERATIONS_PER_LEAGUE_PER_DAY and the global daily
        # budget, both of which are claimed atomically. The worst case here is
        # someone double-clicking Generate and getting four regenerations
        # instead of three, which costs pennies and harms nobody.
        if not is_edit:
            row["generation_count"] = (
                (existing.data[0].get("generation_count") or 1) + 1)

        client().table("newspapers").update(row).eq("id", existing.data[0]["id"]).execute()
    else:
        row.setdefault("ai_cache_original", ai_cache or None)
        client().table("newspapers").insert(row).execute()


def list_papers(league_id: str) -> list[dict[str, Any]]:
    res = (
        client().table("newspapers")
        .select("id, week, season, generated_at, public_url, storage_path, "
                "view_count, last_viewed_at, generation_count")
        .eq("league_id", league_id)
        .order("season", desc=True).order("week", desc=True)
        .execute()
    )
    return res.data or []


def record_view(league_id: str, season: int, week: int) -> None:
    """Count one read.

    Incremented inside the database rather than read-modify-written here,
    because the normal case for this product is a link hitting a group chat and
    eight people opening it in the same second.

    Never raises: a counter is not worth failing a page load over.
    """
    try:
        client().rpc("bump_paper_views", {
            "p_league_id": league_id,
            "p_season": int(season),
            "p_week": int(week),
        }).execute()
    except Exception:  # noqa: BLE001
        pass


def health_check() -> None:
    """Is the database reachable, with a key that can actually use it?

    Raises only for the two things that make the whole app useless. The first
    version ran a bare select, which was worse than useless: with RLS on and no
    policies a publishable key returns an empty result rather than an error, so
    the check passed with a key that could do nothing.

    Deliberately does NOT check the schema. A missing migration is a real
    problem, but published papers still serve without it, and failing the
    health check would pull a partly-working site out of rotation entirely.
    That's schema_report's job, and it reports rather than raises.
    """
    client()  # raises on a key that cannot bypass RLS
    client().table("leagues").select("id").limit(1).execute()


#: What each migration leaves behind, so a half-migrated database can say so
#: rather than failing later with "could not find the column".
_EXPECTED_SCHEMA = [
    ("005_setup", "column", "leagues", "stakes"),
    ("006_edits", "column", "newspapers", "ai_cache_original"),
    ("007_themes", "column", "leagues", "theme"),
    ("008_views", "column", "newspapers", "view_count"),
    ("009_limits", "function", "claim_rate_slot", None),
    ("011_generation_count", "column", "newspapers", "generation_count"),
]


#: Set once the schema has been seen intact, so the probe above — five round
#: trips to Supabase — runs at most a handful of times rather than on every
#: generate. Deliberately one-way: it latches on success only, so running the
#: missing migration fixes the site on the next request with no redeploy.
#: Nothing sets it back, because a column cannot un-exist.
_SCHEMA_CONFIRMED = False


def schema_blockers() -> list[str]:
    """Migrations whose absence would make generating a paper fail.

    Called *before* generation rather than after. Migration 006 was missing
    through a deploy, and the shape of that failure was: sixteen Claude calls
    succeed, fourteen seconds pass, the paper is written and paid for — and
    then the insert raises `column newspapers.ai_cache_original does not
    exist` and all of it is discarded. The user sees a crash page and the
    money is gone.

    Checking first costs one round trip the first time and nothing after.
    """
    global _SCHEMA_CONFIRMED
    if _SCHEMA_CONFIRMED:
        return []
    missing = schema_report()
    if not missing:
        _SCHEMA_CONFIRMED = True
    return missing


def schema_report() -> list[str]:
    """Migrations that look unapplied. Empty list means everything is present.

    Written because the symptom of a missing migration is a 500 in the middle
    of a user's first attempt, with the real cause — "run 007" — visible only
    in a server log. One request to /healthz should be able to say it instead.
    """
    missing: list[str] = []
    for name, kind, target, column in _EXPECTED_SCHEMA:
        try:
            if kind == "column":
                client().table(target).select(column).limit(1).execute()
            else:
                # A zero limit always refuses and never writes a row.
                client().rpc(target, {
                    "p_bucket": "schema-probe",
                    "p_limit": 0,
                    "p_window": "1 seconds",
                }).execute()
        except Exception:  # noqa: BLE001 — absence is the signal
            missing.append(name)
    return missing


# ---------------------------------------------------------------------------
# Rate limiting
#
# Shared state, so that the daily spend ceiling is a property of the product
# rather than of however many processes happen to be running. See migration
# 009 for why the counting happens inside one statement.
# ---------------------------------------------------------------------------

def claim_rate_slot(bucket: str, limit: int, window_seconds: int) -> bool:
    """Consume one unit of an allowance. True if the caller may proceed.

    Fails CLOSED. A caller that can't reach the database is about to fail
    anyway — every endpoint behind one of these limits writes to the database
    moments later — so refusing costs nothing, while failing open would mean a
    database blip switches off the cost ceiling precisely when nobody is
    watching.
    """
    try:
        res = client().rpc("claim_rate_slot", {
            "p_bucket": bucket[:200],
            "p_limit": int(limit),
            "p_window": f"{int(window_seconds)} seconds",
        }).execute()
    except Exception:  # noqa: BLE001
        return False
    return bool(res.data)


def sweep_rate_events(older_than_seconds: int = 2 * 86400) -> int:
    """Drop rate-limit rows for buckets nobody has visited since.

    Returns how many went. Never raises: housekeeping must not fail a job.
    """
    try:
        res = client().rpc("sweep_rate_events", {
            "p_older_than": f"{int(older_than_seconds)} seconds",
        }).execute()
        return int(res.data or 0)
    except Exception:  # noqa: BLE001
        return 0


def get_paper(league_id: str, season: int, week: int) -> Optional[dict[str, Any]]:
    res = (
        client().table("newspapers").select("*")
        .eq("league_id", league_id).eq("season", season).eq("week", week)
        .limit(1).execute()
    )
    return res.data[0] if res.data else None


# ---------------------------------------------------------------------------
# Subscribers
#
# Double opt-in: `subscribe` creates or revives an UNCONFIRMED row and returns
# the confirm token. Nothing is ever mailed to an address that hasn't clicked
# through, which is both the legal posture and the reason the sending domain
# stays out of spam folders.
# ---------------------------------------------------------------------------

def subscribe(league_id: str, email: str, source: str = "reader") -> Optional[dict[str, Any]]:
    """Create or revive a subscription. Returns the row, or None if already confirmed."""
    email = email.strip().lower()
    existing = (
        client().table("subscribers").select("*")
        .eq("league_id", league_id).eq("email", email).limit(1).execute()
    )

    if existing.data:
        row = existing.data[0]
        if row["confirmed"] and not row.get("unsubscribed_at"):
            return None  # already on the list; don't re-send a confirmation
        # Previously unsubscribed or never confirmed: issue a fresh token.
        new_token = _token()
        client().table("subscribers").update({
            "confirm_token": new_token,
            "unsubscribed_at": None,
            "source": source,
        }).eq("id", row["id"]).execute()
        row["confirm_token"] = new_token
        return row

    res = client().table("subscribers").insert({
        "league_id": league_id,
        "email": email,
        "confirm_token": _token(),
        "unsubscribe_token": _token(),
        "source": source,
    }).execute()
    return res.data[0] if res.data else None


def confirm_subscription(token: str) -> Optional[dict[str, Any]]:
    res = (
        client().table("subscribers").select("*, leagues(*)")
        .eq("confirm_token", token).limit(1).execute()
    )
    if not res.data:
        return None
    row = res.data[0]
    client().table("subscribers").update({
        "confirmed": True,
        "confirmed_at": "now()",
        "unsubscribed_at": None,
    }).eq("id", row["id"]).execute()
    return row


def unsubscribe(token: str) -> Optional[dict[str, Any]]:
    res = (
        client().table("subscribers").select("*, leagues(*)")
        .eq("unsubscribe_token", token).limit(1).execute()
    )
    if not res.data:
        return None
    row = res.data[0]
    client().table("subscribers").update({"unsubscribed_at": "now()"})\
        .eq("id", row["id"]).execute()
    return row


def active_subscribers(league_id: str) -> list[dict[str, Any]]:
    """Confirmed and not unsubscribed. The weekly send reads exactly this."""
    res = (
        client().table("subscribers").select("*")
        .eq("league_id", league_id).eq("confirmed", True)
        .is_("unsubscribed_at", "null").execute()
    )
    return res.data or []


def subscriber_count(league_id: str) -> int:
    return len(active_subscribers(league_id))


# ---------------------------------------------------------------------------
# Magic links — the whole of "authentication"
# ---------------------------------------------------------------------------

def leagues_for_email(email: str) -> list[dict[str, Any]]:
    res = (
        client().table("leagues").select("*")
        .ilike("owner_email", email.strip()).execute()
    )
    return res.data or []


def create_magic_link(email: str, token: str, expires_at: str,
                      purpose: str = "recover") -> None:
    """A single-use, expiring link.

    `purpose` keeps the two uses apart: a link mailed to recover a manage URL
    must not be redeemable as a password reset, or anyone who ever received
    one holds a permanent key to the account.
    """
    client().table("magic_links").insert({
        "email": email.strip().lower(),
        "token": token,
        "expires_at": expires_at,
        "purpose": purpose,
    }).execute()


def peek_magic_link(token: str, purpose: str = "recover") -> Optional[str]:
    """The email a token is for, WITHOUT burning it.

    Exists so a rejected password doesn't cost somebody their one-shot reset
    link. Validate first, consume only once the new password is acceptable.
    """
    res = client().table("magic_links").select("*").eq("token", token).limit(1).execute()
    if not res.data:
        return None
    row = res.data[0]
    if row.get("used_at") or (row.get("purpose") or "recover") != purpose:
        return None

    from datetime import datetime, timezone
    try:
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if datetime.now(timezone.utc) > expires:
        return None
    return row["email"]


def consume_magic_link(token: str, purpose: str = "recover") -> Optional[str]:
    """Return the email if the token is valid, unused and for this purpose.

    Burns it either way it succeeds. `purpose` must match what the link was
    minted for: a recovery link redeemed as a password reset would turn every
    manage-link email ever sent into a permanent key to the account.
    """
    res = client().table("magic_links").select("*").eq("token", token).limit(1).execute()
    if not res.data:
        return None
    row = res.data[0]
    if row.get("used_at"):
        return None
    if (row.get("purpose") or "recover") != purpose:
        return None

    from datetime import datetime, timezone
    try:
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if datetime.now(timezone.utc) > expires:
        return None

    client().table("magic_links").update({"used_at": "now()"}).eq("id", row["id"]).execute()
    return row["email"]


# ---------------------------------------------------------------------------
# Auto-send bookkeeping
# ---------------------------------------------------------------------------

def leagues_with_auto_send() -> list[dict[str, Any]]:
    res = client().table("leagues").select("*").eq("auto_send", True).execute()
    return res.data or []


def leagues_for_weekly_send() -> list[dict[str, Any]]:
    """Every league the Tuesday job should write and deliver, each with its
    owner attached as `_owner`.

    A league qualifies when its OWNER is on a plan that includes weekly
    delivery right now — so a lapsed card stops the papers the same week — and
    the owner hasn't switched delivery off (migration 018). Asked per owner
    rather than per league: a handful of paying accounts, not every league.
    """
    import plans
    owners = (client().table("users").select("*")
              .in_("plan", [plans.PAID, plans.STAFF]).execute().data or [])
    owners = {u["id"]: u for u in owners if plans.plan_for(u).auto_send}
    if not owners:
        return []
    leagues = (client().table("leagues").select("*")
               .in_("user_id", list(owners)).execute().data or [])
    out = []
    for league in leagues:
        if league.get("auto_send_off"):
            continue
        out.append({**league, "_owner": owners[league["user_id"]]})
    return out


def mark_emailed(league_id: str, season: int, week: int) -> None:
    from datetime import datetime, timezone
    (
        client().table("newspapers")
        .update({"emailed_at": datetime.now(timezone.utc).isoformat()})
        .eq("league_id", league_id).eq("season", season).eq("week", week).execute()
    )


def _token() -> str:
    import secrets
    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------------------
# Accounts
#
# Readers never touch any of this. It exists because the admin token was the
# only credential a commissioner had, and losing the URL it lived in was
# unrecoverable. See migration 010.
# ---------------------------------------------------------------------------

def create_user(email: str, password_hash: str) -> Optional[dict[str, Any]]:
    """Register an account. None if the address is already taken.

    Uniqueness is enforced by a unique index on lower(email), so a race between
    two simultaneous signups is settled by the database rather than by a
    check-then-insert here that both requests would pass.
    """
    try:
        res = client().table("users").insert({
            "email": email.strip().lower(),
            "password_hash": password_hash,
        }).execute()
    except Exception:  # noqa: BLE001 — unique violation is the expected case
        return None
    return res.data[0] if res.data else None


def user_by_email(email: str) -> Optional[dict[str, Any]]:
    res = (client().table("users").select("*")
           .ilike("email", (email or "").strip().lower())
           .limit(1).execute())
    return res.data[0] if res.data else None


# --- Google sign-in ---------------------------------------------------------
#
# The lookup is by SUB, never by email. See migrations/015_google.sql.

def user_by_google_sub(sub: str) -> Optional[dict[str, Any]]:
    if not sub:
        return None
    res = (client().table("users").select("*")
           .eq("google_sub", sub).limit(1).execute())
    return res.data[0] if res.data else None


def create_google_user(email: str, sub: str,
                       name: str = "") -> Optional[dict[str, Any]]:
    """An account with no password, ever.

    password_hash is left null, which migration 015 exists to permit. None
    comes back if the address is somehow taken between the caller's check and
    this insert — the unique index settles that race rather than a
    check-then-insert here, same as create_user.
    """
    try:
        res = client().table("users").insert({
            "email": email.strip().lower(),
            "google_sub": sub,
            "google_email": email.strip().lower(),
            "google_linked_at": "now()",
            "verified_at": "now()",
        }).execute()
    except Exception:  # noqa: BLE001 — unique violation is the expected case
        return None
    return res.data[0] if res.data else None


def link_google(user_id: str, sub: str, email: str) -> None:
    """Attach a Google identity to an account that already exists.

    Only ever called after the caller has established that Google VERIFIED
    this address. There is no code path that links on an address alone.
    """
    client().table("users").update({
        "google_sub": sub,
        "google_email": (email or "").strip().lower(),
        "google_linked_at": "now()",
    }).eq("id", user_id).execute()


def user_by_id(user_id: str) -> Optional[dict[str, Any]]:
    if not user_id:
        return None
    res = client().table("users").select("*").eq("id", user_id).limit(1).execute()
    return res.data[0] if res.data else None


def update_user(user_id: str, fields: dict[str, Any]) -> None:
    client().table("users").update(fields).eq("id", user_id).execute()


def leagues_for_user(user_id: str) -> list[dict[str, Any]]:
    res = (client().table("leagues").select("*")
           .eq("user_id", user_id)
           .order("created_at", desc=True).execute())
    return res.data or []


def claim_league(league_id: str, user_id: str) -> None:
    """Attach a league to an account."""
    client().table("leagues").update({"user_id": user_id}).eq("id", league_id).execute()


# ---------------------------------------------------------------------------
# Plans
#
# These are a cache of Stripe, written by the webhook. Unlike almost every
# other read in this file, they do NOT fail soft: a lookup that swallowed its
# error and returned None would silently drop somebody's subscription on the
# floor and leave them paying for the free tier. If the database is unhappy
# the webhook should fail, so Stripe retries it — which is exactly what Stripe
# retries are for.
# ---------------------------------------------------------------------------

def user_by_stripe_customer(customer_id: str) -> Optional[dict[str, Any]]:
    """Who this Stripe customer is.

    The webhook arrives knowing a customer id and nothing else, so this is the
    join between what Stripe says and who it happened to.
    """
    if not customer_id:
        return None
    res = (client().table("users").select("*")
           .eq("stripe_customer_id", customer_id).limit(1).execute())
    return res.data[0] if res.data else None


def set_plan(user_id: str, *, plan: str, status: Optional[str] = None,
             subscription_id: Optional[str] = None,
             renews_at: Optional[str] = None) -> None:
    """Record what Stripe just said about somebody's subscription."""
    client().table("users").update({
        "plan": plan,
        "plan_status": status,
        "stripe_subscription_id": subscription_id,
        "plan_renews_at": renews_at,
        "plan_updated_at": "now()",
    }).eq("id", user_id).execute()


def remember_stripe_customer(user_id: str, customer_id: str) -> None:
    """Tie an account to its Stripe customer, once.

    Written before checkout rather than after, so that the webhook can find
    the account even if the person closes the tab on Stripe's page and the
    success redirect never happens. The subscription event arrives regardless
    and has to land somewhere.
    """
    client().table("users").update(
        {"stripe_customer_id": customer_id}).eq("id", user_id).execute()


# ---------------------------------------------------------------------------
# The classifieds page
#
# The first global content in the app. Every other read in this file is scoped
# to a league, and the scoping is load-bearing security — a stray ID from
# another league cannot be touched because the league_id is in the filter.
# Nothing here is scoped, because the page genuinely does belong to everyone.
# What replaces the scoping is that only one route can reach these functions,
# and it needs PUBLISHER_TOKEN.
#
# Every read fails SOFT. If migration 012 has not been applied, or Supabase is
# having a moment, a league's paper prints without the classifieds page —
# which is a paper missing a page of memes, not a paper that failed. Compare
# `schema_blockers`, which is deliberately hard: that list is migrations whose
# absence makes generation fail *after* the Claude calls have been paid for,
# and this is not one of them.
# ---------------------------------------------------------------------------

def publisher_ads(season: int, week: int) -> list[dict[str, Any]]:
    """This week's ads, in the order they should appear."""
    try:
        res = (client().table("publisher_ads").select("*")
               .eq("season", int(season)).eq("week", int(week))
               .order("position").order("created_at").execute())
        return res.data or []
    except Exception as exc:  # noqa: BLE001 — see the note above
        print(f"[ads] could not read the classifieds page: "
              f"{type(exc).__name__}: {exc}", flush=True)
        return []


def publisher_ads_table_ready() -> bool:
    """Whether migration 012 has been applied.

    Only the publisher page asks. It is the one surface where "there are no ads
    this week" and "the table does not exist" look identical and mean entirely
    different things — one needs five images, the other needs a migration.
    """
    try:
        client().table("publisher_ads").select("id").limit(1).execute()
        return True
    except Exception:  # noqa: BLE001 — absence is the signal
        return False


def add_publisher_ad(season: int, week: int, image_url: str,
                     storage_path_: Optional[str] = None,
                     width: Optional[int] = None,
                     height: Optional[int] = None,
                     caption: str = "", link_url: str = "") -> dict[str, Any]:
    """Append an ad to a week. Returns the stored row."""
    existing = publisher_ads(season, week)
    row = {
        "season": int(season),
        "week": int(week),
        "position": len(existing),
        "image_url": image_url,
        "storage_path": storage_path_,
        "width": int(width) if width else None,
        "height": int(height) if height else None,
        "caption": (caption or "").strip()[:200] or None,
        "link_url": (link_url or "").strip()[:500] or None,
    }
    res = client().table("publisher_ads").insert(row).execute()
    return (res.data or [row])[0]


def delete_publisher_ad(ad_id: str) -> None:
    """Remove an ad, and the file behind it.

    The row goes either way. A bucket delete that fails leaves one orphaned
    image, which costs a fraction of a cent; a row that survives its own
    delete leaves a broken image on the page.
    """
    row = None
    try:
        res = (client().table("publisher_ads").select("storage_path")
               .eq("id", ad_id).limit(1).execute())
        row = (res.data or [None])[0]
    except Exception:  # noqa: BLE001
        pass

    client().table("publisher_ads").delete().eq("id", ad_id).execute()

    path = (row or {}).get("storage_path")
    if path:
        try:
            client().storage.from_(BUCKET).remove([path])
        except Exception as exc:  # noqa: BLE001
            print(f"[ads] orphaned {path}: {exc}", flush=True)


def reorder_publisher_ads(season: int, week: int, ordered_ids: list[str]) -> None:
    """Write the order the publisher dragged them into.

    Scoped by season and week in every update, so an id from another week
    cannot be dragged into this one by a handcrafted request.
    """
    for index, ad_id in enumerate(ordered_ids):
        (client().table("publisher_ads").update({"position": index})
         .eq("id", ad_id).eq("season", int(season)).eq("week", int(week))
         .execute())


def upload_publisher_image(filename: str, data: bytes,
                           content_type: str = "image/jpeg",
                           season: int = 0, week: int = 0) -> tuple[str, str]:
    """Store an ad image. Returns (storage_path, public_url).

    Under its own prefix rather than a league's, because it belongs to no
    league — and because a league being deleted must not take the classifieds
    page down with it.
    """
    import secrets
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()[:5]
    path = (f"publisher/{int(season)}/week-{int(week):02d}/"
            f"{secrets.token_urlsafe(12)}.{ext}")
    storage = client().storage.from_(BUCKET)
    options = {"content-type": content_type, "upsert": "true"}
    try:
        storage.upload(path, data, options)
    except Exception:
        storage.update(path, data, options)
    return path, storage.get_public_url(path)


# ---------------------------------------------------------------------------
# The people in the league
#
# Lore was one flat list per league. This is the other half: what is true about
# one specific person, and what to call them.
#
# Every read here fails SOFT, like the classifieds. A league whose managers
# table is missing gets a paper that prints handles — which is what it printed
# all last season — rather than no paper.
# ---------------------------------------------------------------------------

def get_managers(league_id: str) -> list[dict[str, Any]]:
    """Everyone in this league, with whatever is known about them."""
    try:
        res = (client().table("managers").select("*")
               .eq("league_id", league_id).order("handle").execute())
        return res.data or []
    except Exception as exc:  # noqa: BLE001 — see the note above
        print(f"[lore] could not read managers: "
              f"{type(exc).__name__}: {exc}", flush=True)
        return []


def remember_managers(league_id: str, handles) -> None:
    """Make sure every handle in this week's data has a row.

    Called after a paper generates, because that is the one moment the app is
    holding the real list of who is in the league. Existing rows are left
    exactly alone — this adds the people it has not seen before and nothing
    else, so it can run every week without touching anything anybody typed.
    """
    wanted = {str(h).strip() for h in (handles or []) if str(h or "").strip()}
    if not wanted:
        return

    try:
        known = {row.get("handle") for row in get_managers(league_id)}
        new = sorted(wanted - known)
        if not new:
            return
        client().table("managers").insert(
            [{"league_id": league_id, "handle": handle} for handle in new]
        ).execute()
    except Exception as exc:  # noqa: BLE001
        print(f"[lore] could not record managers: "
              f"{type(exc).__name__}: {exc}", flush=True)


def save_manager(league_id: str, handle: str,
                 display_name: str = "", notes: str = "") -> None:
    """Write what the commissioner typed about one person."""
    fields = {
        "display_name": (display_name or "").strip()[:80] or None,
        "notes": (notes or "").strip()[:2000] or None,
        "updated_at": "now()",
    }
    # league_id in the filter so a handle from another league cannot be
    # written through this, the same rule as every other scoped update here.
    (client().table("managers").update(fields)
     .eq("league_id", league_id).eq("handle", handle).execute())


# ---------------------------------------------------------------------------
# The league's own awards (migration 016)
#
# Every read fails soft to an empty list: a missing table means the paper
# prints its standing awards and the account page says the migration is due.
# ---------------------------------------------------------------------------

def get_awards(league_id: str) -> list[dict[str, Any]]:
    try:
        res = (client().table("league_awards").select("*")
               .eq("league_id", league_id).order("created_at").execute())
        return res.data or []
    except Exception as exc:  # noqa: BLE001
        print(f"[awards] could not read: {type(exc).__name__}: {exc}", flush=True)
        return []


def awards_table_ready() -> bool:
    try:
        client().table("league_awards").select("id").limit(1).execute()
        return True
    except Exception:  # noqa: BLE001
        return False


def add_award(league_id: str, fields: dict) -> None:
    client().table("league_awards").insert(dict(fields, league_id=league_id)).execute()


def update_award(league_id: str, award_id: str, fields: dict) -> None:
    (client().table("league_awards").update(dict(fields, updated_at="now()"))
     .eq("league_id", league_id).eq("id", award_id).execute())


def delete_award(league_id: str, award_id: str) -> None:
    (client().table("league_awards").delete()
     .eq("league_id", league_id).eq("id", award_id).execute())
