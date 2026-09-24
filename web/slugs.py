"""
Identifier generation for the accountless model.

Two different identifiers, two different jobs:

  public_slug  — appears in every shared paper URL. Readable at the front,
                 because people paste these into group chats and a readable
                 link gets clicked more than a hash — but with a random tail
                 long enough that it can't be guessed. Anyone with the link can
                 read the paper; nobody can FIND a paper without the link.

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


#: No 0/o, 1/l/i: people read these aloud and retype them from screenshots.
_SLUG_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
#: 31 ** 10 is about 2 ** 50. The suffix used to be four hex characters
#: (65,536 options), so anyone who knew a league's name could walk every
#: address in under an hour and read a paper full of real people (John,
#: 24 Sep). The name stays in front so the link is still readable.
SLUG_SUFFIX_LENGTH = 10


def public_slug(league_name: str) -> str:
    """A readable slug that can't be guessed: "kevlarville-k3x9q2m7wd"."""
    suffix = "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(SLUG_SUFFIX_LENGTH))
    return f"{slugify(league_name, 24)}-{suffix}"


def admin_token() -> str:
    """An unguessable token. This is the league's only credential."""
    return secrets.token_urlsafe(24)
