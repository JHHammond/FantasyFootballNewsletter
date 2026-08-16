"""
ESPN adapter -- skeleton.

Not implemented yet. This file exists so the shape of the work is obvious and
so `get_provider("espn")` fails with something useful instead of a KeyError.

WHAT YOU'RE UP AGAINST

ESPN has no official public fantasy API. Everyone uses the same undocumented
endpoint the web app talks to:

    https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}

Query it with a `view` parameter (repeatable) to control what comes back:

    view=mTeam          teams, owners, records
    view=mRoster        rosters, including per-player stats
    view=mMatchup       matchup pairings and scores
    view=mSettings      roster slots, scoring rules
    view=mMatchupScore  live scoring
    view=kona_player_info   the player universe (needs an X-Fantasy-Filter header)

Historical seasons live at a different path (.../leagueHistory/{league_id}?seasonId=).

PRIVATE LEAGUES need two cookies from a logged-in browser session, passed as a
Cookie header: `espn_s2` and `SWID`. There is no OAuth flow and no way to get
these programmatically -- the user has to paste them in, and they expire. Budget
UI for "your ESPN connection expired, paste new cookies." Public leagues need
no auth at all.

THE THREE THINGS THAT WILL BITE YOU

 1. Player IDs are integers, not strings. The old codebase compared IDs across
    dicts with inconsistent str()/raw keying, which happened to work because
    every Sleeper ID is already a string. Namespaced IDs (base.namespaced_id)
    make this a non-issue as long as you go through them.

 2. Slots are numeric IDs, not names. 0=QB, 2=RB, 4=WR, 6=TE, 16=D/ST, 17=K,
    20=Bench, 21=IR, 23=FLEX. Map them through SLOT_MAP below into our
    canonical names -- do not let numbers past this file.

 3. Scoring periods are not NFL weeks in the playoffs, and ESPN's matchupPeriod
    vs scoringPeriod distinction matters for multi-week playoff matchups.
    Use scoringPeriodId for weekly stats, matchupPeriodId for pairings.

Projections come back inside each player's `stats` array: the entry with
statSourceId=1 is the projection, statSourceId=0 is the actual. Both carry a
statSplitTypeId and scoringPeriodId you need to filter on.
"""

from __future__ import annotations

from .base import AuthRequired, FantasyProvider
from .models import League, WeekData

BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

#: ESPN numeric lineup slot -> our canonical slot name.
SLOT_MAP = {
    0: "QB",
    1: "QB",        # TQB, team QB
    2: "RB",
    3: "WR_RB_FLEX",
    4: "WR",
    5: "REC_FLEX",  # WR/TE
    6: "TE",
    7: "SUPER_FLEX",
    8: "DL",
    9: "DL",        # DE
    10: "LB",
    11: "LB",       # DT handled as DL upstream
    12: "DB",       # CB
    13: "DB",       # S
    14: "DB",
    15: "IDP_FLEX",
    16: "DEF",
    17: "K",
    18: "K",        # P
    19: "K",        # HC
    20: "BN",
    21: "IR",
    23: "FLEX",
}

#: ESPN numeric position -> our canonical position.
POSITION_MAP = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "DEF",
}


class ESPNProvider(FantasyProvider):
    name = "espn"
    display_name = "ESPN"
    supports_public_leagues = True   # public leagues only; private needs cookies
    supports_projections = True

    def __init__(self, cache=None, espn_s2: str | None = None, swid: str | None = None):
        super().__init__(cache=cache, espn_s2=espn_s2, swid=swid)
        self.espn_s2 = espn_s2
        self.swid = swid

    @property
    def _cookies(self) -> dict:
        if self.espn_s2 and self.swid:
            return {"espn_s2": self.espn_s2, "SWID": self.swid}
        return {}

    def get_league(self, league_id: str, season: int) -> League:
        raise NotImplementedError(
            "The ESPN provider is a stub. The endpoint and slot mappings are "
            "documented at the top of providers/espn.py -- implementing this "
            "means filling in get_league and get_week against the same "
            "contract SleeperProvider already satisfies."
        )

    def get_week(self, league_id: str, season: int, week: int) -> WeekData:
        raise NotImplementedError(
            "The ESPN provider is a stub. See providers/espn.py for the "
            "endpoint notes and the two cookies private leagues require."
        )
