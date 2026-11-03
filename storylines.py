def get_weekly_storylines(games):
    if not games:
        return {}

    closest_game = min(games, key=lambda g: g["margin"])
    biggest_blowout = max(games, key=lambda g: g["margin"])

    all_teams = []
    for game in games:
        all_teams.append(game["team_1"])
        all_teams.append(game["team_2"])

    highest_score = max(all_teams, key=lambda t: t["points"])
    lowest_score = min(all_teams, key=lambda t: t["points"])

    # --- Bench blunder ---
    #
    # AMONG THE TEAMS THAT LOST. A manager who left thirty points on the bench
    # and won by forty has not blundered — the points were surplus, and an
    # award for it reads as the paper inventing a grievance. The award is for
    # the manager whose bench cost them the game.
    #
    # Falls back to the whole league only if nobody lost, which means every
    # game was a tie and the award is meaningless anyway.
    losers = []
    for game in games:
        t1, t2 = game["team_1"], game["team_2"]
        if game["winner"] == t1["team_name"]:
            losers.append(t2)
        elif game["winner"] == t2["team_name"]:
            losers.append(t1)

    # THE AWARD IS ABOUT ONE PLAYER, NOT A TOTAL.
    #
    # It used to go to the biggest lineup GAP, which is an optimizer's number:
    # the sum of everything a perfect lineup would have gained. That is a real
    # measurement and it is not what the award is. Nobody in a league says "he
    # left 31.4 aggregate points on his bench" — they say "he benched Bijan".
    # A manager can top the gap table with four mildly wrong calls while
    # somebody else sat a 38-point running back, and the second one is the
    # story every single time.
    #
    # So: the highest-scoring individual bench player, among the teams that
    # lost.
    benched = []
    for team in (losers or all_teams):
        for player in team.get("all_bench") or []:
            if player and isinstance(player.get("actual"), (int, float)):
                benched.append((player, team))

    if benched:
        bench_blunder_player, bench_blunder = max(
            benched, key=lambda pair: pair[0]["actual"])
    else:
        # No bench data at all — some providers do not supply one. Fall back
        # to the gap so the award still has a recipient.
        bench_blunder = max(losers or all_teams, key=lambda t: t["lineup_gap"])
        bench_blunder_player = None

    # --- Best loser (the Joe Burrow award) ---
    #
    # "Did everything right and still lost" — the highest score that lost. It
    # used to go to the week's LOWEST score, which is the opposite person: the
    # manager who did everything wrong. Kept with the game it lost, because
    # the joke is who beat them and by how little.
    best_loser, best_loser_game = None, None
    for game in games:
        t1, t2 = game["team_1"], game["team_2"]
        if game["winner"] == t1["team_name"]:
            loser = t2
        elif game["winner"] == t2["team_name"]:
            loser = t1
        else:
            continue
        if best_loser is None or loser["points"] > best_loser["points"]:
            best_loser, best_loser_game = loser, game

    # --- The four standing awards (John, 23 Sep) -----------------------------
    #
    # TONY SNELL WINDSPRINT: the starter who did nothing at all — named for
    # the night Snell played twenty minutes and recorded no stats. The starter
    # closest to zero, so a true 0.0 always wins; ties go to whoever was
    # projected for more, because that is the funnier nothing.
    #
    # KYLE PITTS: the manager who started the player who missed his
    # projection by the most. Every year we think it's his year.
    #
    # NICK FOLES: the best bench performance anywhere in the league — the
    # backup who could have won it all.
    # Kickers and defenses are not award material (John, 25 Sep): a defense at
    # zero is an ordinary Sunday, and it used to win the Tony Snell nearly
    # every week. They are only eligible if there is nobody else at all.
    starters, special = [], []
    for team in all_teams:
        for p in team.get("all_starters") or []:
            if isinstance(p.get("actual"), (int, float)):
                pos = (p.get("position") or "").upper()
                (special if pos in ("K", "DEF", "DST", "D/ST") else starters).append((p, team))
    starters = starters or special

    tony_snell = None
    if starters:
        p, team = min(starters, key=lambda pt: (abs(pt[0]["actual"]),
                                                -(pt[0].get("projected") or 0)))
        tony_snell = {"player": p, "team": team}

    kyle_pitts = None
    # Not the Tony Snell winner again (28 Sep: Barkley won both with one
    # zero, and the awards read as the same paragraph twice).
    snell_player = tony_snell["player"] if tony_snell else None
    missed_all = [(p, t) for p, t in starters
                  if isinstance(p.get("beat_projection_by"), (int, float))]
    # ...unless he is the only one who missed at all.
    missed = [(p, t) for p, t in missed_all
              if p is not snell_player and p["beat_projection_by"] < 0] or missed_all
    if missed:
        p, team = min(missed, key=lambda pt: pt[0]["beat_projection_by"])
        kyle_pitts = {"player": p, "team": team}

    nick_foles = None
    bench_all = [(p, t) for t in all_teams for p in (t.get("all_bench") or [])
                 if isinstance(p.get("actual"), (int, float))]
    if bench_all:
        p, team = max(bench_all, key=lambda pt: pt[0]["actual"])
        nick_foles = {"player": p, "team": team}

    # OVER OF THE WEEK (1 Oct): the starter who beat his projection by the
    # most. Kickers and defenses out, as for the Snell. Nobody beat his
    # projection at all: no award.
    over_of_week = None
    beat = [(p, t) for p, t in starters
            if isinstance(p.get("beat_projection_by"), (int, float)) and p["beat_projection_by"] > 0]
    if beat:
        p, team = max(beat, key=lambda pt: (pt[0]["beat_projection_by"], pt[0].get("actual") or 0))
        over_of_week = {"player": p, "team": team}

    # --- Empty lineup ---
    empty_teams = [t for t in all_teams if t["empty_slots"] > 0]

    # --- Upset ---
    upset = None
    biggest_upset_margin = -1

    for game in games:
        t1 = game["team_1"]
        t2 = game["team_2"]

        wins_1 = int(t1["record"].split("-")[0])
        wins_2 = int(t2["record"].split("-")[0])

        if game["winner"] == t1["team_name"] and wins_1 < wins_2:
            if game["margin"] > biggest_upset_margin:
                upset = game
                biggest_upset_margin = game["margin"]
        elif game["winner"] == t2["team_name"] and wins_2 < wins_1:
            if game["margin"] > biggest_upset_margin:
                upset = game
                biggest_upset_margin = game["margin"]

    # --- Fraud ---
    fraud, fraud_facts = find_fraud(games)

    # --- Dominance ---
    dominance = None
    best_combo_score = -1

    for game in games:
        if game["winner"] == game["team_1"]["team_name"]:
            winner_team = game["team_1"]
        elif game["winner"] == game["team_2"]["team_name"]:
            winner_team = game["team_2"]
        else:
            continue

        combo_score = winner_team["points"] + game["margin"]

        if combo_score > best_combo_score:
            dominance = {
                "team": winner_team,
                "margin": game["margin"]
            }
            best_combo_score = combo_score

    # --- Jerry Jones Award ---
    # Goes to the loser with the highest lineup gap —
    # the manager who had the talent, made terrible decisions, and still lost.
    jerry_jones = None
    worst_score = -1

    for game in games:
        t1 = game["team_1"]
        t2 = game["team_2"]
        winner = game["winner"]

        # Find the loser
        loser = t2 if winner == t1["team_name"] else t1
        loser_gap = float(loser.get("lineup_gap", 0))

        # Score = lineup gap + penalty for low score
        # Higher gap and lower score = worse manager
        loser_score = float(loser.get("points", 0))
        jerry_score = loser_gap + max(0, 120 - loser_score) * 0.5

        if jerry_score > worst_score:
            jerry_jones = loser
            worst_score = jerry_score

    return {
        "closest_game": closest_game,
        "biggest_blowout": biggest_blowout,
        "highest_score": highest_score,
        "lowest_score": lowest_score,
        "best_loser": best_loser,
        "best_loser_game": best_loser_game,
        "bench_blunder": bench_blunder,
        "bench_blunder_player": bench_blunder_player,
        "empty_teams": empty_teams,
        "upset": upset,
        "fraud": fraud,
        "fraud_facts": fraud_facts,
        "dominance": dominance,
        "jerry_jones": jerry_jones,
        "tony_snell": tony_snell,
        "kyle_pitts": kyle_pitts,
        "nick_foles": nick_foles,
        "over_of_week": over_of_week,
    }

