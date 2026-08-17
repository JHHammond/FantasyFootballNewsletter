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
# Inside jokes — the thing that makes one league's paper unlike another's
# ---------------------------------------------------------------------------

def get_jokes(league_id: str) -> list[dict[str, Any]]:
    res = (
        client().table("inside_jokes").select("*")
        .eq("league_id", league_id).eq("active", True)
        .order("created_at").execute()
    )
    return res.data or []


def add_joke(league_id: str, text: str) -> None:
    client().table("inside_jokes").insert({"league_id": league_id, "joke": text}).execute()


def deactivate_joke(joke_id: str, league_id: str) -> None:
    # league_id in the filter so a stray ID from another league can't be touched.
    (
        client().table("inside_jokes").update({"active": False})
        .eq("id", joke_id).eq("league_id", league_id).execute()
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
    public_url: str, ai_cache: Any,
) -> None:
    row = {
        "league_id": league_id,
        "week": week,
        "season": season,
        "storage_path": storage_path_,
        "public_url": public_url,
        "ai_cache": ai_cache or None,   # jsonb column; pass the dict through
    }
    existing = (
        client().table("newspapers").select("id")
        .eq("league_id", league_id).eq("week", week).eq("season", season)
        .execute()
    )
    if existing.data:
        client().table("newspapers").update(row).eq("id", existing.data[0]["id"]).execute()
    else:
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
