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
                  demo_db._STORAGE, demo_db._SUBSCRIBERS, demo_db._MAGIC_LINKS,
                  demo_db._RATE_EVENTS, demo_db._USERS,
                  demo_db._PUBLISHER_ADS, demo_db._MANAGERS):
        store.clear()
    monkeypatch.setattr(webapp, "db", demo_db)
    yield


@pytest.fixture
def client():
    return TestClient(webapp.app)


@pytest.fixture
def league():
    """A league on the PAID plan.

    Deliberately paid, because most of this file is about photo uploads,
    themes and regenerations — things the paywall gates but that are not what
    those tests are about. A free fixture would turn every one of them into a
    test of the paywall by accident, and they would stop covering what they
    were written to cover. The gates get their own tests, with `free_league`.
    """
    owner = demo_db.create_user("owner@example.com", "x")
    demo_db.set_plan(owner["id"], plan="paid", status="active")
    made = demo_db.create_league(
        provider="sleeper", platform_league_id="123",
        league_name="Kevlarville", paper_name="The Kevlarville Times",
        commissioner_name="johnhenryhammond", season=2025,
        public_slug="kevlarville-7f3a", admin_token="secret-admin-token",
    )
    demo_db.claim_league(made["id"], owner["id"])
    return demo_db.league_by_admin_token("secret-admin-token")


@pytest.fixture
def free_league():
    """The same league, owned by somebody who has not paid."""
    owner = demo_db.create_user("free@example.com", "x")
    made = demo_db.create_league(
        provider="sleeper", platform_league_id="789",
        league_name="Thriftville", paper_name="The Thriftville Times",
        commissioner_name="somebody", season=2025,
        public_slug="thriftville-1", admin_token="free-admin-token",
    )
    demo_db.claim_league(made["id"], owner["id"])
    return demo_db.league_by_admin_token("free-admin-token")


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


def _verify_ok(name="Kevlarville", season=2025, weeks=(1, 2, 3)):
    """Stub provider matching the real interface: describe_league + available_weeks."""
    from providers.models import League

    league = None if name is None else League(
        provider="sleeper", league_id="999", name=name, season=season,
        roster_slots=["QB", "RB", "BN"], team_count=10, status="in_season")

    class P:
        def describe_league(self, lid, s=None):
            return league

        def verify_league(self, lid, s=None):
            return league.name if league else None

        def available_weeks(self, lid, s):
            return list(weeks)

    return lambda provider, **kw: P()


# ---------------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------------

def test_landing_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    # The landing page now sells and points at signup. Connecting a league
    # happens after an account exists, where a username can replace the league
    # ID entirely.
    assert "/signup" in r.text


def test_landing_promises_readers_never_sign_up(client):
    """Commissioners have accounts now; readers still do not, and that is the
    load-bearing half of the pitch. If this line goes, check it went on
    purpose."""
    text = client.get("/").text.lower()
    assert "never sign" in text or "no sign-in" in text


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
    r = client.post("/leagues", data={"league_id": "bogus"})
    assert "Couldn&#39;t find that league" in r.text or "Couldn't find that league" in r.text


def test_duplicate_league_does_not_leak_admin_token(client, league, monkeypatch):
    """Anyone can read a league ID off a URL. It can't be proof of ownership."""
    monkeypatch.setattr(webapp, "get_provider", _verify_ok(season=2025))
    r = client.post("/leagues", data={"league_id": "123"})
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


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def test_creating_a_league_goes_to_setup_not_manage(client, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _verify_ok())
    r = client.post("/leagues", data={"league_id": "999", "season": 2025},
                    follow_redirects=False)
    assert r.headers["location"].endswith("/setup")


def test_setup_page_asks_only_what_the_api_cannot_answer(client, league):
    """Scoring, superflex and team count come from Sleeper. Asking again is a
    form for no reason."""
    text = client.get("/l/secret-admin-token/setup").text.lower()
    assert "dynasty" in text
    assert "punishment" in text
    assert "lore" in text
    assert "ppr" not in text
    assert "superflex" not in text
    assert "how many teams" not in text


def test_setup_saves_everything(client, league):
    client.post("/l/secret-admin-token/setup", data={
        "format": "dynasty", "tone": "brutal", "founded_year": "2014",
        "stakes": "$100 buy-in", "punishment": "Tattoo picked by the group chat",
        "lore": "Nick benches his best guy\nChamp sold his soul",
    })
    saved = demo_db._LEAGUES[league["id"]]
    assert saved["format"] == "dynasty"
    assert saved["tone"] == "brutal"
    assert saved["founded_year"] == 2014
    assert saved["punishment"].startswith("Tattoo")
    assert saved["setup_complete"] is True
    assert len(demo_db.get_lore(league["id"])) == 2


def test_setup_lore_accepts_a_pasted_bulleted_list(client, league):
    client.post("/l/secret-admin-token/setup", data={
        "lore": "- Nick benches his best guy\n• Champ sold his soul\n\n  \n* Dave never wins",
    })
    entries = sorted(e["entry"] for e in demo_db.get_lore(league["id"]))
    assert entries == ["Champ sold his soul", "Dave never wins", "Nick benches his best guy"]


def test_setup_rejects_junk_values(client, league):
    client.post("/l/secret-admin-token/setup", data={
        "format": "sqlinjection", "tone": "nuclear", "founded_year": "banana",
    })
    saved = demo_db._LEAGUES[league["id"]]
    assert saved["format"] == "redraft"
    assert saved["tone"] == "standard"
    assert saved["founded_year"] is None


def test_setup_can_be_skipped(client, league):
    r = client.post("/l/secret-admin-token/skip-setup", follow_redirects=False)
    assert r.headers["location"] == "/l/secret-admin-token?new=1"
    assert demo_db._LEAGUES[league["id"]]["setup_complete"] is True


def test_setup_requires_the_token(client, league):
    assert client.get("/l/wrong/setup").status_code == 404


# ---------------------------------------------------------------------------
# Generate lands on the paper
# ---------------------------------------------------------------------------

def test_generate_redirects_to_the_published_paper(client, league, monkeypatch):
    """Waiting 30 seconds and being handed a URL to click is a bad payoff."""
    def fake_generate(db_, lg, week):
        db_.upload_paper(lg["public_slug"], lg["season"], week, "<h1>PAPER</h1>")
        db_.save_paper(lg["id"], week, lg["season"], "p", "u", {})
        return {}

    monkeypatch.setattr(webapp, "generate_and_store", fake_generate)
    r = client.post("/l/secret-admin-token/generate", data={"week": 3},
                    follow_redirects=False)
    assert r.headers["location"] == "/l/secret-admin-token/published/3"


def test_published_page_frames_the_paper(client, league):
    demo_db.save_paper(league["id"], 3, 2025, "p", "u", {})
    demo_db._STORAGE[demo_db.storage_path(league["public_slug"], 2025, 3)] = "<h1>P</h1>"
    r = client.get("/l/secret-admin-token/published/3")
    assert r.status_code == 200
    assert "<iframe" in r.text
    assert f"/p/{league['public_slug']}/2025/week-3" in r.text


def test_published_page_has_a_copy_button(client, league):
    demo_db.save_paper(league["id"], 3, 2025, "p", "u", {})
    r = client.get("/l/secret-admin-token/published/3")
    assert "copy-btn" in r.text
    assert "clipboard" in r.text


def test_published_page_requires_the_token(client, league):
    demo_db.save_paper(league["id"], 3, 2025, "p", "u", {})
    assert client.get("/l/wrong/published/3").status_code == 404


