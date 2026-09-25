"""
Sign in with Yahoo — for reading a league, not for signing in to this site.

Google sign-in (web/oauth.py) proves who somebody is and then throws the
credential away. This is the opposite: Yahoo will not show us a league unless
we present a token belonging to someone in it, EVERY time — including at
6am on a Tuesday when the weekly job runs and nobody is signed in. So this
file keeps a credential, which is the one thing Google sign-in was careful
never to do. That changes what it has to defend.

WHAT IS STORED, AND HOW

One row per account in `yahoo_tokens` (migration 020): the refresh token,
the current access token and when it expires. Both tokens are encrypted with
Fernet before they reach the database, under a key derived from
YAHOO_TOKEN_KEY (or SESSION_SECRET when that isn't set). A leaked database
dump is then a list of ciphertext. Access tokens live an hour; the refresh
token is what matters, and Yahoo lets the person revoke it from their own
account settings at any time.

The scope is read-only (fspt-r, chosen on the app's Yahoo developer page).
This app can never change a lineup or make a move.

THE FLOW

  /connect/yahoo/start     signed state cookie, redirect to Yahoo
  /connect/yahoo/callback  state check, code -> tokens, store, back to picker
  providers.yahoo          asks `access_token_for_league` for every request

UNCONFIGURED IS A SUPPORTED STATE, same as Google and Stripe: no
YAHOO_CLIENT_ID means Yahoo stays on the waiting list.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import requests

AUTH_ENDPOINT = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_ENDPOINT = "https://api.login.yahoo.com/oauth2/get_token"

REQUEST_TIMEOUT = 15
STATE_MAX_AGE = 15 * 60
STATE_COOKIE = "cd_yahoo_state"

#: Refresh this long before Yahoo says the token expires, so a request that
#: starts at 59:58 doesn't arrive at 60:01.
REFRESH_MARGIN = 5 * 60


class YahooAuthError(Exception):
    """Shown to the person. Never contains a token or Yahoo's raw reply."""


class NotConfigured(YahooAuthError):
    pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def client_id() -> str:
    value = os.getenv("YAHOO_CLIENT_ID", "").strip()
    if not value:
        raise NotConfigured("YAHOO_CLIENT_ID is not set")
    return value


def client_secret() -> str:
    value = os.getenv("YAHOO_CLIENT_SECRET", "").strip()
    if not value:
        raise NotConfigured("YAHOO_CLIENT_SECRET is not set")
    return value


def configured() -> bool:
    return bool(os.getenv("YAHOO_CLIENT_ID", "").strip()
                and os.getenv("YAHOO_CLIENT_SECRET", "").strip())


def redirect_uri(base_url: str) -> str:
    """Must match the Redirect URI on the Yahoo app EXACTLY, or Yahoo
    refuses before the person ever sees a consent screen."""
    return f"{(base_url or '').rstrip('/')}/connect/yahoo/callback"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def new_state() -> str:
    return f"{secrets.token_urlsafe(24)}.{int(time.time())}"


def state_is_fresh(state: str, now: Optional[float] = None) -> bool:
    try:
        issued = int((state or "").rsplit(".", 1)[-1])
    except (ValueError, IndexError):
        return False
    return 0 <= (now or time.time()) - issued <= STATE_MAX_AGE


def consent_url(state: str, base_url: str) -> str:
    return AUTH_ENDPOINT + "?" + urlencode({
        "client_id": client_id(),
        "redirect_uri": redirect_uri(base_url),
        "response_type": "code",
        "state": state,
        "language": "en-us",
    })


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------

