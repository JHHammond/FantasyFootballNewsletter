"""
The paper as a printed object.

Every browser can already save a page as PDF; what it cannot do is guess which
parts of a web page were furniture. Without the rules below, "Save as PDF"
produces a document with a drop shadow around it, an email signup form nobody
can type into, stories sliced across page breaks mid-sentence, and — on the
Gameday theme — a solid black rectangle that empties a cartridge.

So this is a second layout, not a filter. It lives beside themes.py and works
the same way: a base block that assumes the tabloid, plus per-theme overrides
stacked on top.

Three rules shaped most of it:

  1. Ink is not free. Large dark areas are removed rather than printed. Small
     ones that carry the paper's identity — the red masthead bar, a section
     rule — are kept and forced through with print-color-adjust, because a
     tabloid with no red is not the same object.
  2. Nothing interactive survives. Forms, buttons and the edit toolbar are
     furniture; on paper they are confusing.
  3. A PDF outlives the tab it came from. It gets a footer with the URL, so
     somebody who is forwarded the file six weeks later can find the league.

Page size is deliberately not forced. The reader's print dialog already knows
whether they are on Letter or A4, and overriding that is how you get a document
with a stripe of blank down one side.
"""

from __future__ import annotations

import themes

# ---------------------------------------------------------------------------
# Base — written against the tabloid, which is the base stylesheet.
# ---------------------------------------------------------------------------

BASE_PRINT_CSS = """
@page {
    /* Size comes from the reader's dialog. Only the margins are ours. */
    margin: 13mm 11mm 15mm;
}

@media print {

    /* --- strip the screen furniture ---------------------------------- */

    body {
        background: #fff !important;
        font-size: 10.5pt;
    }

    .page {
        max-width: none !important;
        margin: 0 !important;
        padding: 0 0 8mm !important;
        box-shadow: none !important;
        border: none !important;
        background: #fff !important;
    }

    /* Interactive things are meaningless on paper. The signup block keeps its
       words — it is the only place the paper asks for anything — but loses the
       input nobody can type into. */
    .print-button,
    .ce-bar,
    .subscribe-form,
    .image-slot-empty,
    .ce-remove,
    button,
    input,
    select,
    textarea {
        display: none !important;
    }

    .subscribe-block {
        border-width: 1px !important;
        padding: 10px 14px !important;
        text-align: center;
    }
    .subscribe-head { font-size: 15pt !important; }
    .subscribe-sub, .subscribe-fine { font-size: 8.5pt !important; }

    /* Photos are resizable on screen via the native handle; on paper that
       affordance is a stray outline. */
    .image-wrap, .image-wrap-editing {
        resize: none !important;
        overflow: visible !important;
    }

    /* --- keep the identity, spend no ink on backgrounds -------------- */
    /* print-color-adjust is off by default in Chrome, which drops every
       background. These are the few that ARE the design rather than
       decoration, so they are forced through. All of them are small. */

    .masthead,
    .week-ticker,
    .fraud-callout,
    .story-label,
    .col-section-label,
    .section-title,
    .section-title-full,
    .ranking-card-rank {
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
    }

    .masthead { padding: 14px 20px 12px !important; }
    .paper-name { font-size: 40pt !important; }

    /* --- page breaks -------------------------------------------------- */
    /* A story split across a page boundary is the single thing that makes a
       printed web page look like a printed web page. */

    .story-card,
    .award-card,
    .ranking-card,
    .player-card,
    .classified,
    .fraud-callout,
    .paired-stories,
    .subscribe-block,
    .image-wrap,
    .hero-image-wrap,
    figure,
    img {
        break-inside: avoid;
        page-break-inside: avoid;
    }

    /* A heading at the foot of a page, its content overleaf, is worse than a
       slightly short page. */
    .masthead,
    .section-title,
    .section-title-full,
    .story-label,
    .col-section-label,
    .story-headline,
    .col-story-headline,
    .award-title,
    .headline {
        break-after: avoid;
        page-break-after: avoid;
    }

    .above-fold { break-after: avoid; }

    p, .story-body, .lead-story, .col-story-body, .award-body {
        orphans: 3;
        widows: 3;
    }

    /* The front page is the front page. Everything after it may flow. */
    .full-section, .rankings-section { break-before: auto; }

    /* --- tighten for a smaller sheet ---------------------------------- */
    /* The screen layout assumes 1200px. A Letter page gives about 720px of
       printable width, so the same grid at the same type size would set the
       outer columns four words wide. */

    .front-page {
        grid-template-columns: 1fr 2fr 1fr !important;
        padding: 0 18px 14px !important;
    }
    .front-col { padding: 12px 10px 0 !important; }

    .headline { font-size: 30pt !important; line-height: 1.05 !important; }
    .lead-story { font-size: 10pt !important; line-height: 1.5 !important; }
    .story-headline-lead { font-size: 17pt !important; }
    .story-headline-feature { font-size: 14pt !important; }
    .story-body, .col-story-body, .award-body { font-size: 9.5pt !important; }
    .col-story-headline { font-size: 11pt !important; }
    .full-section { padding: 0 18px !important; }
    .rankings-section { padding: 14px 18px !important; }

    table, .stats { font-size: 8.5pt !important; }

    /* Floated photos at print width leave a gutter of two words beside them,
       exactly as they did on phones. Same fix. */
    .image-wrap {
        float: none !important;
        width: 46% !important;
        max-width: 46% !important;
        margin: 0 auto 10px !important;
        display: block !important;
    }

    /* And a hard height ceiling. On screen a photo is 100% of its column and
       the page just gets longer; on paper "longer" means a player's headshot
       claiming two thirds of a sheet, with the story that photo illustrates
       pushed overleaf. Height is the dimension that matters once the sheet is
       fixed, so it's the one that gets capped. */
    .image-wrap img,
    .hero-image-wrap img,
    .image-wrap-editing img {
        max-height: 62mm !important;
        width: auto !important;
        max-width: 100% !important;
        height: auto !important;
        margin: 0 auto !important;
        display: block !important;
        object-fit: contain !important;
    }
    .hero-image-wrap img { max-height: 70mm !important; }

    .player-grid { grid-template-columns: repeat(5, 1fr) !important; }
    .rankings-grid { grid-template-columns: repeat(2, 1fr) !important; }
    .classifieds-grid { grid-template-columns: repeat(2, 1fr) !important; }

    /* --- the footer that only exists on paper ------------------------- */
    /* A PDF gets forwarded. Six weeks later it needs to say where it came
       from, because the tab it was printed from is long gone. */

    .print-footer {
        display: block !important;
        margin: 10mm 18px 0;
        padding-top: 5px;
        border-top: 1px solid #999;
        font-family: "Helvetica Neue", Arial, sans-serif;
        font-size: 7.5pt;
        letter-spacing: 0.4px;
        color: #555;
        text-align: center;
        break-inside: avoid;
    }
    .print-footer a { color: #555; text-decoration: none; }
}
"""


