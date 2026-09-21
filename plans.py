"""
What each plan allows.

ONE FILE, because the alternative is what always happens instead: the server
refuses a third generation, the button still says you have one left, and the
pricing page promises something neither of them implements. Every gate, every
lock icon and every line of pricing copy in this app reads from the table
below, so they cannot disagree.

THE SUBSCRIBER IS A PERSON, NOT A LEAGUE. One member of a league pays — not
necessarily the commissioner — and everything they own is covered. A league
with no account behind it (created from a manage link, which is how every
league before accounts works) is on the free plan and keeps working exactly as
it did.

WHAT IS DELIBERATELY FREE, and should stay that way:

  Reading.   Papers are public. Readers never sign up for anything, which is
             half the pitch.
  The archive. Every back issue, both plans. Locking your own history behind a
             renewal is the kind of thing that makes people distrust a product.
  Editing.   Fixing a sentence the writer got wrong costs nothing to serve and
             is how a paper stops being "AI slop" and starts being yours.
  The lore.  Per-person notes, league lore, the punishment. It is the thing
             that makes the paper good, and a free paper that is bad sells
             nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

FREE = "free"
PAID = "paid"

#: The site's own people. Never sold and never reachable from Stripe: the only
#: way onto it is somebody with database access typing
#:
#:     update users set plan = 'staff' where email = '...';
#:
#: It exists because testing the paper means generating the same week over and
#: over, and the paid allowance of three runs out before lunch. Not an env var
#: of email addresses, on purpose: signup does not verify email, so a list of
#: addresses would hand unlimited generation to whoever registered one first.
STAFF = "staff"

#: What it costs, in one place, for the pricing page and every upgrade prompt.
PRICE_TEXT = "$4.99 a month"
PRICE_SHORT = "$4.99/mo"


@dataclass(frozen=True)
class Plan:
    key: str
    label: str

    #: How many times a week's paper may be REGENERATED, after the first one.
    #:
    #: Regenerations, not generations, because that is the unit the product
    #: already counts and shows ("2 regenerations left for week 3"). The first
    #: paper of a week is never charged against this — it is the paper coming
    #: into existence, not a second attempt at it.
    regenerations_per_week: int

    #: How many leagues one account may run. None is unlimited.
    leagues: Optional[int]

    #: Which themes are offered. None means all of them.
    themes: Optional[tuple[str, ...]]

    #: The Sunday-morning cron that writes and mails the paper without anybody
    #: pressing anything.
    auto_send: bool

    #: Putting your own photos in the paper.
    photo_uploads: bool


PLANS: dict[str, Plan] = {
    FREE: Plan(
        key=FREE,
        label="Free",
        regenerations_per_week=2,
        leagues=1,
        # Tabloid only. It is the loudest and the best demonstration of what
        # the thing is; the other two read as restraint, which is a taste you
        # develop after you already like the product.
        themes=("tabloid",),
        auto_send=False,
        photo_uploads=False,
    ),
    PAID: Plan(
        key=PAID,
        label="Paid",
        regenerations_per_week=3,
        leagues=None,
        themes=None,
        auto_send=True,
        photo_uploads=True,
    ),
    STAFF: Plan(
        key=STAFF,
        label="Staff",
        # Not infinite, because every generation is a real Anthropic call and
        # a loop in somebody's testing should still hit a wall. A hundred a
        # week is more than anybody testing by hand will ever press.
        regenerations_per_week=100,
        leagues=None,
        themes=None,
        auto_send=True,
        photo_uploads=True,
    ),
}


#: Subscription states that mean the money is arriving. Everything else —
#: past_due, unpaid, canceled, incomplete, paused — is not paid.
#:
#: `active` covers the person who has cancelled but whose month is still
#: running: Stripe keeps them active until the period actually ends, which is
#: the correct behaviour and the one people expect from something they paid
#: for.
ACTIVE_STATUSES = frozenset({"active", "trialing"})


def plan_for(user: Optional[dict[str, Any]]) -> Plan:
    """The plan this person is on.

    Nobody signed in, no account behind the league, a row with no plan column
    because migration 014 has not been run — all free. Every unknown is free,
    which fails toward giving the product away rather than toward charging
    somebody who has not paid or locking out somebody who has.
    """
    if not user:
        return PLANS[FREE]

    # Checked before the Stripe status, because staff has none and needs none.
    if (user.get("plan") or FREE).strip().lower() == STAFF:
        return PLANS[STAFF]

    if (user.get("plan") or FREE).strip().lower() != PAID:
        return PLANS[FREE]

    status = (user.get("plan_status") or "").strip().lower()
    if status and status not in ACTIVE_STATUSES:
        # The plan column says paid and Stripe says otherwise. Stripe wins:
        # it is the one that knows whether the card cleared.
        return PLANS[FREE]

    return PLANS[PAID]


def is_paid(user: Optional[dict[str, Any]]) -> bool:
    """Has everything the paid plan has — which staff does, so nobody on staff
    is ever shown an upgrade button or sent to checkout."""
    return plan_for(user).key in (PAID, STAFF)


# ---------------------------------------------------------------------------
# The individual questions, asked the same way everywhere
# ---------------------------------------------------------------------------

def allows_theme(plan: Plan, theme: str) -> bool:
    return plan.themes is None or theme in plan.themes


def resolve_theme(plan: Plan, theme: str, fallback: str = "tabloid") -> str:
    """The theme this plan may actually have.

    A free account posting `theme=gameday` straight at the form gets tabloid,
    silently. Not an error: the form is a hint, the plan is the rule, and there
    is no reading of "pick a theme" where the right answer is a 400.
    """
    return theme if allows_theme(plan, theme) else fallback


def allows_another_league(plan: Plan, current_count: int) -> bool:
    return plan.leagues is None or current_count < plan.leagues


def regenerations_per_week(plan: Plan) -> int:
    return max(0, plan.regenerations_per_week)


# ---------------------------------------------------------------------------
# What to say when something is locked
#
# The copy lives here rather than in the templates so that every lock in the
# app says the same thing, and so that changing the price changes it once.
# ---------------------------------------------------------------------------

LOCK_REASONS = {
    "themes": f"Other looks are part of {PRICE_TEXT}.",
    "leagues": f"Running more than one league is part of {PRICE_TEXT}.",
    "auto_send": f"Sending itself every week is part of {PRICE_TEXT}.",
    "photo_uploads": f"Putting your own photos in is part of {PRICE_TEXT}.",
    "generations": f"{PRICE_TEXT} gets you another go at each week.",
}


def billing_enabled() -> bool:
    """Whether this deployment can actually take money.

    With Stripe unconfigured — a fresh checkout, a local run, demo mode — the
    upgrade buttons have nowhere to go. The gates still hold, so nothing is
    given away; the buttons simply do not appear, which is better than a button
    that 500s.
    """
    return bool(os.getenv("STRIPE_SECRET_KEY") and os.getenv("STRIPE_PRICE_ID"))
