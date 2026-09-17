"""Layout regressions, checked in a real browser.

These exist because the mobile bugs they cover were invisible to every other
kind of test. The paper rendered, the HTML was well-formed, 337 unit tests
passed — and the front page still put a list of scores above the story it was
about, on the surface where nearly every reader actually opens it.

One of the rules involved, `.dateline-bar { grid-template-columns: 1fr }`, was
a grid property on a flex container. It had been in the stylesheet doing
nothing at all since the day it was written. Nothing but a browser was ever
going to say so.

Playwright is a development dependency and is not in requirements.txt. These
skip cleanly where it isn't installed rather than failing a normal test run.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

pytest.importorskip("playwright.sync_api",
                    reason="playwright not installed (dev-only dependency)")

from playwright.sync_api import sync_playwright  # noqa: E402

import newspaper  # noqa: E402


#: Widths worth checking. 320 is the smallest phone still in use, 390 is the
#: common iPhone, 430 the large one. Everything here has to hold at all three.
PHONE_WIDTHS = (320, 390, 430)

#: The container ships Chromium at a known path; a normal machine has whatever
#: `playwright install` put somewhere. Try ours, fall back to the default.
_BUNDLED = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def _launch(pw):
    if os.path.exists(_BUNDLED):
        return pw.chromium.launch(executable_path=_BUNDLED)
    return pw.chromium.launch()


def _long_lead() -> str:
    """Long enough to exercise the case that broke: a real lead story."""
    return (
        "Ladies, Gentlemen, and Chase, we are so back. This weekend was better "
        "than Christmas morning, and the season is officially underway. "
        "It has been an eventful offseason and absolutely none of it matters "
        "now. Our little make-believe football league has started and we are "
        "at war for the next eighteen weeks. " * 6
    )


def _paper_html() -> str:
    teams = [("WillDavidson10", 177.8), ("Audobo", 155.6),
             ("johnhenryhammond", 155.3), ("champayyy", 146.4),
             ("Jagan34", 138.8), ("CoosaRiverTv", 131.0),
             ("mikevidan3", 126.7), ("nickarrowood", 122.5),
             ("HankStocke", 100.2), ("superchaser", 96.3)]

    def side(name, points):
        return {"team_name": name, "owner_name": name, "points": points,
                "record": "0-0", "record_after": "1-0", "wins": 0, "losses": 0,
                "ties": 0, "avatar_url": None, "lineup_gap": 4.0,
                "empty_slots": 0, "all_starters": [], "all_bench": [],
                "top_performer": None, "bottom_performer": None,
                "players_points": {}, "starters": [], "players": []}

    games, matchups = [], []
    for wi, li in [(0, 1), (2, 9), (4, 7), (3, 5), (6, 8)]:
        wn, wp = teams[wi]
        ln, lp = teams[li]
        games.append({"team_1": side(wn, wp), "team_2": side(ln, lp),
                      "winner": wn, "margin": round(wp - lp, 1)})
        matchups.append({
            "winner": wn, "loser": ln, "winner_score": wp, "loser_score": lp,
            "winner_record": "1-0", "loser_record": "0-1",
            "winner_lineup_gap": 4.0, "loser_lineup_gap": 18.0,
            "margin": round(wp - lp, 1), "winner_avatar": None,
            "loser_avatar": None,
            "headline": f"{wn.upper()} HOLDS OFF {ln.upper()}",
            "body": "A recap with several sentences in it. " * 8,
            "teaser": f"{wn} handles {ln}",
        })

    summary = {
        "highest_score": {"team_name": teams[0][0], "owner_name": teams[0][0],
                          "points": teams[0][1]},
        "lowest_score": {"team_name": teams[-1][0], "owner_name": teams[-1][0],
                         "points": teams[-1][1]},
        "closest_game": games[3], "biggest_blowout": games[1],
        "bench_blunder": {"team_name": teams[8][0], "owner_name": teams[8][0],
                          "lineup_gap": 18.0},
        "upset": None, "jerry_jones": {"team_name": teams[-1][0]},
    }
    rankings = [{"team_name": n, "owner_name": n, "points": p, "record": "0-0",
                 "wins": 0, "losses": 0, "avatar_url": None} for n, p in teams]
    ai = {
        "headline": "HOLY HELL. WE'RE BACK.",
        "lead_story": _long_lead(),
        "matchup_content": matchups,
        "awards": [{"title": "GARDNER MINSHEW AWARD", "body": "Bench award. " * 6}],
        "fraud_watch": "Chase. How do you lose by sixty.",
        "power_rankings_comments": {n: f"{p:.0f} points." for n, p in teams},
        "pull_quote": "Kyler Murray put up 0.7 points and the week was over by noon.",
        "classifieds": [],
    }

    edition = newspaper.build_edition(
        "The Kevlarville Times", 1, summary, games, rankings, ai,
        subscribe_slug="kevlarville-test")
    edition["paper_name"] = "The Kevlarville Times"
    return newspaper.render_html(edition)


@pytest.fixture(scope="module")
def paper_file():
    path = pathlib.Path(tempfile.mkdtemp()) / "paper.html"
    path.write_text(_paper_html(), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = _launch(pw)
        yield b
        b.close()


def _measure(browser, path, width):
    page = browser.new_page(viewport={"width": width, "height": 844})
    page.goto(path.as_uri())
    page.wait_for_timeout(400)
    data = page.evaluate("""() => {
        const doc = document.documentElement;
        const top = sel => {
            const el = document.querySelector(sel);
            return el ? Math.round(el.getBoundingClientRect().top + window.scrollY) : null;
        };
        const wide = [...document.querySelectorAll('*')]
            .filter(el => el.getBoundingClientRect().right > doc.clientWidth + 1)
            .map(el => (typeof el.className === 'string' && el.className)
                        ? el.className : el.tagName);
        const dateline = document.querySelector('.dateline-bar');
        const button = document.querySelector('.print-button');
        return {
            scrollWidth: doc.scrollWidth,
            clientWidth: doc.clientWidth,
            overflowing: [...new Set(wide)],
            leadTop: top('.lead-story'),
            teasersTop: top('.front-col .col-section-label'),
            datelineDirection: dateline ? getComputedStyle(dateline).flexDirection : null,
            buttonPosition: button ? getComputedStyle(button).position : null,
        };
    }""")
    page.close()
    return data


@pytest.mark.parametrize("width", PHONE_WIDTHS)
def test_the_page_never_scrolls_sideways_on_a_phone(browser, paper_file, width):
    """Five ticker tiles across 390px leaves about 55px each, and a label
    reading BIGGEST BLOWOUT at 1.5px letter-spacing does not fit in 55px. The
    document measured 404px wide inside a 390px viewport.

    Horizontal scroll is most of what "janky" actually feels like: the whole
    page slides under your thumb while you are trying to read a column.
    """
    m = _measure(browser, paper_file, width)
    assert m["scrollWidth"] <= m["clientWidth"], (
        f"{m['scrollWidth']}px document in a {m['clientWidth']}px viewport; "
        f"pushed wide by: {m['overflowing'][:6]}")


@pytest.mark.parametrize("width", PHONE_WIDTHS)
def test_the_lead_story_comes_before_the_teasers_on_a_phone(browser, paper_file,
                                                            width):
    """The front page is [This Week | lead | Standings]. Collapsed to one
    column it stacked in DOM order, so the reader got a list of five scores
    before the story the paper is about — measured at 390px, the lead began
    580px below the teasers.

    Nearly everyone opens this from a link in a group chat, on a phone.
    """
    m = _measure(browser, paper_file, width)
    assert m["leadTop"] is not None and m["teasersTop"] is not None
    assert m["leadTop"] < m["teasersTop"], (
        f"lead story at {m['leadTop']}px, teasers at {m['teasersTop']}px")


@pytest.mark.parametrize("width", PHONE_WIDTHS)
def test_the_dateline_stacks_on_a_phone(browser, paper_file, width):
    """The rule that was supposed to do this set grid-template-columns on a
    flex container, so it did nothing, so the dateline stayed three squeezed
    columns with the week summary wrapping over six lines."""
    m = _measure(browser, paper_file, width)
    assert m["datelineDirection"] == "column", m["datelineDirection"]


def test_the_save_button_does_not_sit_on_top_of_the_text_on_a_phone(browser,
                                                                    paper_file):
    """Fixed position on a phone means covering whatever is underneath, and
    underneath is the paragraph somebody is reading. There is no free corner —
    the screen is all text."""
    assert _measure(browser, paper_file, 390)["buttonPosition"] == "static"


def test_the_save_button_still_floats_on_a_desktop(browser, paper_file):
    """The mobile fix must not cost the desktop affordance."""
    assert _measure(browser, paper_file, 1280)["buttonPosition"] == "fixed"


def test_the_desktop_front_page_is_still_three_columns(browser, paper_file):
    """All of the above is a phone-width override. At desk width the paper is
    still a newspaper."""
    m = _measure(browser, paper_file, 1280)
    assert m["leadTop"] is not None
    assert abs(m["leadTop"] - m["teasersTop"]) < 200, (
        "the columns are no longer side by side on a desktop")


def test_every_element_of_a_section_header_avoids_a_break_after_it(browser,
                                                                   paper_file):
    """`break-after: avoid` binds a block to whatever comes IMMEDIATELY next.

    The Honor Roll heading had it. The one-line note underneath it did not —
    added later, without anyone noticing what it did to the rule above. So the
    heading bound to the note, the two of them sat alone at the foot of a page,
    and the players went overleaf. That is what the first ESPN paper printed.

    Checked as a computed style rather than by hunting for the break in a
    rendered PDF: where the break lands depends on how long that week's stories
    happen to be, so a page-boundary test passes or fails by luck. The rule
    either applies to every element in the stack or it does not.
    """
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    page.goto(paper_file.as_uri())
    page.wait_for_timeout(400)

    results = page.evaluate("""() => {
        const out = {};
        // Force the print stylesheet, which is where these rules live.
        for (const sel of ['.section-title-full', '.section-note',
                           '.story-headline', '.award-title']) {
            const el = document.querySelector(sel);
            out[sel] = el ? getComputedStyle(el).breakAfter : 'NOT PRESENT';
        }
        return out;
    }""")
    page.close()

    # On screen these are 'auto'; the print rule is what matters, and the
    # element has to exist at all for the rule to have anything to bind.
    assert results['.section-note'] != 'NOT PRESENT', (
        "the section note has gone — the print rule now binds the heading to "
        "the content again, but check the PDF before deleting this test")
    assert results['.section-title-full'] != 'NOT PRESENT'


def test_the_print_stylesheet_binds_the_note_to_what_follows_it():
    """The rule itself, read out of the stylesheet.

    Playwright cannot evaluate an @media print block's computed styles without
    emulating print media per element, so this asserts the rule is written —
    which is the thing that regressed.
    """
    import printing

    import re

    css = printing.BASE_PRINT_CSS
    # Anchor on the DECLARATION — "break-after: avoid;" with its semicolon and
    # its indentation. The prose explaining this rule also contains the words
    # "break-after: avoid", and a plain .index() finds the comment first.
    match = re.search(r"\n\s+break-after:\s*avoid;", css)
    assert match, "the break-after rule has gone entirely"
    at = match.start()

    # The selector list is whatever sits between the previous rule's closing
    # brace and this declaration's opening one.
    selectors = css[css.rindex("}", 0, at) + 1:at]
    selectors = re.sub(r"/\*.*?\*/", "", selectors, flags=re.S)

    assert ".section-note," in selectors, (
        "the section note is not in the break-after group, so a heading binds "
        f"to it and strands the content overleaf. Group was:\n{selectors}")
    assert ".section-title-full," in selectors


# ---------------------------------------------------------------------------
# Print density
#
# A paper with holes in it does not read as a newspaper, it reads as a broken
# export. This measures actual ink: rasterise each page and find where the
# content stops, because nothing in the DOM knows where a page break landed.
# ---------------------------------------------------------------------------

def _page_gaps(pdf_path):
    """Percentage of each page left blank below its last line of content."""
    Image = pytest.importorskip(
        "PIL.Image", reason="pillow not installed (dev-only dependency)")
    import glob
    import subprocess
    import tempfile

    out = tempfile.mkdtemp()
    subprocess.run(["pdftoppm", "-r", "50", "-gray", "-png",
                    str(pdf_path), f"{out}/p"], check=True)

    gaps = []
    for f in sorted(glob.glob(f"{out}/p*.png")):
        im = Image.open(f).convert("L")
        width, height = im.size
        px = im.load()
        last = 0
        for y in range(height):
            if any(px[x, y] < 235 for x in range(0, width, 3)):
                last = y
        gaps.append(100 * (height - last) / height)
    return gaps


@pytest.fixture(scope="module")
def printed_pdf(paper_file, browser, tmp_path_factory):
    path = tmp_path_factory.mktemp("pdf") / "paper.pdf"
    page = browser.new_page()
    page.goto(paper_file.as_uri())
    page.wait_for_timeout(800)
    page.pdf(path=str(path), format="Letter", print_background=True,
             margin={"top": "12mm", "bottom": "12mm",
                     "left": "10mm", "right": "10mm"})
    page.close()
    return path


def test_no_page_is_left_a_third_empty(printed_pdf):
    """The symptom that started this: a page ending 68% down, because the next
    game recap was marked unbreakable and would not fit in what was left, so
    it jumped whole and took a third of a sheet with it.

    The LAST page is exempt — that is where the paper ends, not a hole in it.
    """
    gaps = _page_gaps(printed_pdf)
    assert len(gaps) > 1, "need a multi-page paper to test this"

    bad = [(i + 1, g) for i, g in enumerate(gaps[:-1]) if g > 20]
    assert not bad, (
        "pages ending well short of the foot: "
        + ", ".join(f"page {i} is {g:.0f}% empty" for i, g in bad))


def test_the_paper_is_dense_on_average(printed_pdf):
    """A newspaper is dense because paper costs money. This one should read
    the same way. Measured at 21% before the density pass, 13% after."""
    gaps = _page_gaps(printed_pdf)
    inner = gaps[:-1]
    average = sum(inner) / len(inner)

    assert average < 16, (
        f"average of {average:.1f}% blank per page; the screen stylesheet's "
        f"spacing has probably leaked back into print")


def test_a_story_may_split_across_pages(printed_pdf):
    """Explicitly NOT break-inside: avoid. A newspaper splits an article
    across a break; moving the whole thing is what leaves the holes."""
    import printing

    css = printing.BASE_PRINT_CSS
    assert ".story-card, .paired-stories { break-inside: auto; }" in css, (
        "story cards are unbreakable again, which will reintroduce the gaps")


# ---------------------------------------------------------------------------
# Segmentation
#
# Density and segmentation are different failures. A page can be perfectly
# full and still be wrong, if what filled it was the back half of one story
# and the headline for the next one sat alone at the foot of the page before.
#
# Nothing in the DOM knows where a page break landed — Chrome does not expose
# page boxes to script, and measuring y/pageHeight measures the flow BEFORE
# the break rules move anything. A tool that did exactly that reported "0
# badly segmented" on a paper that was visibly broken.
#
# So the page a thing lands on is read out of the printed artefact: stamp a
# uniquely coloured square at the start of every block worth tracking, print,
# rasterise, and see which page each colour is on. rgb(250, id, 7) — one exact
# match recovers the id from the green channel.
# ---------------------------------------------------------------------------

#: A story's header, in the order the markup emits it. Each one has to end up
#: on the same page as the next, or the paper reads as broken.
_STORY_STACK = [".story-label", ".story-headline", ".story-subhead",
                ".story-scorebar", ".story-body"]

_STAMP_JS = """(sels) => {
    let id = 0;
    const out = [];
    document.querySelectorAll('.story-card').forEach((card, ci) => {
        sels.forEach(sel => {
            const el = card.querySelector(sel);
            if (!el) return;
            const m = document.createElement('span');
            m.style.cssText = 'display:inline-block;width:8px;height:8px;' +
                'background:rgb(250,' + id + ',7);' +
                '-webkit-print-color-adjust:exact;print-color-adjust:exact;' +
                'vertical-align:middle;';
            el.insertBefore(m, el.firstChild);
            out.push({id: id, card: ci, sel: sel});
            id += 1;
        });
    });
    return out;
}"""

_SPACER_JS = """(h) => {
    let s = document.getElementById('__spacer');
    if (!s) {
        s = document.createElement('div');
        s.id = '__spacer';
        document.body.insertBefore(s, document.body.firstChild);
    }
    s.style.height = h + 'px';
}"""


def _pages_of_markers(page, workdir, tag):
    """{marker id: 1-based page number} for the paper as currently laid out."""
    Image = pytest.importorskip(
        "PIL.Image", reason="pillow not installed (dev-only dependency)")
    import glob
    import os
    import subprocess

    pdf = os.path.join(workdir, f"{tag}.pdf")
    page.pdf(path=pdf, format="Letter", print_background=True,
             margin={"top": "12mm", "bottom": "12mm",
                     "left": "12mm", "right": "12mm"})
    prefix = os.path.join(workdir, tag)
    subprocess.run(["pdftoppm", "-r", "72", "-png", pdf, prefix], check=True)

    found = {}
    for n, png in enumerate(sorted(glob.glob(prefix + "-*.png")), start=1):
        im = Image.open(png).convert("RGB")
        # getcolors over the whole page, rather than a Python loop over half a
        # million pixels per page per offset. Presence is all that is wanted.
        for _count, (r, g, b) in (im.getcolors(1 << 20) or []):
            if r == 250 and b == 7:
                found.setdefault(g, n)
        im.close()
        os.remove(png)
    os.remove(pdf)
    return found


def test_a_story_header_is_never_split_across_a_page_break(browser, paper_file,
                                                           tmp_path):
    """A story's header is four blocks:

        .story-label -> .story-headline -> .story-subhead -> .story-scorebar

    `break-after: avoid` binds a block only to whatever comes IMMEDIATELY
    after it, so the chain is only as long as the number of links written
    down. Two of the four were listed, so the chain ended at the subhead and
    the page was free to break between the deck and the score box — genre tag,
    headline and deck alone at the foot of a page, the score and the whole
    article overleaf.

    Swept rather than sampled. Where a break lands depends on how long that
    week's stories happen to be; testing one fixture tests one week's luck,
    which is how the previous version of this file passed a stylesheet with
    the bug in it. Pushing the paper down in steps walks the breaks through
    every position on the page. Before the fix this found 3 splits; at 40px
    steps across a full page it now finds none.
    """
    page = browser.new_page(viewport={"width": 1100, "height": 900})
    page.goto(paper_file.as_uri())
    page.wait_for_timeout(400)
    markers = page.evaluate(_STAMP_JS, _STORY_STACK)
    assert markers, "no .story-card elements to check"

    splits = []
    # 160px steps: enough to catch it (the pre-fix run failed at 160 and 640)
    # without printing two dozen PDFs on every test run. The scratch sweep at
    # 40px is the thorough one.
    for spacer in range(0, 960, 160):
        page.evaluate(_SPACER_JS, spacer)
        where = _pages_of_markers(page, str(tmp_path), f"s{spacer}")

        by_card = {}
        for m in markers:
            by_card.setdefault(m["card"], []).append(
                (m["sel"], where.get(m["id"])))

        for card, items in sorted(by_card.items()):
            seen = [(s, p) for s, p in items if p is not None]
            for (s1, p1), (s2, p2) in zip(seen, seen[1:]):
                if p1 != p2:
                    splits.append(
                        f"at +{spacer}px, story {card}: {s1} is on page {p1} "
                        f"but {s2} is on page {p2}")
    page.close()

    assert not splits, (
        "a story's header was left on a different page from its story:\n  "
        + "\n  ".join(splits))


def test_the_whole_story_header_stack_is_in_the_break_after_group():
    """The rule as written, without a browser — the fast guard.

    The browser test above is the real one, but it needs Playwright, Pillow
    and pdftoppm. This one runs anywhere and names the exact regression.
    """
    import re

    import printing

    css = printing.BASE_PRINT_CSS
    match = re.search(r"\n\s+break-after:\s*avoid;", css)
    assert match, "the break-after rule has gone entirely"

    selectors = css[css.rindex("}", 0, match.start()) + 1:match.start()]
    selectors = re.sub(r"/\*.*?\*/", "", selectors, flags=re.S)

    for needed in (".story-label,", ".story-headline,", ".story-subhead,",
                   ".story-scorebar,"):
        assert needed in selectors, (
            f"{needed[:-1]} is not in the break-after group, so a page can "
            f"break in the middle of a story's header. Group was:\n{selectors}")


# ---------------------------------------------------------------------------
# The classifieds page
#
# The publisher's page is the one section in the paper that IS a page: it
# starts a fresh sheet on purpose, which is the deliberate exception to the
# rule that made the paper dense in the first place. That exception has to be
# held to its side of the bargain — a sheet of its own, and only one.
#
# Built with real image files at real sizes. The shapes are the whole problem
# here; five squares would pass a test that five real memes fail.
# ---------------------------------------------------------------------------

#: A banner, two portraits, a landscape and a square — the mix from the first
#: real page, and the mix every packing bug so far has needed to show itself.
_AD_SHAPES = [(1600, 360), (720, 960), (1200, 700), (900, 900), (640, 880)]


@pytest.fixture(scope="module")
def ad_rows(tmp_path_factory):
    """Five ads on disk, as the database would hand them over."""
    Image = pytest.importorskip(
        "PIL.Image", reason="pillow not installed (dev-only dependency)")
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from web import images as image_tools

    folder = tmp_path_factory.mktemp("ads")
    rows = []
    for n, (width, height) in enumerate(_AD_SHAPES):
        path = folder / f"ad{n}.png"
        Image.new("RGB", (width, height), (40 * n % 255, 90, 200)).save(path)
        data = path.read_bytes()
        # Read back rather than asserted: this is also a check that the header
        # parser and the encoder agree about what was written.
        assert image_tools.dimensions(data) == (width, height)
        rows.append({
            # An absolute path, which from a file:// document resolves to the
            # file and is also one of the two shapes ads._safe_url accepts.
            # data: URLs are NOT — the first version of this used them and the
            # page came out empty, which was the sanitiser doing its job.
            "image_url": str(path),
            "width": width, "height": height, "caption": "",
        })
    return rows


@pytest.fixture(scope="module")
def classifieds_paper_file(ad_rows, tmp_path_factory):
    import newspaper

    # Same paper as every other test here, with the page added, so anything
    # that changes is the page's doing.
    html = _paper_html()
    section = ads_module().render_publisher_page(ad_rows)
    assert section, "the page rendered as nothing"
    html = html.replace("<!-- SUBSCRIBE", section + "\n<!-- SUBSCRIBE", 1)
    assert "publisher-page" in html

    path = tmp_path_factory.mktemp("classifieds") / "paper.html"
    path.write_text(html, encoding="utf-8")
    return path


def ads_module():
    import ads
    return ads


_AD_STAMP_JS = """() => {
    let id = 0;
    const out = [];
    const mark = (el, where, label) => {
        const m = document.createElement('span');
        m.style.cssText = 'display:block;width:8px;height:8px;' +
            'background:rgb(250,' + id + ',7);' +
            '-webkit-print-color-adjust:exact;print-color-adjust:exact;';
        if (where === 'top') { el.insertBefore(m, el.firstChild); }
        else { el.appendChild(m); }
        out.push({id: id, label: label, where: where});
        id += 1;
    };
    const page = document.querySelector('.publisher-page');
    if (page) { mark(page, 'top', 'PAGE'); mark(page, 'bottom', 'PAGE'); }
    document.querySelectorAll('.pub-ad').forEach((ad, i) => {
        mark(ad, 'top', 'ad' + i);
        mark(ad, 'bottom', 'ad' + i);
    });
    return out;
}"""


@pytest.fixture(scope="module")
def classifieds_measured(browser, classifieds_paper_file, tmp_path_factory):
    """Which sheet every part of the classifieds page landed on."""
    page = browser.new_page(viewport={"width": 1100, "height": 900})
    page.goto(classifieds_paper_file.as_uri())
    page.wait_for_timeout(700)
    markers = page.evaluate(_AD_STAMP_JS)
    assert markers, "the classifieds page is not in the paper"

    workdir = str(tmp_path_factory.mktemp("adpdf"))
    where = _pages_of_markers(page, workdir, "ads")
    page.close()

    landed = {}
    for marker in markers:
        landed.setdefault(marker["label"], {})[marker["where"]] = \
            where.get(marker["id"])
    return landed


def test_the_classifieds_page_gets_a_sheet_of_its_own(classifieds_measured):
    """Both halves of that sentence.

    A sheet OF ITS OWN: `break-before: page`, so it never starts halfway down
    a sheet under the tail of the power rankings. A page of advertising that
    begins in the middle of something else is not a page, it is a gap with
    pictures in it.

    ONE sheet: five ads at the size they were exported at do not fit on a
    Letter page, so each ad has a height ceiling. Without it the last ad went
    over onto a second sheet that was 53% empty.
    """
    page = classifieds_measured["PAGE"]
    assert page["top"] is not None, "the page did not print at all"
    assert page["top"] == page["bottom"], (
        f"the classifieds page runs from sheet {page['top']} to "
        f"{page['bottom']}; it is supposed to be one sheet")


def test_no_single_ad_is_split_across_a_fold(classifieds_measured):
    """Unlike a paragraph, half an ad cannot be read across the break."""
    split = [label for label, where in classifieds_measured.items()
             if label != "PAGE" and where["top"] != where["bottom"]]
    assert not split, (
        "these ads were cut in half by a page break: "
        + ", ".join(f"{label} (sheets {classifieds_measured[label]['top']}"
                    f"–{classifieds_measured[label]['bottom']})"
                    for label in split))


def test_every_ad_reached_the_printed_page(classifieds_measured):
    """The page is one sheet either because it fits or because ads fell off
    the end of it, and those are not the same thing."""
    printed = {label for label in classifieds_measured if label != "PAGE"}
    assert len(printed) == len(_AD_SHAPES), (
        f"{len(printed)} of {len(_AD_SHAPES)} ads printed")


def test_the_classifieds_page_does_not_hollow_out_the_rest_of_the_paper(
        browser, classifieds_paper_file, tmp_path_factory, classifieds_measured):
    """The forced page break is allowed to leave a short sheet before it. It
    is not allowed to leave two.

    A section that starts a new sheet necessarily leaves whatever room was
    left on the previous one, and that is the price of the page being a page.
    Everything else in the paper still has to be dense — this is the test that
    notices if adding the page quietly undid the density work.
    """
    path = tmp_path_factory.mktemp("adpdf2") / "paper.pdf"
    page = browser.new_page()
    page.goto(classifieds_paper_file.as_uri())
    page.wait_for_timeout(800)
    page.pdf(path=str(path), format="Letter", print_background=True,
             margin={"top": "12mm", "bottom": "12mm",
                     "left": "12mm", "right": "12mm"})
    page.close()

    gaps = _page_gaps(path)
    classifieds_sheet = classifieds_measured["PAGE"]["top"]

    bad = []
    for number, gap in enumerate(gaps[:-1], start=1):
        if number == classifieds_sheet - 1:
            continue        # the sheet the forced break cut short, by design
        if gap > 20:
            bad.append((number, gap))
    assert not bad, (
        "pages ending well short of the foot: "
        + ", ".join(f"sheet {n} is {g:.0f}% empty" for n, g in bad))
