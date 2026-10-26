"""My Team and its Portfolio (web/team.py, /team)."""
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from web import team as tm  # noqa: E402

BASE = date(2026, 8, 27).toordinal()
OPENER = date(2026, 9, 10).toordinal()


def _rating(players, lines=None, last=60):
    """players: [(key, name, pos, rank)]"""
    return {"decay": 0.95, "shift": 8.0, "spot": 0.0,
            "pickable": [{"k": k, "name": n, "pos": p, "rank": r, "costs": f"#{r}", "v": 100.0 / r}
                         for k, n, p, r in players],
            "daily": {"base": date.fromordinal(BASE).isoformat(), "last": last,
                      "lines": lines or {}}}


def test_ranks_share_the_better_place_and_ordinals_handle_the_teens():
    assert tm.rank_of({"a": 10, "b": 10, "c": 5}, "b") == 1
    assert tm.rank_of({"a": 10, "b": 10, "c": 5}, "c") == 3
    assert [tm.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 112)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "112th"]


def test_values_add_up_and_fall_with_place():
    p = tm.Prices(_rating([("a", "A", "RB", 1), ("b", "B", "RB", 12)]))
    assert p.now("a") == 100.0
    assert 0 < p.now("b") < p.now("a")
    assert p.now("nobody") == 0.0 and not p.priced("nobody")


def test_a_line_carries_forward_and_ends_on_todays_place():
    r = _rating([("a", "A", "WR", 5)], lines={"a": [[0, 20], [10, 8], [30, 6]]}, last=40)
    p = tm.Prices(r)
    d = p.dense("a")
    assert len(d) == 41
    assert d[5] == p.value(20) and d[10] == p.value(8) and d[29] == p.value(8)
    assert d[-1] == p.now("a") == p.value(5)


def test_a_draft_pick_is_read_on_the_markets_scale_without_ties():
    r = _rating([(k, k, "RB", i) for i, k in enumerate("abcd", 1)])
    p = tm.Prices(r, adp={"a": 1.5, "b": 3.0, "c": 9.0, "d": 20.0})
    assert p.draft_value(1.0) == p.value(1)
    # Two picks between the same two ADPs still cost different amounts.
    assert p.draft_value(4.0) > p.draft_value(8.0) > p.draft_value(10.0)


def test_the_flex_counts_as_the_players_position():
    r = _rating([("rb1", "", "RB", 1), ("rb2", "", "RB", 2), ("rb3", "", "RB", 3), ("wr1", "", "WR", 40)])
    p = tm.Prices(r)
    pos = {"rb1": "RB", "rb2": "RB", "rb3": "RB", "wr1": "WR"}
    got = tm.starter_values(pos, pos, p, {"RB": 2, "WR": 1, "FLEX": 1})
    assert set(got["starters"]) == set(pos)
    assert got["by_pos"]["RB"] == round(p.now("rb1") + p.now("rb2") + p.now("rb3"), 1)


def test_lineup_slots_are_read_off_the_rosters():
    rows = [{"team_id": t, "week": 3, "player_key": f"{t}{i}", "slot": s, "started": s != "BN", "points": 1}
            for t in ("x", "y") for i, s in enumerate(["QB", "RB", "RB", "WR", "FLEX", "DEF", "BN"])]
    assert tm.lineup_slots(rows, 3) == {"QB": 1, "RB": 2, "WR": 1, "FLEX": 1, "DEF": 1}
    assert tm.lineup_slots(rows, 9) == tm.DEFAULT_SLOTS


def test_the_story_is_the_biggest_disagreement():
    ranks = [{"pos": "WR", "prod_rank": 10, "value_rank": 2}, {"pos": "RB", "prod_rank": 2, "value_rank": 7},
             {"pos": "QB", "prod_rank": 3, "value_rank": 4}, {"pos": "K", "prod_rank": 1, "value_rank": None}]
    assert tm.story(ranks).startswith("Your receivers are valued 2nd")
    assert tm.story([{"pos": "QB", "prod_rank": 3, "value_rank": 4}]) is None


def test_hexagon_puts_first_on_the_rim_and_last_off_the_centre():
    h = tm.hexagon({p: 1 for p in tm.POSITIONS}, {p: 12 for p in tm.VALUED}, 12)
    assert all(x["r"] == 1.0 for x in h["prod"])
    assert all(x["r"] == 0.16 for x in h["value"] if x["r"] is not None)
    assert [x["r"] for x in h["value"]][4:] == [None, None]          # DEF and K: no value
    g = tm.geometry(h)
    assert len(g["prod"].split()) == 6 and len(g["value"].split()) == 4


# ---- the whole page, on a tiny league ------------------------------------

