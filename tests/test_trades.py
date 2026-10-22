"""
Trades, kept (045). What one platform's trade turns into on disk, and that
the Tuesday job asks a league about its trades even when it already has
that league's week.
Run with: python -m pytest tests/test_trades.py -q
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from providers.models import Transaction, TransactionPlayer  # noqa: E402
from web import demo_db, league_stats, ppr, trades  # noqa: E402

SEASON, WEEK = 2026, 3


@pytest.fixture(autouse=True)
def clean():
    for store in (demo_db._LEAGUES, demo_db._TEAM_WEEKS, demo_db._LINEUPS,
                  demo_db._TRADES, demo_db._TRADE_PLAYERS, demo_db._TRADE_SCANS):
        store.clear()
    league_stats.clear_cache()
    yield


def _league(pid="a"):
    return demo_db.create_league(provider="sleeper", platform_league_id=pid,
                                 league_name="A League", paper_name="The A Times",
                                 commissioner_name="", season=SEASON,
                                 public_slug=f"slug-{pid}", admin_token="t" * 20)


def _p(pid, name, pos="WR", team="CIN"):
    return TransactionPlayer(player_id=pid, name=name, position=pos, nfl_team=team)


def _ctx():
    """Sleeper knows Chase by name and the Jaguars by team; the ESPN man is
    nobody we can match."""
    index = ppr.build_index({
        "7564": {"full_name": "Ja'Marr Chase", "position": "WR", "team": "CIN"},
        "JAX": {"first_name": "Jacksonville", "last_name": "Jaguars",
                "position": "DEF", "team": "JAX"},
    })
    return ppr.Context(index, {"7564": [23.0, 20.0, 17.0]})


def _trade(**kw):
    base = dict(kind="trade", status="complete", week=WEEK,
                teams=["Team A", "Team B"],
                adds=[("Team A", _p("espn:1", "Ja'Marr Chase"))],
                drops=[("Team B", _p("espn:1", "Ja'Marr Chase"))],
                created=1_760_000_000_000)
    base.update(kw)
    return Transaction(**base)


def test_a_trade_becomes_rows_keyed_like_a_lineup():
    lg = _league()
    heads, players = trades.rows(lg, [_trade()], SEASON, WEEK, _ctx())
    assert len(heads) == 1 and len(players) == 1
    h = heads[0]
    assert h["trade_key"].startswith("t") and len(h["trade_key"]) == 13
    assert h["teams"] == ["Team A", "Team B"] and h["provider"] == "sleeper"
    assert h["traded_at"].startswith("2025-")        # the platform's own stamp
    # Keyed and named by Sleeper, so he joins to what he went on to score.
    p = players[0]
    assert p["player_key"] == "7564" and p["name"] == "Ja'Marr Chase"
    assert (p["from_team"], p["to_team"]) == ("Team B", "Team A")
    assert p["week"] == WEEK and p["trade_key"] == h["trade_key"]


def test_an_unmatched_player_keeps_his_platform_id():
    lg = _league()
    move = _trade(adds=[("Team A", _p("espn:999", "Nobody Known"))],
                  drops=[("Team B", _p("espn:999", "Nobody Known"))])
    _, players = trades.rows(lg, [move], SEASON, WEEK, _ctx())
    assert players[0]["player_key"] == "espn:999"     # countable, not joinable


def test_only_completed_trades_are_kept():
    lg = _league()
    moves = [
        _trade(),
        _trade(status="failed"),                      # vetoed or withdrawn
        Transaction(kind="waiver", status="complete", week=WEEK,
                    teams=["Team A"], adds=[("Team A", _p("espn:2", "Waiver Man"))]),
        Transaction(kind="free_agent", status="complete", week=WEEK,
                    teams=["Team B"], adds=[("Team B", _p("espn:3", "FA Man"))]),
    ]
    heads, players = trades.rows(lg, moves, SEASON, WEEK, _ctx())
    assert len(heads) == 1 and len(players) == 1      # trades only


def test_the_same_trade_twice_in_a_feed_is_one_row():
    lg = _league()
    heads, _ = trades.rows(lg, [_trade(), _trade()], SEASON, WEEK, _ctx())
    assert len(heads) == 1


def test_a_three_way_keeps_one_row_a_player():
    lg = _league()
    move = _trade(
        teams=["A", "B", "C"],
        adds=[("A", _p("espn:1", "Ja'Marr Chase")), ("B", _p("JAX", "Jaguars D/ST", "DEF", "JAX"))],
        drops=[("B", _p("espn:1", "Ja'Marr Chase")), ("C", _p("JAX", "Jaguars D/ST", "DEF", "JAX"))])
    heads, players = trades.rows(lg, [move], SEASON, WEEK, _ctx())
    assert len(heads) == 1
    assert {p["player_key"] for p in players} == {"7564", "JAX"}
    jax = next(p for p in players if p["player_key"] == "JAX")
    assert (jax["from_team"], jax["to_team"]) == ("C", "B")


def test_a_league_with_no_trades_is_still_recorded(monkeypatch):
    """Otherwise a quiet league and a league we never asked look the same,
    and neither the skip logic nor the volume numbers mean anything."""
    lg = _league()
    monkeypatch.setattr("providers.load_transactions", lambda *a, **k: [])
    assert trades.collect_league(demo_db, lg, SEASON, WEEK) == 0
    assert demo_db.trade_scan_league_ids(SEASON, WEEK) == {lg["id"]}
    vol = demo_db.trade_volume(SEASON)
    assert vol["weeks"] == [{"week": WEEK, "leagues": 1, "trades": 0,
                             "players": 0, "resolved": 0}]


def test_volume_counts_what_a_valuation_could_actually_use(monkeypatch):
    lg = _league()
    move = _trade(adds=[("A", _p("espn:1", "Ja'Marr Chase")),
                        ("A", _p("espn:9", "Nobody Known"))],
                  drops=[("B", _p("espn:1", "Ja'Marr Chase")),
                         ("B", _p("espn:9", "Nobody Known"))])
    monkeypatch.setattr("providers.load_transactions", lambda *a, **k: [move])
    assert trades.collect_league(demo_db, lg, SEASON, WEEK, _ctx()) == 1
    vol = demo_db.trade_volume(SEASON)
    assert vol["trades"] == 1 and vol["players"] == 2
    # One of the two resolved to Sleeper; the other can never be joined to
    # a score, and the panel says so.
    assert vol["weeks"][0]["players"] == 2 and vol["weeks"][0]["resolved"] == 1
    assert vol["busiest"][0]["moves"] == 1


def test_the_collector_asks_a_league_it_has_already_collected(monkeypatch):
    """The 029 trap again: a week stored before 045 would never be asked
    about its trades if "done" meant one thing for both."""
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: WEEK)
    monkeypatch.setattr(league_stats, "week_is_final", lambda *a, **k: True)
    lg = _league()
    # Its week is already stored, so load_week must not be called again.
    demo_db.upsert_team_weeks([{
        "league_id": lg["id"], "provider": "sleeper", "platform_league_id": "a",
        "season": SEASON, "week": WEEK, "team_id": "t1", "team_name": "T",
        "manager": "", "points": 100.0, "opponent_points": 90.0, "margin": 10.0,
        "result": "W", "optimal_points": 100.0, "bench_left": 0.0,
        "empty_slots": 0, "team_count": 12, "scoring_type": "ppr",
        "ppr_points": 100.0, "ppr_coverage": 1.0}])
    demo_db.upsert_lineups([{"league_id": lg["id"], "season": SEASON, "week": WEEK,
                             "team_id": "t1", "player_key": "7564", "slot": "WR",
                             "started": True, "points": 20.0, "ppr_points": 20.0}])

    def boom(*a, **k):
        raise AssertionError("the week was already stored; don't fetch it again")
    monkeypatch.setattr("providers.load_week", boom)
    monkeypatch.setattr("providers.load_transactions", lambda *a, **k: [_trade()])
    monkeypatch.setattr(ppr, "context", lambda season, week: None)

    report = league_stats.collect_week(demo_db, WEEK, season=SEASON,
                                       sleep=lambda s: None, log=lambda s: None)
    assert report["scanned"] == 1 and report["trades"] == 1
    assert report["collected"] == 0                  # nothing re-collected
    assert demo_db.trade_scan_league_ids(SEASON, WEEK) == {lg["id"]}

    # And a second run skips it entirely.
    again = league_stats.collect_week(demo_db, WEEK, season=SEASON,
                                      sleep=lambda s: None, log=lambda s: None)
    assert again["scanned"] == 0 and again["skipped_done"] == 1


def test_a_broken_trades_feed_never_costs_the_week(monkeypatch):
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: WEEK)
    monkeypatch.setattr(league_stats, "week_is_final", lambda *a, **k: True)
    lg = _league()

    def boom(*a, **k):
        raise RuntimeError("the transactions feed is down")
    monkeypatch.setattr("providers.load_transactions", boom)
    monkeypatch.setattr(ppr, "context", lambda season, week: None)

    class Week:
        season, week, matchups, byes, teams = SEASON, WEEK, [], [], []
        league = type("L", (), {"season": SEASON, "team_count": 12,
                                "scoring_type": "ppr"})()
    monkeypatch.setattr("providers.load_week", lambda *a, **k: Week())
    report = league_stats.collect_week(demo_db, WEEK, season=SEASON,
                                       sleep=lambda s: None, log=lambda s: None)
    assert report["errors"] == 0 and report["trades"] == 0


# --- the market -------------------------------------------------------------

def test_the_market_reads_an_order_off_the_trades():
    """Three tiers, traded consistently: a star goes for two middles, a
    middle for two scrubs. The solver has to come back with that order."""
    from web import trade_market
    ex = []
    for i in range(40):
        ex.append({"w": 1 + i % 3, "a": ["star"], "b": [f"mid{i % 4}", f"mid{(i + 1) % 4}"]})
        ex.append({"w": 1 + i % 3, "a": [f"mid{i % 4}"], "b": [f"sc{i % 5}", f"sc{(i + 2) % 5}"]})
    got = trade_market.solve(ex, min_trades=3)
    v = got["values"]
    assert v["star"] > v["mid0"] > v["sc0"] > 0 or v["sc0"] == 0
    assert v["star"] > v["mid0"] * 1.5          # a star really is dearer
    assert trade_market._median(list(v.values())) == trade_market.MEDIAN_AT


def test_a_player_nobody_trades_gets_no_number():
    from web import trade_market
    ex = [{"w": 1, "a": ["a"], "b": ["b"]} for _ in range(20)]
    ex.append({"w": 1, "a": ["a"], "b": ["rare"]})
    got = trade_market.solve(ex, min_trades=5)
    assert "rare" not in got["values"]          # one trade is one opinion
    assert got["trades"]["a"] == 21


def test_a_trade_for_himself_is_not_an_equation():
    from web import trade_market
    ex = [{"w": 1, "a": ["a"], "b": ["a"]}] * 5
    assert trade_market.solve(ex)["used"] == 0


def test_a_player_who_adds_nothing_lands_on_the_floor():
    """The league trades "star" for "solid" alone, and also for "solid plus
    a throw-in". The throw-in adds nothing to the price, so the fit wants
    to pay him nothing — and a value can never go below zero.

    The prior matters here and is passed as production passes it. A player
    nobody in the country starts arrives with a prior at the floor, and the
    trades agree, so he floors. Handed no prior at all he would instead be
    pulled toward an average player by the ridge, which is the ridge doing
    its job (it is what forbids the degenerate all-zeros fit) and is why
    the prior is not optional — see trade_market.solve.
    """
    from web import trade_market
    ex = ([{"w": 1, "a": ["star"], "b": ["solid"]} for _ in range(20)]
          + [{"w": 1, "a": ["star"], "b": ["solid", "throwin"]} for _ in range(20)])
    got = trade_market.solve(ex, min_trades=3,
                             prior={"star": 1.4, "solid": 1.4, "throwin": 0.08})
    rows = trade_market.board(got["values"], got["trades"], {}, 10)
    throwin = next(r for r in rows if r["player_key"] == "throwin")
    assert throwin["floored"] and throwin["value"] < trade_market.FLOOR_AT
    assert all(r["value"] >= 0 for r in rows)
    # And the star is still worth what the solid one is, which is the
    # answer those trades actually give.
    star = next(r for r in rows if r["player_key"] == "star")
    solid = next(r for r in rows if r["player_key"] == "solid")
    assert abs(star["value"] - solid["value"]) < 1.0


def test_the_series_is_one_value_a_day():
    """Six points a season is a flat line. Trades carry the day they
    happened, so the line is daily, each point solved on the window
    behind it."""
    from web import trade_market
    ex = [{"day": d, "a": ["a"], "b": ["b", "c"]} for d in range(100, 110) for _ in range(6)]
    got = trade_market.series(ex, range(100, 110), window=3, min_trades=3)
    assert [p["day"] for p in got["series"]["a"]] == list(range(100, 110))
    assert [m["day"] for m in got["days"]] == list(range(100, 110))
    # Each point rests on its window, not on the whole season.
    assert got["days"][-1]["used"] == 18          # three days of six trades


def test_the_line_does_not_drift_over_a_season():
    """Every day is solved cold, and this is the test that forced it.

    The first version warm-started each day from the day before — the
    windows share nearly all their trades, so it looked like free speed.
    But the loop stops when nothing moves by TOLERANCE in a round, and the
    weak direction (scale the whole vector) creeps slower than that while
    still creeping, so each day stopped short of its own minimum and
    handed the error to tomorrow. The values climbed all season: the
    dearest player went from 168 to 1,811 and the ranking fell apart.

    The trades below are drawn the same way every day, so the market is
    genuinely flat and any trend is the solver talking to itself.
    """
    import random

    from web import trade_market
    rng = random.Random(4)
    pool = [f"p{i}" for i in range(60)]
    # Lopsided on purpose: a flat pool converges instantly and would prove
    # nothing. Dearer players are picked more often and go for two cheaper.
    ex = []
    for day in range(1, 60):
        for _ in range(25):
            i = min(int(abs(rng.gauss(0, 18))), 59)
            j, k = rng.randrange(i, 60), rng.randrange(i, 60)
            if len({i, j, k}) == 3:
                ex.append({"day": day, "a": [pool[i]], "b": [pool[j], pool[k]]})
    got = trade_market.series(ex, range(20, 60), window=14, min_trades=3)

    assert all(m["settled"] for m in got["days"]), (
        "a day stopped on the round cap, so its values are wherever the "
        "loop happened to be: " + str([m["rounds"] for m in got["days"]]))

    # The dearest player on the board, start of the season against the end.
    tops = [max(p["value"] for s in got["series"].values() for p in s
                if p["day"] == m["day"]) for m in got["days"]]
    assert max(tops) < 3 * min(tops), (
        f"the top of the board drifted {min(tops):.0f} -> {max(tops):.0f} "
        f"across a flat season")

    # And no single player's line wanders by an order of magnitude.
    for key, s in got["series"].items():
        if len(s) > 10:
            vs = [p["value"] for p in s]
            assert max(vs) < 10 * max(min(vs), 1.0), (key, vs)


def test_a_trade_with_no_timestamp_lands_in_its_week():
    """The platform didn't say when, but it did say which week, and the
    week is real even when the hour isn't."""
    from web import trade_market
    import nfl_week
    opener = nfl_week.season_opener(SEASON)
    out = trade_market.days_for(
        [{"w": 3, "d": None, "a": ["x"], "b": ["y"]},
         {"w": 3, "d": "2026-09-30", "a": ["x"], "b": ["y"]}], SEASON)
    assert out[0]["day"] == opener.toordinal() + 14 + 3        # mid-week 3
    assert out[1]["day"] == __import__("datetime").date(2026, 9, 30).toordinal()


