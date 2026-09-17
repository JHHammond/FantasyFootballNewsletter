"""
ESPN's response shape, CHECKED against a real league.

Originally written from memory, then corrected on 16 Sep 2026 against league
1909054258 ("Ba1Lers", 10 teams, 2026, public) read out of a live browser. The
three things memory got wrong are noted inline with WAS/IS, because they are
exactly the kind of thing that will drift again.

Fields ESPN sends that this fixture deliberately omits: `rankings` and
`universeId` on players, `notificationSettings` on members, and the twenty-odd
settings blocks (`acquisitionSettings`, `financeSettings`, `tradeSettings`,
`scheduleSettings`, `draftSettings`...). None are read by the adapter. Omitting
them keeps this file legible; it also means this fixture cannot catch a bug
that depends on one of them.

To re-check against a real response:

    python -m tests.espn_diff captured.json

The league below is deliberately awkward in the ways real leagues are:

  - a team with a benched player who outscored a starter (the lineup_gap path)
  - a player with no projection at all (the vs_projection None path)
  - a D/ST, which has no headshot and a different position id
  - a player on IR, which is a bench slot but not slot 20
  - a completed week 1 and a week 2 being reported, so records entering the
    week are non-zero and can be checked against something
  - `name` missing on one team so the location+nickname fallback is exercised
"""

from __future__ import annotations

import copy

SEASON = 2025
LEAGUE_ID = "1234567"

# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


def _player(pid, name, position_id, pro_team_id, actual, projected,
            *, injury=None, eligible=None):
    """One player as ESPN nests them: entry -> playerPoolEntry -> player."""
    # SEASON-TO-DATE FIRST, ON PURPOSE.
    #
    # ESPN does not promise an order, and the filter that separates a weekly
    # number from a season total is the whole correctness of this parse. With
    # the weekly rows first, a test suite passes even if that filter is deleted
    # — the right answer is found before the wrong one is reached. Putting the
    # season rows first makes the filter load-bearing, which is the only way
    # the tests can actually defend it.
    stats = [
        {"statSourceId": 0, "statSplitTypeId": 0,
         "scoringPeriodId": 2, "seasonId": SEASON,
         "appliedTotal": (actual or 0) * 9},
        {"statSourceId": 1, "statSplitTypeId": 0,
         "scoringPeriodId": 2, "seasonId": SEASON,
         "appliedTotal": (projected or 0) * 9},
        # A different week's numbers, which must also never be picked up.
        {"statSourceId": 0, "statSplitTypeId": 1,
         "scoringPeriodId": 1, "seasonId": SEASON,
         "appliedTotal": 99.9},
        {"statSourceId": 1, "statSplitTypeId": 1,
         "scoringPeriodId": 1, "seasonId": SEASON,
         "appliedTotal": 88.8},
    ]
    if actual is not None:
        stats.append({
            "statSourceId": 0, "statSplitTypeId": 1,
            "scoringPeriodId": 2, "seasonId": SEASON,
            "appliedTotal": actual,
        })
    if projected is not None:
        stats.append({
            "statSourceId": 1, "statSplitTypeId": 1,
            "scoringPeriodId": 2, "seasonId": SEASON,
            "appliedTotal": projected,
        })

    player = {
        "id": pid,
        "fullName": name,
        "firstName": name.split()[0],
        "lastName": " ".join(name.split()[1:]),
        "defaultPositionId": position_id,
        "proTeamId": pro_team_id,
        "eligibleSlots": eligible if eligible is not None else [],
        "stats": stats,
    }
    # WAS: absent for healthy players.
    # IS:  always present, "ACTIVE" when healthy. Fourteen of sixteen players
    #      in the real league carried "ACTIVE", which naive handling turns
    #      into an injury note beside almost every name on the page.
    player["injuryStatus"] = injury or "ACTIVE"
    if injury:
        player["injured"] = True
    return player


def _entry(pid, name, position_id, pro_team_id, slot_id, actual, projected,
           **kw):
    return {
        "playerId": pid,
        "lineupSlotId": slot_id,
        "playerPoolEntry": {
            "id": pid,
            "appliedStatTotal": actual,
            "player": _player(pid, name, position_id, pro_team_id,
                              actual, projected, **kw),
        },
    }


