"""
The Undefeated Watch (068) and the waiver wire it reads (067).

The engine's SQL twin is checked against compute() on a real Postgres by
`python -m scripts.check_undefeated_sql`; these pin the rules themselves,
the words, the page's gate and its privacy, and the collector.
Run with: python -m pytest tests/test_undefeated.py -q
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.pop("RESEND_API_KEY", None)

from providers.models import Transaction, TransactionPlayer  # noqa: E402
from web import demo_db, league_stats, ppr, undefeated as uw, waivers  # noqa: E402

SEASON = 2026
STORES = ("_LEAGUES", "_USERS", "_TEAM_WEEKS", "_LINEUPS", "_NFL_PLAYERS", "_DRAFT_PICKS",
          "_DRAFT_SCANS", "_TRADES", "_TRADE_PLAYERS", "_TRADE_SCANS", "_WAIVERS",
          "_WAIVER_SCANS", "_WEEK_PLAYERS", "_RATE_EVENTS")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for s in STORES:
        getattr(demo_db, s).clear()
    uw.clear_cache()
    league_stats.clear_cache()
    monkeypatch.setattr(ppr, "context", lambda season, week: None)
    yield


def _row(lid, week, tid, result, pts, opp, name=None, **kw):
    return {"league_id": lid, "provider": "sleeper", "platform_league_id": lid,
            "season": SEASON, "week": week, "team_id": tid,
            "team_name": name or f"Secret {lid} {tid}", "manager": f"mgr-{tid}",
            "points": pts, "opponent_points": opp, "margin": round(pts - opp, 2),
            "result": result, "optimal_points": pts + 5, "bench_left": 5.0,
            "empty_slots": 0, "team_count": 4, "scoring_type": "ppr",
            "ppr_points": pts, "ppr_coverage": 1.0, **kw}


def _league(lid, weeks, results):
    """results[team] = 'WWL...' for weeks 1..weeks. Opponents are notional;
    a W scores 120 against 100, an L 100 against 120, a T 110-110."""
    rows = []
    for tid, rec in results.items():
        for w in range(1, weeks + 1):
            r = rec[w - 1]
            pts, opp = {"W": (120.0 + w, 100.0), "L": (100.0, 120.0 + w), "T": (110.0, 110.0)}[r]
            rows.append(_row(lid, w, tid, r, pts, opp))
    return rows


def _compute(team_rows, lineups=(), dynasty=(), **kw):
    return uw.compute(team_rows=team_rows, lineup_rows=list(lineups), draft_rows=kw.get("drafts", []),
                      trade_heads=[], trade_players=[], waiver_rows=kw.get("waivers", []),
                      waiver_scans=kw.get("scans", []), played={}, nfl_players={},
                      dynasty_ids=set(dynasty), season=SEASON)


# --- who counts --------------------------------------------------------------

def test_unbeaten_means_no_losses_and_no_ties():
    rows = _league("a", 3, {"t1": "WWW", "t2": "WWT", "t3": "LLL", "t4": "WLW"})
    out = _compute(rows)
    s = out["summary"]
    assert out["through"] == 3 and s["teams"] == 4
    assert s["unbeaten"] == 1                     # the tie ends perfection
    assert s["winless"] == 1
    assert [x["alive"] for x in out["survival"]] == [3, 2, 1]
    assert out["fallen"]["n"] == 1                # t2's tie in week 3


def test_a_league_missing_a_week_drops_out_rather_than_inflating_the_count():
    full = _league("a", 3, {"t1": "WWW", "t2": "LLL", "t3": "WLW", "t4": "LWL"})
    gap = [r for r in _league("b", 3, {"u1": "WLW", "u2": "LLL", "u3": "WWL", "u4": "LWW"})
           if r["week"] != 2]
    out = _compute(full + gap)
    assert out["summary"]["leagues"] == 1 and out["summary"]["incomplete_left"] == 1
    assert out["summary"]["unbeaten"] == 1


def test_through_is_the_week_most_leagues_have_finished():
    """While Tuesday's collector is half done, the page stays on last week
    instead of shrinking to the leagues collected so far."""
    rows = []
    for i in range(10):
        rows += _league(f"L{i}", 3, {"a": "WWW", "b": "LLL", "c": "WLW", "d": "LWL"})
    rows = [r for r in rows if not (r["week"] == 3 and r["league_id"] not in ("L0",))]
    assert _compute(rows)["through"] == 2


def test_dynasty_and_tiny_leagues_are_left_out():
    rows = (_league("red", 2, {"a": "WW", "b": "LL", "c": "WL", "d": "LW"})
            + _league("dyn", 2, {"a": "WW", "b": "LL", "c": "WL", "d": "LW"})
            + _league("tiny", 2, {"a": "WW", "b": "LL", "c": "WL"}))
    out = _compute(rows, dynasty={"dyn"})
    assert out["summary"]["dynasty_left"] == 1
    assert out["summary"]["leagues"] == 1 and out["summary"]["unbeaten"] == 1


def test_an_abandoned_roster_is_not_the_winless_foil():
    rows = _league("a", 2, {"t1": "WW", "t2": "LL", "t3": "LL", "t4": "WW"})
    lu = []
    for w in (1, 2):
        for tid in ("t1", "t2", "t3", "t4"):
            for j in range(4):
                dead = tid == "t3" and j > 0             # one starter of four turned up
                lu.append({"league_id": "a", "season": SEASON, "week": w, "team_id": tid,
                           "player_key": f"p{tid}{j}", "slot": "WR", "started": True,
                           "points": 0.0 if dead else 10.0, "ppr_points": 0.0 if dead else 10.0})
    s = _compute(rows, lu)["summary"]
    assert s["winless_all"] == 2 and s["winless"] == 1 and s["abandoned"] == 1


def test_all_play_tells_the_real_thing_from_a_pretender():
    # t1 wins every week while scoring least: a pretender.
    rows = []
    for w in (1, 2):
        rows += [_row("a", w, "t1", "W", 90.0, 80.0), _row("a", w, "t2", "L", 80.0, 90.0),
                 _row("a", w, "t3", "W", 150.0, 140.0), _row("a", w, "t4", "L", 140.0, 150.0)]
    rows = [r for r in rows if not (r["team_id"] == "t3" and r["week"] == 2)] + \
        [_row("a", 2, "t3", "L", 150.0, 151.0)]
    out = _compute(rows)
    assert out["summary"]["unbeaten"] == 1
    assert out["verdicts"]["pretender"] == 1
    assert out["cards"][0]["allplay"] == pytest.approx(1 / 3, abs=1e-4)


# --- the words ----------------------------------------------------------------

@pytest.mark.parametrize("n,head", [
    (0, "Nobody’s Perfect Anymore."),
    (1, "One Team Left in America."),
    (9, "The Final Nine."),
    (41, "Forty-One Left."),
    (116, "116 Teams Haven’t Lost Yet."),
])
def test_the_headline_counts_down(n, head):
    raw = {"through": 8, "season": SEASON,
           "summary": {"teams": 3000, "unbeaten": n, "extinct": 7 if n == 0 else None},
           "survival": [{"week": 8, "alive": n, "fell": 4, "close": 1}],
           "cards": [{"league_id": "x", "team_id": "y", "w": 7, "l": 1, "t": 0}] if n == 0 else []}
    assert uw.build(raw)["headline"] == head


def test_the_regular_season_closes_the_book():
    raw = {"through": 14, "summary": {"teams": 3000, "unbeaten": 3},
           "survival": [{"week": 14, "alive": 3, "fell": 1, "close": 0}]}
    assert uw.build(raw)["headline"] == "Three Teams Went 14-0."


def test_a_coin_is_the_honest_comparison():
    raw = {"through": 4, "summary": {"teams": 1600, "unbeaten": 300},
           "survival": [{"week": 4, "alive": 300, "fell": 40, "close": 3}]}
    u = uw.build(raw)
    assert u["coin_txt"] == "100" and u["over_txt"] == "3.0x"
    assert "A coin would have left 100." in u["deck"]


def test_the_public_never_sees_a_team_name():
    card = {"league_id": "a", "team_id": "t1", "team_name": "Secret Name",
            "manager": "secret_mgr", "provider": "espn", "team_count": 12,
            "scoring_type": "ppr", "w": 8, "l": 0, "t": 0, "allplay": 0.8, "pf_rank": 1}
    raw = {"through": 8, "summary": {"teams": 3000, "unbeaten": 1},
           "survival": [{"week": 8, "alive": 1, "fell": 2, "close": 0}], "cards": [card],
           "fallen": {"cruel": [card], "nearest": [card]}, "pretenders": [card]}
    public = repr(uw.build(raw))
    assert "Secret Name" not in public and "secret_mgr" not in public
    assert "A 12-team PPR league on ESPN" in public
    assert "Secret Name" in repr(uw.build(raw, staff=True))


def test_lessons_say_which_way_it_cuts():
    tape = {"u": {"n": 50, "bench": 20.0, "adds": 4.0, "ppg": 140.0},
            "w": {"n": 40, "bench": 20.4, "adds": 9.0, "ppg": 95.0},
            "a": {"n": 900, "ppg": 115.0}}
    heads = [l["head"] for l in uw.lessons({"tape": tape})]
    assert "They leave the wire alone." in heads
    assert "Lineups aren’t the difference." in heads


# --- the share card ------------------------------------------------------------

def _seeded(monkeypatch):
    """The demo's season, offline. monkeypatch, not an assignment: a stub
    left on the module broke test_around's own check of sleeper_keys."""
    from scripts import demo_around
    monkeypatch.setattr(demo_around, "sleeper_keys", lambda: {})
    import random
    demo_around.seed(40, 6, SEASON, random.Random(7))
    return uw.build(demo_db.undefeated(SEASON))