# ---------------------------------------------------------------------------
# Per-theme print overrides.
# ---------------------------------------------------------------------------

#: Tabloid is what the base block above was written against.
TABLOID_PRINT_CSS = ""


BROADSHEET_PRINT_CSS = """
@media print {
    /* Already light and already restrained — it mostly wants to be left
       alone. The masthead is sized with clamp(), which resolves against the
       viewport and so goes wrong on a page that has no viewport. */
    .paper-name {
        font-size: 34pt !important;
        letter-spacing: 0.05em !important;
    }
    .masthead { border-bottom: 2px double #111 !important; }
    .headline { font-size: 24pt !important; }
    .lead-story { font-size: 10pt !important; }
    body { font-size: 10pt; }
}
"""


GAMEDAY_PRINT_CSS = """
@media print {
    /* Gameday is white type on near-black. Printed as-is it is a solid black
       page that empties a cartridge and is unreadable if the printer is even
       slightly short of toner — and most browsers drop the background anyway,
       which leaves white text on white paper.
       So: same typography, inverted ground. The condensed caps, the enormous
       numbers and the green accent all survive; only the ink is different. */

    body, .page, .above-fold, .front-col, .story-card, .award-card,
    .ranking-card, .paired-stories, .player-card, .week-ticker,
    .subscribe-block, .classifieds-section, .classified {
        background: #fff !important;
        color: #111 !important;
    }

    .headline, .story-headline, .col-story-headline, .paper-name,
    .ranking-card-name, .classified-heading, .subscribe-head {
        color: #111 !important;
    }

    .lead-story, .story-body, .col-story-body, .award-body,
    .ranking-card-comment, .story-subhead, .col-story-teaser,
    .paper-meta, .hero-caption, .classified-body, .subscribe-sub,
    .subscribe-fine, table, td, th {
        color: #333 !important;
    }

    /* The honor roll and detention cards are recoloured by the dark theme
       through `.player-card div`, so on white paper they print as bordered
       boxes containing nothing. Each line is named, so each gets the colour it
       should have had — the score stays the accent rather than collapsing into
       the caption grey. */
    /* Qualified by the parent on purpose. The rule being overridden is
       `.player-card div` — a class plus an element, which outranks a bare
       class no matter how late it appears. `.player-card .player-card-name`
       is two classes, so it wins on specificity rather than on order. */
    .player-card .player-card-name { color: #111 !important; }
    .player-card .player-card-meta { color: #555 !important; }
    .player-card .player-card-stat { color: #0a7d2c !important; }
    .player-card .player-card-proj { color: #777 !important; }
    .player-card .player-card-shot { border-color: #0a7d2c !important; }

    /* #16f04e is a screen green — on white it disappears. This is the same
       hue with enough depth to survive ink. */
    .story-label, .col-section-label, .section-title, .section-title-full,
    .award-title, .ranking-card-rank, .subscribe-kicker, .classified-contact,
    .pull-quote {
        color: #0a7d2c !important;
        background: transparent !important;
    }

    /* The masthead is the one place the accent stays a fill, because it is
       the paper's identity and it is a single band. */
    .masthead {
        background: #16f04e !important;
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
    }
    .masthead .paper-name { color: #06210e !important; }
    .edition-line { color: #06210e !important; }

    .dateline-bar {
        background: #fff !important;
        border-top: 2px solid #0a7d2c !important;
        border-bottom: 2px solid #0a7d2c !important;
        color: #333 !important;
    }

    .front-col, .story-card, .award-card, .ranking-card, .player-card,
    .classified, .classifieds-section {
        border-color: #bbb !important;
    }

    .fraud-callout {
        background: #fff !important;
        border: 2px solid #a11313 !important;
    }
    .fraud-callout-label { color: #a11313 !important; }
    .fraud-callout-body { color: #333 !important; }

    .pull-quote { border-left-color: #0a7d2c !important; }

    .week-ticker {
        border-top: 2px solid #0a7d2c !important;
        border-bottom: 2px solid #0a7d2c !important;
    }
    .week-ticker * { color: #111 !important; }
}
"""


