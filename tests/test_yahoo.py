"""
Yahoo: the adapter against Yahoo-shaped fixtures, the token store, and the
connect flow. Run with: python -m pytest tests/test_yahoo.py -q

The fixtures are written from Yahoo's documentation (see fixtures_yahoo.py),
so these prove the adapter reads THAT shape correctly — not that Yahoo sends
it. The live check at /connect/yahoo/check is what proves the second thing.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.pop("RESEND_API_KEY", None)

from providers import AuthRequired, get_provider  # noqa: E402
from providers import yahoo as yahoo_mod  # noqa: E402
from providers.cache import TTLCache  # noqa: E402
from providers.yahoo import YahooProvider, reconcile  # noqa: E402
from tests import fixtures_yahoo as fx  # noqa: E402

KEY = fx.LEAGUE_KEY


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    calls = []

    def fake_get(self, path, league_key=None):
        self._token_for(league_key)       # the auth path still runs
        calls.append(path)
        return fx.route(path)

    monkeypatch.setattr(YahooProvider, "_get", fake_get)
    a = YahooProvider(cache=TTLCache(tmp_path, "yahoo"), access_token="tok")
    a.calls = calls
    return a


# --- the league -------------------------------------------------------------

def test_registered_but_not_yet_live():
    from providers import available_providers
    y = next(p for p in available_providers() if p["name"] == "yahoo")
    assert y["implemented"] is False, \
        "flip this only after /connect/yahoo/check passes on a real league"
    assert isinstance(get_provider("yahoo"), YahooProvider)


def test_league_settings(adapter):
    league = adapter.get_league(KEY)
    assert league.name == "Dirty South Dynasty"
    assert league.season == 2026
    assert league.team_count == 4
    assert league.scoring_type == "half_ppr"
    assert league.roster_slots.count("WR") == 2
    assert "FLEX" in league.roster_slots and "DEF" in league.roster_slots
    assert league.roster_slots.count("BN") == 5
    assert league.previous_league_id == "449.l.13579"
    assert league.status == "in_season"


def test_a_typed_id_is_refused_with_a_sentence(adapter):
    from providers import LeagueNotFound
    with pytest.raises(LeagueNotFound, match="pick your league"):
        adapter.get_league("24680")


def test_finished_weeks_stop_before_the_current_one(adapter):
    assert adapter.available_weeks(KEY, 2026) == [1, 2, 3]


def test_user_leagues_lists_the_signed_in_accounts_leagues(adapter):
    leagues = adapter.user_leagues()
    assert [l.league_id for l in leagues] == [KEY]
    assert leagues[0].name == "Dirty South Dynasty"
    assert leagues[0].season == 2026


# --- a week -----------------------------------------------------------------

def test_starters_add_up_to_yahoos_own_score(adapter):
    """THE check. Bench and IR points are in the fixture on purpose."""
    week = adapter.get_week(KEY, 2026, 3)
    rows = {r["team"]: r for r in reconcile(week)}
    hank = rows["Hank's Heroes"]
    assert hank["yahoo"] == 61.60
    assert hank["starters"] == 61.60 and hank["ok"]
    assert hank["n_starters"] == 9 and hank["n_bench"] == 2


def test_bench_and_ir_are_bench(adapter):
    week = adapter.get_week(KEY, 2026, 3)
    hank = week.team_by_id("461.l.24680.t.1")
    bench = {p.name: p.slot for p in hank.bench}
    assert bench == {"Jaylen Warren": "BN", "Christian McCaffrey": "IR"}
    flex = next(p for p in hank.lineup if p.name == "Rashee Rice")
    assert flex.slot == "FLEX"


def test_player_details(adapter):
    week = adapter.get_week(KEY, 2026, 3)
    hank = week.team_by_id("461.l.24680.t.1")
    burrow = next(p for p in hank.lineup if p.name == "Joe Burrow")
    assert burrow.player_id == "yahoo:30123"
    assert burrow.nfl_team == "CIN"
    assert burrow.stats.describe() == "2 pass TD, 1 INT"
    assert burrow.projected is None      # Yahoo has no per-player projection
    bijan = next(p for p in hank.lineup if p.name == "Bijan Robinson")
    assert bijan.stats.describe() == "1 rush TD, 1 FUM"
    hall = next(p for p in hank.lineup if p.name == "Breece Hall")
    assert hall.injury_status == "Questionable"
    assert burrow.injury_status is None
    jax = next(p for p in hank.lineup if p.position == "DEF")
    assert jax.nfl_team == "JAX"


def test_names_and_managers(adapter):
    week = adapter.get_week(KEY, 2026, 3)
    hank = week.team_by_id("461.l.24680.t.1")
    assert hank.team_name == "Hank's Heroes"
    assert hank.manager.display_name == "Hank"
    assert hank.manager.is_commissioner


def test_records_are_entering_the_week(adapter):
    """Week 3's own result must not be in the record printed beside it."""
    week = adapter.get_week(KEY, 2026, 3)
    rec = {t.team_name: t.record for t in week.teams}
    assert rec["Hank's Heroes"] == "2-0"
    assert rec["Waiver Wire Warriors"] == "1-1"
    assert rec["Kevlarville"] == "0-2"
    assert rec["The Mid Tier"] == "1-1"


