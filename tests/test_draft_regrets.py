"""Draft regrets (20 Oct 2026): the draft re-graded with the season so far."""

from providers.models import League, Manager, Matchup, PlayerLine, Team, WeekData
from web import draft_regrets as dr


def _league():
    return League(provider="sleeper", league_id="L", name="Test", season=2026)


def _week(week, rosters):
    """rosters: {team_id: [(pid, name, pos, points, status), ...]}"""
    teams = []
    for tid, players in rosters.items():
        lineup = [PlayerLine(player_id=f"sleeper:{pid}", name=name, position=pos,
                             points=pts, slot=pos, injury_status=status)
                  for pid, name, pos, pts, status in players]
        teams.append(Team(team_id=tid, team_name=f"Team {tid}",
                          manager=Manager(tid, f"m{tid}"), points=0.0, lineup=lineup))
    ms = [Matchup(matchup_id=str(i), teams=(teams[i], teams[i + 1]))
          for i in range(0, len(teams) - 1, 2)]
    return WeekData(league=_league(), week=week, matchups=ms)


# Two teams, a 10-round draft of RBs and WRs (20 picks). Team 1's last
# pick took off; team 2's first-rounder flopped and was cut after week 2.
PICKS = {}
DRAFT = [  # (pid, team, pos)  in pick order
    ("r1", "1", "RB"), ("r2", "2", "RB"), ("w1", "2", "WR"), ("w2", "1", "WR"),
    ("r3", "1", "RB"), ("r4", "2", "RB"), ("w3", "2", "WR"), ("w4", "1", "WR"),
    ("r5", "1", "RB"), ("r6", "2", "RB"), ("w5", "2", "WR"),
    # Rounds 6-10: eight forgettable receivers, then w6 with the last pick.
    ("w7", "1", "WR"), ("w8", "2", "WR"), ("w9", "1", "WR"), ("w10", "2", "WR"),
    ("w11", "1", "WR"), ("w12", "2", "WR"), ("w13", "1", "WR"), ("w14", "2", "WR"),
    ("w6", "1", "WR"),
]
for i, (pid, tid, pos) in enumerate(DRAFT, 1):
    PICKS[pid] = {"round": (i + 1) // 2, "overall": i, "team_id": tid}

# Season points per week. r2 (pick 2, the RB2) is terrible, then cut.
# w6 (pick 12, round 6, the WR6) is the best WR by miles.
WEEKLY = {"r1": 15, "r2": 1, "w1": 14, "w2": 12, "r3": 13, "r4": 12,
          "w3": 10, "w4": 9, "r5": 11, "r6": 10, "w5": 8, "w6": 25,
          "w7": 6, "w8": 5.5, "w9": 5, "w10": 4.5, "w11": 4, "w12": 3.5,
          "w13": 3, "w14": 2.5}
# Spread the RB/WR fields out so a move of 8 places is possible: fillers on
# each roster that outscore r2 and rank above him.
FILLER = [(f"f{i}", f"Filler {i}", "RB", 5.0 + i / 10, None) for i in range(10)]


def _season(weeks=5, cut_after=2, r2_status=None):
    out = []
    for w in range(1, weeks + 1):
        rosters = {"1": [], "2": []}
        for pid, tid, pos in DRAFT:
            if pid == "r2" and w > cut_after:
                continue
            status = r2_status if pid == "r2" else None
            rosters[tid].append((pid, pid.upper(), pos, float(WEEKLY[pid]), status))
        rosters["1"] += FILLER[:5]
        rosters["2"] += FILLER[5:]
        out.append(_week(w, rosters))
    return out


def test_too_early_or_no_draft_is_nothing():
    assert dr.build(_season(weeks=3), PICKS, 3) is None
    assert dr.build(_season(), {}, 5) is None


def test_the_late_pick_who_took_off_is_the_steal():
    out = dr.build(_season(), PICKS, 5)
    steal = out["steals"][0]
    assert steal["player"] == "W6"
    assert (steal["round"], steal["drafted_as"], steal["now"]) == (10, "WR14", "WR1")
    assert steal["team"] == "Team 1"


def test_the_first_rounder_who_was_cut_is_the_regret():
    out = dr.build(_season(), PICKS, 5)
    regret = out["regrets"][0]
    assert regret["player"] == "R2"
    assert regret["drafted_as"] == "RB2"
    assert regret["where"] == "cut"
    assert regret["team"] == "Team 2"


def test_an_injured_player_is_not_called_cut():
    out = dr.build(_season(r2_status="IR"), PICKS, 5)
    regret = next(r for r in out["regrets"] if r["player"] == "R2")
    assert regret["where"] == "injured"
    assert regret["injured"] is True
    assert "blame luck" in dr.writer_facts(out)


def test_a_traded_player_says_where_he_went():
    weeks = _season()
    # Week 5: w6 moves from team 1 to team 2.
    wk5 = weeks[-1]
    t1, t2 = wk5.matchups[0].teams
    w6 = next(p for p in t1.lineup if p.name == "W6")
    t1.lineup.remove(w6); t2.lineup.append(w6)
    steal = dr.build(weeks, PICKS, 5)["steals"][0]
    assert steal["where"] == "now on Team 2"
    assert steal["team"] == "Team 1", "credit goes to whoever drafted him"


def test_writer_gets_short_background_lines():
    facts = dr.writer_facts(dr.build(_season(), PICKS, 5))
    assert facts.startswith("THE DRAFT, IN HINDSIGHT")
    assert "W6 in round 10" in facts and "has been cut" in facts
    assert dr.writer_facts(None) == ""


# --- in the paper -------------------------------------------------------------

def _paper(ai_extra):
    from tests.test_web import SAMPLE_AI, _full_paper
    return _full_paper({**SAMPLE_AI, **ai_extra})


def test_no_regrets_no_section():
    html = _paper({})
    assert 'id="draft"' not in html
    assert 'href="#draft"' not in html


def test_the_section_prints_with_its_nav_link():
    out = dr.build(_season(), PICKS, 5)
    html = _paper({"draft_regrets": out})
    assert 'id="draft"' in html and 'href="#draft"' in html
    assert "Draft Regrets" in html and "re-graded" not in html
    assert "WR14" in html and "WR1" in html
    assert "(cut)" in html and "dr-tag" not in html


def test_names_are_escaped():
    out = dr.build(_season(), PICKS, 5)
    out["regrets"][0]["team"] = "<script>x</script>"
    out["steals"][0]["player"] = "<b>bold</b>"
    html = _paper({"draft_regrets": out})
    assert "<script>x</script>" not in html and "<b>bold</b>" not in html
