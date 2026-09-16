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

        _client = create_client(url, key)
    return _client


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
        client().table("newspapers").select("id, ai_cache_original")
        .eq("league_id", league_id).eq("week", week).eq("season", season)
        .execute()
    )

    if is_edit:
        row["edited_at"] = "now()"
    else:
        row["ai_cache_original"] = ai_cache or None
        row["edited_at"] = None

    if existing.data:
        # Never overwrite an original that's already recorded.
        if is_edit and not existing.data[0].get("ai_cache_original"):
            row["ai_cache_original"] = ai_cache or None
        client().table("newspapers").update(row).eq("id", existing.data[0]["id"]).execute()
    else:
        row.setdefault("ai_cache_original", ai_cache or None)
        client().table("newspapers").insert(row).execute()


def list_papers(league_id: str) -> list[dict[str, Any]]:
    res = (
        client().table("newspapers")
        .select("id, week, season, generated_at, public_url, storage_path, "
                "view_count, last_viewed_at")
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


def mark_emailed(league_id: str, season: int, week: int) -> None:
    (
        client().table("newspapers").update({"emailed_at": "now()"})
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
