"""
Provider layer tests. Run with: python -m pytest tests/ -v

No network required -- SleeperProvider._get is swapped for fixtures that mirror
the real API's shapes, including the awkward ones.
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers import (  # noqa: E402
    SleeperProvider,
    TTLCache,
    apply_lineup_gaps,
    available_providers,
    get_provider,
    optimal_lineup,
    week_to_legacy_games,
)
from providers.models import (  # noqa: E402
    League,
    Manager,
    PlayerLine,
    Team,
    can_fill_slot_any,
)
from tests import fixtures  # noqa: E402


@pytest.fixture
def provider(tmp_path, monkeypatch):
    p = SleeperProvider(cache=TTLCache(cache_dir=tmp_path, namespace="test"))
    monkeypatch.setattr(p, "_get", lambda url, params=None: fixtures.fake_get(url, params))
    return p


@pytest.fixture
def week3(provider):
    return apply_lineup_gaps(provider.get_week("TESTLEAGUE", 2025, 3))


# --------------------------------------------------------------------------
# League parsing
# --------------------------------------------------------------------------

def test_league_basics(provider):
    league = provider.get_league("TESTLEAGUE", 2025)
    assert league.provider == "sleeper"
    assert league.name == "Kevlarville"
    assert league.season == 2025
    assert league.team_count == 4
    assert league.scoring_type == "ppr"
    assert league.previous_league_id == "OLDLEAGUE"


def test_commissioner_detected(provider):
    league = provider.get_league("TESTLEAGUE", 2025)
    assert league.commissioner_ids == ["u1"]


def test_starting_slots_exclude_bench(provider):
    league = provider.get_league("TESTLEAGUE", 2025)
    assert "BN" in league.roster_slots
    assert "BN" not in league.starting_slots
    assert len(league.starting_slots) == 9


# --------------------------------------------------------------------------
# Records -- the bug that made every upset comparison wrong
# --------------------------------------------------------------------------

def test_records_are_computed_from_history_not_roster_settings(week3):
    """Roster 1's stored settings claim 2-0. It actually went 1-1."""
    team = week3.team_by_id("1")
    assert team.record == "1-1", "should replay weeks 1-2, not trust settings.wins"


def test_records_exclude_the_week_being_reported(week3):
    """Every team has played exactly 2 games entering week 3."""
    for team in week3.teams:
        assert team.games_played == 2


def test_week_one_records_are_all_zero(provider):
    week1 = provider.get_week("TESTLEAGUE", 2025, 1)
    for team in week1.teams:
        assert team.record == "0-0"


# --------------------------------------------------------------------------
# Null / empty handling -- the TypeError waiting to happen
# --------------------------------------------------------------------------

def test_null_player_points_become_zero(week3):
    """players_points has an explicit null for Mark Andrews on roster 2."""
    team = week3.team_by_id("2")
    andrews = next(p for p in team.all_players if p.name == "Mark Andrews")
    assert andrews.points == 0.0
    assert isinstance(andrews.points, float)


def test_no_player_has_none_points(week3):
    for team in week3.teams:
        for player in team.all_players:
            assert player.points is not None
            assert isinstance(player.points, float)


def test_empty_starting_slot_is_counted(week3):
    """Roster 3 has a "0" in its starters array."""
    team = week3.team_by_id("3")
    assert team.empty_slots == 1


def test_teams_without_empty_slots_report_zero(week3):
    assert week3.team_by_id("1").empty_slots == 0


# --------------------------------------------------------------------------
# Lineup / slot mapping
# --------------------------------------------------------------------------

def test_starters_align_with_slots(week3):
    team = week3.team_by_id("1")
    slots = [p.slot for p in team.lineup]
    assert slots == ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]


def test_bench_excludes_starters(week3):
    team = week3.team_by_id("1")
    starter_ids = {p.player_id for p in team.lineup if p.player_id}
    bench_ids = {p.player_id for p in team.bench}
    assert not (starter_ids & bench_ids)
    assert len(team.bench) == 1  # Derrick Henry


def test_defense_gets_a_readable_name(week3):
    team = week3.team_by_id("1")
    defense = next(p for p in team.lineup if p.position == "DEF")
    assert defense.name == "SEA Defense"
    assert "team_logos" in defense.headshot_url


