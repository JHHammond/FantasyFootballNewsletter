"""
In-memory stand-in for db.py. Same functions, no Supabase.

Turn it on with DEMO_MODE=1. Everything works — creating a league, adding
jokes, generating a real paper from real Sleeper data with real Claude prose,
reading it at a public URL — it just evaporates when you stop the server.

The point is being able to click through the whole product before deciding how
to set up a database. Do not run this in production: one process holds all
state, and restarting loses every league.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

_lock = threading.Lock()

_LEAGUES: dict[str, dict[str, Any]] = {}
_JOKES: dict[str, dict[str, Any]] = {}
_PAPERS: dict[tuple[str, int, int], dict[str, Any]] = {}
_STORAGE: dict[str, str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Leagues
# ---------------------------------------------------------------------------

def create_league(**kw) -> dict[str, Any]:
    with _lock:
        league = {"id": str(uuid4()), "created_at": _now(), **kw}
        _LEAGUES[league["id"]] = league
        return dict(league)


def league_by_admin_token(token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    return next((dict(l) for l in _LEAGUES.values() if l.get("admin_token") == token), None)


def league_by_public_slug(slug: str) -> Optional[dict[str, Any]]:
    found = next((l for l in _LEAGUES.values() if l.get("public_slug") == slug), None)
    if not found:
        return None
    # Mirror the real implementation: never hand back the admin token.
    return {k: v for k, v in found.items() if k != "admin_token"}


def find_existing_league(provider: str, platform_league_id: str, season: int):
    return next(
        (dict(l) for l in _LEAGUES.values()
         if l.get("provider") == provider
         and l.get("platform_league_id") == platform_league_id
         and l.get("season") == season),
        None,
    )


def update_league(league_id: str, fields: dict[str, Any]) -> None:
    with _lock:
        if league_id in _LEAGUES:
            _LEAGUES[league_id].update(fields)


# ---------------------------------------------------------------------------
# Jokes
# ---------------------------------------------------------------------------

def get_jokes(league_id: str) -> list[dict[str, Any]]:
    return [
        dict(j) for j in _JOKES.values()
        if j["league_id"] == league_id and j["active"]
    ]


def add_joke(league_id: str, text: str) -> None:
    with _lock:
        jid = str(uuid4())
        _JOKES[jid] = {
            "id": jid, "league_id": league_id, "joke": text,
            "active": True, "created_at": _now(),
        }


def deactivate_joke(joke_id: str, league_id: str) -> None:
    with _lock:
        joke = _JOKES.get(joke_id)
        if joke and joke["league_id"] == league_id:
            joke["active"] = False


# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------

def storage_path(public_slug: str, season: int, week: int) -> str:
    return f"{public_slug}/{season}/week-{int(week):02d}.html"


def upload_paper(public_slug: str, season: int, week: int, html: str) -> tuple[str, str]:
    path = storage_path(public_slug, season, week)
    with _lock:
        _STORAGE[path] = html
    return path, f"/p/{public_slug}/{season}/week-{week}"


def download_paper(path: str) -> Optional[str]:
    return _STORAGE.get(path)


def save_paper(league_id, week, season, storage_path_, public_url, ai_cache) -> None:
    with _lock:
        _PAPERS[(league_id, season, week)] = {
            "id": str(uuid4()),
            "league_id": league_id, "week": week, "season": season,
            "storage_path": storage_path_, "public_url": public_url,
            "ai_cache": ai_cache, "generated_at": _now(),
        }


def list_papers(league_id: str) -> list[dict[str, Any]]:
    papers = [dict(p) for k, p in _PAPERS.items() if k[0] == league_id]
    papers.sort(key=lambda p: (p["season"], p["week"]), reverse=True)
    return papers


def get_paper(league_id: str, season: int, week: int) -> Optional[dict[str, Any]]:
    found = _PAPERS.get((league_id, season, week))
    return dict(found) if found else None
