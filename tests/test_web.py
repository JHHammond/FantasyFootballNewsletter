"""
Web app tests. Run with: python -m pytest tests/test_web.py -v

The database layer is stubbed with an in-memory fake, so these run with no
Supabase project and no network. They exercise routing, the token
authorization model, and template rendering.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi.testclient import TestClient  # noqa: E402

from web import app as webapp  # noqa: E402
from web import slugs  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory stand-in for the Supabase layer
# ---------------------------------------------------------------------------

class FakeDB:
    def __init__(self):
        self.leagues = {}
        self.jokes = {}
        self.papers = {}
        self.storage = {}
        self._id = 0

    def _next_id(self):
        self._id += 1
        return f"id-{self._id}"

    def create_league(self, **kw):
        league = {"id": self._next_id(), **kw}
        self.leagues[league["id"]] = league
        return league

    def league_by_admin_token(self, token):
        return next((l for l in self.leagues.values() if l["admin_token"] == token), None)

    def league_by_public_slug(self, slug):
        found = next((l for l in self.leagues.values() if l["public_slug"] == slug), None)
        if not found:
            return None
        return {k: v for k, v in found.items() if k != "admin_token"}

    def find_existing_league(self, provider, platform_league_id, season):
        return next(
            (l for l in self.leagues.values()
             if l["provider"] == provider
             and l["platform_league_id"] == platform_league_id
             and l["season"] == season),
            None,
        )

    def update_league(self, league_id, fields):
        self.leagues[league_id].update(fields)

    def get_jokes(self, league_id):
        return [j for j in self.jokes.values()
                if j["league_id"] == league_id and j["active"]]

    def add_joke(self, league_id, text):
        jid = self._next_id()
        self.jokes[jid] = {"id": jid, "league_id": league_id, "joke": text, "active": True}

    def deactivate_joke(self, joke_id, league_id):
        joke = self.jokes.get(joke_id)
        if joke and joke["league_id"] == league_id:
            joke["active"] = False

    def storage_path(self, slug, season, week):
        return f"{slug}/{season}/week-{int(week):02d}.html"

    def upload_paper(self, slug, season, week, html):
        path = self.storage_path(slug, season, week)
        self.storage[path] = html
        return path, f"https://cdn.example/{path}"

    def download_paper(self, path):
        return self.storage.get(path)

    def save_paper(self, league_id, week, season, path, url, ai_cache):
        key = (league_id, season, week)
        self.papers[key] = {
            "id": self._next_id(), "league_id": league_id, "week": week,
            "season": season, "storage_path": path, "public_url": url,
            "generated_at": "2025-09-10T00:00:00Z",
        }

    def list_papers(self, league_id):
        return [p for k, p in self.papers.items() if k[0] == league_id]

    def get_paper(self, league_id, season, week):
        return self.papers.get((league_id, season, week))


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(webapp, "db", fake)
    return fake


@pytest.fixture
def client(fake_db):
    return TestClient(webapp.app)


@pytest.fixture
def league(fake_db):
    return fake_db.create_league(
        provider="sleeper",
        platform_league_id="123",
        league_name="Kevlarville",
        paper_name="The Kevlarville Times",
        commissioner_name="johnhenryhammond",
        season=2025,
        public_slug="kevlarville-7f3a",
        admin_token="secret-admin-token",
    )


@pytest.fixture(autouse=True)
def reset_rate_limits():
    webapp._GENERATION_LOG.clear()
    yield
    webapp._GENERATION_LOG.clear()


# ---------------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------------

def test_landing_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "deserves a" in r.text
    assert "league_id" in r.text


def test_landing_mentions_no_account(client):
    """The pitch. If this disappears, the whole reason for the redesign did."""
    r = client.get("/")
    assert "No account" in r.text


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


# ---------------------------------------------------------------------------
# League creation
# ---------------------------------------------------------------------------

def test_create_league_redirects_to_admin_page(client, fake_db, monkeypatch):
    monkeypatch.setattr(
        webapp, "get_provider",
        lambda name, **kw: type("P", (), {"verify_league": lambda self, i, s: "Kevlarville"})(),
    )
    r = client.post("/leagues", data={
        "provider": "sleeper", "league_id": "999",
        "commissioner": "john", "paper_name": "", "season": 2025,
    }, follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"].startswith("/l/")
    assert len(fake_db.leagues) == 1


def test_create_league_rejects_unknown_id(client, monkeypatch):
    monkeypatch.setattr(
        webapp, "get_provider",
        lambda name, **kw: type("P", (), {"verify_league": lambda self, i, s: None})(),
    )
    r = client.post("/leagues", data={
        "provider": "sleeper", "league_id": "bogus", "season": 2025,
    })
    assert r.status_code == 200
    assert "Couldn&#39;t find that league" in r.text or "Couldn't find that league" in r.text


def test_duplicate_league_does_not_leak_the_admin_token(client, league):
    """Anyone can read a Sleeper league ID off a URL. Submitting one must never
    hand back control of an existing paper."""
    r = client.post("/leagues", data={
        "provider": "sleeper", "league_id": "123", "season": 2025,
    })
    assert r.status_code == 200
    assert "already has a paper" in r.text
    assert "secret-admin-token" not in r.text
    assert league["public_slug"] in r.text


def test_league_creation_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(
        webapp, "get_provider",
        lambda name, **kw: type("P", (), {"verify_league": lambda self, i, s: "L"})(),
    )
    for i in range(webapp.LEAGUE_CREATES_PER_HOUR):
        client.post("/leagues", data={"league_id": f"id{i}", "season": 2025})
    r = client.post("/leagues", data={"league_id": "one-too-many", "season": 2025})
    assert "several leagues" in r.text


# ---------------------------------------------------------------------------
# Authorization by token
# ---------------------------------------------------------------------------

def test_manage_page_requires_the_right_token(client, league):
    assert client.get("/l/secret-admin-token").status_code == 200
    assert client.get("/l/wrong-token").status_code == 404


def test_bad_token_is_a_404_not_a_403(client, league):
    """A wrong guess should be indistinguishable from a league that isn't there."""
    r = client.get("/l/definitely-not-a-real-token")
    assert r.status_code == 404
    assert "403" not in r.text


