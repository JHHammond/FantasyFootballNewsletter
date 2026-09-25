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
import time
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
_USERS: dict[str, dict[str, Any]] = {}


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


def delete_league(league_id: str) -> None:
    """What `on delete cascade` does in Postgres, by hand.

    Every table that references leagues has to be listed here or demo mode
    stops being a truthful preview — it would leave a subscriber list behind
    that production correctly removes.
    """
    with _lock:
        for key, paper in list(_PAPERS.items()):
            if key[0] == league_id:
                _STORAGE.pop(paper.get("storage_path") or "", None)
                del _PAPERS[key]

        # Not magic_links: those are keyed by email and belong to the person,
        # not the league, in both stores.
        for store in (_LORE, _SUBSCRIBERS):
            for key, row in list(store.items()):
                if row.get("league_id") == league_id:
                    del store[key]

        for key in [k for k in _MANAGERS if k[0] == league_id]:
            del _MANAGERS[key]

        _LEAGUES.pop(league_id, None)


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
            # Regenerating replaces the prose, not the readership.
            "view_count": existing.get("view_count", 0),
            "first_viewed_at": existing.get("first_viewed_at"),
            "last_viewed_at": existing.get("last_viewed_at"),
            # Renders, not edits. An edit re-renders the page but writes no
            # prose and costs nothing, so it must not spend a regeneration.
            "generation_count": (existing.get("generation_count", 0) + 1
                                 if not is_edit
                                 else existing.get("generation_count", 1)),
        }


def list_papers(league_id: str) -> list[dict[str, Any]]:
    papers = [dict(p) for k, p in _PAPERS.items() if k[0] == league_id]
    papers.sort(key=lambda p: (p["season"], p["week"]), reverse=True)
    return papers


def get_paper(league_id: str, season: int, week: int) -> Optional[dict[str, Any]]:
    found = _PAPERS.get((league_id, season, week))
    return dict(found) if found else None


def record_view(league_id: str, season: int, week: int) -> None:
    with _lock:
        paper = _PAPERS.get((league_id, season, week))
        if paper is not None:
            paper["view_count"] = int(paper.get("view_count") or 0) + 1
            paper["first_viewed_at"] = paper.get("first_viewed_at") or _now()
            paper["last_viewed_at"] = _now()


def health_check() -> None:
    """Always healthy — the store is this process's own memory."""
    return None


def schema_report() -> list[str]:
    """Nothing to migrate when the schema is a handful of dicts."""
    return []


def schema_blockers() -> list[str]:
    """Same contract as db.schema_blockers. Dicts grow columns for free."""
    return []


# ---------------------------------------------------------------------------
# Rate limiting — same contract as db.claim_rate_slot, in a dict.
# ---------------------------------------------------------------------------

_RATE_EVENTS: dict[str, list[float]] = {}


def claim_rate_slot(bucket: str, limit: int, window_seconds: int) -> bool:
    """Consume one unit of an allowance. True if the caller may proceed.

    A sliding window, matching the SQL function it stands in for: expired
    entries are dropped before counting, so an allowance can't be spent twice
    either side of a boundary.
    """
    if limit <= 0:
        return False
    now = time.time()
    with _lock:
        kept = [t for t in _RATE_EVENTS.get(bucket, ()) if now - t < window_seconds]
        if len(kept) >= limit:
            _RATE_EVENTS[bucket] = kept
            return False
        kept.append(now)
        _RATE_EVENTS[bucket] = kept
        return True


def sweep_rate_events(older_than_seconds: int = 2 * 86400) -> int:
    now = time.time()
    with _lock:
        stale = [b for b, times in _RATE_EVENTS.items()
                 if not times or now - times[-1] > older_than_seconds]
        for bucket in stale:
            _RATE_EVENTS.pop(bucket, None)
    return len(stale)


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


def create_magic_link(email: str, token: str, expires_at: str,
                      purpose: str = "recover") -> None:
    with _lock:
        _MAGIC_LINKS[token] = {
            "id": str(uuid4()), "email": email.strip().lower(),
            "token": token, "expires_at": expires_at, "purpose": purpose,
            "used_at": None, "created_at": _now(),
        }


def peek_magic_link(token: str, purpose: str = "recover") -> Optional[str]:
    """Validate without burning — see db.peek_magic_link."""
    row = _MAGIC_LINKS.get(token)
    if not row or row.get("used_at"):
        return None
    if (row.get("purpose") or "recover") != purpose:
        return None
    try:
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if datetime.now(timezone.utc) > expires:
        return None
    return row["email"]


def consume_magic_link(token: str, purpose: str = "recover") -> Optional[str]:
    with _lock:
        row = _MAGIC_LINKS.get(token)
        if not row or row.get("used_at"):
            return None
        if (row.get("purpose") or "recover") != purpose:
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


