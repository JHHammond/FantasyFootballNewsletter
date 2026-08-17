"""
Identifier generation for the accountless model.

Two different identifiers, two different jobs:

  public_slug  — appears in every shared paper URL. Readable, because people
                 paste these into group chats and a readable link gets clicked
                 more than a hash. Not a secret.

  admin_token  — the *only* thing standing between a stranger and the ability
                 to edit a league. Must be unguessable. 32 chars of
                 `secrets.token_urlsafe` is ~192 bits of entropy; brute forcing
                 it is not a realistic attack.

Losing the admin token means losing control of the league, with no password
reset to fall back on. That's the tradeoff for having no accounts — the manage
page tells the user to bookmark it, loudly.
"""

from __future__ import annotations

import re
import secrets

#: Words that would make for confusing or unfortunate slugs.
_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_length: int = 32) -> str:
    """"The Kevlarville Times" -> "the-kevlarville-times" """
    cleaned = _STRIP.sub("-", (text or "").lower()).strip("-")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip("-")
    return cleaned or "league"


def public_slug(league_name: str) -> str:
    """A readable, collision-resistant slug: "kevlarville-7f3a"."""
    return f"{slugify(league_name, 24)}-{secrets.token_hex(2)}"


def admin_token() -> str:
    """An unguessable token. This is the league's only credential."""
    return secrets.token_urlsafe(24)
