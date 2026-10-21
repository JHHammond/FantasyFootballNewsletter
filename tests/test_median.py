"""League median games (20 Oct 2026).

A median league gives every team a second result each week: a win for a
top-half score, a loss for a bottom-half one. Before this, the paper's
records counted only the head-to-head games and disagreed with Sleeper.
"""

import copy

import pytest

from providers.cache import TTLCache
from providers.models import median_outcomes
from providers.sleeper import SleeperProvider, _median_through_week
from tests import fixtures


# --- the rule ---------------------------------------------------------------

def test_top_half_wins_bottom_half_loses():
    out = median_outcomes({"a": 140, "b": 120, "c": 100, "d": 80})
    assert out == {"a": "W", "b": "W", "c": "L", "d": "L"}


def test_a_dead_heat_across_the_cut_is_a_tie_for_both():
    out = median_outcomes({"a": 140, "b": 110, "c": 110, "d": 80})
    assert out == {"a": "W", "b": "T", "c": "T", "d": "L"}


def test_odd_league_the_middle_team_ties():
    out = median_outcomes({"a": 140, "b": 120, "c": 100, "d": 90, "e": 80})
    assert out == {"a": "W", "b": "W", "c": "T", "d": "L", "e": "L"}


def test_nothing_to_compare():
    assert median_outcomes({}) == {}
    assert median_outcomes({"a": 100}) == {}


# --- reading Sleeper's setting --------------------------------------------

def test_sleeper_setting():
    assert _median_through_week({}) is None
    assert _median_through_week({"league_average_match": 0, "playoff_week_start": 15}) is None
    assert _median_through_week({"league_average_match": 1, "playoff_week_start": 15}) == 14
    assert _median_through_week({"league_average_match": 1}) == 18


# --- end to end on the Sleeper fixture -------------------------------------

def _provider(tmp_path, monkeypatch, median: bool, playoff_start=15):
    league = copy.deepcopy(fixtures.LEAGUE)
    settings = dict(league.get("settings") or {})
    settings["league_average_match"] = 1 if median else 0
    settings["playoff_week_start"] = playoff_start
    league["settings"] = settings

    def fake(url, params=None):
        if "/league/" in url and not any(x in url for x in
                                         ("/users", "/rosters", "/matchups/", "/transactions/")):
            return league
        return fixtures.fake_get(url, params)

    p = SleeperProvider(cache=TTLCache(cache_dir=tmp_path,
                                       namespace=f"med{int(median)}{playoff_start}"))
    monkeypatch.setattr(p, "_get", fake)
    return p


def _by_team(p, week):
    from providers.compat import week_to_legacy_games
    out = {}
    for g in week_to_legacy_games(p.get_week("TESTLEAGUE", 2025, week)):
        for side in ("team_1", "team_2"):
            out[g[side]["team_name"]] = g[side]
    return out


def _games(team_record: str) -> int:
    return sum(int(x) for x in team_record.split("-"))


def test_median_league_gets_two_results_a_week(tmp_path, monkeypatch):
    plain = _by_team(_provider(tmp_path, monkeypatch, median=False), 2)
    median = _by_team(_provider(tmp_path, monkeypatch, median=True), 2)
    for name in plain:
        # Two weeks played: two games without the median, four with it.
        assert _games(plain[name]["record_after"]) == 2
        assert _games(median[name]["record_after"]) == 4
        assert plain[name]["median_result"] is None
        assert median[name]["median_result"] in ("W", "L", "T")


def test_median_records_carry_over_week_to_week(tmp_path, monkeypatch):
    """Week N's printed record must be week N+1's starting record."""
    p = _provider(tmp_path, monkeypatch, median=True)
    for week in (1, 2):
        now, nxt = _by_team(p, week), _by_team(p, week + 1)
        for name, team in now.items():
            assert team["record_after"] == nxt[name]["record"], name


def test_median_switches_off_for_the_playoffs(tmp_path, monkeypatch):
    p = _provider(tmp_path, monkeypatch, median=True, playoff_start=3)
    week3 = _by_team(p, 3)
    for team in week3.values():
        assert team["median_result"] is None
        # Weeks 1-2 count the median, week 3 doesn't: 2*2 + 1 results.
        assert _games(team["record_after"]) == 5


def test_writer_is_told_about_a_1_1_week():
    from writer import _records_line
    ctx = {"winner": "A", "loser": "B", "winner_record": "3-1",
           "loser_record": "2-2", "winner_median": "L", "loser_median": "W"}
    line = _records_line(ctx)
    assert "median" in line
    assert "B lost the game but still went 1-1" in line
    assert "A won the game but only went 1-1" in line
    ctx.pop("winner_median"); ctx.pop("loser_median")
    assert "median" not in _records_line(ctx)


def test_history_counts_the_median_in_the_record_not_in_games():
    from history import Result, team_histories
    rs = [Result(1, "a", "A", "m", 120.0, "b", "B", "n", 100.0, median="W"),
          Result(2, "a", "A", "m", 90.0, "b", "B", "n", 95.0, median="L")]
    h = team_histories(rs, 2)["a"]
    assert (h.wins, h.losses) == (2, 2)
    assert h.games == 2
    assert h.points_per_game == pytest.approx(105.0)


def test_a_record_with_a_tie_keeps_its_place_in_the_standings():
    """parse_record split on "-" into exactly two parts, so "7-4-1" failed
    and came back 0-0, and the team dropped to last."""
    from newspaper import build_standings, parse_record
    assert parse_record("7-4-1") == (7, 4)
    assert parse_record("7-5") == (7, 5)
    games = [{"team_1": {"team_name": "Tied", "record_after": "7-4-1", "points": 100},
              "team_2": {"team_name": "Plain", "record_after": "7-5", "points": 150}},
             {"team_1": {"team_name": "Bad", "record_after": "2-10", "points": 90},
              "team_2": {"team_name": "Good", "record_after": "9-3", "points": 80}}]
    order = [t["team_name"] for t in build_standings(games)]
    assert order == ["Good", "Tied", "Plain", "Bad"]


def test_fraud_needs_twice_the_wins_in_a_median_league(tmp_path, monkeypatch):
    """Fraud watch is "8+ wins and a weak week". In a median league that's
    only four weeks in, so the bar doubles. Uses the real fixture games."""
    from providers.compat import week_to_legacy_games
    from storylines import get_weekly_storylines
    p = _provider(tmp_path, monkeypatch, median=True)
    games = week_to_legacy_games(p.get_week("TESTLEAGUE", 2025, 3))
    for g in games:
        for side in ("team_1", "team_2"):
            g[side]["record"] = "9-3"      # would be a fraud candidate at 8+
    assert get_weekly_storylines(games).get("fraud") is None
    for g in games:
        for side in ("team_1", "team_2"):
            g[side]["record"] = "16-4"
    assert get_weekly_storylines(games).get("fraud") is not None
