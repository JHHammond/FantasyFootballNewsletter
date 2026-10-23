"""
Around the Leagues: rows, the collector, the leaderboards, the staff page.
Run with: python -m pytest tests/test_league_stats.py -q
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.pop("RESEND_API_KEY", None)

import providers  # noqa: E402
from providers import ProviderError, apply_lineup_gaps  # noqa: E402
from providers.cache import TTLCache  # noqa: E402
from providers.yahoo import YahooProvider  # noqa: E402
from tests import fixtures_yahoo as fx  # noqa: E402
from web import demo_db, league_stats  # noqa: E402

SEASON = 2026


@pytest.fixture(autouse=True)
def clean():
    for store in (demo_db._LEAGUES, demo_db._USERS, demo_db._TEAM_WEEKS, demo_db._LINEUPS, demo_db._NFL_PLAYERS,
                  demo_db._RATE_EVENTS):
        store.clear()
    league_stats.clear_cache()
    from web import around
    around.clear_cache()
    yield


def _week(monkeypatch, tmp_path, week=3):
    def fake_get(self, path, league_key=None):
        return fx.route(path)
    monkeypatch.setattr(YahooProvider, "_get", fake_get)
    a = YahooProvider(cache=TTLCache(tmp_path, "yahoo"), access_token="t")
    return apply_lineup_gaps(a.get_week(fx.LEAGUE_KEY, SEASON, week))


def _league(name="Dirty South Dynasty", provider="yahoo", pid=fx.LEAGUE_KEY,
            season=SEASON):
    row = demo_db.create_league(provider=provider, platform_league_id=pid,
                                league_name=name, paper_name=f"The {name} Times",
                                commissioner_name="", season=season,
                                public_slug=f"{name.lower().replace(' ', '-')}-x",
                                admin_token=("t" * 19) + name[0])
    return row


# --- rows -------------------------------------------------------------------

def test_one_row_per_team_with_results(monkeypatch, tmp_path):
    rows = league_stats.rows_from_week(_league(), _week(monkeypatch, tmp_path))
    assert len(rows) == 4
    by = {r["team_name"]: r for r in rows}
    hank = by["Hank's Heroes"]
    assert hank["points"] == 61.60 and hank["result"] == "W"
    assert hank["opponent_name"] == "The Mid Tier"
    assert hank["margin"] == 16.50
    assert hank["top_player"] == "Joe Burrow" and hank["top_player_points"] == 19.0
    assert hank["best_bench_player"] == "Jaylen Warren"   # not the IR player
    assert hank["bench_left"] == pytest.approx(19.90)
    assert by["The Mid Tier"]["result"] == "L" and by["The Mid Tier"]["margin"] == -16.50
    assert by["Kevlarville"]["result"] == "T"
    assert all(r["week"] == 3 and r["season"] == SEASON for r in rows)


def test_a_paper_for_an_unfinished_week_saves_nothing(monkeypatch, tmp_path):
    wd = _week(monkeypatch, tmp_path)
    monkeypatch.setattr(league_stats, "week_is_final", lambda s, w, now=None: False)
    league_stats.save_from_paper(demo_db, _league(), wd)
    assert demo_db._TEAM_WEEKS == {}
    monkeypatch.setattr(league_stats, "week_is_final", lambda s, w, now=None: True)
    league_stats.save_from_paper(demo_db, _league(name="Other"), wd)
    assert len(demo_db._TEAM_WEEKS) == 4


def test_week_final_is_the_tuesday_after():
    # Week 2 of 2026 ends Monday 21 Sep; final at midnight Eastern going
    # into Tuesday 22 Sep, which is 04:00 UTC in September (13 Oct).
    assert not league_stats.week_is_final(2026, 2, datetime(2026, 9, 22, 3, 59, tzinfo=timezone.utc))
    assert league_stats.week_is_final(2026, 2, datetime(2026, 9, 22, 4, tzinfo=timezone.utc))


# --- the collector ----------------------------------------------------------

@pytest.fixture
def fake_load(monkeypatch, tmp_path):
    wd = _week(monkeypatch, tmp_path)
    calls = []

    def load_week(provider, pid, season, week, **kw):
        calls.append(pid)
        if pid == "broken":
            raise ProviderError("private league")
        if pid == "explodes":
            raise RuntimeError("bad payload")
        return wd

    monkeypatch.setattr(providers, "load_week", load_week)
    # Trades (045) are fetched in the same loop. Nothing in this file is about
    # them, and no test may touch the network: give every league none.
    monkeypatch.setattr(providers, "load_transactions", lambda *a, **k: [])
    monkeypatch.setattr(league_stats, "week_is_final", lambda s, w, now=None: True)
    return calls


def test_collects_every_league_and_keeps_going(fake_load):
    _league("A", pid="a")
    _league("B", pid="broken")
    _league("C", pid="explodes")
    _league("D", pid="d")
    _league("Old", pid="old", season=2025)          # last season: ignored
    log = []
    report = league_stats.collect_week(demo_db, 3, season=SEASON,
                                       sleep=lambda s: None, log=log.append)
    assert report["leagues"] == 4
    assert report["collected"] == 2 and report["rows"] == 8
    assert report["no_data"] == 1 and report["errors"] == 1
    assert "old" not in fake_load


def test_a_rerun_skips_what_it_already_has(fake_load):
    _league("A", pid="a")
    league_stats.collect_week(demo_db, 3, season=SEASON, sleep=lambda s: None,
                              log=lambda s: None)
    fake_load.clear()
    report = league_stats.collect_week(demo_db, 3, season=SEASON,
                                       sleep=lambda s: None, log=lambda s: None)
    assert report["skipped_done"] == 1 and fake_load == []


def test_the_same_real_league_twice_is_counted_once(fake_load):
    _league("A", pid="same")
    _league("B", pid="same")
    report = league_stats.collect_week(demo_db, 3, season=SEASON,
                                       sleep=lambda s: None, log=lambda s: None)
    assert report["leagues"] == 1 and fake_load == ["same"]


def test_an_unfinished_week_is_refused(monkeypatch):
    monkeypatch.setattr(league_stats, "week_is_final", lambda s, w, now=None: False)
    _league("A", pid="a")
    report = league_stats.collect_week(demo_db, 3, season=SEASON,
                                       sleep=lambda s: None, log=lambda s: None)
    assert report.get("not_final") and demo_db._TEAM_WEEKS == {}


def test_it_paces_itself(fake_load):
    """Three calls to a platform for one league — the week, its draft and
    its trades — so three waits. The pace is per REQUEST, not per league,
    which is the whole point: adding the draft collection to this job added
    a wait rather than quietly raising the request rate at Yahoo."""
    _league("A", pid="a")
    slept = []
    league_stats.collect_week(demo_db, 3, season=SEASON, sleep=slept.append,
                              log=lambda s: None)
    assert slept == [league_stats.PACE["yahoo"]] * 3


# --- leaderboards -----------------------------------------------------------

def _row(league, team, points, result="W", opp=50.0, **kw):
    base = {"league_id": league["id"], "provider": "sleeper",
            "platform_league_id": "x", "season": SEASON, "week": 3,
            "team_id": team, "team_name": team, "manager": "", "points": points,
            "opponent_name": "Opp", "opponent_points": opp,
            "margin": round(points - opp, 2) if result != "BYE" else None,
            "result": result, "optimal_points": None, "bench_left": 0.0,
            "empty_slots": 0, "top_player": "P", "top_player_pos": "QB",
            "top_player_points": 10.0, "best_bench_player": None,
            "best_bench_points": None, "team_count": 10, "scoring_type": "ppr"}
    base.update(kw)
    return base


def test_boards_skip_byes_and_abandoned_teams():
    lg = _league("A", pid="a")
    demo_db.upsert_team_weeks([
        _row(lg, "ghost", 0.0, result="L"),            # abandoned: never "lowest"
        _row(lg, "bye", 12.0, result="BYE"),
        _row(lg, "sad", 41.2, result="L", opp=120.0),
        _row(lg, "big", 180.5, result="W", opp=60.0),
        _row(lg, "close", 101.0, result="W", opp=100.9),
    ])
    boards = {b["key"]: b["rows"] for b in
              league_stats.leaderboards(demo_db, SEASON, 3)["boards"]}
    assert [r["team_name"] for r in boards["lowest"]][:1] == ["sad"]
    assert "ghost" not in [r["team_name"] for r in boards["lowest"]]
    assert "bye" not in [r["team_name"] for r in boards["lowest"]]
    assert boards["blowouts"][0]["team_name"] == "big"
    assert boards["closest"][0]["team_name"] == "close"
    assert all(r["result"] == "W" for r in boards["blowouts"])
    assert boards["lowest"][0]["leagues"]["league_name"] == "A"


def test_summary():
    lg = _league("A", pid="a")
    demo_db.upsert_team_weeks([_row(lg, "a", 100.0), _row(lg, "b", 50.0),
                               _row(lg, "c", 0.0)])
    s = league_stats.leaderboards(demo_db, SEASON, 3)["summary"]
    assert s["teams"] == 2 and s["leagues"] == 1 and s["avg_points"] == 75.0


# --- the staff page ---------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    from fastapi.testclient import TestClient
    from web import app as webapp
    monkeypatch.setattr(webapp, "db", demo_db)
    return TestClient(webapp.app)


def _sign_up(client, plan):
    client.post("/signup", data={"email": "s@example.com",
                                 "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"})
    user = demo_db.user_by_email("s@example.com")
    demo_db.update_user(user["id"], {"plan": plan})


def test_staff_only(web):
    _sign_up(web, "paid")
    assert web.get("/staff/around").status_code == 404
    assert web.get("/around").status_code == 404          # until AROUND_PUBLIC=1
    assert web.post("/staff/around/collect", data={"weeks": "1-2"}).status_code == 404


def test_staff_page_renders_boards(web):
    _sign_up(web, "staff")
    lg = _league("A", pid="a")
    demo_db.upsert_team_weeks([_row(lg, "Sad Sacks", 41.2, result="L", opp=120.0)])
    r = web.get("/staff/around?week=3")
    assert r.status_code == 200
    assert "Halls of fame and shame" in r.text and "Sad Sacks" in r.text
    assert "Collect weeks 1" in r.text


def test_backfill_button_starts_a_background_run(web, monkeypatch):
    _sign_up(web, "staff")
    started = []
    monkeypatch.setattr(league_stats, "start_background",
                        lambda db, weeks: started.append(weeks) or True)
    r = web.post("/staff/around/collect", data={"weeks": "1-2"},
                 follow_redirects=False)
    assert r.status_code == 303 and started == [[1, 2]]


def test_the_homepage_wire_uses_numbers_never_team_names(web):
    from web import app as webapp
    lg = _league("Secret League Name", pid="a")
    demo_db.upsert_team_weeks([
        _row(lg, "Private Team Name", 276.4, result="W", opp=100.0,
             top_player="Ja'Marr Chase", top_player_points=41.3),
        _row(lg, "Other Private Team", 100.06, result="W", opp=100.0),
    ])
    wire = webapp.homepage_wire(SEASON, 3)
    text = " ".join(wire["lines"])
    assert "276.4" in text and "0.06" in text and "Ja'Marr Chase" in text
    assert "Private Team" not in text and "Secret League" not in text
    page = web.get("/").text
    assert "Around the leagues" in page and "Private Team" not in page


def test_the_homepage_wire_falls_back_when_nothing_is_collected(web):
    page = web.get("/").text
    assert "Bench decisions, graded in public" in page


def test_leagues_in_print_counts_every_league_not_just_collected_ones(web):
    """It showed 137 on 25 Sep: the leagues with week 2 stats collected, while
    1,611 had signed up. The figure is the signup count now."""
    for i in range(5):
        _league(f"L{i}", pid=f"p{i}")
    lg = demo_db._LEAGUES[next(iter(demo_db._LEAGUES))]
    demo_db.upsert_team_weeks([_row(lg, "t", 100.0)])
    from web import app as webapp
    webapp._LEAGUE_COUNT.update(at=0.0, n=0)
    page = web.get("/").text
    assert "<strong>5</strong><span>leagues in print</span>" in page


def test_the_homepage_ticker_waits_for_the_new_week_to_fill_in(monkeypatch):
    """29 Sep: 327 early rows of week 3 replaced 18,000 of week 2 at 7am."""
    from web import app as webapp
    monkeypatch.setattr(webapp, "db", demo_db)
    counts = {3: 327, 2: 18000}
    monkeypatch.setattr(league_stats, "leaderboards", lambda db, s, w, limit=10: {
        "boards": [], "summary": {"teams": counts.get(w, 0), "leagues": 1}})
    assert webapp.homepage_wire(SEASON, 3)["week"] == 2
    counts[3] = 17000
    assert webapp.homepage_wire(SEASON, 3)["week"] == 3


def test_the_homepage_wire_is_standardized_not_a_custom_leagues_numbers(web):
    """3 Oct: a league scoring 6 a catch put a 356-point player game on the
    homepage. Team scores use PPR, and the best game is the best PPR game."""
    from web import app as webapp
    normal = _league("Normal", pid="n")
    wild = _league("Wild", pid="w")
    demo_db.upsert_team_weeks([
        dict(_row(normal, "A", 150.0, result="W", opp=120.0), ppr_points=148.0),
        dict(_row(normal, "B", 120.0, result="L", opp=150.0), ppr_points=119.0),
        dict(_row(wild, "C", 1052.0, result="W", opp=900.0,
                  top_player="Jahmyr Gibbs", top_player_points=356.0), ppr_points=160.0),
        dict(_row(wild, "D", 900.0, result="L", opp=1052.0), ppr_points=110.0),
    ])
    demo_db.upsert_lineups([
        {"league_id": wild["id"], "season": SEASON, "week": 3, "team_id": "C",
         "player_key": "gibbs", "slot": "RB", "started": True,
         "points": 356.0, "ppr_points": 41.4},
    ])
    demo_db.upsert_nfl_players([{"player_key": "gibbs", "name": "Jahmyr Gibbs"}])
    text = " ".join(webapp.homepage_wire(SEASON, 3)["lines"])
    assert "356" not in text and "1052" not in text
    assert "High score in PPR: 160.0" in text
    assert "Best game in PPR: Jahmyr Gibbs, 41.4" in text


def test_a_week_stored_without_ppr_is_collected_again_once_ppr_is_back(fake_load, monkeypatch):
    """3 Oct: a PPR league never ranked. Its week had been saved while
    Sleeper's stats were unreachable (lineups, no PPR), which counted as
    done, so no later run ever filled the PPR in."""
    from web import ppr
    lg = _league("A", pid="a")
    monkeypatch.setattr(ppr, "context", lambda s, w: None)
    league_stats.collect_week(demo_db, 3, season=SEASON, sleep=lambda s: None,
                              log=lambda s: None)
    assert all(r.get("ppr_points") is None for r in demo_db._TEAM_WEEKS.values())

    class Ctx:
        def team_score(self, team):
            return 100.0, 1.0
        def resolve(self, p):
            return None
        def ppr(self, sid):
            return None
        def name(self, sid):
            return None
    monkeypatch.setattr(ppr, "context", lambda s, w: Ctx())
    fake_load.clear()
    report = league_stats.collect_week(demo_db, 3, season=SEASON,
                                       sleep=lambda s: None, log=lambda s: None)
    assert fake_load == ["a"] and report["collected"] == 1
    assert all(r["ppr_points"] == 100.0 for r in demo_db._TEAM_WEEKS.values()
               if r["league_id"] == lg["id"])
    # and now it's done
    fake_load.clear()
    league_stats.collect_week(demo_db, 3, season=SEASON, sleep=lambda s: None,
                              log=lambda s: None)
    assert fake_load == []


def test_a_walkover_is_not_a_lucky_win():
    """John, 22 Oct: "for worst team that won, i think we need to add
    something where their opponent has to have scored points."

    "Worst score that won" is meant to be a team that put up 71 and
    somehow survived. Without this it finds teams that put up 71 against
    somebody who put up NOTHING — an abandoned roster, a league that
    stopped playing in September. Beating a corpse is not luck, and it
    crowds out the real story every time, because nothing beats nothing.
    """
    lg = _league("Walkovers", pid="wk")
    rows = [
        # The genuine article: a bad score that survived a worse one.
        _row(lg, "squeaker", 71.0, "W", opp=70.0),
        _row(lg, "loser", 70.0, "L", opp=71.0),
        # A walkover. Lower score, so it would take the top spot.
        _row(lg, "walkover", 55.0, "W", opp=0.0),
        _row(lg, "ghost", 0.0, "L", opp=55.0),
    ] + [_row(lg, f"f{i}", 95.0 + i, "W", opp=90.0) for i in range(6)]
    got = league_stats.compute_week(rows, [], {}, SEASON, 3)
    lucky = [c["team_name"] for c in got["lucky"]]
    assert "walkover" not in lucky, lucky
    assert lucky[0] == "squeaker", lucky


def test_a_forfeit_is_not_a_blowout():
    """The same artifact wearing a different hat. "The biggest beating in
    America" against a team that never set a lineup is a forfeit with a
    scoreline."""
    lg = _league("Forfeits", pid="ff")
    rows = [
        _row(lg, "forfeit", 150.0, "W", opp=0.0),
        _row(lg, "absent", 0.0, "L", opp=150.0),
        _row(lg, "mauler", 180.0, "W", opp=60.0),
        _row(lg, "mauled", 60.0, "L", opp=180.0),
    ] + [_row(lg, f"f{i}", 95.0 + i, "W", opp=90.0) for i in range(6)]
    got = league_stats.compute_week(rows, [], {}, SEASON, 3)
    blowouts = [c["team_name"] for c in got["halls"]["blowouts"]]
    assert "forfeit" not in blowouts, blowouts
    assert blowouts[0] == "mauler", blowouts


def _lineup(lg, team, scored, blank, week=3):
    """Starters, some of whom were playing and some of whom were not."""
    out = []
    for i in range(scored):
        out.append({"league_id": lg["id"], "season": SEASON, "week": week,
                    "team_id": team, "player_key": f"ok{i}", "slot": "WR",
                    "started": True, "points": 8.0, "ppr_points": 8.0})
    for i in range(blank):
        out.append({"league_id": lg["id"], "season": SEASON, "week": week,
                    "team_id": team, "player_key": f"out{i}", "slot": "WR",
                    "started": True, "points": 0.0, "ppr_points": 0.0})
    return out


def test_an_abandoned_roster_is_not_a_low_score():
    """John, 22 Oct: "these numbers are bad. It doesnt provide any info to
    know that someone basically threw a game."

    The five lowest scores in America were 2.7, 4.4, 4.8, 5.0 and 6.9.
    None of those is a bad week — a fantasy team that turns up scores
    sixty. They are abandoned rosters, and they win this hall every week
    forever, because nobody can out-lose a team that is not there.
    """
    lg = _league("Corpses", pid="cp")
    rows, lu = [], []
    # A genuinely dreadful week, played out by a full lineup.
    rows.append(_row(lg, "tried", 41.0, "L", opp=90.0))
    lu += _lineup(lg, "tried", scored=8, blank=1)
    # An abandoned one: one man playing, eight not.
    rows.append(_row(lg, "gone", 8.0, "L", opp=90.0))
    lu += _lineup(lg, "gone", scored=1, blank=8)
    for i in range(8):
        rows.append(_row(lg, f"f{i}", 95.0 + i, "W", opp=90.0))
        lu += _lineup(lg, f"f{i}", scored=9, blank=0)

    got = league_stats.compute_week(rows, lu, {}, SEASON, 3)
    lowest = [c["team_name"] for c in got["halls"]["lowest"]]
    assert "gone" not in lowest, lowest
    assert lowest[0] == "tried", lowest
    # ...and the row carries the thing that was missing: proof it was a
    # real lineup.
    card = got["halls"]["lowest"][0]
    assert (card["played"], card["starters"]) == (8, 9)


def test_an_abandoned_roster_cannot_be_the_luckiest_win_either():
    """Same guard on Worst that won. A corpse that beat another corpse is
    not the luckiest team in America."""
    lg = _league("Corpses2", pid="cp2")
    rows, lu = [], []
    rows.append(_row(lg, "zombie", 9.0, "W", opp=6.0))
    lu += _lineup(lg, "zombie", scored=1, blank=8)
    rows.append(_row(lg, "squeaker", 68.0, "W", opp=67.0))
    lu += _lineup(lg, "squeaker", scored=9, blank=0)
    for i in range(8):
        rows.append(_row(lg, f"g{i}", 95.0 + i, "W", opp=90.0))
        lu += _lineup(lg, f"g{i}", scored=9, blank=0)

    got = league_stats.compute_week(rows, lu, {}, SEASON, 3)
    lucky = [c["team_name"] for c in got["lucky"]]
    assert "zombie" not in lucky, lucky
    assert lucky[0] == "squeaker", lucky


def test_a_team_with_no_stored_lineup_is_not_assumed_dead():
    """We cannot tell, and dropping what we cannot tell would empty the
    hall for any week collected before lineups were stored."""
    lg = _league("NoLineups", pid="nl")
    rows = [_row(lg, "quiet", 44.0, "L", opp=90.0)]
    rows += [_row(lg, f"h{i}", 95.0 + i, "W", opp=90.0) for i in range(8)]
    got = league_stats.compute_week(rows, [], {}, SEASON, 3)
    assert got["halls"]["lowest"][0]["team_name"] == "quiet"
    assert got["halls"]["lowest"][0]["played"] is None


def test_an_abandoned_roster_is_not_a_bench_disaster():
    """Live, 22 Oct: "scored 2.7 with C.J. Stroud on the bench, 1 of 4
    starters scored" led the front page as the bench disaster. That is a
    roster nobody opened, not a lineup decision. Same rule as Lowest."""
    lg = _league("Benchers", pid="bn")
    rows, lu = [], []
    rows.append(_row(lg, "gone", 2.7, "L", opp=90.0, bench_left=163.7))
    lu += _lineup(lg, "gone", scored=1, blank=3)
    rows.append(_row(lg, "chose_badly", 88.0, "L", opp=90.0, bench_left=61.0))
    lu += _lineup(lg, "chose_badly", scored=9, blank=0)
    for i in range(8):
        rows.append(_row(lg, f"b{i}", 95.0 + i, "W", opp=90.0, bench_left=5.0))
        lu += _lineup(lg, f"b{i}", scored=9, blank=0)

    got = league_stats.compute_week(rows, lu, {}, SEASON, 3)
    bench = [c["team_name"] for c in got["halls"]["bench"]]
    assert "gone" not in bench, bench
    assert bench[0] == "chose_badly", bench


def test_a_player_who_did_not_play_has_no_win_rate():
    """Live, 22 Oct: Worst win rate was Hurts, Lamar, Saquon and
    Montgomery, every one at 0.0 — players on a bye, started by managers
    who did not look. A zero cannot tell a bad game from no game, so it
    is left off both win-rate boards. A negative score was a game."""
    lg = _league("WinRates", pid="wr")
    rows, lu = [], []

    def start(team, key, pts, result):
        rows.append(_row(lg, team, 80.0, result, opp=90.0 if result == "L" else 70.0))
        lu.append({"league_id": lg["id"], "season": SEASON, "week": 3,
                   "team_id": team, "player_key": key, "slot": "QB",
                   "started": True, "points": pts, "ppr_points": pts})

    for i in range(4):
        start(f"dnp{i}", "on_a_bye", 0.0, "L")      # never played: 0% won
        start(f"neg{i}", "fumbled", -1.5, "L")      # played, badly: 0% won
        start(f"dud{i}", "dud", 3.0, "W" if i == 0 else "L")
    got = league_stats.compute_week(rows, lu, {}, SEASON, 3, min_starts=3)

    worst = [c["player_key"] for c in got["players"]["win_worst"]]
    best = [c["player_key"] for c in got["players"]["win_best"]]
    assert "on_a_bye" not in worst and "on_a_bye" not in best, (worst, best)
    assert worst[0] == "fumbled", worst
    assert "dud" in worst, worst
    # Still in the full list: he was rostered, and searchable.
    assert "on_a_bye" in [c["player_key"] for c in got["players"]["all"]]


def test_a_player_who_played_and_scored_nothing_is_still_on_the_board():
    """John, 23 Oct: Worst win rate had "cycled out people who played but
    actually scored 0 points (Cam Skattebo)". 054 dropped every exact zero
    to keep byes off the board. Snaps (055) tell the two apart."""
    lg = _league("Snaps", pid="sn")
    rows, lu = [], []

    def start(team, key, pts, result):
        rows.append(_row(lg, team, 80.0, result, opp=90.0 if result == "L" else 70.0))
        lu.append({"league_id": lg["id"], "season": SEASON, "week": 3,
                   "team_id": team, "player_key": key, "slot": "RB",
                   "started": True, "points": pts, "ppr_points": pts})

    for i in range(4):
        start(f"sk{i}", "skattebo", 0.0, "L")     # played, scored nothing
        start(f"bye{i}", "on_a_bye", 0.0, "L")    # never took the field
        start(f"unk{i}", "unmatched", 0.0, "L")   # no snap data at all
    played = {"skattebo": True, "on_a_bye": False}
    got = league_stats.compute_week(rows, lu, {}, SEASON, 3, min_starts=3, played=played)
    worst = [c["player_key"] for c in got["players"]["win_worst"]]
    assert "skattebo" in worst, worst
    assert "on_a_bye" not in worst, worst
    # Nothing known about him: the old rule, a zero stays off.
    assert "unmatched" not in worst, worst

    # Without any snap data the board behaves exactly as 054 did.
    old = league_stats.compute_week(rows, lu, {}, SEASON, 3, min_starts=3)
    assert "skattebo" not in [c["player_key"] for c in old["players"]["win_worst"]]


def test_a_starter_who_played_for_nothing_counts_as_turning_up():
    """The abandoned-roster rule counted starters who SCORED. A team whose
    starters played and scored zero was there; snaps say so."""
    lg = _league("Turned", pid="tu")
    rows, lu = [], []
    rows.append(_row(lg, "unlucky", 20.0, "L", opp=90.0))
    lu += _lineup(lg, "unlucky", scored=3, blank=6)    # six starters at 0.0...
    for i in range(8):
        rows.append(_row(lg, f"t{i}", 95.0 + i, "W", opp=90.0))
        lu += _lineup(lg, f"t{i}", scored=9, blank=0)
    played = {f"out{i}": True for i in range(6)}        # ...who all played
    got = league_stats.compute_week(rows, lu, {}, SEASON, 3, played=played)
    lowest = [c["team_name"] for c in got["halls"]["lowest"]]
    assert lowest[0] == "unlucky", lowest
    assert (got["halls"]["lowest"][0]["played"], got["halls"]["lowest"][0]["starters"]) == (9, 9)
