"""
Sleeper API fixtures, shaped exactly like the real responses.

Includes the ugly cases on purpose: a null in players_points, an empty starting
slot, a team defense, a roster whose stored wins/losses disagrees with actual
results, and a league that lists SUPER_FLEX before QB.
"""

LEAGUE = {
    "league_id": "TESTLEAGUE",
    "name": "Kevlarville",
    "season": "2025",
    "status": "in_season",
    "total_rosters": 4,
    "avatar": "abc123",
    "previous_league_id": "OLDLEAGUE",
    "roster_positions": [
        "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF",
        "BN", "BN", "BN", "BN",
    ],
    "scoring_settings": {"rec": 1.0, "pass_td": 4.0},
}

# Same league but with the flex listed FIRST -- the ordering that broke the old
# greedy optimizer, because FLEX would grab the best RB before the RB slots ran.
LEAGUE_FLEX_FIRST = {
    **LEAGUE,
    "roster_positions": ["FLEX", "SUPER_FLEX", "QB", "RB", "WR", "BN", "BN"],
}

USERS = [
    {"user_id": "u1", "display_name": "johnhenryhammond", "avatar": "av1", "is_owner": True},
    {"user_id": "u2", "display_name": "champayyy", "avatar": "av2"},
    {"user_id": "u3", "display_name": "HankStocke", "avatar": None},
    {"user_id": "u4", "display_name": "nickarrowood", "avatar": "av4"},
]

ROSTERS = [
    # Stored record says 2-0 even though it's week 3 and this team went 1-1.
    # The old code trusted this field; we recompute from matchup history.
    {"roster_id": 1, "owner_id": "u1", "settings": {"wins": 2, "losses": 0},
     "metadata": {"team_name": "The Commissioner"},
     "starters": [], "players": []},
    {"roster_id": 2, "owner_id": "u2", "settings": {"wins": 0, "losses": 2},
     "metadata": {}, "starters": [], "players": []},
    {"roster_id": 3, "owner_id": "u3", "settings": {"wins": 1, "losses": 1},
     "metadata": {"team_name": "Hank's Heroes"}, "starters": [], "players": []},
    {"roster_id": 4, "owner_id": "u4", "settings": {"wins": 1, "losses": 1},
     "metadata": {}, "starters": [], "players": []},
]

PLAYERS = {
    "1001": {"first_name": "Josh",    "last_name": "Allen",    "fantasy_positions": ["QB"], "team": "BUF"},
    "1002": {"first_name": "Lamar",   "last_name": "Jackson",  "fantasy_positions": ["QB"], "team": "BAL"},
    "2001": {"first_name": "Bijan",   "last_name": "Robinson", "fantasy_positions": ["RB"], "team": "ATL"},
    "2002": {"first_name": "Derrick", "last_name": "Henry",    "fantasy_positions": ["RB"], "team": "BAL"},
    "2003": {"first_name": "Chase",   "last_name": "Brown",    "fantasy_positions": ["RB"], "team": "CIN"},
    "3001": {"first_name": "CeeDee",  "last_name": "Lamb",     "fantasy_positions": ["WR"], "team": "DAL"},
    "3002": {"first_name": "Puka",    "last_name": "Nacua",    "fantasy_positions": ["WR"], "team": "LAR"},
    "3003": {"first_name": "Xavier",  "last_name": "Worthy",   "fantasy_positions": ["WR"], "team": "KC"},
    "4001": {"first_name": "Mark",    "last_name": "Andrews",  "fantasy_positions": ["TE"], "team": "BAL"},
    "4002": {"first_name": "T.J.",    "last_name": "Hockenson","fantasy_positions": ["TE"], "team": "MIN"},
    "5001": {"first_name": "Jason",   "last_name": "Myers",    "fantasy_positions": ["K"],  "team": "SEA"},
    "5002": {"first_name": "Chase",   "last_name": "McLaughlin","fantasy_positions": ["K"], "team": "TB"},
    "SEA":  {"fantasy_positions": ["DEF"], "team": "SEA", "last_name": "Seahawks"},
    "DET":  {"fantasy_positions": ["DEF"], "team": "DET", "last_name": "Lions"},
}

PROJECTIONS = {
    "1001": {"pts_ppr": 23.5}, "1002": {"pts_ppr": 21.0},
    "2001": {"pts_ppr": 18.0}, "2002": {"pts_ppr": 15.2}, "2003": {"pts_ppr": 11.0},
    "3001": {"pts_ppr": 17.5}, "3002": {"pts_ppr": 16.0}, "3003": {"pts_ppr": 14.4},
    "4001": {"pts_ppr": 10.5}, "4002": {"pts_ppr": 10.1},
    "5001": {"pts_ppr": 8.5},  "5002": {"pts_ppr": 9.0},
    "SEA":  {"pts_ppr": 7.0},  "DET":  {"pts_ppr": 6.6},
}

