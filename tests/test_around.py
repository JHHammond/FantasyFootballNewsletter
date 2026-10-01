"""
Around the Leagues, full page: standardized PPR, lineups, the week and
season numbers, and the page. The SQL twin of compute_week/compute_season
(migration 029) was checked against these on a real Postgres.
Run with: python -m pytest tests/test_around.py -q
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.pop("RESEND_API_KEY", None)

import providers  # noqa: E402
from providers import apply_lineup_gaps  # noqa: E402
from providers.cache import TTLCache  # noqa: E402
from providers.models import PlayerLine  # noqa: E402
from providers.yahoo import YahooProvider  # noqa: E402
from tests import fixtures_yahoo as fx  # noqa: E402
from web import around, demo_db, league_stats, ppr  # noqa: E402

SEASON = 2026


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for store in (demo_db._LEAGUES, demo_db._USERS, demo_db._TEAM_WEEKS,
                  demo_db._LINEUPS, demo_db._NFL_PLAYERS, demo_db._RATE_EVENTS):
        store.clear()
    league_stats.clear_cache()
    around.clear_cache()
    # Never the network: a test that wants standardized scores passes a context.
    monkeypatch.setattr(ppr, "context", lambda season, week: None)
    yield


def _week(monkeypatch):
    monkeypatch.setattr(YahooProvider, "_get", lambda self, path, league_key=None: fx.route(path))
    a = YahooProvider(cache=TTLCache(tempfile.mkdtemp(), "yahoo"), access_token="t")
    return apply_lineup_gaps(a.get_week(fx.LEAGUE_KEY, SEASON, 3))


def _league(name="A", pid="a"):
    return demo_db.create_league(provider="yahoo", platform_league_id=pid,
                                 league_name=name, paper_name=f"The {name} Times",
                                 commissioner_name="", season=SEASON,
                                 public_slug=f"{name.lower()}-x", admin_token="t" * 20)


def _ctx():
    """Sleeper knows Burrow by his yahoo id, Chase only by name, and the
    Jaguars by team. Everyone else on the roster is unmatched."""
    index = ppr.build_index({
        "4866": {"full_name": "Joe Burrow", "position": "QB", "team": "CIN", "yahoo_id": 30123},
        "7564": {"full_name": "Ja'Marr Chase", "position": "WR", "team": "CIN"},
        "JAX": {"first_name": "Jacksonville", "last_name": "Jaguars", "position": "DEF", "team": "JAX"},
        "9999": {"full_name": "Kicker Nobody", "position": "K", "team": "DAL"},
    })
    # Burrow 25.5 in PPR, Chase 23.0, the Jaguars 7, nobody else played.
    return ppr.Context(index, {"4866": [25.5, 24.0, 22.5], "7564": [23.0, 20.0, 17.0],
                               "JAX": [7.0, 7.0, 7.0]})


# --- matching ---------------------------------------------------------------

def test_matching_across_platforms():
    ctx = _ctx()
    p = lambda pid, name, pos, team=None: PlayerLine(pid, name, pos, 0.0, nfl_team=team)  # noqa: E731
    assert ctx.resolve(p("yahoo:30123", "J. Burrow", "QB")) == "4866"      # by yahoo id
    assert ctx.resolve(p("espn:1", "Ja'Marr Chase", "WR")) == "7564"       # by name
    assert ctx.resolve(p("espn:-16030", "Jaguars D/ST", "DEF", "JAC")) == "JAX"  # by team, any spelling
    assert ctx.resolve(p("sleeper:4866", "Joe Burrow", "QB")) == "4866"
    assert ctx.resolve(p("espn:2", "Nobody Known", "RB")) is None
    assert ctx.ppr("4866") == 25.5 and ctx.ppr("9999") == 0.0 and ctx.ppr(None) is None


def test_names_are_normalized():
    assert ppr.norm_name("Marvin Harrison Jr.") == ppr.norm_name("marvin harrison")
    assert ppr.norm_name("Amon-Ra St. Brown") == "amon ra st brown"
    assert ppr.team("WSH") == "WAS" and ppr.team("jac") == "JAX"


def test_both_shapes_of_the_stats_endpoint():
    as_list = [{"player_id": "1", "stats": {"pts_ppr": 10.5, "pts_half_ppr": 9, "pts_std": 7.5}},
               {"player_id": "2", "stats": {"rec": 3}}]
    as_dict = {"1": {"pts_ppr": 10.5, "pts_half_ppr": 9, "pts_std": 7.5}}
    assert ppr._stats_from(as_list) == {"1": [10.5, 9.0, 7.5]}
    assert ppr._stats_from(as_dict) == {"1": [10.5, 9.0, 7.5]}


def test_a_team_scored_in_ppr(monkeypatch):
    team = _week(monkeypatch).matchups[0].teams[0]          # Hank's Heroes
    score, coverage = _ctx().team_score(team)
    # Burrow 25.5 + Chase 23.0 + JAX 7.0 matched; the other six starters at
    # their own league's points: 4.0 + 11.0 + 3.1 + 0.0 + 2.5 + 5.0
    assert score == pytest.approx(25.5 + 23.0 + 7.0 + 4.0 + 11.0 + 3.1 + 0.0 + 2.5 + 5.0)
    assert coverage == pytest.approx(3 / 9, abs=0.001)


# --- storing a week ---------------------------------------------------------

def test_save_week_stores_lineups_and_ppr(monkeypatch):
    lg = _league()
    n = league_stats.save_week(demo_db, lg, _week(monkeypatch), _ctx())
    assert n == 4
    hank = next(r for r in demo_db._TEAM_WEEKS.values() if r["team_name"] == "Hank's Heroes")
    assert hank["ppr_points"] == pytest.approx(81.1) and hank["ppr_coverage"] == pytest.approx(0.333)
    mine = [r for r in demo_db._LINEUPS.values() if r["team_id"] == hank["team_id"]]
    keys = {r["player_key"]: r for r in mine}
    assert keys["4866"]["started"] and keys["4866"]["ppr_points"] == 25.5   # one key per player
    assert keys["yahoo:36001"]["started"] is False and keys["yahoo:36001"]["slot"] == "BN"
    assert keys["yahoo:36002"]["slot"] == "IR" and not keys["yahoo:36002"]["started"]
    assert demo_db._NFL_PLAYERS["4866"]["name"] == "Joe Burrow"            # Sleeper's spelling
    assert demo_db._NFL_PLAYERS["JAX"]["name"] == "Jaguars D/ST"


def test_without_sleeper_the_week_still_saves(monkeypatch):
    lg = _league()
    league_stats.save_week(demo_db, lg, _week(monkeypatch), None)
    assert all(r["ppr_points"] is None for r in demo_db._TEAM_WEEKS.values())
    assert demo_db._LINEUPS and all(r["ppr_points"] is None for r in demo_db._LINEUPS.values())


def test_a_week_collected_before_lineups_is_collected_again(monkeypatch):
    wd = _week(monkeypatch)
    monkeypatch.setattr(providers, "load_week", lambda *a, **k: wd)
    monkeypatch.setattr(league_stats, "week_is_final", lambda s, w, now=None: True)
    lg = _league()
    demo_db.upsert_team_weeks(league_stats.rows_from_week(lg, wd))   # old-style, no lineups
    report = league_stats.collect_week(demo_db, 3, season=SEASON,
                                       sleep=lambda s: None, log=lambda s: None)
    assert report["collected"] == 1 and demo_db._LINEUPS
    again = league_stats.collect_week(demo_db, 3, season=SEASON,
                                      sleep=lambda s: None, log=lambda s: None)
    assert again["skipped_done"] == 1


# --- the numbers ------------------------------------------------------------

def _tw(lg, team, pts, result, opp, week=3, **kw):
    r = {"league_id": lg, "provider": "sleeper", "platform_league_id": "x",
         "season": SEASON, "week": week, "team_id": team, "team_name": team,
         "manager": "", "points": pts, "opponent_points": opp,
         "margin": pts - opp, "result": result, "optimal_points": pts,
         "bench_left": 0.0, "empty_slots": 0, "team_count": 4,
         "scoring_type": "ppr", "ppr_points": None, "ppr_coverage": None}
    r.update(kw)
    return r


def test_lineup_losses_luck_and_streaks():
    rows = [
        # week 2
        _tw("L1", "a", 120, "W", 90, week=2), _tw("L1", "b", 90, "L", 120, week=2),
        _tw("L1", "c", 100, "W", 80, week=2), _tw("L1", "d", 80, "L", 100, week=2),
        # week 3: b loses but its bench would have won; d wins with the low score
        _tw("L1", "a", 150, "W", 140, week=3),
        _tw("L1", "b", 140, "L", 150, week=3, optimal_points=155.0, bench_left=15.0),
        _tw("L1", "c", 130, "L", 70, week=3),
        _tw("L1", "d", 70, "W", 60, week=3, empty_slots=1),
    ]
    # fix c/d week 3 to be a real pair
    rows[6].update(result="W", opponent_points=70, margin=60)
    rows[7].update(result="L", opponent_points=130, margin=-60, points=70)
    wk = league_stats.compute_week(rows, [], {}, SEASON, 3)
    assert wk["lineup"]["losses"] == 2 and wk["lineup"]["lineup_losses"] == 1
    assert wk["lineup"]["ghosts"] == 1
    assert wk["unlucky"][0]["team_id"] == "b"           # 140 and lost
    ss = league_stats.compute_season(rows, SEASON)
    assert ss["through"] == 3 and ss["tracker"]["unbeaten"] == 2   # a and c
    assert ss["win_streaks"][0]["team_id"] in ("a", "c") and ss["win_streaks"][0]["streak"] == 2
    # b beat 1 of 3 teams in week 2 and 2 of 3 in week 3: a 1.0-win
    # scorer with 0 wins, the unluckiest
    assert ss["unluckiest"][0]["team_id"] == "b"
    assert ss["unluckiest"][0]["expected"] == 1.0 and ss["unluckiest"][0]["luck"] == -1.0


def test_player_boards():
    rows = [_tw("L1", t, 100 + i, "W" if i % 2 else "L", 100) for i, t in enumerate("abcd")]
    lu = []
    for i, t in enumerate("abcd"):
        lu.append({"league_id": "L1", "season": SEASON, "week": 3, "team_id": t,
                   "player_key": "star", "slot": "QB", "started": True,
                   "points": 30.0, "ppr_points": 30.0})
        lu.append({"league_id": "L1", "season": SEASON, "week": 3, "team_id": t,
                   "player_key": "dud", "slot": "BN" if t == "a" else "RB",
                   "started": t != "a", "points": 1.0, "ppr_points": 1.0})
    players = {"star": {"name": "Star Man", "position": "QB"}}
    wk = league_stats.compute_week(rows, lu, players, SEASON, 3, min_starts=2)
    P = wk["players"]
    assert P["top_on"][0] == {"player_key": "star", "name": "Star Man", "position": "QB",
                              "teams": 4, "best": 30.0}
    assert P["started"][0]["player_key"] == "star" and P["started"][0]["start_pct"] == 1.0
    assert P["benched"][0]["player_key"] == "dud" and P["benched"][0]["benched"] == 1
    assert P["flops"][0]["player_key"] == "dud"
    # dud started for b, c, d (won, lost, won); star for all four (2-2)
    assert [(p["player_key"], p["win_pct"]) for p in P["win_best"]] == [("dud", 0.667), ("star", 0.5)]


def test_describe_never_names_a_team():
    r = {"team_name": "Secret Name", "team_count": 12, "scoring_type": "half_ppr",
         "provider": "espn"}
    assert around.describe(r) == "a 12-team half-PPR league on ESPN"
    assert around.one_in(1000, 6100) == "1 in 6"


# --- the page ---------------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    from fastapi.testclient import TestClient
    from web import app as webapp
    monkeypatch.setattr(webapp, "db", demo_db)
    return TestClient(webapp.app)


def _staff(client):
    client.post("/signup", data={"email": "s@example.com",
                                 "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"})
    user = demo_db.user_by_email("s@example.com")
    demo_db.update_user(user["id"], {"plan": "staff"})


def test_the_page_and_its_public_preview(web, monkeypatch):
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    _staff(web)
    lg = _league()
    league_stats.save_week(demo_db, lg, _week(monkeypatch), _ctx())
    r = web.get("/staff/around?week=3")
    assert r.status_code == 200
    for words in ("Where does your score rank?", "The lineup desk", "The players",
                  "Face-offs", "The season so far", "Joe Burrow", "Collect weeks 1"):
        assert words in r.text, words
    assert "Hank&#39;s Heroes" in r.text or "Hank's Heroes" in r.text   # staff see names

    pub = web.get("/staff/around?week=3&preview=public")
    assert pub.status_code == 200
    assert "Hank" not in pub.text                     # nobody's team name
    assert "Collect weeks" not in pub.text
    assert "4-team" in pub.text or "league on Yahoo" in pub.text


def test_page_before_migration_029(web, monkeypatch):
    _staff(web)
    monkeypatch.setattr(demo_db, "lineups_ready", lambda: False)
    r = web.get("/staff/around")
    assert r.status_code == 200 and "029_lineups_and_ppr.sql" in r.text


def test_a_league_with_custom_scoring_stays_out_of_raw_stats():
    """30 Sep: 'There Can Only Be One' averages 1,052 a team. It must not
    fill the chart, the halls or the format lookups."""
    rows = []
    for lg, mult in (("normal", 1.0), ("huge", 8.0)):
        for i, t in enumerate("abcd"):
            p = (100 + i * 10) * mult
            rows.append(_tw(lg, t, p, "W" if i % 2 else "L", p - 5,
                            bench_left=20.0 * mult, ppr_points=None))
    wk = league_stats.compute_week(rows, [], {}, SEASON, 3)          # nothing standardized yet
    assert wk["summary"]["custom_leagues"] == 1 and wk["summary"]["compared"] == 4
    assert max(h["lo"] for h in wk["hist"]) < 200
    assert wk["halls"]["highest"][0]["league_id"] == "normal"
    assert wk["pct"]["n_ppr"] == 4 and wk["lineup"]["avg_bench_left"] == 20.0
    # Once standardized, the huge league's PPR scores count again; its own points don't.
    for r in rows:
        r["ppr_points"] = r["points"] / (8.0 if r["league_id"] == "huge" else 1.0)
    wk = league_stats.compute_week(rows, [], {}, SEASON, 3)
    assert wk["summary"]["compared"] == 8 and wk["summary"]["custom_leagues"] == 1
    assert wk["pct"]["n_ppr"] == 4 and wk["halls"]["blowouts"][0]["league_id"] == "normal"
