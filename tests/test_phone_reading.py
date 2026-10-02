"""The paper on a phone: structure and the section bar (2 Oct 2026).

Measured on a real Week 3 paper at 375px: 18,700px tall, about 23 screens,
with no way to move around it, and not one heading tag in the document, so
neither a screen reader nor reader mode could find a single section. These
pin the structure. The layout half, in a real browser, is in test_layout.py.
"""

from __future__ import annotations

import re
import tempfile

import pytest

import newspaper
from newspaper import build_edition, build_power_rankings_from_matchups, render_html


def _games():
    from providers import (SleeperProvider, TTLCache, apply_lineup_gaps,
                           week_to_legacy_games)
    from tests import fixtures

    p = SleeperProvider(cache=TTLCache(cache_dir=tempfile.mkdtemp(), namespace="ph"))
    p._get = lambda u, params=None: fixtures.fake_get(u, params)
    return week_to_legacy_games(apply_lineup_gaps(p.get_week("TESTLEAGUE", 2025, 3)))


def _ai(games, awards=True):
    content = []
    for g in games:
        a, b = g["team_1"], g["team_2"]
        w, l = (a, b) if a["points"] >= b["points"] else (b, a)
        content.append({
            "winner": w["team_name"], "loser": l["team_name"],
            "headline": f"{w['team_name']} WINS", "body": "It happened.",
            "teaser": "Something", "winner_score": w["points"],
            "loser_score": l["points"], "winner_record": "1-0",
            "loser_record": "0-1", "margin": round(w["points"] - l["points"], 1)})
    # Written in the reverse of the platform's order, so a teaser that
    # linked by position instead of by game would point at the wrong story.
    content.reverse()
    ai = {"headline": "THE HEADLINE", "lead_story": "Lead.",
          "matchup_content": content,
          "power_rankings_comments": {}, "fraud_watch": "Somebody."}
    if awards:
        ai["awards"] = [{"title": "KYLE PITTS AWARD", "body": "Short."}]
    return ai


@pytest.fixture(scope="module")
def games():
    return _games()


def _paper(games, **kw):
    summary = __import__("storylines").get_weekly_storylines(games)
    edition = build_edition("The Test Times", 3, summary, games,
                            build_power_rankings_from_matchups(games),
                            _ai(games, **kw), subscribe_slug="s")
    edition["paper_name"] = "The Test Times"
    return edition, render_html(edition)


def _tags(html, cls):
    """The tag names carrying a class, in document order."""
    return re.findall(r'<(\w+) class="' + re.escape(cls) + r'[" ]', html)


def test_the_paper_has_one_h1_and_it_is_the_name(games):
    _, html = _paper(games)
    assert re.findall(r"<h1[ >]", html) == ["<h1 "]
    assert _tags(html, "paper-name") == ["h1"]


def test_sections_and_stories_are_real_headings(games):
    _, html = _paper(games)
    assert set(_tags(html, "section-title-full")) == {"h2"}
    assert set(_tags(html, "col-section-label")) == {"h2"}
    assert set(_tags(html, "story-headline")) == {"h3"}
    assert set(_tags(html, "award-title")) == {"h3"}
    assert _tags(html, "headline") == ["h2"]


def test_the_paper_is_one_main_landmark(games):
    _, html = _paper(games)
    assert html.count("<main ") == 1 and html.count("</main>") == 1


def test_headings_keep_the_look_of_the_divs_they_replaced(games):
    """Every heading is styled by its class; the browser's own heading
    margins and sizes are reset so the change is invisible on the page."""
    _, html = _paper(games)
    assert "h1, h2, h3 {{" not in html  # the f-string escaping came through
    assert "h1, h2, h3 { margin: 0; font-size: inherit; font-weight: inherit; }" in html


def test_every_game_story_has_an_anchor(games):
    _, html = _paper(games)
    ids = re.findall(r'class="story-headline[^"]*" id="(game-\d+)"', html)
    assert ids == [f"game-{n}" for n in range(1, len(games) + 1)]


def test_each_front_page_teaser_links_to_its_own_story(games):
    """Linked by the game, not by position: the stories are in the writer's
    order and the teasers in the platform's, and _ai() makes those differ."""
    _, html = _paper(games)
    stories = dict(re.findall(
        r'id="(game-\d+)"[^>]*>([^<]+) WINS</h3>', html))
    links = re.findall(r'<a class="teaser-link" href="#(game-\d+)">([^<]+?) def\. ', html)
    assert len(links) == len(games)
    for anchor, winner in links:
        assert stories[anchor] == winner


def test_the_section_bar_lists_the_sections_in_page_order(games):
    _, html = _paper(games)
    nav = re.search(r'<nav class="section-nav"[^>]*>(.*?)</nav>', html).group(1)
    hrefs = re.findall(r'href="#([\w-]+)"', nav)
    assert hrefs[:3] == ["front", "honor-roll", "games"]
    positions = [html.index(f'id="{h}"') for h in hrefs]
    assert positions == sorted(positions), hrefs
    for h in hrefs:
        assert html.count(f'id="{h}"') == 1, h


def test_the_section_bar_leaves_out_a_section_the_paper_lacks(games):
    edition, html = _paper(games)
    nav = re.search(r'<nav class="section-nav"[^>]*>(.*?)</nav>', html).group(1)
    assert ('href="#awards"' in nav) == bool(edition["awards_html"].strip())
    assert ('href="#back-page"' in nav) == bool(str(edition.get("back_page_html") or "").strip())
    assert newspaper.render_section_nav({}).count("<a ") == 1  # Front only


def test_the_section_bar_never_reaches_paper(games):
    """A bare max-width query also matches a printed page; the bar is
    screen-only by its own media query and hidden by default."""
    _, html = _paper(games)
    assert ".section-nav { display: none; }" in html
    assert "@media screen and (max-width: 760px) {\n            .section-nav {" in html


def test_the_edition_line_does_not_repeat_the_paper_name(games):
    edition, _ = _paper(games)
    assert "The Test Times" not in edition["edition_line"]
    assert edition["edition_line"].startswith("Week 3 Edition")