def leagues_for_weekly_send() -> list[dict[str, Any]]:
    import plans
    out = []
    for league in _LEAGUES.values():
        owner = _USERS.get(league.get("user_id") or "")
        if not owner or not plans.plan_for(owner).auto_send:
            continue
        if league.get("auto_send_off"):
            continue
        out.append({**dict(league), "_owner": dict(owner)})
    return out


def mark_emailed(league_id: str, season: int, week: int) -> None:
    with _lock:
        paper = _PAPERS.get((league_id, season, week))
        if paper:
            paper["emailed_at"] = _now()


# ---------------------------------------------------------------------------
# Accounts — same contract as db.py, in dicts.
# ---------------------------------------------------------------------------

def create_user(email: str, password_hash: str) -> Optional[dict[str, Any]]:
    email = (email or "").strip().lower()
    with _lock:
        if any(u["email"] == email for u in _USERS.values()):
            return None   # the unique index, in miniature
        uid = str(uuid4())
        _USERS[uid] = {
            "id": uid, "email": email, "password_hash": password_hash,
            "created_at": _now(), "last_login_at": None, "verified_at": None,
            # Migration 015: a password account has no Google identity. The
            # key must be PRESENT and null rather than absent, or code that
            # reads it gets a KeyError in production and None in demo mode.
            "google_sub": None, "google_email": None, "google_linked_at": None,
            # Migration 014's defaults, so demo mode is on the free tier from
            # the moment an account exists rather than from the first webhook.
            "plan": "free", "plan_status": None,
            "stripe_customer_id": None, "stripe_subscription_id": None,
            "plan_renews_at": None, "plan_updated_at": None,
        }
        return dict(_USERS[uid])


def user_by_email(email: str) -> Optional[dict[str, Any]]:
    email = (email or "").strip().lower()
    return next((dict(u) for u in _USERS.values() if u["email"] == email), None)


def user_by_id(user_id: str) -> Optional[dict[str, Any]]:
    found = _USERS.get(user_id)
    return dict(found) if found else None


# --- Google sign-in, mirroring db.py ----------------------------------------

def user_by_google_sub(sub: str) -> Optional[dict[str, Any]]:
    if not sub:
        return None
    return next((dict(u) for u in _USERS.values()
                 if u.get("google_sub") == sub), None)


def create_google_user(email: str, sub: str,
                       name: str = "") -> Optional[dict[str, Any]]:
    email = (email or "").strip().lower()
    with _lock:
        if any(u["email"] == email for u in _USERS.values()):
            return None
        if any(u.get("google_sub") == sub for u in _USERS.values()):
            return None
        uid = str(uuid4())
        _USERS[uid] = {
            "id": uid, "email": email,
            # No password, which is the entire point of migration 015.
            "password_hash": None,
            "google_sub": sub, "google_email": email,
            "google_linked_at": _now(),
            "created_at": _now(), "last_login_at": None,
            "verified_at": _now(),
            "plan": "free", "plan_status": None,
            "stripe_customer_id": None, "stripe_subscription_id": None,
            "plan_renews_at": None, "plan_updated_at": None,
        }
        return dict(_USERS[uid])


def link_google(user_id: str, sub: str, email: str) -> None:
    with _lock:
        row = _USERS.get(user_id)
        if row:
            row["google_sub"] = sub
            row["google_email"] = (email or "").strip().lower()
            row["google_linked_at"] = _now()


def update_user(user_id: str, fields: dict[str, Any]) -> None:
    with _lock:
        if user_id in _USERS:
            _USERS[user_id].update(fields)


def leagues_for_user(user_id: str) -> list[dict[str, Any]]:
    leagues = [dict(l) for l in _LEAGUES.values() if l.get("user_id") == user_id]
    leagues.sort(key=lambda l: l.get("created_at") or "", reverse=True)
    return leagues


def claim_league(league_id: str, user_id: str) -> None:
    with _lock:
        if league_id in _LEAGUES:
            _LEAGUES[league_id]["user_id"] = user_id


# --- plans, mirroring db.py -------------------------------------------------

def user_by_stripe_customer(customer_id: str) -> Optional[dict[str, Any]]:
    if not customer_id:
        return None
    return next((dict(u) for u in _USERS.values()
                 if u.get("stripe_customer_id") == customer_id), None)


def set_plan(user_id: str, *, plan: str, status: Optional[str] = None,
             subscription_id: Optional[str] = None,
             renews_at: Optional[str] = None) -> None:
    with _lock:
        row = _USERS.get(user_id)
        if row:
            row.update({
                "plan": plan, "plan_status": status,
                "stripe_subscription_id": subscription_id,
                "plan_renews_at": renews_at, "plan_updated_at": _now(),
            })


def remember_stripe_customer(user_id: str, customer_id: str) -> None:
    with _lock:
        row = _USERS.get(user_id)
        if row:
            row["stripe_customer_id"] = customer_id


# ---------------------------------------------------------------------------
# The classifieds page
#
# Mirrors db.py. The demo store is where the publisher page gets clicked
# through before anyone has run migration 012, so `publisher_ads_table_ready`
# is unconditionally true here — in demo mode the table is a dict and it
# always exists.
# ---------------------------------------------------------------------------

