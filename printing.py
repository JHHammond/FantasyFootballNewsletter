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

import json

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

    /* SMALL things only.
       .story-card and .paired-stories used to be in here, and that is what
       made the paper full of holes: a game recap is about a third of a page,
       so one that does not fit in the space left jumps WHOLE to the next
       sheet and leaves that third blank. Measured at 29.5% of every page
       wasted, with one page losing a third of itself and the last two thirds.
       A newspaper splits a story across a break. It does not move it.
       Orphan and widow control below keeps the split from landing somewhere
       stupid. */
    .award-card,
    .ranking-card,
    .player-card,
    .obit,
    .line-card,
    .bp-promo,
    .classified,
    .fraud-callout,
    .subscribe-block,
    .image-wrap,
    .hero-image-wrap,
    /* The score box is one row of two team names. Split down the middle it
       reads as two different results. */
    .story-scorebar,
    figure,
    img {
        break-inside: avoid;
        page-break-inside: avoid;
    }

    /* A story may split, but never right after its own headline and never
       leaving one line stranded. */
    .story-card, .paired-stories { break-inside: auto; }

    /* A heading at the foot of a page, its content overleaf, is worse than a
       slightly short page. */
    .masthead,
    .section-title,
    .section-title-full,
    /* The one-line note under Honor Roll and Detention. Without it here, the
       heading binds to the NOTE and the two of them sit alone at the foot of
       a page with the players overleaf — which is exactly what the first ESPN
       paper printed. break-after: avoid only ever binds a block to whatever
       comes immediately next, so every element in a header stack needs it,
       not just the first. */
    .section-note,
    /* "Game stories, continued". It follows the classifieds page, so it is
       the first thing on a sheet — and a continuation line at the foot of a
       page, with the stories it introduces overleaf, is the same bug as the
       Honor Roll heading, one section further on. */
    .continued-note,
    .story-label,
    .col-section-label,
    .story-headline,
    .col-story-headline,
    /* A story's header is four blocks, not one:
           .story-label -> .story-headline -> .story-subhead -> .story-scorebar
       Only the first two were listed here, so the chain ended at the subhead
       and the page was free to break between the subhead and the score box.
       Measured over a sweep of the whole page height: 3 breaks landed exactly
       there, leaving a genre tag, a headline and a deck at the foot of a page
       with the score and the entire article overleaf. Every link in the chain
       needs the rule, because break-after: avoid binds a block only to
       whatever comes IMMEDIATELY after it. */
    .story-subhead,
    .story-scorebar,
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

    /* --- THE CLASSIFIEDS PAGE ----------------------------------------- */
    /* The one section in the paper that IS a page. Everything else is told to
       flow, because making sections unbreakable is what filled the paper with
       holes; this is the deliberate exception, and it earns it — a page of
       advertising that starts halfway down a sheet under the tail of the
       power rankings is not a page, it is a gap with pictures in it. */
    .publisher-page {
        break-before: page;
        page-break-before: always;
    }

    /* An ad is a single object. Split across a fold it is two broken objects,
       and unlike a paragraph it cannot be read across the break. */
    .pub-ad {
        break-inside: avoid;
        page-break-inside: avoid;
    }

    /* A GUARD, NOT A LAYOUT TOOL.
       The columns are sized so they come out level and every ad fills its
       column edge to edge — see pack_columns. Nothing here is needed to make
       a normal page fit, and this ceiling should never fire on one.
       It exists for the pathological upload: one 800x4000 screenshot of a
       group chat, which at any column width is taller than the sheet and
       would print as a blank page followed by a cropped one.
       Applied as a max-WIDTH computed from the ad's own aspect ratio, which
       is what --ad-aspect on each figure is for. That matters: `max-height`
       on an image that is also `width: 100%` does not scale the image down,
       it squashes it — the height obeys and the width does not, and a
       squashed meme is a cropped meme by another name. Capping the width
       scales both together, so the shape survives.
       An earlier version used this at 300px as the actual fitting mechanism.
       It worked, and it left every tall ad floating in the middle of its
       column with white down both sides — five ads, ten gutters, and a page
       that read as spaced out rather than as a classifieds sheet. */
    .publisher-page { --ad-cap: 620px; --ad-fit: 94%; }
    .pub-ad-frame {
        max-width: calc(var(--ad-cap) * var(--ad-aspect, 1));
    }

    /* THE LAST FEW PERCENT.
       The columns are sized to come out level, but nothing makes their
       combined height match a sheet — that depends entirely on which five
       images got uploaded. Measured at full width the first real page came
       out about 1% too tall, and 1% too tall is not a slightly cramped page,
       it is BOTH columns fragmenting and the bottom ad of each landing on a
       sheet of its own.
       So the block runs a little narrower than the page and centres. Narrower
       is shorter, in exact proportion, because every ad in it is locked to
       its own aspect ratio — 8% off the width takes 8% off the height and
       buys back roughly 55px of slack on Letter.
       The cost is a margin down each side of the block. That is a very
       different thing from the gutters the old height ceiling produced: this
       is white at the edge of the page, where a margin belongs, rather than
       white between ads, where it reads as the page being half empty.
       Deliberately not computed from a page size. Readers print with whatever
       margins their dialog is set to, and on A4 as well as Letter; a
       percentage is right in all of them. */
    .pub-ad-cols {
        max-width: var(--ad-fit);
        margin-left: auto;
        margin-right: auto;
    }

    /* On screen the page needs air above it. On paper it starts a fresh
       sheet, so a top margin is 30px of advertising thrown away. */
    .publisher-page { margin-top: 0 !important; }

    /* The page's own heading, tighter than a section heading in the body of
       the paper. Every millimetre here is a millimetre of advertising. */
    .publisher-page .section-title-full { margin-bottom: 9px !important; }

    /* Colour is the point of this page — a meme in greyscale is a meme with
       the joke removed — so the backgrounds print even where the rest of the
       paper is happy to be ink on white. */
    .pub-ad-frame, .pub-ad-img {
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
    }

    /* Nothing on this page is a click target on paper. */
    .pub-ad-link { text-decoration: none !important; color: inherit !important; }

    /* --- tighten for a smaller sheet ---------------------------------- */
    /* The screen layout assumes 1200px. A Letter page gives about 720px of
       printable width, so the same grid at the same type size would set the
       outer columns four words wide. */

    /* --- the front page stops being three columns ---------------------- */
    /* THE WEEK 1 PDF BUG.
       On screen the front page is [This Week | lead story | Standings] and the
       page simply gets taller — the columns end where they end and nobody
       notices. On paper a grid row cannot be split intelligently: the browser
       slices the whole row at each page boundary, so a long lead story becomes
       a thin ribbon running down the middle of three or four sheets with two
       columns of white space beside it, because the sidebars ran out on page
       one. That is exactly what "ugly and segmented" was.

       So in print it isn't a grid. The lead story runs first at full width and
       sets in two real newspaper columns; the sidebars become ordinary blocks
       underneath it and are free to break wherever they like. */
    .front-page {
        display: flex !important;
        flex-direction: column !important;
        grid-template-columns: none !important;
        padding: 0 18px 14px !important;
    }

    /* The lead is the lead: it goes first on the sheet, whatever the DOM
       order is for the screen layout. */
    .front-col-center { order: 1; }
    .front-page > .front-col:first-child { order: 2; }
    .front-page > .front-col:last-child  { order: 3; }

    /* The vertical rules separated columns that no longer sit side by side. */
    .front-col,
    .front-col:first-child,
    .front-col:last-child,
    .front-col-center {
        border-left: none !important;
        border-right: none !important;
        padding: 0 0 10px !important;
    }

    .front-page > .front-col:first-child,
    .front-page > .front-col:last-child {
        border-top: 1px solid #ccc !important;
        padding-top: 10px !important;
        margin-top: 10px !important;
    }

    /* Two columns of 9.5pt across a Letter sheet is roughly 60 characters a
       line, which is the measure a newspaper actually wants. One full-width
       column would be 110 and unreadable. */
    .lead-story {
        column-count: 2 !important;
        column-gap: 7mm !important;
        column-rule: 1px solid #ddd !important;
    }

    /* Keep the standings together if they fit on the remainder of a sheet,
       and keep the header with them if they don't. */
    .front-col .stats { break-inside: auto; }
    .front-col .stats thead { display: table-header-group; }
    .front-col .stats tr { break-inside: avoid; }

    /* Now that the sidebars are full width, one teaser per line wastes most of
       a sheet. Set them two up — and keep each teaser whole, because a headline
       stranded at the foot of a column with its score overleaf is worse than a
       slightly ragged column. */
    .front-page > .front-col:first-child {
        column-count: 2 !important;
        column-gap: 7mm !important;
    }
    .front-col .col-story,
    .front-col .col-section-label { break-inside: avoid; }
    .front-col .col-section-label { column-span: all; }

    /* A ten-row standings table stretched across a Letter sheet is mostly
       empty cell. Hold it to a sensible measure. */
    .front-page > .front-col:last-child .stats {
        max-width: 130mm !important;
        margin-left: auto !important;
        margin-right: auto !important;
    }

    .headline { font-size: 30pt !important; line-height: 1.05 !important; }
    .lead-story { font-size: 10pt !important; line-height: 1.5 !important; }
    .story-headline-lead { font-size: 17pt !important; }
    .story-headline-feature { font-size: 14pt !important; }
    .story-body, .col-story-body, .award-body { font-size: 9.5pt !important; }
    .col-story-headline { font-size: 11pt !important; }
    .full-section { padding: 0 18px !important; }
    .rankings-section { padding: 10px 18px !important; }

    /* --- DENSITY -------------------------------------------------------
       The screen stylesheet is built for a 1200px scroll where vertical
       whitespace is free and helps. On a fixed sheet it is the enemy: every
       28px gutter is a line of type not printed, and enough of them push a
       section onto a page it did not need.

       Measured: the paper was averaging over 20% blank on every page that had
       content after it. A real newspaper is dense because paper costs money;
       this one should read the same way. */
    .full-section,
    .wire-section,
    .classifieds-section,
    .subscribe-block { margin-top: 10px !important; margin-bottom: 10px !important; }

    .section-title-full {
        margin-bottom: 8px !important;
        padding: 3px 0 !important;
        font-size: 10pt !important;
    }
    .section-note { margin: 2px 0 6px !important; font-size: 8.5pt !important; }

    .story-card { margin-bottom: 10px !important; }
    .story-headline-lead { margin-bottom: 4px !important; }
    .player-grid { gap: 8px !important; }
    .awards-grid-full, .awards-grid { gap: 10px !important; }
    .rankings-row { gap: 8px !important; }
    .rankings-row + .rankings-row { margin-top: 8px !important; }
    .week-ticker { margin: 8px 0 0 !important; }
    .fraud-callout { margin: 10px 0 !important; padding: 10px 14px !important; }
    .player-card { padding: 6px 4px !important; }
    .award-card { padding: 10px 12px !important; }

    /* Type that fills a column rather than floating in one. */
    .story-body, .col-story-body, .award-body {
        line-height: 1.38 !important;
    }
    .lead-story { line-height: 1.42 !important; }

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
    .rankings-row { grid-template-columns: repeat(2, 1fr) !important; }
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

    /* The classifieds page is already light — it is a sheet of framed colour
       images and it looks the same in every theme on purpose. The only thing
       gameday would otherwise do to it is turn the caption white, on a cream
       frame. */
    .pub-ad-caption { color: #555 !important; }
    .pub-ad-frame { background: #fffdf8 !important; border-color: #111 !important; }

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

    /* The newer sections, which the dark theme draws light-on-dark. */
    :root { --accent: #0a7d2c; }
    .ticker-stat, .rankings-section, table.stats th, .story-scorebar, .lt-initial {
        background: #fff !important;
        color: #111 !important;
        border-color: #bbb !important;
    }
    .dateline-bar span:nth-child(2) { color: #222 !important; }
    .story-team-name, .obit-name, .lt-name, .promo-code, .letter-sign,
    .obit p, .promo-pitch { color: #111 !important; }
    .story-team-meta, .award-desc, .section-note { color: #555 !important; }
    .story-margin { color: #0a7d2c !important; }
    .back-page { border-color: #0a7d2c !important; }
    .bp-obits, .bp-promo.has-right, .bp-tx, .obit, .bp-label, .line-card,
    .line-row { border-color: #bbb !important; }
    .promo-code { border-color: #0a7d2c !important; }
    .line-bar { background: #ddd !important; }
    .line-chip { border-color: #555 !important; color: #333 !important; }
    .line-tag { background: #111 !important; color: #fff !important; border-color: #111 !important; }
    .line-tag.coin { background: #c8a200 !important; color: #111 !important; }
    .spark { color: #111 !important; }
    .spark-now { fill: #0a7d2c !important; }
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
#:
#: TWO KINDS OF PDF.
#:
#: Where the browser honours a page size set in CSS (Chrome, Edge, Brave,
#: Arc, Firefox — desktop and Android) the button makes ONE long page: the
#: paper exactly as it looks on screen, at full desktop width, top to bottom
#: with no breaks. A newspaper that is a web page reads better as one scroll
#: than as a web page chopped into Letter sheets.
#:
#: How: the paper is copied into an invisible 1200px-wide frame, the paged
#: print stylesheet (#cd-print-paged) is taken out of the copy, the copy is
#: measured, and a `@page { size: 1200px <height>px }` rule is written into it
#: before it is printed. Measuring in a frame of the print width — not the
#: reader's window — is what makes the height right on a phone, where the
#: window is 390px wide and the phone layout is a different height entirely.
#:
#: Everywhere else (Safari, and every browser on an iPhone or iPad, which are
#: all Safari underneath) the button falls back to window.print() and the
#: paged Letter/A4 layout below, which those browsers handle properly.
PRINT_BUTTON_HTML = """
<button type="button" class="print-button" onclick="cdSavePdf(this)"
        aria-label="Save this paper as a PDF">
    <span aria-hidden="true">&#8595;</span> Save as PDF
</button>
"""

#: The paper's width on screen (.page max-width). The long PDF is this wide,
#: so it lays out as the desktop paper.
LONG_PDF_WIDTH = 1200

#: Rules for the copy that becomes the long PDF. Applied to the copy only, so
#: they don't need @media print — the copy exists to be printed.
LONG_PRINT_CSS = """
html, body { margin: 0 !important; padding: 0 !important; }
.page {
    margin: 0 auto !important;
    box-shadow: none !important;
    border: none !important;
    max-width: none !important;
}
.print-button, .ce-bar, .subscribe-form, .image-slot-empty, .ce-remove,
button, input, select, textarea { display: none !important; }
.image-wrap, .image-wrap-editing { resize: none !important; }
/* It is a file, not ink on a sheet: print every colour, exactly as on screen. */
* { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
.print-footer {
    display: block !important;
    margin: 0 !important;
    padding: 10px 18px 14px;
    border-top: 1px solid #999;
    font-family: "Helvetica Neue", Arial, sans-serif;
    font-size: 11px;
    letter-spacing: 0.4px;
    color: #555;
    text-align: center;
}
.print-footer a { color: #555; text-decoration: none; }
"""

PRINT_SCRIPT = (
    "<script>\n"
    "(function () {\n"
    "  var WIDTH = " + str(LONG_PDF_WIDTH) + ";\n"
    "  var LONG_CSS = " + json.dumps(LONG_PRINT_CSS) + ";\n"
    r"""
  // Browsers whose Save-as-PDF obeys a CSS page size. Every iOS browser is
  // WebKit, and desktop Safari's support is patchy, so those get pages.
  function longSupported() {
    var ua = navigator.userAgent || "";
    var ios = /iPhone|iPad|iPod/.test(ua) ||
              (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    if (ios) return false;
    if (/Firefox\//.test(ua)) return true;
    return /Chrome\/|Chromium\/|Edg\//.test(ua);
  }

  function paged() { window.print(); }

  function waitForImages(doc) {
    var imgs = Array.prototype.slice.call(doc.images);
    return Promise.all(imgs.map(function (img) {
      if (img.complete) return null;
      return new Promise(function (ok) {
        img.addEventListener("load", ok); img.addEventListener("error", ok);
        setTimeout(ok, 4000);
      });
    }));
  }

  function stripNarrowRules(doc) {
    for (var i = 0; i < doc.styleSheets.length; i++) {
      var sheet = doc.styleSheets[i], rules;
      try { rules = sheet.cssRules; } catch (e) { continue; }  // cross-origin fonts
      for (var r = rules.length - 1; r >= 0; r--) {
        var rule = rules[r];
        if (rule.media && /max-width/.test(rule.media.mediaText)) sheet.deleteRule(r);
      }
    }
  }

  window.cdSavePdf = function (button) {
    if (!longSupported()) return paged();
    var label = button ? button.innerHTML : "";
    if (button) { button.disabled = true; button.textContent = "Preparing PDF…"; }
    function done() { if (button) { button.disabled = false; button.innerHTML = label; } }

    // A copy of the paper, not the paper: the reader's page is left alone.
    var copy = document.documentElement.cloneNode(true);
    var drop = copy.querySelectorAll("#cd-print-paged, script");
    for (var i = 0; i < drop.length; i++) drop[i].parentNode.removeChild(drop[i]);
    // Offscreen, lazy images never load. Every one has to be in the PDF.
    var lazy = copy.querySelectorAll("img[loading]");
    for (var j = 0; j < lazy.length; j++) lazy[j].removeAttribute("loading");
    var head = copy.querySelector("head");
    var base = document.createElement("base");
    base.href = document.baseURI;
    head.insertBefore(base, head.firstChild);
    var style = document.createElement("style");
    style.textContent = LONG_CSS;
    head.appendChild(style);

    var frame = document.createElement("iframe");
    frame.setAttribute("aria-hidden", "true");
    frame.style.cssText = "position:fixed;left:-20000px;top:0;border:0;" +
                          "width:" + WIDTH + "px;height:1000px;opacity:0;pointer-events:none;";
    document.body.appendChild(frame);

    var cleaned = false;
    function cleanup() {
      if (cleaned) return; cleaned = true; done();
      setTimeout(function () { if (frame.parentNode) frame.parentNode.removeChild(frame); }, 500);
    }

    frame.onload = function () {
      var win = frame.contentWindow, doc = frame.contentDocument;
      var fonts = doc.fonts && doc.fonts.ready ? doc.fonts.ready : Promise.resolve();
      Promise.all([fonts, waitForImages(doc)]).then(function () {
        // Chrome evaluates media queries for print against a default sheet
        // width, not the page size set below, so the copy would print with
        // the tablet layout (one column) while being measured as desktop.
        // The copy is only ever desktop width, so its narrow-screen rules
        // are dead weight anyway: take them out, and screen and print agree.
        stripNarrowRules(doc);
        var page = doc.querySelector(".page");
        // Any sliver below the paper shows the body, so make it the paper.
        if (page) doc.body.style.background = win.getComputedStyle(page).backgroundColor;
        var root = doc.documentElement;
        var h = Math.max(Math.ceil(root.getBoundingClientRect().height),
                         root.scrollHeight) + 4;
        var size = doc.createElement("style");
        size.textContent = "@page { size: " + WIDTH + "px " + h + "px; margin: 0; }";
        doc.head.appendChild(size);
        win.addEventListener("afterprint", cleanup);
        try { win.focus(); win.print(); } catch (e) { cleanup(); paged(); return; }
        // Chrome's print() returns once the dialog closes; afterprint may not fire.
        setTimeout(cleanup, 1000);
      }).catch(function () { cleanup(); paged(); });
    };
    frame.srcdoc = "<!DOCTYPE html>" + copy.outerHTML;
  };
})();
</script>
""")

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
            /* A fixed button on a phone covers whatever is under it, and what
               is under it is the paragraph somebody is reading. There is no
               corner where that isn't true — the screen is all text.

               So on phones it stops floating and sits at the end of the paper,
               which is also where somebody who has just finished reading would
               look for it. Saving a PDF is not something anyone does halfway
               through a story on a phone. */
            .print-button {
                position: static;
                display: block;
                width: calc(100% - 24px);
                margin: 22px auto 28px;
                font-size: 13px;
                padding: 13px 16px;
                box-shadow: none;
            }
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