def test_player_ids_are_namespaced(week3):
    team = week3.team_by_id("1")
    assert all(p.player_id.startswith("sleeper:") for p in team.lineup if p.player_id)


# --------------------------------------------------------------------------
# Matchups
# --------------------------------------------------------------------------

def test_matchups_are_paired(week3):
    assert len(week3.matchups) == 2
    assert len(week3.teams) == 4


def test_winner_and_margin(week3):
    m = next(m for m in week3.matchups if m.matchup_id == "1")
    assert m.winner.team_id == "1"
    assert m.loser.team_id == "2"
    assert m.margin == pytest.approx(23.3)
    assert not m.is_tie


def test_tie_has_no_winner():
    mgr = Manager("u1", "someone")
    a = Team(team_id="1", team_name="A", manager=mgr, points=100.0)
    b = Team(team_id="2", team_name="B", manager=mgr, points=100.0)
    from providers.models import Matchup
    m = Matchup(matchup_id="1", teams=(a, b))
    assert m.is_tie
    assert m.winner is None
    assert m.margin == 0.0


def test_missing_week_raises(provider):
    from providers import WeekNotAvailable
    with pytest.raises(WeekNotAvailable):
        provider.get_week("TESTLEAGUE", 2025, 14)


# --------------------------------------------------------------------------
# Performers -- the "wrong guy got roasted" bug
# --------------------------------------------------------------------------

def test_top_scorer(week3):
    team = week3.team_by_id("1")
    assert team.top_scorer.name == "Josh Allen"


def test_biggest_bust_is_not_the_lowest_scorer(week3):
    """Roster 2's lowest scorer is its 0-point defense, but the real story is
    Xavier Worthy putting up 3.0 against a 14.4 projection."""
    team = week3.team_by_id("2")
    assert team.low_scorer.name == "DET Defense"
    assert team.biggest_bust.name == "Xavier Worthy"
    assert team.biggest_bust.vs_projection == pytest.approx(-11.4)


def test_goose_eggs_found(week3):
    team = week3.team_by_id("2")
    names = {p.name for p in team.goose_eggs}
    assert "DET Defense" in names


def test_vs_projection_is_none_without_a_projection():
    p = PlayerLine(player_id="x:1", name="Nobody", position="WR", points=5.0)
    assert p.vs_projection is None


# --------------------------------------------------------------------------
# Optimizer
# --------------------------------------------------------------------------

def test_lineup_gap_catches_the_benched_stud(week3):
    """Roster 1 started Chase Brown (6.5) with Derrick Henry (24.7) on the bench."""
    team = week3.team_by_id("1")
    assert team.lineup_gap == pytest.approx(18.2)


def test_optimal_never_below_actual(week3):
    for team in week3.teams:
        assert team.optimal_points >= team.points - 0.01


def test_lineup_gap_is_zero_for_a_perfect_lineup(week3):
    team = week3.team_by_id("4")
    assert team.lineup_gap == 0.0


def test_flex_first_ordering_does_not_break_the_optimizer():
    """The case that broke greedy-by-slot-order.

    Slots are FLEX, SUPER_FLEX, QB, RB, WR. A naive left-to-right greedy fills
    FLEX with the best RB (20), SUPER_FLEX with the best QB (30), then has no QB
    left for the QB slot. Correct answer uses every slot.
    """
    mgr = Manager("u1", "someone")
    roster = [
        PlayerLine("t:1", "QB One",  "QB", 30.0, positions=("QB",)),
        PlayerLine("t:2", "QB Two",  "QB", 25.0, positions=("QB",)),
        PlayerLine("t:3", "RB One",  "RB", 20.0, positions=("RB",)),
        PlayerLine("t:4", "RB Two",  "RB", 15.0, positions=("RB",)),
        PlayerLine("t:5", "WR One",  "WR", 18.0, positions=("WR",)),
    ]
    team = Team(team_id="1", team_name="T", manager=mgr, points=0.0, bench=roster)
    slots = ["FLEX", "SUPER_FLEX", "QB", "RB", "WR", "BN", "BN"]

    total, lineup = optimal_lineup(team, slots)

    # Best possible: QB(30) + SUPERFLEX(25) + RB(20) + FLEX(15) + WR(18) = 108
    assert total == pytest.approx(108.0)
    assert len({p.slot for p in lineup}) == 5


