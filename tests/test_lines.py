"""Next week's lines price the best lineup a roster can field (9 Nov)."""

import history
from providers.models import Manager, PlayerLine, Team

SLOTS = ["QB", "RB", "WR", "FLEX", "BN", "BN", "BN"]


def _p(pid, pos, proj, slot="BN", status=None):
    return PlayerLine(player_id=f"sleeper:{pid}", name=pid, position=pos, points=0.0,
                      slot=slot, positions=(pos,), projected=proj, injury_status=status)


def _team(lineup, bench):
    return Team(team_id="1", team_name="T", manager=Manager(manager_id="m", display_name="M"),
                lineup=lineup, bench=bench)


def test_a_starter_on_bye_is_replaced_from_the_bench():
    # Set lineup: the QB is on bye (no projection) and the WR is ruled out.
    team = _team(
        [_p("qb1", "QB", None, "QB"), _p("rb1", "RB", 15, "RB"),
         _p("wr1", "WR", 18, "WR", status="Out"), _p("rb2", "RB", 10, "FLEX")],
        [_p("qb2", "QB", 17), _p("wr2", "WR", 12), _p("wr3", "WR", 11)],
    )
    # Best on projections: qb2 17 + rb1 15 + wr2 12 + FLEX wr3 11 (beats rb2 10) = 55.
    assert history.projected_total(team, SLOTS) == 55.0
    # The old way summed the set lineup: 0 + 15 + 18 + 10 = 43.
    assert history.projected_total(team) == 43.0


def test_ir_and_taxi_players_are_never_started():
    team = _team([_p("qb1", "QB", 20, "QB"), _p("rb1", "RB", 10, "RB"), _p("wr1", "WR", 10, "WR"),
                  _p("rb2", "RB", 5, "FLEX")],
                 [_p("rb9", "RB", 30, "IR"), _p("wr9", "WR", 30, "TAXI")])
    assert history.projected_total(team, SLOTS) == 45.0


def test_too_few_projections_means_no_line():
    team = _team([_p("qb1", "QB", None, "QB")], [_p("rb1", "RB", None)])
    assert history.projected_total(team, SLOTS) is None