def _fernet():
    from cryptography.fernet import Fernet

    secret = (os.getenv("YAHOO_TOKEN_KEY", "").strip()
              or os.getenv("SESSION_SECRET", "").strip())
    if not secret:
        # Same stance as the session secret: works locally, loudly.
        from . import auth
        secret = auth.session_secret().decode("latin-1")
    key = base64.urlsafe_b64encode(
        hashlib.sha256(("yahoo-tokens:" + secret).encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except Exception:  # noqa: BLE001 — a rotated key reads as "no token"
        return ""


# ---------------------------------------------------------------------------
# Talking to Yahoo's token endpoint
# ---------------------------------------------------------------------------

def _token_request(data: dict) -> dict:
    basic = base64.b64encode(f"{client_id()}:{client_secret()}".encode()).decode()
    try:
        response = requests.post(
            TOKEN_ENDPOINT, data=data,
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise YahooAuthError("Couldn't reach Yahoo. Try again.") from exc
    if response.status_code != 200:
        print(f"[yahoo] token request failed: {response.status_code} "
              f"{response.text[:200]}", flush=True)
        raise YahooAuthError("Yahoo wouldn't complete that sign-in. Try again.")
    body = response.json() or {}
    if not body.get("access_token"):
        raise YahooAuthError("Yahoo's reply had no access token in it.")
    return body


def exchange_code(code: str, base_url: str) -> dict:
    if not code:
        raise YahooAuthError("Yahoo sent us back without an authorization code.")
    return _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(base_url),
    })


def refresh(refresh_token: str, base_url: str = "") -> dict:
    return _token_request({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "redirect_uri": redirect_uri(base_url or _base_url()),
    })


def _base_url() -> str:
    from .generate import public_base_url
    return public_base_url()


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def _db():
    """Whichever store the running process uses.

    Resolved at call time. Inside the web app that is `web.app.db` (which the
    tests swap for demo_db); in the weekly job, web.app is never imported and
    the choice is made the same way the app makes it.
    """
    import sys
    webapp = sys.modules.get("web.app")
    if webapp is not None and getattr(webapp, "db", None) is not None:
        return webapp.db
    if os.getenv("DEMO_MODE") == "1":
        from . import demo_db
        return demo_db
    from . import db
    return db


def _expires_at(body: dict) -> str:
    raw = body.get("expires_in")
    try:
        seconds = int(raw) if raw is not None else 3600
    except (TypeError, ValueError):
        seconds = 3600
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def save_tokens(user_id: str, body: dict, db=None) -> None:
    """Store what Yahoo just gave us. A refresh reply may omit the refresh
    token, in which case the old one stays."""
    db = db or _db()
    fields: dict[str, Any] = {
        "access_token": encrypt(body["access_token"]),
        "expires_at": _expires_at(body),
    }
    if body.get("refresh_token"):
        fields["refresh_token"] = encrypt(body["refresh_token"])
    if body.get("xoauth_yahoo_guid"):
        fields["yahoo_guid"] = str(body["xoauth_yahoo_guid"])
    db.save_yahoo_token(user_id, fields)


def has_tokens(user_id: str, db=None) -> bool:
    db = db or _db()
    try:
        row = db.get_yahoo_token(user_id)
    except Exception:  # noqa: BLE001 — table missing reads as "not connected"
        return False
    return bool(row and decrypt(row.get("refresh_token") or ""))


def forget(user_id: str, db=None) -> None:
    (db or _db()).delete_yahoo_token(user_id)


_refresh_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(user_id: str) -> threading.Lock:
    with _locks_guard:
        return _refresh_locks.setdefault(user_id, threading.Lock())


def _is_fresh(expires_at: Any) -> bool:
    if not expires_at:
        return False
    try:
        when = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when - datetime.now(timezone.utc) > timedelta(seconds=REFRESH_MARGIN)


def access_token_for_user(user_id: str, db=None) -> Optional[str]:
    """A working access token for this account, refreshing if needed.

    None when the account never connected Yahoo, or Yahoo has refused the
    refresh token (revoked from Yahoo's side) — the provider turns None into
    an AuthRequired with a "sign in with Yahoo again" sentence.

    One refresh at a time per account: the weekly job and a person clicking
    Generate can arrive together, and two simultaneous refreshes can leave
    the loser holding a token Yahoo has already replaced.
    """
    if not user_id:
        return None
    db = db or _db()
    with _lock_for(str(user_id)):
        try:
            row = db.get_yahoo_token(user_id)
        except Exception:  # noqa: BLE001
            return None
        if not row:
            return None
        if _is_fresh(row.get("expires_at")):
            token = decrypt(row.get("access_token") or "")
            if token:
                return token
        refresh_token = decrypt(row.get("refresh_token") or "")
        if not refresh_token:
            return None
        try:
            body = refresh(refresh_token)
        except YahooAuthError:
            return None
        save_tokens(user_id, body, db=db)
        return body["access_token"]


def access_token_for_league(league_key: str) -> Optional[str]:
    """The token source registered with providers.yahoo.

    A league belongs to one account here; that account's Yahoo token reads
    it. A league with no owner, or whose owner disconnected, has none.
    """
    db = _db()
    try:
        row = db.find_league_by_platform_id("yahoo", league_key)
    except Exception:  # noqa: BLE001
        return None
    if not row or not row.get("user_id"):
        return None
    return access_token_for_user(row["user_id"], db=db)


def register() -> None:
    from providers import yahoo
    yahoo.set_token_source(access_token_for_league)
