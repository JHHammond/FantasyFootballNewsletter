"""
Yahoo response shapes, built the way Yahoo builds them.

WRITTEN FROM YAHOO'S DOCUMENTATION, NOT CAPTURED. When a real league has been
checked (/connect/yahoo/check), replace or correct these against what it
returned — ESPN's fixtures passed first time by luck and were wrong in three
places, which is why this note exists.

The two oddities every builder reproduces:
  * collections are {"0": {...}, "1": {...}, "count": n}
  * a resource is [ [ {one-key dict}, {one-key dict}, [], ... ], {sub}, ... ]
"""

LEAGUE_KEY = "461.l.24680"
GAME_KEY = "461"


def _collection(key, items):
    out = {str(i): {key: item} for i, item in enumerate(items)}
    out["count"] = len(items)
    return out


def _meta(**fields):
    """Yahoo's metadata block: a list of one-key dicts with padding."""
    out = []
    for k, v in fields.items():
        out.append({k: v})
        if k == "name":
            out.append([])        # Yahoo's empty-list padding
    return out


LEAGUE_META = {
    "league_key": LEAGUE_KEY, "league_id": "24680", "name": "Dirty South Dynasty",
    "url": "https://football.fantasysports.yahoo.com/f1/24680",
    "logo_url": "https://example.test/logo.png",
    "draft_status": "postdraft", "num_teams": 4, "scoring_type": "head",
    "current_week": "4", "start_week": "1", "end_week": "17",
    "is_finished": 0, "season": "2026", "renew": "449_13579", "renewed": "",
    "game_code": "nfl",
}


def metadata():
    return {"league": [LEAGUE_META]}


def settings():
    roster = [{"roster_position": {"position": p, "position_type": "O",
                                   "count": c, "is_starting_position": 1}}
              for p, c in (("QB", 1), ("WR", 2), ("RB", 2), ("TE", 1),
                           ("W/R/T", 1), ("K", 1), ("DEF", 1),
                           ("BN", 5), ("IR", 1))]
    mods = [{"stat": {"stat_id": sid, "value": v}} for sid, v in (
        ("4", "0.04"), ("5", "4"), ("6", "-1"), ("9", "0.1"), ("10", "6"),
        ("11", "0.5"), ("12", "0.1"), ("13", "6"), ("18", "-2"), ("29", "1"))]
    return {"league": [LEAGUE_META, {"settings": [{
        "roster_positions": roster,
        "stat_modifiers": {"stats": mods},
    }]}]}


TEAMS = [
    ("461.l.24680.t.1", "Hank's Heroes", "Hank", 1),
    ("461.l.24680.t.2", "Waiver Wire Warriors", "Dee", 0),
    ("461.l.24680.t.3", "Kevlarville", "Kev", 0),
    ("461.l.24680.t.4", "The Mid Tier", "Mo", 0),
]


def _team(key, name, nick, commish, points, projected):
    return [
        _meta(team_key=key, team_id=key.rsplit(".", 1)[-1], name=name,
              url="https://example.test",
              team_logos=[{"team_logo": {"size": "large", "url": "https://example.test/t.png"}}],
              managers=[{"manager": {"manager_id": key[-1], "nickname": nick,
                                     "guid": f"GUID{key[-1]}",
                                     "is_commissioner": str(commish)}}]),
        {"team_points": {"coverage_type": "week", "week": "3", "total": points},
         "team_projected_points": {"coverage_type": "week", "week": "3",
                                   "total": projected}},
    ]


#: Week -> [(team_a_index, points_a, team_b_index, points_b, winner_index|None)]
RESULTS = {
    1: [(0, "110.00", 1, "100.00", 0), (2, "90.00", 3, "95.00", 3)],
    2: [(0, "120.00", 2, "80.00", 0), (1, "101.00", 3, "99.00", 1)],
    3: [(0, "61.60", 3, "45.10", 0), (1, "70.00", 2, "70.00", None)],
}


