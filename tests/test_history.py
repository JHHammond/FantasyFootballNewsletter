"""The season so far, next week's lines, and the obituary's subject."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import history  # noqa: E402
from providers.models import (League, Manager, Matchup, PlayerLine,  # noqa: E402
                              Team, WeekData)

LEAGUE = League(provider="sleeper", league_id="1", name="L", season=2026)


def _player(name, points, projected=None, pos="RB"):
    return PlayerLine(player_id=f"sleeper:{name}", name=name, position=pos,
                      points=points, slot=pos, projected=projected)


def _team(tid, points, lineup=None):
    return Team(team_id=tid, team_name=f"Team {tid}",
                manager=Manager(manager_id=tid, display_name=tid.capitalize()),
                points=points, lineup=lineup or [])


def _week(n, *games):
    return WeekData(league=LEAGUE, week=n, matchups=[
        Matchup(matchup_id=str(i), teams=(a, b)) for i, (a, b) in enumerate(games)])


# Four teams, three weeks. Carson wins every week; Will loses every week;
# week 3 is a rematch of week 1 (carson v will).
W1 = _week(1, (_team("carson", 120), _team("will", 100)),
              (_team("steve", 110), _team("mark", 90)))
W2 = _week(2, (_team("carson", 130), _team("steve", 100)),
              (_team("will", 80), _team("mark", 95)))
W3 = _week(3, (_team("carson", 140), _team("will", 139)),
              (_team("steve", 105), _team("mark", 106)))
RESULTS = history.results_from_weeks([W1, W2, W3])


def test_streaks_and_records():
    h = history.team_histories(RESULTS, 3)
    assert h["carson"].record == "3-0"
    assert (h["carson"].streak_kind, h["carson"].streak_len) == ("W", 3)
    assert (h["will"].streak_kind, h["will"].streak_len) == ("L", 3)
    assert (h["mark"].streak_kind, h["mark"].streak_len) == ("W", 2)
    assert h["steve"].last.week == 2   # the week BEFORE this one


def test_the_briefing_names_streaks_rematches_and_last_week():
    text = history.previously_on(RESULTS, 3, [("carson", "will"), ("steve", "mark")])
    assert "Team will (Will): 0-3" in text
    assert "lost 3 straight" in text
    assert "won 3 straight" in text
    assert "rematch" in text and "week 1" in text and "120.0-100.0" in text
    assert "last week beat Team steve 130.0-100.0" in text


def test_the_briefing_carries_last_weeks_awards():
    last = {"headline": "CARSON ROLLS", "awards": [
        {"title": "GARDNER MINSHEW AWARD", "body": "Will benched Bijan for 31."}]}
    text = history.previously_on(RESULTS, 3, [], last)
    assert "CARSON ROLLS" in text
    assert "Last week's GARDNER MINSHEW AWARD: Will benched Bijan" in text


def test_week_one_has_no_briefing():
    assert history.previously_on(history.results_from_weeks([W1]), 1, []) == ""


def test_a_week_that_cannot_be_fetched_is_skipped_not_fatal():
    def fetch(w):
        if w == 2:
            raise RuntimeError("platform down")
        return {1: W1, 3: W3}[w]
    results = history.load_season(fetch, 3)
    assert {r.week for r in results} == {1, 3}


# --- lines -----------------------------------------------------------------

def _proj_team(tid, *projections):
    return _team(tid, 0, [_player(f"{tid}{i}", 0, p) for i, p in enumerate(projections)])


def test_the_favourite_is_whoever_projects_higher_and_the_gap_is_the_spread():
    nxt = _week(4, (_proj_team("will", 20, 20, 20.2), _proj_team("carson", 30, 30, 30)))
    [line] = history.betting_lines(nxt, {})
    assert line["favorite"] == "Team carson"
    assert line["spread"] == 30.0          # 90 - 60.2 = 29.8 -> nearest half
    assert line["total"] == 150.0
    assert line["basis"] == "projection"
    assert history.format_line(line) == "Team carson −30 vs Team will"


def test_a_near_tie_is_a_pickem():
    nxt = _week(4, (_proj_team("a", 50.2), _proj_team("b", 50.0)))
    [line] = history.betting_lines(nxt, {})
    assert line["pickem"]
    assert "PICK'EM" in history.format_line(line)


def test_no_projections_falls_back_to_points_per_game_for_both_teams():
    """Never one team's projection against the other's average."""
    nxt = _week(4, (_proj_team("carson", 40, 40), _team("will", 0)))
    hist = history.team_histories(RESULTS, 3)
    [line] = history.betting_lines(nxt, hist)
    assert line["basis"] == "average"
    assert line["favorite"] == "Team carson"
    assert line["spread"] == history.round_spread(130.0 - (100 + 80 + 139) / 3)


def test_lines_are_sorted_biggest_spread_first():
    nxt = _week(4, (_proj_team("a", 50), _proj_team("b", 49)),
                   (_proj_team("c", 90), _proj_team("d", 40)))
    lines = history.betting_lines(nxt, {})
    assert [l["favorite"] for l in lines] == ["Team c", "Team a"]


# --- the obituary ------------------------------------------------------------

def test_the_obituary_goes_to_the_biggest_miss_against_projection():
    wk = _week(3, (_team("a", 50, [_player("Jacobs", 2.1, 18.4),
                                   _player("Kicker", 1.0, 8.0, "K")]),
                   _team("b", 60, [_player("Henry", 35.3, 16.0)])))
    bust = history.biggest_bust(wk)
    assert bust["name"] == "Jacobs"
    assert bust["manager"] == "A"
    assert bust["projected"] == 18.4


def test_without_projections_the_obituary_skips_kickers_and_defences():
    wk = _week(3, (_team("a", 50, [_player("Kicker", 0.0, None, "K"),
                                   _player("Waddle", 1.2, None, "WR")]),
                   _team("b", 60, [_player("Henry", 35.3)])))
    assert history.biggest_bust(wk)["name"] == "Waddle"


# --- wired into generation ---------------------------------------------------

def test_generation_hands_the_writer_the_season_and_next_weeks_lines(monkeypatch):
    from web import generate, demo_db

    weeks = {1: W1, 2: W2, 3: W3,
             4: _week(4, (_proj_team("carson", 40, 40), _proj_team("will", 20, 20)))}
    monkeypatch.setattr(generate, "load_week",
                        lambda provider, lid, season, w, **k: weeks[w])
    league = {"id": "L1", "provider": "sleeper", "platform_league_id": "1",
              "season": 2026}
    out = generate._season_briefing(demo_db, league, 3, W3)
    assert "won 3 straight" in out["briefing"]
    assert out["lines"][0]["favorite"] == "Team carson"


def test_the_briefing_never_breaks_generation(monkeypatch):
    from web import generate, demo_db

    def boom(*a, **k):
        raise RuntimeError("platform down")
    monkeypatch.setattr(generate, "load_week", boom)
    league = {"id": "L1", "provider": "sleeper", "platform_league_id": "1",
              "season": 2026}
    out = generate._season_briefing(demo_db, league, 3, W3)
    assert out["lines"] == []


def test_obituaries_are_the_lowest_scoring_starters():
    """John: the lowest-scoring players that were in active lineups."""
    wk = _week(3, (_team("a", 50, [_player("Jacobs", 2.1, 18.4),
                                   _player("DefA", -3.0, 6.0, "DEF"),
                                   _player("KickA", 1.0, 8.0, "K"),
                                   _player("Waddle", 1.2, 9.0, "WR")]),
                   _team("b", 60, [_player("Henry", 35.3, 16.0),
                                   _player("Pitts", 0.0, 9.0, "TE"),
                                   _player("Swift", 4.0, 12.0)])))
    names = [d["name"] for d in history.lowest_starters(wk)]
    # at most one kicker/defence, then the lowest scores in order
    assert names == ["DefA", "Pitts", "Waddle", "Jacobs"]


def test_the_promo_comes_from_the_environment(monkeypatch):
    from web import generate
    monkeypatch.delenv("PRIZEPICKS_CODE", raising=False)
    assert generate.promo_settings() is None
    monkeypatch.setenv("PRIZEPICKS_CODE", "JOHNH")
    monkeypatch.setenv("PRIZEPICKS_IMAGE_URL", "https://cdn/x.png")
    assert generate.promo_settings() == {"code": "JOHNH", "image_url": "https://cdn/x.png",
                                         "link": ""}
