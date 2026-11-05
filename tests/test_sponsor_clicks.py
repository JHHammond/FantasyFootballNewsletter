"""Sponsor link presses in a paper (4 Nov). Run with:
python -m pytest tests/test_sponsor_clicks.py -v
"""

import pytest

from tests.test_web import clean_state, client, league  # noqa: F401 — fixtures
from tests.test_analytics import BROWSER, IMESSAGE
from web import analytics, demo_db, sponsor_clicks

SPONSORED = ('<html><head></head><body>'
             '<div class="bp-preview sponsored"><a class="sp-button" href="https://example.com/pp" '
             'rel="sponsored noopener">Make your picks</a></div></body></html>')


@pytest.fixture(autouse=True)
def _events():
    demo_db._EVENTS.clear()
    yield


def _paper(league):
    demo_db.save_paper(league["id"], 3, 2025, "p", "u", {"headline": "X"})
    demo_db._STORAGE[demo_db.storage_path(league["public_slug"], 2025, 3)] = SPONSORED
    return f"/p/{league['public_slug']}/2025/week-3"


def clicks():
    return [e for e in demo_db._EVENTS if e["name"] == "sponsor_click"]


def test_apply_adds_the_counter_once_and_leaves_links_alone():
    once = sponsor_clicks.apply(SPONSORED)
    assert sponsor_clicks.MARK in once
    assert sponsor_clicks.apply(once) == once
    assert 'href="https://example.com/pp"' in once      # still straight to the sponsor


def test_every_paper_carries_it_but_not_the_homepage_preview(client, league):
    url = _paper(league)
    assert sponsor_clicks.MARK in client.get(url, headers=BROWSER).text
    assert sponsor_clicks.MARK not in client.get(url + "?embed=1", headers=BROWSER).text


def test_a_press_is_counted_against_its_league(client, league):
    url = _paper(league)
    r = client.post("/e", data={"n": "sponsor_click", "where": "lines", "paper": url}, headers=BROWSER)
    assert r.status_code == 204
    (e,) = clicks()
    assert e["league_id"] == league["id"]
    assert e["props"] == {"where": "lines", "season": 2025, "week": 3}


def test_unknown_placements_bots_and_bad_paths(client, league):
    url = _paper(league)
    client.post("/e", data={"n": "sponsor_click", "where": "the-moon", "paper": url}, headers=BROWSER)
    client.post("/e", data={"n": "sponsor_click", "where": "lines", "paper": url}, headers=IMESSAGE)
    assert clicks() == []
    client.post("/e", data={"n": "sponsor_click", "where": "numbers", "paper": "/l/secret"}, headers=BROWSER)
    (e,) = clicks()
    assert e["league_id"] is None and e["props"] == {"where": "numbers"}


def test_the_staff_table():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    rows = [{"league_id": "a", "props": {"where": "lines"}, "occurred_at": now},
            {"league_id": "a", "props": {"where": "lines"}, "occurred_at": now},
            {"league_id": "b", "props": {"where": "numbers"}, "occurred_at": now}]
    t = analytics.sponsor_table(rows, [])
    assert t["total"] == 3 and t["last_7"] == 3 and t["leagues"] == 2
    assert [(p["where"], p["clicks"], p["leagues"]) for p in t["placements"]] == [("lines", 2, 1), ("numbers", 1, 1)]
    assert t["weeks"][0]["clicks"] == 3 and t["weeks"][0]["per_1000"] is None
    reads = [{"week_start": t["weeks"][0]["week_start"], "paper_read": 600}]
    assert analytics.sponsor_table(rows, reads)["weeks"][0]["per_1000"] == 5.0


def test_staff_sees_sponsor_clicks(client, league):
    url = _paper(league)
    client.post("/e", data={"n": "sponsor_click", "where": "lines", "paper": url}, headers=BROWSER)
    client.post("/signup", data={"email": "staff@example.com", "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"})
    demo_db.update_user(demo_db.user_by_email("staff@example.com")["id"], {"plan": "staff"})
    body = client.get("/staff/stats?fresh=1").text
    assert "Sponsor clicks, last 28 days" in body
    from markupsafe import escape  # the template escapes the apostrophe
    assert str(escape(analytics.SPONSOR_PLACEMENTS["lines"])) in body
