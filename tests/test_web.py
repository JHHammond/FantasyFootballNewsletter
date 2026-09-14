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


def test_tone_changes_the_system_prompt():
    from writer import system_prompt

    assert "NO MERCY" in system_prompt("brutal")
    assert "KEEP IT LIGHT" in system_prompt("friendly")
    assert "No profanity" in system_prompt("friendly")
    assert system_prompt("standard") == system_prompt(None)


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
    assert 'name="league_id"' in text


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
    from writer import system_prompt
    assert "NO MERCY" in system_prompt("brutal")
    assert "KEEP IT LIGHT" in system_prompt("friendly")
    assert "Could this sentence be moved" in system_prompt("friendly")


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


def test_rate_limit_keys_do_not_accumulate_forever():
    """Reading a key used to create it, and nothing was ever evicted."""
    webapp._HITS.clear()
    webapp._rate_limited("probe", 5)
    assert list(webapp._HITS) == ["probe"]
    # A key that was only ever checked shouldn't linger as an empty list.
    assert webapp._HITS["probe"]


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
    # Not part of the interface app.py uses: `client` is db.py's own Supabase
    # handle, and the rest are names it imported.
    missing -= {"create_client", "Client", "client"}
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
