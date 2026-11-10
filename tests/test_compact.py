"""The compact phone layout, served on request (31 Oct)."""

from web import compact

PAPER = ("<html><head><title>x</title></head><body>"
         '<div class="lead-story"><p>Lead.</p></div>'
         '<article class="story-card"><h3 class="story-headline">A HEADLINE</h3>'
         '<div class="story-body"><p>One.</p><p>Two.</p></div></article>'
         "</body></html>")


def test_compact_adds_its_styles_and_script_once():
    out = compact.apply(PAPER)
    assert out.count('id="compact-layout"') == 1
    assert out.count('id="compact-layout-js"') == 1
    assert out.index('id="compact-layout"') < out.index("</head>")
    assert compact.apply(out) == out                       # idempotent


def test_compact_only_touches_phone_screens():
    """Desktop, print and the PDF must look exactly as they did."""
    css = compact.COMPACT_CSS
    body = css.split("<style id=\"compact-layout\">", 1)[1].rsplit("</style>", 1)[0].strip()
    assert body.startswith("@media screen and (max-width: 760px) {")
    assert body.rstrip().endswith("}")
    assert "@media print" not in css
    assert 'matchMedia("screen and (max-width: 760px)")' in compact.COMPACT_JS


def test_the_paper_is_unchanged_without_the_flag():
    assert "compact-layout" not in PAPER


def test_the_front_headline_gets_a_label_and_a_rule():
    from web import compact
    out = compact.apply("<html><head></head><body><div class=\"above-fold\"><h2 class=\"headline\">X</h2></div></body></html>")
    assert "cx-kicker" in out and "cx-headrule" in out and "Cover story" in out
    assert "text-wrap: balance" in out


def test_the_lead_story_is_never_folded():
    assert "Read the full story" not in compact.COMPACT_JS
    assert 'querySelectorAll(".story-card .story-body")' in compact.COMPACT_JS


def test_photos_keep_their_own_shape_on_a_phone():
    css = compact.COMPACT_CSS
    assert "16 / 10" not in css and "4 / 3" not in css
    assert "aspect-ratio: auto !important" in css and "object-fit: contain !important" in css


def test_a_folded_story_still_wraps_around_its_photo():
    # overflow: hidden makes a new formatting context, which refuses to flow
    # around a float; clip must come after it so it wins where supported.
    css = compact.COMPACT_CSS
    fold = css[css.index(".cx-fold {"):]
    fold = fold[:fold.index("}")]
    assert fold.index("overflow: hidden") < fold.index("overflow: clip")


def test_game_story_headlines_are_tabloid_capitals_with_a_label():
    css, js = compact.COMPACT_CSS, compact.COMPACT_JS
    assert '"Anton"' in css and "text-transform: uppercase !important" in css
    assert ".cx-tag" in css and "cx-tag" in js
    assert "Upset" in js and "Nail-biter" in js and "Blowout" in js
    assert "family=Anton" in js and "family=Anton" not in css   # loaded on phones only


def test_position_abbreviations_stay_capitalised():
    assert "qb|rb|wr|te" in compact.COMPACT_JS


def test_this_week_is_rebuilt_as_tabloid_teasers():
    css, js = compact.COMPACT_CSS, compact.COMPACT_JS
    assert ".cx-tw-teaser" in css and "font-size: 19px" in css
    assert ".cx-hot .cx-tw-bar" not in css and "cx-week-head" in css   # one stripe colour
    # Rows take their story's label, and names are set as text, never markup.
    assert 'classList.contains("cx-tag")' in js
    tw = js[js.index("This Week (9 Nov)"):js.index("Medal colours")]
    assert "innerHTML" not in tw