# --- Week 1: roster 1 beat 2, roster 3 beat 4 -----------------------------
WEEK_1 = [
    {"roster_id": 1, "matchup_id": 1, "points": 120.4,
     "starters": [], "players": [], "players_points": {}},
    {"roster_id": 2, "matchup_id": 1, "points": 107.8,
     "starters": [], "players": [], "players_points": {}},
    {"roster_id": 3, "matchup_id": 2, "points": 135.2,
     "starters": [], "players": [], "players_points": {}},
    {"roster_id": 4, "matchup_id": 2, "points": 122.3,
     "starters": [], "players": [], "players_points": {}},
]

# --- Week 2: roster 2 beat 1, roster 4 beat 3 -----------------------------
WEEK_2 = [
    {"roster_id": 1, "matchup_id": 1, "points": 99.0,
     "starters": [], "players": [], "players_points": {}},
    {"roster_id": 2, "matchup_id": 1, "points": 130.5,
     "starters": [], "players": [], "players_points": {}},
    {"roster_id": 3, "matchup_id": 2, "points": 88.1,
     "starters": [], "players": [], "players_points": {}},
    {"roster_id": 4, "matchup_id": 2, "points": 101.6,
     "starters": [], "players": [], "players_points": {}},
]

# --- Week 3: the week under test ------------------------------------------
# Roster 1: full lineup, a bench RB who outscored the starting one.
# Roster 2: a NULL in players_points and a zero-scoring defense (chug).
# Roster 3: an EMPTY starting slot.
# Roster 4: normal.
WEEK_3 = [
    {
        "roster_id": 1, "matchup_id": 1, "points": 118.5,
        "starters": ["1001", "2001", "2003", "3001", "3002", "4001", "3003", "5001", "SEA"],
        "starters_points": [28.5, 19.0, 6.5, 17.0, 14.0, 9.5, 12.0, 8.0, 4.0],
        "players": ["1001", "2001", "2003", "3001", "3002", "4001", "3003", "5001", "SEA", "2002"],
        "players_points": {
            "1001": 28.5, "2001": 19.0, "2003": 6.5, "3001": 17.0, "3002": 14.0,
            "4001": 9.5, "3003": 12.0, "5001": 8.0, "SEA": 4.0,
            "2002": 24.7,   # on the bench, outscored the starting RB2 by 18.2
        },
    },
    {
        "roster_id": 2, "matchup_id": 1, "points": 95.2,
        "starters": ["1002", "2002", "2003", "3003", "3002", "4002", "2001", "5002", "DET"],
        "starters_points": [22.0, 18.0, 5.0, 3.0, 11.0, 4.5, 16.7, 15.0, 0.0],
        "players": ["1002", "2002", "2003", "3003", "3002", "4002", "2001", "5002", "DET", "4001"],
        "players_points": {
            "1002": 22.0, "2002": 18.0, "2003": 5.0, "3003": 3.0, "3002": 11.0,
            "4002": 4.5, "2001": 16.7, "5002": 15.0, "DET": 0.0,
            "4001": None,   # Sleeper does this. The old code let it through as None.
        },
    },
    {
        "roster_id": 3, "matchup_id": 2, "points": 88.0,
        # Note the "0" -- an empty starting slot, which Sleeper marks this way.
        "starters": ["1001", "2001", "0", "3001", "3002", "4001", "3003", "5001", "SEA"],
        "starters_points": [20.0, 15.0, 0.0, 13.0, 10.0, 8.0, 12.0, 6.0, 4.0],
        "players": ["1001", "2001", "3001", "3002", "4001", "3003", "5001", "SEA"],
        "players_points": {
            "1001": 20.0, "2001": 15.0, "3001": 13.0, "3002": 10.0,
            "4001": 8.0, "3003": 12.0, "5001": 6.0, "SEA": 4.0,
        },
    },
    {
        "roster_id": 4, "matchup_id": 2, "points": 110.0,
        "starters": ["1002", "2002", "2003", "3001", "3003", "4002", "3002", "5002", "DET"],
        "starters_points": [25.0, 20.0, 9.0, 14.0, 11.0, 7.0, 13.0, 5.0, 6.0],
        "players": ["1002", "2002", "2003", "3001", "3003", "4002", "3002", "5002", "DET"],
        "players_points": {
            "1002": 25.0, "2002": 20.0, "2003": 9.0, "3001": 14.0, "3003": 11.0,
            "4002": 7.0, "3002": 13.0, "5002": 5.0, "DET": 6.0,
        },
    },
]

WEEKS = {1: WEEK_1, 2: WEEK_2, 3: WEEK_3}


def fake_get(url, params=None):
    """Stand-in for SleeperProvider._get. Routes on URL suffix."""
    if "/players/nfl" in url:
        return PLAYERS
    if "/projections/nfl" in url:
        return PROJECTIONS
    if url.endswith("/users"):
        return USERS
    if url.endswith("/rosters"):
        return ROSTERS
    if "/matchups/" in url:
        week = int(url.rsplit("/", 1)[-1])
        return WEEKS.get(week, [])
    if "/league/" in url:
        return LEAGUE
    raise AssertionError(f"Fixture has no route for {url}")