def test_the_card_draws_in_every_size(monkeypatch):
    from web import undefeated_card
    u = _seeded(monkeypatch)
    for size, dims in undefeated_card.SIZES.items():
        png = undefeated_card.render(u, size=size)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        from PIL import Image
        import io
        assert Image.open(io.BytesIO(png)).size == dims


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_card_and_the_page_light_the_same_seats():
    """The share card and the room on the page are the same picture: the
    same shuffle, in Python and in the page's script."""
    from web import undefeated_card
    src = (Path(__file__).resolve().parent.parent / "web" / "templates" / "undefeated.html").read_text()
    assert "seed = (seed * 16807) % 2147483647" in src and "var order = [], seed = 20261029;" in src
    js = ("var N=1000,order=[],seed=20261029;for(var i=0;i<N;i++)order.push(i);"
          "function rnd(){seed=(seed*16807)%2147483647;return seed/2147483647;}"
          "for(var j=N-1;j>0;j--){var k=Math.floor(rnd()*(j+1));var t=order[j];order[j]=order[k];order[k]=t;}"
          "console.log(JSON.stringify(order.slice(N-37).sort(function(a,b){return a-b;})));")
    got = subprocess.run(["node", "-e", js], capture_output=True, text=True).stdout
    assert sorted(undefeated_card.seats(37)) == __import__("json").loads(got)


