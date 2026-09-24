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
    assert "Team will: 0-3" in text
    assert "(Will)" not in text          # team names only (John, 23 Sep)
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
    for k in ("PROMO_CODE", "PRIZEPICKS_CODE", "PROMO_IMAGE_URL", "PROMO_BRAND", "PROMO_LINK"):
        monkeypatch.delenv(k, raising=False)
    assert generate.promo_settings() is None
    monkeypatch.setenv("PROMO_CODE", "JOHNHAMMOND3")
    out = generate.promo_settings()
    assert out["code"] == "JOHNHAMMOND3" and out["brand"] == "Underdog"
    assert out["image_url"].endswith("/static/promo/referral.png")
    monkeypatch.setenv("PROMO_IMAGE_URL", "https://cdn/x.png")
    assert generate.promo_settings()["image_url"] == "https://cdn/x.png"


def test_matchup_memory_is_only_the_notable_things():
    last = {"awards": [{"title": "JERRY JONES AWARD", "body": "Will started a bye."},
                       {"title": "KYLE PITTS AWARD", "body": "Steve got lucky."}]}
    text = history.matchup_memory(RESULTS, 3, "carson", "will", last)
    assert "won 3 straight" in text and "lost 3 straight" in text
    assert "Rematch: in week 1" in text
    assert "Jerry Jones Award" in text and "Steve got lucky" not in text
    # no routine records, last results or headlines
    assert "last week" not in text.lower().replace("last week the paper", "")


def test_a_game_with_nothing_notable_gets_no_memory():
    # week 2, carson v steve: first meeting, no streak of three, too early
    # for a season high — so nothing to say, and nothing is said
    results = history.results_from_weeks([W1, W2])
    assert history.matchup_memory(results, 2, "carson", "steve") == ""


def test_draft_notes_only_where_they_are_worth_a_mention():
    from web import generate
    games = [{"team_1": {"all_starters": [
                {"player_id": "1", "actual": 6.0}, {"player_id": "5", "actual": 9.0},
                {"player_id": "12", "actual": 22.0}, {"player_id": "99", "actual": 24.0},
                {"player_id": "98", "actual": 3.0}]},
              "team_2": {}}]
    picks = {"1": {"round": 1, "overall": 3}, "5": {"round": 5, "overall": 50},
             "12": {"round": 12, "overall": 130}}
    generate.annotate_draft(games, picks)
    by_id = {p["player_id"]: p.get("draft_note") for p in games[0]["team_1"]["all_starters"]}
    assert by_id["1"] == "drafted round 1, #3 overall"
    assert by_id["5"] is None
    assert by_id["12"] == "drafted round 12"
    assert "undrafted" in by_id["99"]
    assert by_id["98"] is None


def test_no_draft_notes_outside_redraft_leagues():
    from web import generate
    assert generate._draft_picks({"format": "dynasty", "provider": "sleeper"}) == {}


def test_sleeper_draft_picks_come_from_the_completed_draft(monkeypatch, tmp_path):
    from providers import SleeperProvider, TTLCache
    p = SleeperProvider(cache=TTLCache(cache_dir=tmp_path, namespace="t"))
    calls = {
        "/league/L/drafts": [{"draft_id": "old", "status": "pre_draft"},
                             {"draft_id": "D", "status": "complete", "start_time": 5}],
        "/draft/D/picks": [{"player_id": "4046", "round": 1, "pick_no": 3},
                           {"player_id": None, "round": 2, "pick_no": 14}],
    }
    monkeypatch.setattr(p, "_get", lambda url, params=None: next(
        v for k, v in calls.items() if url.endswith(k)))
    assert p.draft_picks("L", 2026) == {"4046": {"round": 1, "overall": 3}}


def test_a_season_high_needs_three_earlier_games():
    w4 = _week(4, (_team("carson", 150), _team("mark", 90)),
                  (_team("steve", 100), _team("will", 90)))
    results = history.results_from_weeks([W1, W2, W3, w4])
    assert "new season high" in history.matchup_memory(results, 4, "carson", "mark")
    assert "new season high" not in history.matchup_memory(RESULTS, 3, "carson", "will")


def test_weekly_scores_share_one_scale_and_use_current_names():
    out = history.weekly_scores(RESULTS, 3)
    assert out["lo"] == 80 and out["hi"] == 140
    assert out["teams"]["Team carson"] == [[1, 120.0], [2, 130.0], [3, 140.0]]
    assert set(out["teams"]) == {"Team carson", "Team will", "Team steve", "Team mark"}


def test_the_trend_is_frozen_into_the_paper(monkeypatch):
    from web import generate, demo_db
    weeks = {1: W1, 2: W2, 3: W3}

    def lw(provider, lid, season, w, **k):
        if w not in weeks:
            raise RuntimeError("no week")
        return weeks[w]
    monkeypatch.setattr(generate, "load_week", lw)
    league = {"id": "L1", "provider": "sleeper", "platform_league_id": "1",
              "season": 2026}
    out = generate._season_briefing(demo_db, league, 3, W3)
    assert out["trends"]["teams"]["Team will"][-1] == [3, 139.0]


# --- John's standing awards (storylines) ------------------------------------

def _legacy_team(name, pts, starters, bench=()):
    return {"team_name": name, "owner_name": name.lower(), "points": pts,
            "record": "0-0", "lineup_gap": 0.0, "empty_slots": 0,
            "all_starters": list(starters), "all_bench": list(bench)}


def test_the_standing_award_winners():
    import storylines
    a = _legacy_team("A", 90, [
        {"name": "Snell", "actual": 0.0, "projected": 9.0, "beat_projection_by": -9.0},
        {"name": "Pitts", "actual": 3.0, "projected": 18.0, "beat_projection_by": -15.0}],
        bench=[{"name": "Foles", "actual": 31.0}])
    b = _legacy_team("B", 120, [
        {"name": "Allen", "actual": 40.0, "projected": 22.0, "beat_projection_by": 18.0},
        {"name": "Zero-ish", "actual": -1.0, "projected": 5.0, "beat_projection_by": -6.0}],
        bench=[{"name": "Backup", "actual": 12.0}])
    games = [{"team_1": a, "team_2": b, "winner": "B", "margin": 30.0}]
    s = storylines.get_weekly_storylines(games)
    assert s["tony_snell"]["player"]["name"] == "Snell"         # a true zero
    assert s["kyle_pitts"]["player"]["name"] == "Pitts"         # biggest miss
    assert s["kyle_pitts"]["team"]["team_name"] == "A"
    assert s["nick_foles"]["player"]["name"] == "Foles"         # best bench anywhere
    assert s["best_loser"]["team_name"] == "A"                  # Joe Burrow