THEME_PRINT_CSS: dict[str, str] = {
    "tabloid": TABLOID_PRINT_CSS,
    "broadsheet": BROADSHEET_PRINT_CSS,
    "gameday": GAMEDAY_PRINT_CSS,
}


def css_for(theme: str | None) -> str:
    """The complete print stylesheet for a theme.

    Base first, then the theme's overrides, mirroring how themes.py stacks on
    the screen stylesheet.
    """
    key = themes.resolve(theme)
    return BASE_PRINT_CSS + THEME_PRINT_CSS.get(key, "")


# ---------------------------------------------------------------------------
# The button, and the footer only paper sees.
# ---------------------------------------------------------------------------

#: Shown on the published paper, hidden while printing and while editing.
#: window.print() is the whole implementation: every browser turns it into a
#: dialog with "Save as PDF" already in the destination list, including iOS and
#: Android. No dependency, no server round trip, and no 400MB of headless
#: Chrome on the box.
PRINT_BUTTON_HTML = """
<button type="button" class="print-button" onclick="window.print()"
        aria-label="Save this paper as a PDF">
    <span aria-hidden="true">&#8595;</span> Save as PDF
</button>
"""

PRINT_BUTTON_CSS = """
        .print-button {
            position: fixed;
            right: 18px;
            bottom: 18px;
            z-index: 40;
            font-family: "Barlow Condensed", "Helvetica Neue", Arial, sans-serif;
            font-size: 14px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.2px;
            padding: 11px 18px;
            border: 2px solid #111;
            border-radius: 2px;
            background: #fff;
            color: #111;
            cursor: pointer;
            box-shadow: 0 2px 10px rgba(0,0,0,0.28);
        }
        .print-button:hover { background: #111; color: #fff; }
        .print-button:focus-visible { outline: 3px solid #c40000; outline-offset: 2px; }

        /* The footer exists only on paper. */
        .print-footer { display: none; }

        @media (max-width: 600px) {
            .print-button { right: 10px; bottom: 10px; font-size: 12px; padding: 9px 13px; }
        }
"""


def footer_html(canonical_url: str | None, paper_name: str = "") -> str:
    """The line at the bottom of the printed page.

    Always rendered, always hidden on screen. Carries the URL because a PDF
    travels further than the tab it came from.
    """
    name = paper_name or "The Commissioner's Desk"
    if canonical_url:
        where = f'Read this edition online at {canonical_url}'
    else:
        where = "Made with The Commissioner&rsquo;s Desk"
    return (f'<div class="print-footer">{name} &middot; {where}</div>')
