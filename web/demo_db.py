"""
In-memory stand-in for db.py. Same functions, no Supabase.

Turn it on with DEMO_MODE=1. Everything works — creating a league, adding lore,
generating a real paper from real league data with real Claude prose, reading
it at a public URL, subscribing, unsubscribing, magic-link recovery — it just
evaporates when you stop the server.

The point is being able to click through the whole product before deciding how
to set up a database. Do not run this in production: one process holds all
state, and restarting loses everything.

This module must stay function-for-function identical to db.py. If they drift,
demo mode stops telling you the truth about production.
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

_lock = threading.Lock()

_LEAGUES: dict[str, dict[str, Any]] = {}
_LORE: dict[str, dict[str, Any]] = {}
_PAPERS: dict[tuple[str, int, int], dict[str, Any]] = {}
_STORAGE: dict[str, str] = {}
_SUBSCRIBERS: dict[str, dict[str, Any]] = {}
_MAGIC_LINKS: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token() -> str:
    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------------------
# Leagues
# ---------------------------------------------------------------------------

def create_league(**kw) -> dict[str, Any]:
    with _lock:
        league = {
            "id": str(uuid4()), "created_at": _now(),
            "owner_email": None, "auto_send": False,
            # Mirrors the column defaults in 005_setup.sql. If these drift,
            # demo mode stops being a truthful preview.
            "format": "redraft", "tone": "standard", "theme": "tabloid",
            "founded_year": None,
            "stakes": None, "punishment": None, "setup_complete": False,
            **kw,
        }
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
# Lore
# ---------------------------------------------------------------------------

def get_lore(league_id: str) -> list[dict[str, Any]]:
    return [dict(l) for l in _LORE.values()
            if l["league_id"] == league_id and l["active"]]


def add_lore(league_id: str, text: str) -> None:
    with _lock:
        lid = str(uuid4())
        _LORE[lid] = {
            "id": lid, "league_id": league_id, "entry": text,
            "active": True, "created_at": _now(),
        }


def deactivate_lore(lore_id: str, league_id: str) -> None:
    with _lock:
        entry = _LORE.get(lore_id)
        if entry and entry["league_id"] == league_id:
            entry["active"] = False


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


#: Demo-mode image store. Served back by the app at /demo-image/<name>.
_IMAGES: dict[str, tuple[bytes, str]] = {}


def upload_image(public_slug: str, filename: str, data: bytes,
                 content_type: str = "image/jpeg") -> str:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()[:5]
    name = f"{public_slug}-{secrets.token_urlsafe(8)}.{ext}"
    with _lock:
        _IMAGES[name] = (data, content_type)
    return f"/demo-image/{name}"


def download_paper(path: str) -> Optional[str]:
    return _STORAGE.get(path)


def save_paper(league_id, week, season, storage_path_, public_url, ai_cache,
               *, is_edit: bool = False) -> None:
    with _lock:
        existing = _PAPERS.get((league_id, season, week), {})

        if is_edit:
            original = existing.get("ai_cache_original") or ai_cache
            edited_at = _now()
        else:
            original = ai_cache
            edited_at = None

        _PAPERS[(league_id, season, week)] = {
            "id": existing.get("id", str(uuid4())),
            "league_id": league_id, "week": week, "season": season,
            "storage_path": storage_path_, "public_url": public_url,
            "ai_cache": ai_cache, "ai_cache_original": original,
            "generated_at": existing.get("generated_at") or _now(),
            "edited_at": edited_at,
            "emailed_at": existing.get("emailed_at"),
        }


def list_papers(league_id: str) -> list[dict[str, Any]]:
    papers = [dict(p) for k, p in _PAPERS.items() if k[0] == league_id]
    papers.sort(key=lambda p: (p["season"], p["week"]), reverse=True)
    return papers


def get_paper(league_id: str, season: int, week: int) -> Optional[dict[str, Any]]:
    found = _PAPERS.get((league_id, season, week))
    return dict(found) if found else None


# ---------------------------------------------------------------------------
# Subscribers
# ---------------------------------------------------------------------------

def subscribe(league_id: str, email: str, source: str = "reader") -> Optional[dict[str, Any]]:
    email = email.strip().lower()
    with _lock:
        existing = next(
            (s for s in _SUBSCRIBERS.values()
             if s["league_id"] == league_id and s["email"] == email),
            None,
        )
        if existing:
            if existing["confirmed"] and not existing.get("unsubscribed_at"):
                return None
            existing["confirm_token"] = _token()
            existing["unsubscribed_at"] = None
            existing["source"] = source
            return dict(existing)

        sid = str(uuid4())
        _SUBSCRIBERS[sid] = {
            "id": sid, "league_id": league_id, "email": email,
            "confirmed": False, "confirm_token": _token(),
            "unsubscribe_token": _token(), "unsubscribed_at": None,
            "confirmed_at": None, "created_at": _now(), "source": source,
        }
        return dict(_SUBSCRIBERS[sid])


def confirm_subscription(token: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = next((s for s in _SUBSCRIBERS.values() if s["confirm_token"] == token), None)
        if not row:
            return None
        row["confirmed"] = True
        row["confirmed_at"] = _now()
        row["unsubscribed_at"] = None
        out = dict(row)
    out["leagues"] = _LEAGUES.get(out["league_id"])
    return out


def unsubscribe(token: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = next((s for s in _SUBSCRIBERS.values() if s["unsubscribe_token"] == token), None)
        if not row:
            return None
        row["unsubscribed_at"] = _now()
        out = dict(row)
    out["leagues"] = _LEAGUES.get(out["league_id"])
    return out


def active_subscribers(league_id: str) -> list[dict[str, Any]]:
    return [
        dict(s) for s in _SUBSCRIBERS.values()
        if s["league_id"] == league_id and s["confirmed"] and not s.get("unsubscribed_at")
    ]


def subscriber_count(league_id: str) -> int:
    return len(active_subscribers(league_id))


# ---------------------------------------------------------------------------
# Magic links
# ---------------------------------------------------------------------------

def leagues_for_email(email: str) -> list[dict[str, Any]]:
    email = (email or "").strip().lower()
    return [dict(l) for l in _LEAGUES.values()
            if (l.get("owner_email") or "").lower() == email]


def create_magic_link(email: str, token: str, expires_at: str) -> None:
    with _lock:
        _MAGIC_LINKS[token] = {
            "id": str(uuid4()), "email": email.strip().lower(),
            "token": token, "expires_at": expires_at,
            "used_at": None, "created_at": _now(),
        }


def consume_magic_link(token: str) -> Optional[str]:
    with _lock:
        row = _MAGIC_LINKS.get(token)
        if not row or row.get("used_at"):
            return None
        try:
            expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        if datetime.now(timezone.utc) > expires:
            return None
        row["used_at"] = _now()
        return row["email"]


# ---------------------------------------------------------------------------
# Auto-send bookkeeping
# ---------------------------------------------------------------------------

def leagues_with_auto_send() -> list[dict[str, Any]]:
    return [dict(l) for l in _LEAGUES.values() if l.get("auto_send")]


def mark_emailed(league_id: str, season: int, week: int) -> None:
    with _lock:
        paper = _PAPERS.get((league_id, season, week))
        if paper:
            paper["emailed_at"] = _now()
