"""
Provider layer tests. Run with: python -m pytest tests/ -v

No network required -- SleeperProvider._get is swapped for fixtures that mirror
the real API's shapes, including the awkward ones.
"""

import sys
import tempfile
from pathlib import Path

import copy

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers import (  # noqa: E402
    ESPNProvider,
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


def test_espn_is_offered_now_that_it_has_seen_a_real_league():
    listed = {p["name"]: p for p in available_providers()}
    assert listed["espn"]["implemented"] is True
    assert listed["sleeper"]["implemented"] is True


def test_the_implemented_flag_is_read_from_the_provider_not_hardcoded():
    """It used to be `cls is not ESPNProvider`, which meant the day ESPN
    started working somebody had to remember to edit a different file."""
    from providers.base import FantasyProvider

    assert FantasyProvider.implemented is True
    assert ESPNProvider.implemented is True


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
    """The base implementation must be a no-op, not a NotImplementedError.

    Transactions are an optional capability. Making it abstract would force
    every future platform to implement a feed some of them do not have.

    ESPN used to be the example here and now implements it, so this asks the
    base class directly rather than naming a provider that might grow the
    feature next.
    """
    from providers.base import FantasyProvider

    class Bare(FantasyProvider):
        name = "bare"

        def get_league(self, league_id, season=None):
            raise NotImplementedError

        def get_week(self, league_id, season, week):
            raise NotImplementedError

    assert Bare().get_transactions("123", 2026, 1) == []


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


# ---------------------------------------------------------------------------
# ESPN
#
# The fixture these run against was corrected on 16 Sep 2026 against a real
# public league. Where a test names a specific ESPN quirk, that quirk was
# observed in that response — it is not a guess about how ESPN might behave.
# ---------------------------------------------------------------------------

from tests import fixtures_espn  # noqa: E402


@pytest.fixture
def espn(tmp_path, monkeypatch):
    p = ESPNProvider(cache=TTLCache(cache_dir=tmp_path, namespace="espn"))
    monkeypatch.setattr(
        p, "_get",
        lambda lid, season, views, params=None, fantasy_filter=None:
            fixtures_espn.fake_get(lid, season, views, params, fantasy_filter))
    return p


@pytest.fixture
def espn_week(espn):
    return apply_lineup_gaps(espn.get_week(fixtures_espn.LEAGUE_ID,
                                           fixtures_espn.SEASON, 2))


def test_espn_league_basics(espn):
    league = espn.get_league(fixtures_espn.LEAGUE_ID, fixtures_espn.SEASON)
    assert league.provider == "espn"
    assert league.name == "Kevlarville"
    assert league.scoring_type == "ppr"     # statId 53 worth 1.0
    assert league.status == "in_season"


def test_espn_roster_slots_skip_the_zero_counts(espn):
    """lineupSlotCounts lists every slot ESPN knows about, and sets most of
    them to zero. A naive read gives a league with a defensive line."""
    league = espn.get_league(fixtures_espn.LEAGUE_ID, fixtures_espn.SEASON)
    assert league.roster_slots.count("QB") == 1
    assert league.roster_slots.count("RB") == 2
    assert league.roster_slots.count("BN") == 6
    assert "DL" not in league.roster_slots
    assert "LB" not in league.roster_slots


def test_espn_starters_and_bench_are_split_on_slot_id(espn_week):
    """20 is bench and 21 is IR; everything else is on the field. Getting this
    wrong is invisible until the scores do not add up."""
    team = espn_week.team_by_id("1")
    assert len(team.lineup) == 9
    assert {p.slot for p in team.bench} == {"BN", "IR"}
    assert "Jahmyr Gibbs" in {p.name for p in team.bench}


def test_espn_starters_sum_to_the_teams_reported_total(espn_week):
    """THE check. Nothing else validates the slot map, the bench rule and the
    points field simultaneously — and against the real league, all ten teams
    reconciled to the cent."""
    for team in espn_week.teams:
        total = round(sum(p.points for p in team.lineup), 2)
        assert total == pytest.approx(team.points, abs=0.05), (
            f"{team.team_name}: starters sum {total}, ESPN says {team.points}")


def test_espn_healthy_players_carry_no_injury_note(espn_week):
    """OBSERVED: injuryStatus is "ACTIVE" for a healthy player, not absent.
    Fourteen of sixteen players in the real league carried it. Passed through
    naively, the writer prints "[Active]" beside almost every name."""
    team = espn_week.team_by_id("1")
    healthy = [p for p in team.lineup if p.name != "Garrett Wilson"]
    assert all(p.injury_status is None for p in healthy), (
        [(p.name, p.injury_status) for p in healthy if p.injury_status])


def test_espn_real_injuries_survive_and_are_readable(espn_week):
    team = espn_week.team_by_id("1")
    wilson = next(p for p in team.lineup if p.name == "Garrett Wilson")
    assert wilson.injury_status == "Questionable"
    ir = next(p for p in team.bench if p.slot == "IR")
    assert ir.injury_status == "Out"


def test_espn_projections_are_the_week_not_the_season(espn_week):
    """The stats array holds weekly and season-to-date rows, actual and
    projected, in one list distinguished only by three numeric fields."""
    team = espn_week.team_by_id("1")
    mahomes = next(p for p in team.lineup if p.name == "Patrick Mahomes")
    assert mahomes.points == pytest.approx(24.6)
    assert mahomes.projected == pytest.approx(21.2)
    # The season-to-date row is nine times the weekly one in the fixture.
    assert mahomes.points != pytest.approx(24.6 * 9)


def test_espn_a_player_with_no_projection_is_not_invented(espn_week):
    team = espn_week.team_by_id("2")
    rookie = next(p for p in team.lineup if p.name == "Undrafted Rookie")
    assert rookie.projected is None
    assert rookie.vs_projection is None


def test_espn_records_exclude_the_week_being_reported(espn_week):
    """team.record.overall includes it — confirmed against the real league,
    where a team that had lost its only game showed 0-1 while we were
    reporting that very game."""
    assert espn_week.team_by_id("1").record == "1-0"
    assert espn_week.team_by_id("2").record == "0-1"


def test_espn_an_unplayed_week_does_not_count_toward_records(espn_week):
    """The fixture carries a week 3 marked UNDECIDED. Counting it either way
    would be inventing a result."""
    for team in espn_week.teams:
        assert team.games_played == 1


def test_espn_team_name_falls_back_to_location_and_nickname(espn_week):
    """ESPN has used both shapes. The failure mode is printing "None None"
    across the top of somebody's paper."""
    assert espn_week.team_by_id("2").team_name == "Satan's Sommeliers"


def test_espn_manager_name_falls_back_when_there_is_no_display_name(espn_week):
    assert espn_week.team_by_id("2").manager.display_name == "Champ Hammond"


def test_espn_player_ids_are_namespaced(espn_week):
    for team in espn_week.teams:
        for player in team.all_players:
            assert player.player_id.startswith("espn:")


def test_espn_defenses_get_no_headshot(espn_week):
    team = espn_week.team_by_id("1")
    defense = next(p for p in team.lineup if p.position == "DEF")
    assert defense.headshot_url is None
    assert defense.nfl_team == "SEA"


def test_espn_lineup_gap_finds_the_benched_stud(espn_week):
    """Gibbs scored 33.6 on the bench while Hubbard started at FLEX for 6.5."""
    team = espn_week.team_by_id("1")
    assert team.lineup_gap > 20


def test_espn_available_weeks_excludes_the_week_in_progress(espn):
    """latestScoringPeriod counts the week being played right now, which has
    partial scores. A paper about a Sunday that is still happening is worse
    than no paper."""
    assert espn.available_weeks(fixtures_espn.LEAGUE_ID,
                                fixtures_espn.SEASON) == [1, 2]


def test_espn_a_private_league_says_how_to_fix_it(tmp_path, monkeypatch):
    """The product tells people to make their league public. Some will not.
    401 is therefore the most likely failure this adapter will ever see, and
    it deserves the instruction rather than a status code."""
    import requests
    from providers.base import AuthRequired

    class Resp:
        status_code = 401

        def json(self):
            return {}

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    p = ESPNProvider(cache=TTLCache(cache_dir=tmp_path, namespace="e2"))

    with pytest.raises(AuthRequired) as caught:
        p.get_league("123", 2026)
    assert "public" in str(caught.value).lower()


def test_espn_an_unknown_league_is_not_found_rather_than_a_crash(tmp_path,
                                                                 monkeypatch):
    """ESPN answers an unknown league with an HTML error page, not JSON."""
    import requests
    from providers.base import LeagueNotFound

    class Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    p = ESPNProvider(cache=TTLCache(cache_dir=tmp_path, namespace="e3"))

    with pytest.raises(LeagueNotFound):
        p.get_league("nope", 2026)


def test_espn_feeds_the_writer_the_same_shape_sleeper_does(espn_week):
    """The whole point of the provider layer. Nothing downstream should be
    able to tell which platform a paper came from."""
    games = week_to_legacy_games(espn_week)
    assert games
    for game in games:
        for side in ("team_1", "team_2"):
            team = game[side]
            assert team["all_starters"], "the writer would have no players"
            assert team["record_after"]
            for player in team["all_starters"]:
                assert player["name"]
                assert player["actual"] is not None


def test_espn_the_wrong_week_is_never_read_as_this_week(espn_week):
    """Every player in the fixture carries a week 1 row worth 99.9 and a
    season-to-date row worth nine times the week. Both sit BEFORE the correct
    row in the stats array, so anything that stops filtering properly picks up
    a wrong number rather than failing loudly."""
    for team in espn_week.teams:
        for player in team.all_players:
            assert player.points != pytest.approx(99.9)
            assert player.projected != pytest.approx(88.8)


# ---------------------------------------------------------------------------
# ESPN transactions
#
# Every record in the fixture is one ESPN actually produced for league
# 1909054258 in week 2 of 2026, captured through a browser because the egress
# proxy here cannot reach ESPN. The feed is not a list of roster moves — it is
# a list of everything that touched a roster, and most of it is not news.
# ---------------------------------------------------------------------------

@pytest.fixture
def espn_moves(espn):
    return espn.get_transactions(fixtures_espn.LEAGUE_ID, 2026, 2)


def test_setting_your_lineup_is_not_a_transaction(espn_moves):
    """The big one. Nine of the league's nineteen records were type ROSTER
    carrying only LINEUP items — somebody moving a running back from the bench
    to the flex. Printed as transactions they are nine lines of nothing in the
    middle of the paper, and they outnumber the real moves two to one."""
    everyone = [p.name for t in espn_moves
                for _, p in (t.adds + t.drops)]
    assert "Rhamondre Stevenson" not in everyone, (
        "a lineup change reached the wire")
    assert len(espn_moves) == 4, (
        f"expected 4 real moves from 7 records, got {len(espn_moves)}: "
        f"{[(t.kind, t.status) for t in espn_moves]}")


def test_a_claim_that_has_not_processed_is_not_reported_at_all(espn_moves):
    """Not as a success, and — the version this shipped with first — not as a
    failure either.

    PENDING is not EXECUTED, so the obvious status mapping calls it failed,
    and the paper tells the league a claim lost that has not run yet and may
    still win. It will process on Tuesday and appear correctly in next week's
    paper, having already been reported as lost in this one.
    """
    claimed = [p.name for t in espn_moves for _, p in t.adds]
    assert "Malik Washington" not in claimed, (
        "a pending waiver claim was printed as though it had happened")


def test_a_completed_trade_survives_its_own_pending_flag(espn_moves):
    """`isPending` does not mean "did not happen".

    The one completed trade in the league reports status EXECUTED and
    isPending TRUE at the same time. Filtering on isPending — which is the
    obvious way to drop the pending claim above — deletes the single most
    interesting row of the week. So the filter is on status instead.
    """
    trades = [t for t in espn_moves if t.kind == "trade"]
    assert len(trades) == 1, "the trade was dropped"

    trade = trades[0]
    assert trade.status == "complete"
    # Both sides, both directions: a TRADE item is one player leaving one
    # roster and arriving on another, so it is a drop and an add at once.
    assert {p.name for _, p in trade.adds} == {"Carnell Tate", "A.J. Brown"}
    assert {p.name for _, p in trade.drops} == {"Carnell Tate", "A.J. Brown"}
    assert set(trade.teams) == {"Mike Vick Legal Team", "ACCOMMODATIONS"}


def test_a_failed_claim_is_recognised_and_kept(espn_moves):
    """`status` is a FAMILY of strings, not a value.

    The real failure was FAILED_INVALIDPLAYERSOURCE. Comparing against
    "FAILED" matches nothing, and every failed claim would print as a
    successful one — the paper announcing pickups that never happened.

    Kept rather than dropped, deliberately, the same as Sleeper: somebody bid
    publicly and did not get the player, and everyone can see what they wanted.
    """
    failed = [t for t in espn_moves if t.status == "failed"]
    assert len(failed) == 1
    assert [p.name for _, p in failed[0].adds] == ["Jalen Coker"]


def test_a_waiver_priority_league_reports_no_bid(espn_moves):
    """ESPN sends bidAmount 0 for every claim in a league that runs on
    priority rather than a budget, which is most of them. Zero is the absence
    of bidding, not a bid of nothing — "$0" in the paper tells a league that
    does not use FAAB that everybody bid nothing."""
    assert all(t.bid is None for t in espn_moves), (
        [t.bid for t in espn_moves])


def test_a_real_faab_bid_still_comes_through(espn):
    """The other half of the rule above: a league that does use a budget has
    to see its numbers."""
    raw = copy.deepcopy(fixtures_espn.TRANSACTIONS_RAW)
    for row in raw["transactions"]:
        if row["type"] == "WAIVER" and row["status"] == "EXECUTED":
            row["bidAmount"] = 47

    espn._get = (lambda lid, season, views, params=None, fantasy_filter=None:
                 raw if "mTransactions2" in set(views)
                 else fixtures_espn.fake_get(lid, season, views, params,
                                             fantasy_filter))
    bids = [t.bid for t in espn.get_transactions(fixtures_espn.LEAGUE_ID, 2026, 2)]
    assert 47 in bids, bids


def test_defences_come_through_named(espn_moves):
    """ESPN gives a D/ST a negative player id and a perfectly ordinary
    fullName, so unlike Sleeper there is no special case — but the negative id
    is exactly the sort of thing a lookup drops on the floor, so it is
    checked."""
    names = {p.name for t in espn_moves for _, p in (t.adds + t.drops)}
    assert "Buccaneers D/ST" in names
    assert "Jaguars D/ST" in names


def test_players_are_looked_up_by_id_rather_than_hoped_for(espn_moves):
    """A transaction record names nobody — it carries player ids and nothing
    else. The names come from a second request carrying an X-Fantasy-Filter
    header, and the test fake returns nothing at all when that header is
    missing, so an adapter that forgets it fails here rather than silently
    printing "A player" nine times."""
    names = {p.name for t in espn_moves for _, p in (t.adds + t.drops)}
    assert "A player" not in names, "the player lookup did not happen"
    assert "Jacory Croskey-Merritt" in names


def test_another_weeks_move_stays_in_another_week(espn_moves):
    """The feed carries the whole season, not the week asked for."""
    names = {p.name for t in espn_moves for _, p in (t.adds + t.drops)}
    assert "Germie Bernard" not in names, "a week 1 claim printed in week 2"


def test_free_agency_is_not_a_manager(espn_moves):
    """`fromTeamId`/`toTeamId` are 0 for the free agent pool. A name for team
    0 would print "Team 0 acquired…" as though it were somebody in the
    league."""
    for t in espn_moves:
        for team, _ in (t.adds + t.drops):
            assert team and not team.startswith("Team 0"), team


def test_a_transactions_outage_costs_the_section_and_nothing_else(espn):
    """The scores are the product; this section is a bonus on top."""
    from providers.base import ProviderError

    def explode(*a, **k):
        raise ProviderError("ESPN is having a moment")

    espn._get = explode
    assert espn.get_transactions(fixtures_espn.LEAGUE_ID, 2026, 2) == []


def _espn_with(transactions, espn):
    """Point the provider at a hand-built transactions payload."""
    raw = copy.deepcopy(fixtures_espn.TRANSACTIONS_RAW)
    raw["transactions"] = transactions
    espn._get = (lambda lid, season, views, params=None, fantasy_filter=None:
                 raw if "mTransactions2" in set(views)
                 else fixtures_espn.fake_get(lid, season, views, params,
                                             fantasy_filter))
    return espn


def test_both_lineup_guards_hold_on_their_own(espn):
    """Written because the first version of the lineup test was vacuous.

    Two independent things drop a lineup change: ROSTER is not in _MOVE_TYPES,
    and LINEUP is not in _MOVE_ITEMS. Against the real fixture, deleting
    EITHER one still produces the right answer, because the other catches it —
    so `test_setting_your_lineup_is_not_a_transaction` passed with the type
    filter removed and was defending nothing.

    Neither shape below was observed in the real league, which is the point:
    they exist to hold each guard up on its own, rather than to model ESPN.
    """
    # A ROSTER record that does move a player. Only the TYPE filter stops it.
    roster_with_an_add = _espn_with([{
        "type": "ROSTER", "status": "EXECUTED", "isPending": False,
        "scoringPeriodId": 2, "teamId": 3, "bidAmount": 0,
        "proposedDate": 1789542778194,
        "items": [{"type": "ADD", "playerId": 4575131,
                   "fromTeamId": 0, "toTeamId": 3}],
    }], espn).get_transactions(fixtures_espn.LEAGUE_ID, 2026, 2)
    assert roster_with_an_add == [], (
        "a ROSTER record reached the wire — only the item filter is holding")

    # A WAIVER record carrying nothing but a lineup shuffle. Only the ITEM
    # filter stops it.
    waiver_with_only_lineup = _espn_with([{
        "type": "WAIVER", "status": "EXECUTED", "isPending": False,
        "scoringPeriodId": 2, "teamId": 3, "bidAmount": 0,
        "proposedDate": 1789542778194,
        "items": [{"type": "LINEUP", "playerId": 4569173,
                   "fromTeamId": 0, "toTeamId": 0}],
    }], espn).get_transactions(fixtures_espn.LEAGUE_ID, 2026, 2)
    assert waiver_with_only_lineup == [], (
        "a LINEUP item reached the wire — only the type filter is holding")


# ---------------------------------------------------------------------------
# What belongs in a WEEKLY paper
# ---------------------------------------------------------------------------

def test_the_offseason_does_not_count_as_this_week(monkeypatch):
    """The Hands Times week 1 printed NINETY transactions across four pages,
    including people dropping players in July.

    ESPN files every move made before the season under scoringPeriodId 1 — the
    whole offseason, every preseason cut, months of it. Filtering by the
    platform's own week number is not a filter at all in week 1, which is
    exactly the week a new league generates its first paper.
    """
    import providers
    from providers.models import Transaction

    start, end = providers.transaction_window(2026, 1)

    july = int(providers.transaction_window(2026, 1)[0]) - 60 * 86400 * 1000
    moves = [
        Transaction(kind="free_agent", status="complete", week=1,
                    created=july, adds=[], drops=[]),
        Transaction(kind="waiver", status="complete", week=1,
                    created=start + 86400 * 1000, adds=[], drops=[]),
    ]

    # monkeypatch, not assignment: the first version of this test replaced
    # providers.get_provider for the rest of the session and broke an
    # unrelated test forty files later.
    monkeypatch.setattr(providers, "get_provider", lambda *a, **k: type(
        "P", (), {"get_transactions": lambda *a, **k: moves})())
    kept = providers.load_transactions("espn", "1", 2026, 1)

    assert len(kept) == 1, (
        f"{len(kept)} moves survived; the July drop should not have")
    assert kept[0].kind == "waiver"


def test_the_window_is_anchored_to_the_week_not_to_today():
    """Anchoring to now would be simpler and would break the archive.

    A paper re-renders every time it is edited, so a week 1 paper opened and
    corrected in December would fetch its transactions against a December
    window and come back empty — the section silently disappearing from an old
    paper. The classifieds page has already been through this exact trap.
    """
    import providers

    week_one = providers.transaction_window(2026, 1)
    week_five = providers.transaction_window(2026, 5)

    assert week_five[0] > week_one[0]
    # Four weeks apart, to the millisecond, regardless of when this runs.
    assert week_five[0] - week_one[0] == 4 * 7 * 86400 * 1000


def test_the_window_is_exactly_a_week_long():
    import providers

    start, end = providers.transaction_window(2026, 3)
    assert end - start == providers.TRANSACTION_WINDOW_DAYS * 86400 * 1000


def test_a_move_with_no_timestamp_keeps_its_place(monkeypatch):
    """A platform that does not date its transactions should lose the filter,
    not the section."""
    import providers
    from providers.models import Transaction

    undated = Transaction(kind="trade", status="complete", week=1,
                          created=None, adds=[], drops=[])
    monkeypatch.setattr(providers, "get_provider", lambda *a, **k: type(
        "P", (), {"get_transactions": lambda *a, **k: [undated]})())

    assert len(providers.load_transactions("sleeper", "1", 2026, 1)) == 1