def test_unpublished_week_bounces_back_to_manage(client, league):
    r = client.get("/l/secret-admin-token/published/9", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/l/secret-admin-token?error=")


# ---------------------------------------------------------------------------
# League context reaches the writer
# ---------------------------------------------------------------------------

def test_dynasty_and_redraft_produce_different_instructions():
    from web.generate import build_league_context

    dynasty = build_league_context({"format": "dynasty"}, [])
    redraft = build_league_context({"format": "redraft"}, [])
    assert "DYNASTY" in dynasty
    assert "rookie" in dynasty.lower()
    assert "Do NOT" in redraft


def test_punishment_is_flagged_as_prime_material():
    from web.generate import build_league_context

    ctx = build_league_context(
        {"format": "redraft", "punishment": "Has to get a tattoo"}, [])
    assert "LAST PLACE PUNISHMENT" in ctx
    assert "tattoo" in ctx


def test_lore_reaches_the_context():
    from web.generate import build_league_context

    ctx = build_league_context({"format": "redraft"},
                               [{"entry": "Nick benches his best guy"}])
    assert "Nick benches his best guy" in ctx


# ---------------------------------------------------------------------------
# The people in the league
#
# Two separate failures this fixes. The paper printed `alexvierheilig4` in the
# middle of sentences, because a handle was the only name anything had. And
# lore was one flat list, so "Nick benches his best guy every week" had to fire
# as a league-wide rule or not at all.
# ---------------------------------------------------------------------------

def _games(*pairs):
    """Minimal legacy `games` — only the two fields the directory reads."""
    return [{"team_1": {"owner_name": a, "team_name": f"{a}'s Team"},
             "team_2": {"owner_name": b, "team_name": f"{b}'s Team"}}
            for a, b in pairs]


def test_the_directory_is_the_people_not_the_teams():
    """Teams get renamed mid-season and two people can pick the same name.
    The row has to key on the person, which is `owner_name`."""
    from web.generate import manager_directory

    found = manager_directory(_games(("alexvierheilig4", "WillDavidson10")))
    assert [m["handle"] for m in found] == ["alexvierheilig4", "WillDavidson10"]
    assert found[0]["team_name"] == "alexvierheilig4's Team"


def test_the_directory_drops_blanks_and_repeats():
    """A bye week puts the same person in two rows in some feeds, and a
    provider with no owner data hands over "". Neither can become a box."""
    from web.generate import manager_directory

    found = manager_directory(_games(("mike", "will"), ("mike", ""), ("", "")))
    assert [m["handle"] for m in found] == ["mike", "will"]


def test_a_real_name_reaches_the_writer():
    from web.generate import build_manager_context

    ctx = build_manager_context([
        {"handle": "alexvierheilig4", "display_name": "Alex", "notes": ""},
    ])
    assert "alexvierheilig4" in ctx
    assert "is Alex" in ctx


def test_personal_notes_reach_the_writer_and_league_lore_still_does():
    """They are different things and both have to arrive. The per-person note
    is true every week; league lore fires when something triggers it."""
    from web.generate import build_league_context

    ctx = build_league_context(
        {"format": "redraft"},
        [{"entry": "Zero from a starter means you owe a drink"}],
        [{"handle": "nick", "display_name": "Nick",
          "notes": "benches his best guy every single week"}],
    )
    assert "owe a drink" in ctx
    assert "benches his best guy" in ctx


def test_the_team_name_is_attached_so_a_person_can_be_matched_to_a_side():
    """The game context names TEAMS. Without this line the writer is handed a
    note about `nick` and no way to know which of ten teams that is."""
    from web.generate import build_manager_context

    ctx = build_manager_context(
        [{"handle": "nick", "display_name": "Nick", "notes": "x"}],
        {"nick": "Regular Season Champs"},
    )
    assert "Regular Season Champs" in ctx


def test_people_with_nothing_filled_in_are_not_sent_at_all():
    """Every league mate gets a row the first time a paper generates, so most
    leagues will have twelve rows of nothing. Shipping twelve handles and no
    facts costs tokens on every call and tells the writer nothing."""
    from web.generate import build_manager_context

    assert build_manager_context([{"handle": "a"}, {"handle": "b"}]) == ""
    assert build_manager_context([]) == ""
    assert build_manager_context(None) == ""


def test_only_a_bounded_number_of_people_reach_the_prompt():
    from web.generate import MAX_MANAGERS_IN_PROMPT, build_manager_context

    ctx = build_manager_context(
        [{"handle": f"h{i}", "notes": "a note"} for i in range(60)])
    assert ctx.count("- h") == MAX_MANAGERS_IN_PROMPT


def test_the_manage_page_shows_a_box_for_each_person(client, league):
    demo_db.remember_managers(league["id"], ["mikevidan3", "WillDavidson10"])
    text = client.get("/l/secret-admin-token").text

    assert "mikevidan3" in text
    assert "WillDavidson10" in text
    assert text.count('name="notes"') == 2


def test_saving_the_page_writes_names_and_notes(client, league):
    demo_db.remember_managers(league["id"], ["mikevidan3", "WillDavidson10"])

    client.post("/l/secret-admin-token/managers", data={
        "handle": ["mikevidan3", "WillDavidson10"],
        "display_name": ["Mike", "Will"],
        "notes": ["drafts a kicker in the seventh", ""],
    }, follow_redirects=False)

    saved = {m["handle"]: m for m in demo_db.get_managers(league["id"])}
    assert saved["mikevidan3"]["display_name"] == "Mike"
    assert saved["mikevidan3"]["notes"] == "drafts a kicker in the seventh"
    assert saved["WillDavidson10"]["display_name"] == "Will"
    assert saved["WillDavidson10"]["notes"] is None


def test_a_ragged_submission_writes_nothing_rather_than_the_wrong_person(
        client, league):
    """The three lists are paired by position. If they ever arrive at
    different lengths the pairing is a guess, and the guess puts one person's
    lore under another person's name — in a paper their friends read."""
    demo_db.remember_managers(league["id"], ["mikevidan3", "WillDavidson10"])

    r = client.post("/l/secret-admin-token/managers", data={
        "handle": ["mikevidan3", "WillDavidson10"],
        "display_name": ["Mike"],
        "notes": ["drafts a kicker in the seventh", "fine"],
    }, follow_redirects=False)

    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert all(m["display_name"] is None and m["notes"] is None
               for m in demo_db.get_managers(league["id"]))


def test_a_handle_from_another_league_cannot_be_written_through_this(
        client, league):
    other = demo_db.create_league(
        provider="sleeper", platform_league_id="456", league_name="Other",
        paper_name="The Other Times", commissioner_name="x", season=2025,
        public_slug="other-1", admin_token="other-token")
    demo_db.remember_managers(other["id"], ["victim"])

    client.post("/l/secret-admin-token/managers", data={
        "handle": ["victim"], "display_name": ["Owned"], "notes": ["hi"],
    }, follow_redirects=False)

    assert demo_db.get_managers(other["id"])[0]["display_name"] is None
    assert demo_db.get_managers(league["id"]) == []


def test_the_boxes_appear_before_the_first_paper_is_generated(
        client, league, monkeypatch):
    """"Once it's in, you should be able to fill in a box for each league
    mate." A commissioner who just connected their league has generated
    nothing, so nothing has told us who is in it yet. The first visit asks."""
    import web.generate as generate

    monkeypatch.setattr(webapp, "get_provider", _verify_ok(weeks=(1, 2)))
    monkeypatch.setattr(generate, "load_week", lambda *a, **k: "week")
    monkeypatch.setattr(generate, "week_to_legacy_games",
                        lambda _w: _games(("mikevidan3", "WillDavidson10")))

    text = client.get("/l/secret-admin-token").text

    assert "mikevidan3" in text
    assert text.count('name="notes"') == 2
    assert len(demo_db.get_managers(league["id"])) == 2


def test_seeding_never_takes_the_manage_page_down(client, league, monkeypatch):
    """It is a network call to somebody else's API on a page the commissioner
    needs in order to do anything at all."""
    import web.generate as generate

    def boom(*a, **k):
        raise RuntimeError("ESPN is having a day")

    monkeypatch.setattr(webapp, "get_provider", _verify_ok(weeks=(1, 2)))
    monkeypatch.setattr(generate, "load_week", boom)

    assert client.get("/l/secret-admin-token").status_code == 200


def test_seeding_does_not_re_fetch_once_anybody_is_known(
        client, league, monkeypatch):
    """Otherwise every load of the manage page pulls a full week from the
    provider, forever."""
    import web.generate as generate

    calls = []

    demo_db.remember_managers(league["id"], ["mikevidan3"])
    monkeypatch.setattr(webapp, "get_provider", _verify_ok(weeks=(1, 2)))
    monkeypatch.setattr(generate, "load_week",
                        lambda *a, **k: calls.append(a))

    text = client.get("/l/secret-admin-token").text

    assert calls == [], "fetched the week when it already knew everybody"
    assert "mikevidan3" in text


def test_generating_keeps_the_list_of_people_current_and_sends_their_lore(
        league, monkeypatch):
    """The end of the wire, in one test: the week's data is the only place the
    app ever holds the real list of who is in this league, so generation is
    what keeps the boxes current — and what anybody has typed into those boxes
    has to come back out in the prompt."""
    import web.generate as generate

    demo_db.remember_managers(league["id"], ["mikevidan3"])
    demo_db.save_manager(league["id"], "mikevidan3", "Mike",
                         "drafts a kicker in the seventh")

    sent = {}

    monkeypatch.setattr(generate, "load_week", lambda *a, **k: "week")
    monkeypatch.setattr(generate, "week_to_legacy_games",
                        lambda _w: _games(("mikevidan3", "newguy")))
    monkeypatch.setattr(generate, "get_weekly_storylines", lambda _g: {})
    monkeypatch.setattr(generate, "generate_full_newspaper_content",
                        lambda **kw: sent.update(kw) or {})
    monkeypatch.setattr(generate, "render_and_store",
                        lambda *a, **k: {"week": 1})

    generate.generate_and_store(demo_db, league, 1)

    handles = {m["handle"] for m in demo_db.get_managers(league["id"])}
    assert handles == {"mikevidan3", "newguy"}, "a new team got no box"

    context = sent["inside_jokes"]
    assert "is Mike" in context
    assert "drafts a kicker in the seventh" in context
    assert "mikevidan3's Team" in context, "no way to tell which team is his"


def test_a_week_that_adds_a_team_adds_a_box_without_wiping_anything(league):
    """Leagues expand, and people rename themselves mid-season. The list has
    to keep up — and it runs on every generation, so it must never overwrite
    what the commissioner typed."""
    demo_db.remember_managers(league["id"], ["mikevidan3"])
    demo_db.save_manager(league["id"], "mikevidan3", "Mike", "drafts a kicker")

    demo_db.remember_managers(league["id"], ["mikevidan3", "newguy"])

    saved = {m["handle"]: m for m in demo_db.get_managers(league["id"])}
    assert set(saved) == {"mikevidan3", "newguy"}
    assert saved["mikevidan3"]["notes"] == "drafts a kicker"


# ---------------------------------------------------------------------------
# Deleting a league
#
# The manage link is a bearer token that gets pasted into group chats and left
# open in tabs. A delete behind it has to be hard to do by accident and total
# when it happens.
# ---------------------------------------------------------------------------

def test_deleting_takes_the_league_and_everything_under_it(client, league):
    demo_db.add_lore(league["id"], "Nick benches his best guy")
    demo_db.remember_managers(league["id"], ["mikevidan3"])
    demo_db.subscribe(league["id"], "reader@example.com")
    demo_db.save_paper(league["id"], 1, 2025, "kevlarville-7f3a/2025/week-01.html",
                       "http://example/p", {})

    r = client.post("/l/secret-admin-token/delete",
                    data={"confirm": "Kevlarville"})

    assert r.status_code == 200
    assert demo_db.league_by_admin_token("secret-admin-token") is None
    assert demo_db.list_papers(league["id"]) == []
    assert demo_db.get_lore(league["id"]) == []
    assert demo_db.get_managers(league["id"]) == []
    assert demo_db.subscriber_count(league["id"]) == 0


def test_the_published_papers_come_down_too(client, league):
    """They are world-readable at URLs people have already shared. A delete
    that leaves the editions up is not a delete."""
    path = "kevlarville-7f3a/2025/week-01.html"
    demo_db.upload_paper("kevlarville-7f3a", 2025, 1, "<html>the paper</html>")
    demo_db.save_paper(league["id"], 1, 2025, path, "http://example/p", {})

    client.post("/l/secret-admin-token/delete", data={"confirm": "Kevlarville"})

    assert demo_db.download_paper(path) is None


def test_the_wrong_name_deletes_nothing(client, league):
    r = client.post("/l/secret-admin-token/delete",
                    data={"confirm": "kevlarvile"}, follow_redirects=False)

    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert demo_db.league_by_admin_token("secret-admin-token") is not None


def test_an_empty_confirmation_deletes_nothing(client, league):
    """A form posted with nothing in it must not read as agreement — and a
    league whose name is somehow blank must not become one-click deletable."""
    for junk in ("", "   "):
        client.post("/l/secret-admin-token/delete", data={"confirm": junk},
                    follow_redirects=False)
        assert demo_db.league_by_admin_token("secret-admin-token") is not None


def test_deleting_needs_the_admin_token(client, league):
    r = client.post("/l/not-the-token/delete", data={"confirm": "Kevlarville"})
    assert r.status_code == 404
    assert demo_db.league_by_admin_token("secret-admin-token") is not None


def test_the_name_is_matched_forgivingly(client, league):
    """Typing it is the safeguard. Capitalising it the same way is not."""
    r = client.post("/l/secret-admin-token/delete",
                    data={"confirm": "  kevlarville "})
    assert demo_db.league_by_admin_token("secret-admin-token") is None
    assert r.status_code == 200


def test_the_delete_form_says_what_to_type(client, league):
    text = client.get("/l/secret-admin-token").text
    assert "Delete this league" in text
    assert "Kevlarville" in text


def test_deleting_one_league_leaves_the_others_alone(client, league):
    other = demo_db.create_league(
        provider="sleeper", platform_league_id="456", league_name="Other",
        paper_name="The Other Times", commissioner_name="x", season=2025,
        public_slug="other-1", admin_token="other-token")
    demo_db.add_lore(other["id"], "keep me")
    demo_db.remember_managers(other["id"], ["someone"])

    client.post("/l/secret-admin-token/delete", data={"confirm": "Kevlarville"})

    assert demo_db.league_by_admin_token("other-token") is not None
    assert len(demo_db.get_lore(other["id"])) == 1
    assert len(demo_db.get_managers(other["id"])) == 1


def _system_text(*args, **kwargs):
    """system_prompt returns API blocks now, not a string — see the docstring
    on it for why. Tests care about the words, so flatten them."""
    from writer import system_prompt

    return "".join(b["text"] for b in system_prompt(*args, **kwargs))


def test_tone_changes_the_system_prompt():
    assert "NO MERCY" in _system_text("brutal")
    assert "KEEP IT LIGHT" in _system_text("friendly")
    assert "No profanity" in _system_text("friendly")
    assert _system_text("standard") == _system_text(None)


def test_settings_page_can_edit_setup_answers(client, league):
    client.post("/l/secret-admin-token/settings", data={
        "paper_name": "The Kevlarville Times", "commissioner": "john",
        "format": "dynasty", "tone": "friendly",
        "stakes": "$50", "punishment": "Wears a dress to the draft",
    })
    saved = demo_db._LEAGUES[league["id"]]
    assert saved["format"] == "dynasty"
    assert saved["tone"] == "friendly"
    assert saved["punishment"] == "Wears a dress to the draft"


# ---------------------------------------------------------------------------
# Season comes from the platform, not from the user
# ---------------------------------------------------------------------------

def _describe(name="Kevlarville", season=2025, status="in_season"):
    from providers.models import League
    league = League(provider="sleeper", league_id="999", name=name, season=season,
                    roster_slots=["QB", "RB", "BN"], team_count=10, status=status)

    class P:
        def describe_league(self, lid, s=None):
            return league

        def available_weeks(self, lid, s):
            return [1, 2, 3]

    return lambda provider, **kw: P()


def test_landing_no_longer_asks_for_a_season(client):
    """The league knows its own season. Asking invites a wrong answer, and the
    symptom is a confusing 'no data for that week' three screens later."""
    text = client.get("/").text
    assert 'name="season"' not in text


def test_season_is_taken_from_the_platform(client, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _describe(season=2024))
    client.post("/leagues", data={"league_id": "999"})
    saved = next(iter(demo_db._LEAGUES.values()))
    assert saved["season"] == 2024


def test_unknown_league_gets_a_useful_message(client, monkeypatch):
    class P:
        def describe_league(self, lid, s=None):
            return None

    monkeypatch.setattr(webapp, "get_provider", lambda provider, **kw: P())
    r = client.post("/leagues", data={"league_id": "nope"})
    assert "long number" in r.text


# ---------------------------------------------------------------------------
# Week picker only offers weeks that exist
# ---------------------------------------------------------------------------

def test_manage_offers_only_weeks_with_results(client, league, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _describe())
    r = client.get("/l/secret-admin-token")
    assert "<select name=\"week\">" in r.text
    assert "Week 3" in r.text
    assert "Week 9" not in r.text


def test_manage_explains_when_no_week_can_be_generated(client, league, monkeypatch):
    class P:
        def available_weeks(self, lid, s):
            return []

    monkeypatch.setattr(webapp, "get_provider", lambda provider, **kw: P())
    r = client.get("/l/secret-admin-token")
    assert "Nothing to write about yet" in r.text
    assert "haven&#39;t drafted" in r.text or "haven't drafted" in r.text


def test_manage_survives_a_provider_outage(client, league, monkeypatch):
    """A dead API shouldn't take down the whole manage page."""
    class P:
        def available_weeks(self, lid, s):
            raise RuntimeError("sleeper is down")

    monkeypatch.setattr(webapp, "get_provider", lambda provider, **kw: P())
    assert client.get("/l/secret-admin-token").status_code == 200


# ---------------------------------------------------------------------------
# The error message actually diagnoses
# ---------------------------------------------------------------------------

def test_predraft_league_says_so(monkeypatch, tmp_path):
    from providers import SleeperProvider, TTLCache
    from providers.models import League

    p = SleeperProvider(cache=TTLCache(cache_dir=tmp_path, namespace="t"))
    league = League(provider="sleeper", league_id="1", name="Brand New League",
                    season=2026, status="pre_draft")
    msg = p._explain_missing_week(league, 1)
    assert "hasn't drafted" in msg
    assert "Brand New League" in msg


def test_played_league_lists_the_weeks_that_work(monkeypatch, tmp_path):
    from providers import SleeperProvider, TTLCache
    from providers.models import League

    p = SleeperProvider(cache=TTLCache(cache_dir=tmp_path, namespace="t"))
    monkeypatch.setattr(p, "available_weeks", lambda lid, s: [1, 2, 3])
    league = League(provider="sleeper", league_id="1", name="Kevlarville",
                    season=2025, status="in_season")
    msg = p._explain_missing_week(league, 9)
    assert "Week 9" in msg
    assert "1, 2, 3" in msg


def test_drafted_but_unplayed_league_says_so(monkeypatch, tmp_path):
    from providers import SleeperProvider, TTLCache
    from providers.models import League

    p = SleeperProvider(cache=TTLCache(cache_dir=tmp_path, namespace="t"))
    monkeypatch.setattr(p, "available_weeks", lambda lid, s: [])
    league = League(provider="sleeper", league_id="1", name="Kevlarville",
                    season=2025, status="in_season")
    assert "no week has been scored" in p._explain_missing_week(league, 1).lower()


# ---------------------------------------------------------------------------
# Offseason: follow the league's previous season
#
# Sleeper mints a new league id every year. Someone signing up in August pastes
# this year's empty pre-draft shell while last season sits one hop back — which
# is exactly what happened to Kevlarville 2026.
# ---------------------------------------------------------------------------

def _chain_provider(current_has_weeks=False):
    from providers.models import League

    current = League(provider="sleeper", league_id="2026id", name="Kevlarville",
                     season=2026, status="pre_draft",
                     previous_league_id="2025id")
    previous = League(provider="sleeper", league_id="2025id", name="Kevlarville",
                      season=2025, status="complete")

    class P:
        def describe_league(self, lid, s=None):
            return current if lid == "2026id" else previous

        def season_chain(self, lid, max_hops=10):
            return [current, previous]

        def available_weeks(self, lid, s):
            if lid == "2025id":
                return [1, 2, 3, 4]
            return [1] if current_has_weeks else []

    return lambda provider, **kw: P()


@pytest.fixture
def predraft_league():
    return demo_db.create_league(
        provider="sleeper", platform_league_id="2026id",
        league_name="Kevlarville", paper_name="The Kevlarville Times",
        commissioner_name="john", season=2026,
        public_slug="kevlarville-7f3a", admin_token="secret-admin-token",
    )


def test_predraft_league_is_offered_last_season(client, predraft_league, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _chain_provider())
    r = client.get("/l/secret-admin-token")
    assert "hasn&#39;t been played yet" in r.text or "hasn't been played yet" in r.text
    assert "Use the 2025 season" in r.text
    assert "4 weeks" in r.text


def test_switching_to_the_previous_season(client, predraft_league, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _chain_provider())
    client.post("/l/secret-admin-token/use-season",
                data={"platform_league_id": "2025id", "season": 2025})
    saved = demo_db._LEAGUES[predraft_league["id"]]
    assert saved["platform_league_id"] == "2025id"
    assert saved["season"] == 2025


def test_cannot_repoint_a_paper_at_an_unrelated_league(client, predraft_league, monkeypatch):
    """Otherwise this endpoint hijacks any league you know the id of."""
    monkeypatch.setattr(webapp, "get_provider", _chain_provider())
    r = client.post("/l/secret-admin-token/use-season",
                    data={"platform_league_id": "999999", "season": 2024},
                    follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert demo_db._LEAGUES[predraft_league["id"]]["platform_league_id"] == "2026id"


def test_use_season_requires_the_token(client, predraft_league, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _chain_provider())
    r = client.post("/l/wrong/use-season",
                    data={"platform_league_id": "2025id", "season": 2025},
                    follow_redirects=False)
    assert r.status_code == 404


def test_no_offer_when_the_current_season_already_works(client, predraft_league, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _chain_provider(current_has_weeks=True))
    r = client.get("/l/secret-admin-token")
    assert "Use the 2025 season" not in r.text
    assert '<select name="week">' in r.text


# ---------------------------------------------------------------------------
# Edit mode
#
# The paper renders from ai_cache, so editing means changing that JSON and
# re-rendering. No Claude call, no cost.
# ---------------------------------------------------------------------------

SAMPLE_AI = {
    "headline": "SATAN FALLS IN KEVLARVILLE",
    "lead_story": "Week one delivered chaos.",
    "fraud_watch": "champayyy is a fraud.",
    "matchup_content": [
        {"winner": "john", "loser": "champ", "headline": "JOHN WINS",
         "body": "It was close.", "teaser": "Commissioner prevails",
         "winner_score": 120.4, "loser_score": 107.8,
         "winner_record": "1-0", "loser_record": "0-1",
         "winner_lineup_gap": 16.6, "loser_lineup_gap": 5.4, "margin": 12.6},
    ],
    "awards": [
        {"title": "GARDNER MINSHEW AWARD", "body": "Left 43 points on the bench."},
    ],
    "power_rankings_comments": {"john": "Untouchable.", "champ": "Cooked."},
}


@pytest.fixture
def paper(league):
    demo_db.upload_paper(league["public_slug"], 2025, 1, "<h1>P</h1>")
    demo_db.save_paper(league["id"], 1, 2025, "p", "u", dict(SAMPLE_AI))
    return demo_db.get_paper(league["id"], 2025, 1)


@pytest.fixture
def no_rerender(monkeypatch):
    """Skip the provider round-trip; we're testing the edit plumbing."""
    calls = []

    def fake(db_, lg, week, ai_content, *, is_edit=False):
        calls.append({"week": week, "ai": ai_content, "is_edit": is_edit})
        db_.save_paper(lg["id"], week, lg["season"], "p", "u", ai_content,
                       is_edit=is_edit)
        return {}

    monkeypatch.setattr(webapp, "render_and_store", fake)
    return calls


def test_editor_shows_every_piece_of_prose(client, paper):
    r = client.get("/l/secret-admin-token/edit/1")
    assert r.status_code == 200
    assert "SATAN FALLS IN KEVLARVILLE" in r.text
    assert "Week one delivered chaos." in r.text
    assert "It was close." in r.text
    assert "Left 43 points on the bench." in r.text
    assert "Untouchable." in r.text
    assert "champayyy is a fraud." in r.text


def test_editor_requires_the_token(client, paper):
    assert client.get("/l/wrong/edit/1").status_code == 404


def test_editing_a_week_that_does_not_exist(client, league):
    r = client.get("/l/secret-admin-token/edit/9", follow_redirects=False)
    assert r.status_code == 303
    assert "Nothing+to+edit" in r.headers["location"]


def test_saving_an_edit_changes_the_prose(client, league, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1", data={
        "headline": "ACTUALLY A DIFFERENT HEADLINE",
        "lead_story": "Rewritten by hand.",
        "fraud_watch": "Nobody is a fraud.",
        "matchup_headline_0": "NEW MATCHUP HEADLINE",
        "matchup_body_0": "New body text.",
        "matchup_teaser_0": "New teaser",
        "award_title_0": "THE NEW AWARD",
        "award_body_0": "New award text.",
        "ranking_value_0": "Still untouchable.",
        "ranking_value_1": "Still cooked.",
    })
    ai = no_rerender[0]["ai"]
    assert ai["headline"] == "ACTUALLY A DIFFERENT HEADLINE"
    assert ai["lead_story"] == "Rewritten by hand."
    assert ai["matchup_content"][0]["headline"] == "NEW MATCHUP HEADLINE"
    assert ai["matchup_content"][0]["body"] == "New body text."
    assert ai["awards"][0]["title"] == "THE NEW AWARD"
    assert ai["power_rankings_comments"]["john"] == "Still untouchable."


def test_editing_preserves_fields_the_form_never_touches(client, paper, no_rerender):
    """Scores and team names aren't editable and must survive a save."""
    client.post("/l/secret-admin-token/edit/1", data={"headline": "NEW"})
    ai = no_rerender[0]["ai"]
    assert ai["matchup_content"][0]["winner_score"] == 120.4
    assert ai["matchup_content"][0]["winner"] == "john"


def test_saving_marks_the_paper_as_edited(client, league, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1", data={"headline": "NEW"})
    assert no_rerender[0]["is_edit"] is True
    saved = demo_db.get_paper(league["id"], 2025, 1)
    assert saved["edited_at"] is not None


def test_the_original_wording_is_kept(client, league, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1", data={"headline": "NEW"})
    saved = demo_db.get_paper(league["id"], 2025, 1)
    assert saved["ai_cache"]["headline"] == "NEW"
    assert saved["ai_cache_original"]["headline"] == "SATAN FALLS IN KEVLARVILLE"


def test_editing_twice_does_not_clobber_the_original(client, league, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1", data={"headline": "FIRST EDIT"})
    client.post("/l/secret-admin-token/edit/1", data={"headline": "SECOND EDIT"})
    saved = demo_db.get_paper(league["id"], 2025, 1)
    assert saved["ai_cache"]["headline"] == "SECOND EDIT"
    assert saved["ai_cache_original"]["headline"] == "SATAN FALLS IN KEVLARVILLE"


def test_reverting_restores_claudes_words(client, league, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1", data={"headline": "NEW"})
    client.post("/l/secret-admin-token/edit/1/revert")
    saved = demo_db.get_paper(league["id"], 2025, 1)
    assert saved["ai_cache"]["headline"] == "SATAN FALLS IN KEVLARVILLE"
    assert saved["edited_at"] is None


def test_saving_redirects_to_the_paper(client, paper, no_rerender):
    r = client.post("/l/secret-admin-token/edit/1", data={"headline": "NEW"},
                    follow_redirects=False)
    assert r.headers["location"] == "/l/secret-admin-token/published/1"


def test_edits_cannot_be_saved_without_the_token(client, paper, no_rerender):
    r = client.post("/l/wrong/edit/1", data={"headline": "NEW"},
                    follow_redirects=False)
    assert r.status_code == 404
    assert no_rerender == []


# --- regeneration must not silently destroy edits --------------------------

def test_regenerating_an_edited_week_is_blocked(client, league, paper, no_rerender,
                                                monkeypatch):
    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda *a, **k: pytest.fail("should not have regenerated"))
    client.post("/l/secret-admin-token/edit/1", data={"headline": "NEW"})

    r = client.post("/l/secret-admin-token/generate", data={"week": 1},
                    follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert "wipe" in r.headers["location"]


def test_regenerating_an_edited_week_is_allowed_when_confirmed(client, league, paper,
                                                               no_rerender, monkeypatch):
    ran = []
    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda db_, lg, wk: ran.append(wk))
    client.post("/l/secret-admin-token/edit/1", data={"headline": "NEW"})

    client.post("/l/secret-admin-token/generate",
                data={"week": 1, "confirm_overwrite": "yes"})
    assert ran == [1]


def test_regenerating_an_unedited_week_needs_no_confirmation(client, league, paper,
                                                             monkeypatch):
    ran = []
    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda db_, lg, wk: ran.append(wk))
    client.post("/l/secret-admin-token/generate", data={"week": 1})
    assert ran == [1]


def test_published_page_offers_both_editors(client, paper):
    r = client.get("/l/secret-admin-token/published/1")
    assert "/live-edit/1" in r.text
    assert "/edit/1" in r.text
    assert "Edit on the page" in r.text


# ---------------------------------------------------------------------------
# Inline editing on the rendered page
# ---------------------------------------------------------------------------

@pytest.fixture
def no_editable_render(monkeypatch):
    """Skip the provider round-trip when rendering the editable view."""
    monkeypatch.setattr(
        webapp, "render_editable",
        lambda db_, lg, wk, ai: (
            "<html><body>"
            '<div class="headline" data-edit-key="headline" contenteditable="true">H</div>'
            '<div data-image-slot="hero" class="image-slot-empty">Click to add a photo</div>'
            "</body></html>"
        ),
    )


def test_live_editor_serves_an_editable_page(client, paper, no_editable_render):
    r = client.get("/l/secret-admin-token/live-edit/1")
    assert r.status_code == 200
    assert 'contenteditable="true"' in r.text
    assert "data-edit-key" in r.text


def test_live_editor_injects_the_toolbar(client, paper, no_editable_render):
    r = client.get("/l/secret-admin-token/live-edit/1")
    assert "liveedit.js" in r.text
    assert "ce-config" in r.text
    assert "/edit/1/inline" in r.text
    assert "/upload-image" in r.text


def test_live_editor_is_never_cached(client, paper, no_editable_render):
    r = client.get("/l/secret-admin-token/live-edit/1")
    assert r.headers["cache-control"] == "no-store"


def test_live_editor_requires_the_token(client, paper, no_editable_render):
    assert client.get("/l/wrong/live-edit/1").status_code == 404


def test_the_published_paper_is_never_editable(client, league, paper):
    """The whole safety property: readers must never get edit hooks."""
    demo_db._STORAGE[demo_db.storage_path(league["public_slug"], 2025, 1)] = \
        "<html><body><div class='headline'>H</div></body></html>"
    r = client.get(f"/p/{league['public_slug']}/2025/week-1")
    assert "contenteditable" not in r.text
    assert "data-edit-key" not in r.text
    assert "liveedit.js" not in r.text


def test_inline_save_applies_text_edits(client, league, paper, no_rerender):
    r = client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {
            "headline": "INLINE HEADLINE",
            "matchup_body_0": "<p>Rewritten in place.</p>",
            "award_title_0": "NEW AWARD",
            "ranking:john": "Edited on the page.",
        },
        "images": {},
    })
    assert r.status_code == 200
    ai = no_rerender[0]["ai"]
    assert ai["headline"] == "INLINE HEADLINE"
    assert ai["matchup_content"][0]["body"] == "<p>Rewritten in place.</p>"
    assert ai["awards"][0]["title"] == "NEW AWARD"
    assert ai["power_rankings_comments"]["john"] == "Edited on the page."


def test_inline_save_stores_photos(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {},
        "images": {"hero": "https://cdn.example/photo.jpg"},
    })
    assert no_rerender[0]["ai"]["images"]["hero"]["url"] == "https://cdn.example/photo.jpg"


def test_inline_save_ignores_unknown_keys(client, paper, no_rerender):
    """The payload comes from a browser; only fields the paper renders count."""
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {"admin_token": "hijacked", "league_id": "1", "matchup_body_99": "x"},
        "images": {},
    })
    ai = no_rerender[0]["ai"]
    assert "admin_token" not in ai
    assert "league_id" not in ai
    assert len(ai["matchup_content"]) == 1


def test_inline_save_will_not_invent_a_ranking(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {"ranking:not-a-real-team": "sneaky"},
        "images": {},
    })
    assert "not-a-real-team" not in no_rerender[0]["ai"]["power_rankings_comments"]


def test_inline_save_marks_the_paper_edited(client, league, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline",
                json={"edits": {"headline": "X"}, "images": {}})
    assert no_rerender[0]["is_edit"] is True
    assert demo_db.get_paper(league["id"], 2025, 1)["edited_at"] is not None


def test_inline_save_requires_the_token(client, paper, no_rerender):
    r = client.post("/l/wrong/edit/1/inline",
                    json={"edits": {"headline": "X"}, "images": {}})
    assert r.status_code == 404
    assert no_rerender == []


# --- photo upload ----------------------------------------------------------
#
# Uploads are now judged by their bytes, not by what the caller claims. These
# payloads carry real magic numbers because that is what the server reads.

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 24
GIF_BYTES = b"GIF89a" + b"\x00" * 24
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20


def test_uploading_a_photo(client, league):
    r = client.post("/l/secret-admin-token/upload-image",
                    files={"photo": ("shot.png", PNG_BYTES, "image/png")})
    assert r.status_code == 200
    assert r.json()["url"].startswith("/demo-image/")


def test_uploaded_photo_is_served_back(client, league):
    url = client.post("/l/secret-admin-token/upload-image",
                      files={"photo": ("shot.png", PNG_BYTES, "image/png")}).json()["url"]
    r = client.get(url)
    assert r.status_code == 200
    assert r.content == PNG_BYTES


def test_every_allowed_format_is_accepted(client, league):
    for name, payload in [("a.jpg", JPEG_BYTES), ("b.png", PNG_BYTES),
                          ("c.gif", GIF_BYTES), ("d.webp", WEBP_BYTES)]:
        r = client.post("/l/secret-admin-token/upload-image",
                        files={"photo": (name, payload, "image/png")})
        assert r.status_code == 200, name


def test_non_images_are_rejected(client, league):
    r = client.post("/l/secret-admin-token/upload-image",
                    files={"photo": ("evil.html", b"<script>alert(1)</script>xx",
                                     "text/html")})
    assert r.status_code == 400


def test_svg_is_rejected_even_when_declared_an_image(client, league):
    """The hole this closes: image/svg+xml passes a "starts with image/" test,
    and SVG can carry script. Declaring it an image must not be enough."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    r = client.post("/l/secret-admin-token/upload-image",
                    files={"photo": ("logo.svg", svg, "image/svg+xml")})
    assert r.status_code == 400
    assert "SVG" in r.json()["error"]


def test_html_disguised_as_a_png_is_rejected(client, league):
    """Filename and content-type both lie; the bytes don't."""
    r = client.post("/l/secret-admin-token/upload-image",
                    files={"photo": ("innocent.png",
                                     b"<html><script>alert(1)</script></html>",
                                     "image/png")})
    assert r.status_code == 400


def test_stored_extension_comes_from_the_bytes_not_the_filename(client, league):
    """A JPEG named .png is stored as a JPEG."""
    url = client.post(
        "/l/secret-admin-token/upload-image",
        files={"photo": ("mislabelled.png", JPEG_BYTES, "image/png")},
    ).json()["url"]
    assert url.endswith(".jpg")


def test_oversized_photos_are_rejected(client, league):
    r = client.post("/l/secret-admin-token/upload-image",
                    files={"photo": ("big.jpg", b"x" * (9 * 1024 * 1024), "image/jpeg")})
    assert r.status_code == 413