# --- the page -------------------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    from fastapi.testclient import TestClient
    from web import app as webapp
    monkeypatch.setattr(webapp, "db", demo_db)
    return TestClient(webapp.app)


def _staff(client):
    client.post("/signup", data={"email": "s@example.com", "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"})
    user = demo_db.user_by_email("s@example.com")
    demo_db.update_user(user["id"], {"plan": "staff"})


def test_the_watch_is_staff_only_until_the_switch(web, monkeypatch):
    _seeded(monkeypatch)
    monkeypatch.delenv("UNDEFEATED_PUBLIC", raising=False)
    assert web.get("/undefeated").status_code == 404
    assert web.get("/undefeated/card.png").status_code == 404
    monkeypatch.setenv("UNDEFEATED_PUBLIC", "1")
    r = web.get("/undefeated")
    assert r.status_code == 200
    for words in ("The countdown", "The tale of the tape", "It starts on draft day",
                  "The common thread", "Real, or just lucky?", "redraft teams"):
        assert words in r.text, words
    # Invented team names never reach a public page.
    for row in list(demo_db._TEAM_WEEKS.values())[:400]:
        assert f">{row['team_name']}<" not in r.text
    assert 'class="ar-staff"' not in r.text
    assert web.get("/undefeated/card.png?size=og").headers["content-type"] == "image/png"


