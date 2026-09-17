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
        <!-- Named for what it is now that the publisher's own Classifieds
             page exists further up. Two sections with the same title in one
             paper is a reader wondering which one they already read. -->
        <div class="classifieds-title">League Notices</div>
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


# ---------------------------------------------------------------------------
# THE CLASSIFIEDS PAGE
#
# A full page of the publisher's own material — topical NFL memes now, paid
# advertising later — laid out like a period classifieds sheet. Distinct from
# everything above it in this file, which is per-league filler the writer
# produced about that league's week. This page is identical in every paper.
#
# LAYOUT: each ad keeps its own shape. Nothing is cropped and nothing is
# letterboxed, because a meme that loses its bottom two lines is no longer a
# joke. That means the page cannot be a fixed template, so it is a two-column
# masonry: images run at column width and stack at whatever height their own
# proportions give them, which is what the vintage sheets this is modelled on
# actually look like. An ad appreciably wider than it is tall runs as a banner
# across both columns, the way the wide blocks do on those sheets — decided
# from the image's own dimensions, so there is nothing to name or configure.
#
# The width and height attributes are not decoration. Without them the page
# has no idea how tall an image will be until it arrives, so the layout jumps
# as each one loads, and — worse — a print render can paginate against a
# half-measured page and put the fold in the middle of an ad.
# ---------------------------------------------------------------------------

#: Wider than this and an ad runs full width instead of in a column. 1.8 is
#: about where a banner stops looking like a square that got stretched: a
#: 728x90 leaderboard is 8.1, a 16:9 screenshot is 1.78 and reads fine in a
#: column, a 3:2 photo is 1.5 and would look absurd spanning the page.
BANNER_ASPECT = 1.8

#: What an ad is assumed to be when its header didn't give up a size. 4:3 is
#: unremarkable in a column and wrong in a way nobody notices; the alternative
#: is reserving no space at all, which makes the page jump.
FALLBACK_ASPECT = (4, 3)


def _attr(text: str) -> str:
    """Escape for inside a double-quoted attribute."""
    return _escape(text).replace('"', "&quot;")


def _safe_url(value: str) -> Optional[str]:
    """http(s) or a same-origin path, or nothing.

    Mirrors web.sanitize.clean_image_url — kept local because this module
    deliberately imports nothing, so it can render a paper with no web package
    present (the CLI does exactly that). The canonical version is the one to
    change first if this ever needs to get cleverer.

    Worth having even though only one person can set these: the whole point of
    a `javascript:` URL is that it does not look like one in a text box.
    """
    candidate = (value or "").strip()
    candidate = "".join(ch for ch in candidate if ord(ch) > 32 or ch == " ")
    lowered = candidate.lower()
    if lowered.startswith("/") and not lowered.startswith("//"):
        return candidate[:600]
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return candidate[:600]
    return None


def _ad_shape(ad: dict) -> tuple:
    """(width, height, aspect) for one ad, falling back where unknown."""
    width = ad.get("width") or 0
    height = ad.get("height") or 0
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        width = height = 0
    if width <= 0 or height <= 0:
        width, height = FALLBACK_ASPECT
    return width, height, width / height


def _ad_figure(ad: dict, index: int, banner: bool = False) -> str:
    """One framed ad. Returns "" for a row with no usable image."""
    src = _safe_url(str(ad.get("image_url") or ""))
    if not src:
        return ""

    width, height, aspect = _ad_shape(ad)

    caption = (ad.get("caption") or "").strip()
    # The caption doubles as alt text. Where there isn't one, the alt says what
    # the thing IS rather than being empty — a screen reader announcing
    # "image" five times running is no better than silence.
    alt = _attr(caption) if caption else f"Classified advertisement {index + 1}"
    caption_html = (f'<figcaption class="pub-ad-caption">{_escape(caption)}'
                    f'</figcaption>') if caption else ""

    img = (f'<img class="pub-ad-img" src="{_attr(src)}" alt="{alt}" '
           f'width="{width}" height="{height}" loading="lazy">')

    link = _safe_url(str(ad.get("link_url") or ""))
    if link:
        # rel is not optional on a link to somebody else's site: without
        # noopener the destination gets a handle on this window.
        img = (f'<a class="pub-ad-link" href="{_attr(link)}" target="_blank" '
               f'rel="noopener noreferrer nofollow">{img}</a>')

    # The aspect ratio is handed to CSS as a plain number so the print rules
    # can cap an ad's HEIGHT by setting its max-width — see the stylesheet. A
    # height cap applied directly to an image that is also width:100% does not
    # scale it, it squashes it.
    classes = "pub-ad pub-ad-banner" if banner else "pub-ad"
    return (f'<figure class="{classes}" data-ad-slot="publisher-{index}" '
            f'style="--ad-aspect:{aspect:.4f};">'
            f'<div class="pub-ad-frame">{img}</div>{caption_html}</figure>')