# --- Which game leads (1 Oct) -------------------------------------------------
#
# The paper used to print the games in whatever order the platform listed them
# (on Sleeper, its matchup number), so the lead story, the pull quote and the
# feature box went to whichever game happened to be matchup 1. John: "how is
# what story goes where decided?" — it wasn't. Now each game gets points for
# the things that make a game worth reading about, and the best one leads.

def _proj(team):
    total = 0.0
    for p in team.get("all_starters") or []:
        if isinstance(p.get("projected"), (int, float)):
            total += p["projected"]
    return total


def game_drama(game, games, rivalries=(), commissioner=""):
    """How much of a story this game is. Arithmetic only, no model call."""
    t1, t2 = game.get("team_1") or {}, game.get("team_2") or {}
    margin = float(game.get("margin") or 0)
    s = 0.0

    # Close games are the best stories; a tie is the closest of all.
    if game.get("winner") == "Tie":
        s += 6
    elif margin < 3:
        s += 6
    elif margin < 6:
        s += 4
    elif margin < 10:
        s += 1.5

    margins = [float(g.get("margin") or 0) for g in games]
    if margin == max(margins) and margin >= 30:
        s += 3                      # the week's blowout
    scores = [float(t.get("points") or 0) for g in games
              for t in (g.get("team_1") or {}, g.get("team_2") or {})]
    mine = (float(t1.get("points") or 0), float(t2.get("points") or 0))
    if scores and max(scores) in mine:
        s += 3                      # the week's high score
    if scores and min(scores) in mine:
        s += 2.5                    # the week's low score

    if game.get("winner") != "Tie":
        if game.get("winner") == t1.get("team_name"):
            w, l = t1, t2
        else:
            w, l = t2, t1
        # An upset by record (records as they stood at kickoff).
        edge = ((l.get("wins") or 0) - (l.get("losses") or 0)) - \
               ((w.get("wins") or 0) - (w.get("losses") or 0))
        if edge >= 4:
            s += 3
        elif edge >= 2:
            s += 1.5
        # An upset by projection.
        pw, pl = _proj(w), _proj(l)
        if pw and pl and pl - pw >= 8:
            s += 2
        # The bench cost the loser the game.
        gap = l.get("lineup_gap")
        if isinstance(gap, (int, float)) and gap > margin and gap >= 10:
            s += 3

    names = {t1.get("team_name"), t2.get("team_name")}
    if frozenset(names) in set(rivalries or ()):
        s += 1.5                    # these two have history
    if commissioner and commissioner in (
            names | {t1.get("owner_name"), t2.get("owner_name")}):
        s += 1                      # the commissioner played
    return s


