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
    for words in ("Where does your score rank?", "The lineup desk", "The player boards",
                  "Face-offs", "The season so far", "Joe Burrow", "Collect weeks 1",
                  "See the public page"):
        assert words in r.text, words
    assert "Hank&#39;s Heroes" in r.text or "Hank's Heroes" in r.text   # staff see names

    # The old preview link goes to the public page, which staff can always see.
    old = web.get("/staff/around?week=3&preview=public", follow_redirects=False)
    assert old.status_code == 303 and old.headers["location"] == "/around?week=3"
    pub = web.get("/around?week=3")
    assert pub.status_code == 200
    assert "Hank" not in pub.text                     # nobody's team name
    assert "Collect weeks" not in pub.text and "See the public page" not in pub.text
    assert "4-team" in pub.text or "league on Yahoo" in pub.text
    assert "Make your league" in pub.text             # the pitch, public only


def test_around_is_closed_until_the_flag_is_set(web, monkeypatch):
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    monkeypatch.delenv("AROUND_PUBLIC", raising=False)
    assert web.get("/around").status_code == 404
    monkeypatch.setenv("AROUND_PUBLIC", "1")
    lg = _league()
    league_stats.save_week(demo_db, lg, _week(monkeypatch), _ctx())
    r = web.get("/around?week=3")
    assert r.status_code == 200
    assert "Hank" not in r.text and "Collect weeks" not in r.text
    assert "Where does your score rank?" in r.text


def test_the_front_headline_and_stories():
    def tw(team, ppr, result, opp, **kw):
        return dict(_tw("L1", team, ppr, result, opp, team_count=10, **kw), ppr_points=ppr)
    rows = [tw("a", 171.2, "L", 175.0), tw("b", 175.0, "W", 171.2),
            tw("c", 61.0, "W", 58.0, bench_left=44.5, optimal_points=105.5,
               best_bench_player="Josh Allen"),
            tw("d", 58.0, "L", 61.0)]
    wk = league_stats.compute_week(rows, [], {}, SEASON, 3)
    a = {"players": wk["players"], "halls": wk["halls"], "lineup": wk["lineup"],
         "summary": wk["summary"], "unlucky": wk["unlucky"], "lucky": wk["lucky"],
         "teams": 4}
    f = around.front(a)
    # No lineups, so no flop: the 171.2 that lost is the banner.
    assert f["headline"] == "171.2 Points. Still Lost."
    kickers = [s["kicker"] for s in f["stories"]]
    assert kickers[:3] == ["The heartbreaker", "Highway robbery", "The bench disaster"]
    assert "Josh Allen" in f["stories"][2]["body"]
    # The words describe a team; they never name one.
    assert all(s["body"].startswith("A 10-team PPR league on Sleeper") for s in f["stories"])

    # A flop started by enough of the week's lineups takes the banner.
    a["summary"] = dict(a["summary"], lineup_teams=100)
    a["players"] = dict(a["players"], flops=[
        {"player_key": "k", "name": "Kicker Guy", "position": "K", "started": 90, "ppr": 1.0},
        {"player_key": "1", "name": "Star Back", "position": "RB", "started": 40, "ppr": 2.1}])
    f = around.front(a)
    assert f["headline"] == "40 Teams Started Star Back. He Scored 2.1."   # not the kicker
    assert f["stars"][0]["kicker"] == "The bust" and f["stars"][0]["p"]["name"] == "Star Back"