def test_who_a_player_gets_traded_for():
    """The question somebody staring at a price actually has."""
    lg = _league()
    heads, players = [], []
    for i in range(5):
        key = f"tk{i}"
        heads.append({"league_id": lg["id"], "season": SEASON, "week": 2,
                      "trade_key": key, "provider": "sleeper", "teams": ["A", "B"],
                      "picks": [], "faab": [], "traded_at": f"2026-09-1{i} 12:00:00+00"})
        players += [
            {"league_id": lg["id"], "season": SEASON, "week": 2, "trade_key": key,
             "player_key": "chase", "name": "Chase", "position": "WR", "to_team": "A"},
            {"league_id": lg["id"], "season": SEASON, "week": 2, "trade_key": key,
             "player_key": "bijan", "name": "Bijan", "position": "RB", "to_team": "B"},
            {"league_id": lg["id"], "season": SEASON, "week": 2, "trade_key": key,
             "player_key": "mate", "name": "Mate", "position": "WR", "to_team": "A"}]
    demo_db.upsert_trades(heads, players)
    out = demo_db.trade_partners(SEASON, "chase")
    assert out["trades"] == 5
    assert [(c["name"], c["n"]) for c in out["counterparts"]] == [("Bijan", 5)]
    assert [(c["name"], c["n"]) for c in out["alongside"]] == [("Mate", 5)]
    got = out["recent"][0]
    assert [p["name"] for p in got["got"]] == ["Chase", "Mate"]
    assert [p["name"] for p in got["gave"]] == ["Bijan"]
    # A trade's team names belong to its league; this is seen by everyone.
    assert "to_team" not in str(out) and "'A'" not in str(out)