def test_staff_see_names_and_the_wire_note(web, monkeypatch):
    _seeded(monkeypatch)
    monkeypatch.delenv("UNDEFEATED_PUBLIC", raising=False)
    _staff(web)
    r = web.get("/undefeated")
    assert r.status_code == 200 and 'class="ar-staff"' in r.text
    assert "The wire, collected" in r.text
    assert 'class="ar-staff"' not in web.get("/undefeated?public=1").text


def test_it_is_in_the_menu_and_the_sitemap(web, monkeypatch):
    monkeypatch.setenv("AROUND_PUBLIC", "1")
    monkeypatch.setenv("LUCK_PUBLIC", "1")
    monkeypatch.setenv("UNDEFEATED_PUBLIC", "1")
    assert 'href="/undefeated"' in web.get("/").text
    assert "/undefeated<" in web.get("/sitemap.xml").text
    from web import analytics
    assert analytics.beacon_allowed("/undefeated") if hasattr(analytics, "beacon_allowed") else True


def test_your_own_team_is_named_to_you():
    lg = demo_db.create_league(provider="sleeper", platform_league_id="m", league_name="Mine",
                               paper_name="The Mine Times", commissioner_name="", season=SEASON,
                               public_slug="mine-x", admin_token="m" * 20)
    demo_db.upsert_team_weeks([_row(lg["id"], 1, "t1", "W", 120, 100, name="Kevlarville Kings"),
                               _row(lg["id"], 2, "t1", "L", 99.5, 101.0, name="Kevlarville Kings")])

    class DB:
        def market_leagues_for_user(self, uid):
            return [dict(lg, my_team_id="t1")]

        def team_weeks_season_for_leagues(self, season, ids):
            return demo_db.team_weeks_season_for_leagues(season, ids)

    me = uw.my_team(DB(), {"id": "u"}, SEASON, 2)
    assert me["name"] == "Kevlarville Kings" and not me["alive"]
    assert me["fell_week"] == 2 and me["record"] == "1-1"
    assert uw.my_team(DB(), {"id": "u"}, SEASON, 1)["alive"]
    assert uw.my_team(DB(), None, SEASON, 2) is None


# --- the wire (067) ----------------------------------------------------------------

def _p(pid, name, pos="RB", team="DAL"):
    return TransactionPlayer(player_id=pid, name=name, position=pos, nfl_team=team)


def _claim(**kw):
    base = dict(kind="waiver", status="complete", week=3, teams=["Team A"],
                adds=[("Team A", _p("9001", "Rico Dowdle"))],
                drops=[("Team A", _p("9002", "A Kicker", "K"))], bid=27,
                created=1_760_000_000_000)
    base.update(kw)
    return Transaction(**base)


def _lg():
    return demo_db.create_league(provider="sleeper", platform_league_id="w", league_name="W",
                                 paper_name="The W Times", commissioner_name="", season=SEASON,
                                 public_slug="w-x", admin_token="w" * 20)


def test_a_claim_becomes_an_add_and_a_drop_with_its_bid():
    rows = waivers.rows(_lg(), [_claim()], SEASON, 3)
    assert {(r["action"], r["player_key"]) for r in rows} == {("add", "9001"), ("drop", "9002")}
    add = next(r for r in rows if r["action"] == "add")
    assert add["bid"] == 27 and add["status"] == "complete" and add["team_name"] == "Team A"
    assert next(r for r in rows if r["action"] == "drop")["bid"] is None
    assert len({r["move_key"] for r in rows}) == 1 and rows[0]["move_key"].startswith("w")


def test_a_lost_claim_is_kept_and_a_trade_is_not():
    trade = Transaction(kind="trade", status="complete", week=3, teams=["A", "B"],
                        adds=[("A", _p("1", "X"))], drops=[("B", _p("1", "X"))])
    rows = waivers.rows(_lg(), [_claim(status="failed", drops=[]), trade,
                                Transaction(kind="commissioner", status="complete", week=3)],
                        SEASON, 3)
    assert [(r["action"], r["status"]) for r in rows] == [("add", "failed")]


def test_the_same_claim_twice_is_one_row_and_keys_are_stable():
    a = waivers.rows(_lg(), [_claim(), _claim()], SEASON, 3)
    b = waivers.rows(_lg(), [_claim()], SEASON, 3)
    assert len(a) == 2 and [r["move_key"] for r in a] == [r["move_key"] for r in b]