def test_a_tie_is_a_tie(adapter):
    week = adapter.get_week(KEY, 2026, 3)
    tied = next(m for m in week.matchups
                if {t.team_name for t in m.teams} == {"Waiver Wire Warriors", "Kevlarville"})
    assert tied.is_tie


def test_week_1_records_are_zero(adapter):
    week = adapter.get_week(KEY, 2026, 1)
    assert all(t.record == "0-0" for t in week.teams)


def test_an_unplayed_week_says_so(adapter):
    from providers import WeekNotAvailable
    with pytest.raises(WeekNotAvailable):
        adapter.get_week(KEY, 2026, 9)


def test_the_optimizer_accepts_it(adapter):
    from providers import apply_lineup_gaps
    week = apply_lineup_gaps(adapter.get_week(KEY, 2026, 3))
    hank = week.team_by_id("461.l.24680.t.1")
    # Warren's 22.40 on the bench beats the 0.00 tight end? No — RB can't
    # play TE — but it beats Rice's 2.50 in the flex.
    assert hank.optimal_points == pytest.approx(61.60 - 2.50 + 22.40)


# --- draft and transactions -------------------------------------------------

def test_draft_picks(adapter):
    picks = adapter.draft_picks(KEY, 2026)
    assert picks["31001"] == {"round": 1, "overall": 1}


def test_transactions(adapter):
    moves = adapter.get_transactions(KEY, 2026, 3)
    kinds = [m.kind for m in moves]
    assert kinds == ["waiver", "free_agent", "trade"], "the vetoed trade is dropped"

    waiver = moves[0]
    assert waiver.bid == 17
    assert [(t, p.name) for t, p in waiver.adds] == [("Hank's Heroes", "Jaylen Warren")]
    assert [(t, p.name) for t, p in waiver.drops] == [("Hank's Heroes", "Zach Ertz")]
    assert waiver.created == 1789900000 * 1000

    fa = moves[1]
    assert fa.bid is None
    assert fa.adds[0][0] == "Waiver Wire Warriors"

    trade = moves[2]
    assert set(trade.teams) == {"Hank's Heroes", "Waiver Wire Warriors"}
    assert [(t, p.name) for t, p in trade.adds] == [("Waiver Wire Warriors", "Tee Higgins")]
    assert [(t, p.name) for t, p in trade.drops] == [("Hank's Heroes", "Tee Higgins")]
    assert trade.picks == [("Hank's Heroes", "2nd-round pick (The Mid Tier's)")]


def test_season_chain_follows_renew(adapter, monkeypatch):
    seen = []
    real = YahooProvider.describe_league

    def fake(self, key, season=None):
        seen.append(key)
        return real(self, key) if key == KEY else None

    monkeypatch.setattr(YahooProvider, "describe_league", fake)
    chain = adapter.season_chain(KEY)
    assert [l.league_id for l in chain] == [KEY]
    assert seen == [KEY, "449.l.13579"]


# --- auth -------------------------------------------------------------------

def test_no_token_is_auth_required_with_a_sentence(monkeypatch, tmp_path):
    monkeypatch.setattr(yahoo_mod, "_token_source", None)
    a = YahooProvider(cache=TTLCache(tmp_path, "yahoo"))
    with pytest.raises(AuthRequired, match="Sign in with Yahoo again"):
        a.get_league(KEY)


def test_token_source_is_asked_per_league(monkeypatch, tmp_path):
    asked = []

    def source(key):
        asked.append(key)
        return "from-source"

    monkeypatch.setattr(yahoo_mod, "_token_source", source)
    a = YahooProvider(cache=TTLCache(tmp_path, "yahoo"))
    assert a._token_for(KEY) == "from-source"
    assert asked == [KEY]


# --- the token store --------------------------------------------------------

def test_tokens_are_encrypted_at_rest():
    from web import yahoo_auth
    sealed = yahoo_auth.encrypt("refresh-me")
    assert "refresh-me" not in sealed
    assert yahoo_auth.decrypt(sealed) == "refresh-me"
    assert yahoo_auth.decrypt("garbage") == ""