#: Team 1 — won week 1, wins week 2. Left a big score on the bench.
TEAM_1_ENTRIES = [
    _entry(3139477, "Patrick Mahomes", 1, 12, 0, 24.6, 21.2,
           eligible=[0, 7, 20]),
    _entry(4362628, "Bijan Robinson", 2, 1, 2, 28.4, 19.0,
           eligible=[2, 3, 23, 20]),
    _entry(4241457, "Kenneth Walker III", 2, 26, 2, 34.1, 13.7,
           eligible=[2, 3, 23, 20]),
    _entry(4362887, "Nico Collins", 3, 34, 4, 22.0, 15.2,
           eligible=[4, 3, 5, 23, 20]),
    _entry(4569618, "Garrett Wilson", 3, 20, 4, 18.9, 14.0,
           injury="QUESTIONABLE", eligible=[4, 3, 5, 23, 20]),
    _entry(4430027, "Trey McBride", 4, 22, 6, 11.2, 12.4,
           eligible=[6, 5, 23, 20]),
    _entry(4239993, "Chuba Hubbard", 2, 29, 23, 6.5, 11.0,
           eligible=[2, 3, 23, 20]),
    _entry(4372065, "Cameron Little", 5, 30, 17, 11.0, 8.0,
           eligible=[17, 20]),
    _entry(-16023, "Seahawks D/ST", 16, 26, 16, 14.0, 6.5,
           eligible=[16, 20]),
    # BENCH — outscored the FLEX starter by a lot. This is the lineup_gap story.
    _entry(4426515, "Jahmyr Gibbs", 2, 8, 20, 33.6, 22.1,
           eligible=[2, 3, 23, 20]),
    # IR — a bench slot that is NOT 20.
    _entry(3916387, "Someone Injured", 3, 17, 21, 0.0, None,
           injury="OUT", eligible=[4, 20, 21]),
]

#: Team 2 — lost week 1, loses week 2. Carries a player with NO projection.
TEAM_2_ENTRIES = [
    _entry(4038941, "Kyler Murray", 1, 22, 0, 0.7, 18.1, eligible=[0, 7, 20]),
    _entry(4361579, "Ja'Marr Chase", 3, 4, 4, 3.2, 20.4,
           eligible=[4, 3, 5, 23, 20]),
    _entry(4360438, "James Cook", 2, 2, 2, 9.4, 14.1,
           eligible=[2, 3, 23, 20]),
    _entry(4685702, "Colston Loveland", 4, 3, 6, 0.0, 13.8,
           eligible=[6, 5, 23, 20]),
    # No projection at all — a just-signed player, or ESPN simply omitting it.
    _entry(9999001, "Undrafted Rookie", 3, 21, 4, 4.5, None,
           eligible=[4, 3, 5, 23, 20]),
    _entry(4426354, "A Kicker", 5, 18, 17, 7.0, 8.5, eligible=[17, 20]),
    _entry(-16002, "Bills D/ST", 16, 2, 16, 3.0, 7.0, eligible=[16, 20]),
]


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------

MEMBERS = [
    {"id": "{AAAA-1111}", "displayName": "johnhenryhammond",
     "firstName": "John", "lastName": "Hammond", "isLeagueManager": True},
    # No displayName — the firstName/lastName fallback has to fire.
    {"id": "{BBBB-2222}", "firstName": "Champ", "lastName": "Hammond",
     "isLeagueManager": False},
]

TEAMS = [
    {
        "id": 1,
        "name": "The Commissioners",
        "abbrev": "COMM",
        "owners": ["{AAAA-1111}"],
        # Includes week 2, which is exactly why we must not read it.
        "record": {"overall": {"wins": 2, "losses": 0, "ties": 0,
                               "pointsFor": 337.4}},
        "roster": {"entries": copy.deepcopy(TEAM_1_ENTRIES)},
    },
    {
        "id": 2,
        # No `name` — forces the location + nickname fallback.
        "location": "Satan's",
        "nickname": "Sommeliers",
        "abbrev": "SATN",
        "owners": ["{BBBB-2222}"],
        "record": {"overall": {"wins": 0, "losses": 2, "ties": 0,
                               "pointsFor": 180.2}},
        "roster": {"entries": copy.deepcopy(TEAM_2_ENTRIES)},
    },
]

