"""
The NFL wire: staff notes about a week that reach the papers they concern.
Run with: python -m pytest tests/test_nfl_wire.py -q
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

import writer  # noqa: E402
from providers.models import (League, Manager, Matchup, PlayerLine, Team,  # noqa: E402
                              WeekData)
from web import demo_db  # noqa: E402
from web.generate import nfl_notes_for  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    demo_db._NFL_NOTES.clear()
    demo_db._USERS.clear()
    yield


def _team(tid, players):
    return Team(team_id=tid, team_name=tid, manager=Manager(tid, tid),
                lineup=[PlayerLine(player_id=f"x:{n}", name=n, position="WR",
                                   points=10.0, slot="WR") for n in players])


def _week(*rosters):
    teams = [_team(f"t{i}", r) for i, r in enumerate(rosters)]
    return WeekData(league=League("sleeper", "1", "L", 2026), week=3,
                    matchups=[Matchup("1", (teams[0], teams[1]))])


def test_a_note_reaches_leagues_with_that_player():
    demo_db.add_nfl_note(2026, 3, "Drake London had 11 catches with Michael Penix Jr. back.")
    has = _week(["Drake London"], ["Ja'Marr Chase"])
    hasnt = _week(["Puka Nacua"], ["Ja'Marr Chase"])
    assert "Drake London" in nfl_notes_for(demo_db, 2026, 3, has)
    assert nfl_notes_for(demo_db, 2026, 3, hasnt) == ""


def test_suffixes_and_punctuation_do_not_matter():
    demo_db.add_nfl_note(2026, 3, "Michael Penix returned and threw for 300.")
    assert nfl_notes_for(demo_db, 2026, 3, _week(["Michael Penix Jr."], ["X Y"]))
    demo_db._NFL_NOTES.clear()
    demo_db.add_nfl_note(2026, 3, "Ja'Marr Chase was held catchless by a double team.")
    assert nfl_notes_for(demo_db, 2026, 3, _week(["JaMarr Chase"], ["X Y"]))


def test_last_names_alone_never_match():
    """Two Josh Allens: a note about one must not land on the other's paper."""
    demo_db.add_nfl_note(2026, 3, "Josh Allen (the Jaguars pass rusher) had 3 sacks.")
    assert nfl_notes_for(demo_db, 2026, 3, _week(["Allen Robinson"], ["X Y"])) == ""
    demo_db._NFL_NOTES.clear()
    demo_db.add_nfl_note(2026, 3, "Keenan Allen left early with a hamstring injury.")
    assert nfl_notes_for(demo_db, 2026, 3, _week(["Josh Allen"], ["X Y"])) == ""


def test_a_name_inside_a_longer_name_does_not_match():
    demo_db.add_nfl_note(2026, 3, "Marvin Harrison Jr. scored twice.")
    assert nfl_notes_for(demo_db, 2026, 3, _week(["Marvin Harrison"], ["X Y"]))
    demo_db._NFL_NOTES.clear()
    demo_db.add_nfl_note(2026, 3, "Tony Pollardson scored.")
    assert nfl_notes_for(demo_db, 2026, 3, _week(["Tony Pollard"], ["X Y"])) == ""


def test_every_league_notes_go_everywhere_and_weeks_stay_apart():
    demo_db.add_nfl_note(2026, 3, "Snow in Buffalo slowed every passing game.", all_leagues=True)
    demo_db.add_nfl_note(2026, 2, "Drake London was quiet.")
    out = nfl_notes_for(demo_db, 2026, 3, _week(["Drake London"], ["X Y"]))
    assert "Snow in Buffalo" in out and "quiet" not in out


def test_the_wire_is_its_own_cached_block_between_voice_and_league():
    blocks = writer.system_prompt("standard", None, league_context="LORE",
                                  nfl_notes="- Drake London went off.")
    assert len(blocks) == 3
    assert "THE NFL WIRE" in blocks[1]["text"] and "Drake London" in blocks[1]["text"]
    assert "LORE" in blocks[2]["text"]
    assert all(b.get("cache_control") for b in blocks)
    assert sum(1 for b in blocks if b.get("cache_control")) <= 4


def test_no_notes_leaves_the_prompt_exactly_as_it_was():
    assert (writer.system_prompt("standard", None, league_context="LORE")
            == writer.system_prompt("standard", None, league_context="LORE", nfl_notes=""))


# --- the staff page ---------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    from fastapi.testclient import TestClient
    from web import app as webapp
    monkeypatch.setattr(webapp, "db", demo_db)
    return TestClient(webapp.app)


def _sign_up(client, plan):
    client.post("/signup", data={"email": "w@example.com",
                                 "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"})
    user = demo_db.user_by_email("w@example.com")
    demo_db.update_user(user["id"], {"plan": plan})


def test_staff_only(web):
    _sign_up(web, "paid")
    assert web.get("/staff/wire").status_code == 404
    assert web.post("/staff/wire", data={"week": 3, "note": "x"}).status_code == 404


def test_add_and_remove_a_note(web):
    import nfl_week
    _sign_up(web, "staff")
    r = web.post("/staff/wire", data={"week": 3, "note": "Drake London <b>went off</b>."})
    assert r.status_code == 200 and "Drake London went off." in r.text
    note = demo_db.nfl_notes(nfl_week.current_season(), 3)[0]
    assert note["note"] == "Drake London went off." and not note["all_leagues"]
    web.post(f"/staff/wire/{note['id']}/delete", data={"week": 3})
    assert demo_db.nfl_notes(nfl_week.current_season(), 3) == []
