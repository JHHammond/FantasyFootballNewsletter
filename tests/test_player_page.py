"""The player page's pieces (web/player_page.py, migration 056)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import demo_db, player_page as pp  # noqa: E402

SEASON = 2026


def _deal(i, gave, got_with=(), picks_got=(), picks_gave=(), faab_gave=0, teams=2):
    return {"key": f"k{i}", "week": 6, "day": "2026-10-22", "provider": "sleeper",
            "team_count": 12, "scoring_type": "ppr", "teams": teams,
            "with": [{"player_key": k, "name": k.title(), "position": "WR"} for k in got_with],
            "gave": [{"player_key": k, "name": k.title(), "position": "WR"} for k in gave],
            "got_picks": list(picks_got), "gave_picks": list(picks_gave),
            "got_faab": 0, "gave_faab": faab_gave}


def test_a_trade_reads_as_got_and_for():
    t = pp.shape_trades([_deal(1, ["nabers"], got_with=["wandale"], picks_gave=["2027 2nd"], faab_gave=15)])[0]
    assert t["day"] == "Oct 22" and t["league"] == "12-team PPR · Sleeper"
    assert [i["label"] for i in t["got"]] == ["Wandale"]
    assert [i["label"] for i in t["gave"]] == ["Nabers", "2027 2nd", "$15 FAAB"]
    assert t["gave_keys"] == ["nabers"] and not t["three_way"]


def test_traded_for_counts_each_player_once_per_deal():
    trades = pp.shape_trades([_deal(1, ["nabers"]), _deal(2, ["nabers", "adams"]), _deal(3, ["adams"]),
                              _deal(4, ["nabers"])])
    got = [(p["key"], p["n"]) for p in pp.traded_for(trades)]
    assert got == [("nabers", 3), ("adams", 2)]


def test_your_trades_are_the_ones_your_players_went_the_other_way_in():
    trades = pp.shape_trades([_deal(1, ["nabers"]), _deal(2, ["cook"]),
                              _deal(3, ["adams", "cook"], got_with=["nabers"])])
    mine = pp.your_trades(trades, {"nabers", "adams"})
    # Deal 3 had Nabers on HIS side, not the other: only Adams counts there.
    assert [t["key"] for t in mine] == ["k1", "k3"]
    assert mine[1]["yours"] == {"adams"}


def test_a_page_with_no_price_still_says_who_he_is():
    s = pp.summary("kc", {"pickable": []}, {"kc": {"name": "Chiefs", "position": "DEF", "nfl_team": "KC"}})
    assert s["name"] == "Chiefs" and not s["priced"]
    s = pp.summary("p", {"pickable": [{"k": "p", "name": "Puka", "pos": "WR", "team": "LAR", "photo": None,
                                       "costs": "1.01", "rank": 1, "drafted": "1.03", "dp": 3,
                                       "trades": 255, "firm": "settled"}]}, {})
    assert s["priced"] and s["moved"] == 2 and s["costs"] == "1.01"


def test_the_demo_twin_splits_sides_picks_and_faab():
    lg = "L-pp"
    demo_db._TRADES[(lg, SEASON, "t1")] = {"league_id": lg, "season": SEASON, "week": 5, "trade_key": "t1",
                                           "provider": "sleeper", "team_count": 10, "scoring_type": "ppr",
                                           "traded_at": "2026-10-08T12:00:00", "picks": [["A", "2027 3rd"], ["B", "2027 1st"]],
                                           "faab": [["A", "B", 20]]}
    for k, to in (("him", "A"), ("buddy", "A"), ("nabers", "B")):
        demo_db._TRADE_PLAYERS[(lg, SEASON, "t1", k)] = {"league_id": lg, "season": SEASON, "week": 5,
                                                        "trade_key": "t1", "player_key": k, "name": k,
                                                        "position": "WR", "from_team": None, "to_team": to}
    try:
        t = demo_db.player_trades(SEASON, "him")["trades"][0]
        assert [p["player_key"] for p in t["with"]] == ["buddy"]
        assert [p["player_key"] for p in t["gave"]] == ["nabers"]
        assert t["got_picks"] == ["2027 3rd"] and t["gave_picks"] == ["2027 1st"]
        assert t["got_faab"] == 0 and t["gave_faab"] == 20 and t["teams"] == 2
        assert lg not in str(t) and "t1" not in str(t)            # no league or trade id leaves
    finally:
        demo_db._TRADES.pop((lg, SEASON, "t1"), None)
        for k in ("him", "buddy", "nabers"):
            demo_db._TRADE_PLAYERS.pop((lg, SEASON, "t1", k), None)
