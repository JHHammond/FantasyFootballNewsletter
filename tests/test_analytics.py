"""Usage analytics (24 Oct). Run with: python -m pytest tests/test_analytics.py -v

The rule that matters most here: the Cloudflare beacon records the full path
of whatever page it runs on, and this app's credentials ARE paths. So the
beacon must never appear on a token page, and these tests check that from both
ends: the allowlist against every route the app registers, and the rendered
HTML of a real manage page.
"""

import re

import pytest

from tests.test_web import clean_state, client, league  # noqa: F401 — fixtures
from web import analytics, demo_db
from web import app as webapp

TOKEN = "47e0c5f29618493b8e800d0a066befeb"
BROWSER = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                         "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"}
IMESSAGE = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_1) AppleWebKit/601.2.4 "
                          "(KHTML, like Gecko) Version/9.0.1 Safari/601.2.4 facebookexternalhit/1.1 "
                          "Facebot Twitterbot/1.0"}


@pytest.fixture(autouse=True)
def _events(monkeypatch):
    demo_db._EVENTS.clear()
    monkeypatch.setenv("CF_BEACON_TOKEN", TOKEN)
    yield


def names():
    return [e["name"] for e in demo_db._EVENTS]


# --- the beacon -------------------------------------------------------------

def test_public_pages_carry_the_beacon(client):
    for path in ("/", "/privacy", "/terms"):
        body = client.get(path).text
        assert "static.cloudflareinsights.com/beacon.min.js" in body, path
        assert TOKEN in body


def test_no_token_no_beacon(client, monkeypatch):
    monkeypatch.delenv("CF_BEACON_TOKEN")
    assert "cloudflareinsights" not in client.get("/").text


def test_no_secret_route_is_on_the_allowlist():
    secret = re.compile(r"^/(l|publisher|staff|recover|reset|unsubscribe|stop|"
                        r"subscribe/confirm|account|billing|auth|login|signup|tasks)(/|$)")
    checked = 0
    for route in webapp.app.routes:
        path = getattr(route, "path", "")
        if not secret.match(path):
            continue
        sample = re.sub(r"\{[^}]+\}", "x", path)
        assert not analytics.beacon_allowed(sample), path
        checked += 1
    assert checked > 30          # the regex really did find the token routes


def test_manage_page_never_carries_the_beacon(client, league):
    body = client.get("/l/secret-admin-token").text
    assert "secret-admin-token" in body        # the page really rendered
    assert "cloudflareinsights" not in body


def test_a_404_under_a_token_path_does_not_either(client):
    assert "cloudflareinsights" not in client.get("/l/not-a-real-token").text


def test_paper_gets_the_beacon_but_not_the_homepage_preview(client, league):
    path = demo_db.storage_path(league["public_slug"], 2025, 3)
    demo_db._STORAGE[path] = "<html><head></head><body><p>Paper</p></body></html>"
    url = f"/p/{league['public_slug']}/2025/week-3"
    assert "cloudflareinsights" in client.get(url, headers=BROWSER).text
    assert "cloudflareinsights" not in client.get(url + "?embed=1", headers=BROWSER).text


# --- reads, bots and events ---------------------------------------------------

def _paper(league):
    demo_db.save_paper(league["id"], 3, 2025, "p", "u", {"headline": "X"})
    demo_db._STORAGE[demo_db.storage_path(league["public_slug"], 2025, 3)] = "<html><body>x</body></html>"
    return f"/p/{league['public_slug']}/2025/week-3"


def test_link_previews_are_not_readers(client, league):
    url = _paper(league)
    client.get(url, headers=IMESSAGE)
    client.get(url, headers={"user-agent": "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)"})
    client.get(url, headers={"user-agent": ""})
    assert demo_db.get_paper(league["id"], 2025, 3).get("view_count") in (None, 0)
    assert names() == []
    client.get(url, headers=BROWSER)
    assert demo_db.get_paper(league["id"], 2025, 3)["view_count"] == 1
    assert names() == ["paper_read"]
    assert demo_db._EVENTS[0]["league_id"] == league["id"]


def test_browser_events_are_an_allowlist(client):
    assert client.post("/e", data={"n": "link_copied", "where": "published"}, headers=BROWSER).status_code == 204
    assert client.post("/e", data={"n": "card_shared", "where": "luck"}, headers=BROWSER).status_code == 204
    assert client.post("/e", data={"n": "anything", "where": "published"}, headers=BROWSER).status_code == 204
    assert client.post("/e", data={"n": "card_shared", "where": "nowhere"}, headers=BROWSER).status_code == 204
    assert names() == ["link_copied", "card_shared"]
    assert demo_db._EVENTS[0]["props"] == {"where": "published"}


def test_browser_events_are_capped_per_ip(client, monkeypatch):
    monkeypatch.setattr(webapp, "CLIENT_EVENTS_PER_HOUR", 3)
    webapp._client_event_hits.clear()
    for _ in range(5):
        client.post("/e", data={"n": "link_copied", "where": "manage"}, headers=BROWSER)
    assert len(names()) == 3


def test_signing_up_is_a_funnel_step(client):
    client.post("/signup", data={"email": "new@example.com", "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"}, headers=BROWSER)
    assert "account_created" in names()


@pytest.mark.parametrize("ua,bot", [
    (BROWSER["user-agent"], False),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/130.0 Safari/537.36", False),
    (IMESSAGE["user-agent"], True),
    ("Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)", True),
    ("WhatsApp/2.23.20.0", True),
    ("TelegramBot (like TwitterBot)", True),
    ("Googlebot/2.1", True),
    ("curl/8.4.0", True),
    ("", True),
    (None, True),
])
def test_is_bot(ua, bot):
    assert analytics.is_bot(ua) is bot