def test_photo_upload_requires_the_token(client, league):
    r = client.post("/l/wrong/upload-image",
                    files={"photo": ("shot.png", b"PNG", "image/png")})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Photo sizing and layout
# ---------------------------------------------------------------------------

def test_resizing_a_photo_is_saved(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {}, "images": {"hero": "https://cdn.example/p.jpg"},
        "widths": {"hero": 55},
    })
    assert no_rerender[0]["ai"]["images"]["hero"]["width"] == 55


def test_absurd_widths_are_clamped(client, paper, no_rerender):
    """A browser can post anything; a 4000%-wide photo would wreck the page
    for every reader."""
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {}, "images": {"hero": "https://cdn.example/p.jpg"},
        "widths": {"hero": 4000},
    })
    assert no_rerender[0]["ai"]["images"]["hero"]["width"] == 100

    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {}, "images": {}, "widths": {"hero": -20},
    })
    assert no_rerender[-1]["ai"]["images"]["hero"]["width"] == 10


def test_a_width_without_a_photo_is_ignored(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {}, "images": {}, "widths": {"nonexistent": 50},
    })
    assert "nonexistent" not in (no_rerender[0]["ai"].get("images") or {})


def test_resizing_keeps_the_photo_url(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {}, "images": {"hero": "https://cdn.example/p.jpg"}, "widths": {},
    })
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {}, "images": {}, "widths": {"hero": 40},
    })
    hero = no_rerender[-1]["ai"]["images"]["hero"]
    assert hero["url"] == "https://cdn.example/p.jpg"
    assert hero["width"] == 40


def test_old_plain_string_photos_still_work(client, league, no_rerender):
    """Rows written before resizing existed hold a bare URL string."""
    demo_db.save_paper(league["id"], 1, 2025, "p", "u",
                       {**SAMPLE_AI, "images": {"hero": "https://old/photo.jpg"}})
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {}, "images": {}, "widths": {"hero": 70},
    })
    hero = no_rerender[0]["ai"]["images"]["hero"]
    assert hero["url"] == "https://old/photo.jpg"
    assert hero["width"] == 70


def test_preview_url_is_cache_busted(client, league, paper):
    """Published papers are cached hard, so without this the iframe keeps
    showing the copy from before the edit."""
    r = client.get("/l/secret-admin-token/published/1")
    assert "week-1?v=" in r.text


def test_preview_version_changes_after_an_edit(client, league, paper, no_rerender):
    before = client.get("/l/secret-admin-token/published/1").text
    client.post("/l/secret-admin-token/edit/1/inline",
                json={"edits": {"headline": "CHANGED"}, "images": {}})
    after = client.get("/l/secret-admin-token/published/1").text

    import re
    v1 = re.search(r"week-1\?v=(\w+)", before).group(1)
    v2 = re.search(r"week-1\?v=(\w+)", after).group(1)
    assert v1 != v2


# --- renderer-level layout guarantees --------------------------------------

def _render_paper(ai, editable=False):
    import tempfile
    from providers import (SleeperProvider, TTLCache, apply_lineup_gaps,
                           week_to_legacy_games)
    from storylines import get_weekly_storylines
    from newspaper import (build_edition, build_power_rankings_from_matchups,
                           render_html)
    from tests import fixtures

    p = SleeperProvider(cache=TTLCache(cache_dir=tempfile.mkdtemp(), namespace="t"))
    p._get = lambda u, params=None: fixtures.fake_get(u, params)
    games = week_to_legacy_games(apply_lineup_gaps(p.get_week("TESTLEAGUE", 2025, 3)))
    summary = get_weekly_storylines(games)
    rankings = build_power_rankings_from_matchups(games)
    html = render_html(build_edition("X", 1, summary, games, rankings, ai,
                                     subscribe_slug="sl", editable=editable))
    return html.split("</style>", 1)[1]   # skip the CSS so class names in
                                          # stylesheets don't false-positive


def test_no_broken_relative_image_paths(client):
    """Memes were emitted as ../memes/x.jpg, which resolves to nothing once a
    paper is served from a URL. That was a broken image on every story."""
    assert 'src="../' not in _render_paper(dict(SAMPLE_AI))


def test_published_paper_has_no_empty_photo_placeholders(client):
    body = _render_paper(dict(SAMPLE_AI))
    assert "image-slot-empty" not in body
    assert "image-wrap-editing" not in body


def test_edit_view_makes_photo_slots_editable(client):
    """Slots are clickable and resizable while editing. They mostly hold an
    automatic photo now rather than an empty placeholder."""
    body = _render_paper(dict(SAMPLE_AI), editable=True)
    assert "image-wrap-editing" in body
    assert "data-image-slot" in body


def test_uploaded_hero_replaces_the_stock_one(client):
    """Two hero images stacked on top of each other is never what anyone
    wanted."""
    body = _render_paper({**SAMPLE_AI,
                          "images": {"hero": {"url": "https://x/h.jpg", "width": 70}}})
    assert body.count("https://x/h.jpg") == 1
    assert 'alt="Hero image"' not in body
    assert "width:70%" in body


# ---------------------------------------------------------------------------
# Sanitization — the stored XSS the editors would otherwise allow
# ---------------------------------------------------------------------------

def test_script_tags_never_survive_an_edit(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {
            "headline": "<script>alert(1)</script>HEADLINE",
            "lead_story": "<p>Fine</p><script>steal()</script>",
        },
        "images": {},
    })
    ai = no_rerender[0]["ai"]
    assert "<script" not in ai["headline"]
    assert "<script" not in ai["lead_story"]
    assert "HEADLINE" in ai["headline"]
    assert "Fine" in ai["lead_story"]


def test_event_handlers_are_stripped(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {"lead_story": '<p onclick="steal()" onmouseover="x()">Text</p>'},
        "images": {},
    })
    body = no_rerender[0]["ai"]["lead_story"]
    assert "onclick" not in body
    assert "onmouseover" not in body
    assert "Text" in body


def test_javascript_urls_are_dropped(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {"lead_story": '<a href="javascript:alert(1)">click</a>'},
        "images": {},
    })
    assert "javascript:" not in no_rerender[0]["ai"]["lead_story"]


def test_style_attributes_are_dropped(client, paper, no_rerender):
    """style carries url() and expression() tricks."""
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {"lead_story": '<p style="background:url(javascript:1)">Hi</p>'},
        "images": {},
    })
    assert "style=" not in no_rerender[0]["ai"]["lead_story"]


def test_headlines_come_back_as_plain_text(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {"headline": "<h1>BIG</h1> <b>NEWS</b>"}, "images": {}})
    assert no_rerender[0]["ai"]["headline"] == "BIG NEWS"


def test_ordinary_formatting_survives(client, paper, no_rerender):
    """Sanitizing must not mean throwing away a legitimate edit."""
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {"lead_story":
                  '<p>He was <strong>terrible</strong> and <em>knew it</em>.</p>'},
        "images": {},
    })
    body = no_rerender[0]["ai"]["lead_story"]
    assert "<strong>terrible</strong>" in body
    assert "<em>knew it</em>" in body


def test_the_form_editor_sanitizes_too(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1", data={
        "headline": "<script>x</script>CLEAN",
        "lead_story": '<p onclick="x()">Body</p>',
    })
    ai = no_rerender[0]["ai"]
    assert "<script" not in ai["headline"]
    assert "onclick" not in ai["lead_story"]


def test_photo_urls_must_be_http_or_same_origin(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {}, "images": {"hero": "javascript:alert(1)"}})
    assert not (no_rerender[0]["ai"].get("images") or {}).get("hero")

    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {}, "images": {"hero": "data:text/html,<script>x</script>"}})
    assert not (no_rerender[-1]["ai"].get("images") or {}).get("hero")


# ---------------------------------------------------------------------------
# Link previews
# ---------------------------------------------------------------------------

def test_paper_carries_open_graph_tags():
    body = _render_paper(dict(SAMPLE_AI))
    head = body if "og:title" in body else _full_paper(dict(SAMPLE_AI))
    assert 'property="og:title"' in head
    assert 'property="og:description"' in head
    assert 'name="twitter:card"' in head


def _full_paper(ai, **kw):
    import tempfile
    from providers import (SleeperProvider, TTLCache, apply_lineup_gaps,
                           week_to_legacy_games)
    from storylines import get_weekly_storylines
    from newspaper import (build_edition, build_power_rankings_from_matchups,
                           render_html)
    from tests import fixtures

    p = SleeperProvider(cache=TTLCache(cache_dir=tempfile.mkdtemp(), namespace="t"))
    p._get = lambda u, params=None: fixtures.fake_get(u, params)
    games = week_to_legacy_games(apply_lineup_gaps(p.get_week("TESTLEAGUE", 2025, 3)))
    summary = get_weekly_storylines(games)
    rankings = build_power_rankings_from_matchups(games)
    theme = kw.pop("theme", None)
    edition = build_edition("X", 1, summary, games, rankings, ai,
                            subscribe_slug="sl", **kw)
    return render_html(edition, theme=theme)


def test_preview_title_is_the_headline():
    html = _full_paper(dict(SAMPLE_AI))
    assert "SATAN FALLS IN KEVLARVILLE" in html
    assert 'og:title" content="SATAN FALLS IN KEVLARVILLE"' in html


def test_preview_description_is_plain_text():
    html = _full_paper({**SAMPLE_AI, "lead_story": "<p>Chaos <b>everywhere</b>.</p>"})
    import re
    desc = re.search(r'og:description" content="([^"]*)"', html).group(1)
    assert "<" not in desc
    assert "Chaos everywhere." in desc


def test_preview_image_uses_the_hero_photo():
    html = _full_paper({**SAMPLE_AI,
                        "images": {"hero": {"url": "https://cdn/x.jpg", "width": 80}}})
    assert 'og:image" content="https://cdn/x.jpg"' in html
    assert 'twitter:card" content="summary_large_image"' in html


def test_relative_photo_becomes_absolute_for_crawlers():
    """A crawler fetching from elsewhere can't resolve /demo-image/x.jpg."""
    html = _full_paper({**SAMPLE_AI, "images": {"hero": "/uploads/x.jpg"}},
                       canonical_base="https://commish.app")
    assert 'og:image" content="https://commish.app/uploads/x.jpg"' in html


def test_no_preview_image_when_there_is_no_photo():
    html = _full_paper(dict(SAMPLE_AI))
    assert 'og:image"' not in html
    assert 'twitter:card" content="summary"' in html


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

def test_default_theme_is_unchanged_by_the_theme_system():
    """Tabloid is the base stylesheet, so it must render identically whether
    the theme is unset, 'tabloid', or nonsense."""
    a = _full_paper(dict(SAMPLE_AI))
    b = _full_paper(dict(SAMPLE_AI), theme="tabloid")
    c = _full_paper(dict(SAMPLE_AI), theme="not-a-real-theme")
    assert a == b == c


def test_each_theme_renders_differently():
    tab = _full_paper(dict(SAMPLE_AI), theme="tabloid")
    broad = _full_paper(dict(SAMPLE_AI), theme="broadsheet")
    game = _full_paper(dict(SAMPLE_AI), theme="gameday")
    assert len({tab, broad, game}) == 3
    assert "BROADSHEET" in broad
    assert "GAMEDAY" in game


def test_themes_load_their_own_fonts():
    assert "Bodoni+Moda" in _full_paper(dict(SAMPLE_AI), theme="broadsheet")
    assert "Anton" in _full_paper(dict(SAMPLE_AI), theme="gameday")


def test_theme_resolution_is_forgiving():
    import themes
    assert themes.resolve(None) == "tabloid"
    assert themes.resolve("") == "tabloid"
    assert themes.resolve("GAMEDAY") == "gameday"
    assert themes.resolve("nonsense") == "tabloid"


def test_setup_saves_a_theme(client, league):
    client.post("/l/secret-admin-token/setup", data={"theme": "gameday"})
    assert demo_db._LEAGUES[league["id"]]["theme"] == "gameday"


def test_setup_rejects_an_unknown_theme(client, league):
    client.post("/l/secret-admin-token/setup", data={"theme": "'; drop table"})
    assert demo_db._LEAGUES[league["id"]]["theme"] == "tabloid"


def test_settings_can_change_the_theme(client, league):
    client.post("/l/secret-admin-token/settings",
                data={"theme": "broadsheet", "paper_name": "x", "commissioner": "y"})
    assert demo_db._LEAGUES[league["id"]]["theme"] == "broadsheet"


def test_setup_page_shows_all_three_themes(client, league):
    text = client.get("/l/secret-admin-token/setup").text
    assert "Tabloid" in text and "Broadsheet" in text and "Gameday" in text


# ---------------------------------------------------------------------------
# Mobile
# ---------------------------------------------------------------------------

def test_player_grids_are_not_inline_styled():
    """Inline grid-template-columns beat media queries on specificity, which
    left five 60px headshots jammed into a 340px phone."""
    html = _full_paper(dict(SAMPLE_AI))
    assert "grid-template-columns:repeat(5,1fr)" not in html
    assert 'class="player-grid"' in html


def test_paper_has_a_phone_breakpoint():
    html = _full_paper(dict(SAMPLE_AI))
    assert "@media (max-width: 600px)" in html


# ---------------------------------------------------------------------------
# Absolute URLs behind a proxy
# ---------------------------------------------------------------------------

def test_share_link_uses_the_configured_base_url(client, league, monkeypatch):
    """Behind a proxy request.base_url reports http and the internal host, so
    every share link handed out would be wrong."""
    monkeypatch.setenv("BASE_URL", "https://commish.app")
    r = client.get("/l/secret-admin-token")
    assert f"https://commish.app/p/{league['public_slug']}" in r.text


# ---------------------------------------------------------------------------
# Automatic photos
#
# Most commissioners have no relevant photo to hand, and a paper with empty
# slots looks unfinished. Every player already carries a headshot through the
# provider layer, so the standout of each game becomes the story art for free.
# ---------------------------------------------------------------------------

def test_stories_get_a_photo_with_no_upload():
    body = _render_paper(dict(SAMPLE_AI))
    assert "sleepercdn.com/content/nfl/players" in body
    assert "photo-caption" in body


def test_auto_photo_captions_name_the_player_and_the_number():
    import re
    body = _render_paper(dict(SAMPLE_AI))
    captions = re.findall(r'<span class="photo-caption">([^<]*)</span>', body)
    assert captions
    assert any("pts" in c for c in captions)
    assert any(c[0].isalpha() for c in captions)


def test_an_uploaded_photo_beats_the_automatic_one():
    body = _render_paper({**SAMPLE_AI,
                          "images": {"hero": {"url": "https://cdn/mine.jpg"}}})
    assert "https://cdn/mine.jpg" in body
    # The automatic hero must not also render — that was the two-heroes bug.
    assert 'data-image-slot="hero"' in body
    hero_slot = body.split('data-image-slot="hero"')[1].split("</span>")[0]
    assert "https://cdn/mine.jpg" in hero_slot
    assert "sleepercdn" not in hero_slot


def test_best_performer_prefers_the_biggest_beat_not_the_top_score():
    from newspaper import best_performer
    team = {"all_starters": [
        {"name": "Big Score", "actual": 30.0, "beat_projection_by": 1.0,
         "headshot_url": "a.jpg"},
        {"name": "Big Beat", "actual": 22.0, "beat_projection_by": 14.0,
         "headshot_url": "b.jpg"},
    ]}
    assert best_performer(team)["name"] == "Big Beat"


def test_players_without_a_headshot_are_skipped():
    from newspaper import best_performer
    team = {"all_starters": [
        {"name": "No Photo", "actual": 40.0, "beat_projection_by": 20.0},
        {"name": "Has Photo", "actual": 10.0, "beat_projection_by": 1.0,
         "headshot_url": "b.jpg"},
    ]}
    assert best_performer(team)["name"] == "Has Photo"


def test_no_crash_when_nobody_has_a_headshot():
    from newspaper import auto_photo_for_game, best_performer
    assert best_performer({"all_starters": []}) is None
    assert best_performer(None) is None
    assert auto_photo_for_game(None) is None


def test_removing_a_photo_falls_back_to_automatic(client, paper, no_rerender):
    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {}, "images": {"hero": "https://cdn/mine.jpg"}})
    assert no_rerender[0]["ai"]["images"]["hero"]["url"] == "https://cdn/mine.jpg"

    client.post("/l/secret-admin-token/edit/1/inline", json={
        "edits": {}, "images": {}, "removed": ["hero"]})
    assert "hero" not in (no_rerender[-1]["ai"].get("images") or {})


# ---------------------------------------------------------------------------
# Broadsheet — the theme that was rendering blackletter on a red tabloid bar
# ---------------------------------------------------------------------------

def test_broadsheet_switches_off_the_tabloid_masthead():
    css = _full_paper(dict(SAMPLE_AI), theme="broadsheet")
    broadsheet_block = css.split("===== BROADSHEET =====")[1]
    # The base sets a red bar, 72px white type and a hard black shadow. Each
    # has to be actively cancelled, not merely re-fonted.
    assert "background: #fffefb !important" in broadsheet_block
    assert "text-shadow: none !important" in broadsheet_block
    assert "color: #111 !important" in broadsheet_block


def test_broadsheet_sets_headlines_in_title_case():
    """CSS can only uppercase, so the writer's ALL CAPS has to be undone here."""
    html = _full_paper({**SAMPLE_AI, "headline": "SATAN FALLS IN KEVLARVILLE"},
                       theme="broadsheet")
    assert "Satan Falls in Kevlarville" in html


def test_tabloid_keeps_the_shouting():
    html = _full_paper({**SAMPLE_AI, "headline": "SATAN FALLS IN KEVLARVILLE"},
                       theme="tabloid")
    assert "SATAN FALLS IN KEVLARVILLE" in html


def test_deliberately_cased_headlines_are_left_alone():
    import themes
    assert themes.smart_title("A Carefully Chosen Headline") == \
        "A Carefully Chosen Headline"
    assert themes.smart_title("mixed Case stays") == "mixed Case stays"


def test_title_case_keeps_small_words_down():
    import themes
    assert themes.smart_title("THE DEVIL AND THE DEEP BLUE SEA") == \
        "The Devil and the Deep Blue Sea"


# ---------------------------------------------------------------------------
# Writing quality guardrails
#
# These assert the prompt, not the output — but the prompt is the only lever,
# and the tells below are exactly what made the prose read as generated.
# ---------------------------------------------------------------------------

def test_prompt_bans_the_specific_ai_tells():
    import writer
    prompt = writer.KEVLARVILLE_SYSTEM_PROMPT
    assert "kind of number that" in prompt      # the mad-lib consequence clause
    assert "PATRICK MAHOMES" in prompt          # name-repeated-in-caps
    assert "every decision you've ever made" in prompt


def test_prompt_no_longer_ships_canned_insults():
    """A list of pre-written lines teaches pastiche, which is what produced
    the formulaic output in the first place."""
    import writer
    assert "INSULT TOOLKIT" not in writer.KEVLARVILLE_SYSTEM_PROMPT


def test_prompt_demands_specificity():
    import writer
    assert "Could this sentence be moved" in writer.KEVLARVILLE_SYSTEM_PROMPT


def test_tone_overrides_still_apply():
    assert "NO MERCY" in _system_text("brutal")
    assert "KEEP IT LIGHT" in _system_text("friendly")
    assert "Could this sentence be moved" in _system_text("friendly")


# ===========================================================================
# Launch hardening
#
# Each test below corresponds to a specific finding from the pre-launch review.
# They exist because "we fixed that" is a claim, and a claim that isn't tested
# stops being true the next time someone edits the file.
# ===========================================================================

# --- the season anchor -----------------------------------------------------

def test_season_opener_is_the_thursday_after_labor_day():
    """The rule, not a written-down date. 2025's opener really was 4 Sept."""
    import nfl_week
    from datetime import date
    assert nfl_week.season_opener(2025) == date(2025, 9, 4)
    assert nfl_week.season_opener(2026) == date(2026, 9, 10)
    assert nfl_week.season_opener(2027) == date(2027, 9, 9)


def test_week_numbers_are_right_in_every_season():
    """The bug this replaces: a fixed 2025 anchor returned week 18 for every
    week of every later season."""
    import nfl_week
    from datetime import date, timedelta

    for year in (2025, 2026, 2027, 2030):
        opener = nfl_week.season_opener(year)
        for week in range(1, 19):
            # Tuesday after that week's games are done.
            tuesday = opener + timedelta(days=(week - 1) * 7 + 5)
            assert nfl_week.completed_week(tuesday) == week, (year, week)


def test_the_offseason_does_not_report_a_played_week():
    import nfl_week
    from datetime import date
    assert nfl_week.current_week(date(2026, 7, 4)) == 1
    assert not nfl_week.is_in_season(date(2026, 7, 4))
    assert nfl_week.is_in_season(date(2026, 10, 1))


def test_january_belongs_to_the_previous_season():
    import nfl_week
    from datetime import date
    assert nfl_week.current_season(date(2027, 1, 10)) == 2026
    assert nfl_week.current_season(date(2026, 9, 20)) == 2026


# --- rate limiting ---------------------------------------------------------

def test_client_ip_ignores_a_forged_forwarded_header():
    """The whole rate limiter rested on this. Proxies append, so the leftmost
    entry is whatever the caller typed and the rightmost is what the edge saw."""
    class FakeRequest:
        def __init__(self, header):
            self.headers = {"x-forwarded-for": header} if header else {}
            self.client = type("C", (), {"host": "10.0.0.1"})()

    # One proxy in front, caller forged an entry: take what the proxy appended.
    assert webapp._client_ip(FakeRequest("1.2.3.4, 203.0.113.9")) == "203.0.113.9"
    # No forgery: still the real address.
    assert webapp._client_ip(FakeRequest("203.0.113.9")) == "203.0.113.9"
    # No header at all: fall back to the socket.
    assert webapp._client_ip(FakeRequest("")) == "10.0.0.1"


