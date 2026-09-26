"""
The photo desk: staff photos tied to a player, used by every paper that
features him. Run with: python -m pytest tests/test_photo_desk.py -q
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

import newspaper  # noqa: E402
from web import demo_db  # noqa: E402
from web.generate import _photo_desk  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def clean():
    demo_db._PLAYER_PHOTOS.clear()
    demo_db._USERS.clear()
    yield


def _p(name, pts, shot=True):
    return {"name": name, "actual": pts, "position": "WR",
            "headshot_url": f"https://cdn/{name}.png" if shot else None}


GAME = {"winner": "A", "team_1": {"team_name": "A", "all_starters": [
            _p("Drake London", 31.0), _p("Bijan Robinson", 12.0)]},
        "team_2": {"team_name": "B", "all_starters": [_p("Puka Nacua", 20.0)]}}


def test_names_match_loosely():
    assert newspaper.plain_player_name("Michael Penix Jr.") == "michael penix"
    assert newspaper.plain_player_name("Ja'Marr  Chase") == "jamarr chase"


def test_no_desk_means_the_headshot():
    shot = newspaper.auto_photo_for_game(GAME, {})
    assert shot["url"] == "https://cdn/Drake London.png"


def test_a_desk_photo_beats_the_headshot_with_its_credit():
    desk = {"drake london": {"url": "https://desk/london.jpg",
                             "caption": "", "credit": "@memes"}}
    shot = newspaper.auto_photo_for_game(GAME, desk)
    assert shot["url"] == "https://desk/london.jpg"
    assert shot["caption"] == "Drake London — 31.0 pts (Photo: @memes)"


def test_another_standout_can_carry_the_photo():
    desk = {"bijan robinson": {"url": "https://desk/bijan.jpg", "caption": "RB1"}}
    assert newspaper.auto_photo_for_game(GAME, desk)["url"] == "https://desk/bijan.jpg"


def test_the_hero_photo_uses_the_desk():
    desk = {"puka nacua": {"url": "https://desk/puka.jpg", "caption": "Puka!"}}
    hero = newspaper.auto_hero_photo([GAME], desk)
    assert hero["url"] == "https://desk/puka.jpg" and hero["caption"] == "Puka!"


def test_this_weeks_photo_beats_an_any_week_one():
    demo_db.add_player_photo({"season": 2026, "week": None, "player_name": "Drake London",
                              "image_url": "any.jpg"})
    demo_db.add_player_photo({"season": 2026, "week": 3, "player_name": "Drake London",
                              "image_url": "wk3.jpg"})
    demo_db.add_player_photo({"season": 2026, "week": 4, "player_name": "Drake London",
                              "image_url": "wk4.jpg"})
    assert _photo_desk(demo_db, 2026, 3)["drake london"]["url"] == "wk3.jpg"
    assert _photo_desk(demo_db, 2026, 5)["drake london"]["url"] == "any.jpg"


# --- the staff page ---------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    from fastapi.testclient import TestClient
    from web import app as webapp
    monkeypatch.setattr(webapp, "db", demo_db)
    return TestClient(webapp.app)


def _sign_up(client, plan):
    client.post("/signup", data={"email": "p@example.com",
                                 "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"})
    user = demo_db.user_by_email("p@example.com")
    demo_db.update_user(user["id"], {"plan": plan})


def test_staff_only(web):
    _sign_up(web, "paid")
    assert web.get("/staff/photos").status_code == 404


def test_upload_and_delete(web):
    import nfl_week
    _sign_up(web, "staff")
    r = web.post("/staff/photos", data={"player_name": "Drake London", "week": "3",
                                        "credit": "@memes"},
                 files={"photo": ("london.png", PNG, "image/png")},
                 follow_redirects=False)
    assert r.status_code == 303 and "saved=1" in r.headers["location"]
    row = demo_db.player_photos(nfl_week.current_season())[0]
    assert row["player_name"] == "Drake London" and row["week"] == 3
    assert row["storage_path"] in demo_db._IMAGES
    web.post(f"/staff/photos/{row['id']}/delete", data={"week": 3})
    assert demo_db.player_photos(nfl_week.current_season()) == []
    assert row["storage_path"] not in demo_db._IMAGES, "the file goes too"


def test_not_an_image_is_refused(web):
    _sign_up(web, "staff")
    r = web.post("/staff/photos", data={"player_name": "Drake London", "week": ""},
                 files={"photo": ("x.png", b"<svg>nope</svg>" * 4, "image/png")},
                 follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_a_last_name_alone_is_refused(web):
    _sign_up(web, "staff")
    r = web.post("/staff/photos", data={"player_name": "London", "week": ""},
                 files={"photo": ("x.png", PNG, "image/png")}, follow_redirects=False)
    assert "full+name" in r.headers["location"] or "full%20name" in r.headers["location"]