def test_negative_scorers_are_left_out():
    """A defense that scored -2 should not be forced into a slot."""
    mgr = Manager("u1", "someone")
    roster = [
        PlayerLine("t:1", "Good DEF", "DEF", 10.0, positions=("DEF",)),
        PlayerLine("t:2", "Bad DEF",  "DEF", -2.0, positions=("DEF",)),
    ]
    team = Team(team_id="1", team_name="T", manager=mgr, points=0.0, bench=roster)
    total, lineup = optimal_lineup(team, ["DEF", "DEF", "BN"])
    assert total == pytest.approx(10.0)
    assert len(lineup) == 1


def test_superflex_eligibility():
    assert can_fill_slot_any(("QB",), "SUPER_FLEX")
    assert can_fill_slot_any(("RB",), "SUPER_FLEX")
    assert not can_fill_slot_any(("QB",), "FLEX")
    assert not can_fill_slot_any(("K",), "FLEX")
    assert not can_fill_slot_any((), "FLEX")


# --------------------------------------------------------------------------
# Compat layer -- the existing pipeline must keep working
# --------------------------------------------------------------------------

LEGACY_TEAM_KEYS = {
    "team_name", "owner_name", "avatar_url", "points", "record", "wins",
    "losses", "empty_slots", "starters", "players", "players_points",
    "top_performer", "bottom_performer", "all_starters", "optimal_score",
    "lineup_gap",
}


def test_legacy_shape_has_every_key_the_old_pipeline_reads(week3):
    games = week_to_legacy_games(week3)
    for game in games:
        assert {"team_1", "team_2", "winner", "margin"} <= set(game)
        for key in ("team_1", "team_2"):
            assert LEGACY_TEAM_KEYS <= set(game[key]), (
                f"missing: {LEGACY_TEAM_KEYS - set(game[key])}"
            )


def test_legacy_player_ids_are_not_namespaced(week3):
    """newspaper.get_player_headshot_url builds a CDN URL straight off this."""
    games = week_to_legacy_games(week3)
    for p in games[0]["team_1"]["all_starters"]:
        assert ":" not in p["player_id"]


def test_legacy_record_parses_the_way_storylines_expects(week3):
    """storylines.py does int(record.split("-")[0]) with no guard."""
    games = week_to_legacy_games(week3)
    for game in games:
        for key in ("team_1", "team_2"):
            int(game[key]["record"].split("-")[0])


def test_storylines_runs_on_compat_output(week3):
    """The real storylines.py, unmodified, against provider output."""
    import storylines
    games = week_to_legacy_games(week3)
    summary = storylines.get_weekly_storylines(games)

    assert summary["closest_game"]["margin"] == pytest.approx(22.0)
    assert summary["biggest_blowout"]["margin"] == pytest.approx(23.3)
    assert summary["highest_score"]["team_name"] == "The Commissioner"
    assert summary["lowest_score"]["points"] == pytest.approx(88.0)
    assert summary["bench_blunder"]["lineup_gap"] == pytest.approx(18.2)
    assert len(summary["empty_teams"]) == 1


def test_upset_detection_now_actually_fires(week3):
    """With correct entering-week records, a 1-1 team beating a 1-1 team is no
    upset, but the old code could never detect one at all because records were
    either zeroed or already included this week's result."""
    import storylines
    games = week_to_legacy_games(week3)
    summary = storylines.get_weekly_storylines(games)
    # All teams are 1-1 entering week 3, so no upset -- but the code path ran
    # against real records rather than a table of zeros.
    assert summary["upset"] is None
    assert all(t["record"] == "1-1" for g in games for t in (g["team_1"], g["team_2"]))


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def test_cache_roundtrip(tmp_path):
    cache = TTLCache(cache_dir=tmp_path, namespace="t")
    cache.set("k", {"a": 1}, ttl_seconds=60)
    assert cache.get("k") == {"a": 1}


def test_cache_expires(tmp_path):
    cache = TTLCache(cache_dir=tmp_path, namespace="t")
    cache.set("k", "v", ttl_seconds=-1)
    assert cache.get("k") is None


def test_cache_miss_returns_none(tmp_path):
    cache = TTLCache(cache_dir=tmp_path, namespace="t")
    assert cache.get("nope") is None