def test_a_forged_header_cannot_buy_a_fresh_quota(client, league, monkeypatch):
    """Simulates what the edge actually delivers: the caller's own header with
    the address it really saw appended after it."""
    monkeypatch.setattr(webapp, "UPLOADS_PER_HOUR", 2)
    ok = 0
    for i in range(6):
        r = client.post(
            "/l/secret-admin-token/upload-image",
            files={"photo": ("a.png", PNG_BYTES, "image/png")},
            # A different claimed origin every time; one real one behind it.
            headers={"X-Forwarded-For": f"9.9.9.{i}, 203.0.113.5"},
        )
        if r.status_code == 200:
            ok += 1
    assert ok == 2, "rotating the forged header bought extra uploads"


def test_a_short_forwarded_header_is_not_believed(monkeypatch):
    """If there are fewer hops than proxies, the header didn't come through the
    path we expect and the socket address is all that's left."""
    monkeypatch.setattr(webapp, "TRUSTED_PROXY_HOPS", 2)

    class FakeRequest:
        headers = {"x-forwarded-for": "1.2.3.4"}
        client = type("C", (), {"host": "10.0.0.1"})()

    assert webapp._client_ip(FakeRequest()) == "10.0.0.1"


def test_rate_limit_buckets_do_not_accumulate_forever():
    """Buckets nobody revisits used to sit in memory for the life of the
    process. They now live in the database, and the weekly job sweeps them."""
    demo_db._RATE_EVENTS.clear()
    webapp._rate_limited("probe", 5)
    assert "probe" in demo_db._RATE_EVENTS

    # Nothing stale yet, so a sweep leaves it alone.
    assert demo_db.sweep_rate_events(older_than_seconds=3600) == 0
    assert "probe" in demo_db._RATE_EVENTS

    # Old enough, and it goes.
    assert demo_db.sweep_rate_events(older_than_seconds=0) == 1
    assert "probe" not in demo_db._RATE_EVENTS


def test_the_ceiling_survives_a_restart():
    """The whole point of moving this out of process memory. Restarting used
    to hand everybody a fresh allowance, including the daily spend ceiling."""
    for _ in range(3):
        assert webapp._rate_limited("global:papers", 3) is False
    assert webapp._rate_limited("global:papers", 3) is True

    # A new process would have had an empty dict. The store is the database.
    import importlib
    importlib.reload(webapp)
    webapp.db = demo_db
    assert webapp._rate_limited("global:papers", 3) is True


