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
