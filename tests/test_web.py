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
