"""
Classified advertising for the newspaper.

Ads are styled as period-appropriate newspaper classifieds rather than banner
units, because a classified block reads as part of the paper instead of an
interruption to it. People actually read classifieds; they have spent twenty
years learning to ignore anything shaped like a leaderboard.

Every slot is still wrapped in a container carrying standard IAB dimensions and
a `data-ad-slot` attribute, so when you're ready to hand inventory to an ad
network the script can target `[data-ad-slot]` and fill them without any
redesign. Until then, the house ads below fill the space and sell the product.

Usage:

    from ads import render_classifieds
    html = render_classifieds(league_name="Kevlarville", week=3)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Standard sizes, kept so network-served creative fits the same holes.
SIZE_SMALL = (300, 100)     # classified text block
SIZE_MEDIUM = (300, 250)    # IAB medium rectangle
SIZE_BANNER = (728, 90)     # IAB leaderboard


@dataclass
class Ad:
    """One classified. `slot_id` is what an ad network targets."""

    slot_id: str
    heading: str
    body: str
    contact: Optional[str] = None
    size: tuple[int, int] = SIZE_SMALL
    is_house_ad: bool = True


#: The one ad that is actually selling something. It is the last line of the
#: paper a reader sees after two thousand words they enjoyed, so it survives
#: every fill: writer-generated classifieds replace the generic filler below,
#: never this.
PRODUCT_AD = Ad(
    slot_id="classified-house",
    heading="YOUR LEAGUE, YOUR PAPER",
    body="This newspaper was generated automatically from real league "
         "data. Free for any league that wants one.",
    contact="commissionersdesk.com",
)

#: Last-resort filler, so the grid never has a visible hole in it. Deliberately
#: generic, which is exactly why it should be crowded out by the ones the
#: writer produces about the actual week — see ads_from_content.
HOUSE_ADS: list[Ad] = [
    PRODUCT_AD,
    Ad(
        slot_id="classified-1",
        heading="WANTED: ONE COMPETENT MANAGER",
        body="League seeks individual capable of setting a lineup before "
             "Sunday kickoff. Experience preferred. Standards low.",
        contact="Inquire within",
    ),
    Ad(
        slot_id="classified-3",
        heading="LOST: ONE SEASON'S DIGNITY",
        body="Last seen Week 1. Sentimental value only. No questions asked "
             "upon return.",
        contact="Reward offered",
    ),
    Ad(
        slot_id="classified-4",
        heading="FOR SALE: RUNNING BACK, BARELY USED",
        body="Drafted second round. Touches ball infrequently. Will consider "
             "any reasonable offer, or an unreasonable one.",
        contact="Serious buyers only",
    ),
]


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _edit(key: str, editable: bool) -> str:
    """Mirror of newspaper.ed(), kept local so ads.py imports nothing."""
    if not editable:
        return ""
    return f' data-edit-key="{key}" contenteditable="true" spellcheck="true"'


def _render_ad(ad: Ad, editable: bool = False, index: int | None = None) -> str:
    width, height = ad.size
    # Only writer-generated ads are editable. The product ad and the generic
    # house filler are ours, not the commissioner's, and an edit to them would
    # be silently discarded on the next generation anyway.
    can_edit = editable and index is not None and not ad.is_house_ad
    contact_html = (
        f'<div class="classified-contact"'
        f'{_edit(f"classified_contact_{index}", can_edit)}>'
        f'{_escape(ad.contact)}</div>'
        if ad.contact else ""
    )
    # data-ad-slot / data-ad-size are the hooks an ad network script uses.
    # Keeping them present even on house ads means swapping in real inventory
    # is a config change, not a template change.
    return f'''
        <div class="classified"
             data-ad-slot="{_escape(ad.slot_id)}"
             data-ad-size="{width}x{height}"
             data-ad-house="{'true' if ad.is_house_ad else 'false'}">
            <div class="classified-heading"{_edit(f"classified_heading_{index}", can_edit)}>{_escape(ad.heading)}</div>
            <div class="classified-body"{_edit(f"classified_body_{index}", can_edit)}>{_escape(ad.body)}</div>
            {contact_html}
        </div>'''


CLASSIFIEDS_CSS = """
    .classifieds-section {
        border-top: 3px double #111;
        border-bottom: 3px double #111;
        padding: 14px 0 18px;
        margin: 28px 0;
    }
    .classifieds-title {
        font-family: "Barlow Condensed", "Georgia", serif;
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 3px;
        text-transform: uppercase;
        text-align: center;
        color: #555;
        margin-bottom: 14px;
    }
    .classifieds-grid {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 0;
    }
    .classified {
        padding: 10px 14px;
        border-right: 1px solid #cfc8b8;
        font-family: "Georgia", "Times New Roman", serif;
    }
    .classified:last-child { border-right: none; }
    .classified-heading {
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.4px;
        margin-bottom: 5px;
        line-height: 1.25;
    }
    .classified-body {
        font-size: 12px;
        line-height: 1.45;
        color: #333;
    }
    .classified-contact {
        font-size: 11px;
        font-style: italic;
        color: #6b6050;
        margin-top: 6px;
    }
    @media (max-width: 900px) {
        .classifieds-grid { grid-template-columns: repeat(2, 1fr); }
        .classified:nth-child(2n) { border-right: none; }
        .classified { border-bottom: 1px solid #cfc8b8; }
    }
"""


def ads_from_content(entries, limit: int = 3) -> list[Ad]:
    """Writer-generated classifieds -> Ad objects.

    The house ads below stay as the last-resort fill (and the product ad stays
    a real house ad). These are the ones written about the week that just
    happened, which is the difference between a classified somebody reads and
    a classified somebody's eye slides off.
    """
    made: list[Ad] = []
    for i, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            continue
        heading = (entry.get("heading") or "").strip()
        body = (entry.get("body") or "").strip()
        if not heading or not body:
            continue
        made.append(Ad(
            slot_id=f"classified-{i + 1}",
            heading=heading,
            body=body,
            contact=(entry.get("contact") or "").strip() or None,
            is_house_ad=False,
        ))
        if len(made) >= limit:
            break
    return made


def render_classifieds(ads: Optional[list[Ad]] = None, limit: int = 4,
                       editable: bool = False) -> str:
    """The classifieds block, ready to drop into the paper."""
    inventory = list(ads or [])

    # Always keep the product ad, and top up from the house inventory if the
    # writer produced fewer than the grid holds — a half-empty classifieds
    # block looks like a rendering fault rather than a light week.
    if len(inventory) < limit:
        have = {a.heading for a in inventory}
        inventory += [a for a in HOUSE_ADS if a.heading not in have]

    inventory = inventory[:limit]
    if not inventory:
        return ""
    blocks = "".join(_render_ad(ad, editable, i)
                     for i, ad in enumerate(inventory))
    return f'''
    <div class="classifieds-section">
        <div class="classifieds-title">Classifieds</div>
        <div class="classifieds-grid">{blocks}</div>
    </div>'''


# ---------------------------------------------------------------------------
# Subscribe block
#
# Goes at the foot of every paper. Someone who has just read the whole thing is
# the warmest audience this product will ever have — far warmer than anyone
# looking at a landing page — so this is where the email ask belongs.
#
# Renders nothing when no slug is passed (e.g. the CLI writing a local file),
# because a form posting to a server that isn't there is worse than no form.
# ---------------------------------------------------------------------------

SUBSCRIBE_CSS = """
    .subscribe-block {
        border: 3px double #111;
        padding: 24px 28px;
        margin: 28px 0;
        text-align: center;
        background: #fffdf8;
    }
    .subscribe-kicker {
        font-family: "Barlow Condensed", "Georgia", serif;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 3px;
        text-transform: uppercase;
        color: #6b6050;
    }
    .subscribe-head {
        font-family: "Playfair Display", Georgia, serif;
        font-size: 26px;
        font-weight: 900;
        margin: 6px 0 4px;
    }
    .subscribe-sub {
        font-size: 14px;
        color: #3a3a3a;
        margin-bottom: 16px;
    }
    .subscribe-form {
        display: flex;
        gap: 8px;
        max-width: 420px;
        margin: 0 auto;
    }
    .subscribe-form input {
        flex: 1;
        font-family: Georgia, serif;
        font-size: 15px;
        padding: 11px 13px;
        border: 1px solid #cfc8b8;
        background: #fff;
    }
    .subscribe-form button {
        font-family: "Barlow Condensed", sans-serif;
        font-size: 14px;
        font-weight: 700;
        letter-spacing: 1px;
        text-transform: uppercase;
        padding: 11px 20px;
        border: none;
        background: #2d5016;
        color: #fff;
        cursor: pointer;
    }
    .subscribe-form button:hover { background: #1e3a0f; }
    .subscribe-fine {
        font-size: 11px;
        color: #6b6050;
        margin-top: 10px;
    }
    @media (max-width: 600px) {
        .subscribe-form { flex-direction: column; }
    }
"""


def render_subscribe_block(public_slug: Optional[str], paper_name: str = "") -> str:
    if not public_slug:
        return ""
    name = _escape(paper_name or "this paper")
    return f'''
    <div class="subscribe-block">
        <div class="subscribe-kicker">Don&rsquo;t miss next week</div>
        <div class="subscribe-head">Get this in your inbox</div>
        <div class="subscribe-sub">
            We&rsquo;ll send you {name} the second it drops. That&rsquo;s it &mdash;
            no other emails, ever.
        </div>
        <form class="subscribe-form" method="post" action="/p/{_escape(public_slug)}/subscribe">
            <input type="email" name="email" placeholder="you@email.com" required />
            <button type="submit">Sign me up</button>
        </form>
        <div class="subscribe-fine">One click to unsubscribe. We won&rsquo;t sell your email.</div>
    </div>'''