# --- the staff table --------------------------------------------------------------

def test_weekly_counts_and_the_staff_table(client):
    for n in ["connect_viewed"] * 4 + ["league_connected"]:
        demo_db.log_event(n)
    demo_db.log_event("paper_generated", "lg-1")
    demo_db.log_event("paper_generated", "lg-1")
    demo_db.log_event("paper_generated", "lg-2")
    week = analytics.weekly_table(demo_db.events_weekly(8))
    assert len(week) == 1
    assert week[0]["connect_viewed"] == 4 and week[0]["league_connected"] == 1
    assert week[0]["connect_rate"] == 25
    assert week[0]["paper_generated"] == 3 and week[0]["paper_generated_leagues"] == 2

    client.post("/signup", data={"email": "staff@example.com", "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"})
    demo_db.update_user(demo_db.user_by_email("staff@example.com")["id"], {"plan": "staff"})
    body = client.get("/staff/stats?fresh=1").text
    # Signing up lands on /connect, which is one more view: 1 league in 5.
    assert "Funnel, by week" in body and "20%" in body
    assert "cloudflareinsights" not in body


# --- the paywall (migration 063) --------------------------------------------

def test_paywall_events_need_a_known_lock(client):
    for n in ("gate_seen", "gate_hit", "upgrade_clicked"):
        assert client.post("/e", data={"n": n, "where": "board"}, headers=BROWSER).status_code == 204
    client.post("/e", data={"n": "gate_seen", "where": "the-moon"}, headers=BROWSER)
    client.post("/e", data={"n": "gate_seen", "where": ""}, headers=BROWSER)
    assert names() == ["gate_seen", "gate_hit", "upgrade_clicked"]
    assert all(e["props"] == {"where": "board"} for e in demo_db._EVENTS)


def test_checkout_remembers_which_lock_sent_them(client, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test")
    client.post("/signup", data={"email": "buyer@example.com", "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"}, headers=BROWSER)
    monkeypatch.setattr(webapp.billing, "checkout_url", lambda *a, **k: "https://stripe.test/c")
    client.post("/billing/checkout", data={"term": "monthly", "gate": "chart_more"},
                headers=BROWSER, follow_redirects=False)
    client.post("/billing/checkout", data={"term": "monthly", "gate": "<script>"},
                headers=BROWSER, follow_redirects=False)
    started = [e["props"] for e in demo_db._EVENTS if e["name"] == "checkout_started"]
    assert started == [{"where": "chart_more", "term": "monthly"}, {"where": "", "term": "monthly"}]


def test_gate_table_reads_like_a_funnel():
    rows = [
        {"gate": "board", "name": "gate_seen", "events": 200},
        {"gate": "board", "name": "upgrade_clicked", "events": 10},
        {"gate": "board", "name": "checkout_started", "events": 4},
        {"gate": "chart_more", "name": "gate_hit", "events": 50},
        {"gate": "chart_more", "name": "upgrade_clicked", "events": 5},
        {"gate": "chart_more", "name": "checkout_started", "events": 5},
        {"gate": "(none)", "name": "subscribed", "events": 3},
        {"gate": "(none)", "name": "cancelled", "events": 1},
    ]
    t = analytics.gate_table(rows)
    assert [r["gate"] for r in t["rows"]] == ["chart_more", "board"]
    assert t["rows"][0]["click_rate"] == 10.0 and t["rows"][1]["click_rate"] == 5.0
    assert t["rows"][1]["label"] == analytics.GATES["board"]
    assert t["paid"] == 3 and t["cancelled"] == 1
    assert analytics.gate_table([]) == {"rows": [], "paid": 0, "cancelled": 0}


def test_demo_gate_report_matches_the_table(client):
    for n in ("gate_seen", "gate_seen", "upgrade_clicked"):
        client.post("/e", data={"n": n, "where": "feed"}, headers=BROWSER)
    demo_db.log_event("subscribed", None, {"status": "active"})
    t = analytics.gate_table(demo_db.gate_report(28))
    assert t["rows"][0]["gate"] == "feed" and t["rows"][0]["gate_seen"] == 2
    assert t["rows"][0]["upgrade_clicked"] == 1 and t["paid"] == 1


def test_subscribing_is_counted_once_not_every_renewal():
    from web import billing
    user = demo_db.create_user("renews@example.com", "x")
    sub = {"id": "sub_r", "customer": "cus_r", "status": "active",
           "metadata": {"user_id": user["id"]}, "current_period_end": 1900000000}
    billing.apply_subscription(demo_db, sub)
    billing.apply_subscription(demo_db, sub)  # a renewal: same status
    billing.apply_subscription(demo_db, {**sub, "status": "canceled"})
    assert [n for n in names() if n in ("subscribed", "cancelled")] == ["subscribed", "cancelled"]


def test_the_locks_are_tagged_on_the_market(client):
    body = client.get("/market", headers=BROWSER).text
    # Whatever the page shows a signed-out visitor, each lock it shows says which it is.
    for g in ("data-gate-seen=", "data-gate="):
        assert g in body or "mk2-locked" not in body
    assert "window.cdGate" in body


def test_staff_sees_the_paywall_table(client):
    client.post("/e", data={"n": "gate_seen", "where": "board"}, headers=BROWSER)
    client.post("/signup", data={"email": "staff@example.com", "password": "correct horse battery 9",
                                 "confirm": "correct horse battery 9"})
    demo_db.update_user(demo_db.user_by_email("staff@example.com")["id"], {"plan": "staff"})
    body = client.get("/staff/stats?fresh=1").text
    assert "Paywall, last 28 days" in body and analytics.GATES["board"] in body
