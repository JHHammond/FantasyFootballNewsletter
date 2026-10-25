"""The live trade feed (24 Oct 2026): shaping, filters, the free limit, and
the poller never recording a scan (which would hide the rest of the week
from the Tuesday collector)."""
from datetime import datetime, timezone

from web import trade_feed

PRICES = {
    "a": {"k": "a", "pos": "RB", "costs": "#1", "v": 1.0, "photo": None},
    "b": {"k": "b", "pos": "WR", "costs": "#2", "v": 0.95, "photo": None},
    "c": {"k": "c", "pos": "TE", "costs": "#40", "v": 0.10, "photo": None},
}


def _t(key, sides, at="2026-10-24T20:00:00+00:00", **kw):
    return dict({"key": key, "at": at, "provider": "sleeper", "team_count": 12,
                 "scoring_type": "ppr", "format": "redraft", "sides": sides}, **kw)


def _side(*keys, picks=(), faab=0):
    return {"players": [{"player_key": k, "name": k.upper(), "position": PRICES.get(k, {}).get("pos")} for k in keys],
            "picks": list(picks), "faab": faab}


def test_shape_weighs_a_fair_swap_and_flags_a_dump():
    now = datetime(2026, 10, 24, 20, 30, tzinfo=timezone.utc)
    rows = trade_feed.shape([_t("1", [_side("a"), _side("b")]), _t("2", [_side("a"), _side("c")])],
                            PRICES, 0.0, now=now)
    fair, dump = rows
    assert fair["straight"] and fair["verdict"]["fair"] and fair["ago"] == "30 min ago"
    assert dump["verdict"]["lopsided"] and dump["verdict"]["winner"] == 0


def test_picks_are_not_weighed_and_one_sided_deals_are_dropped():
    rows = trade_feed.shape([_t("1", [_side("a"), _side(picks=["2027 1st"])]),
                             _t("2", [_side("a")])], PRICES, 0.0)
    assert len(rows) == 1 and rows[0]["picks"] and rows[0]["verdict"] is None


def test_free_accounts_filter_by_position_only():
    f = trade_feed.clean_filters({"pos": "RB", "shape": "straight", "lopsided": "1"}, paid=False)
    assert f["pos"] == "RB" and not f["shape"] and not f["lopsided"]
    f = trade_feed.clean_filters({"pos": "XX", "shape": "straight"}, paid=True)
    assert f["pos"] == "" and f["shape"] == "straight"


def test_filters_apply():
    rows = trade_feed.shape([_t("1", [_side("a"), _side("b")]), _t("2", [_side("a"), _side("c")]),
                             _t("3", [_side("b"), _side("c")], team_count=10)], PRICES, 0.0)
    pick = lambda **kw: [r["key"] for r in trade_feed.apply(rows, dict(trade_feed.clean_filters({}, True), **kw))]  # noqa: E731
    assert pick(pos="TE") == ["2", "3"]
    assert pick(lopsided="1") == ["2", "3"]
    assert pick(size="10") == ["3"]
    assert pick(player="a") == ["1", "2"]
    mine = trade_feed.apply(rows, dict(trade_feed.clean_filters({}, True), mine="1"), mine={"c"})
    assert [r["key"] for r in mine] == ["2", "3"]


def test_the_poller_never_records_a_scan(monkeypatch):
    from web import live_trades, trades as trades_mod
    import providers
    calls = {"scans": 0, "upserts": 0}

    class DB:
        def sleeper_leagues(self, season):
            return [{"id": "L1", "provider": "sleeper", "platform_league_id": "1", "league_name": "x"}]

        def upsert_trades(self, heads, players):
            calls["upserts"] += 1

        def upsert_trade_scan(self, row):
            calls["scans"] += 1

    monkeypatch.setattr(providers, "load_transactions", lambda *a, **k: ["move"])
    monkeypatch.setattr(trades_mod, "rows", lambda league, moves, s, w, ctx=None: ([{"trade_key": "t"}], []))
    monkeypatch.setattr(live_trades, "PACE", 0)
    report = live_trades.poll(DB(), 2026, 8, log=lambda *a: None)
    assert report["trades"] == 1 and calls["upserts"] == 1 and calls["scans"] == 0