def pack_columns(ads: list, columns: int = 2) -> list:
    """Lay the ads out: banners full width, everything else into columns.

    Returns a list of blocks, each either ("banner", ad, index) or
    ("columns", [[(ad, index), ...] per column]).

    WHY THIS IS DONE HERE AND NOT IN CSS

    The first version was a CSS multi-column container, which is the obvious
    way to get a masonry and reads beautifully in the stylesheet. Printed, it
    fell apart: `column-span: all` silently does nothing on the inline-block
    the ads needed to be, so the banner never spanned; and a multicol container
    that has to fragment across a page boundary produced a five-ad page that
    ran to SIX sheets, one of them 73% empty. Measured, not guessed.

    Packing here instead means the page is ordinary block boxes by the time a
    browser sees it, which paginate predictably, and it means the column
    balance can be tested without a browser at all.

    Greedy shortest-column-first. Not optimal — optimal is bin packing, and
    with five items the difference is invisible — but it is stable: the same
    ads in the same order always produce the same page.
    """
    blocks: list = []
    pending: list = []
    heights = [0.0] * columns

    def flush():
        nonlocal pending, heights
        if not pending:
            return
        packed: list = [[] for _ in range(columns)]
        heights = [0.0] * columns

        # Tallest first. Assigning in document order is the obvious version
        # and it balances badly: on the five-ad page it put both portraits in
        # one column and left the other 40% shorter, which is a page with a
        # hole in the side of it. Taking the tallest first is the standard fix
        # and cost one line.
        #
        # Height at a fixed column width is 1/aspect — the width cancels, so
        # these compare correctly without knowing what it will be.
        order = sorted(pending, key=lambda item: -1.0 / _ad_shape(item[0])[2])
        for ad, index in order:
            shortest = heights.index(min(heights))
            packed[shortest].append((ad, index))
            heights[shortest] += 1.0 / _ad_shape(ad)[2]

        # Assigned tallest-first, but READ top to bottom: within a column the
        # publisher's own order is restored, so dragging an ad up the list
        # still moves it up the page.
        for column in packed:
            column.sort(key=lambda item: item[1])

        blocks.append(("columns", packed))
        pending = []

    for index, ad in enumerate(ads):
        _, _, aspect = _ad_shape(ad)
        if aspect >= BANNER_ASPECT:
            # A banner interrupts the columns rather than sitting in one, so
            # everything above it is settled before it goes down.
            flush()
            blocks.append(("banner", ad, index))
        else:
            pending.append((ad, index))
    flush()
    return blocks


def render_publisher_page(ads: Optional[list] = None,
                          title: str = "Classifieds",
                          note: str = "") -> str:
    """The full-page classifieds section, or "" if there is nothing to show.

    Empty in, empty out — and that is the whole fallback. The small classifieds
    block above tops itself up with house ads because a half-empty grid in the
    middle of a paper looks like a rendering fault. A whole page cannot be
    padded that way: five house ads stretched over a page would look far worse
    than the page simply not being there on a week nobody uploaded anything.
    """
    rows = [a for a in (ads or []) if a and _safe_url(str(a.get("image_url") or ""))]
    if not rows:
        return ""

    parts: list[str] = []
    for block in pack_columns(rows):
        if block[0] == "banner":
            _, ad, index = block
            parts.append(_ad_figure(ad, index, banner=True))
        else:
            columns_html = "".join(
                f'<div class="pub-ad-col">'
                + "".join(_ad_figure(ad, index) for ad, index in column)
                + "</div>"
                for column in block[1])
            parts.append(f'<div class="pub-ad-cols">{columns_html}</div>')

    body = "".join(parts)
    if not body.strip():
        return ""

    note_html = (f'<div class="pub-page-note">{_escape(note)}</div>'
                 if note else "")

    return f'''
    <section class="full-section publisher-page">
        <div class="pub-page-head">
            <div class="section-title-full">{_escape(title)}</div>
            {note_html}
        </div>
        <div class="pub-ad-grid">{body}</div>
    </section>'''


PUBLISHER_PAGE_CSS = """
    .publisher-page {
        margin-top: 30px;
    }
    .pub-page-head { margin-bottom: 4px; }
    .pub-page-note {
        font-family: Georgia, "Times New Roman", serif;
        font-style: italic;
        font-size: 12px;
        color: #6b6050;
        text-align: center;
        margin: -12px 0 14px;
    }

    /* Two columns, packed on the server — see pack_columns for why this is
       not a CSS multi-column container. These are ordinary blocks, which is
       the entire point: they paginate the way every other block in the paper
       does. */
    .pub-ad-cols {
        display: flex;
        gap: 18px;
        align-items: flex-start;
    }
    .pub-ad-col { flex: 1 1 0; min-width: 0; }

    .pub-ad {
        break-inside: avoid;
        page-break-inside: avoid;
        margin: 0 0 18px;
    }
    .pub-ad:last-child { margin-bottom: 0; }

    /* The frame. Double rule outside, hairline inside, which is the whole
       trick to making a colour image sit inside a black-and-white paper
       without looking pasted on. */
    .pub-ad-frame {
        border: 3px double #111;
        padding: 6px;
        background: #fffdf8;
        margin: 0 auto;
    }

    .pub-ad-img {
        display: block;
        width: 100%;
        height: auto;
        border: 1px solid #cfc8b8;
    }
    .pub-ad-link { display: block; text-decoration: none; }

    .pub-ad-caption {
        font-family: "Barlow Condensed", Georgia, serif;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1.5px;
        text-transform: uppercase;
        text-align: center;
        color: #555;
        padding: 6px 4px 0;
    }

    /* One column on a phone. Two columns of memes on a 390px screen is two
       columns of unreadable memes.

       `screen and` is load-bearing, not tidiness. A printed Letter page is
       about 726px wide, so a bare `max-width: 760px` fires on PAPER as well
       as on phones — and this page printed as a single stacked column running
       over two sheets, one of them two-thirds empty, until that word was
       added. Any breakpoint above roughly 700px in this codebase is a print
       rule whether its author meant it to be or not. */
    @media screen and (max-width: 760px) {
        .pub-ad-cols { display: block; }
        .pub-ad-col { width: 100%; }
    }
"""
