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
# a blackletter masthead, and body text that expects to be read rather than
# glanced at. The same brutal prose lands differently when it's set like this.
# ---------------------------------------------------------------------------

BROADSHEET_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com" />'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=UnifrakturMaguntia'
    '&family=Libre+Baskerville:ital,wght@0,400;0,700;1,400'
    '&family=Libre+Franklin:wght@400;600;700'
    '&display=swap" rel="stylesheet" />'
)

BROADSHEET_CSS = """
/* ===== BROADSHEET ===== */
body { background: #f7f5f0; }
.page { background: #fffefb; }

.masthead { border-bottom: 1px solid #111; padding-bottom: 10px; }
.paper-name {
    font-family: "UnifrakturMaguntia", "Playfair Display", serif !important;
    font-weight: 400 !important;
    letter-spacing: 0 !important;
    color: #111 !important;
    text-transform: none !important;
}
.edition-line {
    font-family: "Libre Franklin", sans-serif !important;
    letter-spacing: 1.6px !important;
    text-transform: uppercase;
    color: #5a5a5a !important;
}

.headline {
    font-family: "Libre Baskerville", Georgia, serif !important;
    font-weight: 700 !important;
    letter-spacing: -0.4px !important;
    line-height: 1.14 !important;
    text-transform: none !important;
    color: #111 !important;
}

.dateline-bar {
    border-top: 1px solid #111 !important;
    border-bottom: 1px solid #111 !important;
    background: transparent !important;
    font-family: "Libre Franklin", sans-serif !important;
    color: #444 !important;
}

.lead-story, .story-body, .col-story-body, .award-body, .fraud-callout-body {
    font-family: "Libre Baskerville", Georgia, serif !important;
    line-height: 1.72 !important;
}
.lead-story { font-size: 15px !important; }

.story-headline, .col-story-headline {
    font-family: "Libre Baskerville", Georgia, serif !important;
    font-weight: 700 !important;
    text-transform: none !important;
    letter-spacing: -0.2px !important;
    color: #111 !important;
}
.story-label, .col-section-label, .section-title, .section-title-full {
    font-family: "Libre Franklin", sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 2.4px !important;
    color: #6a6a6a !important;
    background: transparent !important;
    border-bottom: 1px solid #111 !important;
}

/* The tabloid's red is doing the shouting. A broadsheet doesn't shout. */
.section-title-full, .story-label { color: #6a6a6a !important; }
.ranking-card-rank { color: #111 !important; }

.fraud-callout {
    background: #f2efe8 !important;
    border: 1px solid #111 !important;
    color: #111 !important;
}
.fraud-callout-label {
    font-family: "Libre Franklin", sans-serif !important;
    color: #8a1c1c !important;
    letter-spacing: 2.4px !important;
}
.fraud-callout-body { color: #222 !important; }

.award-card, .ranking-card, .story-card {
    border: 1px solid #d6d2c8 !important;
    background: #fffefb !important;
}
.award-title, .ranking-card-name {
    font-family: "Libre Franklin", sans-serif !important;
    letter-spacing: 1.4px !important;
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

.week-ticker { background: #f2efe8 !important; color: #111 !important;
               border-top: 1px solid #111; border-bottom: 1px solid #111; }
.week-ticker * { color: #111 !important; }

.image-wrap img, .hero-image-wrap img { border: 1px solid #c9c4b8 !important; }
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
}

DEFAULT_THEME = "tabloid"


def resolve(name: str | None) -> str:
    """A known theme key. Unknown values fall back rather than breaking a paper."""
    key = (name or "").strip().lower()
    return key if key in THEMES else DEFAULT_THEME


def fonts_for(name: str | None) -> str:
    return THEMES[resolve(name)]["fonts"]


def css_for(name: str | None) -> str:
    return THEMES[resolve(name)]["css"]


def choices() -> list[dict]:
    """For rendering a picker."""
    return [
        {"key": key, "label": theme["label"], "blurb": theme["blurb"]}
        for key, theme in THEMES.items()
    ]