def test_one_fetch_fills_both_trades_and_the_wire(monkeypatch):
    lg = _lg()
    calls = []
    monkeypatch.setattr("providers.load_transactions",
                        lambda *a, **k: calls.append(a) or [_claim()])
    got = waivers.collect_league(demo_db, lg, SEASON, 3)
    assert got == (0, 1) and len(calls) == 1
    assert demo_db.waiver_scan_league_ids(SEASON, 3) == {lg["id"]}
    assert demo_db.trade_scan_league_ids(SEASON, 3) == {lg["id"]}
    vol = demo_db.waiver_volume(SEASON)
    assert vol["weeks"] == [{"week": 3, "leagues": 1, "adds": 1, "failed": 0, "faab": 27}]


def test_the_collector_goes_back_for_the_wire_of_weeks_it_already_has(monkeypatch):
    """The backfill: a week stored, its trades scanned, before 067. The next
    run asks only for the wire, and the run after that skips it."""
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    monkeypatch.setattr(league_stats, "week_is_final", lambda *a, **k: True)
    lg = _lg()
    demo_db.upsert_team_weeks([_row(lg["id"], 3, "t1", "W", 100.0, 90.0)])
    demo_db.upsert_lineups([{"league_id": lg["id"], "season": SEASON, "week": 3, "team_id": "t1",
                             "player_key": "9001", "slot": "RB", "started": True,
                             "points": 20.0, "ppr_points": 20.0}])
    demo_db.upsert_trade_scan({"league_id": lg["id"], "season": SEASON, "week": 3, "trades": 0})
    demo_db.upsert_draft_scan({"league_id": lg["id"], "season": SEASON, "picks": 0})

    def boom(*a, **k):
        raise AssertionError("the week was already stored; don't fetch it again")
    monkeypatch.setattr("providers.load_week", boom)
    monkeypatch.setattr("providers.load_transactions", lambda *a, **k: [_claim()])
    rep = league_stats.collect_week(demo_db, 3, season=SEASON, sleep=lambda s: None,
                                    log=lambda s: None)
    assert rep["waiver_scanned"] == 1 and rep["waiver_moves"] == 1
    assert rep["scanned"] == 0                      # trades were already done
    again = league_stats.collect_week(demo_db, 3, season=SEASON, sleep=lambda s: None,
                                      log=lambda s: None)
    assert again["waiver_scanned"] == 0 and again["skipped_done"] == 1


def test_the_live_poll_keeps_claims_without_marking_the_week_done(monkeypatch):
    from web import live_trades
    lg = _lg()
    monkeypatch.setattr(demo_db, "sleeper_leagues", lambda season: [lg])
    monkeypatch.setattr("providers.load_transactions", lambda *a, **k: [_claim()])
    monkeypatch.setattr(live_trades, "PACE", 0)
    rep = live_trades.poll(demo_db, SEASON, 3, log=lambda *a: None)
    assert rep["waiver_moves"] == 1
    assert len(demo_db._WAIVERS) == 2 and not demo_db.waiver_scan_league_ids(SEASON, 3)


def test_the_wire_summary_says_what_is_missing():
    vol = {"weeks": [{"week": 1, "leagues": 900, "adds": 5}, {"week": 3, "leagues": 880, "adds": 7}]}
    s = waivers.summary(vol, 3)
    assert s["missing"] == [2] and not s["complete"] and s["adds"] == 12


def test_the_menu_opens_without_the_luck_index(web, monkeypatch):
    """30 Oct: with the desk and the Watch open but not the Luck Index, the
    menu collapsed to a plain Around the Leagues link and the Watch was
    nowhere in it."""
    monkeypatch.setenv("AROUND_PUBLIC", "1")
    monkeypatch.delenv("LUCK_PUBLIC", raising=False)
    monkeypatch.setenv("UNDEFEATED_PUBLIC", "1")
    page = web.get("/").text
    assert 'href="/undefeated"' in page and 'href="/luck"' not in page
    monkeypatch.delenv("UNDEFEATED_PUBLIC", raising=False)
    page = web.get("/").text
    assert 'href="/undefeated"' not in page and 'nav-drop-menu' in page  # the market's menu