def test_where_a_score_ranks():
    pct = {"all": [round(40 + 1.6 * i, 1) for i in range(101)], "n_all": 3512,
           "half_ppr": [round(40 + 1.2 * i, 1) for i in range(101)], "n_half_ppr": 1250}
    r = around.rank(pct, 171.2)
    assert (r["pct"], r["teams"]) == (82, 3512)
    assert r["beat"] + r["place"] == r["teams"]          # beaten + at-or-above
    assert r["stamp"] == "TOP 25%" and r["verdict"] == "A strong week."
    assert around.rank(pct, 171.2, "half_ppr")["teams"] == 1250
    assert around.rank(pct, 171.2, "std") is None        # not collected
    # The ends, where interpolation has nothing to lean on.
    assert around.rank(pct, 0)["pct"] == 0 and around.rank(pct, 0)["place"] == 3512
    assert around.rank(pct, 400)["pct"] == 100 and around.rank(pct, 400)["place"] == 1
    assert [around.ordinal(n) for n in (1, 2, 3, 11, 13, 21, 112, 728)] == \
        ["1st", "2nd", "3rd", "11th", "13th", "21st", "112th", "728th"]


def test_the_script_and_the_card_agree_on_a_rank():
    """The page answers as you type and the card answers on the server. If
    the two drift, a share says something the page never said."""
    import re
    page = (Path(__file__).resolve().parent.parent
            / "web" / "templates" / "around.html").read_text()
    js = page[page.index("function below("):page.index("function verdict(")]
    assert "arr.length - 1" in js and "v <= arr[0]" in js   # the same shape
    # Every verdict the script can print is one the server can print.
    said = set(re.findall(r'"([^"]+\.)"', page[page.index("function verdict("):
                                                page.index("function ordinal(")]))
    assert said == {words for _, words, _ in around.VERDICTS}


def test_the_lookups_own_teams(monkeypatch):
    lg = _league()
    demo_db.update_league(lg["id"], {"user_id": "u1", "my_team_id": "b"})
    rows = [_tw(lg["id"], t, pts, "W", 90) for t, pts in (("a", 120.0), ("b", 140.0))]
    for r in rows:
        r["team_name"] = {"a": "Zebras", "b": "Anteaters"}[r["team_id"]]
    demo_db.upsert_team_weeks(rows)
    mine = around.my_teams(demo_db, {"id": "u1"}, SEASON, 3)
    assert len(mine) == 1 and len(mine[0]["teams"]) == 2
    # Their own team first, whatever it's called, then the rest by name.
    assert [t["name"] for t in mine[0]["teams"]] == ["Anteaters", "Zebras"]
    assert mine[0]["teams"][0]["mine"] is True
    assert around.my_teams(demo_db, None, SEASON, 3) == []          # signed out

    def boom(*a, **k):
        raise RuntimeError("supabase is having a day")
    monkeypatch.setattr(demo_db, "team_weeks_for_leagues", boom)
    assert around.my_teams(demo_db, {"id": "u1"}, SEASON, 3) == []  # never raises


def test_the_share_card(web, monkeypatch):
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    monkeypatch.setenv("AROUND_PUBLIC", "1")
    lg = _league()
    league_stats.save_week(demo_db, lg, _week(monkeypatch), _ctx())
    r = web.get("/around/card.png?score=120.5&week=3")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n" and len(r.content) > 5000
    for size in ("story", "og", "nonsense"):          # nonsense falls back
        assert web.get(f"/around/card.png?score=120.5&week=3&size={size}").status_code == 200
    # A score nobody could have scored isn't a card.
    for bad in ("", "abc", "-5", "9999", "NaN"):
        assert web.get(f"/around/card.png?score={bad}&week=3").status_code == 404
    # A shared link previews as its own card.
    page = web.get("/around?score=120.5")
    assert "/around/card.png?score=120.50" in page.text
    assert "/static/brand/og-image.png" in web.get("/around").text

    monkeypatch.delenv("AROUND_PUBLIC", raising=False)
    assert web.get("/around/card.png?score=120.5&week=3").status_code == 404