def test_the_market_panel_builds_from_the_database(monkeypatch):
    from web import trade_market
    lg = _league()
    heads, players = [], []
    for i in range(30):
        key = f"t{i:012d}"
        heads.append({"league_id": lg["id"], "season": SEASON, "week": 1 + i % 3,
                      "trade_key": key, "provider": "sleeper", "teams": ["A", "B"],
                      "picks": [], "faab": []})
        for pk, team in (("star", "A"), ("mid", "B"), ("scrub", "B")):
            players.append({"league_id": lg["id"], "season": SEASON, "week": 1 + i % 3,
                            "trade_key": key, "player_key": pk, "name": pk.title(),
                            "position": "RB", "nfl_team": "ATL",
                            "from_team": "B" if team == "A" else "A", "to_team": team})
    demo_db.upsert_trades(heads, players)
    monkeypatch.setattr(trade_market, "MIN_TRADES", 5)
    out = trade_market.build(demo_db, SEASON, [1, 2, 3])
    assert out["used"] == 30 and out["board"]
    assert out["all_series"]
    assert out["board"][0]["name"] == "Star"       # one for two, so dearer
    assert out["chart"]["lines"]


def test_a_trade_with_picks_is_not_an_equation():
    """A dynasty trade is mostly picks, and we don't know what a pick is
    worth. Including it would corrupt the players standing beside it."""
    lg = _league()
    demo_db.upsert_trades(
        [{"league_id": lg["id"], "season": SEASON, "week": 1, "trade_key": "tp",
          "provider": "sleeper", "teams": ["A", "B"],
          "picks": [["A", "2027 1st"]], "faab": []}],
        [{"league_id": lg["id"], "season": SEASON, "week": 1, "trade_key": "tp",
          "player_key": "x", "name": "X", "from_team": "B", "to_team": "A"},
         {"league_id": lg["id"], "season": SEASON, "week": 1, "trade_key": "tp",
          "player_key": "y", "name": "Y", "from_team": "A", "to_team": "B"}])
    assert demo_db.trade_exchanges(SEASON, 1, 18) == []


