"""
Sign in with Google.

Everything platform-specific about Google lives here. The app's own session
model does not change at all: this file's job ends the moment it can hand a
verified identity back, and `_set_session` takes it from there exactly as it
does for a password login. Leagues, plans and Stripe all key off `users.id`
and never learn how somebody proved who they were.

THE ENDPOINTS BELOW WERE READ OUT OF GOOGLE'S OWN DISCOVERY DOCUMENT
(https://accounts.google.com/.well-known/openid-configuration) on 19 Sep 2026,
not written from memory. Verified against the installed google-auth 2.58:
`verify_oauth2_token` checks the signature, the issuer and the expiry, and it
does NOT look at `email_verified`. That check is ours, and it is the one that
matters — see `identity_from_claims`.

WHAT THIS FILE IS DEFENDING AGAINST

  A forged callback. Anybody can hit /auth/google/callback with any query
  string they like. The `state` parameter is signed with the app's own secret
  and echoed in a cookie, and both have to agree — otherwise the endpoint is a
  way to log somebody into an account they do not own by sending them a link.

  A code that did not come from us. The authorization code is exchanged
  server-side over TLS with the client secret attached; a code obtained
  elsewhere cannot be redeemed against this client.

  An unverified address. Google will tell you about an account whose email it
  has not verified. Linking one of those to an existing password account on
  the strength of the address alone is the classic OAuth takeover, and it is
  a one-line mistake to make.

UNCONFIGURED IS A SUPPORTED STATE, same as Stripe. No GOOGLE_CLIENT_ID means
no button and routes that 404.
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import requests

#: From Google's discovery document. Hardcoded rather than fetched per
#: request: they have been stable for years, and a sign-in that depends on a
#: second network call has two ways to fail instead of one.
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

#: Non-sensitive scopes only. `openid` and `email` are what this needs;
#: `profile` is there for the display name, and nothing else is requested.
#: Asking for more would mean a consent screen that frightens people and a
#: Google review process that does not need to exist.
SCOPES = "openid email profile"

REQUEST_TIMEOUT = 15

#: How long a sign-in attempt may sit half-finished. Long enough to read the
#: consent screen and pick an account, short enough that a state cookie left
#: on a shared machine is not usable tomorrow.
STATE_MAX_AGE = 15 * 60

STATE_COOKIE = "cd_oauth_state"


class OAuthError(Exception):
    """Sign-in could not be completed. The message is shown to the person, so
    it never contains a token, a code, or anything Google said verbatim."""


class NotConfigured(OAuthError):
    """This deployment has no Google credentials."""


@dataclass(frozen=True)
class GoogleIdentity:
    """A person, as Google describes them, once we believe it."""

    sub: str
    email: str
    email_verified: bool
    name: str = ""


def client_id() -> str:
    value = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not value:
        raise NotConfigured("GOOGLE_CLIENT_ID is not set")
    return value


def client_secret() -> str:
    value = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not value:
        raise NotConfigured("GOOGLE_CLIENT_SECRET is not set")
    return value


def configured() -> bool:
    """Whether this deployment can offer the button at all."""
    return bool(os.getenv("GOOGLE_CLIENT_ID", "").strip()
                and os.getenv("GOOGLE_CLIENT_SECRET", "").strip())


def redirect_uri(base_url: str) -> str:
    """Where Google sends the browser back.

    Must match a URI registered in the Google console CHARACTER FOR
    CHARACTER — scheme, host, path, and no trailing slash. A mismatch is
    rejected by Google before the person ever reaches this app, with an error
    page that names redirect_uri_mismatch and nothing else, so it is worth
    building this in one place.
    """
    return f"{(base_url or '').rstrip('/')}/auth/google/callback"


# ---------------------------------------------------------------------------
# state — the thing that makes the callback ours
# ---------------------------------------------------------------------------

def new_state() -> str:
    """An unguessable value, carrying the time it was made.

    Signed by the caller with the app's session secret, so a state this server
    never issued cannot be presented back to it.
    """
    return f"{secrets.token_urlsafe(24)}.{int(time.time())}"


def state_is_fresh(state: str, now: Optional[float] = None) -> bool:
    """Has this sign-in attempt taken an implausibly long time?

    Separate from the signature check, because a correctly signed state from
    three weeks ago is still a state somebody could have lifted off a shared
    machine.
    """
    try:
        issued = int((state or "").rsplit(".", 1)[-1])
    except (ValueError, IndexError):
        return False
    return 0 <= (now or time.time()) - issued <= STATE_MAX_AGE


def consent_url(state: str, base_url: str) -> str:
    """Where to send somebody who pressed the button."""
    return AUTH_ENDPOINT + "?" + urlencode({
        "client_id": client_id(),
        "redirect_uri": redirect_uri(base_url),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        # Asks Google to return the address even when the person has several
        # accounts signed in, rather than silently picking one.
        "prompt": "select_account",
        # No refresh token: this app never acts on anybody's behalf, it only
        # needs to know who they are once, at sign-in. Asking for offline
        # access would mean holding a credential with no use for it.
        "access_type": "online",
    })


# ---------------------------------------------------------------------------
# The callback
# ---------------------------------------------------------------------------

def exchange_code(code: str, base_url: str) -> str:
    """Trade the authorization code for an ID token.

    Server-side, with the client secret. This is why a code intercepted from
    the browser's address bar is not enough on its own to sign in as somebody.
    """
    if not code:
        raise OAuthError("Google sent us back without an authorization code.")

    response = requests.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": client_id(),
            "client_secret": client_secret(),
            "redirect_uri": redirect_uri(base_url),
            "grant_type": "authorization_code",
        },
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code != 200:
        # Deliberately not echoed to the person: Google's error bodies are for
        # developers and occasionally quote the request back.
        print(f"[oauth] token exchange failed: {response.status_code} "
              f"{response.text[:200]}", flush=True)
        raise OAuthError("Google wouldn't complete that sign-in. "
                         "Try again, or use a password instead.")

    token = (response.json() or {}).get("id_token")
    if not token:
        raise OAuthError("Google's reply had no identity in it.")
    return token


def verify_id_token(token: str) -> dict[str, Any]:
    """The claims in an ID token, once its signature is checked.

    google-auth fetches Google's public keys, checks the signature, the issuer
    and the expiry, and raises on any of them. What it does NOT check is
    `email_verified` — that is handled in identity_from_claims, and it is the
    check this whole feature turns on.
    """
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
    except ImportError as exc:  # pragma: no cover - deployment problem
        raise NotConfigured("the google-auth package is not installed") from exc

    try:
        return google_id_token.verify_oauth2_token(
            token, google_requests.Request(), client_id(),
            # A minute of slack for clock drift between here and Google.
            # Without it, a server whose clock runs fast rejects every token
            # it is handed, intermittently, with an error about expiry.
            clock_skew_in_seconds=60,
        )
    except Exception as exc:  # noqa: BLE001 — the library raises several
        print(f"[oauth] id token rejected: {type(exc).__name__}: {exc}",
              flush=True)
        raise OAuthError("That sign-in couldn't be verified.") from exc


def identity_from_claims(claims: dict[str, Any]) -> GoogleIdentity:
    """The person, out of verified claims."""
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise OAuthError("Google didn't say who that was.")

    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise OAuthError("That Google account has no email address on it.")

    # Google sends this as a real boolean, but it has historically been a
    # string in some flows and a missing key in others. Anything that is not
    # unambiguously true is treated as false, because the whole point of this
    # field is to be the thing we refuse to guess about.
    raw_verified = claims.get("email_verified")
    verified = raw_verified is True or str(raw_verified).lower() == "true"

    return GoogleIdentity(sub=sub, email=email, email_verified=verified,
                          name=str(claims.get("name") or "").strip()[:120])


def identity_from_code(code: str, base_url: str) -> GoogleIdentity:
    """The whole callback, in one call: code in, verified person out."""
    return identity_from_claims(verify_id_token(exchange_code(code, base_url)))