def test_scores_we_do_not_believe_stay_out(monkeypatch):
    """21 Oct, from the live staff page. Two shapes reached the stories:

      * a team whose starters all matched Sleeper but whose week had no
        stat lines, stored as ppr_points 0.00 beside a real 114.30. It was
        "0.0 points — the worst score that won", over a story saying the
        team scored 114.3.
      * a 4-team league starting twenty players, averaging ~317 in plain
        PPR. Its own points and its PPR points agree, so the "is this
        league standard?" ratio test passed it, and it owned every board.
    """
    rows = []
    # eight ordinary leagues
    for lg in range(8):
        for t in range(12):
            p = 100.0 + t * 4
            rows.append(_tw(f"ok{lg}", f"t{t}", p, "W" if t % 2 else "L", p - 3,
                            team_count=12, ppr_points=p - 1))
    # the 4-team league with enormous lineups
    for t, p in enumerate((315.7, 365.3, 288.1, 301.4)):
        rows.append(_tw("big", f"b{t}", p, "L" if t % 2 else "W", 300.0,
                        team_count=4, ppr_points=p))
    # the team whose standardized score came back as nothing
    rows.append(_tw("ghost", "g1", 114.3, "W", 110.2, team_count=12, ppr_points=0.0))
    rows.append(_tw("ghost", "g2", 110.2, "L", 114.3, team_count=12, ppr_points=105.0))
    for t in range(2, 12):
        rows.append(_tw("ghost", f"g{t}", 118.0, "W", 110.0, team_count=12, ppr_points=117.0))

    wk = league_stats.compute_week(rows, [], {}, SEASON, 3)
    std = [r["ppr_points"] for r in wk["halls"]["highest"] + wk["halls"]["lowest"]
           + wk["unlucky"] + wk["lucky"]]
    assert std, "something should still be on the boards"
    assert all(40 <= v <= 200 for v in std), std      # no 0.00, no 315.7
    assert all(r["league_id"] != "big" for r in wk["halls"]["highest"])
    assert wk["lucky"][0]["ppr_points"] > 0
    assert wk["summary"]["compared"] == 8 * 12 + 11   # everyone but big, and not g1
    assert wk["summary"]["custom_leagues"] == 1       # the big league's own points

    # g1's own 114.3 is still real in its own league, so it still counts
    # anywhere a league's own points are read: only its standardized 0.00
    # is untrusted. Its league is not a "custom scoring" league either.
    assert wk["pct"]["n_ppr"] == 8 * 12 + 12          # the ghost league included
    assert wk["summary"]["median_raw"] > 100          # its 114.3 is in the raw pool

    # The front page reads from those boards, so it's clean too.
    f = around.front(dict(wk, teams=wk["summary"]["teams"], front={}))
    assert "315.7" not in str(f) and "0.0 points" not in str(f)
    for story in f["stories"]:
        assert "0.0" != story["big"]


def test_a_league_rescued_by_standardizing_still_counts():
    """The other side of the same coin: a league averaging 1,052 of its own
    points standardizes to an ordinary 130, and that is exactly what
    standardizing is for (30 Sep). It must not be thrown out with the 317s."""
    rows = []
    for t in range(12):
        own = 1000.0 + t * 10
        rows.append(_tw("wild", f"w{t}", own, "W" if t % 2 else "L", own - 20,
                        team_count=12, ppr_points=120.0 + t))
    for t in range(12):
        p = 110.0 + t
        rows.append(_tw("plain", f"p{t}", p, "W" if t % 2 else "L", p - 2,
                        team_count=12, ppr_points=p))
    wk = league_stats.compute_week(rows, [], {}, SEASON, 3)
    assert wk["summary"]["compared"] == 24          # both leagues compared
    assert wk["summary"]["custom_leagues"] == 1     # only the wild one's own points
    assert max(h["lo"] for h in wk["hist"]) < 200
    assert all(r["league_id"] == "plain" for r in wk["halls"]["blowouts"])