# ---------------------------------------------------------------- the prior


def _usage(rows):
    """{key: usage} from (key, position, starts, per_start) tuples."""
    return {k: {"position": pos, "rostered": n + 5, "started": n,
                "delivered": round((r or 0) * n, 2), "per_start": r}
            for k, pos, n, r in rows}


def test_a_quarterback_is_not_worth_more_just_for_scoring_more():
    """The flaw raw points would have. A QB outscores a running back every
    week and there is one of him in a lineup, so what a manager pays for is
    points above what the position gives away free. Here the QB and the RB
    are identically placed AT THEIR POSITIONS — both 6 points clear of
    their own replacement, both started as often — so they must price the
    same, even though the QB scores nearly twice as much."""
    from web import trade_prior
    rows = [("qb1", "QB", 900, 24.0), ("qb2", "QB", 900, 18.0),
            ("qb3", "QB", 100, 18.0),
            ("rb1", "RB", 900, 14.0), ("rb2", "RB", 900, 8.0),
            ("rb3", "RB", 100, 8.0)]
    got = trade_prior.build(_usage(rows), min_starts=40)
    assert abs(got["qb1"] - got["rb1"]) < 0.02, got
    # And the replacement-level men at each position price near nothing.
    assert got["qb2"] < got["qb1"] / 4 and got["rb2"] < got["rb1"] / 4