def test_get_or_fetch_only_calls_once(tmp_path):
    cache = TTLCache(cache_dir=tmp_path, namespace="t")
    calls = []

    def fetch():
        calls.append(1)
        return {"data": "x"}

    cache.get_or_fetch("k", 60, fetch)
    cache.get_or_fetch("k", 60, fetch)
    assert len(calls) == 1


def test_player_index_is_fetched_once_per_provider(tmp_path, monkeypatch):
    """The whole point: no re-downloading 5MB on every generation."""
    p = SleeperProvider(cache=TTLCache(cache_dir=tmp_path, namespace="t"))
    hits = []

    def counting_get(url, params=None):
        if "/players/nfl" in url:
            hits.append(url)
        return fixtures.fake_get(url, params)

    monkeypatch.setattr(p, "_get", counting_get)
    p.get_week("TESTLEAGUE", 2025, 3)
    p.get_week("TESTLEAGUE", 2025, 3)
    assert len(hits) == 1


def test_corrupt_cache_entry_is_a_miss(tmp_path):
    cache = TTLCache(cache_dir=tmp_path, namespace="t")
    cache.set("k", "v", 60)
    cache._path("k").write_text("{ not json")
    assert cache.get("k") is None


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

def test_get_provider_by_name():
    assert isinstance(get_provider("sleeper"), SleeperProvider)
    assert isinstance(get_provider("SLEEPER"), SleeperProvider)


def test_unknown_provider_raises_a_useful_error():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("draftkings")


def test_espn_is_registered_but_not_implemented():
    listed = {p["name"]: p for p in available_providers()}
    assert listed["espn"]["implemented"] is False
    assert listed["sleeper"]["implemented"] is True

    espn = get_provider("espn")
    with pytest.raises(NotImplementedError):
        espn.get_league("x", 2025)


def test_namespaced_id():
    assert SleeperProvider().namespaced_id(4046) == "sleeper:4046"


# ---------------------------------------------------------------------------
# Records as the paper prints them
# ---------------------------------------------------------------------------

def test_the_record_the_paper_prints_includes_the_week_it_covers(tmp_path,
                                                                 monkeypatch):
    """The Week 1 paper printed a standings table reading 0-0 for all ten
    teams, directly underneath the headlines about the games that had just
    decided those records.

    Team.record is the record *entering* the week, on purpose, so upset and
    fraud logic can compare standings as they stood at kickoff. Correct for
    that, wrong for the reader. `record_after` is the one that gets printed.
    """
    from providers.compat import week_to_legacy_games

    p = SleeperProvider(cache=TTLCache(cache_dir=tmp_path, namespace="rec"))
    monkeypatch.setattr(p, "_get",
                        lambda url, params=None: fixtures.fake_get(url, params))

    games = week_to_legacy_games(p.get_week("1252396303246176256", 2025, 1))

    for game in games:
        for side in ("team_1", "team_2"):
            team = game[side]
            assert team["record"] == "0-0", "fixture assumption changed"
            won = game["winner"] == team["team_name"]
            assert team["record_after"] == ("1-0" if won else "0-1"), team


def test_records_stay_consistent_across_consecutive_weeks(tmp_path, monkeypatch):
    """Week N's `record_after` has to equal week N+1's `record`, or the
    standings visibly jump."""
    from providers.compat import week_to_legacy_games

    p = SleeperProvider(cache=TTLCache(cache_dir=tmp_path, namespace="rec2"))
    monkeypatch.setattr(p, "_get",
                        lambda url, params=None: fixtures.fake_get(url, params))

    def by_team(week):
        out = {}
        for game in week_to_legacy_games(p.get_week("1252396303246176256", 2025, week)):
            for side in ("team_1", "team_2"):
                out[game[side]["team_name"]] = game[side]
        return out

    for week in (1, 2):
        now, nxt = by_team(week), by_team(week + 1)
        for name, team in now.items():
            assert team["record_after"] == nxt[name]["record"], (
                f"{name}: week {week} ended {team['record_after']} but week "
                f"{week + 1} opened {nxt[name]['record']}")


def test_the_renderer_prefers_the_post_week_record(tmp_path):
    """The whole fix is worthless if newspaper.py keeps reading `record`."""
    import newspaper

    assert newspaper.get_team_record(
        {"record": "0-0", "record_after": "1-0"}) == "1-0"
    # Older rows, and any provider that doesn't supply it yet.
    assert newspaper.get_team_record({"record": "3-1"}) == "3-1"
    assert newspaper.get_team_record({}) == ""


