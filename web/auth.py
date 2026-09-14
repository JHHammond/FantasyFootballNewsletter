"""
Passwords and sessions.

Two things live here and nothing else touches either of them: turning a
password into something safe to store, and turning a logged-in user into a
cookie that can't be forged.

WHY SCRYPT, AND WHY FROM THE STANDARD LIBRARY

A password hash has one job: make guessing expensive. Plain SHA-256 fails at
this — it is fast by design, which is exactly wrong here, and a GPU will try
billions a second. scrypt is deliberately slow *and* memory-hard, so the
attacker's advantage from specialised hardware is small.

It ships in Python's hashlib, so there is no dependency to install, nothing to
compile on the host, and no supply-chain surface for the most security-critical
code in the app. bcrypt and argon2 are also good answers; they are just answers
with a build step.

The cost parameters are stored inside each hash. That means the cost can be
raised later and old hashes still verify — they simply get upgraded the next
time their owner logs in and we can see the plaintext. Without that, raising
the cost would lock everybody out.

WHAT THIS DOES NOT DO

It does not stop somebody reusing the password from their email account. That
is why login attempts are rate limited per email as well as per address, and
why the reset flow exists.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

#: Cost parameters. n is the work factor and dominates both time AND memory,
#: which is the tension worth understanding before changing it.
#:
#: Memory per hash is roughly 128 * n * r. At 2**15 that is ~32MB, about 95ms
#: per verify here — good against an attacker, and a real problem on a small
#: instance: scrypt is memory-hard for the defender too, so a burst of logins
#: is a burst of 32MB allocations. Ten at once on a 512MB box is most of the
#: box, and the failure mode is the whole site running out of memory rather
#: than a slow login page.
#:
#: 2**14 is ~16MB and ~50ms. Still far beyond what a GPU shrugs off, and it
#: can't be turned into an out-of-memory attack by anyone who can reach the
#: login form. Raise it when the app has more headroom than the login path —
#: old hashes carry their own parameters and keep verifying, and needs_rehash
#: upgrades them as their owners log in.
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
SALT_BYTES = 16

#: Long enough to matter, short enough that nobody is fighting the form.
#: Length beats character-class rules: a long ordinary phrase is stronger than
#: a short one with a punctuation mark bolted on, and mandatory symbols mostly
#: produce Password1! across an entire user base.
MIN_PASSWORD_LENGTH = 10

#: Rejected outright regardless of length. Not a substitute for a real
#: breached-password check, but it catches the handful that turn up first in
#: every credential-stuffing list.
_OBVIOUS = frozenset({
    "password", "password1", "password123", "12345678", "123456789",
    "1234567890", "qwertyuiop", "letmein123", "iloveyou1", "fantasyfootball",
    "commissioner", "footballfootball", "changeme123", "adminadmin",
})


def hash_password(password: str) -> str:
    """A self-describing hash: scrypt$n$r$p$salt$key.

    Carrying the parameters means a hash made today still verifies after the
    cost is raised — see needs_rehash.
    """
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_N * SCRYPT_R * 200,
    )
    return "$".join([
        "scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
        base64.b64encode(salt).decode(), base64.b64encode(key).decode(),
    ])


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. False for anything malformed rather than raising.

    compare_digest rather than ==, because == returns as soon as two bytes
    differ and that timing difference is measurable over enough requests.
    """
    try:
        scheme, n, r, p, salt_b64, key_b64 = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(key_b64)
        n, r, p = int(n), int(r), int(p)
    except (ValueError, TypeError):
        return False

    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=n, r=r, p=p, dklen=len(expected),
            maxmem=n * r * 200,
        )
    except ValueError:
        return False

    return hmac.compare_digest(candidate, expected)


def needs_rehash(stored: str) -> bool:
    """True if this hash was made with weaker parameters than we now use."""
    try:
        scheme, n, r, p, _, _ = (stored or "").split("$")
        return scheme != "scrypt" or int(n) < SCRYPT_N or int(r) < SCRYPT_R
    except (ValueError, TypeError):
        return True