def scoreboard(week):
    matchups = []
    for a, pa, b, pb, win in RESULTS[week]:
        ta, tb = TEAMS[a], TEAMS[b]
        m = {"week": str(week), "status": "postevent", "is_playoffs": "0",
             "is_tied": 1 if win is None else 0,
             "0": {"teams": _collection("team", [
                 _team(*ta, pa, "100.00"), _team(*tb, pb, "100.00")])}}
        if win is not None:
            m["winner_team_key"] = TEAMS[win][0]
        matchups.append(m)
    return {"league": [LEAGUE_META, {"scoreboard": {
        "0": {"matchups": _collection("matchup", matchups)}, "week": week}}]}


def _player(pid, full, pos, team, slot, points, stats=None, status=None,
            eligible=None):
    meta = dict(player_key=f"{GAME_KEY}.p.{pid}", player_id=str(pid),
                name={"full": full, "first": full.split()[0],
                      "last": full.split()[-1]},
                editorial_team_abbr=team, display_position=pos,
                primary_position=pos,
                image_url=f"https://example.test/{pid}.png",
                eligible_positions=[{"position": p} for p in (eligible or [pos])])
    if status:
        meta["status"] = status
    return [
        _meta(**meta),
        {"selected_position": [{"coverage_type": "week"}, {"week": "3"},
                               {"position": slot}]},
        {"player_stats": {"coverage_type": "week", "week": "3",
                          "stats": [{"stat": {"stat_id": k, "value": str(v)}}
                                    for k, v in (stats or {}).items()]},
         "player_points": {"coverage_type": "week", "week": "3",
                           "total": str(points)}},
    ]


#: Team 1's week 3. Starters sum to 61.60, its scoreboard total.
HANK_ROSTER = [
    # 250 yds * .04 = 10, 2 TD = 8, 1 INT = -1, 20 rush yds = 2  -> 19.00
    _player(30123, "Joe Burrow", "QB", "Cin", "QB", "19.00",
            {"4": 250, "5": 2, "6": 1, "9": 20}),
    # 6 rec * .5 = 3, 80 yds = 8, 1 rec TD = 6 -> 17.00
    _player(31001, "Ja'Marr Chase", "WR", "Cin", "WR", "17.00",
            {"11": 6, "12": 80, "13": 1}, eligible=["WR"]),
    _player(31002, "Tee Higgins", "WR", "Cin", "WR", "4.00",
            {"11": 2, "12": 30}),
    # 70 yds = 7, 1 rush TD = 6, 1 fumble lost = -2 -> 11.00
    _player(32001, "Bijan Robinson", "RB", "Atl", "RB", "11.00",
            {"9": 70, "10": 1, "18": 1}),
    _player(32002, "Breece Hall", "RB", "NYJ", "RB", "3.10", {"9": 31},
            status="Q"),
    _player(33001, "Sam LaPorta", "TE", "Det", "TE", "0.00", {}),
    _player(34001, "Rashee Rice", "WR", "KC", "W/R/T", "2.50", {"11": 1, "12": 20},
            eligible=["WR"]),
    _player(35001, "Brandon Aubrey", "K", "Dal", "K", "5.00", {"29": 5}),
    _player(100022, "Jacksonville", "DEF", "Jax", "DEF", "0.00", {}),
    # Bench and IR: points that must NOT count.
    _player(36001, "Jaylen Warren", "RB", "Pit", "BN", "22.40", {"9": 104, "10": 2}),
    _player(36002, "Christian McCaffrey", "RB", "SF", "IR", "0.00", {},
            status="IR"),
]


def _score(team_key, week):
    idx = [t[0] for t in TEAMS].index(team_key)
    for a, pa, b, pb, _ in RESULTS.get(week, []):
        if a == idx:
            return pa
        if b == idx:
            return pb
    return "0.00"


def roster(team_key, week):
    """Hank's real-looking week 3; everyone else one QB who scored the lot,
    so every team reconciles and the check page can say so."""
    if team_key.endswith(".t.1") and week == 3:
        players = HANK_ROSTER
    else:
        n = int(team_key.rsplit(".", 1)[-1])
        players = [_player(40000 + n, f"Somebody {n}", "QB", "Buf", "QB",
                           _score(team_key, week), {})]
    return {"team": [_meta(team_key=team_key, name="x"), {"roster": {
        "coverage_type": "week", "week": str(week), "is_editable": 0,
        "0": {"players": _collection("player", players)}}}]}


