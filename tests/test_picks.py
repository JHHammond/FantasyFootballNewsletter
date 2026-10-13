"""
The league votes on next week's lines (12 Oct 2026). web/picks.py.
Run with: python -m pytest tests/test_picks.py -q
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.pop("RESEND_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

import history  # noqa: E402
import newspaper  # noqa: E402
import nfl_week  # noqa: E402
from web import app as webapp  # noqa: E402
from web import demo_db, picks  # noqa: E402

_REAL_IS_LOCKED = picks.is_locked   # the fixture swaps it out

V1, V2, V3 = "a" * 32, "b" * 32, "c" * 32
GAME = "1-2"
LINE = {"key": GAME, "favorite": "Sell the Falcons", "underdog": "Wasteland",
        "favorite_id": "1", "underdog_id": "2", "spread": 6.5, "pickem": False,
        "total": 230.0, "favorite_points": 118.0, "underdog_points": 112.0}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for store in (demo_db._LEAGUES, demo_db._RATE_EVENTS, demo_db._LINE_VOTES):
        store.clear()
    monkeypatch.setattr(webapp, "db", demo_db)
    monkeypatch.setattr(picks, "is_locked", lambda season, week, now=None: False)
    yield


@pytest.fixture
def client():
    return TestClient(webapp.app)


@pytest.fixture
def league():
    return demo_db.create_league(
        provider="sleeper", platform_league_id="123", league_name="K",
        paper_name="", commissioner_name="", season=2026,
        public_slug="kev-7f3a", admin_token="tok")


URL = "/p/kev-7f3a/2026/week-5/lines"


def _vote(client, voter, side, game=GAME, **kw):
    return client.post(URL, json={"voter": voter, "game": game, "side": side}, **kw)


# --- The routes -------------------------------------------------------------

def test_you_see_the_split_only_after_you_vote(client, league):
    _vote(client, V1, "fav")
    _vote(client, V2, "fav")
    r = client.get(URL, params={"v": V3})
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["games"][GAME] == {"mine": None}

    got = _vote(client, V3, "dog").json()
    assert (got["mine"], got["fav"], got["dog"]) == ("dog", 2, 1)
    assert client.get(URL, params={"v": V3}).json()["games"][GAME]["fav"] == 2


def test_voting_again_changes_your_vote_instead_of_adding_one(client, league):
    _vote(client, V1, "fav")
    got = _vote(client, V1, "dog").json()
    assert (got["mine"], got["fav"], got["dog"]) == ("dog", 0, 1)


def test_once_voting_closes_everyone_sees_it_and_nobody_can_vote(client, league, monkeypatch):
    _vote(client, V1, "fav")
    monkeypatch.setattr(picks, "is_locked", lambda season, week, now=None: True)
    data = client.get(URL, params={"v": V2}).json()
    assert data["locked"] and data["games"][GAME]["fav"] == 1
    r = _vote(client, V2, "dog")
    assert r.status_code == 409
    assert len(demo_db._LINE_VOTES) == 1


@pytest.mark.parametrize("body", [
    {"voter": "short", "game": GAME, "side": "fav"},
    {"voter": V1, "game": "<script>", "side": "fav"},
    {"voter": V1, "game": GAME, "side": "over"},
    {"voter": V1, "game": GAME},
])
def test_bad_votes_are_refused(client, league, body):
    assert client.post(URL, json=body).status_code == 400
    assert not demo_db._LINE_VOTES


def test_only_json_is_accepted(client, league):
    """A cross-site form can't post JSON without a preflight we never allow."""
    r = client.post(URL, data={"voter": V1, "game": GAME, "side": "fav"})
    assert r.status_code == 415 and not demo_db._LINE_VOTES


def test_a_league_that_isnt_there_or_the_wrong_season_is_a_404(client, league):
    assert client.get("/p/nope/2026/week-5/lines").status_code == 404
    assert client.get("/p/kev-7f3a/2025/week-5/lines").status_code == 404
    assert _vote(client, V1, "fav").status_code == 200
    assert client.post("/p/kev-7f3a/2026/week-40/lines",
                       json={"voter": V1, "game": GAME, "side": "fav"}).status_code == 404


def test_votes_are_rate_limited_per_ip(client, league, monkeypatch):
    monkeypatch.setattr(picks, "VOTES_PER_IP_PER_HOUR", 2)
    assert _vote(client, V1, "fav").status_code == 200
    assert _vote(client, V2, "fav").status_code == 200
    assert _vote(client, V3, "fav").status_code == 429


def test_the_manage_page_counts_people_who_voted(client, league):
    demo_db.save_paper(league["id"], 5, 2026, "p", "u", {})
    for v in (V1, V2):
        _vote(client, v, "fav")
        _vote(client, v, "dog", game="3-4")
    text = client.get("/l/tok").text
    assert "2 people voted on the lines" in text


