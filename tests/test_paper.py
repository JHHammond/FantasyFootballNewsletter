"""How the written content becomes a printed page.

Two things in here went out in the first real paper and were wrong in ways
nobody would have caught from the code:

  - the pull quote, the single line blown up in display type beside the lead
    story, was produced by splitting the body on "." and taking fragment two.
    In a paper where nearly every sentence contains a decimal score, that
    printed half-sentences.
  - the classifieds were four fixed strings, identical in every paper of every
    league, forever.
"""

from __future__ import annotations

import re

import ads
import newspaper


# ---------------------------------------------------------------------------
# Pull quote
# ---------------------------------------------------------------------------

LEAD_BODY = (
    "<p>Mikevidan3 wins 126.66 to 100.16, and the margin flatters Hank more "
    "than he deserves. Justin Jefferson put up 31.2 against a 17-point "
    "projection. Drake London and Kyle Pitts combined for 5.5 points.</p>"
)


def test_the_pull_quote_is_never_cut_at_a_decimal_point():
    """The exact Week 1 output was:

        "Justin Jefferson (best receiver in football) put up 31..."

    A sentence severed mid-number, printed as the line the reader's eye lands
    on first. Splitting on "." is indefensible in a paper about scores.
    """
    quote = newspaper.build_pull_quote(LEAD_BODY)

    assert quote.endswith((".", "!", "?")), quote
    assert "..." not in quote
    assert "31.2" in quote, "the decimal was cut out of the number"


def test_the_writers_own_quote_wins_over_the_fallback():
    chosen = "Kyler Murray put up 0.7 points and the week was over by noon."
    assert newspaper.build_pull_quote(LEAD_BODY, chosen) == chosen


def test_a_useless_chosen_quote_falls_back_rather_than_printing_it():
    """Generation can return "" or a stray word. Printing that in 28pt italic
    is worse than printing the fallback."""
    for junk in ("", "  ", "ok", None, "x" * 400):
        quote = newspaper.build_pull_quote(LEAD_BODY, junk)
        assert "Jefferson" in quote or "Drake" in quote, junk


def test_quote_marks_are_stripped_so_they_are_not_doubled():
    """The template wraps the quote in &ldquo;/&rdquo; itself."""
    quote = newspaper.build_pull_quote(LEAD_BODY, '"Allen threw for four scores and it still went down to the kicker."')
    assert not quote.startswith('"')


def test_html_in_the_body_never_reaches_the_quote():
    body = '<p>A sentence. <em>Jefferson</em> put up 31.2 on eleven targets that day.</p>'
    assert "<" not in newspaper.build_pull_quote(body)


def test_nothing_quotable_produces_no_quote_rather_than_a_stub():
    assert newspaper.build_pull_quote("<p>Short. Tiny. No.</p>") == ""
    assert newspaper.build_pull_quote("") == ""
    assert newspaper.build_pull_quote(None) == ""


# ---------------------------------------------------------------------------
# Classifieds
# ---------------------------------------------------------------------------

#: Three, because the grid holds four and the product ad takes the last slot.
#: That is the intended steady state: no generic filler at all.
WRITTEN = [
    {"heading": "WANTED: A QB WHO SHOWS UP", "contact": "Ask for John",
     "body": "Kyler Murray, 0.7 points against an 18 projection."},
    {"heading": "FOR SALE: CHUBA HUBBARD", "contact": "No offer refused",
     "body": "23.7 points, all of them scored on the bench."},
    {"heading": "LOST: ONE TIGHT END", "contact": "Reward offered",
     "body": "Colston Loveland, last seen scoring zero in a 59-point game."},
]


def test_written_classifieds_replace_the_generic_filler():
    html = ads.render_classifieds(ads.ads_from_content(WRITTEN))

    assert "WANTED: A QB WHO SHOWS UP" in html
    assert "WANTED: ONE COMPETENT MANAGER" not in html, (
        "the generic house ad crowded out a real one")
    assert "LOST: ONE SEASON'S DIGNITY" not in html