def password_problem(password: str, email: str = "") -> Optional[str]:
    """What's wrong with this password, or None.

    Returns a sentence to show the person, not an error code — the whole point
    of checking at signup is to say something useful while they can still act
    on it.
    """
    pw = password or ""
    if len(pw) < MIN_PASSWORD_LENGTH:
        return (f"Passwords need to be at least {MIN_PASSWORD_LENGTH} "
                f"characters. A few ordinary words in a row works well.")
    if len(pw) > 256:
        return "That's longer than 256 characters, which is longer than we store."
    if pw.lower() in _OBVIOUS:
        return "That's one of the first passwords anyone guesses. Try another."
    if email and pw.lower() == email.strip().lower():
        return "Your password can't be your email address."
    if len(set(pw)) < 5:
        return "That's too few different characters to be hard to guess."
    return None


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

#: Deliberately loose. Validating addresses by regex is a famous way to reject
#: real ones; the only check that proves an address works is sending to it,
#: which the verification mail does.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_email(value: str) -> Optional[str]:
    address = (value or "").strip().lower()
    if len(address) > 320 or not _EMAIL.match(address):
        return None
    return address


# ---------------------------------------------------------------------------
# Sessions
#
# A signed cookie holding a user id and an issue time. No server-side session
# store: there is nothing in it worth the extra round trip, and a stateless
# cookie survives a restart, which matters on a host that restarts on deploy.
#
# Signed, not encrypted. The contents are not secret — the point is that they
# cannot be *changed*. Without a signature, "user_id=<somebody else>" is an
# edit in devtools.
# ---------------------------------------------------------------------------

SESSION_COOKIE = "cd_session"

#: Long enough that a commissioner checking in weekly stays logged in through
#: a season; short enough that a borrowed laptop is not permanent.
SESSION_MAX_AGE = 60 * 60 * 24 * 30


def session_secret() -> bytes:
    """The key every session cookie is signed with.

    A generated fallback keeps local development working with no setup, at the
    cost of logging everyone out on restart. In production that would be a
    silent, confusing bug, so it is loud in the log instead.
    """
    configured = os.getenv("SESSION_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")

    global _EPHEMERAL_SECRET
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_bytes(32)
        print("SESSION_SECRET is not set — generating a temporary one. Every "
              "restart will log everybody out. Set it in production.",
              flush=True)
    return _EPHEMERAL_SECRET


_EPHEMERAL_SECRET: Optional[bytes] = None


def _sign(payload: str) -> str:
    digest = hmac.new(session_secret(), payload.encode("utf-8"),
                      hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def make_session(user_id: str) -> str:
    """Cookie value for a logged-in user."""
    payload = f"{user_id}.{int(time.time())}"
    return f"{payload}.{_sign(payload)}"


def read_session(cookie: str) -> Optional[str]:
    """The user id in a cookie, or None if it's forged, stale or malformed."""
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) != 3:
        return None
    user_id, issued, signature = parts

    payload = f"{user_id}.{issued}"
    if not hmac.compare_digest(_sign(payload), signature):
        return None

    try:
        age = time.time() - int(issued)
    except ValueError:
        return None
    if age < 0 or age > SESSION_MAX_AGE:
        return None

    return user_id


def cookie_kwargs() -> dict:
    """Flags every session cookie is set with.

    httponly  — script can't read it, so an XSS bug can't lift the session.
    samesite  — 'lax' blocks the cookie on cross-site POSTs, which is what
                makes CSRF against these forms impractical, while still
                allowing ordinary inbound links to arrive logged in.
    secure    — https only. Off when BASE_URL is http, or local development
                silently never receives the cookie back.
    """
    https = os.getenv("BASE_URL", "").startswith("https://")
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": https,
        "max_age": SESSION_MAX_AGE,
        "path": "/",
    }