def _store():
    from web import demo_db
    demo_db._YAHOO_TOKENS.clear()
    return demo_db


def test_a_fresh_token_is_used_as_is(monkeypatch):
    from web import yahoo_auth
    db = _store()
    yahoo_auth.save_tokens("u1", {"access_token": "A1", "refresh_token": "R1",
                                  "expires_in": 3600}, db=db)
    monkeypatch.setattr(yahoo_auth, "refresh",
                        lambda *a, **k: pytest.fail("should not refresh"))
    assert yahoo_auth.access_token_for_user("u1", db=db) == "A1"
    raw = db.get_yahoo_token("u1")
    assert raw["access_token"] != "A1" and raw["refresh_token"] != "R1"


def test_an_expired_token_is_refreshed_and_the_refresh_token_kept(monkeypatch):
    from web import yahoo_auth
    db = _store()
    yahoo_auth.save_tokens("u1", {"access_token": "A1", "refresh_token": "R1",
                                  "expires_in": 60}, db=db)   # inside the margin
    got = []

    def fake_refresh(token, base_url=""):
        got.append(token)
        return {"access_token": "A2", "expires_in": 3600}

    monkeypatch.setattr(yahoo_auth, "refresh", fake_refresh)
    assert yahoo_auth.access_token_for_user("u1", db=db) == "A2"
    assert got == ["R1"]
    assert yahoo_auth.decrypt(db.get_yahoo_token("u1")["refresh_token"]) == "R1"


def test_a_revoked_refresh_token_reads_as_disconnected(monkeypatch):
    from web import yahoo_auth
    db = _store()
    yahoo_auth.save_tokens("u1", {"access_token": "A1", "refresh_token": "R1",
                                  "expires_in": 0}, db=db)

    def refused(*a, **k):
        raise yahoo_auth.YahooAuthError("no")

    monkeypatch.setattr(yahoo_auth, "refresh", refused)
    assert yahoo_auth.access_token_for_user("u1", db=db) is None


def test_a_league_reads_with_its_owners_token(monkeypatch):
    from web import app as webapp, demo_db, yahoo_auth
    monkeypatch.setattr(webapp, "db", demo_db)
    _store()
    demo_db._LEAGUES.clear()
    row = demo_db.create_league(provider="yahoo", platform_league_id=KEY,
                                league_name="x", paper_name="x",
                                commissioner_name="", season=2026,
                                public_slug="x-1", admin_token="t" * 20)
    assert yahoo_auth.access_token_for_league(KEY) is None     # no owner
    demo_db.claim_league(row["id"], "owner")
    yahoo_auth.save_tokens("owner", {"access_token": "OWN", "refresh_token": "R",
                                     "expires_in": 3600}, db=demo_db)
    assert yahoo_auth.access_token_for_league(KEY) == "OWN"


def test_the_generator_registers_the_token_source():
    import web.generate  # noqa: F401
    from web import yahoo_auth
    assert yahoo_mod._token_source is yahoo_auth.access_token_for_league


# --- the connect flow -------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    from fastapi.testclient import TestClient
    from web import app as webapp, demo_db
    for store in (demo_db._LEAGUES, demo_db._USERS, demo_db._RATE_EVENTS,
                  demo_db._YAHOO_TOKENS):
        store.clear()
    monkeypatch.setattr(webapp, "db", demo_db)
    monkeypatch.setenv("YAHOO_CLIENT_ID", "cid")
    monkeypatch.setenv("YAHOO_CLIENT_SECRET", "csecret")
    return TestClient(webapp.app), demo_db


def _sign_up(client, db, plan="free", email="y@example.com"):
    r = client.post("/signup", data={"email": email, "password": "correct horse battery 9",
                                     "confirm": "correct horse battery 9"},
                    follow_redirects=False)
    assert r.status_code in (302, 303), r.text[:300]
    user = db.user_by_email(email)
    db.update_user(user["id"], {"plan": plan})
    return db.user_by_email(email)


def test_yahoo_is_still_a_waiting_list_for_ordinary_accounts(web):
    client, db = web
    _sign_up(client, db)
    r = client.get("/connect/yahoo")
    assert "isn&rsquo;t ready yet" in r.text
    r = client.get("/connect")
    assert "Coming soon" in r.text


def test_staff_get_the_sign_in_button(web):
    client, db = web
    _sign_up(client, db, plan="staff")
    r = client.get("/connect/yahoo")
    assert "/connect/yahoo/start" in r.text


