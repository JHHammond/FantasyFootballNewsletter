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
            "name_style": "team",   # 039
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

        for key in [k for k in list(_LINE_VOTES) if k[0] == league_id]:
            del _LINE_VOTES[key]

        _LEAGUES.pop(league_id, None)


# ---------------------------------------------------------------------------
# Lore
# ---------------------------------------------------------------------------

def get_lore(league_id: str) -> list[dict[str, Any]]:
    return [dict(l) for l in _LORE.values()
            if l["league_id"] == league_id and l["active"]]


def add_lore(league_id: str, text: str, always: bool = False) -> None:
    with _lock:
        lid = str(uuid4())
        _LORE[lid] = {
            "id": lid, "league_id": league_id, "entry": text,
            "active": True, "created_at": _now(), "always": bool(always),
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
            "generated_at": (existing.get("generated_at") if is_edit else None) or _now(),
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


# ---------------------------------------------------------------------------
# Events — same contract as db.log_event / db.events_weekly, in a list.
# Synchronous here, so a test can assert on what was logged.
# ---------------------------------------------------------------------------

_EVENTS: list[dict[str, Any]] = []


def log_event(name: str, league_id: Optional[str] = None,
              props: Optional[dict[str, Any]] = None) -> None:
    with _lock:
        _EVENTS.append({"name": str(name)[:64], "league_id": league_id,
                        "props": props or {}, "occurred_at": _now()})


def events_weekly(weeks: int = 8) -> list[dict[str, Any]]:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    eastern = ZoneInfo("America/New_York")
    today = datetime.now(eastern).date()
    first = today - timedelta(days=today.weekday()) - timedelta(weeks=max(weeks, 1) - 1)
    counts: dict[tuple, dict] = {}
    for e in list(_EVENTS):
        day = datetime.fromisoformat(str(e["occurred_at"]).replace("Z", "+00:00")).astimezone(eastern).date()
        start = day - timedelta(days=day.weekday())
        if start < first:
            continue
        c = counts.setdefault((start, e["name"]), {"events": 0, "leagues": set()})
        c["events"] += 1
        if e.get("league_id"):
            c["leagues"].add(e["league_id"])
    return [{"week_start": start.isoformat(), "name": name, "events": c["events"],
             "leagues": len(c["leagues"])}
            for (start, name), c in sorted(counts.items(), key=lambda kv: (kv[0][0], kv[0][1]),
                                           reverse=True)]


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


_TRIAL: set = set()

#: (league_id, season, week, game, voter) -> side. Migration 041.
_LINE_VOTES: dict = {}


def save_line_vote(league_id: str, season: int, week: int, game: str,
                   voter: str, side: str) -> None:
    with _lock:
        _LINE_VOTES[(league_id, int(season), int(week), game, voter)] = side


def line_votes(league_id: str, season: int, week: int) -> list[dict[str, Any]]:
    return [{"game": g, "voter": v, "side": s}
            for (l, se, w, g, v), s in list(_LINE_VOTES.items())
            if l == league_id and se == int(season) and w == int(week)]


_TRADE_VOTES: dict = {}


def save_trade_vote(league_id: str, season: int, week: int, trade: str,
                    voter: str, side: str) -> None:
    with _lock:
        _TRADE_VOTES[(league_id, int(season), int(week), trade, voter)] = side


def trade_votes(league_id: str, season: int, week: int) -> list[dict[str, Any]]:
    return [{"trade": t, "voter": v, "side": s}
            for (l, se, w, t, v), s in list(_TRADE_VOTES.items())
            if l == league_id and se == int(season) and w == int(week)]


def trial_weeks(provider: str, platform_league_id: str, season: int) -> list[int]:
    return sorted(w for (p, l, s, w) in _TRIAL
                  if p == provider and l == str(platform_league_id) and s == int(season))


def trial_weeks_by_league(season: int) -> dict[tuple, set]:
    out: dict[tuple, set] = {}
    for (p, l, s, w) in _TRIAL:
        if s == int(season):
            out.setdefault((p, str(l)), set()).add(int(w))
    return out


def record_trial_week(provider: str, platform_league_id: str, season: int,
                      week: int) -> None:
    with _lock:
        _TRIAL.add((provider, str(platform_league_id), int(season), int(week)))


def find_league_by_platform_id(provider: str, platform_league_id: str):
    rows = [dict(l) for l in _LEAGUES.values()
            if l.get("provider") == provider
            and l.get("platform_league_id") == str(platform_league_id)]
    rows.sort(key=lambda l: l.get("season") or 0, reverse=True)
    return rows[0] if rows else None


_YAHOO_TOKENS: dict[str, dict[str, Any]] = {}


def get_yahoo_token(user_id: str):
    row = _YAHOO_TOKENS.get(user_id)
    return dict(row) if row else None


def save_yahoo_token(user_id: str, fields: dict[str, Any]) -> None:
    with _lock:
        row = _YAHOO_TOKENS.setdefault(user_id, {"user_id": user_id})
        row.update(fields)


def delete_yahoo_token(user_id: str) -> None:
    with _lock:
        _YAHOO_TOKENS.pop(user_id, None)


def yahoo_tokens_ready() -> bool:
    return True


_TEAM_WEEKS: dict[tuple, dict[str, Any]] = {}
#: Trades (045): headers, the players who moved, and which leagues we
#: have looked at.
_TRADES: dict[tuple, dict[str, Any]] = {}
_TRADE_PLAYERS: dict[tuple, dict[str, Any]] = {}
_TRADE_SCANS: dict[tuple, dict[str, Any]] = {}


def all_leagues(season: int) -> list[dict[str, Any]]:
    return [dict(l) for l in _LEAGUES.values() if l.get("season") == int(season)]


def upsert_team_weeks(rows: list[dict[str, Any]]) -> None:
    with _lock:
        for r in rows:
            _TEAM_WEEKS[(r["league_id"], r["season"], r["week"], r["team_id"])] = dict(r)


def team_week_league_ids(season: int, week: int) -> set[str]:
    return {r["league_id"] for r in _TEAM_WEEKS.values()
            if r["season"] == int(season) and r["week"] == int(week)}


def ppr_league_ids(season: int, week: int) -> set[str]:
    return {r["league_id"] for r in _TEAM_WEEKS.values()
            if r["season"] == int(season) and r["week"] == int(week)
            and r.get("ppr_points") is not None}


def _tw_rows(season, week, where):
    rows = [r for r in _TEAM_WEEKS.values() if r["season"] == int(season)
            and (not week or r["week"] == int(week))]
    if where == "won":
        return [r for r in rows if r["result"] == "W"]
    return [r for r in rows if r["result"] != "BYE" and (r["points"] or 0) > 0]


def team_weeks_top(season, week, column, desc, where, limit=10):
    rows = [r for r in _tw_rows(season, week, where) if r.get(column) is not None]
    rows.sort(key=lambda r: r[column], reverse=desc)
    out = []
    for r in rows[:limit]:
        league = _LEAGUES.get(r["league_id"]) or {}
        out.append(dict(r, leagues={"league_name": league.get("league_name"),
                                    "public_slug": league.get("public_slug")}))
    return out


def team_weeks_summary(season, week):
    import statistics
    rows = _tw_rows(season, week, "played")
    pts = [r["points"] for r in rows]
    return {"teams": len(rows), "leagues": len({r["league_id"] for r in rows}),
            "avg_points": round(sum(pts) / len(pts), 2) if pts else None,
            "median_points": round(statistics.median(pts), 2) if pts else None}


def team_weeks_ready() -> bool:
    return True


_LINEUPS: dict[tuple, dict[str, Any]] = {}
_NFL_PLAYERS: dict[str, dict[str, Any]] = {}


def upsert_lineups(rows: list[dict[str, Any]]) -> None:
    with _lock:
        for r in rows:
            _LINEUPS[(r["league_id"], int(r["season"]), int(r["week"]),
                      r["team_id"], r["player_key"])] = dict(r)


def upsert_nfl_players(rows: list[dict[str, Any]]) -> None:
    with _lock:
        for r in rows:
            _NFL_PLAYERS[r["player_key"]] = dict(r)


def lineups_ready() -> bool:
    return True


def lineup_for_team(league_id: str, season: int, week: int,
                    team_id: str) -> list[dict[str, Any]]:
    cols = ("player_key", "slot", "started", "points", "ppr_points")
    return [{k: r.get(k) for k in cols} for r in _LINEUPS.values()
            if str(r["league_id"]) == str(league_id)
            and int(r["season"]) == int(season) and int(r["week"]) == int(week)
            and str(r["team_id"]) == str(team_id)][:60]


def lineup_league_ids(season: int, week: int) -> set[str]:
    return {r["league_id"] for r in _LINEUPS.values()
            if r["season"] == int(season) and r["week"] == int(week)}


def around_week(season: int, week: int, min_starts: int = 150) -> dict[str, Any]:
    """The SQL function's twin (web.league_stats.compute_week)."""
    from web.league_stats import compute_week
    return compute_week(list(_TEAM_WEEKS.values()), list(_LINEUPS.values()),
                        dict(_NFL_PLAYERS), int(season), int(week), min_starts)


def around_season(season: int) -> dict[str, Any]:
    from web.league_stats import compute_season
    return compute_season(list(_TEAM_WEEKS.values()), int(season))


def top_ppr_game(season: int, week: int) -> dict[str, Any]:
    rows = [r for r in _LINEUPS.values()
            if r["season"] == int(season) and r["week"] == int(week)
            and r.get("started") and r.get("ppr_points") is not None]
    if not rows:
        return {}
    best = max(rows, key=lambda r: float(r["ppr_points"]))
    name = (_NFL_PLAYERS.get(best["player_key"]) or {}).get("name") or best["player_key"]
    return {"name": name, "ppr_points": best["ppr_points"]}


_NFL_NOTES: dict[str, dict[str, Any]] = {}


def nfl_notes(season: int, week: int) -> list[dict[str, Any]]:
    rows = [dict(n) for n in _NFL_NOTES.values()
            if n["season"] == int(season) and n["week"] == int(week)]
    rows.sort(key=lambda n: n["created_at"])
    return rows


def add_nfl_note(season: int, week: int, note: str, all_leagues: bool = False) -> None:
    import uuid
    with _lock:
        nid = str(uuid.uuid4())
        _NFL_NOTES[nid] = {"id": nid, "season": int(season), "week": int(week),
                           "note": note, "all_leagues": bool(all_leagues),
                           "created_at": _now()}


def delete_nfl_note(note_id: str) -> None:
    with _lock:
        _NFL_NOTES.pop(note_id, None)


_NICKNAMES: dict[str, dict[str, Any]] = {}


def player_nicknames() -> list[dict[str, Any]]:
    return sorted((dict(n) for n in _NICKNAMES.values()), key=lambda n: n["player_name"])


def add_player_nickname(player_name: str, nickname: str) -> None:
    import uuid
    with _lock:
        nid = str(uuid.uuid4())
        _NICKNAMES[nid] = {"id": nid, "player_name": player_name,
                           "nickname": nickname, "created_at": _now()}


def delete_player_nickname(nick_id: str) -> None:
    with _lock:
        _NICKNAMES.pop(nick_id, None)


def player_nicknames_ready() -> bool:
    return True


def nfl_notes_ready() -> bool:
    return True


def luck_league(season: int, league_id: str) -> list[dict[str, Any]]:
    from web.luck import compute
    return compute(list(_TEAM_WEEKS.values()), list(_LINEUPS.values()),
                   int(season), league_id)


def luck_national(season: int) -> dict[str, Any]:
    from web.luck import compute, national
    return national(compute(list(_TEAM_WEEKS.values()), list(_LINEUPS.values()),
                            int(season)))


def luck_search(season: int, name: str) -> list[dict[str, Any]]:
    name = (name or "").strip().lower()
    if len(name) < 2:
        return []
    latest: dict = {}
    for r in _TEAM_WEEKS.values():
        if r["season"] == int(season):
            latest[r["league_id"]] = max(latest.get(r["league_id"], 0), r["week"])
    out = []
    for r in _TEAM_WEEKS.values():
        if (r["season"] == int(season) and r["week"] == latest.get(r["league_id"])
                and name in ((r.get("manager") or "").lower(), (r.get("team_name") or "").lower())):
            league = _LEAGUES.get(r["league_id"]) or {}
            out.append({"league_id": r["league_id"], "team_id": r["team_id"],
                        "team_name": r.get("team_name"), "manager": r.get("manager"),
                        "league_name": league.get("league_name"),
                        "public_slug": league.get("public_slug"),
                        "provider": r.get("provider")})
    return sorted(out, key=lambda x: x["league_name"] or "")


def luck_team(season: int, league_id: str, team_id: str) -> dict[str, Any]:
    from web.luck import compute_team
    return compute_team(list(_TEAM_WEEKS.values()), list(_LINEUPS.values()), int(season),
                        league_id, str(team_id), dict(_NFL_PLAYERS))


def luck_ready() -> bool:
    return True


def power_national(season: int) -> dict[str, Any]:
    from web.luck import compute
    from web.rankings import compute as rank, national_json
    rows = list(_TEAM_WEEKS.values())
    return national_json(rank(compute(rows, list(_LINEUPS.values()), int(season)),
                              rows, int(season)))


def power_ready() -> bool:
    return True


def power_view(season: int, league_id: str, team_id: str = "") -> dict[str, Any]:
    """migration 033's power_view, from the same arithmetic."""
    from web.rankings import COLS
    nat = power_national(season)
    rows = [dict(zip(COLS, a)) for a in nat["rows"]]
    dist = [0] * 20
    for r in rows:
        dist[min(int((r["score"] or 0) // 5), 19)] += 1
    me = next((r for r in rows if str(r["league_id"]) == str(league_id)
               and str(r["team_id"]) == str(team_id)), None)
    near = ([r for r in rows if abs(r["rank"] - me["rank"]) <= 12] if me else [])
    return {"teams": len(rows), "through": nat["through"], "dist": dist,
            "top": rows[:25],
            "league": [r for r in rows if str(r["league_id"]) == str(league_id)],
            "near": near}


def power_page(season: int, size: Optional[int] = None, offset: int = 0, limit: int = 50,
               league_id: str = "", team_id: str = "") -> dict[str, Any]:
    """migration 037's power_page, from the same arithmetic."""
    from web.rankings import COLS
    nat = power_national(season)
    rows = [dict(zip(COLS, a)) for a in nat["rows"]]
    sizes: dict = {}
    for r in rows:
        if r.get("team_count") is not None:
            sizes[r["team_count"]] = sizes.get(r["team_count"], 0) + 1
    f = [r for r in rows if size is None or r.get("team_count") == size]
    me = next((i + 1 for i, r in enumerate(f) if str(r["league_id"]) == str(league_id)
               and str(r["team_id"]) == str(team_id)), None)
    offset, limit = max(0, int(offset)), min(max(1, int(limit)), 200)
    page = [{**r, "pos": offset + i + 1} for i, r in enumerate(f[offset:offset + limit])]
    return {"teams": len(rows), "total": len(f), "me": me, "rows": page,
            "sizes": [{"size": k, "n": v} for k, v in sorted(sizes.items())]}


def refresh_power_board(season: int) -> int:
    return len(power_national(season)["rows"])


def sibling_league_ids(provider: str, platform_league_id: str, season: int) -> list[str]:
    return [l["id"] for l in _LEAGUES.values() if l.get("provider") == provider
            and str(l.get("platform_league_id")) == str(platform_league_id)
            and l.get("season") == int(season)]


_EDITOR_BITS: dict[str, dict[str, Any]] = {}


def editor_bits(season: int, week: int) -> list[dict[str, Any]]:
    rows = [dict(b) for b in _EDITOR_BITS.values() if b["season"] == int(season)
            and (b["week"] is None or b["week"] == int(week))]
    rows.sort(key=lambda b: b["created_at"])
    return rows


def add_editor_bit(season, week, kind, players, body) -> None:
    import uuid
    with _lock:
        bid = str(uuid.uuid4())
        _EDITOR_BITS[bid] = {"id": bid, "season": int(season),
                             "week": int(week) if week else None, "kind": kind,
                             "players": list(players), "body": body,
                             "created_at": _now()}


def delete_editor_bit(bit_id: str) -> None:
    with _lock:
        _EDITOR_BITS.pop(bit_id, None)


def editor_bits_ready() -> bool:
    return True


_PLAYER_PHOTOS: dict[str, dict[str, Any]] = {}


def player_photos(season: int, week=None) -> list[dict[str, Any]]:
    rows = [dict(r) for r in _PLAYER_PHOTOS.values() if r["season"] == int(season)]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    if week is None:
        return rows
    return [r for r in rows if r.get("week") in (None, int(week))]


def add_player_photo(fields: dict[str, Any]) -> None:
    import uuid
    with _lock:
        pid = str(uuid.uuid4())
        _PLAYER_PHOTOS[pid] = dict(fields, id=pid, created_at=_now())


def delete_player_photo(photo_id: str) -> None:
    with _lock:
        row = _PLAYER_PHOTOS.pop(photo_id, None)
        if row and row.get("storage_path"):
            _IMAGES.pop(row["storage_path"], None)


def upload_player_photo(filename: str, data: bytes, content_type: str,
                        season: int) -> tuple[str, str]:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()[:5]
    name = f"desk-{secrets.token_urlsafe(8)}.{ext}"
    with _lock:
        _IMAGES[name] = (data, content_type)
    return name, f"/demo-image/{name}"


def player_photos_ready() -> bool:
    return True


def count_leagues() -> int:
    return len(_LEAGUES)


def national_ranks(season, week, points, league_avg, league_id):
    pool = [r for r in _TEAM_WEEKS.values()
            if r["season"] == int(season) and r["week"] == int(week)
            and r["result"] != "BYE" and (r["points"] or 0) > 0
            and r["league_id"] != league_id]
    by_league: dict = {}
    for r in pool:
        by_league.setdefault(r["league_id"], []).append(r["points"])
    avgs = [sum(v) / len(v) for v in by_league.values()]
    return {"teams": len(pool), "leagues": len(by_league),
            "above": [sum(1 for r in pool if r["points"] > x) for x in points],
            "leagues_above": sum(1 for a in avgs if a > league_avg)}


def leagues_for_weekly_send() -> list[dict[str, Any]]:
    import plans
    from web.db import _weekly_rows
    owners = {u["id"]: dict(u) for u in _USERS.values() if plans.plan_for(u).auto_send}
    by_email = {(u.get("email") or "").strip().lower(): u for u in owners.values()}
    this = [dict(l) for l in _LEAGUES.values()]
    linked = [l for l in this if l.get("user_id") in owners]
    unlinked = [l for l in this if not l.get("user_id")
                and (l.get("owner_email") or "").strip().lower() in by_email]
    return _weekly_rows(linked, unlinked, owners, by_email)


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


_FOLLOWERS: dict = {}


def follow_league(user_id: str, league_id: str, team_id: Optional[str] = None) -> None:
    with _lock:
        row = _FOLLOWERS.setdefault((user_id, league_id), {"user_id": user_id, "league_id": league_id})
        if team_id:
            row["team_id"] = str(team_id)


def market_leagues_for_user(user_id: str) -> list[dict[str, Any]]:
    own = leagues_for_user(user_id)
    seen = {str(l["id"]) for l in own}
    followed = [dict(_LEAGUES[r["league_id"]], my_team_id=r.get("team_id"), followed=True)
                for (u, lid), r in _FOLLOWERS.items()
                if u == user_id and lid in _LEAGUES and str(lid) not in seen]
    return own + followed


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


def remember_managers(league_id: str, handles, teams: dict | None = None) -> None:
    wanted = {str(h).strip() for h in (handles or []) if str(h or "").strip()}
    teams = {str(k).strip(): (str(v).strip() or None) for k, v in (teams or {}).items()}
    with _lock:
        for handle in sorted(wanted):
            key = (league_id, handle)
            # Never overwrite: this runs every week and must not wipe what the
            # commissioner typed. The team name is the platform's, so it may.
            if key not in _MANAGERS:
                _MANAGERS[key] = {
                    "id": str(uuid4()), "league_id": league_id,
                    "handle": handle, "display_name": None, "notes": None,
                    "team_name": teams.get(handle),
                    "created_at": _now(), "updated_at": _now(),
                }
            elif teams.get(handle):
                _MANAGERS[key]["team_name"] = teams[handle]


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


# --- reminder emails, mirroring db.py ----------------------------------------
_OPTOUTS: set = set()
_REMINDERS: set = set()


def market_only_league_ids(season: int) -> set:
    return {l["id"] for l in _LEAGUES.values()
            if int(l.get("season") or 0) == int(season) and l.get("market_only")}


def leagues_for_reminders(season: int) -> list[dict[str, Any]]:
    return [dict(l) for l in _LEAGUES.values() if l.get("season") == int(season)]


def all_users() -> list[dict[str, Any]]:
    return [dict(u) for u in _USERS.values()]


def league_ids_with_paper(season: int, week: int) -> set:
    return {k[0] for k in _PAPERS if k[1] == int(season) and k[2] == int(week)}


def email_optouts() -> set:
    out = {e.strip().lower() for e in _OPTOUTS}
    out |= {(s.get("email") or "").strip().lower() for s in _SUBSCRIBERS.values()
            if s.get("unsubscribed_at")}
    out.discard("")
    return out


def add_email_optout(email: str) -> None:
    _OPTOUTS.add(email.strip().lower())


def reminders_sent(campaign: str) -> set:
    return {e for e, c in _REMINDERS if c == campaign}


def record_reminder(email: str, campaign: str) -> None:
    _REMINDERS.add((email.strip().lower(), campaign))


def reminders_ready() -> bool:
    return True


# --- Trades (045) -----------------------------------------------------------

def upsert_trades(heads: list[dict[str, Any]], players: list[dict[str, Any]]) -> None:
    with _lock:
        for r in heads:
            _TRADES[(r["league_id"], int(r["season"]), r["trade_key"])] = dict(r)
        for r in players:
            _TRADE_PLAYERS[(r["league_id"], int(r["season"]), r["trade_key"],
                            r["player_key"])] = dict(r)


def upsert_trade_scan(row: dict[str, Any]) -> None:
    with _lock:
        _TRADE_SCANS[(row["league_id"], int(row["season"]), int(row["week"]))] = dict(row)


def trades_ready() -> bool:
    return True


def trade_scan_league_ids(season: int, week: int) -> set[str]:
    return {r["league_id"] for r in _TRADE_SCANS.values()
            if int(r["season"]) == int(season) and int(r["week"]) == int(week)}


def trade_volume(season: int) -> dict[str, Any]:
    """The SQL function's twin (migration 045)."""
    season = int(season)
    scans = [r for r in _TRADE_SCANS.values() if int(r["season"]) == season]
    moves = [r for r in _TRADE_PLAYERS.values() if int(r["season"]) == season]
    weeks = []
    for wk in sorted({int(r["week"]) for r in scans}):
        mine = [r for r in moves if int(r["week"]) == wk]
        weeks.append({
            "week": wk,
            "leagues": sum(1 for r in scans if int(r["week"]) == wk),
            "trades": sum(int(r.get("trades") or 0) for r in scans if int(r["week"]) == wk),
            "players": len(mine),
            "resolved": sum(1 for r in mine if _resolved(r["player_key"])),
        })
    busiest: dict = {}
    for r in moves:
        e = busiest.setdefault(r["player_key"], {"player_key": r["player_key"],
                                                 "name": r.get("name") or "", "moves": 0})
        e["moves"] += 1
        e["name"] = max(e["name"], r.get("name") or "")
    return {
        "season": season, "weeks": weeks,
        "trades": len({(r["league_id"], r["trade_key"]) for r in _TRADES.values()
                       if int(r["season"]) == season}),
        "players": len({r["player_key"] for r in moves}),
        "busiest": sorted(busiest.values(),
                          key=lambda e: (-e["moves"], e["player_key"]))[:10],
    }


def _resolved(key: str) -> bool:
    """A Sleeper id (digits) or a defense (a team code), the way the SQL
    function tests it."""
    k = str(key or "")
    return k.isdigit() or (2 <= len(k) <= 3 and k.isalpha() and k.isupper())


def trade_exchanges(season: int, from_week: int = 1,
                    to_week: int = 18) -> list[dict[str, Any]]:
    """Redraft leagues' trades (059, 062). See _exchanges."""
    gone = dynasty_league_ids(season)
    return _exchanges(season, from_week, to_week, lambda lid: str(lid) not in gone)


def dynasty_trade_exchanges(season: int, from_week: int = 1,
                            to_week: int = 18) -> list[dict[str, Any]]:
    keep = dynasty_league_ids(season)
    return _exchanges(season, from_week, to_week, lambda lid: str(lid) in keep)


def _exchanges(season: int, from_week: int, to_week: int, keep) -> list[dict[str, Any]]:
    """The SQL function's twin (migrations 046 and 047): player-only,
    two-sided, each with the DAY it happened.

    The day is not decoration. It went missing here when 047 added it to
    the SQL and this twin was not followed through, and the symptom was
    subtle: every trade fell back to the middle of its own week, so the
    daily line became a staircase that changed once every seven days and
    looked like a quiet market rather than a broken one."""
    season = int(season)
    pure = {(r["league_id"], r["trade_key"]): r for r in _TRADES.values()
            if int(r["season"]) == season
            and int(from_week) <= int(r["week"]) <= int(to_week)
            and not r.get("picks") and not r.get("faab") and keep(r["league_id"])}
    sides: dict = {}
    for r in _TRADE_PLAYERS.values():
        key = (r["league_id"], r["trade_key"])
        if int(r["season"]) != season or key not in pure or not r.get("to_team"):
            continue
        sides.setdefault(key, {}).setdefault(str(r["to_team"]), []).append(r["player_key"])
    out = []
    for key, by_team in sides.items():
        if len(by_team) != 2:
            continue
        a, b = (sorted(by_team[t]) for t in sorted(by_team))
        when = pure[key].get("traded_at")
        out.append({"w": int(pure[key]["week"]), "a": a, "b": b,
                    "d": str(when)[:10] if when else None})
    return out


def trade_partners(season: int, player_key: str, limit: int = 8) -> dict[str, Any]:
    """The SQL function's twin (migration 047)."""
    season, key = int(season), str(player_key)
    mine = {(r["league_id"], r["trade_key"]): r for r in _TRADE_PLAYERS.values()
            if int(r["season"]) == season and r["player_key"] == key}
    against: dict = {}
    with_him: dict = {}
    for r in _TRADE_PLAYERS.values():
        tk = (r["league_id"], r["trade_key"])
        if int(r["season"]) != season or tk not in mine or r["player_key"] == key:
            continue
        side = against if r.get("to_team") != mine[tk].get("to_team") else with_him
        e = side.setdefault(r["player_key"], {
            "player_key": r["player_key"], "name": r.get("name") or r["player_key"],
            "position": r.get("position"), "nfl_team": r.get("nfl_team"), "n": 0})
        e["n"] += 1

    def rank(d, n, nfl=True):
        rows = sorted(d.values(), key=lambda e: (-e["n"], e["player_key"]))[:n]
        # `alongside` carries no nfl_team in the SQL, and the twin must
        # match it field for field or scripts/check_trade_sql.py is only
        # checking the fields it happens to share.
        return rows if nfl else [{k: v for k, v in r.items() if k != "nfl_team"}
                                 for r in rows]

    recent = []
    heads = [(h, mine[(h["league_id"], h["trade_key"])])
             for h in _TRADES.values()
             if int(h["season"]) == season and (h["league_id"], h["trade_key"]) in mine]
    heads.sort(key=lambda hm: (str(hm[0].get("traded_at") or ""), int(hm[0]["week"]),
                               hm[0]["trade_key"]), reverse=True)
    for head, me in heads[:6]:
        got, gave = [], []
        for r in _TRADE_PLAYERS.values():
            if (r["league_id"], r["trade_key"]) != (head["league_id"], head["trade_key"]):
                continue
            row = {"name": r.get("name") or r["player_key"], "position": r.get("position")}
            (got if r.get("to_team") == me.get("to_team") else gave).append(row)
        key_of = lambda x: x["name"]  # noqa: E731
        # Labels and amounts only — the team that sent the pick stays in
        # its league. See migration 051.
        picks = [p[1] for p in (head.get("picks") or [])
                 if isinstance(p, (list, tuple)) and len(p) > 1 and p[1] is not None]
        faab = [f[2] for f in (head.get("faab") or [])
                if isinstance(f, (list, tuple)) and len(f) > 2 and f[2] is not None]
        recent.append({"week": int(head["week"]),
                       "day": (str(head.get("traded_at") or "")[:10]) or None,
                       "got": sorted(got, key=key_of), "gave": sorted(gave, key=key_of),
                       "picks": picks, "faab": faab})
    return {"player": key, "trades": len(mine),
            "counterparts": rank(against, limit),
            "alongside": rank(with_him, 4, nfl=False),
            "recent": recent}


def lineup_for_league(league_id: str, season: int, week: int) -> list[dict[str, Any]]:
    """The Supabase query's twin: every roster in a league for a week."""
    return [{"team_id": l["team_id"], "player_key": l["player_key"],
             "slot": l.get("slot"), "started": l.get("started")}
            for l in _LINEUPS.values()
            if str(l["league_id"]) == str(league_id) and int(l["season"]) == int(season)
            and int(l["week"]) == int(week)]


def recent_trades(season: int, limit: int = 400) -> list[dict[str, Any]]:
    """The SQL function's twin (migration 060)."""
    import hashlib
    season = int(season)
    dyn = dynasty_league_ids(season)
    heads = sorted((h for h in _TRADES.values() if int(h["season"]) == season and h.get("traded_at")),
                   key=lambda h: h["traded_at"], reverse=True)[:limit]
    out = []
    for h in heads:
        sides = []
        for team in h.get("teams") or []:
            sides.append({
                "players": [{"player_key": r["player_key"], "name": r.get("name"), "position": r.get("position")}
                            for r in sorted(_TRADE_PLAYERS.values(), key=lambda r: r["player_key"])
                            if r["league_id"] == h["league_id"] and r["trade_key"] == h["trade_key"]
                            and int(r["season"]) == season and r.get("to_team") == team],
                "picks": [p[1] for p in h.get("picks") or [] if p and p[0] == team and len(p) > 1],
                "faab": sum(float(f[2]) for f in h.get("faab") or [] if len(f) > 2 and f[1] == team)})
        out.append({"key": hashlib.md5(f"{h['league_id']}:{h['trade_key']}".encode()).hexdigest(),
                    "week": h["week"], "at": h["traded_at"], "provider": h.get("provider"),
                    "team_count": h.get("team_count"), "scoring_type": h.get("scoring_type"),
                    "format": "dynasty" if str(h["league_id"]) in dyn else "redraft", "sides": sides})
    return out


def sleeper_leagues(season: int) -> list[dict[str, Any]]:
    return [l for l in all_leagues(season) if l.get("provider") == "sleeper"]


def player_trades(season: int, player_key: str) -> dict[str, Any]:
    """The SQL function's twin (migration 056)."""
    import hashlib
    season, key = int(season), str(player_key)
    mine = {(r["league_id"], r["trade_key"]): r for r in _TRADE_PLAYERS.values()
            if int(r["season"]) == season and r["player_key"] == key}
    heads = [h for h in _TRADES.values()
             if int(h["season"]) == season and (h["league_id"], h["trade_key"]) in mine]
    heads.sort(key=lambda h: (h.get("traded_at") is not None, str(h.get("traded_at") or ""),
                              int(h["week"])), reverse=True)
    out = []
    for h in heads:
        tk = (h["league_id"], h["trade_key"])
        to = mine[tk].get("to_team")
        rows = [r for r in _TRADE_PLAYERS.values()
                if (r["league_id"], r["trade_key"]) == tk and int(r["season"]) == season]
        pl = lambda r: {"player_key": r["player_key"], "name": r.get("name") or r["player_key"],  # noqa: E731
                        "position": r.get("position")}
        picks = [p for p in (h.get("picks") or []) if isinstance(p, (list, tuple)) and len(p) > 1 and p[1]]
        faab = [f for f in (h.get("faab") or []) if isinstance(f, (list, tuple)) and len(f) > 2 and f[2] is not None]
        out.append({
            "key": hashlib.md5(f"{h['league_id']}:{h['trade_key']}".encode()).hexdigest(),
            "week": int(h["week"]),
            "day": (str(h.get("traded_at") or "")[:10]) or None,
            "provider": h.get("provider"), "team_count": h.get("team_count"),
            "scoring_type": h.get("scoring_type"),
            "teams": len({r.get("to_team") for r in rows}),
            "with": sorted((pl(r) for r in rows if r["player_key"] != key and r.get("to_team") == to),
                           key=lambda x: x["player_key"]),
            "gave": sorted((pl(r) for r in rows if r.get("to_team") != to),
                           key=lambda x: x["player_key"]),
            "got_picks": [p[1] for p in picks if p[0] == to],
            "gave_picks": [p[1] for p in picks if p[0] != to],
            "got_faab": sum(f[2] for f in faab if f[1] == to),
            "gave_faab": sum(f[2] for f in faab if f[1] != to),
        })
    return {"player": key, "trades": out}


def _Dec(x):
    from decimal import Decimal
    return Decimal(x)


def _round2(x) -> float:
    """Round like Postgres numeric does, not like Python does.

    Two separate differences, both found by scripts/check_trade_sql.py and
    neither visible on a page:

      * Postgres rounds a half AWAY FROM ZERO; Python rounds it to the
        nearest even digit. 128.94 / 12 is 10.745, which is 10.75 there and
        10.74 here.
      * Postgres sums `numeric` EXACTLY; a column of floats drifts, so the
        total was 193.82999999999998 before the division had a chance.

    So the twin accumulates in Decimal and rounds half-up, and the two
    copies agree to the digit — which is the only way a drift checker can
    say anything useful.
    """
    from decimal import ROUND_HALF_UP, Decimal
    d = x if isinstance(x, Decimal) else Decimal(repr(float(x)))
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


_DRAFT_PICKS: dict[tuple, dict[str, Any]] = {}
_DRAFT_SCANS: dict[tuple, dict[str, Any]] = {}


def upsert_draft_picks(rows: list[dict[str, Any]]) -> None:
    with _lock:
        for r in rows:
            _DRAFT_PICKS[(r["league_id"], int(r["season"]), r["player_key"])] = dict(r)


def upsert_draft_scan(row: dict[str, Any]) -> None:
    with _lock:
        _DRAFT_SCANS[(row["league_id"], int(row["season"]))] = dict(row)


def drafts_ready() -> bool:
    return True


#: Who played in each NFL week (055): (season, week, player_key) -> row.
_WEEK_PLAYERS: dict = {}


def week_players_ready() -> bool:
    return True


def upsert_week_players(rows: list[dict[str, Any]]) -> None:
    with _lock:
        for r in rows:
            _WEEK_PLAYERS[(int(r["season"]), int(r["week"]), r["player_key"])] = dict(r)


def draft_scan_league_ids(season: int) -> set:
    return {r["league_id"] for r in _DRAFT_SCANS.values()
            if int(r["season"]) == int(season)}


def adp(season: int, min_drafts: int = 20) -> dict[str, Any]:
    """Redraft ADP: every league's draft except dynasty leagues' (059, 062)."""
    gone = dynasty_league_ids(season)
    return _adp(season, min_drafts, lambda lid: str(lid) not in gone)


def dynasty_adp(season: int, min_drafts: int = 5) -> dict[str, Any]:
    """The twin of 062: dynasty STARTUP drafts only."""
    keep = _dynasty_startups(season)
    out = _adp(season, min_drafts, lambda lid: str(lid) in keep)
    out["drafts"] = len(keep)
    return out


def _adp(season: int, min_drafts: int, keep) -> dict[str, Any]:
    """The twin of migration 050.

    Slots are normalised to a twelve-team league before anything else:
    pick 11 of ten teams is the last of round one, pick 11 of fourteen is
    the middle of it, and averaging them raw would blur the two.
    """
    import statistics
    season, floor_n = int(season), int(min_drafts)
    slots: dict[str, list] = {}
    meta: dict[str, dict] = {}
    for r in _DRAFT_PICKS.values():
        if int(r["season"]) != season or int(r.get("overall") or 0) <= 0 \
                or not keep(r["league_id"]):
            continue
        size = int(r.get("team_count") or 12) or 12
        slots.setdefault(r["player_key"], []).append(
            (int(r["overall"]) - 1) * 12.0 / size + 1)
        m = meta.setdefault(r["player_key"], {})
        for k in ("name", "position", "nfl_team"):
            if r.get(k) and not m.get(k):
                m[k] = r[k]

    def pct(xs, q):
        """percentile_cont, which interpolates — not the nearest sample."""
        s = sorted(xs)
        if len(s) == 1:
            return s[0]
        i = q * (len(s) - 1)
        lo = int(i)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (i - lo)

    rows = []
    for key, xs in slots.items():
        if len(xs) < floor_n:
            continue
        known = _NFL_PLAYERS.get(key) or {}
        m = meta.get(key) or {}
        rows.append({
            "player_key": key,
            "name": known.get("name") or m.get("name") or key,
            "position": known.get("position") or m.get("position"),
            "nfl_team": known.get("nfl_team") or m.get("nfl_team"),
            "drafts": len(xs),
            "adp": _round2(sum(xs) / len(xs)),
            "median": _round2(pct(xs, 0.5)),
            "early": _round2(pct(xs, 0.10)),
            "late": _round2(pct(xs, 0.90)),
            "spread": _round2(statistics.stdev(xs) if len(xs) > 1 else 0.0)})
    rows.sort(key=lambda r: (r["adp"], r["player_key"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    allpicks = [r for r in _DRAFT_PICKS.values() if int(r["season"]) == season]
    import re as _re
    resolved = sum(1 for r in allpicks
                   if _re.fullmatch(r"[0-9]+", r["player_key"])
                   or _re.fullmatch(r"[A-Z]{2,3}", r["player_key"]))
    return {
        "season": season, "min_drafts": floor_n,
        "drafts": sum(1 for s in _DRAFT_SCANS.values()
                      if int(s["season"]) == season and int(s.get("picks") or 0) > 0),
        "scanned": sum(1 for s in _DRAFT_SCANS.values() if int(s["season"]) == season),
        "picks": len(allpicks),
        "resolved": resolved,
        "players": rows,
    }


def trade_backfill(season: int) -> dict[str, Any]:
    """The twin of migration 049."""
    season = int(season)
    grid: dict[tuple, dict] = {}

    def cell(provider, week):
        return grid.setdefault((str(provider), int(week)),
                               {"provider": str(provider), "week": int(week),
                                "leagues": 0, "trades": 0, "usable": 0, "dated": 0})

    for s in _TRADE_SCANS.values():
        if int(s["season"]) != season:
            continue
        # An inner join in the SQL, so a scan whose league has gone is
        # dropped rather than filed under an unknown platform.
        lg = _LEAGUES.get(s["league_id"])
        if not lg or not lg.get("provider"):
            continue
        cell(lg["provider"], s["week"])["leagues"] += 1
    for t in _TRADES.values():
        if int(t["season"]) != season:
            continue
        c = cell(t.get("provider") or "?", t["week"])
        c["trades"] += 1
        if not t.get("picks") and not t.get("faab"):
            c["usable"] += 1
        if t.get("traded_at"):
            c["dated"] += 1

    rows = sorted(grid.values(), key=lambda r: (r["provider"], r["week"]))
    providers: dict[str, dict] = {}
    for r in rows:
        p = providers.setdefault(r["provider"], {
            "provider": r["provider"], "weeks": 0, "leagues": 0, "trades": 0,
            "usable": 0, "dated": 0, "empty_weeks": 0})
        p["weeks"] += 1
        for k in ("leagues", "trades", "usable", "dated"):
            p[k] += r[k]
        if r["trades"] == 0 and r["leagues"] > 0:
            p["empty_weeks"] += 1

    days = sorted(str(t["traded_at"])[:10] for t in _TRADES.values()
                  if int(t["season"]) == season and t.get("traded_at"))
    return {
        "season": season, "rows": rows,
        "providers": sorted(providers.values(), key=lambda p: p["provider"]),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "undated": sum(1 for t in _TRADES.values()
                       if int(t["season"]) == season and not t.get("traded_at")),
    }


def trade_prior(season: int, from_week: int = 1, to_week: int = 18) -> dict[str, Any]:
    """The twin of migration 048: usage per player, out of the demo's own
    lineups. Aggregates only — the arithmetic is in web/trade_prior.py, so
    there is one copy of that and this cannot drift from it."""
    season, lo, hi = int(season), int(from_week), int(to_week)
    tally: dict[str, dict[str, float]] = {}
    for r in _LINEUPS.values():
        if int(r["season"]) != season or not (lo <= int(r["week"]) <= hi):
            continue
        t = tally.setdefault(r["player_key"],
                             {"rostered": 0, "started": 0,
                              "delivered": _Dec("0"), "scored": 0})
        t["rostered"] += 1
        if r.get("started"):
            t["started"] += 1
            ppr = r.get("ppr_points")
            if ppr is not None:
                # Decimal, not float: Postgres sums `numeric` exactly, and a
                # column of floats drifts far enough to change the second
                # decimal place after division. See _round2.
                t["delivered"] += _Dec(str(ppr))
                t["scored"] += 1
    out = {}
    for key, t in tally.items():
        known = _NFL_PLAYERS.get(key) or {}
        out[key] = {
            "position": known.get("position"),
            "rostered": int(t["rostered"]),
            "started": int(t["started"]),
            "delivered": _round2(t["delivered"]),
            "per_start": (_round2(t["delivered"] / t["scored"])
                          if t["scored"] else None)}
    return out


def trade_player_names(season: int) -> dict[str, Any]:
    season = int(season)
    out: dict[str, dict] = {}
    for r in _TRADE_PLAYERS.values():
        if int(r["season"]) != season:
            continue
        known = _NFL_PLAYERS.get(r["player_key"]) or {}
        out[r["player_key"]] = {
            "name": known.get("name") or r.get("name") or r["player_key"],
            "position": known.get("position") or r.get("position"),
            "nfl_team": known.get("nfl_team") or r.get("nfl_team")}
    return out


def team_weeks_for_leagues(season: int, week: int,
                           league_ids: list[str]) -> list[dict[str, Any]]:
    keep = {str(i) for i in (league_ids or []) if i}
    cols = ("league_id", "team_id", "team_name", "points", "ppr_points", "scoring_type")
    return [{k: r.get(k) for k in cols} for r in _TEAM_WEEKS.values()
            if str(r["league_id"]) in keep and int(r["season"]) == int(season)
            and int(r["week"]) == int(week)]


def team_weeks_for_week(season: int, week: int) -> list[dict[str, Any]]:
    return [dict(r) for r in _TEAM_WEEKS.values()
            if r.get("season") == int(season) and r.get("week") == int(week)]


def papers_for_week(season: int, week: int) -> list[dict[str, Any]]:
    out = []
    for (lid, s, w), p in _PAPERS.items():
        if s == int(season) and w == int(week):
            lg = _LEAGUES.get(lid, {})
            out.append({**dict(p), "league_id": lid, "season": s, "week": w,
                        "leagues": {k: lg.get(k) for k in
                                    ("public_slug", "league_name", "paper_name", "user_id")}})
    return out


def newspapers_for_stats() -> list[dict[str, Any]]:
    return [{"league_id": k[0], "season": k[1], "week": k[2],
             "view_count": p.get("view_count") or 0,
             "emailed_at": p.get("emailed_at")}
            for k, p in _PAPERS.items()]


def count_users() -> int:
    return len(_USERS)


def count_subscribers() -> int:
    return sum(1 for s in _SUBSCRIBERS.values() if s.get("confirmed"))


def count_trial_papers(season: int) -> int:
    return 0


# My Team (/team): the twins of db.py's league_* reads.

def league_lineups(league_id: str, season: int) -> list[dict[str, Any]]:
    return [{k: l.get(k) for k in ("team_id", "week", "player_key", "slot", "started", "points")}
            for l in _LINEUPS.values()
            if str(l["league_id"]) == str(league_id) and int(l["season"]) == int(season)]


def league_team_weeks(league_id: str, season: int) -> list[dict[str, Any]]:
    return [{k: r.get(k) for k in ("team_id", "week", "team_name", "points")}
            for r in _TEAM_WEEKS.values()
            if str(r["league_id"]) == str(league_id) and int(r["season"]) == int(season)]


def league_trades(league_id: str, season: int) -> list[dict[str, Any]]:
    out = []
    for (lid, s, tk, _), r in _TRADE_PLAYERS.items():
        if str(lid) != str(league_id) or int(s) != int(season):
            continue
        head = _TRADES.get((lid, s, tk)) or {}
        out.append({k: r.get(k) for k in ("trade_key", "week", "player_key", "name", "position",
                                          "from_team", "to_team")}
                   | {"traded_at": head.get("traded_at")})
    return out


def league_draft(league_id: str, season: int) -> list[dict[str, Any]]:
    return [{k: r.get(k) for k in ("player_key", "overall", "round", "team_id", "team_count", "position")}
            for r in _DRAFT_PICKS.values()
            if str(r["league_id"]) == str(league_id) and int(r["season"]) == int(season)]


def player_positions(keys: list[str]) -> dict[str, str]:
    return {k: (_NFL_PLAYERS.get(k) or {}).get("position") for k in keys or []
            if (_NFL_PLAYERS.get(k) or {}).get("position")}


# Dynasty and redraft (062): the twins.

def _draft_per_team(season: int) -> dict[str, float]:
    count: dict[str, int] = {}
    size: dict[str, int] = {}
    for r in _DRAFT_PICKS.values():
        if int(r["season"]) != int(season):
            continue
        lid = str(r["league_id"])
        count[lid] = count.get(lid, 0) + 1
        size[lid] = max(size.get(lid, 0), int(r.get("team_count") or 12) or 12)
    return {lid: count[lid] / size[lid] for lid in count}


def dynasty_league_ids(season: int) -> set[str]:
    out = set()
    for lid, l in _LEAGUES.items():
        lt = l.get("league_type")
        if lt == "dynasty" or (lt is None and l.get("format") == "dynasty"):
            out.add(str(lid))
    for lid, per in _draft_per_team(season).items():
        if (_LEAGUES.get(lid) or {}).get("league_type") is None and per <= 6:
            out.add(lid)
    return out


def _dynasty_startups(season: int) -> set[str]:
    dyn = dynasty_league_ids(season)
    return {lid for lid, per in _draft_per_team(season).items() if lid in dyn and per >= 10}


def set_league_type(league_id: str, league_type: Optional[str]) -> bool:
    if league_type not in ("redraft", "keeper", "dynasty"):
        return False
    with _lock:
        if league_id in _LEAGUES:
            _LEAGUES[league_id]["league_type"] = league_type
    return True
