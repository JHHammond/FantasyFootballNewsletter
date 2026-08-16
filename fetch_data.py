import requests

BASE_URL = "https://api.sleeper.app/v1"


def get_league(league_id):
    url = f"{BASE_URL}/league/{league_id}"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def get_users(league_id):
    url = f"{BASE_URL}/league/{league_id}/users"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def get_rosters(league_id):
    url = f"{BASE_URL}/league/{league_id}/rosters"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def get_matchups(league_id, week):
    url = f"{BASE_URL}/league/{league_id}/matchups/{week}"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def get_players():
    url = f"{BASE_URL}/players/nfl"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def build_avatar_url(avatar_id):
    if not avatar_id:
        return None
    return f"https://sleepercdn.com/avatars/{avatar_id}"


def build_user_map(users):
    user_map = {}

    for user in users:
        user_id = user["user_id"]
        user_map[user_id] = {
            "display_name": user.get("display_name", "Unknown"),
            "avatar_url": build_avatar_url(user.get("avatar")),
        }

    return user_map


def build_roster_map(rosters, users, current_week=None):
    user_map = build_user_map(users)
    roster_map = {}

    for roster in rosters:
        roster_id = roster["roster_id"]
        owner_id = roster.get("owner_id")

        owner_info = user_map.get(owner_id, {})
        owner_name = owner_info.get("display_name", "Unknown")
        avatar_url = owner_info.get("avatar_url")

        team_name = owner_name
        metadata = roster.get("metadata") or {}
        if metadata.get("team_name"):
            team_name = metadata["team_name"]

        wins = roster.get("settings", {}).get("wins", 0)
        losses = roster.get("settings", {}).get("losses", 0)

        # If current_week is provided, sanity-check the record.
        # If wins+losses > current_week, the data is from a prior season — reset to 0-0.
        if current_week is not None:
            if (wins + losses) > current_week:
                wins = 0
                losses = 0

        roster_map[roster_id] = {
            "team_name": team_name,
            "owner_name": owner_name,
            "avatar_url": avatar_url,
            "wins": wins,
            "losses": losses,
        }

    return roster_map


def pair_matchups(matchups, roster_map):
    grouped = {}

    for matchup in matchups:
        matchup_id = matchup.get("matchup_id")
        grouped.setdefault(matchup_id, []).append(matchup)

    games = []

    for matchup_id, teams in grouped.items():
        if len(teams) != 2:
            continue

        team_1_raw, team_2_raw = teams

        roster_1 = roster_map[team_1_raw["roster_id"]]
        roster_2 = roster_map[team_2_raw["roster_id"]]

        score_1 = team_1_raw["points"]
        score_2 = team_2_raw["points"]

        if score_1 > score_2:
            winner = roster_1["team_name"]
            margin = round(score_1 - score_2, 2)
        elif score_2 > score_1:
            winner = roster_2["team_name"]
            margin = round(score_2 - score_1, 2)
        else:
            winner = "Tie"
            margin = 0

        empty_slots_1 = sum(1 for player in team_1_raw.get("starters", []) if player is None)
        empty_slots_2 = sum(1 for player in team_2_raw.get("starters", []) if player is None)

        # Use current season record from roster_map (wins/losses this season)
        # If both are 0 (week 1 before any games counted), derive from this week's result
        wins_1 = roster_1["wins"]
        losses_1 = roster_1["losses"]
        wins_2 = roster_2["wins"]
        losses_2 = roster_2["losses"]

        # If records are identical and suspiciously high, they may be from last season
        # Fall back to showing just this week's result
        total_games_1 = wins_1 + losses_1
        total_games_2 = wins_2 + losses_2

        games.append({
            "team_1": {
                "team_name": roster_1["team_name"],
                "owner_name": roster_1["owner_name"],
                "avatar_url": roster_1["avatar_url"],
                "points": score_1,
                "record": f"{wins_1}-{losses_1}",
                "wins": wins_1,
                "losses": losses_1,
                "empty_slots": empty_slots_1,
                "starters": team_1_raw.get("starters", []),
                "players": team_1_raw.get("players", []),
                "players_points": team_1_raw.get("players_points", {}),
            },
            "team_2": {
                "team_name": roster_2["team_name"],
                "owner_name": roster_2["owner_name"],
                "avatar_url": roster_2["avatar_url"],
                "points": score_2,
                "record": f"{wins_2}-{losses_2}",
                "wins": wins_2,
                "losses": losses_2,
                "empty_slots": empty_slots_2,
                "starters": team_2_raw.get("starters", []),
                "players": team_2_raw.get("players", []),
                "players_points": team_2_raw.get("players_points", {}),
            },
            "winner": winner,
            "margin": margin,
        })

    return games