# ---------------------------------------------------------------------------
# Transactions — an OPTIONAL provider capability
# ---------------------------------------------------------------------------

_TX_USERS = [
    {"user_id": "u1", "display_name": "johnhenryhammond"},
    {"user_id": "u2", "display_name": "Jagan34"},
    {"user_id": "u3", "display_name": "champayyy"},
    {"user_id": "u7", "display_name": "HankStocke"},
    {"user_id": "u5", "display_name": "superchaser"},
]
_TX_ROSTERS = [
    {"roster_id": 1, "owner_id": "u1"},
    {"roster_id": 2, "owner_id": "u2"},
    {"roster_id": 3, "owner_id": "u3", "metadata": {"team_name": "Satan"}},
    {"roster_id": 7, "owner_id": "u7"},
    {"roster_id": 5, "owner_id": "u5"},
]
_TX_PLAYERS = {
    "4034": {"full_name": "Rico Dowdle", "position": "RB", "team": "CAR"},
    "1234": {"first_name": "Tyjae", "last_name": "Spears",
             "position": "RB", "team": "TEN"},
    "4046": {"full_name": "Bijan Robinson", "position": "RB", "team": "ATL"},
    "6794": {"full_name": "Josh Allen", "position": "QB", "team": "BUF"},
    "PIT": {},
}
_TX_ROWS = [
    {"type": "waiver", "status": "complete", "roster_ids": [3],
     "adds": {"4034": 3}, "drops": {"1234": 3},
     "settings": {"waiver_bid": 14}, "created": 1000},
    {"type": "waiver", "status": "failed", "roster_ids": [7],
     "adds": {"4034": 7}, "drops": None,
     "settings": {"waiver_bid": 3}, "created": 2000},
    {"type": "trade", "status": "complete", "roster_ids": [1, 2],
     "adds": {"4046": 1, "6794": 2}, "drops": {"4046": 2, "6794": 1},
     "settings": None, "created": 3000},
    {"type": "free_agent", "status": "complete", "roster_ids": [5],
     "adds": {"PIT": 5}, "drops": None, "settings": None, "created": 4000},
    # Must not crash the section:
    {"type": "waiver", "status": "complete", "roster_ids": [9],
     "adds": None, "drops": None},
    "not a dict",
]


def _tx_provider(tmp_path, rows=None, boom=False):
    p = SleeperProvider(cache=TTLCache(cache_dir=tmp_path, namespace="tx"))

    def fake(url, params=None):
        if "/transactions/" in url:
            if boom:
                from providers.base import ProviderError
                raise ProviderError("Sleeper is down")
            return _TX_ROWS if rows is None else rows
        if url.endswith("/users"):
            return _TX_USERS
        if url.endswith("/rosters"):
            return _TX_ROSTERS
        if "players/nfl" in url:
            return _TX_PLAYERS
        return None

    p._get = fake
    return p


def test_transactions_are_named_and_attributed(tmp_path):
    """The platform speaks in roster ids and player ids. A newspaper cannot
    print either."""
    tx = _tx_provider(tmp_path).get_transactions("123", 2026, 2)

    waiver = tx[0]
    assert waiver.teams == ["Satan"], "roster metadata name beats display name"
    assert waiver.adds[0][1].name == "Rico Dowdle"
    assert waiver.drops[0][1].name == "Tyjae Spears", "first+last was not joined"
    assert waiver.bid == 14


def test_a_failed_claim_is_kept(tmp_path):
    """Often the better story: somebody bid, publicly, and did not get him."""
    tx = _tx_provider(tmp_path).get_transactions("123", 2026, 2)
    failed = [t for t in tx if not t.succeeded]

    assert len(failed) == 1
    assert failed[0].teams == ["HankStocke"]
    assert failed[0].bid == 3


def test_a_trade_arrives_as_one_record_covering_both_sides(tmp_path):
    """Sleeper models a two-way trade as a single row with a merged adds map,
    not as two transactions."""
    tx = _tx_provider(tmp_path).get_transactions("123", 2026, 2)
    trade = next(t for t in tx if t.is_trade)

    got = {team: player.name for team, player in trade.adds}
    assert got == {"johnhenryhammond": "Bijan Robinson",
                   "Jagan34": "Josh Allen"}


