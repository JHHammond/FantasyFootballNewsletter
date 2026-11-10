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


def test_a_paper_is_cached_for_a_minute_not_an_hour():
    # Edits, the no-ads switch and this layout all change a served paper.
    import inspect
    from web import app as webapp
    src = inspect.getsource(webapp.read_paper)
    assert '"public, max-age=60"' in src and "s-maxage" not in src


def test_both_stories_of_a_pair_get_a_label():
    js = compact.COMPACT_JS
    assert 'querySelectorAll(".story-card .story-headline")' in js
    assert 'h.closest(".paired-col")' in js


def test_standings_and_week_in_numbers_are_restyled():
    css, js = compact.COMPACT_CSS, compact.COMPACT_JS
    assert ".cx-st-rank" in css and "cx-st-src" in js
    num = css[css.index(".front-col .wk-board .wk-num"):]
    assert "var(--accent, #c40000)" in num[:num.index("}")]      # every number red
    st = js[js.index("Standings (9 Nov)"):js.index("Medal colours")]
    assert "innerHTML" not in st                                  # team names stay text


def test_a_paragraph_starting_with_a_number_sign_is_not_a_heading():
    import newspaper
    out = newspaper.md("Para one.\n\n#1 Pick Jeremiah Smith is still 2-7.")
    assert "#1 Pick Jeremiah Smith" in out and "<h1" not in out


def test_a_sleeper_team_photo_beats_the_account_picture():
    from providers.sleeper import _team_avatar, AVATAR_URL
    photo = "https://sleepercdn.com/uploads/abc.jpg"
    assert _team_avatar({"avatar": "robot", "metadata": {"avatar": photo}}) == photo
    assert _team_avatar({"avatar": "robot", "metadata": {}}) == f"{AVATAR_URL}/robot"
    assert _team_avatar({"avatar": None, "metadata": {"avatar": "javascript:x"}}) is None


def test_fraud_watch_and_power_rankings_are_restyled():
    css, js = compact.COMPACT_CSS, compact.COMPACT_JS
    assert ".fraud-callout .fc-stamp" in css and "transform: none" in css
    assert ".ranking-card-rank.cx-rank-top" in css and "cx-rank-top" in js
    assert "power rankings" in js