SCHEDULE = [
    # Week 1 — already decided. Drives records entering week 2.
    {
        "id": 1, "matchupPeriodId": 1, "winner": "HOME",
        "home": {"teamId": 1, "totalPoints": 148.2},
        "away": {"teamId": 2, "totalPoints": 103.9},
    },
    # Week 2 — the one being reported.
    {
        "id": 2, "matchupPeriodId": 2, "winner": "HOME",
        "home": {
            "teamId": 1, "totalPoints": 170.7,
            "rosterForCurrentScoringPeriod": {
                "entries": copy.deepcopy(TEAM_1_ENTRIES)},
        },
        "away": {
            "teamId": 2, "totalPoints": 27.8,
            "rosterForCurrentScoringPeriod": {
                "entries": copy.deepcopy(TEAM_2_ENTRIES)},
        },
    },
    # Week 3 — not played. Must not count toward records or appear as a week.
    {
        "id": 3, "matchupPeriodId": 3, "winner": "UNDECIDED",
        "home": {"teamId": 2, "totalPoints": 0},
        "away": {"teamId": 1, "totalPoints": 0},
    },
]

SETTINGS = {
    "name": "Kevlarville",
    "size": 2,
    # Confirmed present. The one field that says whether a server with no
    # cookies will be able to read this league at all.
    "isPublic": True,
    "rosterSettings": {
        "lineupSlotCounts": {
            "0": 1,    # QB
            "2": 2,    # RB
            "4": 2,    # WR
            "6": 1,    # TE
            "16": 1,   # D/ST
            "17": 1,   # K
            "20": 6,   # Bench
            "21": 1,   # IR
            "23": 1,   # FLEX
            # Every unused slot comes back as a zero, and must be skipped.
            "3": 0, "5": 0, "7": 0, "8": 0, "9": 0, "10": 0, "11": 0,
            "12": 0, "13": 0, "14": 0, "15": 0, "18": 0, "19": 0, "24": 0,
            # 22 is in the real payload and is in no published slot table.
            # Count zero, so it never reaches a lineup — but it exists.
            "22": 0,
        }
    },
    "scoringSettings": {
        "scoringItems": [
            {"statId": 53, "points": 1.0},   # receptions -> full PPR
            {"statId": 42, "points": 0.1},
        ]
    },
}

STATUS = {
    "currentMatchupPeriod": 3,
    "latestScoringPeriod": 3,
    "finalScoringPeriod": 17,
    "isActive": True,
    # Every prior season this league has existed for. Not used yet; it is what
    # season_chain would be built on for ESPN.
    "previousSeasons": [2023, 2024],
}

# WAS: "draftDetails". IS: "draftDetail", singular.
DRAFT = {"drafted": True, "inProgress": False}


def league_payload(*, week: int = 2) -> dict:
    """The full object, as the league endpoint returns it."""
    return {
        "gameId": 1,
        "id": int(LEAGUE_ID),
        "seasonId": SEASON,
        "segmentId": 0,
        "scoringPeriodId": week,
        "settings": copy.deepcopy(SETTINGS),
        "status": copy.deepcopy(STATUS),
        "draftDetail": copy.deepcopy(DRAFT),
        "members": copy.deepcopy(MEMBERS),
        "teams": copy.deepcopy(TEAMS),
        "schedule": copy.deepcopy(SCHEDULE),
    }


def fake_get(league_id, season, views, params=None, fantasy_filter=None):
    """Drop-in for ESPNProvider._get in tests.

    Dispatches on the view, the way the real endpoint does — the transactions
    feed and the player lookup are separate requests to the same URL and a
    fake that ignored the view would hand the adapter a roster when it asked
    for a name.
    """
    names = set(views or [])

    if "kona_player_info" in names:
        # The real endpoint returns ONLY the ids asked for. Returning the lot
        # would hide an adapter that forgot to send the filter at all.
        wanted = (((fantasy_filter or {}).get("players") or {})
                  .get("filterIds") or {}).get("value")
        if wanted is None:
            # The real endpoint answers an unfiltered kona_player_info with
            # the league's whole player universe, paginated — never with the
            # ten ids the caller happened to want. Returning nothing here
            # makes the filter load-bearing: an adapter that forgets to send
            # it gets no names, and the tests below say so.
            return {"players": []}
        keep = {int(i) for i in wanted}
        return {"players": [r for r in TRANSACTION_PLAYERS["players"]
                            if int(r["id"]) in keep]}

    if "mTransactions2" in names:
        return TRANSACTIONS_RAW

    week = int((params or {}).get("scoringPeriodId") or 2)
    return league_payload(week=week)