def test_every_player_rides_along_for_the_search():
    rows = [_tw("L1", t, 100 + i, "W" if i % 2 else "L", 100)
            for i, t in enumerate("abcd")]
    lu = []
    for i, t in enumerate("abcd"):
        for key in ("star", "dud", "deep"):
            lu.append({"league_id": "L1", "season": SEASON, "week": 3, "team_id": t,
                       "player_key": key, "slot": "QB" if key != "deep" else "BN",
                       "started": key != "deep",
                       "points": {"star": 30.0, "dud": 1.0, "deep": 9.0}[key],
                       "ppr_points": {"star": 30.0, "dud": 1.0, "deep": 9.0}[key]})
    players = {"star": {"name": "Star Man", "position": "QB"}}
    wk = league_stats.compute_week(rows, lu, players, SEASON, 3, min_starts=2)
    everyone = wk["players"]["all"]
    assert {p["player_key"] for p in everyone} == {"star", "dud", "deep"}
    # Ranked by how many rosters he is on, and carrying every board's number
    # so the page can re-rank without asking the database again.
    assert everyone[0]["rostered"] == 4
    star = next(p for p in everyone if p["player_key"] == "star")
    assert star["teams"] == 4 and star["started"] == 4 and star["win_pct"] == 0.5
    assert star["name"] == "Star Man"


def test_the_demo_seeder_finds_real_sleeper_ids(monkeypatch):
    """The demo is only honest if its players carry real Sleeper ids: that's
    where the page gets their photos. Offline, it must still run."""
    from scripts import demo_around as demo
    raw = {"4866": {"full_name": "Brian Thomas Jr.", "position": "WR", "team": "JAX"},
           "9001": {"full_name": "Brian Thomas", "position": "WR", "team": "NYG"},
           "4984": {"full_name": "Josh Allen", "position": "QB", "team": "BUF"},
           "PIT": {"first_name": "Pittsburgh", "last_name": "Steelers",
                   "position": "DEF", "team": "PIT"}}
    monkeypatch.setattr(ppr, "fetch_index", lambda: ppr.build_index(raw))
    keys = demo.sleeper_keys()
    assert keys["Brian Thomas Jr."] == "4866"        # not his name-twin on the Giants
    assert keys["Josh Allen"] == "4984" and keys["Steelers D/ST"] == "PIT"
    assert around.photo({"player_key": keys["Josh Allen"], "position": "QB"})

    monkeypatch.setattr(ppr, "fetch_index", lambda: (_ for _ in ()).throw(OSError("offline")))
    assert demo.sleeper_keys() == {}                  # and the demo still runs
    demo.KEYS.clear()
    assert around.photo({"player_key": demo._key("Josh Allen"), "position": "QB"}) is None


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


def test_the_market_has_its_own_page(web, monkeypatch):
    """John, 22 Oct: split them, so Around the Leagues can go public while
    the trade market is still being refined. One page meant neither half
    could ship until both were ready."""
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    _staff(web)
    r = web.get("/staff/market")
    assert r.status_code == 200
    assert "trade market" in r.text.lower()
    # And a way back, so the two pages are not strangers.
    assert "/staff/around" in r.text


def test_the_market_is_staff_only_on_its_own_page(web, monkeypatch):
    """It moved; it did not become public. A reader who is not staff must
    not find it, and must not find it by guessing either."""
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    # 401 signed out, 404 signed in without the plan: either way, gone.
    assert web.get("/staff/market").status_code in (401, 302, 303, 404)


def test_around_carries_no_trade_market_any_more(web, monkeypatch):
    """The point of the split. Nothing about trades, prices or drafts may
    be left on the page that is about to go public — including on the
    staff view of it, or the next person to look will wonder which page
    is the real one."""
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    _staff(web)
    for path in ("/around", "/staff/around"):
        text = web.get(path).text
        for fragment in ("at-draftmkt", "at-trades", "at-mkt-board",
                         "What a player costs"):
            assert fragment not in text, (path, fragment)
        # The one mention allowed is the link across to the other page.
        assert text.count("trade market") <= (1 if path == "/staff/around" else 0), path
    # ...but staff can still get there.
    assert "/staff/market" in web.get("/staff/around").text