def test_unconfigured_means_waiting_list_even_for_staff(web, monkeypatch):
    client, db = web
    monkeypatch.delenv("YAHOO_CLIENT_ID")
    _sign_up(client, db, plan="staff")
    assert "isn&rsquo;t ready yet" in client.get("/connect/yahoo").text


def test_start_redirects_to_yahoo_with_a_signed_state(web):
    client, db = web
    _sign_up(client, db, plan="staff")
    r = client.get("/connect/yahoo/start", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(
        "https://api.login.yahoo.com/oauth2/request_auth?")
    assert "connect%2Fyahoo%2Fcallback" in r.headers["location"]
    assert "cd_yahoo_state" in r.headers["set-cookie"]


def test_callback_refuses_a_state_we_did_not_issue(web, monkeypatch):
    client, db = web
    from web import yahoo_auth
    user = _sign_up(client, db, plan="staff")
    monkeypatch.setattr(yahoo_auth, "exchange_code",
                        lambda *a: pytest.fail("must not exchange"))
    r = client.get("/connect/yahoo/callback?code=abc&state=forged.1",
                   follow_redirects=False)
    assert "didn" in r.headers["location"]
    assert db.get_yahoo_token(user["id"]) is None


def test_callback_stores_the_token(web, monkeypatch):
    client, db = web
    from web import yahoo_auth
    user = _sign_up(client, db, plan="staff")
    start = client.get("/connect/yahoo/start", follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    from urllib.parse import unquote
    monkeypatch.setattr(yahoo_auth, "exchange_code", lambda code, base: {
        "access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
        "xoauth_yahoo_guid": "G1"})
    r = client.get(f"/connect/yahoo/callback?code=abc&state={unquote(state)}",
                   follow_redirects=False)
    assert r.headers["location"] == "/connect/yahoo"
    assert yahoo_auth.access_token_for_user(user["id"], db=db) == "AT"


def _connected(client, db, monkeypatch, tmp_path):
    from web import yahoo_auth
    user = _sign_up(client, db, plan="staff")
    yahoo_auth.save_tokens(user["id"], {"access_token": "AT", "refresh_token": "RT",
                                        "expires_in": 3600}, db=db)

    def fake_get(self, path, league_key=None):
        self._token_for(league_key)
        return fx.route(path)

    monkeypatch.setattr(YahooProvider, "_get", fake_get)
    # Routes build their own provider on the app's disk cache; keep tests off it.
    monkeypatch.setattr(YahooProvider, "_cached",
                        lambda self, key, ttl, fetch: fetch())
    return user


def test_connected_staff_see_their_leagues(web, monkeypatch, tmp_path):
    client, db = web
    _connected(client, db, monkeypatch, tmp_path)
    r = client.get("/connect/yahoo")
    assert "Dirty South Dynasty" in r.text
    assert f'value="{KEY}"' in r.text


def test_adding_a_league_makes_a_paper(web, monkeypatch, tmp_path):
    client, db = web
    user = _connected(client, db, monkeypatch, tmp_path)
    r = client.post("/connect/yahoo/add", data={"league_id": KEY},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/setup")
    league = db.find_existing_league("yahoo", KEY, 2026)
    assert league["user_id"] == user["id"]
    assert league["league_name"] == "Dirty South Dynasty"


def test_a_league_not_on_your_account_is_refused(web, monkeypatch, tmp_path):
    """Otherwise anyone signed in with Yahoo could post any league key."""
    client, db = web
    _connected(client, db, monkeypatch, tmp_path)
    r = client.post("/connect/yahoo/add", data={"league_id": "461.l.99999"},
                    follow_redirects=False)
    assert "isn" in r.headers["location"]
    assert db.find_existing_league("yahoo", "461.l.99999", 2026) is None


def test_disconnect_forgets_the_token(web, monkeypatch, tmp_path):
    client, db = web
    user = _connected(client, db, monkeypatch, tmp_path)
    client.post("/connect/yahoo/disconnect")
    assert db.get_yahoo_token(user["id"]) is None


def test_the_check_page_is_staff_only(web):
    client, db = web
    _sign_up(client, db)
    assert client.get("/connect/yahoo/check").status_code == 404


def test_the_check_page_reconciles(web, monkeypatch, tmp_path):
    client, db = web
    _connected(client, db, monkeypatch, tmp_path)
    r = client.get(f"/connect/yahoo/check?league={KEY}&week=3")
    assert r.status_code == 200
    assert "add up to the cent" in r.text
    # Every Hank player's stats reproduce Yahoo's points through the
    # league's own scoring — the stat-id check.
    assert "11 of 11 players reproduce exactly" in r.text
    assert "don&rsquo;t add up" not in r.text
