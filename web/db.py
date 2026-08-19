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
        .select("id, week, season, generated_at, public_url, storage_path")
        .eq("league_id", league_id)
        .order("season", desc=True).order("week", desc=True)
        .execute()
    )
    return res.data or []


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


def create_magic_link(email: str, token: str, expires_at: str) -> None:
    client().table("magic_links").insert({
        "email": email.strip().lower(),
        "token": token,
        "expires_at": expires_at,
    }).execute()


def consume_magic_link(token: str) -> Optional[str]:
    """Return the email if the token is valid and unused, then burn it."""
    res = client().table("magic_links").select("*").eq("token", token).limit(1).execute()
    if not res.data:
        return None
    row = res.data[0]
    if row.get("used_at"):
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
