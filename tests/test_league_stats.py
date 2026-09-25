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
    for store in (demo_db._LEAGUES, demo_db._USERS, demo_db._TEAM_WEEKS,
                  demo_db._RATE_EVENTS):
        store.clear()
    league_stats.clear_cache()
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
    # Week 2 of 2026 ends Monday 21 Sep; final at 08:00 UTC Tuesday 22 Sep.
    assert not league_stats.week_is_final(2026, 2, datetime(2026, 9, 22, 7, tzinfo=timezone.utc))
    assert league_stats.week_is_final(2026, 2, datetime(2026, 9, 22, 9, tzinfo=timezone.utc))


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
    _league("A", pid="a")
    slept = []
    league_stats.collect_week(demo_db, 3, season=SEASON, sleep=slept.append,
                              log=lambda s: None)
    assert slept == [league_stats.PACE["yahoo"]]


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
    assert web.post("/staff/around/collect", data={"weeks": "1-2"}).status_code == 404


def test_staff_page_renders_boards(web):
    _sign_up(web, "staff")
    lg = _league("A", pid="a")
    demo_db.upsert_team_weeks([_row(lg, "Sad Sacks", 41.2, result="L", opp=120.0)])
    r = web.get("/staff/around?week=3")
    assert r.status_code == 200
    assert "Lowest scores" in r.text and "Sad Sacks" in r.text
    assert "Backfill weeks 1" in r.text


def test_backfill_button_starts_a_background_run(web, monkeypatch):
    _sign_up(web, "staff")
    started = []
    monkeypatch.setattr(league_stats, "start_background",
                        lambda db, weeks: started.append(weeks) or True)
    r = web.post("/staff/around/collect", data={"weeks": "1-2"},
                 follow_redirects=False)
    assert r.status_code == 303 and started == [[1, 2]]
