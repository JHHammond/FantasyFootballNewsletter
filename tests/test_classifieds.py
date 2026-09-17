"""The classifieds page.

The publisher's own page — one sheet a week, identical in every league's
paper. Three separable things are tested here:

  * reading an image's real dimensions out of its header bytes;
  * packing ads into columns, which is done on the server for reasons the
    docstring on pack_columns explains at length;
  * rendering, escaping and the empty case.

Everything to do with how it actually paginates lives in test_layout.py,
because answering that needs a browser and a real PDF.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ads  # noqa: E402
from web import images  # noqa: E402


# ---------------------------------------------------------------------------
# How big is it?
# ---------------------------------------------------------------------------

def _image_bytes(fmt: str, size: tuple[int, int], **kwargs) -> bytes:
    Image = pytest.importorskip(
        "PIL.Image", reason="pillow not installed (dev-only dependency)")
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 40, 40)).save(buf, fmt, **kwargs)
    return buf.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF", "WEBP"])
@pytest.mark.parametrize("size", [(1234, 567), (40, 900), (7, 11)])
def test_dimensions_reads_every_format_the_uploader_accepts(fmt, size):
    """Checked against a real encoder rather than against hand-built bytes.

    A header parser tested on headers the same test wrote is a test of one
    opinion about the format, held twice.
    """
    assert images.dimensions(_image_bytes(fmt, size)) == size


def test_dimensions_walks_past_an_exif_block():
    """The JPEG dimensions are not at a fixed offset.

    A JPEG is a chain of length-prefixed segments and the frame header can be
    anywhere — commonly after an EXIF block carrying an entire thumbnail
    image. Reading at a fixed offset works on the files a test generates and
    fails on the files a phone generates.
    """
    thumbnail = _image_bytes("JPEG", (160, 40))
    data = _image_bytes("JPEG", (1600, 400), exif=b"Exif\x00\x00" + thumbnail)
    assert images.dimensions(data) == (1600, 400)


def test_dimensions_reads_lossless_webp():
    """"WebP" is three different formats behind one four-letter name."""
    assert images.dimensions(
        _image_bytes("WEBP", (321, 123), lossless=True)) == (321, 123)


def test_a_truncated_upload_reports_no_size_rather_than_a_wrong_one():
    """None is a real answer and the page handles it.

    A guessed aspect ratio is worse than no aspect ratio: the page would
    reserve the wrong shape and silently letterbox the ad.
    """
    assert images.dimensions(_image_bytes("JPEG", (500, 500))[:40]) is None
    assert images.dimensions(b"this is not an image at all") is None


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------

def _ad(width, height, url="/x.png"):
    return {"image_url": url, "width": width, "height": height}


#: The shapes from the first real page: a banner, two portraits, a landscape
#: and a square. Deliberately not five of the same thing — every packing bug
#: found so far only showed up on mixed shapes.
MIXED = [
    _ad(1600, 360, "/banner.png"),   # 0 — banner
    _ad(720, 960, "/tall.png"),      # 1
    _ad(1200, 700, "/wide.png"),     # 2
    _ad(900, 900, "/square.png"),    # 3
    _ad(640, 880, "/tall2.png"),     # 4
]


def test_a_wide_ad_runs_as_a_banner_and_the_rest_go_in_columns():
    blocks = ads.pack_columns(MIXED)
    kinds = [b[0] for b in blocks]
    assert kinds == ["banner", "columns"], kinds
    assert blocks[0][2] == 0, "the 1600x360 image should be the banner"

    placed = sorted(i for column in blocks[1][1] for _, i in column)
    assert placed == [1, 2, 3, 4], "every non-banner ad has to land somewhere"


def test_a_banner_interrupts_the_columns_rather_than_sitting_in_one():
    """Order survives. An ad dropped between two others stays between them."""
    blocks = ads.pack_columns([_ad(900, 900), _ad(1600, 360), _ad(900, 900)])
    assert [b[0] for b in blocks] == ["columns", "banner", "columns"]


def test_the_columns_come_out_balanced():
    """This is the bug the packing exists to prevent.

    Assigning in document order put both portraits in one column and left the
    other 40% shorter — a page with a hole down one side. Tallest-first fixes
    it. The assertion is on the ratio of predicted heights, which is what the
    reader actually sees as balance.
    """
    blocks = ads.pack_columns(MIXED)
    columns = blocks[1][1]

    heights = []
    for column in columns:
        heights.append(sum(1.0 / ads._ad_shape(ad)[2] for ad, _ in column))

    assert min(heights) > 0
    assert max(heights) / min(heights) < 1.35, (
        f"columns are lopsided: predicted heights {heights}")


def test_document_order_is_restored_inside_each_column():
    """Packed tallest-first, read top to bottom.

    The two are different orders and both matter: the balance comes from the
    first, and "drag an ad up the list, it moves up the page" comes from the
    second.
    """
    for column in ads.pack_columns(MIXED)[1][1]:
        indexes = [i for _, i in column]
        assert indexes == sorted(indexes), (
            f"column is in packing order, not reading order: {indexes}")


def test_an_ad_with_no_known_size_still_gets_placed():
    """A dimensionless ad is laid out as 4:3 rather than dropped."""
    blocks = ads.pack_columns([_ad(None, None), _ad(900, 900)])
    placed = sorted(i for column in blocks[0][1] for _, i in column)
    assert placed == [0, 1]
    assert ads._ad_shape({"width": 0, "height": 0})[2] == pytest.approx(4 / 3)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def test_a_week_with_no_ads_renders_nothing_at_all():
    """Not an empty heading. Not house ads stretched over a page.

    The small per-league block tops itself up with filler because a half-empty
    grid mid-paper reads as a rendering fault. A whole page cannot be padded
    that way, so on a week nobody uploaded anything the page is simply absent.
    """
    assert ads.render_publisher_page([]) == ""
    assert ads.render_publisher_page(None) == ""
    assert ads.render_publisher_page([{}, {"caption": "no image"}]) == ""


def test_a_javascript_url_never_reaches_the_page():
    """Only one person can set these, which is not a reason to trust them —
    the entire point of a `javascript:` URL is that it does not look like one
    in a text box."""
    for hostile in ("javascript:alert(1)", "data:text/html,<script>",
                    "vbscript:x", "//evil.example.com/x.png"):
        assert ads.render_publisher_page([_ad(900, 900, hostile)]) == "", hostile


def test_the_real_dimensions_are_written_into_the_markup():
    """Not decoration. Without them the page does not know how tall an image
    will be until it arrives, so the layout jumps as each one loads — and a
    print render can paginate against a half-measured page."""
    html = ads.render_publisher_page([_ad(1200, 700)])
    assert 'width="1200"' in html and 'height="700"' in html
    # And as a number CSS can do arithmetic on, which is how the print rules
    # cap an ad's height without squashing it.
    assert "--ad-aspect:1.7143" in html


def test_a_link_cannot_hand_the_destination_our_window():
    html = ads.render_publisher_page(
        [dict(_ad(900, 900), link_url="https://example.com")])
    assert 'rel="noopener noreferrer nofollow"' in html


def test_a_caption_is_escaped_and_becomes_the_alt_text():
    html = ads.render_publisher_page(
        [dict(_ad(900, 900), caption='Chase <b>"the kicker"</b>')])
    assert "<b>" not in html
    assert "&lt;b&gt;" in html
    assert "&quot;" in html


def test_an_ad_with_no_caption_still_says_what_it_is():
    """An empty alt on five images running is a screen reader announcing
    "image" five times, which is no better than silence."""
    html = ads.render_publisher_page([_ad(900, 900)])
    assert 'alt="Classified advertisement 1"' in html


# ---------------------------------------------------------------------------
# The stylesheet
# ---------------------------------------------------------------------------

def test_the_phone_breakpoint_cannot_fire_on_paper():
    """The one that cost two sheets.

    A printed Letter page is about 726px wide, so a bare `max-width: 760px`
    matches on PAPER as well as on a phone. With it bare, the two columns
    collapsed into one stacked column in print and the five-ad page ran over
    onto a second sheet that was two-thirds empty. `screen and` is the fix and
    it is invisible on screen, which is exactly why it needs a test.

    Any breakpoint above roughly 700px in this codebase is a print rule
    whether its author meant it to be or not.
    """
    import re

    css = ads.PUBLISHER_PAGE_CSS
    queries = re.findall(r"@media([^{]+)\{", css)
    assert queries, "the phone rule has gone"

    for query in queries:
        match = re.search(r"max-width:\s*(\d+)px", query)
        if match and int(match.group(1)) > 700:
            assert "screen" in query, (
                f"`@media{query.strip()}` fires on a printed page too, which "
                f"collapses the classifieds page into one column")