def test_a_team_defense_is_not_printed_as_a_raw_id(tmp_path):
    """Sleeper identifies a defense by the team abbreviation itself, with no
    name fields at all, so it falls through every name lookup."""
    tx = _tx_provider(tmp_path).get_transactions("123", 2026, 2)
    fa = next(t for t in tx if t.kind == "free_agent")

    assert fa.adds[0][1].name == "PIT D/ST"


def test_empty_and_malformed_rows_are_dropped(tmp_path):
    tx = _tx_provider(tmp_path).get_transactions("123", 2026, 2)
    assert len(tx) == 4, [t.kind for t in tx]


def test_transactions_are_ordered_as_they_happened(tmp_path):
    tx = _tx_provider(tmp_path).get_transactions("123", 2026, 2)
    assert [t.created for t in tx] == sorted(t.created for t in tx)


def test_a_broken_feed_costs_the_section_not_the_paper(tmp_path):
    """The scores are the product. A secondary feed being down must never
    take a league's matchups with it."""
    assert _tx_provider(tmp_path, boom=True).get_transactions("123", 2026, 2) == []


def test_a_platform_without_transactions_returns_nothing_quietly():
    """ESPN and Yahoo inherit the base implementation. This must be a no-op,
    not a NotImplementedError — it is an optional capability, and adding a
    third abstract method would make every future platform implement it."""
    from providers.espn import ESPNProvider

    assert ESPNProvider().get_transactions("123", 2026, 1) == []


def test_load_transactions_never_raises(tmp_path, monkeypatch):
    """The helper the web path calls. Everything degrades to "no section"."""
    import providers

    monkeypatch.setattr(providers, "get_provider",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert providers.load_transactions("sleeper", "123", 2026, 1) == []


# ---------------------------------------------------------------------------
# Waiver PRIORITY leagues — no money involved at all
# ---------------------------------------------------------------------------

def _one_waiver(tmp_path, settings):
    """A single completed waiver claim, with whatever settings Sleeper sent."""
    row = {"type": "waiver", "status": "complete", "roster_ids": [3],
           "adds": {"4034": 3}, "drops": None, "created": 1}
    if settings is not ...:
        row["settings"] = settings

    p = SleeperProvider(cache=TTLCache(cache_dir=tmp_path, namespace="pri"))

    def fake(url, params=None):
        if "/transactions/" in url:
            return [row]
        if url.endswith("/users"):
            return _TX_USERS
        if url.endswith("/rosters"):
            return _TX_ROSTERS
        if "players/nfl" in url:
            return _TX_PLAYERS
        return None

    p._get = fake
    return p.get_transactions("123", 2026, 2)[0]


@pytest.mark.parametrize("settings,expected", [
    (..., None),                    # key absent entirely
    (None, None),                   # settings: null
    ({}, None),                     # settings present, empty
    ({"seq": 1}, None),             # priority leagues carry other keys
    ({"waiver_bid": 0}, 0),         # some priority leagues send a zero
    ({"waiver_bid": 14}, 14),       # actual FAAB
    ({"waiver_bid": "14"}, None),   # a string is not a bid
])
def test_faab_is_only_read_when_the_league_actually_uses_it(tmp_path, settings,
                                                            expected):
    assert _one_waiver(tmp_path, settings).bid == expected


@pytest.mark.parametrize("settings", [..., None, {}, {"seq": 1},
                                      {"waiver_bid": 0}, {"waiver_bid": "14"}])
def test_a_priority_league_never_sees_a_dollar_sign(tmp_path, settings):
    """Plenty of leagues run waiver priority, not FAAB. Some of them send
    waiver_bid: 0, and printing "$0" against every claim is not a rounding
    error — it is a false statement about how the league works.

    This is the guard on `bid > 0`, which looks like an arbitrary check and
    is not.
    """
    import newspaper

    tx = _one_waiver(tmp_path, settings)
    html = newspaper._transactions_block([tx.to_dict()])

    assert "$" not in html, html
    assert "wire-bid" not in html
    # The move itself still has to print.
    assert "Rico Dowdle" in html


def test_a_faab_league_still_shows_the_bid(tmp_path):
    """The fix above must not cost real FAAB leagues the most interesting
    number in the section."""
    import newspaper

    tx = _one_waiver(tmp_path, {"waiver_bid": 14})
    html = newspaper._transactions_block([tx.to_dict()])

    assert "$14" in html
