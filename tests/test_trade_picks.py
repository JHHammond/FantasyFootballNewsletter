"""
The league votes on who won each trade (20 Oct 2026). web/trade_picks.py.
The twin of tests/test_picks.py.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.pop("RESEND_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

import newspaper  # noqa: E402
from web import app as webapp  # noqa: E402
from web import demo_db, picks, trade_picks  # noqa: E402

V1, V2, V3 = "a" * 32, "b" * 32, "c" * 32
TRADE = {"kind": "trade", "status": "complete", "week": 5, "teams": ["Wasteland", "Satan"],
         "adds": [["Wasteland", {"name": "Bijan Robinson", "position": "RB", "nfl_team": "ATL"}],
                  ["Satan", {"name": "Josh Allen", "position": "QB", "nfl_team": "BUF"}]],
         "drops": [], "picks": [], "faab": []}
KEY = trade_picks.trade_key(TRADE)
URL = "/p/kev-7f3a/2026/week-5/trades"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for store in (demo_db._LEAGUES, demo_db._RATE_EVENTS, demo_db._TRADE_VOTES):
        store.clear()
    monkeypatch.setattr(webapp, "db", demo_db)
    monkeypatch.setattr(picks, "is_locked", lambda season, week, now=None: False)
    yield


@pytest.fixture
def client():
    return TestClient(webapp.app)


@pytest.fixture
def league():
    return demo_db.create_league(
        provider="sleeper", platform_league_id="123", league_name="K",
        paper_name="", commissioner_name="", season=2026,
        public_slug="kev-7f3a", admin_token="tok")


def _vote(client, voter, side, trade=KEY, **kw):
    return client.post(URL, json={"voter": voter, "trade": trade, "side": side}, **kw)


# --- the key -----------------------------------------------------------------

def test_the_key_is_stable_and_short():
    assert trade_picks.TRADE_RE.match(KEY)
    assert trade_picks.trade_key(dict(TRADE)) == KEY
    other = dict(TRADE, adds=[["Wasteland", {"name": "Someone Else"}],
                              ["Satan", {"name": "Josh Allen"}]])
    assert trade_picks.trade_key(other) != KEY


def test_only_two_team_trades_are_voted_on():
    assert trade_picks.votable(TRADE)
    three = dict(TRADE, teams=["A", "B", "C"])
    assert not trade_picks.votable(three)


# --- the routes ----------------------------------------------------------------

def test_you_see_the_split_only_after_you_vote(client, league):
    assert _vote(client, V1, "a").status_code == 200
    _vote(client, V2, "a")
    _vote(client, V3, "b")
    before = client.get(URL, params={"v": "d" * 32}).json()
    assert before["enabled"] and before["trades"][KEY] == {"mine": None}
    mine = client.get(URL, params={"v": V3}).json()["trades"][KEY]
    assert mine == {"mine": "b", "a": 2, "b": 1}


def test_voting_again_changes_your_vote(client, league):
    _vote(client, V1, "a")
    r = _vote(client, V1, "b").json()
    assert (r["a"], r["b"], r["mine"]) == (0, 1, "b")
    assert len(demo_db._TRADE_VOTES) == 1


def test_once_voting_closes_everyone_sees_it_and_nobody_can_vote(client, league, monkeypatch):
    _vote(client, V1, "a")
    monkeypatch.setattr(picks, "is_locked", lambda season, week, now=None: True)
    data = client.get(URL, params={"v": V2}).json()
    assert data["locked"] and data["trades"][KEY]["a"] == 1
    assert _vote(client, V2, "b").status_code == 409


@pytest.mark.parametrize("body", [
    {"voter": "short", "trade": KEY, "side": "a"},
    {"voter": V1, "trade": "<script>", "side": "a"},
    {"voter": V1, "trade": KEY, "side": "c"},
    {"voter": V1, "trade": KEY},
])
def test_bad_votes_are_refused(client, league, body):
    assert client.post(URL, json=body).status_code == 400
    assert not demo_db._TRADE_VOTES


def test_only_json_is_accepted(client, league):
    r = client.post(URL, data={"voter": V1, "trade": KEY, "side": "a"})
    assert r.status_code == 415 and not demo_db._TRADE_VOTES


def test_wrong_league_or_season_is_a_404(client, league):
    assert client.get("/p/nope/2026/week-5/trades").status_code == 404
    assert client.get("/p/kev-7f3a/2025/week-5/trades").status_code == 404


def test_trade_votes_share_the_per_ip_limit(client, league, monkeypatch):
    monkeypatch.setattr(picks, "VOTES_PER_IP_PER_HOUR", 2)
    assert _vote(client, V1, "a").status_code == 200
    assert _vote(client, V2, "a").status_code == 200
    assert _vote(client, V3, "a").status_code == 429


# --- in the paper ---------------------------------------------------------------

def test_the_ballot_is_on_the_paper_hidden_until_the_script_opens_it():
    html = newspaper.render_back_page([], None, [], [TRADE])
    assert f'data-trade="{KEY}"' in html
    assert '<div class="tv-row" hidden>' in html
    assert html.count('class="tv-side"') == 2 and "disabled" in html
    assert "TRADE_VOTE" not in html and "/trades" in html     # the script itself


def test_no_ballot_in_the_editor_or_on_a_three_team_trade():
    assert "tv-row" not in newspaper.render_back_page([], None, [], [TRADE], editable=True)
    three = dict(TRADE, teams=["Wasteland", "Satan", "Hands"],
                 adds=TRADE["adds"] + [["Hands", {"name": "Rico Dowdle"}]])
    assert "tv-row" not in newspaper.render_back_page([], None, [], [three])


def test_the_pdf_never_shows_the_ballot():
    import printing
    assert ".tv-row { display: none !important; }" in printing.LONG_PRINT_CSS