def test_a_short_week_is_topped_up_rather_than_left_ragged():
    """If the writer only manages two, the grid still fills — a half-empty
    classifieds block reads as a rendering fault, not as a light week."""
    html = ads.render_classifieds(ads.ads_from_content(WRITTEN[:2]))
    assert html.count('class="classified"') == 4
    assert "WANTED: A QB WHO SHOWS UP" in html


def test_the_product_ad_survives_the_fill():
    """It is the only ad actually selling anything, and it sits at the foot of
    two thousand words somebody enjoyed. It does not get bumped."""
    html = ads.render_classifieds(ads.ads_from_content(WRITTEN))
    assert "YOUR LEAGUE, YOUR PAPER" in html




def test_generation_failing_entirely_still_prints_a_block():
    html = ads.render_classifieds(ads.ads_from_content([]))
    assert html.count('class="classified"') == 4
    assert "YOUR LEAGUE, YOUR PAPER" in html


def test_malformed_entries_are_dropped_not_rendered():
    made = ads.ads_from_content([
        {"heading": "Fine", "body": "Also fine."},
        {"heading": "", "body": "no heading"},
        {"body": "no heading key at all"},
        "not a dict",
        None,
    ])
    assert len(made) == 1


def test_classified_text_is_escaped():
    """Written by a model, rendered into the page. Treat it as untrusted."""
    html = ads.render_classifieds(ads.ads_from_content([
        {"heading": "<script>alert(1)</script>", "body": "a & b", "contact": "x"},
    ]))
    assert "<script>" not in html
    assert "&amp;" in html


# ---------------------------------------------------------------------------
# Editability — and the line the published copy must never cross
# ---------------------------------------------------------------------------

def test_written_classifieds_are_editable():
    html = ads.render_classifieds(ads.ads_from_content(WRITTEN), editable=True)
    keys = set(re.findall(r'data-edit-key="([^"]+)"', html))

    assert "classified_heading_0" in keys
    assert "classified_body_0" in keys
    assert "classified_contact_0" in keys


def test_house_ads_are_not_editable():
    """Editing the product ad would be silently thrown away on the next
    generation, which is a worse experience than not offering it."""
    html = ads.render_classifieds(ads.ads_from_content(WRITTEN), editable=True)
    product = html[html.index("YOUR LEAGUE"):]
    assert "contenteditable" not in product.split('class="classified"')[0]


def test_the_published_copy_carries_no_edit_hooks_at_all():
    """The uploaded paper is world-readable. contenteditable in it would be a
    disclosure that the page has an editor, on a page anyone can open."""
    html = ads.render_classifieds(ads.ads_from_content(WRITTEN), editable=False)
    assert "contenteditable" not in html
    assert "data-edit-key" not in html


# ---------------------------------------------------------------------------
# The one-line notes under Honor Roll and Detention
# ---------------------------------------------------------------------------

def test_the_section_notes_describe_what_the_sections_actually_do():
    """Somebody opening their first paper has no idea what "Detention" is.

    The trap here is writing the caption you assume is true. The obvious pair
    — "beat their projection" / "missed their projection" — is only half
    right: Detention is sorted by projection miss, but Honor Roll is sorted by
    RAW SCORE, so a quarterback with a modest week outranks a running back who
    tripled his projection. A caption that describes the wrong sort order is
    worse than no caption, because the reader then mistrusts the numbers.
    """
    import inspect

    source = inspect.getsource(newspaper)

    # Honor Roll: top N by actual score.
    assert 'key=lambda p: p["actual"], reverse=True' in source, (
        "Honor Roll's sort changed — the note under it needs to change too")
    assert "highest scorers" in source

    # Detention: the biggest projection misses.
    assert 'key=lambda p: p["beat_by"]' in source
    assert "missed their projection" in source


def test_both_notes_are_short_enough_to_read_in_passing():
    """A paragraph under a heading is a different product from a caption.
    These are meant to be absorbed without stopping."""
    import re

    rendered = re.findall(r'<div class="section-note">(.*?)</div>',
                          inspect_source())
    assert rendered, "the section notes have gone"
    for note in rendered:
        words = re.sub(r"&[a-z]+;", "'", note).split()
        assert len(words) <= 10, f"{len(words)} words: {note}"


def inspect_source():
    import inspect
    return inspect.getsource(newspaper)