def _mine(web, user_email="s@example.com", lid="demo-mine"):
    """Give the signed-in viewer a league with a stored lineup."""
    import nfl_week
    u = demo_db.user_by_email(user_email)
    demo_db._LEAGUES[lid] = {"id": lid, "user_id": u["id"], "season": SEASON,
                             "provider": "sleeper", "platform_league_id": "x",
                             "league_name": "Mine", "paper_name": "My Times",
                             "my_team_id": "t0"}
    week = 3
    # National rows too, or there is no country to be read against: the
    # player cards come from around_week, which starts at team_weeks.
    demo_db.upsert_team_weeks([
        {"league_id": lid, "provider": "sleeper", "platform_league_id": "x",
         "season": SEASON, "week": week, "team_id": team, "team_name": team,
         "manager": "", "points": 42.0, "opponent_name": "o",
         "opponent_points": 40.0, "margin": 2.0, "result": "W",
         "optimal_points": 50.0, "bench_left": 8.0, "empty_slots": 0,
         "top_player": "star", "best_bench_player": "bench", "team_count": 12,
         "scoring_type": "ppr", "ppr_points": 42.0, "ppr_coverage": 1.0}
        for team in ("t0", "t1")])
    rows = []
    for team in ("t0", "t1"):
        for n, (key, pts, started) in enumerate((
                ("star", 30.0, True), ("mid", 12.0, True), ("bench", 25.0, False))):
            rows.append({"league_id": lid, "season": SEASON, "week": week,
                         "team_id": team, "player_key": key,
                         "slot": "WR" if started else "BN",
                         "started": started, "points": pts, "ppr_points": pts})
    demo_db.upsert_lineups(rows)
    demo_db.upsert_nfl_players([
        {"player_key": k, "name": k.title(), "position": "WR", "nfl_team": "CIN"}
        for k in ("star", "mid", "bench")])
    return u, lid, week


def test_your_own_lineup_is_read_against_the_country(web, monkeypatch):
    """John, 22 Oct: let a connected viewer see their own players. The page
    above is 14,000 strangers, which is interesting once; this is the same
    week read back through the lineup the reader actually cares about."""
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    _staff(web)
    u, lid, week = _mine(web)
    got = around.my_lineup(demo_db, u, SEASON, week, lid, "t0")
    assert got["league"] == "My Times"
    names = [p["name"] for p in got["started"]]
    assert names == ["Star", "Mid"], got["started"]      # best first
    # Somebody on the bench beat the worst starter, and he has a name.
    assert got["regret"] and got["regret"]["name"] == "Bench"
    assert got["worst"]["name"] == "Mid"


def test_a_lineup_is_only_yours_to_see(web, monkeypatch):
    """The decisive test. A team id is guessable and a lineup is somebody's
    private roster, so the league has to belong to the asker. Ownership is
    checked in my_lineup and nowhere else, which is why it is checked
    here."""
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    _staff(web)
    u, lid, week = _mine(web)
    assert around.my_lineup(demo_db, u, SEASON, week, lid, "t0")["started"]

    # A second person, who does not own that league, gets nothing — even
    # with the right league id and the right team id.
    web.post("/signup", data={"email": "nosy@example.com",
                              "password": "correct horse battery 9",
                              "confirm": "correct horse battery 9"})
    other = demo_db.user_by_email("nosy@example.com")
    assert around.my_lineup(demo_db, other, SEASON, week, lid, "t0") == {}
    assert around.my_lineup(demo_db, None, SEASON, week, lid, "t0") == {}


def test_the_lineup_endpoint_needs_a_session(web, monkeypatch):
    """It is one person's roster, so it is not a public URL."""
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    r = web.get("/around/my-lineup.json?league=demo-mine&team=t0&week=3")
    assert r.status_code in (401, 404)


