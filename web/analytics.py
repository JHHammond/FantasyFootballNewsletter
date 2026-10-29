"""
Usage numbers, two layers (John, 24 Oct).

1. Cloudflare Web Analytics for page traffic. One script tag, no cookies, and
   only on the pages in BEACON_PATHS.
2. A first-party event log (migration 060) for the funnel: connect page →
   league connected → paper made → paper read → link copied / card shared.
   Cloudflare can't count conversions, and a count we can explain is the one
   an advertiser will believe.

Why an allowlist for the beacon and not a denylist: this app's credentials are
URLs. /l/<admin_token>, its live-edit pages and /publisher/<token> are bearer
secrets, and any analytics script records the full path of the page it runs
on. A denylist fails open: the next token route somebody adds gets tracked by
default. An allowlist fails closed: a new public page just goes uncounted
until it's added here. tests/test_analytics.py checks both directions.

Pages that take something personal in the query string stay off the list
too: /signup?email=… and /login?next=/l/<token>.
"""

from __future__ import annotations

import json
import os
import re

from markupsafe import Markup

#: Public pages that carry the beacon. Matched against the path alone.
BEACON_PATHS = [re.compile(p) for p in (
    r"/",
    r"/connect",
    r"/connect/(sleeper|espn|yahoo)",
    r"/around",
    r"/around/rank",
    r"/around/players",
    r"/undefeated",
    r"/luck",
    r"/luck/[^/]+",
    r"/rankings",
    r"/market",
    r"/market/player/[^/]+",
    r"/p/[^/]+",                     # a league's archive
    r"/p/[^/]+/\d+/week-\d+",        # a paper (served raw; see beacon_for_paper)
    r"/privacy",
    r"/terms",
)]


def beacon_token() -> str:
    """Read per call, so setting it on Render needs no code change."""
    return os.getenv("CF_BEACON_TOKEN", "").strip()


def beacon_allowed(path: str) -> bool:
    return any(p.fullmatch(path or "") for p in BEACON_PATHS)


def _snippet(token: str) -> str:
    config = json.dumps({"token": token})
    return ("<script defer src=\"https://static.cloudflareinsights.com/beacon.min.js\" "
            f"data-cf-beacon='{config}'></script>")


def beacon(request) -> Markup:
    """The script tag for this page, or nothing. Used by base.html."""
    token = beacon_token()
    if not token or request is None or not beacon_allowed(request.url.path):
        return Markup("")
    return Markup(_snippet(token))


def beacon_for_paper(html: str, path: str) -> str:
    """Papers are stored HTML, not templates, so the tag goes in at serve time."""
    token = beacon_token()
    if not token or not beacon_allowed(path):
        return html
    at = html.rfind("</body>")
    if at == -1:
        return html
    return html[:at] + _snippet(token) + html[at:]


# ---------------------------------------------------------------------------
# Bots
#
# When a paper link lands in iMessage, Slack or Discord, the platform fetches
# it to build the preview card. Server-side that looks exactly like a reader,
# so every share used to count as a read. These are the fetchers' own names
# for themselves; iMessage announces itself as facebookexternalhit + Twitterbot.
# ---------------------------------------------------------------------------

_BOT_UA = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|facebookexternalhit|facebot|whatsapp|"
    r"slack|discord|telegram|skypeuripreview|embedly|preview|"
    r"curl/|wget/|python-|httpx|go-http-client|okhttp|headless",
    re.IGNORECASE,
)


def is_bot(user_agent: str | None) -> bool:
    ua = (user_agent or "").strip()
    return not ua or bool(_BOT_UA.search(ua))


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

#: Server-side steps, in funnel order, with how /staff/stats labels them.
FUNNEL = [
    ("account_created", "Accounts made"),
    ("connect_viewed", "Connect page"),
    ("league_connected", "Leagues connected"),
    ("paper_generated", "Papers made"),
    ("paper_read", "Paper reads"),
    ("link_copied", "Links copied"),
    ("card_shared", "Cards shared"),
]

#: Every lock on the site, by the name its events carry (migration 063). John,
#: 26 Oct: "tons of people are signing up. Very few are paying" - so which
#: paid feature does anybody actually reach for?
GATES = {
    "chart_team": "Chart: add your team",
    "chart_more": "Chart: more than five players",
    "board": "Market board past the top 20",
    "movers": "Moving this week",
    "lockbar": "Market: more of your leagues",
    "getsell": "Player page: Get him / Sell him",
    "player_trades": "Player page: every trade",
    "feed": "Live trades: the rest of the feed",
    "feed_filters": "Live trades: filters",
    "offer": "Upgrade pop-up",
    "account": "Account and plan pages",
}
#: The steps, in order, with how /staff/stats labels them.
PAYWALL_STEPS = [("gate_seen", "Seen"), ("gate_hit", "Hit"), ("upgrade_clicked", "Clicked"),
                 ("checkout_started", "Checkout")]

#: What a browser may report through POST /e, and from where. Anything else is
#: dropped, so the endpoint can't be used to write arbitrary rows.
CLIENT_EVENTS = {
    "link_copied": {"manage", "published"},
    "card_shared": {"around", "luck", "undefeated"},
    "gate_seen": set(GATES),
    "gate_hit": set(GATES),
    "upgrade_clicked": set(GATES),
}


def gate_table(rows: list[dict]) -> dict:
    """gate_report rows → {rows: one per lock, most clicked first; paid: n; cancelled: n}."""
    by: dict[str, dict] = {}
    paid = cancelled = 0
    for r in rows or []:
        n, g, c = r.get("name"), r.get("gate"), int(r.get("events") or 0)
        if n == "subscribed":
            paid += c
            continue
        if n == "cancelled":
            cancelled += c
            continue
        row = by.setdefault(g, {"gate": g, "label": GATES.get(g, g)})
        row[n] = row.get(n, 0) + c
    out = sorted(by.values(), key=lambda r: (-r.get("checkout_started", 0), -r.get("upgrade_clicked", 0),
                                             -r.get("gate_seen", 0), r["gate"]))
    for r in out:
        seen = r.get("gate_seen", 0) + r.get("gate_hit", 0)
        r["click_rate"] = round(100 * r.get("upgrade_clicked", 0) / seen, 1) if seen else None
    return {"rows": out, "paid": paid, "cancelled": cancelled}


def weekly_table(rows: list[dict], weeks: int = 8) -> list[dict]:
    """events_weekly rows → one dict per week, newest first, for the template."""
    by_week: dict[str, dict] = {}
    for r in rows or []:
        start = str(r.get("week_start"))[:10]
        w = by_week.setdefault(start, {"week_start": start})
        w[r.get("name")] = int(r.get("events") or 0)
        w[f"{r.get('name')}_leagues"] = int(r.get("leagues") or 0)
    out = sorted(by_week.values(), key=lambda w: w["week_start"], reverse=True)[:weeks]
    for w in out:
        seen = w.get("connect_viewed", 0)
        w["connect_rate"] = round(100 * w.get("league_connected", 0) / seen) if seen else None
    return out