def test_being_started_a_lot_is_most_of_being_valuable():
    """Two players of identical quality, one trusted by ten times as many
    teams. Demand is half the number."""
    from web import trade_prior
    got = trade_prior.build(_usage(
        [("wide", "WR", 9000, 18.0), ("thin", "WR", 900, 18.0),
         ("repl", "WR", 9000, 9.0)]), min_starts=40)
    assert got["wide"] > got["thin"] * 8


def test_no_prior_is_ever_zero():
    """A zero prior hands the solver back the degenerate fit the prior is
    there to prevent — zeroing a player out has to cost something. A player
    who turns up in real trades is worth more than nothing by definition."""
    from web import trade_prior
    got = trade_prior.build(_usage(
        [("good", "WR", 9000, 20.0), ("repl", "WR", 9000, 6.0),
         ("never", "WR", 2, None), ("bench", "WR", 0, None)]), min_starts=40)
    assert all(v >= trade_prior.FLOOR for v in got.values()), got
    assert got["never"] == trade_prior.FLOOR


def test_the_prior_is_normalised_over_the_players_who_trade():
    """Normalising over everybody rostered in the country would put the
    average player at a deep bench piece and make every traded player look
    dear. `only` is what keeps 1.0 meaning "an average TRADED player"."""
    from web import trade_prior
    usage = _usage([("a", "WR", 900, 20.0), ("b", "WR", 900, 15.0),
                    ("c", "WR", 900, 6.0)]
                   + [(f"deep{i}", "WR", 3, None) for i in range(200)])
    everyone = trade_prior.build(usage, min_starts=40)
    traded = trade_prior.build(usage, only=["a", "b", "c"], min_starts=40)
    # Not exactly 1.0: "c" sits at replacement, so he is lifted to the
    # floor afterwards and nudges the average up a little.
    assert sum(traded.values()) / 3 == pytest.approx(1.0, abs=0.05)
    assert everyone["a"] > traded["a"] * 10