def test_the_full_player_list_needs_an_account(web, monkeypatch):
    """John, 22 Oct: searching the boards should cost you a signup.

    The ten-a-board lists stay public — they are what readers and search
    engines arrive on — and the whole searchable list is what an account
    is for.
    """
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    monkeypatch.setenv("AROUND_PUBLIC", "1")
    _seed_players()
    page = web.get("/around").text
    assert "at-all-gate" in page, "no invitation to sign up"
    assert 'id="at-all-toggle"' not in page, "the control is there without an account"
    assert web.get("/around/players.json").status_code == 401


def test_the_gate_is_a_gate_and_not_a_curtain(web, monkeypatch):
    """The one that matters. The list used to ship inside the page for
    every visitor, so gating the BUTTON would have left the data one View
    Source away — and would have cost every reader the weight of a list
    most never open. It is not in the markup at all now."""
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    monkeypatch.setenv("AROUND_PUBLIC", "1")
    _seed_players()
    page = web.get("/around").text
    assert "var ALL = [];" in page
    assert "var ALL = [{" not in page


def test_an_account_opens_it(web, monkeypatch):
    import nfl_week
    monkeypatch.setattr(nfl_week, "completed_week", lambda *a, **k: 3)
    monkeypatch.setenv("AROUND_PUBLIC", "1")
    _seed_players()
    web.post("/signup", data={"email": "reader@example.com",
                              "password": "correct horse battery 9",
                              "confirm": "correct horse battery 9"})
    page = web.get("/around").text
    assert 'id="at-all-toggle"' in page
    assert "at-all-gate" not in page


def test_signing_up_from_the_gate_brings_you_back(web):
    """Somebody who signed up to search the player boards wants the player
    boards back, not the connect wizard."""
    r = web.post("/signup", data={"email": "back@example.com",
                                  "password": "correct horse battery 9",
                                  "confirm": "correct horse battery 9",
                                  "next": "/around"}, follow_redirects=False)
    assert r.headers.get("location") == "/around"


def test_signup_will_not_bounce_you_off_the_site(web):
    """`next` is a redirect target from a query string, so it is somebody
    else's input. "//evil.example" is protocol-relative and a browser
    follows it straight off the site."""
    for bad in ("//evil.example", "https://evil.example", "javascript:alert(1)"):
        r = web.post("/signup", data={"email": f"x{abs(hash(bad))}@example.com",
                                      "password": "correct horse battery 9",
                                      "confirm": "correct horse battery 9",
                                      "next": bad}, follow_redirects=False)
        assert r.headers.get("location") == "/connect?welcome=1", bad


def _seed_players(n_teams: int = 14, n_players: int = 20) -> None:
    """A week with enough teams and players that the full board exists.

    Without lineups there is no player list at all, so neither the gate
    nor the control renders and a test asserting on them proves nothing.
    """
    tw, lu = [], []
    for i in range(n_teams):
        tw.append({"league_id": "gate-lg", "provider": "sleeper",
                   "platform_league_id": "g", "season": SEASON, "week": 3,
                   "team_id": f"t{i}", "team_name": f"T{i}", "manager": "",
                   "points": 100.0 + i, "opponent_name": "o",
                   "opponent_points": 95.0, "margin": 5.0, "result": "W",
                   "optimal_points": 110.0, "bench_left": 10.0, "empty_slots": 0,
                   "top_player": "p0", "best_bench_player": "p1", "team_count": 12,
                   "scoring_type": "ppr", "ppr_points": 100.0 + i,
                   "ppr_coverage": 1.0})
        for j in range(n_players):
            lu.append({"league_id": "gate-lg", "season": SEASON, "week": 3,
                       "team_id": f"t{i}", "player_key": f"p{j}",
                       "slot": "WR" if j < 9 else "BN", "started": j < 9,
                       "points": 20.0 - j, "ppr_points": 20.0 - j})
    demo_db.upsert_team_weeks(tw)
    demo_db.upsert_lineups(lu)
    demo_db.upsert_nfl_players([
        {"player_key": f"p{j}", "name": f"Player {j}", "position": "WR",
         "nfl_team": "CIN"} for j in range(n_players)])
    around.clear_cache()
