"""Dynasty and redraft as two markets the whole site understands (062)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from web import demo_db, league_kind, trade_feed  # noqa: E402

SEASON = 2026


@pytest.fixture(autouse=True)
def clean():
    stores = (demo_db._LEAGUES, demo_db._DRAFT_PICKS, demo_db._TRADES, demo_db._TRADE_PLAYERS)
    for st in stores:
        st.clear()
    yield
    for st in stores:
        st.clear()


def _league(lid, league_type=None, fmt="redraft"):
    demo_db._LEAGUES[lid] = {"id": lid, "season": SEASON, "provider": "sleeper",
                             "format": fmt, "league_type": league_type}


def _draft(lid, picks_per_team, teams=12, key="p"):
    for i in range(picks_per_team * teams):
        demo_db._DRAFT_PICKS[(lid, SEASON, f"{key}{i}")] = {
            "league_id": lid, "season": SEASON, "player_key": f"{key}{i}", "overall": i + 1,
            "round": i // teams + 1, "team_id": f"t{i % teams}", "team_count": teams}


def _trade(lid, key, a, b):
    demo_db._TRADES[(lid, SEASON, key)] = {"league_id": lid, "season": SEASON, "week": 3,
                                           "trade_key": key, "picks": [], "faab": [],
                                           "traded_at": "2026-09-25T12:00:00+00:00"}
    for p, to in ((a, "A"), (b, "B")):
        demo_db._TRADE_PLAYERS[(lid, SEASON, key, p)] = {"league_id": lid, "season": SEASON, "week": 3,
                                                         "trade_key": key, "player_key": p,
                                                         "from_team": "B" if to == "A" else "A", "to_team": to}


def test_the_platforms_word_beats_the_setup_form_and_the_draft_shape():
    _league("sleeper-says-dynasty", "dynasty", fmt="redraft")      # the 349
    _league("form-says-dynasty", None, fmt="dynasty")
    _league("rookie-draft", None)
    _draft("rookie-draft", 4)
    _league("sleeper-says-redraft", "redraft", fmt="dynasty")
    _draft("sleeper-says-redraft", 4, key="q")
    _league("plain", None)
    _draft("plain", 15, key="r")
    assert demo_db.dynasty_league_ids(SEASON) == {"sleeper-says-dynasty", "form-says-dynasty", "rookie-draft"}


def test_startup_drafts_make_dynasty_adp_and_leave_redraft_adp_alone():
    _league("startup", "dynasty")
    _draft("startup", 15, key="s")
    _league("rookies", "dynasty")
    _draft("rookies", 4, key="s")
    _league("redraft", "redraft")
    _draft("redraft", 15, key="s")
    assert demo_db._dynasty_startups(SEASON) == {"startup"}
    red = {p["player_key"]: p for p in demo_db.adp(SEASON, 1)["players"]}
    dyn = {p["player_key"]: p for p in demo_db.dynasty_adp(SEASON, 1)["players"]}
    assert red["s0"]["drafts"] == 1 and dyn["s0"]["drafts"] == 1   # never the rookie draft
    assert len(dyn) == 15 * 12


def test_trades_go_to_their_own_market():
    _league("d", "dynasty")
    _league("r", "redraft")
    _trade("d", "x", "a", "b")
    _trade("r", "y", "c", "e")
    assert [sorted(t["a"] + t["b"]) for t in demo_db.dynasty_trade_exchanges(SEASON)] == [["a", "b"]]
    assert [sorted(t["a"] + t["b"]) for t in demo_db.trade_exchanges(SEASON)] == [["c", "e"]]


def test_kind_and_market():
    assert league_kind.kind({"league_type": "dynasty", "format": "redraft"}) == "dynasty"
    assert league_kind.kind({"league_type": None, "format": "keeper"}) == "keeper"
    assert league_kind.kind({}) == "redraft"
    assert league_kind.market_of("keeper") == "redraft"
    assert league_kind.clean_market("Dynasty") == "dynasty" and league_kind.clean_market("x") is None
    assert league_kind.league_market({"id": "1", "format": "dynasty"}, {"2"}) == "redraft"  # SQL wins


def test_the_paper_is_told_what_the_platform_says():
    """349 dynasty leagues left the setup form on its default, redraft."""
    from web import generate
    told = generate.build_league_context({"league_type": "dynasty", "format": "redraft"}, [])
    assert generate.FORMAT_NOTES["dynasty"] in told
    assert generate.FORMAT_NOTES["redraft"] not in told


def test_a_dynasty_trade_in_the_feed_is_judged_in_the_dynasty_market():
    side = lambda k: {"players": [{"player_key": k, "name": k}], "picks": [], "faab": 0}  # noqa: E731
    raw = [{"key": "1", "format": "dynasty", "sides": [side("a"), side("b")]},
           {"key": "2", "format": "redraft", "sides": [side("a"), side("b")]}]
    red = {"a": {"v": 1.0, "costs": "#1"}, "b": {"v": 0.2, "costs": "#90"}}
    dyn = {"a": {"v": 0.5, "costs": "#40"}, "b": {"v": 0.5, "costs": "#41"}}
    rows = trade_feed.shape(raw, red, 0.0, dynasty=(dyn, 0.0))
    assert rows[0]["sides"][0]["players"][0]["costs"] == "#40" and rows[0]["verdict"]["fair"]
    assert rows[1]["sides"][0]["players"][0]["costs"] == "#1" and not rows[1]["verdict"]["fair"]


def test_the_market_page_remembers_the_switch(monkeypatch):
    from fastapi.testclient import TestClient
    from web import app as webapp
    monkeypatch.setattr(webapp, "db", demo_db)
    monkeypatch.setattr(webapp, "_trade_rating", lambda season, latest, market="redraft": None)
    c = TestClient(webapp.app)
    r = c.get("/market?m=dynasty")
    assert r.status_code == 200 and 'aria-current="true">Dynasty' in r.text
    assert c.cookies.get(webapp.MARKET_COOKIE) == "dynasty"
    assert 'aria-current="true">Dynasty' in c.get("/market").text            # remembered
    assert 'aria-current="true">Redraft' in c.get("/market?m=redraft").text


def test_the_dynasty_chart_opens_on_four_movers_not_number_one():
    """John, 25 Oct: #1 has been Bijan all year in dynasty."""
    from web import trade_rating as tr
    rated = [f"p{i}" for i in range(30)]
    last = 40
    # Everybody flat except four who moved this week; #1 never moves.
    lines = {k: [[0, i + 1], [last - 8, i + 1], [last, i + 1]] for i, k in enumerate(rated)}
    for k, then, now in (("p10", 20, 11), ("p12", 12, 25), ("p15", 30, 16), ("p20", 15, 21)):
        lines[k] = [[0, then], [last - 8, then], [last, now]]
    trades = {k: 50 for k in rated}
    day = {"lines": lines, "last": last}
    red = tr.chart_default(rated, day, trades)
    dyn = tr.chart_default(rated, day, trades, lead_with_top=False)
    assert red[0] == "p0" and len(red) == tr.CHART_DEFAULT
    # Three movers (John, 26 Oct), the biggest first from each side, never #1.
    assert "p0" not in dyn and len(dyn) == tr.CHART_DEFAULT
    assert set(dyn) <= {"p10", "p12", "p15", "p20"}
