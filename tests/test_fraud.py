"""Fraud Watch picks the team that fell short of expectations (2 Nov)."""

from storylines import find_fraud


def _team(name, record, points, starters, after=None, gap=0):
    return {"team_name": name, "record": record, "record_after": after or record,
            "points": points, "owner_name": name.lower(), "lineup_gap": gap,
            "all_starters": [{"name": n, "position": pos, "projected": pr, "actual": ac}
                             for n, pos, pr, ac in starters]}


def _game(a, b):
    winner = a if a["points"] > b["points"] else b
    return {"team_1": a, "team_2": b, "winner": winner["team_name"],
            "margin": abs(a["points"] - b["points"])}


def _even(proj, actual, n=4):
    return [(f"P{i}", "WR", proj, actual) for i in range(n)]


def test_a_contender_that_lays_an_egg_beats_a_bad_team_being_bad():
    contender = _team("Contender", "5-2", 70.0,
                      [("Lamar Jackson", "QB", 20.5, 0.0)] + _even(25.0, 17.5),
                      after="5-3", gap=16.6)
    rival = _team("Rival", "4-3", 140.0, _even(25.0, 35.0))
    cellar = _team("Cellar", "1-6", 60.0, _even(16.0, 15.0), after="1-7")
    winner = _team("Winner", "2-5", 90.0, _even(15.0, 22.5))
    team, facts = find_fraud([_game(contender, rival), _game(cellar, winner)])
    assert team["team_name"] == "Contender"
    text = " ".join(facts)
    assert "came in 5-2, 1st of 4 in the standings, and is now 5-3" in text
    assert "projected for 120.5. It scored 70.0, 50.5 short" in text
    assert "projected to beat Rival (120.5 to 100.0) and lost 140.0-70.0" in text
    assert "Lamar Jackson (QB) scored 0.0 against a 20.5 projection" in text
    assert "left 16.6 points on its bench" in text


def test_a_record_its_scoring_never_earned_counts():
    a = _team("Lucky", "3-3", 100.0, _even(30.0, 25.0), after="3-4")
    b = _team("Hot", "3-3", 120.0, _even(25.0, 30.0))
    c = _team("Also", "3-3", 95.0, _even(30.0, 23.75), after="3-4")
    d = _team("Fine", "3-3", 110.0, _even(25.0, 27.5))
    season = {"Lucky": {"record_rank": 1, "pf_rank": 4, "streak": "It has lost 2 straight."},
              "Also": {"record_rank": 4, "pf_rank": 4, "streak": ""}}
    team, facts = find_fraud([_game(a, b), _game(c, d)], season)
    assert team["team_name"] == "Lucky"
    assert "record is 1st in the league but its points scored this season rank 4th" in " ".join(facts)
    assert "It has lost 2 straight." in facts


def test_nobody_short_of_anything_means_no_fraud():
    a = _team("A", "0-0", 110.0, _even(25.0, 27.5))
    b = _team("B", "0-0", 105.0, _even(25.0, 26.25))
    assert find_fraud([_game(a, b)]) == (None, [])
    assert find_fraud([]) == (None, [])
