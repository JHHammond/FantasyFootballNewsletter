def player_can_fill_slot(player_positions, slot_name):
    if not player_positions:
        return False

    if slot_name == "QB":
        return "QB" in player_positions
    if slot_name == "RB":
        return "RB" in player_positions
    if slot_name == "WR":
        return "WR" in player_positions
    if slot_name == "TE":
        return "TE" in player_positions
    if slot_name == "K":
        return "K" in player_positions
    if slot_name == "DEF":
        return "DEF" in player_positions

    if slot_name == "FLEX":
        return any(pos in player_positions for pos in ["RB", "WR", "TE"])

    if slot_name == "SUPER_FLEX":
        return any(pos in player_positions for pos in ["QB", "RB", "WR", "TE"])

    return False


def build_player_pool(team, players_data):
    pool = []

    for player_id in team.get("players", []):
        if player_id is None:
            continue

        player_meta = players_data.get(str(player_id), {})
        positions = player_meta.get("fantasy_positions", []) or []
        points = team.get("players_points", {}).get(player_id, 0)

        pool.append({
            "player_id": player_id,
            "positions": positions,
            "points": points,
        })

    return pool


def compute_optimal_lineup_score(team, roster_positions, players_data):
    """
    Greedy optimizer for legal lineup slots.
    Good enough for Build 1.
    """
    player_pool = build_player_pool(team, players_data)
    used_player_ids = set()
    optimal_score = 0

    for slot in roster_positions:
        if slot in ["BN", "IR", "TAXI"]:
            continue

        best_player = None

        for player in player_pool:
            if player["player_id"] in used_player_ids:
                continue

            if player_can_fill_slot(player["positions"], slot):
                if best_player is None or player["points"] > best_player["points"]:
                    best_player = player

        if best_player:
            optimal_score += best_player["points"]
            used_player_ids.add(best_player["player_id"])

    return round(optimal_score, 2)


def add_lineup_gap_to_games(games, roster_positions, players_data):
    for game in games:
        for team_key in ["team_1", "team_2"]:
            team = game[team_key]
            optimal_score = compute_optimal_lineup_score(team, roster_positions, players_data)
            actual_score = round(team["points"], 2)
            lineup_gap = round(optimal_score - actual_score, 2)

            team["optimal_score"] = optimal_score
            team["lineup_gap"] = max(lineup_gap, 0)

    return games