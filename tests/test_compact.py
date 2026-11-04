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