#: Every top-level key we expect, for the diff tool.
EXPECTED_TOP_LEVEL = {
    "gameId", "id", "seasonId", "segmentId", "scoringPeriodId",
    "settings", "status", "draftDetail", "members", "teams", "schedule",
}

#: Paths this adapter actually reads. If a real response is missing one of
#: these, the adapter is broken in a specific, nameable way.
LOAD_BEARING_PATHS = [
    "settings.name",
    "settings.size",
    "settings.rosterSettings.lineupSlotCounts",
    "settings.scoringSettings.scoringItems",
    "status.currentMatchupPeriod",
    "status.latestScoringPeriod",
    "members[].id",
    "members[].displayName",
    "teams[].id",
    "teams[].name",
    "teams[].owners",
    "schedule[].matchupPeriodId",
    "schedule[].winner",
    "schedule[].home.teamId",
    "schedule[].home.totalPoints",
    "schedule[].home.rosterForCurrentScoringPeriod.entries",
    "entries[].lineupSlotId",
    "entries[].playerPoolEntry.appliedStatTotal",
    "entries[].playerPoolEntry.player.fullName",
    "entries[].playerPoolEntry.player.defaultPositionId",
    "entries[].playerPoolEntry.player.proTeamId",
    "entries[].playerPoolEntry.player.stats[].statSourceId",
    "entries[].playerPoolEntry.player.stats[].statSplitTypeId",
    "entries[].playerPoolEntry.player.stats[].scoringPeriodId",
    "entries[].playerPoolEntry.player.stats[].appliedTotal",
]


# ---------------------------------------------------------------------------
# TRANSACTIONS
#
# Copied field for field out of league 1909054258, week 2 of 2026, captured
# through a browser because the egress proxy here cannot reach ESPN.
#
# Every record below is one ESPN actually produced, and the set is chosen for
# the four things that make this feed harder than it looks:
#
#   1. ROSTER/LINEUP. Nine of that league's nineteen records were somebody
#      moving a player from the bench to the flex. They are not transactions.
#   2. FAILED_INVALIDPLAYERSOURCE. `status` is a family; there is no plain
#      "FAILED" to compare against.
#   3. A TRADE that EXECUTED while reporting isPending TRUE. Filtering on
#      isPending deletes the best story of the week.
#   4. bidAmount 0 everywhere, because this league runs waiver priority. Zero
#      is the absence of a bid, not a bid of zero.
#
# The lineup record is FIRST on purpose. A filter that only skips the tail
# passes on a fixture that puts the awkward row last.
# ---------------------------------------------------------------------------

