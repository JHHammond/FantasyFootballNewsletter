"""
Web app tests. Run with: python -m pytest tests/test_web.py -v

These run against `web.demo_db` rather than a hand-written stub, which means
demo mode gets tested too — if it drifts from db.py, it stops being a truthful
preview of production, and that drift is exactly what these would catch.

No Supabase, no network, no emails sent (emailer falls back to stdout when
RESEND_API_KEY is unset).
"""

import html as html_lib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.pop("RESEND_API_KEY", None)   # keep sends to stdout

from fastapi.testclient import TestClient  # noqa: E402

from web import app as webapp  # noqa: E402
from web import demo_db, emailer, slugs  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    for store in (demo_db._LEAGUES, demo_db._LORE, demo_db._PAPERS,
                  demo_db._STORAGE, demo_db._SUBSCRIBERS, demo_db._MAGIC_LINKS):
        store.clear()
    webapp._HITS.clear()
    monkeypatch.setattr(webapp, "db", demo_db)
    yield


@pytest.fixture
def client():
    return TestClient(webapp.app)


@pytest.fixture
def league():
    return demo_db.create_league(
        provider="sleeper", platform_league_id="123",
        league_name="Kevlarville", paper_name="The Kevlarville Times",
        commissioner_name="johnhenryhammond", season=2025,
        public_slug="kevlarville-7f3a", admin_token="secret-admin-token",
    )


@pytest.fixture
def sent_emails(monkeypatch):
    """Capture outgoing mail instead of printing it."""
    captured = []

    def fake_send(to, subject, html_body, *, unsubscribe_url=None):
        captured.append({
            "to": to, "subject": subject, "body": html_body,
            "unsubscribe_url": unsubscribe_url,
        })
        return emailer.SendResult(ok=True)

    monkeypatch.setattr(emailer, "_send", fake_send)
    return captured


def _verify_ok(name="Kevlarville"):
    return lambda provider, **kw: type(
        "P", (), {"verify_league": lambda self, i, s: name})()


# ---------------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------------

def test_landing_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "league_id" in r.text


def test_landing_mentions_no_account(client):
    """The pitch. If this goes, the reason for the whole redesign went."""
    assert "No account" in client.get("/").text


def test_landing_says_lore_not_inside_jokes(client):
    text = client.get("/").text
    assert "lore" in text.lower()
    assert "inside joke" not in text.lower()


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


# ---------------------------------------------------------------------------
# League creation
# ---------------------------------------------------------------------------