_PUBLISHER_ADS: dict[str, dict[str, Any]] = {}


def publisher_ads(season: int, week: int) -> list[dict[str, Any]]:
    rows = [dict(a) for a in _PUBLISHER_ADS.values()
            if a["season"] == int(season) and a["week"] == int(week)]
    # Same ordering as the real one, ties broken the same way, so a reorder
    # that half-applied looks identical in both stores.
    rows.sort(key=lambda a: (a.get("position", 0), a.get("created_at") or ""))
    return rows


def publisher_ads_table_ready() -> bool:
    return True


def add_publisher_ad(season: int, week: int, image_url: str,
                     storage_path_: Optional[str] = None,
                     width: Optional[int] = None,
                     height: Optional[int] = None,
                     caption: str = "", link_url: str = "") -> dict[str, Any]:
    row = {
        "id": str(uuid4()),
        "season": int(season),
        "week": int(week),
        "position": len(publisher_ads(season, week)),
        "image_url": image_url,
        "storage_path": storage_path_,
        "width": int(width) if width else None,
        "height": int(height) if height else None,
        "caption": (caption or "").strip()[:200] or None,
        "link_url": (link_url or "").strip()[:500] or None,
        "created_at": _now(),
    }
    with _lock:
        _PUBLISHER_ADS[row["id"]] = row
    return dict(row)


def delete_publisher_ad(ad_id: str) -> None:
    with _lock:
        row = _PUBLISHER_ADS.pop(ad_id, None)
    path = (row or {}).get("storage_path")
    if path:
        _IMAGES.pop(path.rsplit("/", 1)[-1], None)


def reorder_publisher_ads(season: int, week: int, ordered_ids: list[str]) -> None:
    with _lock:
        for index, ad_id in enumerate(ordered_ids):
            row = _PUBLISHER_ADS.get(ad_id)
            # Scoped exactly as the real one is: an id from another week is
            # ignored rather than dragged into this one.
            if row and row["season"] == int(season) and row["week"] == int(week):
                row["position"] = index


def upload_publisher_image(filename: str, data: bytes,
                           content_type: str = "image/jpeg",
                           season: int = 0, week: int = 0) -> tuple[str, str]:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()[:5]
    name = f"publisher-{secrets.token_urlsafe(8)}.{ext}"
    with _lock:
        _IMAGES[name] = (data, content_type)
    return name, f"/demo-image/{name}"


# ---------------------------------------------------------------------------
# The people in the league — mirrors db.py
# ---------------------------------------------------------------------------

_MANAGERS: dict[tuple, dict[str, Any]] = {}


def get_managers(league_id: str) -> list[dict[str, Any]]:
    rows = [dict(m) for (lid, _h), m in _MANAGERS.items() if lid == league_id]
    # Case-insensitive, to match what Postgres' collation does in db.py —
    # otherwise demo mode lists every capitalised handle first and stops being
    # a truthful preview.
    rows.sort(key=lambda m: (m.get("handle") or "").lower())
    return rows


def remember_managers(league_id: str, handles) -> None:
    wanted = {str(h).strip() for h in (handles or []) if str(h or "").strip()}
    with _lock:
        for handle in sorted(wanted):
            key = (league_id, handle)
            # Never overwrite: this runs every week and must not wipe what the
            # commissioner typed.
            if key not in _MANAGERS:
                _MANAGERS[key] = {
                    "id": str(uuid4()), "league_id": league_id,
                    "handle": handle, "display_name": None, "notes": None,
                    "created_at": _now(), "updated_at": _now(),
                }


def save_manager(league_id: str, handle: str,
                 display_name: str = "", notes: str = "") -> None:
    with _lock:
        row = _MANAGERS.get((league_id, handle))
        if row:
            row["display_name"] = (display_name or "").strip()[:80] or None
            row["notes"] = (notes or "").strip()[:2000] or None
            row["updated_at"] = _now()


# --- the league's own awards (mirrors db.py) --------------------------------

_AWARDS: dict = {}


def get_awards(league_id: str) -> list[dict[str, Any]]:
    with _lock:
        return [dict(a) for a in _AWARDS.values() if a["league_id"] == league_id]


def awards_table_ready() -> bool:
    return True


def add_award(league_id: str, fields: dict) -> None:
    import uuid
    with _lock:
        aid = str(uuid.uuid4())
        _AWARDS[aid] = dict(fields, id=aid, league_id=league_id)


def update_award(league_id: str, award_id: str, fields: dict) -> None:
    with _lock:
        row = _AWARDS.get(award_id)
        if row and row["league_id"] == league_id:
            row.update(fields)


def delete_award(league_id: str, award_id: str) -> None:
    with _lock:
        row = _AWARDS.get(award_id)
        if row and row["league_id"] == league_id:
            del _AWARDS[award_id]