TRANSACTIONS_RAW = {
    "seasonId": 2026,
    "scoringPeriodId": 2,
    "teams": [
        {"id": 1, "name": "The Intentional Torts"},
        {"id": 2, "name": "Mike Vick Legal Team"},
        {"id": 3, "name": "Prisoner of AzBijan"},
        {"id": 5, "name": "ACCOMMODATIONS"},
        {"id": 10, "name": "Likely going back 2 back"},
    ],
    "transactions": [
        # 1 — a lineup change. Not a transaction. Deliberately first.
        {"type": "ROSTER", "status": "EXECUTED", "isPending": False,
         "executionType": "EXECUTE", "scoringPeriodId": 2, "teamId": 10,
         "bidAmount": 0, "proposedDate": 1789467771866,
         "items": [{"type": "LINEUP", "playerId": 4569173,
                    "fromTeamId": 0, "toTeamId": 0,
                    "fromLineupSlotId": 23, "toLineupSlotId": 2}]},

        # 2 — a waiver claim that has not processed. Nobody has him yet.
        {"type": "WAIVER", "status": "PENDING", "isPending": True,
         "executionType": "EXECUTE", "scoringPeriodId": 2, "teamId": 2,
         "bidAmount": 0, "proposedDate": 1789562601765,
         "items": [{"type": "ADD", "playerId": 4569603,
                    "fromTeamId": 0, "toTeamId": 2},
                   {"type": "DROP", "playerId": 4696044,
                    "fromTeamId": 2, "toTeamId": 0}]},

        # 3 — a claim that went through.
        {"type": "WAIVER", "status": "EXECUTED", "isPending": False,
         "executionType": "PROCESS", "scoringPeriodId": 2, "teamId": 3,
         "bidAmount": 0, "proposedDate": 1789542778194,
         "items": [{"type": "ADD", "playerId": 4575131,
                    "fromTeamId": 0, "toTeamId": 3},
                   {"type": "DROP", "playerId": 4373626,
                    "fromTeamId": 3, "toTeamId": 0}]},

        # 4 — a claim that lost. The better story, most weeks.
        {"type": "WAIVER", "status": "FAILED_INVALIDPLAYERSOURCE",
         "isPending": False, "executionType": "PROCESS", "scoringPeriodId": 2,
         "teamId": 10, "bidAmount": 0, "proposedDate": 1789542778194,
         "items": [{"type": "ADD", "playerId": 4695883,
                    "fromTeamId": 0, "toTeamId": 10},
                   {"type": "DROP", "playerId": 4683062,
                    "fromTeamId": 10, "toTeamId": 0}]},

        # 5 — a free agent swap, both sides a defence. ESPN gives D/ST
        # negative ids and resolves their names like anybody else's.
        {"type": "FREEAGENT", "status": "EXECUTED", "isPending": False,
         "executionType": "EXECUTE", "scoringPeriodId": 2, "teamId": 1,
         "bidAmount": 0, "proposedDate": 1789500000000,
         "items": [{"type": "ADD", "playerId": -16027,
                    "fromTeamId": 0, "toTeamId": 1},
                   {"type": "DROP", "playerId": -16030,
                    "fromTeamId": 1, "toTeamId": 0}]},

        # 6 — the trade. EXECUTED, and isPending is True anyway.
        {"type": "TRADE_ACCEPT", "status": "EXECUTED", "isPending": True,
         "executionType": "PROCESS", "scoringPeriodId": 2, "teamId": 2,
         "bidAmount": 0, "proposedDate": 1789580000000,
         "items": [{"type": "TRADE", "playerId": 4871023,
                    "fromTeamId": 2, "toTeamId": 5},
                   {"type": "TRADE", "playerId": 4047646,
                    "fromTeamId": 5, "toTeamId": 2}]},

        # 7 — last week's claim. Right shape, wrong week.
        {"type": "WAIVER", "status": "EXECUTED", "isPending": False,
         "executionType": "PROCESS", "scoringPeriodId": 1,
         "teamId": 1, "bidAmount": 0, "proposedDate": 1789000000000,
         "items": [{"type": "ADD", "playerId": 4685261,
                    "fromTeamId": 0, "toTeamId": 1}]},
    ],
}

#: What kona_player_info returns for the ids above. Real names, real ids.
TRANSACTION_PLAYERS = {
    "players": [
        {"id": 4569603, "player": {"fullName": "Malik Washington",
                                   "defaultPositionId": 3, "proTeamId": 15}},
        {"id": 4696044, "player": {"fullName": "Kaelon Black",
                                   "defaultPositionId": 2, "proTeamId": 25}},
        {"id": 4575131, "player": {"fullName": "Jacory Croskey-Merritt",
                                   "defaultPositionId": 2, "proTeamId": 28}},
        {"id": 4373626, "player": {"fullName": "Tyler Allgeier",
                                   "defaultPositionId": 2, "proTeamId": 22}},
        {"id": 4695883, "player": {"fullName": "Jalen Coker",
                                   "defaultPositionId": 3, "proTeamId": 29}},
        {"id": 4683062, "player": {"fullName": "Xavier Worthy",
                                   "defaultPositionId": 3, "proTeamId": 12}},
        {"id": -16027, "player": {"fullName": "Buccaneers D/ST",
                                  "defaultPositionId": 16, "proTeamId": 27}},
        {"id": -16030, "player": {"fullName": "Jaguars D/ST",
                                  "defaultPositionId": 16, "proTeamId": 30}},
        {"id": 4871023, "player": {"fullName": "Carnell Tate",
                                   "defaultPositionId": 3, "proTeamId": 10}},
        {"id": 4047646, "player": {"fullName": "A.J. Brown",
                                   "defaultPositionId": 3, "proTeamId": 17}},
    ]
}

#: Paths the transactions adapter reads. Fed to tests/espn_diff.py so a real
#: capture can be checked against them in one command.
TRANSACTION_PATHS = [
    "transactions[].type",
    "transactions[].status",
    "transactions[].scoringPeriodId",
    "transactions[].bidAmount",
    "transactions[].proposedDate",
    "transactions[].items[].type",
    "transactions[].items[].playerId",
    "transactions[].items[].fromTeamId",
    "transactions[].items[].toTeamId",
]