def test_create_league_redirects_to_manage(client, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _verify_ok())
    r = client.post("/leagues", data={
        "provider": "sleeper", "league_id": "999", "season": 2025,
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/l/")
    assert len(demo_db._LEAGUES) == 1


def test_create_league_rejects_unknown_id(client, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _verify_ok(name=None))
    r = client.post("/leagues", data={"league_id": "bogus", "season": 2025})
    assert "Couldn&#39;t find that league" in r.text or "Couldn't find that league" in r.text


def test_duplicate_league_does_not_leak_admin_token(client, league):
    """Anyone can read a league ID off a URL. It can't be proof of ownership."""
    r = client.post("/leagues", data={"league_id": "123", "season": 2025})
    assert "already has a paper" in r.text
    assert "secret-admin-token" not in r.text
    assert "/recover" in r.text


def test_league_creation_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _verify_ok(name="L"))
    for i in range(webapp.LEAGUE_CREATES_PER_HOUR):
        client.post("/leagues", data={"league_id": f"id{i}", "season": 2025})
    r = client.post("/leagues", data={"league_id": "extra", "season": 2025})
    assert "Give it an hour" in r.text


# ---------------------------------------------------------------------------
# Token authorization
# ---------------------------------------------------------------------------

def test_manage_requires_the_right_token(client, league):
    assert client.get("/l/secret-admin-token").status_code == 200
    assert client.get("/l/wrong-token").status_code == 404


def test_bad_token_is_404_not_403(client, league):
    r = client.get("/l/nope")
    assert r.status_code == 404
    assert "403" not in r.text


def test_manage_warns_to_bookmark_on_first_visit(client, league):
    assert "Bookmark this page" in client.get("/l/secret-admin-token?new=1").text


def test_manage_shows_public_share_link(client, league):
    assert f"p/{league['public_slug']}" in client.get("/l/secret-admin-token").text


# ---------------------------------------------------------------------------
# Lore
# ---------------------------------------------------------------------------

def test_add_and_remove_lore(client, league):
    client.post("/l/secret-admin-token/lore", data={"entry": "Nick benches his best guy"})
    entries = demo_db.get_lore(league["id"])
    assert len(entries) == 1
    assert entries[0]["entry"] == "Nick benches his best guy"

    client.post(f"/l/secret-admin-token/lore/{entries[0]['id']}/remove")
    assert demo_db.get_lore(league["id"]) == []


def test_lore_requires_token(client, league):
    r = client.post("/l/wrong/lore", data={"entry": "nope"}, follow_redirects=False)
    assert r.status_code == 404


def test_empty_lore_is_ignored(client, league):
    client.post("/l/secret-admin-token/lore", data={"entry": "   "})
    assert demo_db.get_lore(league["id"]) == []


# ---------------------------------------------------------------------------
# Public reading
# ---------------------------------------------------------------------------

def test_public_archive_needs_no_token(client, league):
    demo_db.save_paper(league["id"], 3, 2025, "p", "u", {})
    r = client.get(f"/p/{league['public_slug']}")
    assert r.status_code == 200
    assert "Week 3" in r.text


def test_public_archive_never_exposes_admin_token(client, league):
    demo_db.save_paper(league["id"], 3, 2025, "p", "u", {})
    assert "secret-admin-token" not in client.get(f"/p/{league['public_slug']}").text


def test_reading_a_published_paper(client, league):
    demo_db._STORAGE[demo_db.storage_path(league["public_slug"], 2025, 3)] = "<h1>PAPER</h1>"
    r = client.get(f"/p/{league['public_slug']}/2025/week-3")
    assert r.status_code == 200
    assert "PAPER" in r.text
    assert "max-age" in r.headers["cache-control"]


def test_unpublished_week_is_404(client, league):
    assert client.get(f"/p/{league['public_slug']}/2025/week-9").status_code == 404


# ---------------------------------------------------------------------------
# Subscriptions — double opt-in
# ---------------------------------------------------------------------------

def test_subscribing_sends_a_confirmation_not_the_paper(client, league, sent_emails):
    client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    assert len(sent_emails) == 1
    assert "Confirm" in sent_emails[0]["subject"]


def test_subscriber_is_not_active_until_confirmed(client, league, sent_emails):
    client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    assert demo_db.active_subscribers(league["id"]) == []


def test_confirming_activates_the_subscription(client, league, sent_emails):
    client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    row = next(iter(demo_db._SUBSCRIBERS.values()))
    r = client.get(f"/subscribe/confirm/{row['confirm_token']}")
    assert r.status_code == 200
    # Jinja escapes the apostrophe, so compare against unescaped output.
    assert "You're in" in html_lib.unescape(r.text)
    assert len(demo_db.active_subscribers(league["id"])) == 1


def test_subscribing_twice_does_not_duplicate(client, league, sent_emails):
    for _ in range(2):
        client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    assert len(demo_db._SUBSCRIBERS) == 1


def test_already_confirmed_subscriber_gets_no_second_email(client, league, sent_emails):
    client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    row = next(iter(demo_db._SUBSCRIBERS.values()))
    client.get(f"/subscribe/confirm/{row['confirm_token']}")
    sent_emails.clear()

    client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    assert sent_emails == []


def test_subscribe_response_is_identical_for_new_and_existing(client, league, sent_emails):
    """Otherwise this endpoint tells a stranger who's on the list."""
    first = client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    second = client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    assert first.url == second.url


def test_bad_confirm_token_is_handled(client, league):
    r = client.get("/subscribe/confirm/garbage")
    assert r.status_code == 200
    assert "no good" in r.text.lower()


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------

def test_one_click_unsubscribe(client, league, sent_emails):
    client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    row = next(iter(demo_db._SUBSCRIBERS.values()))
    client.get(f"/subscribe/confirm/{row['confirm_token']}")
    assert len(demo_db.active_subscribers(league["id"])) == 1

    r = client.get(f"/unsubscribe/{row['unsubscribe_token']}")
    assert r.status_code == 200
    assert demo_db.active_subscribers(league["id"]) == []


def test_unsubscribe_takes_no_confirmation_step(client, league, sent_emails):
    """A GET must do it. Mail clients prefetch, and CAN-SPAM wants one click."""
    client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    row = next(iter(demo_db._SUBSCRIBERS.values()))
    client.get(f"/subscribe/confirm/{row['confirm_token']}")
    client.get(f"/unsubscribe/{row['unsubscribe_token']}")
    assert demo_db._SUBSCRIBERS[row["id"]]["unsubscribed_at"] is not None


def test_resubscribing_after_unsubscribe_works(client, league, sent_emails):
    client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    row = next(iter(demo_db._SUBSCRIBERS.values()))
    client.get(f"/subscribe/confirm/{row['confirm_token']}")
    client.get(f"/unsubscribe/{row['unsubscribe_token']}")

    client.post(f"/p/{league['public_slug']}/subscribe", data={"email": "a@b.com"})
    fresh = demo_db._SUBSCRIBERS[row["id"]]["confirm_token"]
    client.get(f"/subscribe/confirm/{fresh}")
    assert len(demo_db.active_subscribers(league["id"])) == 1


# ---------------------------------------------------------------------------
# Commissioner email + magic-link recovery
# ---------------------------------------------------------------------------

def test_saving_owner_email_sends_the_manage_link(client, league, sent_emails):
    client.post("/l/secret-admin-token/email", data={"email": "me@example.com"})
    assert demo_db._LEAGUES[league["id"]]["owner_email"] == "me@example.com"
    assert len(sent_emails) == 1
    assert "secret-admin-token" in sent_emails[0]["body"]


def test_manage_link_email_has_no_unsubscribe(client, league, sent_emails):
    """It's transactional. Adding marketing chrome would forfeit the exemption."""
    client.post("/l/secret-admin-token/email", data={"email": "me@example.com"})
    assert sent_emails[0]["unsubscribe_url"] is None


def test_recovery_emails_a_magic_link(client, league, sent_emails):
    demo_db.update_league(league["id"], {"owner_email": "me@example.com"})
    client.post("/recover", data={"email": "me@example.com"})
    assert len(sent_emails) == 1
    assert len(demo_db._MAGIC_LINKS) == 1


def test_recovery_reveals_nothing_for_unknown_addresses(client, sent_emails):
    r = client.post("/recover", data={"email": "nobody@example.com"},
                    follow_redirects=False)
    assert r.headers["location"] == "/recover?sent=1"
    assert sent_emails == []


def test_magic_link_returns_you_to_the_manage_page(client, league, sent_emails):
    demo_db.update_league(league["id"], {"owner_email": "me@example.com"})
    client.post("/recover", data={"email": "me@example.com"})
    token = next(iter(demo_db._MAGIC_LINKS))
    r = client.get(f"/recover/{token}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/l/secret-admin-token"


def test_magic_link_works_only_once(client, league, sent_emails):
    demo_db.update_league(league["id"], {"owner_email": "me@example.com"})
    client.post("/recover", data={"email": "me@example.com"})
    token = next(iter(demo_db._MAGIC_LINKS))

    client.get(f"/recover/{token}", follow_redirects=False)
    second = client.get(f"/recover/{token}")
    assert "expired" in second.text.lower()


def test_expired_magic_link_is_rejected(client, league):
    demo_db.update_league(league["id"], {"owner_email": "me@example.com"})
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    demo_db.create_magic_link("me@example.com", "stale", past)
    assert "expired" in client.get("/recover/stale").text.lower()


def test_multiple_leagues_get_a_picker(client, league, sent_emails):
    demo_db.update_league(league["id"], {"owner_email": "me@example.com"})
    second = demo_db.create_league(
        provider="sleeper", platform_league_id="456", league_name="Second",
        paper_name="The Second Times", commissioner_name="", season=2025,
        public_slug="second-1234", admin_token="second-token",
    )
    demo_db.update_league(second["id"], {"owner_email": "me@example.com"})

    client.post("/recover", data={"email": "me@example.com"})
    token = next(iter(demo_db._MAGIC_LINKS))
    r = client.get(f"/recover/{token}")
    assert "secret-admin-token" in r.text
    assert "second-token" in r.text


def test_recovery_is_rate_limited(client, sent_emails):
    for _ in range(webapp.RECOVERIES_PER_HOUR + 2):
        client.post("/recover", data={"email": "me@example.com"})
    # Never errors, just quietly stops sending.
    assert len(sent_emails) == 0


# ---------------------------------------------------------------------------
# Weekly auto-send
# ---------------------------------------------------------------------------

def _subscribe_and_confirm(client, league, email="a@b.com"):
    client.post(f"/p/{league['public_slug']}/subscribe", data={"email": email})
    row = next(s for s in demo_db._SUBSCRIBERS.values() if s["email"] == email)
    client.get(f"/subscribe/confirm/{row['confirm_token']}")
    return row


def test_weekly_send_mails_confirmed_subscribers(client, league, sent_emails):
    from web.tasks import send_weekly

    _subscribe_and_confirm(client, league)
    demo_db.update_league(league["id"], {"auto_send": True})
    demo_db.save_paper(league["id"], 3, 2025, "path", "url", {"headline": "CHAOS"})
    sent_emails.clear()

    report = send_weekly(demo_db, 3)
    assert report["emails_sent"] == 1
    assert "CHAOS" in sent_emails[0]["body"]


def test_weekly_send_is_idempotent(client, league, sent_emails):
    """A retrying cron must not mail everyone twice."""
    from web.tasks import send_weekly

    _subscribe_and_confirm(client, league)
    demo_db.update_league(league["id"], {"auto_send": True})
    demo_db.save_paper(league["id"], 3, 2025, "path", "url", {"headline": "X"})

    send_weekly(demo_db, 3)
    sent_emails.clear()
    second = send_weekly(demo_db, 3)

    assert second["emails_sent"] == 0
    assert sent_emails == []


def test_weekly_send_skips_leagues_without_auto_send(client, league, sent_emails):
    from web.tasks import send_weekly

    _subscribe_and_confirm(client, league)
    demo_db.save_paper(league["id"], 3, 2025, "path", "url", {})
    assert send_weekly(demo_db, 3)["leagues"] == 0


def test_weekly_email_carries_unsubscribe(client, league, sent_emails):
    from web.tasks import send_weekly

    _subscribe_and_confirm(client, league)
    demo_db.update_league(league["id"], {"auto_send": True})
    demo_db.save_paper(league["id"], 3, 2025, "path", "url", {})
    sent_emails.clear()

    send_weekly(demo_db, 3)
    assert sent_emails[0]["unsubscribe_url"] is not None
    assert "Unsubscribe" in sent_emails[0]["body"]


def test_weekly_task_endpoint_requires_the_key(client, league):
    assert client.post("/tasks/weekly", data={"week": 3}).status_code == 404


def test_weekly_task_endpoint_accepts_the_key(client, league, monkeypatch):
    monkeypatch.setenv("TASK_KEY", "hunter2")
    r = client.post("/tasks/weekly", data={"week": 3}, headers={"x-task-key": "hunter2"})
    assert r.status_code == 200
    assert r.json()["week"] == 3


# ---------------------------------------------------------------------------
# Slugs and tokens
# ---------------------------------------------------------------------------

def test_public_slug_is_readable_and_unique():
    a = slugs.public_slug("The Kevlarville Times")
    assert a.startswith("the-kevlarville-times-")
    assert a != slugs.public_slug("The Kevlarville Times")


def test_slugify_handles_junk():
    assert slugs.slugify("  Bob's !!! League  ") == "bob-s-league"
    assert slugs.slugify("") == "league"


def test_admin_token_is_unguessable():
    assert len(slugs.admin_token()) >= 30
    assert len({slugs.admin_token() for _ in range(100)}) == 100
