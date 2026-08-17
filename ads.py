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


#: Filler inventory. Shown when nothing has been sold for a slot, so the paper
#: never has a visible hole in it. House ads sell the product itself.
HOUSE_ADS: list[Ad] = [
    Ad(
        slot_id="classified-1",
        heading="WANTED: ONE COMPETENT MANAGER",
        body="League seeks individual capable of setting a lineup before "
             "Sunday kickoff. Experience preferred. Standards low.",
        contact="Inquire within",
    ),
    Ad(
        slot_id="classified-2",
        heading="YOUR LEAGUE, YOUR PAPER",
        body="This newspaper was generated automatically from real league "
             "data. Free for any league that wants one.",
        contact="commish.app",
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


def _render_ad(ad: Ad) -> str:
    width, height = ad.size
    contact_html = (
        f'<div class="classified-contact">{_escape(ad.contact)}</div>'
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
            <div class="classified-heading">{_escape(ad.heading)}</div>
            <div class="classified-body">{_escape(ad.body)}</div>
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


def render_classifieds(ads: Optional[list[Ad]] = None, limit: int = 4) -> str:
    """The classifieds block, ready to drop into the paper."""
    inventory = (ads or HOUSE_ADS)[:limit]
    if not inventory:
        return ""
    blocks = "".join(_render_ad(ad) for ad in inventory)
    return f'''
    <div class="classifieds-section">
        <div class="classifieds-title">Classifieds</div>
        <div class="classifieds-grid">{blocks}</div>
    </div>'''
