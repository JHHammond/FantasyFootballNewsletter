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
    bench_blunder = max(all_teams, key=lambda t: t["lineup_gap"])

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
        "bench_blunder": bench_blunder,
        "empty_teams": empty_teams,
        "upset": upset,
        "fraud": fraud,
        "dominance": dominance,
        "jerry_jones": jerry_jones,
    }