def test_a_half_finished_season_gives_a_flat_prior_not_nonsense():
    """Before anybody clears replacement there is nothing to say, and
    saying it flatly is right: the market falls back to the un-anchored fit
    rather than to numbers built on one week."""
    from web import trade_prior
    got = trade_prior.build(_usage([("a", "WR", 5, 9.0), ("b", "WR", 4, 9.0)]),
                            min_starts=40)
    assert set(got.values()) == {1.0}


def test_the_prior_reaches_the_market_and_moves_it():
    """End to end: the demo's own lineups become a prior, the prior reaches
    solve(), and a player usage likes starts out dearer for it."""
    from web import trade_market, trade_prior
    usage = demo_db.trade_prior(SEASON, 1, 18)
    assert isinstance(usage, dict)
    # Built by hand so the test does not depend on the demo seeder.
    pri = trade_prior.build(_usage(
        [("star", "WR", 9000, 22.0), ("solid", "WR", 9000, 8.0)]), min_starts=40)
    ex = [{"a": ["star"], "b": ["solid"]}] * 12
    flat = trade_market.solve(ex)["values"]
    tilted = trade_market.solve(ex, prior=pri)["values"]
    # Twelve trades all saying "these two are equal" against a prior saying
    # the star is worth 25 times as much: the trades should mostly win, and
    # do. What the prior buys is the tilt, not the verdict.
    assert flat["star"] == pytest.approx(flat["solid"], rel=0.05)
    assert tilted["star"] > tilted["solid"] * 1.15
    assert tilted["star"] < tilted["solid"] * 3


def test_the_market_prints_a_price_against_book():
    """End to end on the panel John looks at: trades plus the leagues' own
    usage become a board where each row says what the field pays AND what
    usage says it should.

    Usage here says the star is worth far more than the middling man, who
    is worth more than the replacement man. The trades say all three are
    nearly level. So the star trades UNDER book and the middling man over
    it, which is the gap the feature exists to show: the country changing
    its mind about a player, measured against its own behaviour.

    The replacement man books at about nothing and so gets a price and NO
    comparison. Dividing by his book gave 11.8x in testing, which would
    have printed as "1081% over book".
    """
    from web import trade_market
    lg = _league("mkt")
    demo_db.upsert_nfl_players([
        {"player_key": "star", "name": "Star Man", "position": "WR", "nfl_team": "ATL"},
        {"player_key": "mid", "name": "Mid Man", "position": "WR", "nfl_team": "CHI"},
        {"player_key": "solid", "name": "Solid Man", "position": "WR", "nfl_team": "BUF"}])
    rows = []
    for week in (1, 2, 3):
        for t in range(60):
            for key, pts in (("star", 22.0), ("mid", 14.0), ("solid", 7.0)):
                rows.append({"league_id": lg["id"], "season": SEASON, "week": week,
                             "team_id": f"t{t}", "player_key": key, "slot": "WR",
                             "started": True, "points": pts, "ppr_points": pts})
    demo_db.upsert_lineups(rows)
    pairs = [("star", "solid")] * 12 + [("mid", "solid")] * 10 + [("star", "mid")] * 9
    demo_db.upsert_trades(
        [{"league_id": lg["id"], "season": SEASON, "week": 2,
          "trade_key": f"k{i}", "provider": "sleeper", "teams": ["A", "B"],
          "picks": [], "faab": []} for i in range(len(pairs))],
        [r for i, (a, b) in enumerate(pairs) for r in (
            {"league_id": lg["id"], "season": SEASON, "week": 2, "trade_key": f"k{i}",
             "player_key": a, "name": a, "from_team": "A", "to_team": "B"},
            {"league_id": lg["id"], "season": SEASON, "week": 2, "trade_key": f"k{i}",
             "player_key": b, "name": b, "from_team": "B", "to_team": "A"})])

    out = trade_market.build(demo_db, SEASON, [1, 2, 3], limit=10)
    assert out and out["board"], out
    assert out["priored"] == 3, "every traded player should carry a prior"
    rows_by = {r["player_key"]: r for r in out["board"]}
    star, mid, solid = rows_by["star"], rows_by["mid"], rows_by["solid"]

    # The board ranks them in the order the trades imply, and usage agrees
    # here, so every premium sits at book and the ranks line up.
    assert star["value"] > mid["value"] > solid["value"]
    assert (star["book_rank"], mid["book_rank"], solid["book_rank"]) == (1, 2, 3)
    assert (star["price_rank"], mid["price_rank"], solid["price_rank"]) == (1, 2, 3)
    assert all(r["premium"] == 1.0 for r in (star, mid, solid))
    assert star["book"] and solid["book"]