def _league():
    """Two teams, five weeks. Mine (t0) drafted d1, kept k1 from week 1,
    traded w1 away for r1 in week 3, and picked up p1 in week 4."""
    players = [("d1", "Drafted", "RB", 3), ("k1", "Kept", "QB", 8), ("w1", "Sold", "WR", 10),
               ("r1", "Bought", "RB", 6), ("p1", "Pickup", "WR", 20), ("o1", "Other", "WR", 2),
               ("o2", "Other2", "QB", 4), ("o3", "Other3", "TE", 30), ("m3", "MyTE", "TE", 50)]
    lines = {k: [[0, r + 5], [40, r]] for k, _, _, r in players}
    rating = _rating(players, lines=lines)
    pos = {k: p for k, _, p, _ in players}
    rost = {w: {"t0": ["d1", "k1", "m3"] + (["w1"] if w < 3 else ["r1"]) + (["p1"] if w >= 4 else []),
                "t1": ["o1", "o2", "o3"] + (["r1"] if w < 3 else ["w1"])} for w in range(1, 6)}
    lineups = [{"team_id": t, "week": w, "player_key": k, "slot": pos[k], "started": True, "points": 10}
               for w, teams in rost.items() for t, ks in teams.items() for k in ks]
    tw = [{"team_id": t, "week": w, "team_name": {"t0": "Mine", "t1": "Theirs"}[t], "points": 50}
          for w in rost for t in ("t0", "t1")]
    when = date.fromordinal(OPENER + 16).isoformat()
    trades = [{"trade_key": "x", "week": 3, "player_key": "w1", "from_team": "Mine", "to_team": "Theirs", "traded_at": when},
              {"trade_key": "x", "week": 3, "player_key": "r1", "from_team": "Theirs", "to_team": "Mine", "traded_at": when}]
    draft = [{"player_key": "d1", "overall": 2, "round": 1, "team_id": "t0", "team_count": 12},
             {"player_key": "w1", "overall": 30, "round": 3, "team_id": "t0", "team_count": 12}]
    prices = tm.Prices(rating, adp={k: float(r) for k, _, _, r in players})
    return tm.assemble(my_team="t0", lineups=lineups, team_weeks=tw, trades=trades, draft=draft,
                       positions=pos, prices=prices, pickable={p["k"]: p for p in rating["pickable"]},
                       spot=0.0, opener=OPENER, latest=5), prices


def test_portfolio_cost_basis_for_each_way_a_player_arrives():
    v, prices = _league()
    f = v["portfolio"]
    how = {h["k"]: h for h in f["holdings"]}
    assert how["d1"]["how"] == "draft" and how["d1"]["basis"] == prices.draft_value(2.0)
    assert how["k1"]["how"] == "kept" and how["k1"]["basis"] == prices.on("k1", 0)
    assert how["r1"]["how"] == "trade" and how["r1"]["label"].startswith("Traded for")
    assert how["p1"]["how"] == "waiver" and how["p1"]["basis"] == 0.0 and how["p1"]["label"] == "Picked up, week 4"
    assert f["value"] == round(sum(h["value"] for h in f["holdings"]), 1)
    assert f["unrealized"] == round(f["value"] - f["cost"], 1)
    sold = {s["k"]: s for s in f["sold"]}
    assert set(sold) == {"w1"}
    assert sold["w1"]["basis"] == round(prices.draft_value(30 - 1 + 1.0), 1)   # his draft pick
    assert len(f["line"]) == len(v["chart"]["days"])


def test_team_header_ranks_and_chart():
    v, _ = _league()
    assert v["team"] == "Mine" and v["header"]["teams"] == 2
    assert {r["pos"] for r in v["ranks"]} == set(tm.POSITIONS)
    assert all(r["value_rank"] is None for r in v["ranks"] if r["pos"] in ("K", "DEF"))
    assert len(v["chart"]["mine"]) == len(v["chart"]["days"]) == len(v["chart"]["avg"])
    assert len(v["chart"]["others"]) == 1


# ---- the routes ------------------------------------------------------------

@pytest.fixture
def app(monkeypatch):
    from web import app as webapp, demo_db
    monkeypatch.setattr(webapp, "db", demo_db)
    return webapp, demo_db


def _signed_in(webapp, demo_db):
    from fastapi.testclient import TestClient
    from web import auth
    u = demo_db.create_user(f"t{os.urandom(4).hex()}@x.test", "x")
    c = TestClient(webapp.app)
    c.cookies.set(auth.SESSION_COOKIE, auth.make_session(u["id"]))
    return c, u


def test_team_is_closed_until_switched_on(app, monkeypatch):
    webapp, demo_db = app
    monkeypatch.delenv("TEAM_PUBLIC", raising=False)
    c, _ = _signed_in(webapp, demo_db)
    assert c.get("/team").status_code == 404


def test_team_asks_signed_out_readers_to_sign_in(app, monkeypatch):
    from fastapi.testclient import TestClient
    webapp, _ = app
    monkeypatch.setenv("TEAM_PUBLIC", "1")
    r = TestClient(webapp.app).get("/team", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_picking_your_team_only_works_in_your_own_league(app, monkeypatch):
    webapp, demo_db = app
    monkeypatch.setenv("TEAM_PUBLIC", "1")
    c, u = _signed_in(webapp, demo_db)
    mine = demo_db.create_league(user_id=u["id"], season=__import__("nfl_week").current_season(),
                                 provider="espn", league_name="L")
    other = demo_db.create_league(user_id="somebody-else", season=mine["season"], provider="espn")
    r = c.post("/team/me", data={"league": other["id"], "team": "3"}, follow_redirects=False)
    assert r.status_code == 404
    r = c.post("/team/me", data={"league": mine["id"], "team": "3"}, follow_redirects=False)
    assert r.status_code == 303
    assert demo_db._LEAGUES[mine["id"]]["my_team_id"] == "3"
    assert c.get("/team").status_code == 200