def order_games(games, last_lead=(), rivalries=(), commissioner="", photo=None):
    """The games, best story first. Ties keep the platform's order.

    One rule on top of the points: a team that led last week's paper does not
    lead this one, unless every game involves one of last week's two teams.

    And one for the photo desk (John, 6 Oct: "the two stories that get photos
    always have one that I've selected"). Only the first two stories carry a
    photo. `photo(game)` says whether a game has a starter the desk has a
    picture of; if neither of the first two does and a later game does, the
    best of those becomes the second story. The lead stays the best story.
    """
    games = list(games or [])
    if len(games) < 2:
        return games
    ranked = [g for _, _, g in sorted(
        ((-game_drama(g, games, rivalries, commissioner), i, g)
         for i, g in enumerate(games)), key=lambda x: (x[0], x[1]))]
    last = {n for n in (last_lead or ()) if n}
    if last:
        def led_last_week(g):
            return bool({(g.get("team_1") or {}).get("team_name"),
                         (g.get("team_2") or {}).get("team_name")} & last)
        if led_last_week(ranked[0]):
            fresh = next((g for g in ranked if not led_last_week(g)), None)
            if fresh is not None:
                ranked.remove(fresh)
                ranked.insert(0, fresh)
    if photo and len(ranked) > 2 and not (photo(ranked[0]) or photo(ranked[1])):
        pick = next((g for g in ranked[2:] if photo(g)), None)
        if pick is not None:
            ranked.remove(pick)
            ranked.insert(1, pick)
    return ranked


# --- Fraud Watch (2 Nov) --------------------------------------------------------
#
# It used to be the lowest scorer among teams with eight wins, and before week
# nine (nobody has eight) simply the week's lowest score. From week nine that
# picks a good team having an ordinary week; before it, a bad team being bad.
# Neither is a fraud. A fraud is a team that fell short of what was expected
# of it: the projection it missed, the game it was supposed to win, the
# standing it had, the record its scoring never earned.

