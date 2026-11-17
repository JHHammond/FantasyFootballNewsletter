"""
Visual themes for the paper.

The base stylesheet in newspaper.py IS the tabloid look — loud, red, condensed,
built to shout from a newsstand. Rather than rewrite 700 lines of working CSS
into abstract tokens, each additional theme layers overrides on top of it. The
default is therefore untouched by definition, and a theme is a self-contained
block of CSS anyone can read and tweak without understanding the whole file.

Adding a theme: give it fonts, an override block, and register it in THEMES.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# TABLOID — the original. No overrides; this is the base stylesheet.
# ---------------------------------------------------------------------------

TABLOID_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com" />'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Playfair+Display:ital,wght@0,700;0,900;1,700;1,900'
    '&family=Barlow+Condensed:wght@400;600;700;800;900'
    '&display=swap" rel="stylesheet" />'
)

TABLOID_CSS = ""


# ---------------------------------------------------------------------------
# BROADSHEET — the paper of record.
#
# Restraint is the whole point: no red, hairline rules instead of heavy bars,
# and body text that expects to be read rather than glanced at. The same
# brutal prose lands differently when it's set like this.
#
# The masthead is a high-contrast didone in spaced capitals, not blackletter.
# Blackletter only reads as "newspaper" on the two or three mastheads everyone
# already knows; on an unfamiliar name it just reads as a heavy metal album,
# and it's close to illegible at the sizes a phone gives you. Didone caps do
# the same job -- old, serious, engraved -- while staying readable.
# ---------------------------------------------------------------------------

BROADSHEET_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com" />'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Bodoni+Moda:opsz,wght@6..96,400;6..96,700;6..96,900'
    '&family=Libre+Baskerville:ital,wght@0,400;0,700;1,400'
    '&family=Libre+Franklin:wght@400;600;700'
    '&display=swap" rel="stylesheet" />'
)

BROADSHEET_CSS = """
/* ===== BROADSHEET =====
   The base stylesheet is a tabloid: red masthead bar, 72px white type with a
   hard black shadow, heavy rules. Every one of those has to be actively
   switched off here, not just re-fonted — otherwise blackletter ends up
   sitting on a red banner, which is what happened the first time. */

