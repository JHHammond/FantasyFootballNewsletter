"""
The weekly reminder to commissioners who aren't paying (28 Sep).
Run with: python -m pytest tests/test_reminders.py -q
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

from web import demo_db, emailer, reminders  # noqa: E402

SEASON, WEEK = 2026, 3


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for store in (demo_db._LEAGUES, demo_db._USERS, demo_db._PAPERS):
        store.clear()
    demo_db._OPTOUTS.clear()
    demo_db._REMINDERS.clear()
    yield


def _user(email, plan="free"):
    u = demo_db.create_user(email, "x")
    demo_db.update_user(u["id"], {"plan": plan})
    return u


def _league(name, user=None, owner_email=None):
    lg = demo_db.create_league(provider="sleeper", platform_league_id=name,
                               league_name=name, paper_name="", commissioner_name="",
                               season=SEASON, public_slug=name.lower(),
                               admin_token=f"tok-{name}")
    fields = {}
    if user:
        fields["user_id"] = user["id"]
    if owner_email:
        fields["owner_email"] = owner_email
    demo_db.update_league(lg["id"], fields)
    return lg


def test_who_gets_it():
    free = _user("free@x.com")
    paid = _user("paid@x.com", "paid")
    _league("Alpha", user=free)
    _league("Beta", user=free)
    _league("Gamma", user=paid)                       # paying: never
    _league("Delta", owner_email="Legacy@X.com")      # no account: by owner email
    done = _league("Echo", owner_email="done@x.com")  # already made week 3
    demo_db.save_paper(done["id"], WEEK, SEASON, "p", "u", {})
    _league("Foxtrot", owner_email="out@x.com")
    demo_db.add_email_optout("out@x.com")             # unsubscribed

    people = {p["email"]: p for p in reminders.audience(demo_db, SEASON, WEEK)}
    assert set(people) == {"free@x.com", "legacy@x.com"}
    assert sorted(l["name"] for l in people["free@x.com"]["leagues"]) == [
        "The Alpha Times", "The Beta Times"]


def test_a_second_run_mails_nobody_twice(monkeypatch):
    _league("Alpha", user=_user("free@x.com"))
    sent = []
    monkeypatch.setattr(emailer, "send_reminder",
                        lambda to, *a, **k: sent.append(to) or emailer.SendResult(ok=True))
    reminders.send(demo_db, SEASON, WEEK, sleep=lambda s: None, log=lambda s: None)
    reminders.send(demo_db, SEASON, WEEK, sleep=lambda s: None, log=lambda s: None)
    assert sent == ["free@x.com"]


def test_dry_run_and_test_send_mark_nothing(monkeypatch):
    _league("Alpha", user=_user("free@x.com"))
    sent = []
    monkeypatch.setattr(emailer, "send_reminder",
                        lambda to, *a, **k: sent.append(to) or emailer.SendResult(ok=True))
    reminders.send(demo_db, SEASON, WEEK, dry_run=True, log=lambda s: None)
    reminders.send(demo_db, SEASON, WEEK, test_to="me@x.com", log=lambda s: None)
    assert sent == ["me@x.com"]
    assert reminders.audience(demo_db, SEASON, WEEK)   # still due


def test_the_unsubscribe_link_is_signed():
    tok = reminders.unsubscribe_token("A@B.com")
    assert reminders.email_from_token(tok) == "a@b.com"
    assert reminders.email_from_token(tok[:-2] + "00") is None


def test_the_email_teases_real_numbers_and_names_nobody(monkeypatch):
    got = {}
    monkeypatch.setattr(emailer, "_send", lambda to, subject, body, **kw:
                        got.update(subject=subject, body=body, **kw) or emailer.SendResult(ok=True))
    emailer.send_reminder("a@b.com", 3, [{"name": "The Alpha Times", "admin_token": "tok",
                                          "league_name": "Alpha",
                                          "teasers": ["Somebody in your league put up 71.4.",
                                                      "Somebody lost by 0.3."]}], "t.sig")
    assert got["subject"] == "Somebody in your league put up 71.4"
    assert "Somebody lost by 0.3." in got["body"] and "The paper knows who" in got["body"]
    assert "Alpha" not in got["body"]                   # no names
    assert "/l/tok" in got["body"] and "Write Week 3" in got["body"]
    assert "/stop/t.sig" in got["unsubscribe_url"] and "Unsubscribe" in got["body"]


def test_without_stats_it_falls_back_to_the_plain_pitch(monkeypatch):
    got = {}
    monkeypatch.setattr(emailer, "_send", lambda to, subject, body, **kw:
                        got.update(subject=subject, body=body) or emailer.SendResult(ok=True))
    emailer.send_reminder("a@b.com", 3, [{"name": "X", "admin_token": "tok", "teasers": []}], "t.sig")
    assert got["subject"] == "Your league's Week 3 paper is ready to write"
    assert "Make my Week 3 paper" in got["body"]


def test_teasers_come_from_the_leagues_own_week():
    rows = [{"result": "W", "points": 140.0, "margin": 0.3, "bench_left": 5, "top_player_points": 41.4},
            {"result": "L", "points": 139.7, "margin": -0.3, "bench_left": 31.2, "top_player_points": 20},
            {"result": "W", "points": 120.0, "margin": 48.6, "bench_left": 2, "top_player_points": 18},
            {"result": "L", "points": 71.4, "margin": -48.6, "bench_left": 9, "top_player_points": 15}]
    assert reminders.teasers_for(rows) == [
        "Somebody in your league put up 71.4.",
        "Somebody lost by 0.3.",
        "Somebody left 31.2 points on their bench."]
    assert reminders.teasers_for(rows[:2]) == []       # too little to go on


def test_the_stop_link_unsubscribes():
    from fastapi.testclient import TestClient
    from web import app as webapp
    orig = webapp.db
    webapp.db = demo_db
    try:
        r = TestClient(webapp.app).get("/stop/" + reminders.unsubscribe_token("x@y.com"))
        assert r.status_code == 200 and "off the list" in r.text
        assert "x@y.com" in demo_db.email_optouts()
    finally:
        webapp.db = orig


def test_the_audience_carries_each_leagues_teasers():
    lg = _league("Alpha", user=_user("free@x.com"))
    demo_db._TEAM_WEEKS.clear()
    demo_db.upsert_team_weeks([
        {"league_id": lg["id"], "provider": "sleeper", "platform_league_id": "Alpha",
         "season": SEASON, "week": WEEK, "team_id": str(i), "points": p,
         "result": res, "margin": m, "bench_left": 0, "top_player_points": 10}
        for i, (p, res, m) in enumerate([(120, "W", 30), (90, "L", -30),
                                         (110, "W", 2), (108, "L", -2)])])
    person = reminders.audience(demo_db, SEASON, WEEK)[0]
    assert person["leagues"][0]["teasers"][:2] == [
        "Somebody in your league put up 90.0.", "Somebody lost by 2.0."]


# --- who the Tuesday job writes for (28 Sep) ---------------------------------

def test_weekly_send_covers_every_league_of_a_paying_owner_this_season_only():
    paid = _user("paid@x.com", "paid")
    a = _league("A", user=paid)
    b = _league("B", user=paid)
    _league("Dup", user=paid)
    dup2 = _league("Dup2", user=paid)
    demo_db.update_league(dup2["id"], {"platform_league_id": "Dup"})   # same real league
    unlinked = _league("Unlinked", owner_email="PAID@x.com")          # never linked
    old = _league("Old", user=paid)
    demo_db.update_league(old["id"], {"season": SEASON - 1})          # last season
    _league("Free", user=_user("free@x.com"))

    names = sorted(l["league_name"] for l in demo_db.leagues_for_weekly_send())
    assert names == ["A", "B", "Dup", "Unlinked"]
    assert all(l["_owner"]["email"] == "paid@x.com" for l in demo_db.leagues_for_weekly_send())
