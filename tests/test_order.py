"""Which game leads the paper (1 Oct)."""
from storylines import order_games, game_drama


def team(name, pts, w=0, l=0, gap=0.0, proj=None):
    starters = [{"projected": proj, "actual": pts}] if proj is not None else []
    return {"team_name": name, "owner_name": name.lower(), "points": pts,
            "wins": w, "losses": l, "lineup_gap": gap, "all_starters": starters}


def game(a, b):
    w, l = (a, b) if a["points"] >= b["points"] else (b, a)
    return {"team_1": a, "team_2": b, "winner": w["team_name"],
            "margin": round(w["points"] - l["points"], 2)}


DULL = game(team("A", 120), team("B", 108))
CLOSE = game(team("C", 115.2), team("D", 114.9))
MEH = game(team("E", 125), team("F", 111))


def test_a_close_game_leads_over_the_platform_order():
    out = order_games([DULL, MEH, CLOSE])
    assert out[0] is CLOSE


def test_ties_keep_the_platform_order():
    g1 = game(team("A", 100), team("B", 88))
    g2 = game(team("C", 101), team("D", 89))
    g3 = game(team("E", 102), team("F", 90))
    # g3 holds the week's high score, g1 the low; nothing else separates them
    out = order_games([g1, g2, g3])
    assert out[0] is g3 and out[1] is g1 and out[2] is g2


def test_an_upset_and_a_costly_bench_count():
    upset = game(team("Underdog", 110, w=1, l=5), team("Champ", 104, w=6, l=0, gap=20))
    assert game_drama(upset, [upset, DULL]) > game_drama(DULL, [upset, DULL])


def test_last_weeks_lead_team_does_not_lead_again():
    out = order_games([DULL, MEH, CLOSE], last_lead=["C", "X"])
    assert out[0] is not CLOSE
    assert CLOSE in out and len(out) == 3


def test_unless_every_game_has_one_of_them():
    out = order_games([CLOSE, DULL], last_lead=["C", "A"])
    assert out[0] is CLOSE


def test_one_game_or_none():
    assert order_games([]) == []
    assert order_games([DULL]) == [DULL]


def _with(g, *names):
    """Give a game's first team starters with these names."""
    g["team_1"]["all_starters"] = [{"name": n, "actual": 10} for n in names]
    return g


def test_a_photo_desk_player_gets_a_photo_slot():
    """John, 6 Oct: one of the two photo stories is always a desk player's."""
    g1 = game(team("A", 115.2), team("B", 114.9))     # closest: leads
    g2 = game(team("C", 120.0), team("D", 116.0))     # second best
    g3 = game(team("E", 140.0), team("F", 100.0))
    g4 = game(team("G", 130.0), team("H", 95.0))
    _with(g4, "Bijan Robinson")
    out = order_games([g1, g2, g3, g4],
                      photo=lambda g: any(p.get("name") == "Bijan Robinson"
                                          for p in g["team_1"].get("all_starters") or []))
    assert out[0] is g1 and out[1] is g4


def test_no_reshuffle_when_a_photo_story_is_already_on_top():
    g1 = game(team("A", 115.2), team("B", 114.9))
    g2 = game(team("C", 120.0), team("D", 116.0))
    g3 = game(team("E", 140.0), team("F", 100.0))
    _with(g1, "Bijan Robinson")
    _with(g3, "Bijan Robinson")
    before = order_games([g1, g2, g3])
    after = order_games([g1, g2, g3], photo=lambda g: g is g1 or g is g3)
    assert after == before