def test_book_compares_standing_not_units():
    """The bug this guards is one I shipped and had to take back out.

    A plain price-divided-by-prior ratio looked almost plausible and was
    nonsense: the prior is multiplicative (points above replacement TIMES
    starts) so its spread is roughly the square of the market's, which is
    additive. On real demo data every single star came out 60-77% "under
    book" and every cheap player over it — the ratio was measuring the
    units, not the market.

    So the prior is mapped onto the price distribution rank for rank
    first. Below, usage likes A least and the field pays him most, and
    that disagreement is the whole point: he must come out OVER book while
    C, whom usage loves and nobody pays for, comes out under.
    """
    from web import trade_market
    ranked = [{"player_key": "a", "value": 100.0},
              {"player_key": "b", "value": 60.0},
              {"player_key": "c", "value": 20.0}]
    # Usage order is the exact reverse of the price order, and on a wildly
    # wider scale — which is what used to break it.
    trade_market.book(ranked, {"a": 0.2, "b": 3.0, "c": 40.0})
    a, b, c = ranked
    assert (c["book_rank"], b["book_rank"], a["book_rank"]) == (1, 2, 3)
    assert (a["price_rank"], b["price_rank"], c["price_rank"]) == (1, 2, 3)
    # Book is drawn from the prices themselves, so it never leaves the scale.
    assert {r["book"] for r in ranked} == {100.0, 60.0, 20.0}
    assert a["premium"] == 5.0 and c["premium"] == 0.2
    assert b["premium"] == 1.0          # middle of both orders


def test_one_priced_player_has_nothing_to_compare_against():
    """Book is built out of the spread of prices, so a board of one has no
    spread and says nothing rather than 1.00 for everybody."""
    from web import trade_market
    ranked = [{"player_key": "only", "value": 50.0},
              {"player_key": "nousage", "value": 30.0}]
    trade_market.book(ranked, {"only": 1.0})
    assert all(r["premium"] is None and r["book"] is None for r in ranked)


def test_a_market_with_no_usage_still_draws():
    """Supabase without migration 048, or a season with no lineups stored.
    The prior is the thing that makes the values good, so its absence is
    logged loudly — but a staff panel is not worth a 500, and the board
    still ranks."""
    from web import trade_market
    lg = _league("nousage")
    demo_db.upsert_trades(
        [{"league_id": lg["id"], "season": SEASON, "week": 1,
          "trade_key": f"n{i}", "provider": "sleeper", "teams": ["A", "B"],
          "picks": [], "faab": []} for i in range(10)],
        [r for i in range(10) for r in (
            {"league_id": lg["id"], "season": SEASON, "week": 1, "trade_key": f"n{i}",
             "player_key": "one", "name": "One", "from_team": "A", "to_team": "B"},
            {"league_id": lg["id"], "season": SEASON, "week": 1, "trade_key": f"n{i}",
             "player_key": "two", "name": "Two", "from_team": "B", "to_team": "A"})])

    class NoPrior:
        def __getattr__(self, name):
            if name == "trade_prior":
                raise AttributeError("no migration 048")
            return getattr(demo_db, name)

    out = trade_market.build(NoPrior(), SEASON, [1], limit=10)
    assert out["board"], out
    assert out["priored"] == 0
    assert all(r["premium"] is None for r in out["board"])


def test_kickers_and_defences_are_priced_but_not_shown():
    """The first run against real trades put six kickers and four defences
    in the ten dearest players in America, with the actual stars nowhere.

    It is not a quirk of the fit. Those positions are STREAMED — picked up
    and dumped every week — so they are the only players who ever clear
    MIN_TRADES, while a genuine star is traded twice a season. Trading
    often is not being worth something.

    They stay in the fit, because a trade with a kicker in it still says
    something about whoever was opposite him. They just don't go on the
    board.
    """
    from web import trade_market
    lg = _league("stream")
    demo_db.upsert_nfl_players([
        {"player_key": "kick", "name": "Streamed Kicker", "position": "K", "nfl_team": "IND"},
        {"player_key": "dst", "name": "Streamed D/ST", "position": "DEF", "nfl_team": "DEN"},
        {"player_key": "wr", "name": "Real Receiver", "position": "WR", "nfl_team": "CIN"}])
    # The kicker and the defence swap constantly; the receiver goes for the
    # kicker often enough to be priced at all.
    pairs = [("kick", "dst")] * 14 + [("wr", "kick")] * 9
    demo_db.upsert_trades(
        [{"league_id": lg["id"], "season": SEASON, "week": 1, "trade_key": f"s{i}",
          "provider": "sleeper", "teams": ["A", "B"], "picks": [], "faab": []}
         for i in range(len(pairs))],
        [r for i, (a, b) in enumerate(pairs) for r in (
            {"league_id": lg["id"], "season": SEASON, "week": 1, "trade_key": f"s{i}",
             "player_key": a, "name": a, "from_team": "A", "to_team": "B"},
            {"league_id": lg["id"], "season": SEASON, "week": 1, "trade_key": f"s{i}",
             "player_key": b, "name": b, "from_team": "B", "to_team": "A"})])

    out = trade_market.build(demo_db, SEASON, [1], limit=10)
    shown = {r["player_key"] for r in out["board"]}
    assert "wr" in shown, out["board"]
    assert "kick" not in shown and "dst" not in shown, out["board"]
    assert out["streamers"] == 2
    # ...and their trades still counted toward the fit.
    assert out["used"] == len(pairs)


