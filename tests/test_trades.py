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
