"""
National Rankings: the arithmetic and the page.
Run with: python -m pytest tests/test_rankings.py -q
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

from web import demo_db, luck, rankings  # noqa: E402

SEASON = 2026


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for store in (demo_db._LEAGUES, demo_db._USERS, demo_db._TEAM_WEEKS,
                  demo_db._LINEUPS, demo_db._RATE_EVENTS):
        store.clear()
    luck.clear_cache()
    rankings.clear_cache()
    monkeypatch.delenv("RANKINGS_PUBLIC", raising=False)
    yield


def _g(lg, team, week, pts, opp, result, ppr=None, cov=1.0, name=None, manager=""):
    return {"league_id": lg, "season": SEASON, "week": week, "team_id": team,
            "team_name": name or team, "manager": manager, "provider": "sleeper",
            "team_count": 4, "scoring_type": "ppr", "points": pts,
            "opponent_points": opp, "result": result, "optimal_points": pts,
            "bench_left": 0.0, "ppr_points": pts if ppr is None else ppr,
            "ppr_coverage": cov}


def _league(lg, scale=1.0):
    """a > b > c > d every week. a and c win week 1; a and b win week 2."""
    s = scale
    return [
        _g(lg, "a", 1, 140 * s, 130 * s, "W"), _g(lg, "b", 1, 130 * s, 140 * s, "L"),
        _g(lg, "c", 1, 90 * s, 60 * s, "W"), _g(lg, "d", 1, 60 * s, 90 * s, "L"),
        _g(lg, "a", 2, 150 * s, 95 * s, "W"), _g(lg, "c", 2, 95 * s, 150 * s, "L"),
        _g(lg, "b", 2, 120 * s, 50 * s, "W"), _g(lg, "d", 2, 50 * s, 120 * s, "L"),
    ]


def _ranked(rows):
    return rankings.compute(luck.compute(rows, [], SEASON), rows, SEASON)


def test_score_is_the_weighted_sum():
    rows = {(r["league_id"], r["team_id"]): r for r in _ranked(_league("L"))}
    a = rows[("L", "a")]
    # a: most points (percentile 1.0), beat everyone every week, 2-0.
    assert a["score"] == pytest.approx(100.0)
    assert a["rank"] == 1
    d = rows[("L", "d")]
    # d: fewest points, beat nobody, 0-2.
    assert d["score"] == pytest.approx(0.0)
    assert d["rank"] == 4
    b = rows[("L", "b")]
    # b: 2nd in points (2/3), beat 2 of 3 then 2 of 3 (0.667), 1-1.
    assert b["score"] == pytest.approx(round(100 * (0.5 * 2 / 3 + 0.3 * (2 / 3) + 0.2 * 0.5), 1),
                                       abs=0.11)


def test_points_are_compared_across_leagues_in_ppr():
    # Two leagues identical in shape; the second scores twice as much in PPR.
    rows = _league("L") + _league("M", scale=2.0)
    ranked = _ranked(rows)
    top = ranked[0]
    assert (top["league_id"], top["team_id"]) == ("M", "a")
    # Same all-play and record, so the higher-scoring league's team ranks higher.
    by = {(r["league_id"], r["team_id"]): r for r in ranked}
    assert by[("M", "c")]["rank"] < by[("L", "c")]["rank"]


def test_allplay_is_national_not_league():
    # The best team in a weak league no longer gets full all-play credit.
    rows = _league("L") + _league("M", scale=2.0)
    by = {(r["league_id"], r["team_id"]): r for r in _ranked(rows)}
    # L's a beat 4 of the other 7 teams in the country each week.
    assert by[("L", "a")]["ap_pct"] == pytest.approx(4 / 7, abs=0.001)
    assert by[("M", "a")]["ap_pct"] == pytest.approx(1.0)


def test_teams_beaten_last_week():
    rows = _league("L") + _league("M", scale=2.0)
    by = {(r["league_id"], r["team_id"]): r for r in _ranked(rows)}
    a = by[("L", "a")]
    # Week 2: L's a scored 150; of the other 7, it beat 120, 95, 50 and M's 100.
    assert (a["lw"], a["lw_beat"], a["lw_pool"]) == (2, 4, 8)


def test_unmatched_lineups_fall_back_to_own_points():
    # d's lineups couldn't be matched; its league scores like PPR, so d is
    # ranked on its own points and flagged.
    rows = _league("L")
    for r in rows:
        if r["team_id"] == "d":
            r["ppr_coverage"] = 0.4
    by = {r["team_id"]: r for r in _ranked(rows)}
    assert set(by) == {"a", "b", "c", "d"}
    assert by["d"]["own"] is True and by["a"]["own"] is False


def test_league_with_no_ppr_at_all_is_ranked_on_its_own_points():
    # John's league, 3 Oct: one collected week, no PPR score at all.
    rows = _league("L") + _league("J")
    for r in rows:
        if r["league_id"] == "J":
            r["ppr_points"] = None
            r["ppr_coverage"] = None
    ranked = {(r["league_id"], r["team_id"]): r for r in _ranked(rows)}
    assert {k for k in ranked if k[0] == "J"} == {("J", t) for t in "abcd"}
    assert all(ranked[("J", t)]["own"] for t in "abcd")


def test_unusual_scoring_is_still_left_off():
    # A league averaging ~1,000 points (defenders, yardage bonuses) with no
    # PPR can't be compared, so it stays off the board.
    rows = _league("L") + _league("X", scale=8.0)
    for r in rows:
        if r["league_id"] == "X":
            r["ppr_points"] = None
    ranked = _ranked(rows)
    assert not any(r["league_id"] == "X" for r in ranked)


def test_tiers_and_shares():
    assert rankings.tier(1, 1000) == "Juggernaut"
    assert rankings.tier(500, 1000) == "Mid"
    assert rankings.tier(1000, 1000) == "Generationally bad"
    assert rankings.top_share(30, 1000) == "Top 3%"
    assert rankings.top_share(1000, 1000) == "Bottom 1%"


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    from fastapi.testclient import TestClient
    from web import app as webapp
    monkeypatch.setattr(webapp, "db", demo_db)
    return TestClient(webapp.app)


def _seed(user_id=None):
    mine = demo_db.create_league(provider="sleeper", platform_league_id="k1",
                                 league_name="Kevlarville", paper_name="The Kevlarville Times",
                                 commissioner_name="", season=SEASON,
                                 public_slug="kevlarville-xk1", admin_token="t" * 20,
                                 user_id=user_id)
    other = demo_db.create_league(provider="espn", platform_league_id="o1",
                                  league_name="Secret League", paper_name="The Secret",
                                  commissioner_name="", season=SEASON,
                                  public_slug="secret-xo1", admin_token="u" * 20)
    rows = _league(mine["id"])
    names = {"a": "Hank's Heroes", "b": "Mike Vick Legal Team",
             "c": "The Mid Tier", "d": "Sad Sacks"}
    for r in rows:
        r["team_name"] = names[r["team_id"]]
    others = _league(other["id"], scale=2.0)
    for r in others:
        r["team_name"] = "Hidden " + r["team_id"]
        r["provider"] = "espn"
    demo_db.upsert_team_weeks(rows + others)
    return mine


def _sign_up(client, email="j@example.com"):
    client.post("/signup", data={"email": email, "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"})
    return demo_db.user_by_email(email)


def _pin_season(monkeypatch):
    import nfl_week
    monkeypatch.setattr(nfl_week, "current_season", lambda *a, **k: SEASON)


def test_hidden_until_public(web):
    assert web.get("/rankings").status_code == 404


def test_public_needs_sign_in(web, monkeypatch):
    monkeypatch.setenv("RANKINGS_PUBLIC", "1")
    res = web.get("/rankings", follow_redirects=False)
    assert res.status_code == 303 and "/login" in res.headers["location"]


def test_no_league_asks_you_to_connect(web, monkeypatch):
    monkeypatch.setenv("RANKINGS_PUBLIC", "1")
    _pin_season(monkeypatch)
    _sign_up(web)
    res = web.get("/rankings")
    assert res.status_code == 200 and "Connect your league" in res.text


def test_your_league_named_everyone_else_anonymous(web, monkeypatch):
    monkeypatch.setenv("RANKINGS_PUBLIC", "1")
    _pin_season(monkeypatch)
    user = _sign_up(web)
    _seed(user_id=user["id"])
    res = web.get("/rankings?team=b")
    assert res.status_code == 200
    html = res.text
    assert "Mike Vick Legal Team" in html and "Sad Sacks" in html
    assert "Hidden" not in html and "Secret League" not in html
    assert "league on ESPN" in html
    assert "of 8 teams in America" in html
    assert "would have beaten" in html


def test_cannot_open_someone_elses_league(web, monkeypatch):
    monkeypatch.setenv("RANKINGS_PUBLIC", "1")
    _pin_season(monkeypatch)
    user = _sign_up(web)
    _seed(user_id=user["id"])
    html = web.get("/rankings?league=secret-xo1").text
    assert "Hidden a" not in html and "Hank&#39;s Heroes" in html