def _streamy(lid, pairs, day0=739900):
    """A league whose trades are spread over real days."""
    lg = _league(lid)
    demo_db.upsert_trades(
        [{"league_id": lg["id"], "season": SEASON, "week": 1 + i // 8,
          "trade_key": f"{lid}{i}", "provider": "sleeper", "teams": ["A", "B"],
          "picks": [], "faab": [],
          "traded_at": f"2026-10-{1 + (i % 20):02d}T12:00:00+00:00"}
         for i in range(len(pairs))],
        [r for i, (a, b) in enumerate(pairs) for r in (
            {"league_id": lg["id"], "season": SEASON, "week": 1 + i // 8,
             "trade_key": f"{lid}{i}", "player_key": a, "name": a,
             "from_team": "A", "to_team": "B"},
            {"league_id": lg["id"], "season": SEASON, "week": 1 + i // 8,
             "trade_key": f"{lid}{i}", "player_key": b, "name": b,
             "from_team": "B", "to_team": "A"})])
    return lg


def test_a_window_is_not_a_season():
    """John, 22 Oct: "why was the chart only one player across two days?"

    Because MIN_TRADES was being applied to each 14-day WINDOW as well as
    to the season. A player with a dozen trades across the autumn has
    three or four inside any one window, so almost nobody qualified for a
    line, and the chart drew the only man who did — a kicker, because
    kickers get churned. The window carries a lighter bar now.
    """
    from web import trade_market
    assert trade_market.WINDOW_MIN_TRADES < trade_market.MIN_TRADES
    # Four players, each traded a handful of times across three weeks.
    names = ["w", "x", "y", "z"]
    pairs = [(names[i % 4], names[(i + 1) % 4]) for i in range(40)]
    _streamy("win", pairs)
    demo_db.upsert_nfl_players([{"player_key": n, "name": n.upper(),
                                 "position": "WR", "nfl_team": "CIN"} for n in names])
    out = trade_market.build(demo_db, SEASON, [1, 2, 3], limit=10)
    assert out["chart"], "no chart drawn at all"
    assert len(out["chart"]["lines"]) >= 2, out["chart"]["lines"]


def test_the_chart_only_offers_players_who_have_a_line():
    """chart() silently drops a key whose series is too short. Handing it
    the dearest six blind produced a chart with ONE player on it and
    nothing on screen to say where the other five went."""
    from web import trade_market
    # Two players trade constantly; a third appears once, late.
    pairs = [("a", "b")] * 30 + [("a", "rare")]
    _streamy("rare", pairs)
    demo_db.upsert_nfl_players([
        {"player_key": k, "name": k, "position": "WR", "nfl_team": "CIN"}
        for k in ("a", "b", "rare")])
    out = trade_market.build(demo_db, SEASON, [1, 2, 3], limit=10)
    drawn = {l["key"] for l in (out["chart"] or {}).get("lines", [])}
    assert "rare" not in drawn, drawn
    # And whatever is offered genuinely has points behind it.
    for line in (out["chart"] or {}).get("lines", []):
        assert len(line["dots"]) > 1, line["key"]


def test_a_market_too_thin_to_draw_says_so():
    """One line is not a market, it is an accident. Better to say there is
    no line yet than to draw a lonely one and explain nothing."""
    from web import trade_market
    _streamy("thin2", [("solo", "mate")] * 12)
    demo_db.upsert_nfl_players([
        {"player_key": k, "name": k, "position": "WR", "nfl_team": "CIN"}
        for k in ("solo", "mate")])
    out = trade_market.build(demo_db, SEASON, [1, 2, 3], limit=10)
    if not out.get("chart"):
        assert out["no_chart"], out
        assert out["board"], "the board should still stand without a chart"
