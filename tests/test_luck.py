"""
The Luck Index: the arithmetic and the pages. The SQL twin (migration 031)
was checked against compute() on a real Postgres.
Run with: python -m pytest tests/test_luck.py -q
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.pop("RESEND_API_KEY", None)

from web import demo_db, luck  # noqa: E402

SEASON = 2026


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for store in (demo_db._LEAGUES, demo_db._USERS, demo_db._TEAM_WEEKS,
                  demo_db._LINEUPS, demo_db._RATE_EVENTS):
        store.clear()
    luck.clear_cache()
    monkeypatch.delenv("LUCK_PUBLIC", raising=False)
    yield


def _g(lg, team, week, pts, opp_pts, result, opt=None, name=None, manager=""):
    return {"league_id": lg, "season": SEASON, "week": week, "team_id": team,
            "team_name": name or team, "manager": manager, "provider": "sleeper",
            "team_count": 4, "scoring_type": "ppr", "points": pts,
            "opponent_points": opp_pts, "result": result,
            "optimal_points": opt if opt is not None else pts, "bench_left": 0.0}


def _league4(lg="L"):
    """a beats b, c beats d in week 1; a beats c, b beats d in week 2.
    Week 1: b scores 2nd-most (130) and loses to a (140): unlucky.
    d scores 3rd (120) - no, d scores 60 and loses."""
    return [
        _g(lg, "a", 1, 140, 130, "W"), _g(lg, "b", 1, 130, 140, "L"),
        _g(lg, "c", 1, 90, 60, "W"), _g(lg, "d", 1, 60, 90, "L"),
        _g(lg, "a", 2, 100, 95, "W", opt=100), _g(lg, "c", 2, 95, 100, "L", opt=110),
        _g(lg, "b", 2, 120, 50, "W"), _g(lg, "d", 2, 50, 120, "L"),
    ]


def test_schedule_luck():
    rows = {r["team_id"]: r for r in luck.compute(_league4(), [], SEASON)}
    # b: week 1 beat 2 of 3 (lost), week 2 beat 3 of 3 (won): 1 win, 1.667 earned
    assert rows["b"]["schedule"] == pytest.approx(-0.667, abs=0.001)
    # c: week 1 beat 1 of 3 and won; week 2 beat 1 of 3 and lost: 1 win, 0.667 earned
    assert rows["c"]["schedule"] == pytest.approx(0.333, abs=0.001)
    assert sum(r["schedule"] for r in rows.values()) == pytest.approx(0, abs=0.01)
    # c's own bench (110) would have beaten a (100): a lineup loss, and a's gift
    assert rows["c"]["own_losses"] == 1 and rows["a"]["gifts"] == 1
    assert rows["b"]["players"] == 0 and rows["b"]["has_players"] is False


def test_player_luck_from_lineups():
    tw = _league4()
    lu = []
    # b's star averages 30 (40 in week 1, 20 in week 2). Week 2 he was 10 under.
    for wk, pts in ((1, 40.0), (2, 20.0)):
        lu.append({"league_id": "L", "season": SEASON, "week": wk, "team_id": "b",
                   "player_key": "star", "slot": "QB", "started": True, "points": pts,
                   "ppr_points": pts})
    rows = {r["team_id"]: r for r in luck.compute(tw, lu, SEASON)}
    b = rows["b"]
    assert b["has_players"] and b["player_dev"] == 0.0
    # Week 1 at average: 130 - 10 = 120 still beats d (60) and c (90): ap 2/3 both ways.
    # Week 2 at average: 120 + 10 = 130 beats everyone, same as real. So no player luck.
    assert b["players"] == pytest.approx(0, abs=0.001)


def test_verdicts_and_percentiles():
    assert luck.verdict(2.0) == "Blessed" and luck.verdict(-0.2) == "Fair"
    assert luck.verdict(-2) == "Cursed"
    pct = [x / 10 for x in range(-50, 51)]       # -5 .. 5 in 101 steps
    assert luck.share_below(pct, 0.0) == pytest.approx(0.5)
    assert luck.share_below(pct, -9) == 0 and luck.share_below(pct, 9) == 1


# --- pages ------------------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    from fastapi.testclient import TestClient
    from web import app as webapp
    monkeypatch.setattr(webapp, "db", demo_db)
    return TestClient(webapp.app)


def _league(name="Kevlarville", pid="k1"):
    return demo_db.create_league(provider="sleeper", platform_league_id=pid,
                                 league_name=name, paper_name=f"The {name} Times",
                                 commissioner_name="", season=SEASON,
                                 public_slug=f"{name.lower()}-x{pid}",
                                 admin_token="t" * 20)


def _seed():
    lg = _league()
    rows = _league4(lg["id"])
    for r in rows:
        r["team_name"] = {"a": "Hank's Heroes", "b": "Mike Vick Legal Team",
                          "c": "The Mid Tier", "d": "Sad Sacks"}[r["team_id"]]
        r["manager"] = {"a": "hank", "b": "johnhenry", "c": "mid", "d": "sad"}[r["team_id"]]
    demo_db.upsert_team_weeks(rows)
    return lg


def _staff(client):
    client.post("/signup", data={"email": "s@example.com",
                                 "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"})
    user = demo_db.user_by_email("s@example.com")
    demo_db.update_user(user["id"], {"plan": "staff"})


def test_closed_until_switched_on(web, monkeypatch):
    lg = _seed()
    assert web.get("/luck").status_code == 404
    assert web.get(f"/luck/{lg['public_slug']}").status_code == 404
    monkeypatch.setenv("LUCK_PUBLIC", "1")
    assert web.get("/luck").status_code == 200


def test_staff_preview(web):
    _staff(web)
    r = web.get("/luck")
    assert r.status_code == 200 and "Staff preview" in r.text


def test_find_your_league_by_your_name(web, monkeypatch):
    monkeypatch.setenv("LUCK_PUBLIC", "1")
    lg = _seed()
    r = web.get("/luck?q=JohnHenry")
    assert r.status_code == 200 and "Mike Vick Legal Team" in r.text
    assert f"/luck/{lg['public_slug']}?team=b" in r.text
    miss = web.get("/luck?q=nobody-at-all")
    assert "No team by that name" in miss.text


def test_league_page_and_a_team_card(web, monkeypatch):
    monkeypatch.setenv("LUCK_PUBLIC", "1")
    lg = _seed()
    page = web.get(f"/luck/{lg['public_slug']}")
    assert page.status_code == 200
    for name in ("Hank&#39;s Heroes", "Mike Vick Legal Team", "The Mid Tier", "Sad Sacks"):
        assert name in page.text
    card = web.get(f"/luck/{lg['public_slug']}?team=b")
    assert "wins of luck" in card.text and "-0.7" in card.text
    assert "Share my report" in card.text and "Your play earned" in card.text
    assert "The Luck Report" in card.text and "Around the country" in card.text
    assert f"/luck/{lg['public_slug']}/card/b.png?size=og" in card.text      # the link preview
    assert "Read this week&#39;s paper" in card.text


def test_a_duplicate_league_row_finds_the_stats(web, monkeypatch):
    monkeypatch.setenv("LUCK_PUBLIC", "1")
    _seed()
    twin = _league(name="Twin", pid="k1")          # same real league, second row
    page = web.get(f"/luck/{twin['public_slug']}")
    assert "Mike Vick Legal Team" in page.text


def test_the_report_cards():
    rows = luck.compute(_league4(), [], SEASON)
    for r in rows:
        r.update(verdict=luck.verdict(r["total"]), luckier_than=50,
                 deserved_w=round(r["w"] + 0.5 * r["t"] - r["total"], 1),
                 deserved_l=round(r["games"] - (r["w"] + 0.5 * r["t"] - r["total"]), 1))
    b = next(r for r in rows if r["team_id"] == "b")
    detail = luck.compute_team(_league4(), [], SEASON, "L", "b")
    rep = luck.report(b, detail, rows)
    kinds = [c["kind"] for c in rep["cards"]]
    assert kinds[0] == "record" and "league" not in kinds   # the verdict carries the league
    week = next(c for c in rep["cards"] if c["kind"] == "week")
    # week 1: b scored 130, 2nd of 4, and lost
    assert week["big"] == "2nd of 4" and week["sub"] == "And you lost."
    assert rep["highlights"][0].startswith("Week 1: the 2nd-highest score")
    c = next(r for r in rows if r["team_id"] == "c")
    skill = next(x for x in luck.report(c, luck.compute_team(_league4(), [], SEASON, "L", "c"), rows)["cards"]
                 if x["kind"] == "skill")
    assert skill["big"] == "1"                       # c's bench would have won week 2


def test_share_images(web, monkeypatch):
    monkeypatch.setenv("LUCK_PUBLIC", "1")
    lg = _seed()
    from PIL import Image
    import io
    for size, dims in (("story", (1080, 1920)), ("square", (1080, 1080)), ("og", (1200, 630))):
        r = web.get(f"/luck/{lg['public_slug']}/card/b.png?size={size}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert Image.open(io.BytesIO(r.content)).size == dims
    assert web.get(f"/luck/{lg['public_slug']}/card/nobody.png").status_code == 404


def test_card_text_survives_emoji_and_long_names():
    from web import luck_card
    team = {"team_name": "\U0001F3C8 Win now or lifelong rebuild Dynasty forever and ever \U0001F3C8",
            "manager": "x", "total": -0.02, "schedule": 0.0, "players": 0.0, "w": 1, "l": 1, "t": 0,
            "deserved_w": 1.0, "deserved_l": 1.0, "verdict": "Fair", "luckier_than": 51}
    for size in luck_card.SIZES:
        assert luck_card.render(team, "League", 100, 3, size, ["A line."])[:8] == b"\x89PNG\r\n\x1a\n"
    assert luck_card.signed(-0.02) == "0.0" and luck_card.signed(-1.25) == "\u22121.2"
    assert luck_card.rank_line(4, 15726) == "Unluckier than 96% of 15,726 teams in America."


def test_player_photos():
    assert luck.photo_url("4866") == "https://sleepercdn.com/content/nfl/players/4866.jpg"
    assert luck.photo_url("KC", "DEF") == "https://sleepercdn.com/images/team_logos/nfl/kc.png"
    assert luck.photo_url("espn:3916387", "WR").startswith("https://a.espncdn.com/")
    assert luck.photo_url("yahoo:461.p.33536", "RB") is None
    assert luck.photo_url(None) is None


def test_the_opponent_card_charts_their_season():
    # a averages 120 and scores 150 against b in week 3
    rows = [
        _g("L", "a", 1, 105, 90, "W"), _g("L", "b", 1, 90, 105, "L"),
        _g("L", "a", 2, 105, 80, "W"), _g("L", "c", 2, 80, 105, "L"),
        _g("L", "b", 2, 100, 70, "W"), _g("L", "d", 2, 70, 100, "L"),
        _g("L", "a", 3, 150, 110, "W"), _g("L", "b", 3, 110, 150, "L"),
        _g("L", "c", 1, 95, 85, "W"), _g("L", "d", 1, 85, 95, "L"),
        _g("L", "c", 3, 90, 88, "W"), _g("L", "d", 3, 88, 90, "L"),
    ]
    table = luck.compute(rows, [], SEASON)
    for r in table:
        r.update(verdict=luck.verdict(r["total"]), luckier_than=50,
                 deserved_w=round(r["w"] + 0.5 * r["t"] - r["total"], 1),
                 deserved_l=round(r["games"] - (r["w"] + 0.5 * r["t"] - r["total"]), 1))
    b = next(r for r in table if r["team_id"] == "b")
    detail = luck.compute_team(rows, [], SEASON, "L", "b")
    card = next(c for c in luck.report(b, detail, table)["cards"] if c["kind"] == "opponent")
    ch = card["chart"]
    assert [x["week"] for x in ch["bars"]] == [1, 2, 3]
    assert [x["vs"] for x in ch["bars"]] == [False, False, True]
    assert ch["hit"]["pts"] == "150.0" and ch["avg"] == "120.0" and ch["you"] == "110.0"
    assert ch["hit"]["y"] < ch["avg_y"] < ch["base"]            # the bump, above the line


def test_the_swap_that_would_have_won_it():
    rows = _league4()
    rows[1]["optimal_points"] = 147                         # b, week 1: lost 130-140
    lu = [
        {"league_id": "L", "season": SEASON, "week": 1, "team_id": "b", "player_key": k,
         "slot": slot, "started": slot != "BN", "points": pts}
        for k, slot, pts in (("london", "WR", 5.0), ("bijan", "RB", 25.0),
                             ("mclaurin", "BN", 17.0), ("kicker", "BN", 30.0))
    ]
    names = {"london": {"name": "Drake London", "position": "WR"},
             "bijan": {"name": "Bijan Robinson", "position": "RB"},
             "mclaurin": {"name": "Terry McLaurin", "position": "WR"},
             "kicker": {"name": "Some Kicker", "position": "K"}}
    table = luck.compute(rows, lu, SEASON)
    for r in table:
        r.update(verdict=luck.verdict(r["total"]), luckier_than=50,
                 deserved_w=round(r["w"] + 0.5 * r["t"] - r["total"], 1),
                 deserved_l=round(r["games"] - (r["w"] + 0.5 * r["t"] - r["total"]), 1))
    b = next(r for r in table if r["team_id"] == "b")
    detail = luck.compute_team(rows, lu, SEASON, "L", "b", names)
    swap = luck.winning_swap(detail)
    assert swap["out"]["name"] == "Drake London" and swap["in"]["name"] == "Terry McLaurin"
    card = next(c for c in luck.report(b, detail, table)["cards"] if c["kind"] == "swap")
    assert card["eyebrow"] == "Week 1 · lost by 10.0"
    assert card["line"] == "Start Terry McLaurin over Drake London and you win by 2.0."
    assert card["swap"]["out"]["pts"] == "5.0" and card["swap"]["in"]["pts"] == "17.0"
    # a kicker can't play wide receiver, and a swap that only ties doesn't count
    lu[2]["points"] = 15.0
    assert luck.winning_swap(luck.compute_team(rows, lu, SEASON, "L", "b", names)) is None


def test_earned_wins_read_as_one_number():
    assert luck.n_wins(0.3) == "0.3 wins" and luck.n_wins(1.0) == "1 win" and luck.n_wins(2) == "2 wins"
    team = {"w": 0, "l": 3, "t": 0, "total": -0.3, "deserved_w": 0.3, "deserved_l": 2.7}
    card = luck.report(team, {}, [])["cards"][0]
    assert card["big"] == "0-3" and card["line"] == "Your play earned 0.3 wins."
    assert card["sub"] == "Somebody owes you 0.3 wins."


def test_the_league_strip():
    rows = luck.compute(_league4(), [], SEASON)
    strip = luck.league_strip(rows)
    xs = [d["x"] for d in strip["dots"]]
    assert xs == sorted(xs) and all(4 <= x <= 96 for x in xs)
    assert strip["luckiest"]["total"] == max(r["total"] for r in rows)
    assert strip["unluckiest"]["total"] == min(r["total"] for r in rows)
    crowd = luck.league_strip([{"team_id": str(i), "team_name": str(i), "total": 0.01 * i}
                               for i in range(4)])
    assert len({d["lane"] for d in crowd["dots"]}) == 3          # piled-up dots stack