# --- The paper ---------------------------------------------------------------

def test_the_teams_are_the_buttons_and_start_switched_off():
    html = newspaper._lines_items([LINE])
    assert 'data-game="1-2"' in html
    assert html.count('class="line-team ') == 2 and html.count('lv-side" data-side') == 2
    assert 'data-side="fav"' in html and 'data-side="dog"' in html
    # Until the script opens voting, a button that does nothing must not
    # look like one: disabled, and the split hidden.
    assert html.count(" disabled>") == 2 and 'class="lv-split" hidden' in html
    assert "<script>" in html and "/lines" in html
    assert '<div class="lv-note" hidden>Tap a team to pick. Closes Thursday.</div>' in html


def test_the_editor_gets_no_buttons():
    html = newspaper._lines_items([LINE], editable=True)
    assert "lv-side" not in html and "<script>" not in html and "<button" not in html


def test_names_on_the_buttons_are_escaped():
    html = newspaper._lines_items([dict(LINE, favorite="<b>x</b>")])
    assert "<b>x</b>" not in html


def test_older_lines_without_ids_get_a_stable_key():
    old = {k: v for k, v in LINE.items() if k not in ("key",)}
    a, b = picks.line_key_for(old), picks.line_key_for(
        dict(old, favorite=old["underdog"], underdog=old["favorite"]))
    assert a == b and picks.GAME_RE.match(a)


def test_lines_are_keyed_by_team_ids_either_way_round():
    assert history.line_key("7", "3") == history.line_key("3", "7") == "3-7"
    assert picks.GAME_RE.match(history.line_key("3", "7"))


# --- Voting closes at the next week's first kickoff --------------------------

def test_voting_closes_at_thursday_night_kickoff():
    closes = picks.voting_closes(2026, 5)              # lines for week 6
    assert closes == datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)  # 8:15pm EDT Thu 15 Oct


def test_thanksgiving_week_closes_at_noon_thirty():
    assert nfl_week.thanksgiving(2026).day == 26
    week = next(w for w in range(1, 19)
                if nfl_week.first_kickoff(w, 2026).date().day == 26
                and nfl_week.first_kickoff(w, 2026).month == 11)
    assert nfl_week.first_kickoff(week, 2026).hour == 17   # 12:30pm EST


def test_is_locked_compares_to_the_close():
    closes = datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)
    assert not _REAL_IS_LOCKED(2026, 5, now=closes.replace(minute=14))
    assert _REAL_IS_LOCKED(2026, 5, now=closes)


# --- Grading, for the next paper ---------------------------------------------

def _week(fav_pts, dog_pts):
    teams = [NS(team_id="1", team_name="Sell the Falcons", points=fav_pts),
             NS(team_id="2", team_name="Wasteland", points=dog_pts)]
    return NS(matchups=[NS(teams=teams)])


@pytest.mark.parametrize("fav,dog,covered", [
    (120.0, 110.0, "fav"), (110.0, 108.0, "dog"), (90.0, 120.0, "dog"),
    (116.5, 110.0, "push"),
])
def test_grading_against_the_spread(fav, dog, covered):
    assert picks.grade(LINE, _week(fav, dog))["covered"] == covered


def test_a_pickem_goes_to_the_winner():
    line = dict(LINE, pickem=True, spread=0.5)
    assert picks.grade(line, _week(100.0, 99.0))["covered"] == "fav"
    assert picks.grade(line, _week(99.0, 100.0))["covered"] == "dog"


def test_a_renamed_team_is_still_found_by_id():
    renamed = dict(LINE, favorite="Old Name")
    assert picks.grade(renamed, _week(120.0, 100.0))["covered"] == "fav"


def test_the_writer_hears_the_league_was_wrong():
    rows = [{"game": GAME, "voter": v, "side": "fav"} for v in "abcdefghi"] + \
           [{"game": GAME, "voter": v, "side": "dog"} for v in "jk"]
    res = picks.results([LINE], _week(81.0, 112.4), rows)
    assert (res["won"], res["lost"]) == (0, 1)
    facts = picks.writer_facts(res)
    assert "the league took Sell the Falcons 9-2" in facts
    assert "Wasteland won 112.4-81.0" in facts and "WRONG" in facts
    assert "went 0-1 against the spread" in facts


def test_no_votes_means_nothing_for_the_writer():
    assert picks.writer_facts(picks.results([LINE], _week(120.0, 100.0), [])) == ""


def test_the_generator_hands_the_writer_the_results(league):
    from web.generate import league_picks_facts
    demo_db.save_line_vote(league["id"], 2026, 4, GAME, V1, "dog")
    out = league_picks_facts(demo_db, league, 5, _week(81.0, 112.4), [LINE])
    assert "HOW THE LEAGUE PICKED" in out and "RIGHT" in out
    assert league_picks_facts(demo_db, league, 5, _week(81.0, 112.4), []) == ""