def _wins(record) -> int:
    try:
        return int(str(record or "0").split("-")[0])
    except ValueError:
        return 0


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _projected(team) -> float | None:
    proj = [p.get("projected") for p in team.get("all_starters") or []
            if isinstance(p.get("projected"), (int, float))]
    if len(proj) < max(1, len(team.get("all_starters") or []) // 2):
        return None
    return round(sum(proj), 1)


def find_fraud(games, season=None):
    """The team that fell furthest short of expectations this week, and the
    case against it as plain sentences for the writer. (None, []) if nobody
    fell short at all.

    `season`, when given, is {team name: {"record_rank", "pf_rank",
    "streak"}} after this week, from the season history: a record that
    outruns a team's scoring is the oldest fraud there is.

    Expectations, each worth points toward the verdict:
    - the projection: points short of its starters' projections;
    - the line: lost a game it was projected to win (+15);
    - the standing: how high it stood coming in (+2 a place above last), so
      a contender laying an egg outranks a last-place team doing its usual;
    - the record: places its record sits above its scoring rank (+3 each);
    - the result: lost at all (+5).
    """
    sides = []
    for g in games or []:
        t1, t2 = g.get("team_1") or {}, g.get("team_2") or {}
        if t1.get("team_name") and t2.get("team_name"):
            sides += [(t1, t2, g), (t2, t1, g)]
    if not sides:
        return None, []
    n = len(sides)
    wins_in = {t["team_name"]: _wins(t.get("record")) for t, _, _ in sides}
    started = any(wins_in.values())
    season = season or {}

    best, best_score = None, 0.0
    for team, opp, g in sides:
        name, opp_name = team["team_name"], opp["team_name"]
        pts, opp_pts = float(team.get("points") or 0), float(opp.get("points") or 0)
        lost = g.get("winner") == opp_name
        proj, opp_proj = _projected(team), _projected(opp)
        miss = (proj - pts) if proj is not None else 0.0
        lost_fav = bool(lost and proj is not None and opp_proj is not None and proj > opp_proj)
        rank_in = 1 + sum(1 for w in wins_in.values() if w > wins_in[name])
        standing = (n - rank_in) if started else 0
        s = season.get(name) or {}
        luck = max(0, (s.get("pf_rank") or 0) - (s.get("record_rank") or 0)) if s else 0
        if miss < 5 and not lost_fav:
            continue
        score = miss + 15 * lost_fav + 2 * standing + 3 * luck + 5 * lost
        if score > best_score:
            best_score = score
            best = (team, opp, g, pts, opp_pts, lost, proj, opp_proj, miss, lost_fav,
                    rank_in, s)
    if not best:
        return None, []

    team, opp, g, pts, opp_pts, lost, proj, opp_proj, miss, lost_fav, rank_in, s = best
    name, opp_name = team["team_name"], opp["team_name"]
    after = team.get("record_after") or team.get("record") or ""
    facts = []
    if started:
        facts.append(f"{name} came in {team.get('record')}, {_ordinal(rank_in)} of {n} "
                     f"in the standings, and is now {after}.")
    elif after:
        facts.append(f"{name} is now {after}.")
    if proj is not None:
        facts.append(f"Its starters were projected for {proj:.1f}. It scored {pts:.1f}, "
                     f"{miss:.1f} short.")
    if lost_fav:
        facts.append(f"It was projected to beat {opp_name} ({proj:.1f} to {opp_proj:.1f}) "
                     f"and lost {opp_pts:.1f}-{pts:.1f}.")
    elif lost:
        facts.append(f"It lost to {opp_name} {opp_pts:.1f}-{pts:.1f}.")
    else:
        facts.append(f"It still beat {opp_name} {pts:.1f}-{opp_pts:.1f}, which flatters it.")
    if s.get("pf_rank") and s.get("record_rank") and s["pf_rank"] > s["record_rank"]:
        facts.append(f"Its record is {_ordinal(s['record_rank'])} in the league but its "
                     f"points scored this season rank {_ordinal(s['pf_rank'])}.")
    if s.get("streak"):
        facts.append(str(s["streak"]))
    misses = sorted(
        (p for p in team.get("all_starters") or []
         if isinstance(p.get("projected"), (int, float)) and isinstance(p.get("actual"), (int, float))
         and p["projected"] - p["actual"] >= 4),
        key=lambda p: p["actual"] - p["projected"])[:3]
    for p in misses:
        facts.append(f"{p.get('name')} ({(p.get('position') or '').upper()}) scored "
                     f"{p['actual']:.1f} against a {p['projected']:.1f} projection.")
    gap = team.get("lineup_gap")
    if isinstance(gap, (int, float)) and gap >= 8:
        facts.append(f"It left {gap:.1f} points on its bench.")
    return team, facts