body { background: #f4f2ed !important; }
.page { background: #fffefb !important; }

/* --- masthead ------------------------------------------------------- */
.masthead {
    background: #fffefb !important;
    border-bottom: 3px double #111 !important;
    padding: 30px 36px 12px !important;
}
/* Bodoni's thin strokes are the whole effect, so it needs the display optical
   size (opsz 96) and real air between the letters. clamp() rather than a fixed
   size because league names run from "The Bee" to eleven syllables of inside
   joke, and a masthead that wraps looks broken. */
.paper-name {
    font-family: "Bodoni Moda", "Playfair Display", Didot, Georgia, serif !important;
    font-variation-settings: "opsz" 96 !important;
    font-size: clamp(30px, 6.4vw, 62px) !important;
    font-weight: 700 !important;
    letter-spacing: 0.055em !important;
    text-indent: 0.055em !important;  /* re-centre: tracking pads the right only */
    text-transform: uppercase !important;
    color: #111 !important;
    text-shadow: none !important;
    line-height: 1.05 !important;
    word-break: break-word !important;
}
.edition-line {
    font-family: "Libre Franklin", sans-serif !important;
    font-size: 10px !important;
    letter-spacing: 2.4px !important;
    font-weight: 600 !important;
    color: #55504a !important;
    border-top: 1px solid #d6d2c8 !important;
    margin-top: 12px !important;
    padding-top: 9px !important;
}

/* --- headline -------------------------------------------------------- */
/* A broadsheet lead is set far smaller than a tabloid scream. The all-caps
   is undone in Python (CSS can't lowercase words it wasn't given). */
.headline {
    font-family: "Libre Baskerville", Georgia, serif !important;
    font-size: 38px !important;
    font-weight: 700 !important;
    letter-spacing: -0.4px !important;
    line-height: 1.16 !important;
    text-transform: none !important;
    color: #111 !important;
    border-bottom: 1px solid #111 !important;
    padding-bottom: 14px !important;
    margin: 22px auto 6px !important;
    max-width: 88% !important;
}

.dateline-bar {
    border-top: 1px solid #111 !important;
    border-bottom: 1px solid #111 !important;
    background: transparent !important;
    font-family: "Libre Franklin", sans-serif !important;
    font-size: 11px !important;
    color: #55504a !important;
}

/* --- body ------------------------------------------------------------ */
.lead-story, .story-body, .col-story-body, .award-body, .fraud-callout-body {
    font-family: "Libre Baskerville", Georgia, serif !important;
    line-height: 1.75 !important;
    color: #1a1a1a !important;
}
.lead-story { font-size: 14.5px !important; }

.story-headline, .col-story-headline {
    font-family: "Libre Baskerville", Georgia, serif !important;
    font-weight: 700 !important;
    text-transform: none !important;
    letter-spacing: -0.2px !important;
    color: #111 !important;
}
.story-headline-lead { font-size: 27px !important; }
.story-headline-feature { font-size: 22px !important; }

.story-label, .col-section-label, .section-title, .section-title-full {
    font-family: "Libre Franklin", sans-serif !important;
    font-weight: 600 !important;
    font-size: 10px !important;
    letter-spacing: 2.6px !important;
    color: #6a6560 !important;
    background: transparent !important;
    border-bottom: 1px solid #111 !important;
}

.story-subhead, .col-story-teaser {
    font-family: "Libre Franklin", sans-serif !important;
    color: #55504a !important;
    font-style: normal !important;
}

/* The tabloid's red is doing all the shouting. A broadsheet doesn't shout. */
.ranking-card-rank { color: #111 !important; }

/* The newer sections: ink, not red. */
:root { --accent: #111; }
.bp-label, .letter-kicker {
    font-family: "Libre Franklin", sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 2.6px !important;
}
.letter-kicker { font-size: 11px !important; border-bottom-width: 1px !important; }
.line-bar { background: #e4e0d6 !important; }

.fraud-callout {
    background: #f2efe8 !important;
    border: 1px solid #111 !important;
    color: #111 !important;
}
.fraud-callout-label {
    font-family: "Libre Franklin", sans-serif !important;
    color: #7d1b1b !important;
    letter-spacing: 2.6px !important;
}
.fraud-callout-body { color: #1a1a1a !important; }

.award-card, .ranking-card, .story-card, .player-card {
    border: 1px solid #ddd9d0 !important;
    background: #fffefb !important;
}
.award-title, .ranking-card-name {
    font-family: "Libre Franklin", sans-serif !important;
    font-weight: 700 !important;
    letter-spacing: 1.2px !important;
    color: #111 !important;
}

.pull-quote {
    font-family: "Libre Baskerville", Georgia, serif !important;
    font-style: italic !important;
    border-left: 2px solid #111 !important;
    border-top: none !important;
    border-bottom: none !important;
    color: #333 !important;
}

.wk-cell.down .wk-num { color: #111 !important; }
.hd-card { border-top-color: #111 !important; }
.hd-rank, .hd-fill { background: #111 !important; color: #fff !important; }
.hd-num { color: #111 !important; }
.hd-detention .hd-num { color: #7d1b1b !important; }
.hd-detention .hd-fill, .hd-detention .hd-rank { background: #7d1b1b !important; }
.fc-stamp { border-color: #7d1b1b !important; color: #7d1b1b !important; }
.fc-tag { color: #7d1b1b !important; }

.week-ticker {
    background: #f2efe8 !important;
    border-top: 1px solid #111 !important;
    border-bottom: 1px solid #111 !important;
}
.week-ticker * { color: #111 !important; }

.image-wrap img, .hero-image-wrap img { border: 1px solid #cfcabd !important; }
.photo-caption {
    font-family: "Libre Franklin", sans-serif !important;
    color: #6a6560 !important;
}

/* .paper-name is sized with clamp(), so it needs no breakpoint of its own. */
@media (max-width: 600px) {
    .paper-name { letter-spacing: 0.04em !important; text-indent: 0.04em !important; }
    .headline { font-size: 25px !important; max-width: 100% !important; }
}
"""


# ---------------------------------------------------------------------------
# GAMEDAY — broadcast graphics.
#
# Not a newspaper at all. Dark, condensed, enormous numbers, one hot accent.
# The reference is a lower-third during a night game, or the poster taped up
# in a sports bar. Loud in a completely different register from the tabloid.
# ---------------------------------------------------------------------------

GAMEDAY_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com" />'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Anton'
    '&family=Barlow+Condensed:wght@500;600;700;800;900'
    '&family=Inter:wght@400;500;600;700;800'
    '&display=swap" rel="stylesheet" />'
)

GAMEDAY_CSS = """
/* The newer sections take the broadcast green. */
:root { --accent: #16f04e; }

/* Everything that was drawn light-on-paper for the tabloid. */
.dateline-bar span:nth-child(2) { color: #e8ecf2 !important; }
.ticker-stat { background: #1c2028 !important; border-color: #2b313c !important; }
.rankings-section { background: #0f1216 !important; }
table.stats th { background: #0f1216 !important; color: #8d9bad !important; }
.story-scorebar { background: #0f1216 !important; border-color: #2b313c !important; }
.story-team-name { color: #ffffff !important; }
.story-team-meta, .award-desc, .section-note { color: #8d9bad !important; }
.story-margin { color: #16f04e !important; }
.st-pts-win { color: #ffffff !important; }
.st-pts-lose { color: #8d9bad !important; }

/* The commissioner's letter. */
.letter-kicker {
    font-family: "Barlow Condensed", sans-serif !important;
    font-weight: 800 !important;
    letter-spacing: 3px !important;
}
.letter-sign { color: #ccd5e0 !important; }

/* The back page: obituaries, the promo, the lines, transactions. */
.back-page { border-color: #16f04e !important; border-width: 2px 0 !important; }
.bp-obits, .bp-promo.has-right, .bp-tx, .obit, .bp-label, .line-card, .line-row {
    border-color: #2b313c !important;
}
.bp-label {
    font-family: "Barlow Condensed", sans-serif !important;
    font-weight: 800 !important;
    letter-spacing: 3px !important;
}
.back-page { font-family: "Inter", system-ui, sans-serif !important; }
.obit-name, .lt-name {
    color: #ffffff !important;
    font-family: "Barlow Condensed", sans-serif !important;
    font-weight: 800 !important;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.obit p, .promo-pitch { color: #ccd5e0 !important; }
.promo-code { border-color: #16f04e !important; color: #ffffff !important;
              font-family: "Anton", sans-serif !important; font-weight: 400 !important; }
.line-card { background: #12161d !important; border-color: #2b313c !important; }
.lc-head { background: #16f04e !important; color: #0b0d10 !important; }
.lc-head .line-chip { color: #0b0d10 !important; }
.line-team + .line-team, .lv-side + .lv-side { border-color: #2b313c !important; }
.lt-spread { font-family: "Anton", sans-serif !important; font-weight: 400 !important;
             letter-spacing: 0 !important; color: #ffffff; }
.line-team.fav .lt-spread { color: #16f04e !important; }
.line-lead .lc-head { background: #16f04e !important; }
.ll-spread { font-family: "Anton", sans-serif !important; font-weight: 400 !important;
             letter-spacing: 0 !important; color: #16f04e !important; }
.ll-name { color: #ffffff !important; font-family: "Barlow Condensed", sans-serif !important;
           text-transform: uppercase; }
.ll-bar.fav { background: #16f04e !important; }
.ll-bar.dog { background: #e9edf2 !important; }
.ll-meet { border-color: #2b313c !important; color: #ccd5e0; }
.ll-meet b { color: #16f04e !important; }
.lt-avatar { border-color: #8d9bad !important; }
.lt-initial { background: #1c2028 !important; color: #ffffff !important; }
.line-bar { background: #2b313c !important; }
.line-chip { border-color: #8d9bad !important; color: #ccd5e0 !important; }
.line-tag { background: #e8ecf2 !important; color: #0b0d10 !important; border-color: #e8ecf2 !important; }
.line-tag.coin { background: #c8a200 !important; color: #111 !important; border-color: #c8a200 !important; }
/* The league's votes on the lines (12 Oct). */
.lv-bar { background: #2b313c !important; }

/* standings trend lines: light ink on the dark page */
.spark { color: #e9edf2 !important; }
.spark-now { fill: #16f04e !important; }

/* ===== GAMEDAY ===== */
body { background: #0b0d10 !important; color: #f2f4f7 !important; }
.page {
    background: #14171c !important;
    box-shadow: none !important;
    border: none !important;
}

.masthead {
    background: linear-gradient(100deg, #16f04e 0%, #0bc93f 100%);
    border: none !important;
    padding: 18px 20px !important;
    margin-bottom: 0 !important;
}
.paper-name {
    font-family: "Anton", "Barlow Condensed", sans-serif !important;
    font-weight: 400 !important;
    text-transform: uppercase !important;
    letter-spacing: -1px !important;
    color: #06210e !important;
}
.edition-line {
    font-family: "Barlow Condensed", sans-serif !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: 3px !important;
    color: rgba(6, 33, 14, 0.72) !important;
}

.above-fold { background: #14171c !important; padding-top: 18px; }
.headline {
    font-family: "Anton", sans-serif !important;
    font-weight: 400 !important;
    text-transform: uppercase !important;
    letter-spacing: -0.5px !important;
    line-height: 0.98 !important;
    color: #ffffff !important;
}

.dateline-bar {
    background: #1c2028 !important;
    border-top: 2px solid #16f04e !important;
    border-bottom: 2px solid #16f04e !important;
    font-family: "Barlow Condensed", sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 1.6px !important;
    text-transform: uppercase;
    color: #9fb0c4 !important;
}

.front-col, .story-card, .award-card, .ranking-card, .paired-stories {
    background: #1c2028 !important;
    border: 1px solid #2b313c !important;
    color: #e8ecf2 !important;
}
.front-col { border-right: 1px solid #2b313c !important; }

.lead-story, .story-body, .col-story-body, .award-body, .fraud-callout-body {
    font-family: "Inter", system-ui, sans-serif !important;
    line-height: 1.62 !important;
    color: #ccd5e0 !important;
}

.story-headline, .col-story-headline, .headline, .paper-name {
    color: #ffffff !important;
}
.story-headline {
    font-family: "Anton", sans-serif !important;
    font-weight: 400 !important;
    text-transform: uppercase !important;
    letter-spacing: -0.3px !important;
    line-height: 1.02 !important;
}
.col-story-headline {
    font-family: "Barlow Condensed", sans-serif !important;
    font-weight: 800 !important;
    text-transform: uppercase !important;
}

.story-label, .col-section-label, .section-title, .section-title-full {
    font-family: "Barlow Condensed", sans-serif !important;
    font-weight: 800 !important;
    text-transform: uppercase !important;
    letter-spacing: 3px !important;
    color: #16f04e !important;
    background: transparent !important;
    border-bottom: 2px solid #2b313c !important;
}

.story-subhead, .col-story-teaser, .paper-meta, .hero-caption {
    color: #8d9bad !important;
}

/* Scores are the point. Make them enormous. */
.ranking-card-rank {
    font-family: "Anton", sans-serif !important;
    font-size: 34px !important;
    color: #16f04e !important;
}
.ranking-card-name {
    font-family: "Barlow Condensed", sans-serif !important;
    font-weight: 800 !important;
    text-transform: uppercase !important;
    color: #ffffff !important;
}
.ranking-card-comment { color: #9fb0c4 !important; }

.award-title {
    font-family: "Barlow Condensed", sans-serif !important;
    font-weight: 900 !important;
    letter-spacing: 2px !important;
    color: #16f04e !important;
}

.numbers-box {
    background: #1c2028 !important;
    border-color: #2b313c !important;
    color: #e8ecf2 !important;
}
.nb-table th { border-bottom-color: #2b313c !important; }
.nb-table td { border-bottom-color: #2b313c !important; }
.aw-pos { background: #16f04e !important; color: #0f1218 !important; }
.aw-photo { border-color: #2b313c !important; }
.aw-stat.up b { color: #16f04e !important; }
.aw-stat.down b { color: #ff4d5e !important; }
.award-sponsored { border-color: #8f6bff !important; }

.fraud-callout {
    background: #2a0f12 !important;
    border: 2px solid #ff2d4a !important;
}
.fraud-callout-label {
    font-family: "Barlow Condensed", sans-serif !important;
    color: #ff2d4a !important;
    letter-spacing: 3px !important;
}
.fraud-callout-body { color: #ffd9de !important; }

.pull-quote {
    font-family: "Barlow Condensed", sans-serif !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    color: #16f04e !important;
    border-left: 4px solid #16f04e !important;
    border-top: none !important;
    border-bottom: none !important;
}

.week-ticker {
    background: #0b0d10 !important;
    border-top: 2px solid #16f04e !important;
    border-bottom: 2px solid #16f04e !important;
}
.week-ticker * { color: #f2f4f7 !important; }

.player-card { background: #1c2028 !important; border-color: #2b313c !important; }
.player-card div { color: #e8ecf2 !important; }
.wk-board { border-top: 2px solid #16f04e !important; border-bottom: 1px solid #2b313c !important; }
.wk-head { color: #16f04e !important; border-bottom-color: #2b313c !important; }
.wk-cell { border-color: #2b313c !important; }
.wk-num { color: #e8ecf2 !important; }
.wk-cell.down .wk-num { color: #ff4d5e !important; }
.wk-label, .wk-unit, .wk-who { color: #9aa3b2 !important; }
.wk-num { font-family: "Anton", sans-serif !important; font-weight: 400 !important; }
.hd-card { border-top-color: #16f04e !important; }
.hd-rank, .hd-fill { background: #16f04e !important; color: #0b0d10 !important; }
.player-card .hd-num { color: #16f04e !important; font-family: "Anton", sans-serif !important; font-weight: 400 !important; }
.hd-detention .hd-card { border-top-color: #ff4d5e !important; }
.hd-detention .hd-rank, .hd-detention .hd-fill { background: #ff4d5e !important; color: #0b0d10 !important; }
.player-card.hd-card .hd-num { color: #16f04e !important; }
.hd-detention .player-card.hd-card .hd-num { color: #ff4d5e !important; }
.hd-bar { background: #2b313c !important; }
.hd-proj { background: #e8ecf2 !important; }
.hd-pos { background: #e8ecf2 !important; color: #0b0d10 !important; }
.hd-photo { border-color: #2b313c !important; }
.fc-head { border-bottom-color: #4a1a20 !important; }
.fc-stamp { border-color: #ff4d5e !important; color: #ff4d5e !important; }
.fc-file { background: rgba(255,77,94,.08) !important; border-color: rgba(255,77,94,.5) !important; }
.fc-tag { color: #ff4d5e !important; }
.fc-name, .fc-fact b { color: #ffffff !important; }

table, td, th { color: #ccd5e0 !important; border-color: #2b313c !important; }
thead tr { border-bottom-color: #16f04e !important; }

.classifieds-section {
    border-color: #2b313c !important;
}
.classifieds-title { color: #8d9bad !important; }
.classified { border-color: #2b313c !important; }
.classified-heading { color: #ffffff !important; }
.classified-body { color: #9fb0c4 !important; }
.classified-contact { color: #16f04e !important; }

.subscribe-block {
    background: #1c2028 !important;
    border: 2px solid #16f04e !important;
}
.subscribe-kicker { color: #16f04e !important; }
.subscribe-head { font-family: "Anton", sans-serif !important;
                  text-transform: uppercase !important; color: #fff !important; }
.subscribe-sub, .subscribe-fine { color: #9fb0c4 !important; }
.subscribe-form input { background: #0b0d10 !important; color: #fff !important;
                        border-color: #2b313c !important; }
.subscribe-form button { background: #16f04e !important; color: #06210e !important;
                         font-weight: 800 !important; }

.image-wrap img, .hero-image-wrap img { border: 1px solid #2b313c !important; }
/* Draft Regrets on the trade market (26 Oct): dark type on the green
   button, and the hollow draft-day dot filled with the page. */
.dr-button { color: #0b0d10 !important; }
.pc-start { fill: #0b0d10 !important; }
/* The chart's paper-strength colours are too dim on the dark page. */
.pc-svg { filter: brightness(1.55) saturate(1.15); }
"""


# ---------------------------------------------------------------------------
# MODERN — the phone layout's look, built out for a computer (17 Nov).
#
# John picked it from a design-canvas mockup of The Hands Times, week 10.
# Anton capitals, red game labels, black bars, rank boxes, big red numbers,
# rules instead of boxes. Type and colour apply everywhere; the multi-column
# layout only above phone width, so web/compact.py still owns the phone.
# ---------------------------------------------------------------------------

MODERN_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com" />'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Anton'
    '&family=Playfair+Display:ital,wght@0,700;0,900;1,700'
    '&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400'
    '&family=Barlow+Condensed:wght@600;700;800'
    '&display=swap" rel="stylesheet" />'
)

MODERN_CSS = """
/* ===== MODERN (17 Nov) =====
   The phone layout's look, built out for a computer screen (John picked the
   design-canvas mockup "The Hands Times – desktop redesign"): Anton tabloid
   capitals for headlines, a red label saying what kind of game it was, black
   bars on This Week and Fraud Watch, rank boxes, big red numbers, and rules
   and white space where the tabloid draws boxes. Colours and type apply
   everywhere; every LAYOUT change sits inside the desktop query, so the
   phone layer (web/compact.py) and print are left alone. */
:root { --accent: #c40000; }
body { background: #f4efe6 !important; font-family: "Source Serif 4", Georgia, serif !important; }
.page { background: #f4efe6 !important; box-shadow: none !important; border: 0 !important; }

/* Nameplate: ink on paper over a red and a black rule, not the red band. */
.masthead { background: transparent !important; border-bottom: 0 !important; padding: 26px 48px 0 !important; }
.paper-name { font-family: "Playfair Display", Georgia, serif !important; font-weight: 900 !important;
    text-transform: none !important; color: #111 !important; text-shadow: none !important;
    letter-spacing: -1px !important; line-height: 1 !important;
    padding-bottom: 14px; border-bottom: 6px solid var(--accent); box-shadow: 0 5px 0 #f4efe6, 0 7px 0 #111; }
.edition-line { color: #5a5146 !important; border-top: 0 !important; margin-top: 18px !important;
    font-family: "Barlow Condensed", sans-serif !important; font-weight: 700 !important;
    letter-spacing: .16em !important; font-size: 13px !important; padding-top: 0 !important; }

/* Front headline: Anton capitals, centred, a short rule under it. */
.above-fold { border-bottom: 0 !important; }
.headline { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important;
    letter-spacing: 0 !important; border-bottom: 0 !important; text-wrap: balance;
    max-width: 980px; margin-left: auto !important; margin-right: auto !important; }
.headline::after { content: ""; display: block; width: 60px; height: 3px; background: #111; margin: 18px auto 0; }
.dateline-bar { border-top: 1px solid #111; border-bottom: 1px solid #cfc6b6 !important;
    font-family: "Barlow Condensed", sans-serif !important; font-weight: 700; letter-spacing: .12em !important; }
.dateline-bar span:nth-child(2) { font-family: "Source Serif 4", Georgia, serif; font-weight: 400 !important;
    letter-spacing: 0; text-transform: none; color: #3a332b !important; }
.section-nav { background: transparent !important; border-color: #111 !important; }
.section-nav a { font-family: "Barlow Condensed", sans-serif !important; font-weight: 700 !important; letter-spacing: .14em !important; }

/* Section heads: Anton, under a heavy rule. This Week and Fraud Watch get a black bar. */
.col-section-label, .wk-head, .section-title-full, .bp-label, .classifieds-title {
    font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important;
    letter-spacing: .02em !important; color: #111 !important; text-transform: uppercase !important;
    background: none !important; text-align: left !important; }
.front-col:first-child .col-section-label { background: #111 !important; color: #fff !important;
    padding: 8px 12px !important; font-size: 20px !important; border: 0 !important; }

/* This Week: a red stripe, the label in red, the teaser in Anton capitals. */
.col-story { border-left: 5px solid var(--accent); padding: 12px 0 12px 14px !important;
    border-bottom: 1px solid #cfc6b6 !important; margin: 0 !important; }
.col-story-headline, .col-story-headline a { font-family: "Barlow Condensed", sans-serif !important;
    font-weight: 700 !important; font-size: 13px !important; letter-spacing: .1em !important;
    color: var(--accent) !important; text-transform: uppercase; }
.col-story-teaser { font-family: "Anton", Impact, sans-serif !important; font-style: normal !important;
    font-size: 20px !important; line-height: 1.08 !important; color: #111 !important; text-transform: uppercase; }
.col-story-body { color: #5a5146 !important; }

/* Lead story: big red first letter. */
.lead-story > p:first-child::first-letter { float: left; font-family: "Anton", Impact, sans-serif;
    font-size: 64px; line-height: .85; margin: 6px 8px 0 0; color: var(--accent); }
.photo-caption { font-family: "Source Serif 4", Georgia, serif !important; text-transform: none !important;
    letter-spacing: 0 !important; color: #5a5146 !important; font-size: 13px !important; }

/* Standings: rank in a black box (red for first). */
table.stats th { background: transparent !important; color: #5a5146 !important; border-bottom: 2px solid #111 !important;
    font-family: "Barlow Condensed", sans-serif !important; letter-spacing: .1em; }
table.stats td { border-bottom: 1px solid #cfc6b6 !important; background: transparent !important; }
table.stats td.order-num { font-family: "Anton", Impact, sans-serif !important; color: #fff !important; width: 30px; }
table.stats tbody tr td.order-num { background: #111 !important; text-align: center; box-shadow: inset 0 0 0 4px #f4efe6; }
table.stats tbody tr:first-child td.order-num { background: var(--accent) !important; }
table.stats td:nth-child(3) { font-family: "Anton", Impact, sans-serif !important; font-size: 17px !important; }

/* The week in numbers: big red Anton figures. */
.wk-num { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important; color: var(--accent) !important; }
.wk-label { font-family: "Barlow Condensed", sans-serif !important; font-weight: 700 !important; color: #111 !important; }
.stack-big { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important; color: var(--accent) !important; }

/* Game stories: Anton headline under a short red rule, the label in red. */
.story-card { border-color: #111 !important; }
.story-label { font-family: "Barlow Condensed", sans-serif !important; font-weight: 700 !important;
    letter-spacing: .14em !important; color: var(--accent) !important; background: none !important;
    padding: 0 !important; border-top: 3px solid var(--accent); display: inline-block; padding-top: 8px !important; }
.story-headline { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important; letter-spacing: 0 !important; }
.story-subhead { font-family: "Source Serif 4", Georgia, serif !important; color: #5a5146 !important; }
.story-scorebar { background: transparent !important; border: 0 !important; border-top: 2px solid #111 !important;
    border-bottom: 1px solid #cfc6b6 !important; border-radius: 0 !important; }
.story-margin .sm-num { font-family: "Anton", Impact, sans-serif !important; color: var(--accent) !important; }
.pull-quote { font-family: "Playfair Display", Georgia, serif !important; font-style: italic !important;
    font-weight: 700 !important; border-left: 0 !important; padding-left: 0 !important; }

/* Honor Roll and Detention: ranked rows, not cards. */
.hd-card { border: 0 !important; border-bottom: 1px solid #cfc6b6 !important; background: transparent !important;
    box-shadow: none !important; border-radius: 0 !important; }
.hd-num { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important; }
.hd-honor .hd-num { color: #111 !important; }
.hd-honor .hd-fill { background: #111 !important; }
.hd-honor .hd-proj { background: var(--accent) !important; }
.hd-rank { background: transparent !important; color: var(--accent) !important; font-family: "Anton", Impact, sans-serif !important;
    font-size: 20px !important; border: 0 !important; }
.hd-detention .hd-rank { color: #111 !important; }
.hd-pos, .aw-pos { background: #111 !important; color: #fff !important; font-family: "Barlow Condensed", sans-serif !important; }

/* Fraud Watch keeps its box, under a black bar with a flat red label. */
.fraud-callout { border: 2px solid #111 !important; background: #fffdf8 !important; padding: 0 !important; box-shadow: none !important; }
.fc-head { background: #111 !important; padding: 10px 20px !important; margin: 0 !important; }
.fraud-callout-label { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important; color: #fff !important;
    font-size: 24px !important; letter-spacing: .03em !important; }
.fc-stamp { transform: none !important; border: 0 !important; background: var(--accent) !important; color: #fff !important;
    font-family: "Barlow Condensed", sans-serif !important; font-weight: 700 !important; letter-spacing: .14em !important;
    padding: 4px 10px !important; box-shadow: none !important; opacity: 1 !important; }
.fc-file { background: transparent !important; border: 0 !important; margin: 18px 20px 0 !important; }
.fc-name, .fc-fact b { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important; }
.fraud-callout-body { padding: 12px 20px 20px !important; font-size: 18px !important; }

/* Awards: four across, divided by rules, no cards. */
.award-card { border: 0 !important; background: transparent !important; box-shadow: none !important; border-radius: 0 !important; }
.award-title { font-family: "Barlow Condensed", sans-serif !important; font-weight: 700 !important;
    letter-spacing: .12em !important; color: var(--accent) !important; border-bottom: 0 !important; }
.aw-stat b { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important; font-size: 30px !important; }

/* Power rankings: thin rows, a big rank number. */
.rankings-section { background: transparent !important; }
.ranking-card { border: 0 !important; background: transparent !important; box-shadow: none !important; border-radius: 0 !important; }
.ranking-card-rank { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important; color: #111 !important; }
.rankings-row:first-child .ranking-card:first-child .ranking-card-rank { color: var(--accent) !important; }
.ranking-card-comment { font-style: normal !important; color: #3a332b !important; opacity: 1 !important; }

/* Back page */
.obit-name { font-family: "Playfair Display", Georgia, serif !important; font-weight: 900 !important; font-size: 19px !important; }
.obit { border-bottom: 1px solid #cfc6b6 !important; }
.bp-obits.has-right { border-right: 1px solid #cfc6b6 !important; }
.subscribe-block { background: #fffdf8 !important; border: 2px solid #111 !important; }
.subscribe-head { font-family: "Playfair Display", Georgia, serif !important; }
.subscribe-form button { background: var(--accent) !important; }
.classified-heading { font-family: "Barlow Condensed", sans-serif !important; letter-spacing: .08em !important; }

/* ===== Desktop layout ===== */
@media screen and (min-width: 761px) {
  .page { max-width: 1320px !important; margin: 0 auto !important; padding-bottom: 64px !important; }
  main.page { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); column-gap: 0; }
  main.page > * { grid-column: 1 / -1; min-width: 0; }
  .masthead, .above-fold, .full-section, .back-page, .classifieds-section, .subscribe-block { margin-left: 48px !important; margin-right: 48px !important; padding-left: 0 !important; padding-right: 0 !important; }
  .masthead { padding-top: 26px !important; }
  .paper-name { font-size: 96px !important; }
  .headline { font-size: 60px !important; line-height: .98 !important; margin-top: 18px !important; }

  .front-page { padding: 0 48px !important; display: grid !important;
      grid-template-columns: 270px minmax(0, 1fr) 320px !important; gap: 40px !important; }
  .front-col, .front-col-center { width: auto !important; padding: 0 !important; border: 0 !important; }
  .lead-story { column-count: 2; column-gap: 32px; column-rule: 1px solid #cfc6b6; font-size: 17px; line-height: 1.6; }
  .lead-story p { margin-top: 0; }

  /* Honor Roll beside Detention. */
  #honor-roll, #honor-roll + .full-section { grid-column: auto; margin-top: 52px !important; }
  #honor-roll { margin-right: 24px !important; }
  #honor-roll + .full-section { margin-left: 24px !important; }
  .section-title-full { font-size: 30px !important; border-top: 4px solid #111 !important; border-bottom: 0 !important;
      padding: 8px 0 0 !important; margin: 0 !important; }
  #honor-roll + .full-section .section-title-full { color: var(--accent) !important; border-top-color: var(--accent) !important; }
  .section-note { text-align: left !important; margin: 2px 0 6px !important; }
  .hd-grid { display: flex !important; flex-direction: column !important; gap: 0 !important; overflow: visible !important; }
  .hd-card { display: grid !important; grid-template-columns: 26px 52px minmax(0, 1fr) 130px 72px;
      grid-template-areas: "rank photo name bar num" "rank photo team bar num" "rank photo sub bar num";
      column-gap: 14px; align-items: center !important; text-align: left !important;
      padding: 10px 0 !important; width: auto !important; min-width: 0 !important; }
  .hd-rank { position: static !important; grid-area: rank; width: auto !important; height: auto !important; }
  .hd-photo { grid-area: photo; width: 52px !important; height: 52px !important; }
  .hd-name { grid-area: name; font-size: 17px !important; text-align: left !important; }
  .hd-team { grid-area: team; text-align: left !important; }
  .hd-sub { grid-area: sub; text-align: left !important; margin: 0 !important; }
  .hd-bar { grid-area: bar; margin: 0 !important; }
  .hd-num { grid-area: num; font-size: 28px !important; text-align: right; margin: 0 !important; }

  /* Game stories keep the paper's own rhythm (lead, feature, a pair, briefs),
     set wider: the photo beside the text, the text in two columns. */
  #games .section-title-full, #awards .section-title-full, #rankings .section-title-full { font-size: 34px !important; }
  .stories-full { display: block !important; }
  .stories-full > .story-card { padding: 0 0 32px !important; margin: 30px 0 0 !important;
      border: 0 !important; border-bottom: 1px solid #cfc6b6 !important; }
  .stories-full > .story-lead { border-bottom: 1px solid #111 !important; }
  .story-lead:has(> .image-wrap:not([style*="display: none"])) { display: grid; grid-template-columns: minmax(0, 1fr) 400px; column-gap: 40px; }
  .story-lead:has(> .image-wrap:not([style*="display: none"])) > * { grid-column: 1; }
  .story-lead:has(> .image-wrap:not([style*="display: none"])) > .image-wrap { grid-column: 2; grid-row: 1 / span 8; align-self: start; width: auto !important;
      max-width: none !important; float: none !important; margin: 0 !important; }
  .story-lead .story-body, .story-brief .story-body, .feature-main .story-body { column-count: 2; column-gap: 32px; column-rule: 1px solid #cfc6b6; }
  .story-headline-lead { font-size: 54px !important; line-height: 1 !important; }
  .story-headline-feature { font-size: 40px !important; line-height: 1.02 !important; }
  .story-headline-small, .story-headline-brief { font-size: 30px !important; line-height: 1.04 !important; }
  .feature-main:has(> .image-wrap:not([style*="display: none"])) { display: grid !important; grid-template-columns: 360px minmax(0, 1fr); column-gap: 36px; align-items: start; }
  .feature-main > .image-wrap { grid-column: 1; width: auto !important; max-width: none !important; float: none !important; margin: 0 !important; }
  .paired-stories { display: grid !important; grid-template-columns: repeat(2, minmax(0, 1fr)) !important; column-gap: 48px !important; }
  .paired-col { min-width: 0; padding: 0 !important; border: 0 !important; }
  .paired-col + .paired-col { border-left: 1px solid #cfc6b6 !important; padding-left: 24px !important; margin-left: -24px; }
  .feature-main > .story-body { grid-column: 2; }
  .story-body-small { font-size: 16px !important; }

  /* This Week: rows sit at their own height, not stretched to the lead. */
  .front-page.fp-fit > .front-col:first-child { display: block !important; }
  .front-page .front-col:first-child .col-story { flex: none !important; }

  .fraud-callout { margin: 56px 48px 0 !important; }

  /* Awards in a row of four. */
  .awards-grid-full { display: grid !important; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)) !important; gap: 0 !important; }
  .award-card { padding: 0 20px !important; border-right: 1px solid #cfc6b6 !important; }
  .award-card:first-child { padding-left: 0 !important; }
  .award-card:last-child { border-right: 0 !important; padding-right: 0 !important; }
  .awards-grid-full > .award-card:last-child:nth-child(odd) { grid-column: auto !important; }
  .aw-figure { flex-direction: row !important; }
  #awards, #rankings { margin-top: 56px !important; }

  /* Power rankings: two columns of rows. */
  .rankings-grid { display: grid !important; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 48px; }
  .rankings-row { display: contents !important; }
  .ranking-card { display: grid !important; grid-template-columns: 50px 44px minmax(0, 1fr); grid-template-areas: "rank img name" "rank img comment";
      column-gap: 14px; align-items: center; text-align: left !important; padding: 12px 0 !important;
      border-bottom: 1px solid #cfc6b6 !important; margin: 0 !important; width: auto !important; }
  .ranking-card-rank { grid-area: rank; font-size: 38px !important; }
  .ranking-card > img { grid-area: img; width: 44px !important; height: 44px !important; margin: 0 !important; }
  .ranking-card-name { grid-area: name; font-size: 18px !important; text-align: left !important; }
  .ranking-card-comment { grid-area: comment; font-size: 15px !important; text-align: left !important; }

  .back-page { margin-top: 56px !important; border-top: 4px solid #111 !important; }
  .subscribe-block { margin-top: 56px !important; }
}
"""


# ---------------------------------------------------------------------------

THEMES: dict[str, dict] = {
    "tabloid": {
        "label": "Tabloid",
        "blurb": "Loud, red, all-caps. Built to shout from a newsstand.",
        "fonts": TABLOID_FONTS,
        "css": TABLOID_CSS,
    },
    "broadsheet": {
        "label": "Broadsheet",
        "blurb": "The paper of record. Restrained type, hairline rules, no colour.",
        "fonts": BROADSHEET_FONTS,
        "css": BROADSHEET_CSS,
    },
    "gameday": {
        "label": "Gameday",
        "blurb": "Broadcast graphics. Dark, condensed caps, enormous numbers.",
        "fonts": GAMEDAY_FONTS,
        "css": GAMEDAY_CSS,
    },
    "modern": {
        "label": "Modern",
        "blurb": "Tabloid capitals, clean rules and room to breathe on a big screen.",
        "fonts": MODERN_FONTS,
        "css": MODERN_CSS,
        # 17 Nov: needs more work. Hidden from the picker and refused by the
        # forms (resolve_choice); a league already set to it still renders.
        "hidden": True,
    },
}

DEFAULT_THEME = "tabloid"

#: Themes that want sentence-ish headlines rather than the writer's ALL CAPS.
#: CSS can only uppercase, never the reverse — it has no idea which letters
#: were capitals to begin with — so this happens in Python at render time.
TITLE_CASE_THEMES = frozenset({"broadsheet"})

#: Words a headline leaves lowercase unless they lead.
_MINOR_WORDS = frozenset({
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "onto", "or", "over", "the", "to", "up", "vs", "with",
})


def smart_title(text: str) -> str:
    """ALL CAPS -> Title Case, leaving anything already mixed-case alone.

    A headline that arrives as "MIKEVIDAN3 COMMITS WAR CRIMES" becomes
    "Mikevidan3 Commits War Crimes". Text that isn't shouting is returned
    untouched, so a deliberately-cased edit survives.
    """
    raw = (text or "").strip()
    if not raw:
        return raw
    letters = [c for c in raw if c.isalpha()]
    if not letters or not all(c.isupper() for c in letters):
        return raw  # not shouting; leave it as the author wrote it

    words = raw.split()
    out = []
    for i, word in enumerate(words):
        lowered = word.lower()
        core = lowered.strip(".,!?;:'\"")
        if i > 0 and core in _MINOR_WORDS and i != len(words) - 1:
            out.append(lowered)
        else:
            out.append(lowered[:1].upper() + lowered[1:])
    return " ".join(out)


def headline_for(theme: str | None, text: str) -> str:
    """The headline as this theme wants it set."""
    return smart_title(text) if resolve(theme) in TITLE_CASE_THEMES else text


def resolve(name: str | None) -> str:
    """A known theme key. Unknown values fall back rather than breaking a paper."""
    key = (name or "").strip().lower()
    return key if key in THEMES else DEFAULT_THEME


def resolve_choice(name: str | None) -> str:
    """resolve(), for a theme someone is picking: hidden themes are refused,
    so posting one straight at the form gets the default instead."""
    key = resolve(name)
    return DEFAULT_THEME if THEMES[key].get("hidden") else key


def fonts_for(name: str | None) -> str:
    return THEMES[resolve(name)]["fonts"]


def css_for(name: str | None) -> str:
    return THEMES[resolve(name)]["css"]


def choices() -> list[dict]:
    """For rendering a picker."""
    return [
        {"key": key, "label": theme["label"], "blurb": theme["blurb"]}
        for key, theme in THEMES.items()
        if not theme.get("hidden")
    ]
