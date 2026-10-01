"""
The Luck Index: the arithmetic and the pages. The SQL twin (migration 031)
was checked against compute() on a real Postgres.
Run with: python -m pytest tests/test_luck.py -q
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

from web import demo_db, luck  # noqa: E402

SEASON = 2026


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for store in (demo_db._LEAGUES, demo_db._USERS, demo_db._TEAM_WEEKS,
                  demo_db._LINEUPS, demo_db._RATE_EVENTS):
        store.clear()
    luck.clear_cache()
    monkeypatch.delenv("LUCK_PUBLIC", raising=False)
    yield


def _g(lg, team, week, pts, opp_pts, result, opt=None, name=None, manager=""):
    return {"league_id": lg, "season": SEASON, "week": week, "team_id": team,
            "team_name": name or team, "manager": manager, "provider": "sleeper",
            "team_count": 4, "scoring_type": "ppr", "points": pts,
            "opponent_points": opp_pts, "result": result,
            "optimal_points": opt if opt is not None else pts, "bench_left": 0.0}


def _league4(lg="L"):
    """a beats b, c beats d in week 1; a beats c, b beats d in week 2.
    Week 1: b scores 2nd-most (130) and loses to a (140): unlucky.
    d scores 3rd (120) - no, d scores 60 and loses."""
    return [
        _g(lg, "a", 1, 140, 130, "W"), _g(lg, "b", 1, 130, 140, "L"),
        _g(lg, "c", 1, 90, 60, "W"), _g(lg, "d", 1, 60, 90, "L"),
        _g(lg, "a", 2, 100, 95, "W", opt=100), _g(lg, "c", 2, 95, 100, "L", opt=110),
        _g(lg, "b", 2, 120, 50, "W"), _g(lg, "d", 2, 50, 120, "L"),
    ]


def test_schedule_luck():
    rows = {r["team_id"]: r for r in luck.compute(_league4(), [], SEASON)}
    # b: week 1 beat 2 of 3 (lost), week 2 beat 3 of 3 (won): 1 win, 1.667 earned
    assert rows["b"]["schedule"] == pytest.approx(-0.667, abs=0.001)
    # c: week 1 beat 1 of 3 and won; week 2 beat 1 of 3 and lost: 1 win, 0.667 earned
    assert rows["c"]["schedule"] == pytest.approx(0.333, abs=0.001)
    assert sum(r["schedule"] for r in rows.values()) == pytest.approx(0, abs=0.01)
    # c's own bench (110) would have beaten a (100): a lineup loss, and a's gift
    assert rows["c"]["own_losses"] == 1 and rows["a"]["gifts"] == 1
    assert rows["b"]["players"] == 0 and rows["b"]["has_players"] is False


def test_player_luck_from_lineups():
    tw = _league4()
    lu = []
    # b's star averages 30 (40 in week 1, 20 in week 2). Week 2 he was 10 under.
    for wk, pts in ((1, 40.0), (2, 20.0)):
        lu.append({"league_id": "L", "season": SEASON, "week": wk, "team_id": "b",
                   "player_key": "star", "slot": "QB", "started": True, "points": pts,
                   "ppr_points": pts})
    rows = {r["team_id"]: r for r in luck.compute(tw, lu, SEASON)}
    b = rows["b"]
    assert b["has_players"] and b["player_dev"] == 0.0
    # Week 1 at average: 130 - 10 = 120 still beats d (60) and c (90): ap 2/3 both ways.
    # Week 2 at average: 120 + 10 = 130 beats everyone, same as real. So no player luck.
    assert b["players"] == pytest.approx(0, abs=0.001)


def test_verdicts_and_percentiles():
    assert luck.verdict(2.0) == "Blessed" and luck.verdict(-0.2) == "Fair"
    assert luck.verdict(-2) == "Cursed"
    pct = [x / 10 for x in range(-50, 51)]       # -5 .. 5 in 101 steps
    assert luck.share_below(pct, 0.0) == pytest.approx(0.5)
    assert luck.share_below(pct, -9) == 0 and luck.share_below(pct, 9) == 1


# --- pages ------------------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    from fastapi.testclient import TestClient
    from web import app as webapp
    monkeypatch.setattr(webapp, "db", demo_db)
    return TestClient(webapp.app)


def _league(name="Kevlarville", pid="k1"):
    return demo_db.create_league(provider="sleeper", platform_league_id=pid,
                                 league_name=name, paper_name=f"The {name} Times",
                                 commissioner_name="", season=SEASON,
                                 public_slug=f"{name.lower()}-x{pid}",
                                 admin_token="t" * 20)


def _seed():
    lg = _league()
    rows = _league4(lg["id"])
    for r in rows:
        r["team_name"] = {"a": "Hank's Heroes", "b": "Mike Vick Legal Team",
                          "c": "The Mid Tier", "d": "Sad Sacks"}[r["team_id"]]
        r["manager"] = {"a": "hank", "b": "johnhenry", "c": "mid", "d": "sad"}[r["team_id"]]
    demo_db.upsert_team_weeks(rows)
    return lg


def _staff(client):
    client.post("/signup", data={"email": "s@example.com",
                                 "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"})
    user = demo_db.user_by_email("s@example.com")
    demo_db.update_user(user["id"], {"plan": "staff"})


def test_closed_until_switched_on(web, monkeypatch):
    lg = _seed()
    assert web.get("/luck").status_code == 404
    assert web.get(f"/luck/{lg['public_slug']}").status_code == 404
    monkeypatch.setenv("LUCK_PUBLIC", "1")
    assert web.get("/luck").status_code == 200


def test_staff_preview(web):
    _staff(web)
    r = web.get("/luck")
    assert r.status_code == 200 and "Staff preview" in r.text


def test_find_your_league_by_your_name(web, monkeypatch):
    monkeypatch.setenv("LUCK_PUBLIC", "1")
    lg = _seed()
    r = web.get("/luck?q=JohnHenry")
    assert r.status_code == 200 and "Mike Vick Legal Team" in r.text
    assert f"/luck/{lg['public_slug']}?team=b" in r.text
    miss = web.get("/luck?q=nobody-at-all")
    assert "No team by that name" in miss.text


def test_league_page_and_a_team_card(web, monkeypatch):
    monkeypatch.setenv("LUCK_PUBLIC", "1")
    lg = _seed()
    page = web.get(f"/luck/{lg['public_slug']}")
    assert page.status_code == 200
    for name in ("Hank&#39;s Heroes", "Mike Vick Legal Team", "The Mid Tier", "Sad Sacks"):
        assert name in page.text
    card = web.get(f"/luck/{lg['public_slug']}?team=b")
    assert "wins of luck" in card.text and "-0.7" in card.text
    assert "Share my luck" in card.text and "played like a" in card.text
    assert "Read this week&#39;s paper" in card.text


def test_a_duplicate_league_row_finds_the_stats(web, monkeypatch):
    monkeypatch.setenv("LUCK_PUBLIC", "1")
    _seed()
    twin = _league(name="Twin", pid="k1")          # same real league, second row
    page = web.get(f"/luck/{twin['public_slug']}")
    assert "Mike Vick Legal Team" in page.text