def test_a_slot_is_claimed_atomically_not_checked_then_taken():
    """Check-then-insert lets two simultaneous callers both pass a ceiling
    with one slot left. Twenty threads, one slot: exactly one gets through."""
    import threading
    demo_db._RATE_EVENTS.clear()
    results, lock = [], threading.Lock()

    def go():
        allowed = demo_db.claim_rate_slot("contended", 1, 3600)
        with lock:
            results.append(allowed)

    threads = [threading.Thread(target=go) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1, f"ceiling leaked: {results.count(True)} got through"


def test_limits_fail_closed_when_the_database_is_unreachable(monkeypatch):
    """Failing open would mean a database blip switches off the spend ceiling
    exactly when nobody is watching. Every endpoint behind one of these writes
    to the database moments later anyway, so refusing costs nothing."""
    from web import db as real_db

    def broken(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(real_db, "client", broken)
    assert real_db.claim_rate_slot("anything", 10, 3600) is False


def test_upload_is_capped_per_league_regardless_of_ip(client, league, monkeypatch):
    """League ID comes from our own database, so this cap can't be spoofed."""
    monkeypatch.setattr(webapp, "UPLOADS_PER_HOUR", 10_000)
    monkeypatch.setattr(webapp, "UPLOADS_PER_LEAGUE_PER_DAY", 3)
    codes = [
        client.post("/l/secret-admin-token/upload-image",
                    files={"photo": ("a.png", PNG_BYTES, "image/png")},
                    headers={"X-Forwarded-For": f"5.5.5.{i}"}).status_code
        for i in range(5)
    ]
    assert codes.count(200) == 3
    assert codes.count(429) == 2


# --- spend ceilings and concurrency ---------------------------------------

def test_generation_stops_at_the_daily_global_ceiling(client, league, monkeypatch):
    monkeypatch.setattr(webapp, "MAX_PAPERS_PER_DAY", 0)
    called = []
    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda *a, **k: called.append(1))
    r = client.post("/l/secret-admin-token/generate", data={"week": 1},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "today" in r.headers["location"]
    assert called == [], "spent money past the ceiling"


def test_generation_is_capped_per_league_per_day(client, league, monkeypatch):
    monkeypatch.setattr(webapp, "GENERATIONS_PER_LEAGUE_PER_DAY", 2)
    monkeypatch.setattr(webapp, "generate_and_store", lambda *a, **k: None)
    locations = [
        client.post("/l/secret-admin-token/generate", data={"week": w},
                    follow_redirects=False).headers["location"]
        for w in (1, 2, 3)
    ]
    assert "daily+limit" in locations[2]
    assert "daily+limit" not in locations[0]


def test_a_busy_press_turns_people_away_instead_of_queueing(client, league,
                                                            monkeypatch):
    """Without this the threadpool fills and readers stop being served."""
    import threading
    monkeypatch.setattr(webapp, "_GENERATION_SLOTS",
                        threading.BoundedSemaphore(1))
    webapp._GENERATION_SLOTS.acquire()          # someone else is generating
    monkeypatch.setattr(webapp, "generate_and_store", lambda *a, **k: None)

    r = client.post("/l/secret-admin-token/generate", data={"week": 1},
                    follow_redirects=False)
    assert "presses+are+busy" in r.headers["location"]


def test_the_slot_is_returned_even_when_generation_fails(client, league,
                                                          monkeypatch):
    from providers import ProviderError

    def boom(*a, **k):
        raise ProviderError("nope")

    monkeypatch.setattr(webapp, "generate_and_store", boom)
    before = webapp._GENERATION_SLOTS._value
    client.post("/l/secret-admin-token/generate", data={"week": 1},
                follow_redirects=False)
    assert webapp._GENERATION_SLOTS._value == before, "leaked a slot on error"


# --- image sniffing --------------------------------------------------------

def test_sniffer_recognises_what_it_should_and_nothing_else():
    from web import images

    assert images.sniff(PNG_BYTES).mime == "image/png"
    assert images.sniff(JPEG_BYTES).mime == "image/jpeg"
    assert images.sniff(GIF_BYTES).mime == "image/gif"
    assert images.sniff(WEBP_BYTES).mime == "image/webp"

    assert images.sniff(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>") is None
    assert images.sniff(b"%PDF-1.7 something") is None
    assert images.sniff(b"") is None
    assert images.sniff(b"short") is None
    # RIFF that isn't WebP — an AVI, say.
    assert images.sniff(b"RIFF\x00\x00\x00\x00AVI LIST") is None


def test_rejection_messages_name_the_actual_problem():
    from web import images
    assert "SVG" in images.describe_rejection(b"<svg xmlns='x'></svg>")
    assert "PDF" in images.describe_rejection(b"%PDF-1.4")


# --- security headers and token rotation ----------------------------------

def test_every_response_suppresses_the_referer(client, league):
    """Ad scripts run inside the paper. Without this they receive the admin
    token in the Referer header of every request they make."""
    for path in ("/", "/l/secret-admin-token", "/privacy"):
        r = client.get(path)
        assert r.headers["referrer-policy"] == "no-referrer", path
        assert r.headers["x-content-type-options"] == "nosniff", path


def test_papers_and_manage_pages_are_not_indexable(client, league):
    r = client.get("/l/secret-admin-token")
    assert "noindex" in r.headers["x-robots-tag"]


def test_rotating_the_token_invalidates_the_old_link(client, league):
    r = client.post("/l/secret-admin-token/rotate", follow_redirects=False)
    assert r.status_code == 303
    new_url = r.headers["location"]
    assert "/l/secret-admin-token" not in new_url

    assert client.get("/l/secret-admin-token").status_code == 404
    assert client.get(new_url.split("?")[0]).status_code == 200


def test_rotation_emails_the_new_link_when_we_have_an_address(
        client, league, sent_emails):
    demo_db.update_league(league["id"], {"owner_email": "john@example.com"})
    client.post("/l/secret-admin-token/rotate", follow_redirects=False)
    assert any(m["to"] == "john@example.com" for m in sent_emails)


def test_robots_txt_keeps_crawlers_off_the_papers(client):
    body = client.get("/robots.txt").text
    assert "Disallow: /p/" in body
    assert "Disallow: /l/" in body


def test_the_paper_itself_carries_a_noindex_tag():
    """The header covers our own routes; this covers the file in the bucket."""
    body = _full_paper(dict(SAMPLE_AI))
    assert 'name="robots"' in body
    assert "noindex" in body
    # ...without breaking link unfurls, which is how the product spreads.
    assert 'property="og:title"' in body


# --- view counting ---------------------------------------------------------

def test_reading_a_paper_counts_it(client, league):
    demo_db.save_paper(league["id"], 1, 2025, "p/1", "http://x/1", {"headline": "H"})
    demo_db._STORAGE[demo_db.storage_path("kevlarville-7f3a", 2025, 1)] = "<html></html>"

    for _ in range(3):
        assert client.get("/p/kevlarville-7f3a/2025/week-1").status_code == 200

    assert demo_db.get_paper(league["id"], 2025, 1)["view_count"] == 3


def test_regenerating_does_not_reset_the_readership(client, league):
    demo_db.save_paper(league["id"], 1, 2025, "p/1", "http://x/1", {"headline": "H"})
    demo_db.record_view(league["id"], 2025, 1)
    demo_db.save_paper(league["id"], 1, 2025, "p/1", "http://x/1", {"headline": "H2"})
    assert demo_db.get_paper(league["id"], 2025, 1)["view_count"] == 1


def test_read_counts_reach_the_commissioner(client, league):
    demo_db.save_paper(league["id"], 1, 2025, "p/1", "http://x/1", {"headline": "H"})
    for _ in range(4):
        demo_db.record_view(league["id"], 2025, 1)
    body = client.get("/l/secret-admin-token").text
    assert "4 reads" in body


def test_both_stores_expose_the_same_functions():
    """demo_db is only a truthful preview if it stays function-for-function
    identical to the real one."""
    from web import db as real_db
    public = {n for n in dir(real_db)
              if not n.startswith("_") and callable(getattr(real_db, n))}
    missing = {n for n in public if not hasattr(demo_db, n)}
    # Not part of the storage interface app.py uses: `client` is db.py's own
    # Supabase handle, `describe_key` inspects the key's format before one is
    # built, and the rest are names it imported.
    missing -= {"create_client", "Client", "client", "describe_key"}
    assert not missing, f"demo_db is missing: {sorted(missing)}"


# --- health, errors, legal -------------------------------------------------

def test_health_check_fails_when_the_database_does(client, monkeypatch):
    def broken():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(demo_db, "health_check", broken, raising=False)
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["ok"] is False


def test_health_check_passes_when_it_should(client):
    assert client.get("/healthz").json()["ok"] is True


def test_privacy_and_terms_are_published(client):
    """Not optional: every ad network requires a privacy policy to approve a
    site, and this one collects email addresses."""
    for path in ("/privacy", "/terms"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert len(r.text) > 2000, f"{path} is too thin to be a real policy"

    privacy = client.get("/privacy").text
    for topic in ("Anthropic", "Resend", "Supabase", "unsubscribe"):
        assert topic in privacy, f"privacy policy never mentions {topic}"


def test_the_footer_links_to_both(client):
    body = client.get("/").text
    assert 'href="/privacy"' in body
    assert 'href="/terms"' in body


# --- lore cap --------------------------------------------------------------

def test_lore_is_capped(client, league):
    for i in range(webapp.MAX_LORE_ENTRIES + 10):
        client.post("/l/secret-admin-token/lore", data={"entry": f"thing {i}"},
                    follow_redirects=False)
    assert len(demo_db.get_lore(league["id"])) == webapp.MAX_LORE_ENTRIES


def test_pasting_a_chat_export_into_setup_does_not_create_thousands_of_rows(
        client, league):
    client.post("/l/secret-admin-token/setup",
                data={"lore": "\n".join(f"line {i}" for i in range(5000))},
                follow_redirects=False)
    assert len(demo_db.get_lore(league["id"])) <= webapp.MAX_LORE_ENTRIES


def test_only_a_bounded_number_of_entries_reach_the_prompt():
    from web.generate import MAX_LORE_IN_PROMPT, build_league_context
    entries = [{"entry": f"joke {i}"} for i in range(500)]
    context = build_league_context({"format": "redraft"}, entries)
    assert context.count("- joke") == MAX_LORE_IN_PROMPT


# --- task key --------------------------------------------------------------

def test_task_endpoint_rejects_a_wrong_key(client, monkeypatch):
    monkeypatch.setenv("TASK_KEY", "the-real-key")
    r = client.post("/tasks/weekly", data={"week": 1},
                    headers={"x-task-key": "the-real-kex"})
    assert r.status_code == 404


def test_task_endpoint_is_invisible_without_a_key_configured(client, monkeypatch):
    monkeypatch.delenv("TASK_KEY", raising=False)
    r = client.post("/tasks/weekly", data={"week": 1},
                    headers={"x-task-key": "anything"})
    assert r.status_code == 404


# --- memes are gone --------------------------------------------------------

def test_no_meme_machinery_survives():
    """Removed rather than disabled: once a paper carries ads, shipping images
    somebody else owns is commercial use of them."""
    import newspaper
    for gone in ("load_memes", "select_meme", "meme_url", "render_meme_html"):
        assert not hasattr(newspaper, gone), f"{gone} is still here"


def test_a_paper_with_no_photos_has_no_broken_images():
    body = _full_paper(dict(SAMPLE_AI))
    assert 'src="../' not in body, "relative path that only resolves on disk"
    assert "/memes/" not in body


# ===========================================================================
# PDF export
#
# There is no PDF library here — the browser's own print engine makes the file.
# What can be tested is the second layout it prints against: that the screen
# furniture is gone, that the dark theme is not a black rectangle, and that the
# rules which have to beat a theme actually beat it.
# ===========================================================================

def _print_block(html):
    """Just the print stylesheet, so assertions can't match screen CSS."""
    style = html.split("</style>")[0]
    marker = "@media print"
    assert marker in style, "no print stylesheet at all"
    return style[style.index("@page"):]


def test_every_theme_ships_a_print_stylesheet():
    for theme in ("tabloid", "broadsheet", "gameday"):
        html = _full_paper(dict(SAMPLE_AI), theme=theme)
        assert "@media print" in html, theme
        assert "@page" in html, theme


def test_page_size_is_left_to_the_readers_dialog():
    """Forcing Letter gives A4 users a stripe of blank down one side."""
    block = _print_block(_full_paper(dict(SAMPLE_AI)))
    page_rule = block[:block.index("}")]
    assert "margin" in page_rule
    assert "size:" not in page_rule


def test_interactive_furniture_is_hidden_on_paper():
    block = _print_block(_full_paper(dict(SAMPLE_AI)))
    for selector in (".print-button", ".ce-bar", ".subscribe-form", "input"):
        assert selector in block, f"{selector} still prints"


def test_stories_and_cards_do_not_split_across_pages():
    block = _print_block(_full_paper(dict(SAMPLE_AI)))
    assert "break-inside: avoid" in block
    assert "page-break-inside: avoid" in block   # older engines
    assert "orphans" in block and "widows" in block


def test_headings_do_not_strand_at_the_foot_of_a_page():
    block = _print_block(_full_paper(dict(SAMPLE_AI)))
    assert "break-after: avoid" in block


def test_photos_get_a_height_ceiling_in_print():
    """On screen a tall photo just makes the page longer. On a fixed sheet it
    claims two thirds of the page and pushes its own story overleaf."""
    block = _print_block(_full_paper(dict(SAMPLE_AI)))
    assert "max-height" in block
    assert "mm" in block


# --- the dark theme ---------------------------------------------------------

def test_gameday_prints_light_not_as_a_black_rectangle():
    block = _print_block(_full_paper(dict(SAMPLE_AI), theme="gameday"))
    assert "background: #fff !important" in block
    # The screen ground must not survive into the print block.
    assert "#0b0d10" not in block
    assert "#14171c" not in block


def test_gameday_keeps_its_accent_but_in_an_ink_that_exists():
    """#16f04e is a screen green — on white paper it disappears."""
    block = _print_block(_full_paper(dict(SAMPLE_AI), theme="gameday"))
    assert "#0a7d2c" in block, "no print-weight green"


def test_gameday_card_text_beats_the_theme_on_specificity():
    """The rule being overridden is `.player-card div` — a class plus an
    element. A bare class loses to it no matter how late it appears, and the
    honor roll prints as empty bordered boxes."""
    block = _print_block(_full_paper(dict(SAMPLE_AI), theme="gameday"))
    assert ".player-card .player-card-name" in block
    assert ".player-card .player-card-stat" in block


def test_player_cards_expose_their_parts():
    html = _full_paper(dict(SAMPLE_AI))
    for part in ("player-card-name", "player-card-meta",
                 "player-card-stat", "player-card-proj"):
        assert part in html, part


# --- the button and the footer ---------------------------------------------

def test_the_paper_carries_a_save_as_pdf_button():
    html = _full_paper(dict(SAMPLE_AI))
    assert "window.print()" in html
    assert "Save as PDF" in html


def test_the_editor_does_not_get_a_second_floating_button():
    """The edit view already has a save bar pinned to the bottom."""
    html = _full_paper(dict(SAMPLE_AI), editable=True)
    assert "Save as PDF" not in html


def test_the_printed_page_says_where_it_came_from():
    """A PDF outlives the tab it was printed from."""
    url = "https://commish.example/p/kevlarville/2026/week-3"
    html = _full_paper(dict(SAMPLE_AI), canonical_url=url)
    assert "print-footer" in html
    assert url in html


def test_the_footer_is_invisible_on_screen():
    html = _full_paper(dict(SAMPLE_AI))
    screen_css = html.split("@page")[0]
    assert ".print-footer { display: none; }" in screen_css


def test_saved_pdf_is_named_for_the_paper_not_the_headline():
    """Browsers name a saved PDF after <title>. In a folder of saved editions
    the paper name sorts; a headline doesn't."""
    html = _full_paper(dict(SAMPLE_AI))
    assert "<title>X — Week 1</title>" in html
    # The headline still drives the link preview, which is what it's for.
    assert 'og:title" content="SATAN FALLS IN KEVLARVILLE"' in html


def test_published_view_prints_the_paper_not_the_toolbar(client, league):
    demo_db.save_paper(league["id"], 1, 2025, "p/1", "http://x/1", {"headline": "H"})
    body = client.get("/l/secret-admin-token/published/1").text
    assert "pdf-btn" in body
    assert "contentWindow.print()" in body


# --- key validation ---------------------------------------------------------
#
# The publishable key doesn't get rejected by Supabase, it gets an empty
# result. Reads return nothing, writes fail deep inside a request, and a health
# check that only ran a select passed the whole time. These make the shape of
# the key a startup error instead.

def test_publishable_keys_are_recognised_as_wrong():
    from web.db import describe_key

    ok, what = describe_key("sb_publishable_abc123")
    assert ok is False
    assert "publishable" in what


def test_secret_keys_are_accepted():
    from web.db import describe_key
    assert describe_key("sb_secret_abc123")[0] is True


def test_legacy_jwts_are_read_by_their_role_claim():
    import base64, json
    from web.db import describe_key

    def jwt(role):
        payload = base64.urlsafe_b64encode(
            json.dumps({"role": role}).encode()).decode().rstrip("=")
        return f"header.{payload}.signature"

    assert describe_key(jwt("service_role"))[0] is True
    anon_ok, anon_what = describe_key(jwt("anon"))
    assert anon_ok is False
    assert "anon" in anon_what


def test_an_unknown_key_format_is_allowed_through():
    """A future key format must not take the site down."""
    from web.db import describe_key
    assert describe_key("something-new-entirely")[0] is True


def test_the_wrong_key_stops_the_app_at_startup(monkeypatch):
    from web import db as real_db

    monkeypatch.setattr(real_db, "_client", None)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_publishable_oops")

    with pytest.raises(RuntimeError) as err:
        real_db.client()
    assert "publishable" in str(err.value)
    assert "sb_secret_" in str(err.value), "error should say what to do instead"
    monkeypatch.setattr(real_db, "_client", None)


def test_the_500_page_does_not_claim_the_page_is_missing(client):
    """It reused the 404 template, so a server error was headed 'Nothing
    here.' above a body saying something broke."""
    @webapp.app.get("/_boom_test")
    def boom():
        raise RuntimeError("deliberate")

    c = TestClient(webapp.app, raise_server_exceptions=False)
    r = c.get("/_boom_test")
    assert r.status_code == 500
    assert "Nothing here" not in r.text
    # Jinja escapes the apostrophe, so match on a stretch without one.
    assert "broke on our end" in r.text
    assert "not on yours" in r.text


def test_health_check_reports_missing_migrations_without_failing(client, monkeypatch):
    """A half-migrated database still serves every paper that already exists.
    Failing the health check over it would pull working pages out of rotation —
    but it IS the likeliest cause of a 500 on a fresh deploy, so it should be
    one request away from obvious."""
    monkeypatch.setattr(demo_db, "schema_report", lambda: ["007_themes"],
                        raising=False)
    r = client.get("/healthz")
    assert r.status_code == 200, "a missing migration must not 503 the site"
    body = r.json()
    assert body["ok"] is True
    assert body["missing"] == ["007_themes"]
    assert "reload schema" in body["fix"], "should name the stale-cache fix too"


def test_health_check_still_fails_when_the_database_is_unreachable(client, monkeypatch):
    def broken():
        raise RuntimeError("connection refused")
    monkeypatch.setattr(demo_db, "health_check", broken, raising=False)
    assert client.get("/healthz").status_code == 503


# ===========================================================================
# Accounts
#
# Added after the accountless model produced a dead end: a lost admin token
# was unrecoverable, and the signup path walked people straight into it.
#
# The rule that did NOT change is the one these tests exist to protect —
# readers still need nothing.
# ===========================================================================

GOOD_PASSWORD = "kevlarville forever 2018"


def _signup(client, email="john@example.com", password=GOOD_PASSWORD,
            confirm=None):
    """Signing up now needs the password typed twice, like the real form."""
    return client.post(
        "/signup",
        data={"email": email, "password": password,
              "confirm": password if confirm is None else confirm},
        follow_redirects=False)


# --- hashing ---------------------------------------------------------------

def test_passwords_are_never_stored_in_the_clear(client):
    _signup(client)
    stored = demo_db.user_by_email("john@example.com")["password_hash"]
    assert GOOD_PASSWORD not in stored
    assert stored.startswith("scrypt$")


def test_the_same_password_hashes_differently_every_time():
    """Per-user salt. Without it, one rainbow table cracks every account that
    picked the same password, and equal hashes reveal equal passwords."""
    from web import auth
    assert auth.hash_password("same input") != auth.hash_password("same input")


def test_a_tampered_hash_does_not_verify():
    from web import auth
    good = auth.hash_password(GOOD_PASSWORD)
    assert auth.verify_password(GOOD_PASSWORD, good)
    assert not auth.verify_password(GOOD_PASSWORD, good[:-4] + "AAAA")
    assert not auth.verify_password(GOOD_PASSWORD, "")
    assert not auth.verify_password(GOOD_PASSWORD, "sha256$deadbeef")


def test_weak_passwords_are_refused_with_a_reason(client):
    r = client.post("/signup", data={"email": "a@b.com", "password": "short",
                                     "confirm": "short"},
                    follow_redirects=False)
    assert "at least 8" in r.text
    assert demo_db.user_by_email("a@b.com") is None


# --- sessions --------------------------------------------------------------

def test_signing_up_signs_you_in(client):
    r = _signup(client)
    assert r.status_code == 303
    # Straight to connecting a league. Signing up was never the goal.
    assert r.headers["location"] == "/connect?welcome=1"
    assert client.get("/account").status_code == 200


def test_a_forged_session_cookie_is_refused(client):
    import time
    from web import auth
    _signup(client)
    victim = demo_db.user_by_email("john@example.com")["id"]

    attacker = TestClient(webapp.app)
    # The id is right; the signature is not.
    attacker.cookies.set(auth.SESSION_COOKIE, f"{victim}.{int(time.time())}.forged")
    assert attacker.get("/account").status_code == 401


def test_sessions_expire(monkeypatch):
    from web import auth
    cookie = auth.make_session("someone")
    assert auth.read_session(cookie) == "someone"
    monkeypatch.setattr(auth, "SESSION_MAX_AGE", -1)
    assert auth.read_session(cookie) is None


def test_the_session_cookie_is_not_readable_by_script(client):
    from web import auth
    r = _signup(client)
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header, "an XSS bug could otherwise lift the session"
    assert "samesite=lax" in header, \
        "samesite is what makes CSRF on these forms impractical"


def test_signing_out_clears_the_session(client):
    _signup(client)
    client.post("/logout", follow_redirects=False)
    assert client.get("/account").status_code == 401


# --- enumeration -----------------------------------------------------------

def test_login_says_the_same_thing_whether_or_not_the_account_exists(client):
    _signup(client, email="real@example.com")

    wrong_password = client.post(
        "/login", data={"email": "real@example.com", "password": "wrong-one-here"})
    no_account = client.post(
        "/login", data={"email": "nobody@example.com", "password": "wrong-one-here"})

    # Jinja escapes the apostrophe, so compare on a stretch without one.
    assert "and password" in wrong_password.text
    assert wrong_password.text == no_account.text, (
        "any difference here hands an attacker a list of real addresses")


def test_signing_up_twice_does_not_reveal_the_address_is_taken(client, sent_emails):
    _signup(client, email="taken@example.com")
    r = client.post("/signup",
                    data={"email": "taken@example.com", "password": GOOD_PASSWORD,
                          "confirm": GOOD_PASSWORD},
                    follow_redirects=False)
    assert "already" not in r.text.lower() or "Check your inbox" in r.text
    # The answer goes to the inbox that owns the address instead.
    assert any(m["to"] == "taken@example.com" for m in sent_emails)


def test_forgot_password_answers_identically_for_unknown_addresses(client):
    a = client.post("/forgot", data={"email": "nobody@example.com"},
                    follow_redirects=False)
    b = client.post("/forgot", data={"email": "also-nobody@example.com"},
                    follow_redirects=False)
    assert a.headers["location"] == b.headers["location"] == "/forgot?sent=1"


# --- brute force -----------------------------------------------------------

def test_login_attempts_are_capped_per_account(client, monkeypatch):
    """Credential stuffing comes from thousands of addresses at once, so an
    IP limit alone protects nobody."""
    monkeypatch.setattr(webapp, "LOGINS_PER_ACCOUNT_PER_HOUR", 3)
    monkeypatch.setattr(webapp, "LOGINS_PER_IP_PER_HOUR", 10_000)
    _signup(client, email="target@example.com")
    client.post("/logout")

    for i in range(6):
        client.post("/login",
                    data={"email": "target@example.com", "password": f"guess{i}"},
                    headers={"X-Forwarded-For": f"7.7.7.{i}, 203.0.113.5"})

    # Even the correct password is refused once the account's allowance is gone.
    r = client.post("/login",
                    data={"email": "target@example.com", "password": GOOD_PASSWORD},
                    headers={"X-Forwarded-For": "7.7.7.99, 203.0.113.5"},
                    follow_redirects=False)
    assert r.status_code == 200, "should not have logged in"


def test_login_attempts_are_capped_per_address(client, monkeypatch):
    monkeypatch.setattr(webapp, "LOGINS_PER_IP_PER_HOUR", 3)
    codes = []
    for i in range(6):
        r = client.post("/login",
                        data={"email": f"user{i}@example.com", "password": "nope-nope"},
                        headers={"X-Forwarded-For": "8.8.8.8, 203.0.113.5"})
        codes.append("Too many" in r.text)
    assert any(codes), "one address should not be able to grind a user list"


# --- password reset --------------------------------------------------------

def test_reset_link_works_once(client, sent_emails):
    _signup(client, email="forgot@example.com")
    client.post("/logout")
    client.post("/forgot", data={"email": "forgot@example.com"})

    token = next(t for t in demo_db._MAGIC_LINKS)
    new_password = "a brand new passphrase"

    r = client.post(f"/reset/{token}",
                    data={"password": new_password, "confirm": new_password},
                    follow_redirects=False)
    assert r.status_code == 303

    again = client.post(f"/reset/{token}", data={"password": "yet another one",
                                                 "confirm": "yet another one"})
    assert "expired" in again.text

    client.post("/logout")
    ok = client.post("/login",
                     data={"email": "forgot@example.com", "password": new_password},
                     follow_redirects=False)
    assert ok.status_code == 303, "the new password should work"


def test_a_recovery_link_cannot_be_redeemed_as_a_password_reset(client):
    """Otherwise every manage-link email ever sent is a permanent key to the
    account it was sent to."""
    _signup(client, email="mixed@example.com")
    from datetime import datetime, timedelta, timezone
    token = "recovery-token-not-a-reset"
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    demo_db.create_magic_link("mixed@example.com", token, expires.isoformat(),
                              purpose="recover")

    r = client.post(f"/reset/{token}", data={"password": "trying to take over",
                                             "confirm": "trying to take over"})
    assert "expired" in r.text
    assert demo_db.consume_magic_link(token, purpose="recover") == "mixed@example.com", \
        "the recovery link itself should be untouched"


def test_a_rejected_password_does_not_burn_the_reset_link(client):
    _signup(client, email="careful@example.com")
    client.post("/forgot", data={"email": "careful@example.com"})
    token = next(t for t in demo_db._MAGIC_LINKS)

    client.post(f"/reset/{token}", data={"password": "short", "confirm": "short"})
    r = client.post(f"/reset/{token}",
                    data={"password": "a perfectly fine one",
                          "confirm": "a perfectly fine one"},
                    follow_redirects=False)
    assert r.status_code == 303, "a typo shouldn't cost them their one-shot link"


# --- leagues and ownership -------------------------------------------------

def test_a_league_made_while_signed_in_belongs_to_you(client, monkeypatch):
    _signup(client)
    user = demo_db.user_by_email("john@example.com")

    league = demo_db.create_league(
        provider="sleeper", platform_league_id="55", league_name="X",
        paper_name="The X Times", commissioner_name="j", season=2026,
        public_slug="x-1", admin_token="tok-x")
    demo_db.claim_league(league["id"], user["id"])

    body = client.get("/account").text
    assert "The X Times" in body


def test_opening_an_orphan_league_while_signed_in_adopts_it(client, league):
    """How a league made before signing up joins an account: open the link you
    already have."""
    # The fixture arrives owned, because most tests need the paid plan. This
    # one is about what happens to a league that has no owner at all.
    demo_db.update_league(league["id"], {"user_id": None})
    _signup(client)
    user = demo_db.user_by_email("john@example.com")

    client.get("/l/secret-admin-token")
    assert demo_db._LEAGUES[league["id"]]["user_id"] == user["id"]


def test_an_owned_league_is_not_silently_stolen(client, league):
    """A shared manage link must not transfer ownership away from its owner."""
    demo_db.update_league(league["id"], {"user_id": "the-real-owner"})
    _signup(client, email="someone-else@example.com")

    client.get("/l/secret-admin-token")
    assert demo_db._LEAGUES[league["id"]]["user_id"] == "the-real-owner"


def test_the_admin_token_still_works_with_no_account_at_all(client, league):
    """Every league that existed before accounts, and every bookmark."""
    assert client.get("/l/secret-admin-token").status_code == 200


# --- the thing that must never change --------------------------------------

def test_readers_still_need_nothing(client, league):
    """The entire monetization rests on this. No session, no cookie, no wall."""
    demo_db.save_paper(league["id"], 1, 2025, "p/1", "http://x/1", {"headline": "H"})
    demo_db._STORAGE[demo_db.storage_path("kevlarville-7f3a", 2025, 1)] = "<html></html>"

    anonymous = TestClient(webapp.app)
    assert anonymous.get("/p/kevlarville-7f3a/2025/week-1").status_code == 200
    assert anonymous.get("/p/kevlarville-7f3a").status_code == 200
    assert not anonymous.cookies, "readers should not be given a cookie"


def test_login_cannot_be_used_as_an_open_redirect(client):
    """`next` on a login form is a classic phishing launchpad: land on a real
    sign-in page, get bounced somewhere else entirely."""
    _signup(client, email="redir@example.com")
    client.post("/logout")
    r = client.post("/login", data={"email": "redir@example.com",
                                    "password": GOOD_PASSWORD,
                                    "next": "//evil.example.com/steal"},
                    follow_redirects=False)
    assert r.headers["location"] == "/account"


# ===========================================================================
# Connecting a platform
#
# "Paste the long number out of your league's URL" was the first thing this
# product ever asked anyone to do, and the worst step in it. Sleeper can
# resolve a username to an account and list its leagues, so that step becomes
# a list to click.
# ===========================================================================

#: Distinct from None, which is a meaningful answer here — "no such user".
_DEFAULT = object()


class _FakeSleeper:
    """Stands in for the adapter so these never touch the network."""

    def __init__(self, leagues=None, user=_DEFAULT):
        # `user=None` has to mean "no such account", so the "you didn't say"
        # case needs its own sentinel. Overloading None made an unknown-user
        # test silently exercise the happy path.
        self._user = {"user_id": "u-1", "username": "johnhh",
                      "display_name": "John"} if user is _DEFAULT else user
        self._leagues = leagues if leagues is not None else [
            _fake_league("111", "Kevlarville", "in_season"),
            _fake_league("222", "The Other One", "in_season"),
        ]

    def find_user(self, username):
        return self._user

    def user_leagues(self, user_id, season):
        return self._leagues

    def describe_league(self, league_id, season=None):
        return next((l for l in self._leagues if l.league_id == league_id), None)


def _fake_league(league_id, name, status="in_season", season=2026):
    from providers.models import League
    return League(provider="sleeper", league_id=league_id, name=name,
                  season=season, team_count=12, status=status)


@pytest.fixture
def fake_sleeper(monkeypatch):
    adapter = _FakeSleeper()
    monkeypatch.setattr(webapp, "get_provider", lambda name="sleeper": adapter)
    return adapter


def test_connecting_requires_an_account(client):
    assert client.get("/connect").status_code == 401
    assert client.get("/connect/sleeper").status_code == 401


def test_a_username_returns_your_leagues(client, fake_sleeper):
    _signup(client)
    r = client.post("/connect/sleeper", data={"username": "johnhh"})
    assert "Kevlarville" in r.text
    assert "The Other One" in r.text
    assert "league_id" in r.text, "each league needs to be selectable"


def test_an_unknown_username_says_so_usefully(client, monkeypatch):
    _signup(client)
    monkeypatch.setattr(webapp, "get_provider",
                        lambda name="sleeper": _FakeSleeper(user=None))
    r = client.post("/connect/sleeper", data={"username": "nobody"})
    assert "No Sleeper account" in r.text
    # The commonest mistake, named rather than left to guess at.
    assert "team name" in r.text


def test_picking_a_league_creates_a_paper_you_own(client, fake_sleeper):
    _signup(client)
    user = demo_db.user_by_email("john@example.com")

    r = client.post("/connect/sleeper/add", data={"league_id": "111"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "/setup" in r.headers["location"]

    mine = demo_db.leagues_for_user(user["id"])
    assert [l["league_name"] for l in mine] == ["Kevlarville"]


def test_a_league_you_already_added_is_marked_not_offered_twice(client, fake_sleeper):
    _signup(client)
    client.post("/connect/sleeper/add", data={"league_id": "111"},
                follow_redirects=False)
    r = client.post("/connect/sleeper", data={"username": "johnhh"})
    assert "Already added" in r.text


def test_adding_your_own_league_twice_just_opens_it(client, fake_sleeper):
    """Not an error. They clicked the thing they already have."""
    _signup(client)
    client.post("/connect/sleeper/add", data={"league_id": "111"},
                follow_redirects=False)
    r = client.post("/connect/sleeper/add", data={"league_id": "111"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "/l/" in r.headers["location"]
    assert "error" not in r.headers["location"]


def test_you_cannot_take_over_someone_elses_league(client, fake_sleeper):
    _signup(client, email="first@example.com")
    client.post("/connect/sleeper/add", data={"league_id": "111"},
                follow_redirects=False)
    client.post("/logout")

    second = TestClient(webapp.app)
    second.post("/signup", data={"email": "second@example.com",
                                 "password": GOOD_PASSWORD,
                                 "confirm": GOOD_PASSWORD})
    r = second.post("/connect/sleeper/add", data={"league_id": "111"},
                    follow_redirects=False)
    assert "already" in r.headers["location"].lower()


def test_a_league_that_has_not_drafted_is_shown_but_not_offered(client, monkeypatch):
    """The confusing failure this replaces: pick it, wait, get told there is no
    data for week 1 with no explanation of why."""
    _signup(client)
    monkeypatch.setattr(webapp, "get_provider", lambda name="sleeper":
                        _FakeSleeper(leagues=[_fake_league("999", "Not Drafted",
                                                           "pre_draft")]))
    r = client.post("/connect/sleeper", data={"username": "johnhh"})
    assert "Not Drafted" in r.text
    assert "hasn" in r.text and "drafted" in r.text
    assert "No games yet" in r.text


def test_platforms_that_are_not_ready_say_so_rather_than_failing(client):
    """A platform with no adapter leads to a waiting list, not a dead end."""
    _signup(client)
    r = client.get("/connect/yahoo")
    assert r.status_code == 200
    assert "isn" in r.text and "ready" in r.text
    assert "Tell me when" in r.text


def test_espn_now_leads_to_a_real_page_not_the_waiting_list(client):
    """ESPN went from waitlist to working. The picker reads `ready` off the
    provider registry, so this follows from ESPNProvider.implemented rather
    than from a second list somebody has to remember to edit."""
    _signup(client)
    r = client.get("/connect/espn")

    assert r.status_code == 200
    assert "Tell me when" not in r.text
    assert "League ID" in r.text
    # The six steps for making a league public are the actual product here:
    # it is the thing most people will have to go and do.
    assert "viewable" in r.text.lower()


def test_the_picker_derives_readiness_from_the_providers(client):
    _signup(client)
    ready = {p["key"]: p["ready"] for p in webapp._platforms()}
    assert ready["sleeper"] is True
    assert ready["espn"] is True
    assert ready["yahoo"] is False


def test_platform_interest_is_recorded(client, capsys):
    _signup(client)
    client.post("/connect/espn/notify", follow_redirects=False)
    assert "PLATFORM INTEREST espn john@example.com" in capsys.readouterr().out


def test_espn_and_yahoo_do_not_pretend_to_find_users():
    """The contract returns None rather than a plausible-looking empty result,
    so a caller can tell 'not supported' from 'no leagues'."""
    from providers import get_provider
    assert get_provider("espn").find_user("anyone") is None
    assert get_provider("espn").user_leagues("x", 2026) == []



def test_mistyped_confirmation_is_caught(client):
    """The commonest signup mistake, and the one whose consequence is worst:
    an account whose password nobody knows."""
    r = client.post("/signup", data={"email": "typo@example.com",
                                     "password": GOOD_PASSWORD,
                                     "confirm": GOOD_PASSWORD + "x"},
                    follow_redirects=False)
    assert r.status_code == 200
    assert "match" in r.text
    assert demo_db.user_by_email("typo@example.com") is None


def test_the_reset_form_confirms_too(client):
    _signup(client, email="r@example.com")
    client.post("/forgot", data={"email": "r@example.com"})
    token = next(t for t in demo_db._MAGIC_LINKS)
    r = client.post(f"/reset/{token}", data={"password": "a new passphrase",
                                             "confirm": "a different one"})
    assert "match" in r.text
    # And the link survives, so the typo costs nothing.
    assert demo_db.peek_magic_link(token, purpose="reset") == "r@example.com"


def test_eight_characters_is_enough(client):
    r = client.post("/signup", data={"email": "eight@example.com",
                                     "password": "brownfox",
                                     "confirm": "brownfox"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert demo_db.user_by_email("eight@example.com") is not None


def test_an_unclaimed_league_is_adopted_rather_than_refused(client, fake_sleeper):
    """The wall this removes: a league made before signing up could never be
    reached again, because the only way in was an admin token you no longer
    had and recovery needed an email you had never given."""
    orphan = demo_db.create_league(
        provider="sleeper", platform_league_id="111", league_name="Kevlarville",
        paper_name="The Kevlarville Times", commissioner_name="", season=2026,
        public_slug="kev-1", admin_token="orphan-token")
    assert orphan.get("user_id") is None

    _signup(client)
    user = demo_db.user_by_email("john@example.com")

    r = client.post("/connect/sleeper/add", data={"league_id": "111"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "/l/orphan-token" in r.headers["location"]
    assert demo_db._LEAGUES[orphan["id"]]["user_id"] == user["id"]


def test_the_create_account_button_is_readable(client):
    """`.masthead-nav a` is a class plus an element and beat `.btn-primary`
    on specificity, so the nav's dark grey text won over the button's white —
    dark grey lettering on dark green. Excluding buttons from the rule is the
    fix; more specificity would just be the next round of the same fight."""
    css = io_open_style()
    assert ".masthead-nav a:not(.btn)" in css
    assert "\n.masthead-nav a {" not in css


def io_open_style():
    import pathlib
    return pathlib.Path("web/static/style.css").read_text(encoding="utf-8")


def test_start_a_new_paper_goes_to_the_connect_page(client):
    """It pointed at "/" — the homepage — which for a signed-in person is the
    marketing page they have already read, with no way onward except the nav.

    The button is the whole path from "I have an account" to "I have a paper",
    so it has to land on the step that actually starts one.
    """
    _signup(client)
    body = client.get("/account").text

    start = [line for line in body.splitlines() if "Start a new paper" in line]
    assert start, "the account page no longer offers to start a paper"
    assert 'href="/connect"' in start[0], start[0]


def test_a_writer_failure_is_a_message_not_a_500(client, league, monkeypatch):
    """What the user actually saw the first time this happened was "Nothing
    here. Something broke on our end." — the 500 page, from a TypeError that
    had nothing to do with the real fault.

    Claude being unreachable is a temporary, ordinary condition. It deserves a
    sentence that says to come back in a few minutes, not a crash page.
    """
    from writer import WriterError

    def unreachable(*a, **k):
        raise WriterError("Couldn't reach Claude — every request failed. "
                          "(APIConnectionError <- ConnectError: no route)")

    monkeypatch.setattr(webapp, "generate_and_store", unreachable)

    r = client.post("/l/secret-admin-token/generate", data={"week": 1},
                    follow_redirects=False)

    assert r.status_code == 303, "a writer failure must not reach the 500 page"
    assert r.headers["location"].startswith("/l/secret-admin-token?error=")


def test_a_writer_failure_does_not_leak_the_stack_to_the_reader(client, league,
                                                                monkeypatch):
    """The cause belongs in the log, where it is useful, and not in a URL a
    commissioner is looking at."""
    from writer import WriterError

    monkeypatch.setattr(webapp, "generate_and_store", lambda *a, **k: (
        _ for _ in ()).throw(WriterError("APIConnectionError <- ConnectError")))

    r = client.post("/l/secret-admin-token/generate", data={"week": 1},
                    follow_redirects=False)

    assert "ConnectError" not in r.headers["location"]
    assert "APIConnection" not in r.headers["location"]


def test_a_writer_failure_returns_the_generation_slot(client, league,
                                                      monkeypatch):
    """Four slots. Leak them and the site refuses to generate anything until
    it restarts — a much worse outage than the one that caused it."""
    from writer import WriterError

    monkeypatch.setattr(webapp, "generate_and_store", lambda *a, **k: (
        _ for _ in ()).throw(WriterError("nope")))

    before = webapp._GENERATION_SLOTS._value
    client.post("/l/secret-admin-token/generate", data={"week": 1},
                follow_redirects=False)
    assert webapp._GENERATION_SLOTS._value == before, "leaked a slot"


def test_generation_is_refused_before_paying_when_a_migration_is_missing(
        client, league, monkeypatch):
    """Migration 006 was missing through a deploy. Every Claude call succeeded,
    fourteen seconds passed, the paper was written — and *then* the insert
    raised `column newspapers.ai_cache_original does not exist` and threw all
    of it away. A crash page, and the tokens spent on nothing.

    The check has to happen before the money does.
    """
    spent = {"called": False}

    def must_not_run(*a, **k):
        spent["called"] = True

    monkeypatch.setattr(webapp, "generate_and_store", must_not_run)
    monkeypatch.setattr(demo_db, "schema_blockers", lambda: ["006_edits"])

    r = client.post("/l/secret-admin-token/generate", data={"week": 1},
                    follow_redirects=False)

    assert not spent["called"], "paid for a paper that could not be saved"
    assert r.status_code == 303
    assert "error=" in r.headers["location"]


def test_the_migration_refusal_does_not_name_the_migration_to_the_reader(
        client, league, monkeypatch):
    """A commissioner can do nothing with "006_edits", and it advertises the
    shape of the database. The log gets the detail; the page gets a sentence."""
    monkeypatch.setattr(webapp, "generate_and_store", lambda *a, **k: None)
    monkeypatch.setattr(demo_db, "schema_blockers", lambda: ["006_edits"])

    r = client.post("/l/secret-admin-token/generate", data={"week": 1},
                    follow_redirects=False)

    assert "error=" in r.headers["location"], "it didn't refuse at all"
    assert "006" not in r.headers["location"]
    assert "migration" not in r.headers["location"].lower()


def test_a_healthy_schema_does_not_block_generation(client, league, monkeypatch):
    """The guard must not become the thing that breaks generating."""
    ran = {"called": False}

    def note(*a, **k):
        ran["called"] = True

    monkeypatch.setattr(webapp, "generate_and_store", note)
    r = client.post("/l/secret-admin-token/generate", data={"week": 1},
                    follow_redirects=False)

    assert ran["called"]
    assert r.headers["location"].endswith("/published/1")


def test_a_failure_after_writing_is_not_a_crash_page(client, league,
                                                     monkeypatch):
    """The save side — storage, the database, the renderer. The paper is gone
    either way; the reader should still get a sentence, not a stack trace."""
    def save_fails(*a, **k):
        raise RuntimeError("column newspapers.ai_cache_original does not exist")

    monkeypatch.setattr(webapp, "generate_and_store", save_fails)

    r = client.post("/l/secret-admin-token/generate", data={"week": 1},
                    follow_redirects=False)

    assert r.status_code == 303, "fell through to the 500 page"
    assert "ai_cache_original" not in r.headers["location"]


def test_a_failure_after_writing_returns_the_generation_slot(client, league,
                                                             monkeypatch):
    monkeypatch.setattr(webapp, "generate_and_store", lambda *a, **k: (
        _ for _ in ()).throw(RuntimeError("storage exploded")))

    before = webapp._GENERATION_SLOTS._value
    client.post("/l/secret-admin-token/generate", data={"week": 1},
                follow_redirects=False)
    assert webapp._GENERATION_SLOTS._value == before, "leaked a slot"


def test_an_unapplied_migration_is_announced_at_startup(monkeypatch, capsys):
    """The check was right; the channel was wrong.

    /healthz has reported this since the first time it bit us — but it reports
    inside a 200 body, on purpose, so a half-migrated database isn't pulled out
    of rotation. The host's probe therefore logs a cheerful "GET /healthz 200
    OK" and nobody ever reads the warning in it. That is how 006 survived two
    deploys. Startup logs are what actually gets looked at after a deploy.
    """
    monkeypatch.setattr(webapp, "DEMO_MODE", False)
    monkeypatch.setattr(demo_db, "schema_report", lambda: ["006_edits"])

    webapp.announce_missing_migrations()

    out = capsys.readouterr().out
    assert "006_edits" in out
    assert "notify pgrst" in out, "the log has to include the step people miss"


def test_a_healthy_schema_says_so_rather_than_saying_nothing(monkeypatch,
                                                             capsys):
    """Silence is ambiguous — it reads the same as the check never running."""
    monkeypatch.setattr(webapp, "DEMO_MODE", False)
    monkeypatch.setattr(demo_db, "schema_report", lambda: [])

    webapp.announce_missing_migrations()
    assert "all migrations present" in capsys.readouterr().out


def test_a_database_that_cannot_be_probed_does_not_stop_the_app_booting(
        monkeypatch, capsys):
    """A site that already has papers should keep serving them even if this
    one probe fails. Refusing to boot would be a worse outage than the one it
    is trying to warn about."""
    monkeypatch.setattr(webapp, "DEMO_MODE", False)
    monkeypatch.setattr(demo_db, "schema_report", lambda: (
        _ for _ in ()).throw(RuntimeError("supabase unreachable")))

    webapp.announce_missing_migrations()
    assert "could not be checked" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The weekly regeneration allowance
# ---------------------------------------------------------------------------

def _generate(client, week=1):
    return client.post("/l/secret-admin-token/generate", data={"week": week},
                       follow_redirects=False)


#: The `league` fixture is paid, so this is the allowance those tests mean.
#: Asking plans.py rather than restating 3 means changing the price sheet
#: changes these too, which is the whole reason plans.py exists.
def _paid_allowance():
    import plans
    return plans.regenerations_per_week(plans.PLANS[plans.PAID])


PAID_ALLOWANCE = _paid_allowance()


def test_the_first_generation_is_not_a_regeneration(client, league, monkeypatch):
    """You cannot redo something you have not done. A week with no paper has
    its whole allowance intact."""
    assert webapp.regenerations_used(None) == 0
    assert webapp.regenerations_left(
        None, demo_db.user_by_email("owner@example.com")) == PAID_ALLOWANCE

    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda db_, lg, wk: demo_db.save_paper(
                            lg["id"], wk, lg["season"], "p", "u", {"headline": "x"}))
    _generate(client)

    paper = demo_db.get_paper(league["id"], league["season"], 1)
    assert webapp.regenerations_used(paper) == 0
    assert webapp.regenerations_left(
        paper, demo_db.user_by_email("owner@example.com")) == PAID_ALLOWANCE


def test_editing_never_spends_a_regeneration(client, league):
    """An edit re-renders the page and makes no Claude call at all. Charging
    for it would make the cheap, unlimited thing feel like the expensive one.
    """
    demo_db.save_paper(league["id"], 1, league["season"], "p", "u", {"headline": "x"})
    for _ in range(4):
        demo_db.save_paper(league["id"], 1, league["season"], "p", "u",
                           {"headline": "edited"}, is_edit=True)

    paper = demo_db.get_paper(league["id"], league["season"], 1)
    assert webapp.regenerations_used(paper) == 0


def test_the_allowance_runs_out_after_three(client, league, monkeypatch):
    calls = {"n": 0}

    def fake(db_, lg, wk):
        calls["n"] += 1
        demo_db.save_paper(lg["id"], wk, lg["season"], "p", "u", {"headline": "x"})

    monkeypatch.setattr(webapp, "generate_and_store", fake)

    _generate(client)                      # the paper itself
    for _ in range(PAID_ALLOWANCE):
        _generate(client)                  # the three redos
    assert calls["n"] == PAID_ALLOWANCE + 1

    blocked = _generate(client)
    assert calls["n"] == PAID_ALLOWANCE + 1, "generated anyway"
    assert blocked.status_code == 303
    assert "error=" in blocked.headers["location"]


def test_running_out_points_at_editing_rather_than_just_refusing(client, league,
                                                                 monkeypatch):
    """Editing is free, unlimited, and changes more than a regeneration would.
    Someone who has run out should be told that, not just told no."""
    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda db_, lg, wk: demo_db.save_paper(
                            lg["id"], wk, lg["season"], "p", "u", {"headline": "x"}))
    for _ in range(PAID_ALLOWANCE + 1):
        _generate(client)

    message = _generate(client).headers["location"]
    assert "edit" in message.lower()


def test_the_allowance_is_per_week_not_per_league(client, league, monkeypatch):
    """Week 3 being spent must not stop week 4 from being written at all."""
    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda db_, lg, wk: demo_db.save_paper(
                            lg["id"], wk, lg["season"], "p", "u", {"headline": "x"}))

    for _ in range(PAID_ALLOWANCE + 1):
        _generate(client, week=1)
    assert "error=" in _generate(client, week=1).headers["location"]

    fresh = _generate(client, week=2)
    assert fresh.headers["location"].endswith("/published/2"), fresh.headers["location"]


def test_a_paper_written_before_the_counter_existed_keeps_its_allowance(client):
    """Rows predating migration 011 have no generation_count. Erring toward
    the commissioner is the only defensible direction — the alternative is
    silently confiscating redos from everyone who already had a paper."""
    free = webapp.regenerations_allowed(None)
    assert webapp.regenerations_left({"week": 3}) == free
    assert webapp.regenerations_left({"week": 3, "generation_count": None}) == free


def test_the_remaining_count_is_on_the_week_picker(client, league, monkeypatch):
    """The whole point. A number that only appears in the error message after
    you have run out is not an allowance, it is a surprise — it has to be
    attached to the week you are about to pick."""
    monkeypatch.setattr(webapp, "get_provider", _verify_ok(weeks=(1, 2, 3)))
    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda db_, lg, wk: demo_db.save_paper(
                            lg["id"], wk, lg["season"], "p", "u", {"headline": "x"}))
    _generate(client)
    _generate(client)   # one redo spent

    body = client.get("/l/secret-admin-token").text
    assert "2 redos left" in body, "the remaining count is not on the week picker"


def test_the_past_editions_list_shows_what_each_paper_has_used(client, league):
    """The picker covers this season. The list is where you see the history,
    including weeks the platform no longer offers."""
    demo_db.save_paper(league["id"], 3, league["season"], "p", "u", {"h": 1})
    demo_db.save_paper(league["id"], 3, league["season"], "p", "u", {"h": 1})

    body = client.get("/l/secret-admin-token").text
    assert "regenerated 1 of 3" in body


def test_a_paper_that_was_never_regenerated_says_nothing_about_it(client, league):
    """Zero of three is noise on every row of a list that is mostly zeroes."""
    demo_db.save_paper(league["id"], 3, league["season"], "p", "u", {"h": 1})

    body = client.get("/l/secret-admin-token").text
    assert "regenerated" not in body


# ---------------------------------------------------------------------------
# Connecting an ESPN league
#
# There is no username lookup — ESPN publishes no directory — so a league ID
# pasted out of a URL is the only handle anyone has. That makes the error
# messages the entire experience of this page.
# ---------------------------------------------------------------------------

def _espn_provider(monkeypatch, *, raises=None, name="Ba1Lers", season=2026):
    from providers.models import League

    class P:
        def get_league(self, league_id, season_=None):
            if raises:
                raise raises
            return League(provider="espn", league_id=league_id, name=name,
                          season=season, roster_slots=["QB", "RB", "BN"],
                          team_count=10, status="in_season")

    monkeypatch.setattr(webapp, "get_provider", lambda *a, **k: P())


def test_connecting_an_espn_league_creates_a_paper(client, monkeypatch):
    _signup(client)
    _espn_provider(monkeypatch)

    r = client.post("/connect/espn", data={"league_id": "1909054258"},
                    follow_redirects=False)

    assert r.status_code == 303
    assert "/setup" in r.headers["location"]


def test_a_private_espn_league_is_told_how_to_become_public(client, monkeypatch):
    """The likeliest failure this page will ever see, and the only one with a
    fix the person can actually carry out. They land back on the page that
    lists the six steps."""
    from providers.base import AuthRequired

    _signup(client)
    _espn_provider(monkeypatch, raises=AuthRequired(
        "That ESPN league is private. Open it on ESPN, go to League "
        "Settings, and set “Make League Viewable to Public” to Yes."))

    r = client.post("/connect/espn", data={"league_id": "1909054258"},
                    follow_redirects=False)

    location = r.headers["location"]
    assert location.startswith("/connect/espn?")
    assert "private" in location.lower()
    assert "public" in location.lower()
    # The ID is handed back so they don't retype it after fixing the setting.
    assert "1909054258" in location


def test_a_non_numeric_league_id_is_caught_before_calling_espn(client,
                                                               monkeypatch):
    """People paste the whole URL. Asking ESPN about "https://fantasy..." gets
    a generic not-found, which sends them hunting for the wrong problem."""
    called = {"n": 0}

    def counter(*a, **k):
        called["n"] += 1
        raise AssertionError("should not have reached the provider")

    monkeypatch.setattr(webapp, "get_provider", counter)
    _signup(client)

    r = client.post(
        "/connect/espn",
        data={"league_id": "https://fantasy.espn.com/football/league?leagueId=123"},
        follow_redirects=False)

    assert called["n"] == 0
    assert "digits" in r.headers["location"].lower()


def test_an_unknown_espn_league_says_to_check_the_number(client, monkeypatch):
    from providers.base import LeagueNotFound

    _signup(client)
    _espn_provider(monkeypatch, raises=LeagueNotFound("nope"))

    r = client.post("/connect/espn", data={"league_id": "999"},
                    follow_redirects=False)
    assert "find+that+league" in r.headers["location"].lower()


def test_connecting_an_espn_league_twice_reopens_it(client, monkeypatch):
    _signup(client)
    _espn_provider(monkeypatch)

    first = client.post("/connect/espn", data={"league_id": "1909054258"},
                        follow_redirects=False)
    token = first.headers["location"].split("/l/")[1].split("/")[0]

    again = client.post("/connect/espn", data={"league_id": "1909054258"},
                        follow_redirects=False)
    assert again.headers["location"] == f"/l/{token}"


def test_espn_still_takes_interest_in_private_league_support(client, capsys):
    """ESPN is ready for public leagues and not for private ones. The notify
    route used to refuse ready platforms outright, which silently broke the
    button asking for exactly that."""
    _signup(client)
    r = client.post("/connect/espn/notify", follow_redirects=False)

    assert r.status_code == 303
    assert "PLATFORM INTEREST espn" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The publisher
#
# The first surface in the app that is not a league. Everything else here is
# reachable with an admin token, and admin tokens are free — anyone can mint
# one in thirty seconds by making a league. This one writes a page that
# appears in EVERY paper, so the interesting tests are the ones about who
# cannot open it.
# ---------------------------------------------------------------------------

import io as _io  # noqa: E402


def _png(width=900, height=900) -> bytes:
    Image = pytest.importorskip(
        "PIL.Image", reason="pillow not installed (dev-only dependency)")
    buf = _io.BytesIO()
    Image.new("RGB", (width, height), (10, 90, 200)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def publisher(monkeypatch):
    monkeypatch.setenv("PUBLISHER_TOKEN", "publisher-secret")
    return "publisher-secret"


def test_the_publisher_page_is_off_when_no_token_is_configured(client,
                                                               monkeypatch):
    """Unset means OFF, not open.

    The failure being guarded against is a deploy where the variable did not
    get set and the page quietly let the first URL through — which is the
    worst possible version of this, because nothing would look wrong.
    """
    monkeypatch.delenv("PUBLISHER_TOKEN", raising=False)
    for path in ("/publisher/anything", "/publisher/", "/publisher/x/preview"):
        assert client.get(path).status_code == 404, path


def test_a_league_admin_token_does_not_open_the_publisher_page(client, league,
                                                               publisher):
    """The whole reason this has its own credential.

    An admin token is free — make a league and you have one. If one opened
    this page, every user of the product could rewrite the advertising in
    everybody else's paper.
    """
    assert client.get(f"/publisher/{league['admin_token']}").status_code == 404


def test_a_wrong_publisher_token_is_indistinguishable_from_no_page(client,
                                                                   publisher):
    assert client.get("/publisher/publisher-secre").status_code == 404
    assert client.get("/publisher/publisher-secretx").status_code == 404
    assert client.get("/publisher/PUBLISHER-SECRET").status_code == 404


def test_the_publisher_page_opens_with_the_right_token(client, publisher):
    response = client.get(f"/publisher/{publisher}?week=6&season=2025")
    assert response.status_code == 200
    assert "Classifieds" in response.text


def test_an_uploaded_ad_records_the_size_read_from_its_own_bytes(client,
                                                                 publisher):
    """The dimensions are not asked of the uploader, they are read.

    Everything the caller says about a file is a claim; the bytes are the
    only fact. Same rule as the league photo endpoint.
    """
    response = client.post(
        f"/publisher/{publisher}/upload",
        files={"photo": ("meme.png", _png(1200, 700), "image/png")},
        data={"week": "6", "season": "2025"})
    assert response.status_code == 200, response.text
    assert response.json()["ad"]["width"] == 1200
    assert response.json()["ad"]["height"] == 700

    stored = demo_db.publisher_ads(2025, 6)
    assert len(stored) == 1
    assert stored[0]["width"] == 1200


def test_an_svg_cannot_be_uploaded_as_an_ad(client, publisher):
    """SVG is a document format that can carry script, and this page is
    served from the project's own storage domain."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
    response = client.post(
        f"/publisher/{publisher}/upload",
        files={"photo": ("meme.svg", svg, "image/svg+xml")},
        data={"week": "6", "season": "2025"})
    assert response.status_code == 400
    assert "SVG" in response.json()["error"]
    assert demo_db.publisher_ads(2025, 6) == []


def test_uploading_needs_the_publisher_token_too(client, league, publisher):
    """The page is guarded; so is every write behind it. A guard on the page
    alone is a guard on the door of a room with a window."""
    response = client.post(
        f"/publisher/{league['admin_token']}/upload",
        files={"photo": ("meme.png", _png(), "image/png")},
        data={"week": "6", "season": "2025"})
    assert response.status_code == 404
    assert demo_db.publisher_ads(2025, 6) == []


def test_a_week_cannot_be_filled_past_the_cap(client, publisher):
    for _ in range(webapp.MAX_PUBLISHER_ADS):
        client.post(f"/publisher/{publisher}/upload",
                    files={"photo": ("m.png", _png(), "image/png")},
                    data={"week": "6", "season": "2025"})
    response = client.post(
        f"/publisher/{publisher}/upload",
        files={"photo": ("m.png", _png(), "image/png")},
        data={"week": "6", "season": "2025"})
    assert response.status_code == 400
    assert len(demo_db.publisher_ads(2025, 6)) == webapp.MAX_PUBLISHER_ADS


def test_reordering_cannot_drag_an_ad_in_from_another_week(client, publisher):
    """The scoping that replaces the per-league scoping everywhere else.

    Every other write in db.py is filtered by league_id so a stray id from
    somebody else's league cannot be touched. Nothing here is scoped to a
    league, so the season and week do that job instead.
    """
    mine = demo_db.add_publisher_ad(2025, 6, "/a.png")
    first = demo_db.add_publisher_ad(2025, 9, "/first.png")
    second = demo_db.add_publisher_ad(2025, 9, "/second.png")

    # Week 9's ads named first, so an unscoped update would move THEM.
    client.post(f"/publisher/{publisher}/reorder",
                json={"ids": [second["id"], first["id"], mine["id"]],
                      "week": 6, "season": 2025})

    # Asserting on ORDER, not membership. The first version of this test
    # compared the ids in each week and passed with the scoping deleted —
    # of course it did: an ad whose position is rewritten is still in the
    # week it was always in. What an unscoped update actually does is
    # reshuffle another week's page, so that is what has to be looked at.
    assert [a["image_url"] for a in demo_db.publisher_ads(2025, 9)] == [
        "/first.png", "/second.png"], "week 9's page was reordered from week 6"
    assert [a["image_url"] for a in demo_db.publisher_ads(2025, 6)] == ["/a.png"]


def test_the_preview_renders_the_page_the_paper_will_print(client, publisher):
    demo_db.add_publisher_ad(2025, 6, "/a.png", width=1200, height=700)
    response = client.get(f"/publisher/{publisher}/preview?week=6&season=2025")
    assert response.status_code == 200
    assert "pub-ad" in response.text
    # Through the paper's own print stylesheet, so the preview's own Print
    # command shows what a reader's PDF will do. A preview built any other way
    # is a preview of the preview.
    assert "@media print" in response.text


def test_the_preview_says_so_when_a_week_is_empty(client, publisher):
    response = client.get(f"/publisher/{publisher}/preview?week=13&season=2025")
    assert response.status_code == 200
    assert "Nothing uploaded" in response.text


def test_a_nonsense_week_cannot_write_rows_nothing_will_read(client, publisher):
    """These arrive in a URL. An unbounded week number is a key that stores
    ads on week 99999, where no paper will ever look for them."""
    response = client.post(
        f"/publisher/{publisher}/upload",
        files={"photo": ("m.png", _png(), "image/png")},
        data={"week": "99999", "season": "2025"})
    assert response.status_code == 200
    assert demo_db.publisher_ads(2025, 99999) == []
    assert len(demo_db.publisher_ads(2025, 22)) == 1


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------

def test_the_classifieds_page_is_frozen_into_the_paper_on_first_sight():
    """A paper is an archive.

    Somebody opening Week 2 in December has to see the page that actually went
    out in Week 2. So the ads are copied into the paper's own content the
    first time it is rendered, and every render after that uses the copy —
    deleting an ad cannot reach backwards into a paper that has been read.
    """
    from web.generate import PUBLISHER_ADS_KEY, _snapshot_publisher_ads

    demo_db.add_publisher_ad(2025, 6, "/week6.png", width=900, height=900)
    content = {}

    first = _snapshot_publisher_ads(demo_db, content, 2025, 6)
    assert [a["image_url"] for a in first] == ["/week6.png"]
    assert content[PUBLISHER_ADS_KEY], "the snapshot was not stored"

    # The publisher rewrites the week. The paper does not change.
    for ad in demo_db.publisher_ads(2025, 6):
        demo_db.delete_publisher_ad(ad["id"])
    demo_db.add_publisher_ad(2025, 6, "/different.png", width=900, height=900)

    again = _snapshot_publisher_ads(demo_db, content, 2025, 6)
    assert [a["image_url"] for a in again] == ["/week6.png"]


def test_a_paper_generated_before_the_page_existed_picks_it_up_later():
    """"First sight", not "at generation".

    A commissioner who generates on Sunday morning, before that week's page
    has been uploaded, gets a paper with no classifieds. The empty list is not
    a snapshot, so their next edit or regeneration picks the page up.
    """
    from web.generate import PUBLISHER_ADS_KEY, _snapshot_publisher_ads

    content = {}
    assert _snapshot_publisher_ads(demo_db, content, 2025, 7) == []
    assert PUBLISHER_ADS_KEY not in content

    demo_db.add_publisher_ad(2025, 7, "/late.png", width=900, height=900)
    later = _snapshot_publisher_ads(demo_db, content, 2025, 7)
    assert [a["image_url"] for a in later] == ["/late.png"]


def test_a_broken_ad_table_never_costs_anybody_a_paper(monkeypatch):
    """A paper missing a page of memes is a paper. A paper that failed to
    render because the ad table hiccuped is not."""
    from web.generate import _snapshot_publisher_ads

    class Exploding:
        def publisher_ads(self, season, week):
            raise RuntimeError("relation publisher_ads does not exist")

    assert _snapshot_publisher_ads(Exploding(), {}, 2025, 6) == []


def test_a_token_with_a_slash_in_it_is_announced_at_boot(monkeypatch, capsys):
    """Three problems, one symptom.

    The token is a URL path segment, so a slash in it splits the path and the
    route never matches. A 404 then means "wrong token", "no token", or "a
    token that cannot possibly work" — and nothing distinguishes them from
    outside. This happened: render.yaml asked Render to generate the value,
    Render generates standard base64, and standard base64 contains "/" and
    "+". The value drawn happened to contain neither, which is a one-in-four
    outcome; the next rotation was better than even money to break the page
    silently.
    """
    monkeypatch.setenv("PUBLISHER_TOKEN", "abc/def")
    webapp.announce_unusable_publisher_token()
    printed = capsys.readouterr().out
    assert "cannot work in a URL" in printed
    assert "token_urlsafe" in printed, "say how to fix it, not just that it is broken"


def test_a_url_safe_token_says_nothing(monkeypatch, capsys):
    """Every line of boot noise costs the next warning some attention."""
    monkeypatch.setenv("PUBLISHER_TOKEN", "4acq7OXHKMF3KzZk9ACa3klegp9RCpDl")
    webapp.announce_unusable_publisher_token()
    assert capsys.readouterr().out == ""


def test_an_unset_token_is_not_reported_as_broken(monkeypatch, capsys):
    """Unset is off, and off is a choice rather than a fault."""
    monkeypatch.delenv("PUBLISHER_TOKEN", raising=False)
    webapp.announce_unusable_publisher_token()
    assert capsys.readouterr().out == ""


# ===========================================================================
# The paywall
#
# The rule that matters here is the one about who can grant access: the
# webhook, and nothing else. A paywall you can walk through by editing a URL
# is not a paywall, and one that charges somebody and then does not let them
# in is worse than not charging at all.
# ===========================================================================

import plans  # noqa: E402


def _paid_user(email="payer@example.com", status="active"):
    user = demo_db.create_user(email, "x")
    demo_db.set_plan(user["id"], plan="paid", status=status)
    return demo_db.user_by_id(user["id"])


# --- what a plan means -----------------------------------------------------

def test_nobody_signed_in_is_on_the_free_plan():
    """Most readers, every token-only league, and every request that arrives
    before migration 014 has run."""
    assert plans.plan_for(None).key == plans.FREE
    assert plans.plan_for({}).key == plans.FREE
    assert plans.plan_for({"email": "x"}).key == plans.FREE


def test_a_cancelled_subscription_loses_the_features():
    """The `plan` column alone would keep somebody on the paid tier forever if
    one webhook were ever missed. Stripe's own word for the subscription is
    checked alongside it, and Stripe wins."""
    for dead in ("canceled", "unpaid", "past_due", "incomplete",
                 "incomplete_expired", "paused"):
        user = {"plan": "paid", "plan_status": dead}
        assert plans.plan_for(user).key == plans.FREE, dead

    for alive in ("active", "trialing"):
        assert plans.plan_for({"plan": "paid", "plan_status": alive}).key \
            == plans.PAID, alive


def test_cancelling_keeps_the_month_that_was_paid_for():
    """Stripe leaves a cancelled-but-not-yet-expired subscription `active`
    until the period actually ends. Anything else takes away something
    somebody has already paid for."""
    assert plans.plan_for({"plan": "paid", "plan_status": "active",
                           "cancel_at_period_end": True}).key == plans.PAID


# --- the gates, which hold against the form as well as the page ------------

def test_a_free_account_cannot_post_its_way_to_a_paid_theme(client, free_league):
    """The greyed-out radio button is a courtesy. This is the rule."""
    client.post("/l/free-admin-token/settings", data={
        "paper_name": "x", "commissioner": "y", "theme": "gameday",
        "format": "redraft", "tone": "standard"})

    assert demo_db._LEAGUES[free_league["id"]]["theme"] == "tabloid"


def test_a_paid_account_gets_the_theme_it_asked_for(client, league):
    client.post("/l/secret-admin-token/settings", data={
        "paper_name": "x", "commissioner": "y", "theme": "gameday",
        "format": "redraft", "tone": "standard"})

    assert demo_db._LEAGUES[league["id"]]["theme"] == "gameday"


def test_the_setup_page_gates_the_theme_too(client, free_league):
    """Two forms write this column. Gating one of them is gating neither."""
    client.post("/l/free-admin-token/setup", data={"theme": "broadsheet"})
    assert demo_db._LEAGUES[free_league["id"]]["theme"] == "tabloid"


def test_a_free_account_cannot_turn_on_auto_send(client, free_league):
    """This one spends money every week without anybody pressing anything,
    so it is the gate that would cost the most to get wrong."""
    client.post("/l/free-admin-token/settings", data={
        "paper_name": "x", "commissioner": "y", "auto_send": "on",
        "theme": "tabloid", "format": "redraft", "tone": "standard"})

    assert demo_db._LEAGUES[free_league["id"]]["auto_send"] is False


def test_a_paid_account_can(client, league):
    client.post("/l/secret-admin-token/settings", data={
        "paper_name": "x", "commissioner": "y", "auto_send": "on",
        "theme": "tabloid", "format": "redraft", "tone": "standard"})

    assert demo_db._LEAGUES[league["id"]]["auto_send"] is True


def test_photo_uploads_are_refused_on_the_free_plan(client, free_league):
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"IHDR"
           + (1).to_bytes(4, "big") + (1).to_bytes(4, "big"))
    r = client.post("/l/free-admin-token/upload-image",
                    files={"photo": ("x.png", png, "image/png")})

    assert r.status_code == 402
    assert "4.99" in r.json()["error"]


def test_the_free_allowance_is_smaller_and_still_works(client, free_league,
                                                       monkeypatch):
    """Free is not crippled, it is smaller. Two redos still happen."""
    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda db_, lg, wk: demo_db.save_paper(
                            lg["id"], wk, lg["season"], "p", "u", {"headline": "x"}))

    free = plans.regenerations_per_week(plans.PLANS[plans.FREE])
    for _ in range(free + 1):
        r = client.post("/l/free-admin-token/generate", data={"week": 1},
                        follow_redirects=False)
        assert "error=" not in r.headers["location"], r.headers["location"]

    spent = client.post("/l/free-admin-token/generate", data={"week": 1},
                        follow_redirects=False)
    assert "error=" in spent.headers["location"]
    assert free < plans.regenerations_per_week(plans.PLANS[plans.PAID])


def test_running_out_only_mentions_paying_to_somebody_it_would_help(
        client, league, free_league, monkeypatch):
    """Telling a paying customer who has used all three that they could pay
    for more is the most irritating sentence a product can print."""
    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda db_, lg, wk: demo_db.save_paper(
                            lg["id"], wk, lg["season"], "p", "u", {"headline": "x"}))

    def spend(token, times):
        for _ in range(times):
            client.post(f"/l/{token}/generate", data={"week": 1},
                        follow_redirects=False)
        return client.post(f"/l/{token}/generate", data={"week": 1},
                           follow_redirects=False).headers["location"]

    free_msg = spend("free-admin-token",
                     plans.regenerations_per_week(plans.PLANS[plans.FREE]) + 2)
    paid_msg = spend("secret-admin-token",
                     plans.regenerations_per_week(plans.PLANS[plans.PAID]) + 2)

    assert "4.99" in free_msg
    assert "4.99" not in paid_msg


def test_a_second_league_is_refused_on_the_free_plan(client, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _verify_ok(name="First"))
    _signup(client)

    client.post("/connect/sleeper/add", data={"league_id": "111"},
                follow_redirects=False)
    second = client.post("/connect/sleeper/add", data={"league_id": "222"},
                         follow_redirects=False)

    assert "error=" in second.headers["location"]
    user = demo_db.user_by_email("john@example.com")
    assert len(demo_db.leagues_for_user(user["id"])) == 1


def test_a_paid_account_can_run_as_many_leagues_as_it_likes(client, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _verify_ok(name="Any"))
    _signup(client)
    user = demo_db.user_by_email("john@example.com")
    demo_db.set_plan(user["id"], plan="paid", status="active")

    for league_id in ("111", "222", "333"):
        client.post("/connect/sleeper/add", data={"league_id": league_id},
                    follow_redirects=False)

    assert len(demo_db.leagues_for_user(user["id"])) == 3


def test_picking_up_a_league_you_already_made_is_never_refused(client,
                                                              monkeypatch):
    """The cap is on creating, not on recovering. Refusing adoption would
    strand somebody with a paper they own and cannot reach."""
    monkeypatch.setattr(webapp, "get_provider", _verify_ok(name="Mine"))
    _signup(client)
    user = demo_db.user_by_email("john@example.com")

    client.post("/connect/sleeper/add", data={"league_id": "111"},
                follow_redirects=False)
    orphan = demo_db.create_league(
        provider="sleeper", platform_league_id="999", league_name="Orphan",
        paper_name="The Orphan Times", commissioner_name="", season=2025,
        public_slug="orphan-1", admin_token="orphan-token")

    client.get("/l/orphan-token")

    assert demo_db._LEAGUES[orphan["id"]]["user_id"] == user["id"]


# --- the plan follows the owner, not whoever is holding the link -----------

def test_a_shared_manage_link_carries_the_subscription_with_it(client, league):
    """One member of a league pays and the paper is better for everybody. A
    league-mate opening the manage link gets the paid features, because they
    belong to the league's owner, not to the browser."""
    r = client.post("/l/secret-admin-token/settings", data={
        "paper_name": "x", "commissioner": "y", "theme": "broadsheet",
        "format": "redraft", "tone": "standard"})

    assert r.status_code in (200, 303)
    assert demo_db._LEAGUES[league["id"]]["theme"] == "broadsheet"


# --- the webhook, which is the only way in ---------------------------------

def test_an_unsigned_webhook_cannot_hand_out_a_subscription(client, monkeypatch):
    """The whole security of the paywall. The URL is public; the signature is
    the only thing that says an event came from Stripe."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    user = demo_db.create_user("victim@example.com", "x")
    demo_db.remember_stripe_customer(user["id"], "cus_123")

    forged = {"type": "customer.subscription.updated",
              "data": {"object": {"customer": "cus_123", "status": "active",
                                  "id": "sub_1"}}}

    r = client.post("/stripe/webhook", json=forged)

    assert r.status_code == 400
    assert demo_db.user_by_id(user["id"])["plan"] == "free"


def test_the_webhook_is_invisible_with_no_secret_configured(client, monkeypatch):
    """An endpoint that cannot verify anything should not look like an
    endpoint that might."""
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    assert client.post("/stripe/webhook", json={"type": "x"}).status_code == 404


def test_the_success_page_grants_nothing(client):
    """Coming back from Stripe proves somebody visited a URL. Anybody can
    visit a URL."""
    _signup(client)
    user = demo_db.user_by_email("john@example.com")

    body = client.get("/billing/done?ok=1").text

    assert demo_db.user_by_id(user["id"])["plan"] == "free"
    assert "Almost there" in body


def test_a_verified_event_is_what_actually_grants_it(monkeypatch):
    """The other half: a real event does work, and sets the plan from the
    subscription's STATUS rather than from the event's name."""
    from web import billing

    user = demo_db.create_user("buyer@example.com", "x")
    demo_db.remember_stripe_customer(user["id"], "cus_abc")

    billing.apply_subscription(demo_db, _subscription("cus_abc", "active"))
    assert demo_db.user_by_id(user["id"])["plan"] == "paid"

    billing.apply_subscription(demo_db, _subscription("cus_abc", "canceled"))
    assert demo_db.user_by_id(user["id"])["plan"] == "free"


class _Obj:
    """A stand-in for a Stripe object: attribute access over a dict."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _subscription(customer, status, period_end=1790000000, metadata=None):
    return _Obj(id="sub_test", customer=customer, status=status,
                metadata=metadata or {},
                items=_Obj(data=[_Obj(current_period_end=period_end)]))


def test_the_period_end_is_read_off_the_item_not_the_subscription():
    """Stripe moved current_period_end onto the subscription ITEM in the
    2025-03-31 API version and left nothing on the parent. Reading it off the
    subscription returns None — silently, which is the worst kind."""
    from web import billing

    user = demo_db.create_user("dated@example.com", "x")
    demo_db.remember_stripe_customer(user["id"], "cus_dated")

    billing.apply_subscription(demo_db, _subscription("cus_dated", "active"))

    renews = demo_db.user_by_id(user["id"])["plan_renews_at"]
    assert renews and renews.startswith("2026-"), renews


def test_an_event_for_a_stranger_changes_nobody(monkeypatch):
    """A subscription against a customer this app has never stored must not
    pick the nearest account and upgrade it."""
    from web import billing

    user = demo_db.create_user("bystander@example.com", "x")

    result = billing.apply_subscription(demo_db,
                                        _subscription("cus_unknown", "active"))

    assert "no matching account" in result
    assert demo_db.user_by_id(user["id"])["plan"] == "free"


def test_a_subscription_made_in_the_stripe_dashboard_still_finds_its_owner():
    """The one case the customer lookup cannot cover, which is why the user id
    is copied onto the subscription's metadata at checkout."""
    from web import billing

    user = demo_db.create_user("dashboard@example.com", "x")
    subscription = _subscription("cus_never_seen", "active",
                                 metadata={"user_id": user["id"]})

    billing.apply_subscription(demo_db, subscription)

    fresh = demo_db.user_by_id(user["id"])
    assert fresh["plan"] == "paid"
    # And the customer id is backfilled, so the next event takes the fast path.
    assert fresh["stripe_customer_id"] == "cus_never_seen"


# --- what the page shows ---------------------------------------------------

def test_the_locked_looks_are_shown_not_hidden(client, free_league):
    """Nobody upgrades to get something they never knew existed."""
    body = client.get("/l/free-admin-token").text

    assert "Broadsheet" in body
    assert "Gameday" in body
    assert 'disabled' in body


def test_a_paid_league_sees_no_locks_on_its_own_settings(client, league):
    body = client.get("/l/secret-admin-token").text
    assert "4.99" not in body


def test_the_account_page_says_what_paying_would_add(client):
    _signup(client)
    body = client.get("/account").text

    assert "Free" in body
    for word in ("leagues", "photos", "Tuesday"):
        assert word in body, word


def test_the_upgrade_button_only_appears_when_stripe_is_configured(
        client, monkeypatch):
    """A button that 500s is worse than no button."""
    _signup(client)

    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    assert "/billing/checkout" not in client.get("/account").text

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test")
    assert "/billing/checkout" in client.get("/account").text


def _signed(payload: bytes, secret: str = "whsec_test") -> str:
    """A real Stripe-Signature header, built the way Stripe builds one.

    Without this, the unsigned-webhook test above proves nothing: a route that
    rejected EVERY request would pass it. This is the other half — a correctly
    signed event has to get through, which is the only thing that makes the
    rejection meaningful.
    """
    import hashlib
    import hmac as hmac_mod
    import time as time_mod

    stamp = int(time_mod.time())
    signed = f"{stamp}.".encode() + payload
    digest = hmac_mod.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def test_a_correctly_signed_event_is_accepted_and_applied(client, monkeypatch):
    import json as json_mod

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")

    user = demo_db.create_user("signed@example.com", "x")
    demo_db.remember_stripe_customer(user["id"], "cus_signed")

    body = json_mod.dumps({
        "id": "evt_1", "object": "event",
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_signed", "object": "subscription",
            "customer": "cus_signed", "status": "active",
            "items": {"object": "list", "data": [
                {"id": "si_1", "object": "subscription_item",
                 "current_period_end": 1790000000},
            ]},
            "metadata": {},
        }},
    }).encode()

    r = client.post("/stripe/webhook", content=body,
                    headers={"stripe-signature": _signed(body),
                             "content-type": "application/json"})

    assert r.status_code == 200, r.text
    fresh = demo_db.user_by_id(user["id"])
    assert fresh["plan"] == "paid"
    assert fresh["plan_status"] == "active"
    assert (fresh["plan_renews_at"] or "").startswith("2026-")


def test_the_same_event_with_one_byte_changed_is_rejected(client, monkeypatch):
    """The signature covers the bytes. Tampering with the amount, the status
    or the customer has to invalidate it, or it is decoration."""
    import json as json_mod

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")

    user = demo_db.create_user("tamper@example.com", "x")
    demo_db.remember_stripe_customer(user["id"], "cus_tamper")

    honest = json_mod.dumps({
        "id": "evt_2", "object": "event",
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_t", "object": "subscription",
            "customer": "cus_tamper", "status": "canceled",
            "items": {"object": "list", "data": []}, "metadata": {},
        }},
    }).encode()
    signature = _signed(honest)
    tampered = honest.replace(b'"canceled"', b'"active"  ')

    r = client.post("/stripe/webhook", content=tampered,
                    headers={"stripe-signature": signature,
                             "content-type": "application/json"})

    assert r.status_code == 400
    assert demo_db.user_by_id(user["id"])["plan"] == "free"


def test_opening_checkout_is_rate_limited(client, monkeypatch):
    """This route creates a Stripe CUSTOMER the first time each account uses
    it, and Stripe's own card-testing guidance names "limit the number of
    customers created by a single IP" as a mitigation. Stripe defends its own
    checkout page; it does not defend this route."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test")
    _signup(client)

    seen = []
    monkeypatch.setattr(webapp.billing, "checkout_url",
                        lambda *a, **k: seen.append(1) or "https://stripe.test/c")

    for _ in range(webapp.CHECKOUTS_PER_HOUR + 4):
        client.post("/billing/checkout", follow_redirects=False)

    assert len(seen) <= webapp.CHECKOUTS_PER_HOUR, (
        f"reached Stripe {len(seen)} times")


def test_a_rate_limited_checkout_says_nothing_was_charged(client, monkeypatch):
    """The one sentence somebody needs when a payment page refuses them."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test")
    _signup(client)
    monkeypatch.setattr(webapp.billing, "checkout_url",
                        lambda *a, **k: "https://stripe.test/c")

    last = None
    for _ in range(webapp.CHECKOUTS_PER_HOUR + 2):
        last = client.post("/billing/checkout", follow_redirects=False)

    assert "charged" in last.headers["location"]


def test_the_manage_page_is_in_the_order_somebody_uses_it(client, league):
    """Make it, then look at what you made, then tune it.

    Past editions used to sit near the bottom, below the delete button, which
    put the thing a commissioner opens the page for behind everything they
    configure once and never touch again. Pinned here because section order is
    the kind of thing a later edit reshuffles without noticing.
    """
    body = client.get("/l/secret-admin-token").text

    # Matched on the heading tag, so a stray mention of "Settings" in some
    # help text higher up can't satisfy the check by accident. Settings sits
    # above the two housekeeping cards: it gets used; they are read once.
    order = ["Send this to your league", "Make this week's paper",
             "Past editions", "Who's in the league", "The lore", "Settings",
             "Don't lose this page", "Delete this league"]
    heads = {t: body.find(f'<h2 class="card-title">{t}</h2>') for t in order}
    seen = [heads[t] for t in order]

    assert all(i >= 0 for i in seen), (
        f"a section is missing: {[t for t in order if heads[t] < 0]}")
    assert seen == sorted(seen), "the manage page sections are out of order"


# ===========================================================================
# Sign in with Google
#
# The session model is unchanged: these routes end by handing a user id to
# _set_session, exactly as a password login does. What is new is everything
# that has to be true before they do.
# ===========================================================================

from web import auth, oauth  # noqa: E402


@pytest.fixture
def google_on(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("SESSION_SECRET", "a-fixed-secret-for-tests")


def _google_says(monkeypatch, sub="google-sub-1", email="new@example.com",
                 verified=True, name="A Person"):
    """Stand in for the whole exchange-and-verify round trip."""
    identity = oauth.GoogleIdentity(sub=sub, email=email,
                                    email_verified=verified, name=name)
    monkeypatch.setattr(webapp.oauth, "identity_from_code",
                        lambda code, base: identity)
    return identity


def _start(client):
    """Press the button, and keep the state cookie the way a browser would."""
    r = client.get("/auth/google", follow_redirects=False)
    return r


# --- the button itself -----------------------------------------------------

def test_the_button_is_absent_and_the_routes_404_without_credentials(
        client, monkeypatch):
    """Same posture as Stripe: a half-configured deploy shows nothing rather
    than a button that fails."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

    assert "/auth/google" not in client.get("/login").text
    assert client.get("/auth/google", follow_redirects=False).status_code == 404
    assert client.get("/auth/google/callback").status_code == 404


def test_the_button_appears_once_it_is_configured(client, google_on):
    assert "/auth/google" in client.get("/login").text
    assert "/auth/google" in client.get("/signup").text


def test_starting_sends_you_to_google_with_a_state(client, google_on):
    r = _start(client)

    assert r.status_code == 303
    assert r.headers["location"].startswith(oauth.AUTH_ENDPOINT)
    assert "state=" in r.headers["location"]
    assert oauth.STATE_COOKIE in r.cookies or r.cookies.get(oauth.STATE_COOKIE)


# --- the state check, which is the security of the callback ----------------

def test_a_callback_with_no_state_signs_nobody_in(client, google_on,
                                                  monkeypatch):
    """Anybody can hit this URL with any query string. Without the state
    check it is a way to log somebody into an account they do not own, by
    sending them a link."""
    _google_says(monkeypatch)

    r = client.get("/auth/google/callback?code=whatever",
                   follow_redirects=False)

    assert r.status_code == 303
    assert "/login?error=" in r.headers["location"]
    assert not demo_db._USERS


def test_a_forged_state_signs_nobody_in(client, google_on, monkeypatch):
    """A state this server never issued, presented with a matching cookie the
    attacker also wrote. The signature is what tells them apart."""
    import time as time_mod

    _google_says(monkeypatch)
    # The forged state must look FRESH, or the freshness check rejects it and
    # this test passes without the signature check existing at all. It did
    # exactly that the first time it was written.
    forged = f"forged-but-current.{int(time_mod.time())}"
    client.cookies.set(oauth.STATE_COOKIE, f"{forged}.not-a-real-signature")

    r = client.get(f"/auth/google/callback?code=x&state={forged}",
                   follow_redirects=False)

    assert "/login?error=" in r.headers["location"]
    assert not demo_db._USERS


def test_a_real_state_from_a_different_attempt_is_refused(client, google_on,
                                                          monkeypatch):
    """Correctly signed, but not the state THIS browser was given."""
    _google_says(monkeypatch)
    _start(client)
    other = auth.sign_value(oauth.new_state())

    r = client.get(
        f"/auth/google/callback?code=x&state={other.rsplit('.', 1)[0]}",
        follow_redirects=False)

    assert "/login?error=" in r.headers["location"]
    assert not demo_db._USERS


def test_a_stale_sign_in_is_refused(client, google_on, monkeypatch):
    """A correctly signed state from last month is still a state somebody
    could have lifted off a shared machine."""
    import time as time_mod
    old = f"{'x' * 22}.{int(time_mod.time()) - oauth.STATE_MAX_AGE - 60}"
    _google_says(monkeypatch)
    client.cookies.set(oauth.STATE_COOKIE, auth.sign_value(old))

    r = client.get(f"/auth/google/callback?code=x&state={old}",
                   follow_redirects=False)

    assert "/login?error=" in r.headers["location"]
    assert not demo_db._USERS


def _complete(client, monkeypatch, **google):
    """A full, honest sign-in."""
    _google_says(monkeypatch, **google)
    start = _start(client)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    from urllib.parse import unquote
    return client.get(
        f"/auth/google/callback?code=good-code&state={unquote(state)}",
        follow_redirects=False)


# --- what happens when it all checks out -----------------------------------

def test_a_new_person_gets_an_account_with_no_password(client, google_on,
                                                        monkeypatch):
    r = _complete(client, monkeypatch, email="brand-new@example.com")

    assert r.status_code == 303
    assert r.headers["location"] == "/connect?welcome=1"

    user = demo_db.user_by_email("brand-new@example.com")
    assert user is not None
    assert user["password_hash"] is None, "a Google account has no password"
    assert user["google_sub"] == "google-sub-1"


def test_coming_back_signs_you_into_the_same_account(client, google_on,
                                                     monkeypatch):
    _complete(client, monkeypatch, email="repeat@example.com")
    first = demo_db.user_by_email("repeat@example.com")

    # Signed out, coming back another day. Without this the second /auth/google
    # sees a live session and bounces to /account, and the test measures
    # nothing.
    client.cookies.clear()

    # Same person, same sub — but Google now reports a changed address, which
    # is exactly why the join key is the sub and not the email.
    r = _complete(client, monkeypatch, email="changed@example.com",
                  sub="google-sub-1")

    assert r.headers["location"] == "/account"
    assert len(demo_db._USERS) == 1
    assert demo_db.user_by_id(first["id"]) is not None


# --- linking, which is where this feature gets people hacked ---------------

def test_a_verified_address_links_to_the_existing_password_account(
        client, google_on, monkeypatch):
    _signup(client, email="both@example.com")
    existing = demo_db.user_by_email("both@example.com")
    client.cookies.clear()

    _complete(client, monkeypatch, email="both@example.com", verified=True)

    assert len(demo_db._USERS) == 1, "a second account was created"
    linked = demo_db.user_by_id(existing["id"])
    assert linked["google_sub"] == "google-sub-1"
    # And the password still works, because linking adds a way in rather than
    # replacing one.
    assert linked["password_hash"]


def test_an_unverified_address_links_to_nothing(client, google_on,
                                                monkeypatch):
    """THE ONE THAT MATTERS. An identity provider asserting an address it
    never checked must not be a way into somebody else's account."""
    _signup(client, email="victim@example.com")
    victim = demo_db.user_by_email("victim@example.com")
    client.cookies.clear()

    r = _complete(client, monkeypatch, email="victim@example.com",
                  verified=False, sub="attacker-sub")

    assert "/login?error=" in r.headers["location"]
    assert demo_db.user_by_id(victim["id"])["google_sub"] is None
    assert len(demo_db._USERS) == 1, "an account was created for the attacker"


def test_the_refusal_does_not_hand_over_the_session(client, google_on,
                                                    monkeypatch):
    """A refused link must not also, quietly, log anybody in."""
    _signup(client, email="victim2@example.com")
    client.cookies.clear()

    _complete(client, monkeypatch, email="victim2@example.com",
              verified=False, sub="attacker-sub")

    assert client.get("/account", follow_redirects=False).status_code == 401


# --- the password-reset edge ----------------------------------------------

def test_a_google_account_is_not_offered_a_password_reset(client, google_on,
                                                          monkeypatch,
                                                          sent_emails):
    """A reset link would take them to a form for a credential they have
    never had, and that page cannot explain why."""
    _complete(client, monkeypatch, email="googler@example.com")
    client.cookies.clear()

    client.post("/forgot", data={"email": "googler@example.com"},
                follow_redirects=False)

    assert len(sent_emails) == 1
    assert "google" in sent_emails[0]["subject"].lower()
    assert "reset" not in sent_emails[0]["subject"].lower()


def test_the_forgot_page_still_says_the_same_thing_either_way(
        client, google_on, monkeypatch, sent_emails):
    """Whatever it mails, the PAGE must not reveal which addresses have
    accounts, or which kind."""
    _complete(client, monkeypatch, email="googler2@example.com")
    client.cookies.clear()

    google = client.post("/forgot", data={"email": "googler2@example.com"},
                         follow_redirects=False)
    nobody = client.post("/forgot", data={"email": "nobody@example.com"},
                         follow_redirects=False)

    assert google.headers["location"] == nobody.headers["location"]


def test_a_google_account_cannot_be_logged_into_with_a_password(
        client, google_on, monkeypatch):
    """password_hash is null. Nothing must treat that as "any password will
    do"."""
    _complete(client, monkeypatch, email="nopass@example.com")
    client.cookies.clear()

    r = client.post("/login", data={"email": "nopass@example.com",
                                    "password": "any-guess-at-all"},
                    follow_redirects=False)
    assert r.status_code == 200          # back to the form, not signed in
    assert client.get("/account", follow_redirects=False).status_code == 401


# --- the claims parser -----------------------------------------------------

def test_only_an_unambiguous_true_counts_as_verified():
    """Google sends a real boolean, but this claim has been a string in some
    flows and absent in others. Anything that is not clearly true is false —
    the whole point of the field is to be what we refuse to guess about."""
    def verified_for(value):
        claims = {"sub": "s", "email": "a@b.com", "email_verified": value}
        return oauth.identity_from_claims(claims).email_verified

    assert verified_for(True) is True
    assert verified_for("true") is True
    for falsey in (False, "false", None, "", 0, "yes", "1"):
        assert verified_for(falsey) is False, falsey


def test_claims_without_a_subject_are_refused():
    with pytest.raises(oauth.OAuthError):
        oauth.identity_from_claims({"email": "a@b.com"})


def test_claims_without_an_email_are_refused():
    with pytest.raises(oauth.OAuthError):
        oauth.identity_from_claims({"sub": "s"})


# ---------------------------------------------------------------------------
# The generating overlay
# ---------------------------------------------------------------------------

def _manage_with_weeks(client, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _verify_ok())
    return client.get("/l/secret-admin-token").text


def test_generate_form_is_wired_to_the_overlay(client, league, monkeypatch):
    html = _manage_with_weeks(client, monkeypatch)
    form = html[html.index('action="/l/secret-admin-token/generate"'):]
    assert "data-generating" in form[:form.index(">")]
    assert 'id="generating"' in html


def test_overlay_is_hidden_until_submit(client, league, monkeypatch):
    html = _manage_with_weeks(client, monkeypatch)
    tag = html[html.index('id="generating"'):]
    assert "hidden" in tag[:tag.index(">")]


def test_overlay_cycles_real_steps_and_warns_against_refresh(client, league, monkeypatch):
    html = _manage_with_weeks(client, monkeypatch)
    assert "Checking the projections" in html
    assert "don't refresh" in html
    # the back-button case: a restored page must not look mid-generation
    assert 'addEventListener("pageshow"' in html


def test_overlay_respects_reduced_motion():
    css = (Path(__file__).resolve().parent.parent / "web" / "static" / "style.css").read_text()
    blocks = [b[:b.index("}\n}")] for b in css.split("prefers-reduced-motion")[1:]]
    assert any(".laces" in b and ".football" in b and "animation: none" in b
               for b in blocks)


# ---------------------------------------------------------------------------
# Staff accounts, the plan link, and the offer after signup
# ---------------------------------------------------------------------------

def _billing_on(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test")


def test_staff_is_its_own_plan_and_stripe_status_cannot_demote_it():
    import plans
    staff = {"plan": "staff", "plan_status": "canceled"}
    assert plans.plan_for(staff).key == plans.STAFF
    assert plans.is_paid(staff)
    assert (plans.plan_for(staff).regenerations_per_week
            > plans.PLANS[plans.PAID].regenerations_per_week)


def test_a_staff_owner_can_regenerate_past_the_paid_allowance(client, league, monkeypatch):
    demo_db.set_plan(league["user_id"], plan="staff")
    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda db_, lg, wk: demo_db.save_paper(
                            lg["id"], wk, lg["season"], "p", "u", {"headline": "x"}))
    for _ in range(PAID_ALLOWANCE + 3):
        r = _generate(client)
        assert "used+all" not in r.headers.get("location", ""), r.headers
    paper = demo_db.get_paper(league["id"], league["season"], 1)
    assert webapp.regenerations_used(paper) == PAID_ALLOWANCE + 2


def test_a_paid_owner_is_still_stopped_at_the_paid_allowance(client, league, monkeypatch):
    """The other side of the staff test: without it, a staff plan that
    accidentally applied to everybody would pass."""
    monkeypatch.setattr(webapp, "generate_and_store",
                        lambda db_, lg, wk: demo_db.save_paper(
                            lg["id"], wk, lg["season"], "p", "u", {"headline": "x"}))
    for _ in range(PAID_ALLOWANCE + 1):
        _generate(client)
    assert "used+all" in _generate(client).headers["location"]


def test_the_webhook_leaves_a_staff_account_alone():
    from web import billing
    user = demo_db.create_user("staff@example.com", "x")
    demo_db.set_plan(user["id"], plan="staff")
    demo_db.remember_stripe_customer(user["id"], "cus_staff")
    billing.apply_subscription(demo_db, {"customer": "cus_staff",
                                         "status": "canceled", "id": "sub_s"})
    assert demo_db.user_by_id(user["id"])["plan"] == "staff"


def test_signup_raises_the_upgrade_offer(client, monkeypatch):
    _billing_on(monkeypatch)
    _signup(client)
    html = client.get("/connect?welcome=1").text
    assert 'id="upgrade-offer"' in html
    assert 'action="/billing/checkout"' in html
    assert "Maybe later" in html


def test_the_offer_is_only_shown_on_arrival(client, monkeypatch):
    _billing_on(monkeypatch)
    _signup(client)
    assert 'id="upgrade-offer"' not in client.get("/connect").text


def test_no_offer_when_checkout_cannot_take_money(client, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    _signup(client)
    assert 'id="upgrade-offer"' not in client.get("/connect?welcome=1").text


def test_no_offer_to_somebody_already_paying(client, monkeypatch):
    _billing_on(monkeypatch)
    _signup(client)
    demo_db.set_plan(demo_db.user_by_email("john@example.com")["id"],
                     plan="paid", status="active")
    assert 'id="upgrade-offer"' not in client.get("/connect?welcome=1").text


def test_the_header_says_upgrade_to_free_accounts_and_plan_to_paid(client):
    _signup(client)
    html = client.get("/account").text
    assert 'class="nav-upgrade" href="/account#plan"' in html
    demo_db.set_plan(demo_db.user_by_email("john@example.com")["id"],
                     plan="paid", status="active")
    html = client.get("/account").text
    assert "nav-upgrade" not in html
    assert '<a href="/account#plan">Your plan</a>' in html


def test_the_plan_card_is_first_on_the_account_page(client):
    _signup(client)
    html = client.get("/account").text
    assert html.index('id="plan"') < html.index("Start a new paper")


def test_free_manage_page_points_at_the_upgrade(client, free_league, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _verify_ok())
    html = client.get("/l/free-admin-token").text
    assert 'class="plan-note"' in html


def test_paid_manage_page_does_not_nag(client, league, monkeypatch):
    monkeypatch.setattr(webapp, "get_provider", _verify_ok())
    assert 'class="plan-note"' not in client.get("/l/secret-admin-token").text


def test_the_header_is_about_the_viewer_not_the_leagues_owner(client, league, monkeypatch):
    """A free account holding the manage link to somebody else's paid league
    still gets the upgrade link: the button subscribes whoever presses it."""
    monkeypatch.setattr(webapp, "get_provider", _verify_ok())
    _signup(client)
    html = client.get("/l/secret-admin-token").text
    assert 'class="nav-upgrade"' in html


def test_the_500_log_line_names_the_actual_error(league, monkeypatch, capsys):
    """It used to print "NoneType: None", because the handler runs after the
    except block has closed. The one line in the logs has to say what broke."""
    def boom(*a, **k):
        raise ZeroDivisionError("the real cause")
    monkeypatch.setattr(demo_db, "save_manager", boom)
    c = TestClient(webapp.app, raise_server_exceptions=False)
    r = c.post("/l/secret-admin-token/managers",
               data={"handle": "steve", "display_name": "", "notes": ""})
    assert r.status_code == 500
    out = capsys.readouterr()
    logged = out.out + out.err
    assert "ZeroDivisionError: the real cause" in logged
    assert "NoneType: None" not in logged


# ---------------------------------------------------------------------------
# The season pass: a second price for the same paid plan
# ---------------------------------------------------------------------------

def _season_on(monkeypatch):
    _billing_on(monkeypatch)
    monkeypatch.setenv("STRIPE_SEASON_PRICE_ID", "price_season")


class _FakeCheckout:
    """Stands in for stripe.checkout.Session and records what it was asked."""

    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("S", (), {"url": "https://stripe.test/c"})()


@pytest.fixture
def fake_stripe(monkeypatch):
    from web import billing
    checkout = _FakeCheckout()
    import stripe as real_stripe
    sdk = type("SDK", (), {
        "InvalidRequestError": real_stripe.InvalidRequestError,
        "StripeError": real_stripe.StripeError,
        "checkout": type("C", (), {"Session": checkout})(),
        "Customer": type("Cu", (), {"create": staticmethod(
            lambda **k: type("X", (), {"id": "cus_new"})())}),
    })()
    monkeypatch.setattr(billing, "_stripe", lambda: sdk)
    return checkout


@pytest.mark.parametrize("term,price", [("season", "price_season"),
                                        ("monthly", "price_test")])
def test_each_term_checks_out_at_its_own_price(client, monkeypatch, fake_stripe,
                                               term, price):
    _season_on(monkeypatch)
    _signup(client)
    r = client.post("/billing/checkout", data={"term": term},
                    follow_redirects=False)
    assert r.headers["location"] == "https://stripe.test/c"
    assert fake_stripe.calls[-1]["line_items"] == [{"price": price, "quantity": 1}]
    assert fake_stripe.calls[-1]["mode"] == "subscription"


def test_no_term_means_monthly(client, monkeypatch, fake_stripe):
    """Older pages, and anything cached, post no term at all."""
    _season_on(monkeypatch)
    _signup(client)
    client.post("/billing/checkout", follow_redirects=False)
    assert fake_stripe.calls[-1]["line_items"][0]["price"] == "price_test"


def test_a_season_pass_is_refused_when_it_is_not_set_up(client, monkeypatch, fake_stripe):
    _billing_on(monkeypatch)
    monkeypatch.delenv("STRIPE_SEASON_PRICE_ID", raising=False)
    _signup(client)
    r = client.post("/billing/checkout", data={"term": "season"},
                    follow_redirects=False)
    assert "isn't+available" in r.headers["location"]
    assert fake_stripe.calls == []


def test_a_made_up_term_is_refused(client, monkeypatch, fake_stripe):
    _season_on(monkeypatch)
    _signup(client)
    r = client.post("/billing/checkout", data={"term": "lifetime"},
                    follow_redirects=False)
    assert "isn't+available" in r.headers["location"]
    assert fake_stripe.calls == []


def test_the_season_pass_leads_when_it_exists(client, monkeypatch):
    _season_on(monkeypatch)
    _signup(client)
    for path in ("/account", "/connect?welcome=1"):
        html = client.get(path).text
        assert 'name="term" value="season"' in html, path
        assert html.index('value="season"') < html.index('value="monthly"'), path
        assert "$19.99" in html


def test_monthly_only_when_there_is_no_season_price(client, monkeypatch):
    _billing_on(monkeypatch)
    monkeypatch.delenv("STRIPE_SEASON_PRICE_ID", raising=False)
    _signup(client)
    html = client.get("/account").text
    assert 'value="season"' not in html
    assert 'name="term" value="monthly"' in html
    assert "$19.99" not in html


def test_a_yearly_subscription_is_the_paid_plan_like_any_other():
    """The webhook never asks which price was bought."""
    from web import billing
    user = demo_db.create_user("season@example.com", "x")
    demo_db.remember_stripe_customer(user["id"], "cus_season")
    billing.apply_subscription(demo_db, {
        "customer": "cus_season", "status": "active", "id": "sub_y",
        "items": {"data": [{"price": {"id": "price_season",
                                      "recurring": {"interval": "year"}},
                            "current_period_end": 1820000000}]}})
    fresh = demo_db.user_by_id(user["id"])
    assert fresh["plan"] == "paid"
    assert (fresh["plan_renews_at"] or "").startswith("2027-")


# --- switching between Stripe's live and test keys --------------------------

def test_a_customer_from_the_other_mode_is_replaced_not_a_500(client, monkeypatch,
                                                               fake_stripe):
    """Production, 21 Sep: an account got a live-mode customer, the keys were
    switched to test mode, and every checkout after that 500'd with "No such
    customer ... a similar object exists in live mode"."""
    import stripe as real_stripe
    _season_on(monkeypatch)
    _signup(client)
    user = demo_db.user_by_email("john@example.com")
    demo_db.remember_stripe_customer(user["id"], "cus_LIVEONLY")

    real_create = fake_stripe.create

    def create(**kwargs):
        if kwargs["customer"] == "cus_LIVEONLY":
            raise real_stripe.InvalidRequestError(
                "No such customer: 'cus_LIVEONLY'; a similar object exists in "
                "live mode", param="customer", code="resource_missing")
        return real_create(**kwargs)
    monkeypatch.setattr(fake_stripe, "create", create)

    r = client.post("/billing/checkout", data={"term": "season"},
                    follow_redirects=False)

    assert r.headers["location"] == "https://stripe.test/c"
    assert fake_stripe.calls[-1]["customer"] == "cus_new"
    assert demo_db.user_by_id(user["id"])["stripe_customer_id"] == "cus_new"


def test_any_other_stripe_error_is_a_message_not_a_500(client, monkeypatch,
                                                       fake_stripe):
    import stripe as real_stripe
    _season_on(monkeypatch)
    _signup(client)

    def create(**kwargs):
        raise real_stripe.InvalidRequestError("No such price: 'price_season'",
                                              param="line_items[0][price]",
                                              code="resource_missing")
    monkeypatch.setattr(fake_stripe, "create", create)

    r = client.post("/billing/checkout", data={"term": "season"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "Nothing+was+charged" in r.headers["location"]


def test_the_homepage_links_the_sample_paper_when_there_is_one(client, monkeypatch):
    monkeypatch.setattr(webapp, "SAMPLE_PAPER_URL", "/p/sample-1234")
    assert 'href="/p/sample-1234"' in client.get("/").text


def test_no_sample_link_when_none_is_set(client, monkeypatch):
    monkeypatch.setattr(webapp, "SAMPLE_PAPER_URL", "")
    assert "Read a whole paper" not in client.get("/").text


def test_a_manage_link_is_never_used_as_the_sample(monkeypatch):
    import importlib
    monkeypatch.setenv("SAMPLE_PAPER_URL", "https://commissionersdesk.com/l/secret-token")
    fresh = importlib.reload(webapp)
    try:
        assert fresh.SAMPLE_PAPER_URL == ""
    finally:
        monkeypatch.delenv("SAMPLE_PAPER_URL")
        importlib.reload(webapp)
