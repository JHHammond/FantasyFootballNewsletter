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


def test_the_email_has_the_pitch_and_the_unsubscribe(monkeypatch):
    got = {}
    monkeypatch.setattr(emailer, "_send", lambda to, subject, body, **kw:
                        got.update(subject=subject, body=body, **kw) or emailer.SendResult(ok=True))
    emailer.send_reminder("a@b.com", 3, [{"name": "The Alpha Times", "admin_token": "tok"}], "t.sig")
    assert got["subject"] == "Week 3: Whose team flopped?"
    assert "Whose team balled out?" in got["body"]
    assert "find out" in got["body"] and "/l/tok" in got["body"]
    assert "/stop/t.sig" in got["unsubscribe_url"] and "Unsubscribe" in got["body"]


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
