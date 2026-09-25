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
    missed = [(p, t) for p, t in starters
              if isinstance(p.get("beat_projection_by"), (int, float))]
    if missed:
        p, team = min(missed, key=lambda pt: pt[0]["beat_projection_by"])
        kyle_pitts = {"player": p, "team": team}

    nick_foles = None
    bench_all = [(p, t) for t in all_teams for p in (t.get("all_bench") or [])
                 if isinstance(p.get("actual"), (int, float))]
    if bench_all:
        p, team = max(bench_all, key=lambda pt: pt[0]["actual"])
        nick_foles = {"player": p, "team": team}

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
    fraud = None
    fraud_score = float("inf")

    for team in all_teams:
        wins = int(team["record"].split("-")[0])
        if wins >= 8 and team["points"] < fraud_score:
            fraud = team
            fraud_score = team["points"]

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
        "dominance": dominance,
        "jerry_jones": jerry_jones,
        "tony_snell": tony_snell,
        "kyle_pitts": kyle_pitts,
        "nick_foles": nick_foles,
    }