def test_manage_page_warns_to_bookmark_on_first_visit(client, league):
    r = client.get("/l/secret-admin-token?new=1")
    assert "Bookmark this page" in r.text


def test_manage_page_shows_the_public_share_link(client, league):
    r = client.get("/l/secret-admin-token")
    assert f"p/{league['public_slug']}" in r.text


# ---------------------------------------------------------------------------
# Jokes
# ---------------------------------------------------------------------------

def test_add_and_remove_joke(client, fake_db, league):
    client.post("/l/secret-admin-token/jokes", data={"joke": "Nick benches his best player"})
    jokes = fake_db.get_jokes(league["id"])
    assert len(jokes) == 1

    client.post(f"/l/secret-admin-token/jokes/{jokes[0]['id']}/remove")
    assert fake_db.get_jokes(league["id"]) == []


def test_cannot_add_jokes_without_the_token(client, league):
    r = client.post("/l/wrong/jokes", data={"joke": "nope"}, follow_redirects=False)
    assert r.status_code == 404


def test_empty_joke_is_ignored(client, fake_db, league):
    client.post("/l/secret-admin-token/jokes", data={"joke": "   "})
    assert fake_db.get_jokes(league["id"]) == []


# ---------------------------------------------------------------------------
# Public reading — no token anywhere
# ---------------------------------------------------------------------------

def test_public_archive_needs_no_token(client, fake_db, league):
    fake_db.save_paper(league["id"], 3, 2025, "p", "u", {})
    r = client.get(f"/p/{league['public_slug']}")
    assert r.status_code == 200
    assert "Week 3" in r.text


def test_public_archive_never_exposes_the_admin_token(client, fake_db, league):
    fake_db.save_paper(league["id"], 3, 2025, "p", "u", {})
    r = client.get(f"/p/{league['public_slug']}")
    assert "secret-admin-token" not in r.text


def test_reading_a_published_paper(client, fake_db, league):
    fake_db.storage[fake_db.storage_path(league["public_slug"], 2025, 3)] = "<h1>PAPER</h1>"
    r = client.get(f"/p/{league['public_slug']}/2025/week-3")
    assert r.status_code == 200
    assert "PAPER" in r.text
    assert "max-age" in r.headers["cache-control"]


def test_unpublished_week_is_404(client, league):
    r = client.get(f"/p/{league['public_slug']}/2025/week-9")
    assert r.status_code == 404


def test_unknown_slug_is_404(client):
    assert client.get("/p/not-a-league").status_code == 404


# ---------------------------------------------------------------------------
# Slug + token generation
# ---------------------------------------------------------------------------

def test_public_slug_is_readable_and_unique():
    a = slugs.public_slug("The Kevlarville Times")
    b = slugs.public_slug("The Kevlarville Times")
    assert a.startswith("the-kevlarville-times-")
    assert a != b


def test_slugify_handles_junk():
    assert slugs.slugify("  Bob's !!! League  ") == "bob-s-league"
    assert slugs.slugify("") == "league"
    assert slugs.slugify("!!!") == "league"


def test_admin_token_is_long_enough_to_be_unguessable():
    token = slugs.admin_token()
    assert len(token) >= 30
    assert len({slugs.admin_token() for _ in range(100)}) == 100