def get_projections(season, week, season_type="regular"):
    url = f"https://api.sleeper.app/projections/nfl/{season}/{week}"
    params = {
        "season_type": season_type,
        "position[]": ["QB", "RB", "WR", "TE", "K", "DEF", "FLEX"],
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()

    # Normalize to dict keyed by player_id regardless of what Sleeper returns
    if isinstance(data, list):
        result = {}
        for item in data:
            # player_id can be at top level or inside stats
            pid = (
                item.get("player_id")
                or item.get("stats", {}).get("player_id")
            )
            if pid:
                # Merge stats into top level for easy access
                merged = {**item.get("stats", {}), **{k: v for k, v in item.items() if k != "stats"}}
                result[str(pid)] = merged
        return result
    return data


def get_player_stats(season, week, season_type="regular"):
    """Fetch actual stats for all players for a given week."""
    url = f"https://api.sleeper.app/stats/nfl/{season}/{week}"
    params = {"season_type": season_type}
    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json()


def get_player_name(player_id, players_data):
    """Get a player's display name from the players database."""
    player = players_data.get(str(player_id), {})
    first = player.get("first_name", "")
    last = player.get("last_name", "")
    positions = player.get("fantasy_positions", [])
    position = positions[0] if positions else "?"

    if first and last:
        return f"{first} {last}", position
    elif last:
        return last, position
    else:
        return str(player_id), position


def enrich_team_with_player_stats(team, players_data, projections, scoring_type="pts_ppr"):
    """
    Add top and bottom performer data to a team dict.
    Uses actual scores from players_points and projected scores from projections.
    Only looks at starters (not bench).
    """
    starters = team.get("starters", [])
    players_points = team.get("players_points", {})

    performers = []

    for player_id in starters:
        if not player_id or player_id == "0":
            continue

        actual = players_points.get(str(player_id), 0) or 0
        actual = float(actual)

        # Get projection
        player_proj_data = projections.get(str(player_id), {})
        projected = None
        if player_proj_data:
            projected = (
                player_proj_data.get(scoring_type)
                or player_proj_data.get("pts_ppr")
                or player_proj_data.get("pts_half_ppr")
                or player_proj_data.get("pts_std")
            )

        projected = float(projected) if projected is not None else None

        name, position = get_player_name(player_id, players_data)

        performers.append({
            "player_id": str(player_id),
            "name": name,
            "position": position,
            "actual": round(actual, 2),
            "projected": round(projected, 2) if projected is not None else None,
            "beat_projection_by": round(actual - projected, 2) if projected is not None else None,
        })

    if not performers:
        team["top_performer"] = None
        team["bottom_performer"] = None
        return team

    # Sort by actual score
    sorted_by_actual = sorted(performers, key=lambda p: p["actual"], reverse=True)

    team["top_performer"] = sorted_by_actual[0] if sorted_by_actual else None
    team["bottom_performer"] = sorted_by_actual[-1] if len(sorted_by_actual) > 1 else None
    team["all_starters"] = performers

    return team


def enrich_games_with_player_stats(games, players_data, projections):
    """Add top/bottom performer data to every team in every game."""
    for game in games:
        for team_key in ["team_1", "team_2"]:
            game[team_key] = enrich_team_with_player_stats(
                game[team_key], players_data, projections
            )
    return games