def draftresults():
    return {"league": [LEAGUE_META, {"draft_results": _collection(
        "draft_result", [
            {"pick": 1, "round": 1, "team_key": TEAMS[0][0],
             "player_key": f"{GAME_KEY}.p.31001"},
            {"pick": 2, "round": 1, "team_key": TEAMS[1][0],
             "player_key": f"{GAME_KEY}.p.32001"},
        ])}]}


def _tx_player(pid, full, pos, team, data):
    return [_meta(player_key=f"{GAME_KEY}.p.{pid}", player_id=str(pid),
                  name={"full": full}, editorial_team_abbr=team,
                  display_position=pos),
            {"transaction_data": data}]


def transactions():
    t1, t2 = TEAMS[0], TEAMS[1]
    rows = [
        # A waiver claim with a FAAB bid, and the drop that made room.
        [{"transaction_key": "461.l.24680.tr.40", "type": "add/drop",
          "status": "successful", "timestamp": "1789900000", "faab_bid": "17"},
         {"players": _collection("player", [
             _tx_player(36001, "Jaylen Warren", "RB", "Pit",
                        [{"type": "add", "source_type": "waivers",
                          "destination_type": "team",
                          "destination_team_key": t1[0],
                          "destination_team_name": t1[1]}]),
             _tx_player(37001, "Zach Ertz", "TE", "Was",
                        {"type": "drop", "source_type": "team",
                         "source_team_key": t1[0], "source_team_name": t1[1],
                         "destination_type": "waivers"}),
         ])}],
        # A free agent add.
        [{"transaction_key": "461.l.24680.tr.41", "type": "add",
          "status": "successful", "timestamp": "1789910000"},
         {"players": _collection("player", [
             _tx_player(38001, "Rico Dowdle", "RB", "Car",
                        [{"type": "add", "source_type": "freeagents",
                          "destination_type": "team",
                          "destination_team_key": t2[0],
                          "destination_team_name": t2[1]}]),
         ])}],
        # A trade, with a pick riding along.
        [{"transaction_key": "461.l.24680.tr.42", "type": "trade",
          "status": "successful", "timestamp": "1789920000",
          "trader_team_key": t1[0], "tradee_team_key": t2[0],
          "picks": [{"pick": {"source_team_key": t2[0],
                              "destination_team_key": t1[0],
                              "original_team_key": TEAMS[3][0],
                              "round": "2"}}]},
         {"players": _collection("player", [
             _tx_player(31002, "Tee Higgins", "WR", "Cin",
                        [{"type": "trade", "source_type": "team",
                          "source_team_key": t1[0], "destination_type": "team",
                          "destination_team_key": t2[0]}]),
         ])}],
        # A vetoed trade: not news.
        [{"transaction_key": "461.l.24680.tr.43", "type": "trade",
          "status": "vetoed", "timestamp": "1789930000"},
         {"players": _collection("player", [])}],
    ]
    return {"league": [LEAGUE_META, {"transactions": _collection("transaction", rows)}]}


def user_leagues():
    return {"users": _collection("user", [[
        {"guid": "GUID1"},
        {"games": _collection("game", [[
            {"game_key": GAME_KEY, "code": "nfl", "season": "2026"},
            {"leagues": _collection("league", [[LEAGUE_META]])},
        ]])},
    ]])}


def route(path):
    """What Yahoo would answer for a path under /fantasy/v2/."""
    if path.startswith("users;use_login=1"):
        return user_leagues()
    if path.startswith("team/"):
        team_key = path.split("/")[1]
        week = int(path.split("roster;week=")[1].split("/")[0])
        return roster(team_key, week)
    sub = path.split("/", 2)[2]
    if sub == "settings":
        return settings()
    if sub == "metadata":
        return metadata()
    if sub == "draftresults":
        return draftresults()
    if sub.startswith("transactions"):
        return transactions()
    if sub.startswith("scoreboard;week="):
        week = int(sub.split("=")[1])
        if week not in RESULTS:
            return {"league": [LEAGUE_META, {"scoreboard": {
                "0": {"matchups": {"count": 0}}, "week": week}}]}
        return scoreboard(week)
    raise KeyError(path)
