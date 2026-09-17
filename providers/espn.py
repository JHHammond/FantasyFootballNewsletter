"""
ESPN adapter.

ESPN has no official public fantasy API. Everyone — this code included — talks
to the same undocumented endpoint the web app uses:

    {BASE_URL}/seasons/{season}/segments/0/leagues/{league_id}?view=...

WHAT HAS ACTUALLY BEEN CHECKED

Written from a remembered shape of that API, then corrected against a real
league (1909054258, 10 teams, 2026, public) on 16 Sep 2026. Confirmed against
that response:

  - `schedule[].home.totalPoints` is the team's score for the matchup period,
    and `rosterForCurrentScoringPeriod` carries the lineup as it stood that
    week. `rosterForMatchupPeriodDelta` did not appear at all.
  - Projections are `statSourceId == 1`, actuals `statSourceId == 0`, weekly
    splits `statSplitTypeId == 1` with a matching `scoringPeriodId`.
  - `lineupSlotId` 20 is bench, 21 is IR, everything else is on the field.
  - `team.record.overall` includes the week being reported, so records are
    replayed from the schedule instead.

The check worth repeating if this ever looks wrong: the players this adapter
classifies as starters should sum to ESPN's own `totalPoints` for that team,
to the cent. They did for all ten teams. Nothing else validates the slot map,
the bench rule and the points field all at once.

What is still NOT verified: the playoffs, where matchupPeriodId and
scoringPeriodId diverge and a single matchup can span two weeks; leagues with
IDP slots (8-15) or a superflex (7); and a league mid-draft.

PRIVATE LEAGUES need `espn_s2` and `SWID` cookies from a logged-in browser.
There is no OAuth and no way to obtain them programmatically. This adapter
accepts them if given and raises AuthRequired with a readable message if a
league turns out to need them — which, since the product asks people to make
their league public, is the common failure and deserves a good sentence.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

import requests

from .base import (
    AuthRequired,
    FantasyProvider,
    LeagueNotFound,
    ProviderError,
    WeekNotAvailable,
)
from .models import (League, Manager, Matchup, PlayerLine, Team,
                     Transaction, TransactionPlayer, WeekData)

BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

REQUEST_TIMEOUT = 20

#: ESPN rejects requests without a browser-ish User-Agent from some edges.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 "
                   "Safari/537.36"),
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# The mappings. ASSUMPTION SURFACE — if this adapter is wrong, it is probably
# wrong here, and here is where to fix it.
# ---------------------------------------------------------------------------

#: ESPN numeric lineup slot -> our canonical slot name.
SLOT_MAP = {
    0: "QB",
    1: "QB",          # TQB (team QB)
    2: "RB",
    3: "WR_RB_FLEX",
    4: "WR",
    5: "REC_FLEX",    # WR/TE
    6: "TE",
    7: "SUPER_FLEX",  # OP — any offensive player
    8: "DL",
    9: "DL",          # DE
    10: "LB",
    11: "DL",         # DT
    12: "DB",         # CB
    13: "DB",         # S
    14: "DB",
    15: "IDP_FLEX",
    16: "DEF",        # D/ST
    17: "K",
    18: "K",          # P
    19: "K",          # HC
    20: "BN",
    21: "IR",
    23: "FLEX",
    24: "EDGE",
}

#: Slots that are not on the field. Everything else counts as a starter.
BENCH_SLOT_IDS = frozenset({20, 21})

#: ESPN numeric position -> our canonical position.
POSITION_MAP = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "DEF",
}

#: ESPN proTeamId -> NFL abbreviation. 0 is free agent.
PRO_TEAMS = {
    0: None, 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
    14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB",
    28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

#: ESPN's injuryStatus is an enum, and its value for a perfectly healthy
#: player is "ACTIVE" — not null, not absent. Passed through naively that
#: tags fourteen of sixteen players as injured, and the writer prints
#: "[Active]" beside nearly every name in the lineup.
#:
#: Observed in a real league: ACTIVE x14, INJURY_RESERVE x1, absent x1.
_HEALTHY_INJURY_STATUSES = frozenset({"ACTIVE", "NORMAL", ""})

_INJURY_LABELS = {
    "QUESTIONABLE": "Questionable",
    "DOUBTFUL": "Doubtful",
    "OUT": "Out",
    "INJURY_RESERVE": "IR",
    "SUSPENSION": "Suspended",
    "DAY_TO_DAY": "Day-to-day",
    "PROBABLE": "Probable",
}


def _injury(value) -> Optional[str]:
    """A readable injury label, or None for a healthy player."""
    raw = (value or "").strip().upper()
    if raw in _HEALTHY_INJURY_STATUSES:
        return None
    return _INJURY_LABELS.get(raw, raw.replace("_", " ").title())


#: statSourceId. 0 is what happened, 1 is what was predicted.
STAT_ACTUAL = 0
STAT_PROJECTED = 1

#: statSplitTypeId 1 is a single scoring period; 0 is season-to-date.
STAT_SPLIT_WEEKLY = 1


def _scoring_type(settings: dict) -> str:
    """std / half_ppr / ppr, read off the reception scoring rule.

    ESPN expresses scoring as a list of {statId, points}. statId 53 is
    receptions. Anything else about the scoring system is irrelevant to us —
    we never compute points, we only report the ones ESPN already applied.
    """
    scoring = (settings or {}).get("scoringSettings") or {}
    for item in scoring.get("scoringItems") or []:
        if item.get("statId") == 53:
            points = item.get("points")
            if points is None:
                overrides = item.get("pointsOverrides") or {}
                points = next(iter(overrides.values()), 0)
            try:
                points = float(points)
            except (TypeError, ValueError):
                return "ppr"
            if points >= 0.9:
                return "ppr"
            if points >= 0.4:
                return "half_ppr"
            return "std"
    return "ppr"


def _roster_slots(settings: dict) -> list[str]:
    """Every slot in the lineup, one entry per available seat.

    `lineupSlotCounts` is {slotId: count} and includes zeroes for every slot
    the format doesn't use, which is most of them.
    """
    counts = ((settings or {}).get("rosterSettings") or {}).get("lineupSlotCounts") or {}
    slots: list[str] = []
    for raw_id, count in sorted(counts.items(), key=lambda kv: int(kv[0])):
        try:
            slot_id, count = int(raw_id), int(count)
        except (TypeError, ValueError):
            continue
        name = SLOT_MAP.get(slot_id)
        if name and count > 0:
            slots.extend([name] * count)
    return slots


def _team_name(team: dict) -> str:
    """ESPN has used `name`, and before that `location` + `nickname`.

    Both appear in the wild depending on season and endpoint, so try the modern
    one and fall back rather than printing "None None" on somebody's paper.
    """
    name = (team.get("name") or "").strip()
    if name:
        return name
    parts = [(team.get("location") or "").strip(),
             (team.get("nickname") or "").strip()]
    combined = " ".join(p for p in parts if p).strip()
    return combined or (team.get("abbrev") or f"Team {team.get('id')}")


def _member_name(member: dict) -> str:
    display = (member.get("displayName") or "").strip()
    if display:
        return display
    parts = [(member.get("firstName") or "").strip(),
             (member.get("lastName") or "").strip()]
    return " ".join(p for p in parts if p).strip() or "Unknown Manager"


def _stat_value(player: dict, scoring_period: int, source_id: int) -> Optional[float]:
    """Pull one week's actual or projected total out of a player's stats array.

    The array holds season totals, weekly splits, and both actual and projected
    versions of each, all in the same list distinguished only by these three
    fields. Filtering on all three is the difference between a projection and a
    season-long average.
    """
    for entry in player.get("stats") or []:
        if entry.get("statSourceId") != source_id:
            continue
        if entry.get("statSplitTypeId") != STAT_SPLIT_WEEKLY:
            continue
        if int(entry.get("scoringPeriodId") or -1) != int(scoring_period):
            continue
        total = entry.get("appliedTotal")
        if total is None:
            continue
        try:
            return round(float(total), 2)
        except (TypeError, ValueError):
            return None
    return None


class ESPNProvider(FantasyProvider):
    name = "espn"
    display_name = "ESPN"
    supports_public_leagues = True   # public leagues only; private needs cookies
    supports_projections = True

    #: Validated 16 Sep 2026 against league 1909054258 ("Ba1Lers", 10 teams,
    #: 2026, public), read live out of a browser. All ten teams parsed with no
    #: anomalies, and — the check that actually matters — the starters this
    #: adapter classifies summed to ESPN's own `totalPoints` to the cent for
    #: every team. That can only be true if the slot map, the bench exclusion
    #: and the points extraction are all correct at once.
    #:
    #: Three things memory had wrong and the real response corrected:
    #:   - `draftDetail`, not `draftDetails`
    #:   - `injuryStatus` is "ACTIVE" for healthy players, not absent
    #:   - `rosterForMatchupPeriodDelta` does not appear at all
    implemented = True

    def __init__(self, cache=None, espn_s2: str | None = None,
                 swid: str | None = None, **_ignored):
        super().__init__(cache=cache, espn_s2=espn_s2, swid=swid)
        self.espn_s2 = espn_s2
        self.swid = swid

    # -- transport ---------------------------------------------------------

    @property
    def _cookies(self) -> dict:
        if self.espn_s2 and self.swid:
            return {"espn_s2": self.espn_s2, "SWID": self.swid}
        return {}

    def _get(self, league_id: str, season: int, views: Iterable[str],
             params: dict | None = None, fantasy_filter: dict | None = None):
        """One request to the league endpoint with the given views.

        `view` is repeatable, and requests encodes a list value as repeated
        parameters, which is exactly what ESPN wants.

        `fantasy_filter` becomes the X-Fantasy-Filter header, which is how
        ESPN takes arguments too large for a query string — the player lookup
        behind the transactions feed passes a list of ids that way.
        """
        url = f"{BASE_URL}/seasons/{int(season)}/segments/0/leagues/{league_id}"
        query = {"view": list(views)}
        query.update(params or {})

        headers = dict(_HEADERS)
        if fantasy_filter is not None:
            headers["X-Fantasy-Filter"] = json.dumps(fantasy_filter)

        try:
            response = requests.get(url, params=query, headers=headers,
                                    cookies=self._cookies,
                                    timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"ESPN request failed: {exc}") from exc

        # 401 on a league that exists means it is private and we have no
        # cookies, which is the single most likely failure this product will
        # hit — the signup flow asks people to make their league public and
        # some of them will not have.
        if response.status_code in (401, 403):
            raise AuthRequired(
                "That ESPN league is private. Open it on ESPN, go to "
                "League Settings, and set “Make League Viewable to "
                "Public” to Yes — then try again."
            )
        if response.status_code == 404:
            raise LeagueNotFound(
                f"ESPN has no league {league_id} in {season}.")
        if response.status_code == 429:
            raise ProviderError("ESPN is rate limiting us. Try again shortly.")

        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ProviderError(f"ESPN returned {response.status_code}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            # ESPN answers an unknown league with an HTML error page rather
            # than JSON, so this is a real branch, not paranoia.
            raise LeagueNotFound(
                f"ESPN did not return league data for {league_id}.") from exc

        # A list comes back from the leagueHistory path; the current-season
        # path returns an object.
        if isinstance(payload, list):
            if not payload:
                raise LeagueNotFound(f"ESPN returned nothing for {league_id}.")
            payload = payload[0]
        return payload

    # -- league ------------------------------------------------------------

    def get_league(self, league_id: str, season: Optional[int] = None) -> League:
        season = int(season or _default_season())
        raw = self._get(league_id, season, ["mSettings", "mTeam"])

        settings = raw.get("settings") or {}
        status = raw.get("status") or {}

        members = raw.get("members") or []
        commissioners = [m.get("id") for m in members
                         if m.get("isLeagueManager") and m.get("id")]

        return League(
            provider=self.name,
            league_id=str(league_id),
            name=(settings.get("name") or "").strip() or f"League {league_id}",
            season=int(raw.get("seasonId") or season),
            roster_slots=_roster_slots(settings),
            team_count=int(settings.get("size")
                           or len(raw.get("teams") or [])
                           or 0),
            scoring_type=_scoring_type(settings),
            avatar_url=None,
            previous_league_id=None,
            commissioner_ids=commissioners,
            status=_league_status(raw, status),
        )

    def available_weeks(self, league_id: str, season: int) -> list[int]:
        """Weeks with scored results.

        `status.latestScoringPeriod` counts the week in progress, which has
        partial scores and would produce a paper about a Sunday that is still
        happening. `finalScoringPeriod` is not always present, so fall back to
        one behind the latest.
        """
        try:
            raw = self._get(league_id, season, ["mSettings"])
        except ProviderError:
            return []

        status = raw.get("status") or {}
        current = status.get("currentMatchupPeriod")
        latest = status.get("latestScoringPeriod")

        try:
            done = int(current) - 1 if current else int(latest or 0) - 1
        except (TypeError, ValueError):
            return []

        return list(range(1, max(done, 0) + 1))

    # -- a week ------------------------------------------------------------

    def get_week(self, league_id: str, season: int, week: int) -> WeekData:
        league = self.get_league(league_id, season)
        season = league.season
        week = int(week)

        raw = self._get(
            league_id, season,
            ["mMatchup", "mMatchupScore", "mRoster", "mTeam"],
            {"scoringPeriodId": week},
        )

        schedule = [row for row in (raw.get("schedule") or [])
                    if int(row.get("matchupPeriodId") or 0) == week]
        if not schedule:
            raise WeekNotAvailable(
                f"ESPN has no week {week} for this league yet."
                if league.has_drafted else
                "This league hasn't drafted yet, so there are no results to "
                "write up.")

        managers = {m.get("id"): Manager(
            manager_id=str(m.get("id")),
            display_name=_member_name(m),
            avatar_url=None,
            is_commissioner=bool(m.get("isLeagueManager")),
        ) for m in (raw.get("members") or []) if m.get("id")}

        teams_raw = {int(t["id"]): t for t in (raw.get("teams") or [])
                     if t.get("id") is not None}

        records = self._records_entering_week(raw, week)

        def build_team(side: dict) -> Optional[Team]:
            team_id = side.get("teamId")
            if team_id is None:
                return None
            meta = teams_raw.get(int(team_id), {})

            owners = meta.get("owners") or []
            manager = next((managers[o] for o in owners if o in managers), None)
            if manager is None:
                manager = Manager(manager_id=str(team_id),
                                  display_name="Unknown Manager")

            lineup, bench = self._roster_lines(side, meta, week)
            record = records.get(int(team_id), {"wins": 0, "losses": 0, "ties": 0})

            return Team(
                team_id=str(team_id),
                team_name=_team_name(meta),
                manager=manager,
                points=_points(side),
                lineup=lineup,
                bench=bench,
                wins=record["wins"],
                losses=record["losses"],
                ties=record["ties"],
            )

        matchups: list[Matchup] = []
        byes: list[Team] = []
        for row in schedule:
            home = build_team(row.get("home") or {})
            away = build_team(row.get("away") or {})
            pair = [t for t in (home, away) if t is not None]
            if len(pair) == 2:
                matchups.append(Matchup(matchup_id=str(row.get("id")),
                                        teams=(pair[0], pair[1])))
            elif pair:
                # ESPN represents a bye as a matchup with one side missing.
                byes.append(pair[0])

        return WeekData(league=league, week=week, matchups=matchups, byes=byes)

    # -- rosters -----------------------------------------------------------

    def _roster_lines(self, side: dict, team_meta: dict,
                      week: int) -> tuple[list[PlayerLine], list[PlayerLine]]:
        """Starters and bench for one team in one week.

        ASSUMPTION: the lineup as it stood in this scoring period lives on the
        matchup side (`rosterForCurrentScoringPeriod`), and `team.roster` is
        the roster as it stands NOW. For a finished week those differ — someone
        who was dropped on Tuesday is gone from the second and present in the
        first — so the matchup side is preferred and the team roster is only a
        fallback for weeks where ESPN omits it.
        """
        entries = ((side.get("rosterForCurrentScoringPeriod") or {}).get("entries")
                   or (side.get("rosterForMatchupPeriodDelta") or {}).get("entries")
                   or ((team_meta.get("roster") or {}).get("entries"))
                   or [])

        lineup: list[PlayerLine] = []
        bench: list[PlayerLine] = []

        for entry in entries:
            line = self._player_line(entry, week)
            if line is None:
                continue
            slot_id = entry.get("lineupSlotId")
            try:
                slot_id = int(slot_id)
            except (TypeError, ValueError):
                slot_id = 20
            (bench if slot_id in BENCH_SLOT_IDS else lineup).append(line)

        return lineup, bench

    def _player_line(self, entry: dict, week: int) -> Optional[PlayerLine]:
        pool = entry.get("playerPoolEntry") or {}
        player = pool.get("player") or entry.get("player") or {}
        player_id = player.get("id") or entry.get("playerId")
        if player_id is None:
            return None

        slot_id = entry.get("lineupSlotId")
        try:
            slot_name = SLOT_MAP.get(int(slot_id), "BN")
        except (TypeError, ValueError):
            slot_name = "BN"

        # appliedStatTotal on the entry is what this team actually scored from
        # this player, which is the number the league sees. The stats array is
        # the fallback and the only source of the projection.
        actual = pool.get("appliedStatTotal")
        if actual is None:
            actual = _stat_value(player, week, STAT_ACTUAL)
        try:
            actual = round(float(actual), 2) if actual is not None else 0.0
        except (TypeError, ValueError):
            actual = 0.0

        eligible = player.get("eligibleSlots") or []
        positions = tuple(
            name for name in
            (SLOT_MAP.get(int(s)) for s in eligible if isinstance(s, int))
            if name and name not in ("BN", "IR")
        )

        return PlayerLine(
            player_id=f"{self.name}:{player_id}",
            name=(player.get("fullName") or "").strip() or str(player_id),
            position=POSITION_MAP.get(player.get("defaultPositionId"), "FLEX"),
            points=actual,
            slot=slot_name,
            positions=positions,
            nfl_team=PRO_TEAMS.get(player.get("proTeamId")),
            projected=_stat_value(player, week, STAT_PROJECTED),
            headshot_url=_headshot(player_id, player.get("defaultPositionId")),
            injury_status=_injury(player.get("injuryStatus")),
        )

    # -- records -----------------------------------------------------------

    def _records_entering_week(self, raw: dict, week: int) -> dict[int, dict]:
        """Each team's record BEFORE the week being reported on.

        `team.record.overall` includes the week we are about to narrate, which
        would make every paper describe standings that already account for its
        own headline. The full schedule is in the payload we already have, so
        replay it rather than fetching anything.
        """
        records: dict[int, dict] = {}

        def bump(team_id, key):
            if team_id is None:
                return
            row = records.setdefault(int(team_id),
                                     {"wins": 0, "losses": 0, "ties": 0})
            row[key] += 1

        for row in raw.get("schedule") or []:
            try:
                period = int(row.get("matchupPeriodId") or 0)
            except (TypeError, ValueError):
                continue
            if period <= 0 or period >= week:
                continue

            home, away = row.get("home") or {}, row.get("away") or {}
            home_id, away_id = home.get("teamId"), away.get("teamId")
            winner = (row.get("winner") or "").upper()

            if winner == "HOME":
                bump(home_id, "wins")
                bump(away_id, "losses")
            elif winner == "AWAY":
                bump(away_id, "wins")
                bump(home_id, "losses")
            elif winner == "TIE":
                bump(home_id, "ties")
                bump(away_id, "ties")
            else:
                # UNDECIDED on a past week means ESPN never finalised it.
                # Counting it as anything would be inventing a result.
                continue

        return records

    # -- transactions ------------------------------------------------------
    #
    # ESPN's feed is NOT a list of roster moves. It is a list of everything
    # that touched a roster, and most of it is people setting their lineup.
    # In the league this was written against, nine of nineteen records were
    # type ROSTER carrying only LINEUP items — somebody moving a running back
    # from the bench to the flex. Printed as transactions those would be nine
    # lines of nothing in the middle of the paper.
    #
    # Three more things the real payload does that the obvious reading gets
    # wrong, each verified against league 1909054258:
    #
    #   * `status` is a FAMILY, not a value. EXECUTED, PENDING, and
    #     FAILED_INVALIDPLAYERSOURCE all appear. Comparing to "FAILED" misses
    #     every real failure; every failure is FAILED_<reason>.
    #
    #   * `isPending` is not "did not happen". The one completed TRADE in the
    #     league reports status EXECUTED with isPending TRUE. Filtering on
    #     isPending drops the single most interesting row of the week.
    #
    #   * `bidAmount` is 0 in a waiver-PRIORITY league, which is most of them.
    #     Zero is not a bid of nothing, it is the absence of bidding, and
    #     printing "$0" tells a league that doesn't use FAAB that everybody
    #     bid nothing.

    #: ESPN transaction types that move a player between rosters. ROSTER is
    #: deliberately absent: it is a lineup change.
    _MOVE_TYPES = {"WAIVER", "FREEAGENT", "TRADE_ACCEPT", "TRADE"}

    #: Item types that mean a player changed hands. LINEUP does not.
    _MOVE_ITEMS = {"ADD", "DROP", "TRADE"}

    def get_transactions(self, league_id: str, season: int, week: int) -> list:
        """Every roster move in this week, named and attributed.

        Failed claims are kept deliberately, the same as the Sleeper adapter:
        somebody bid, publicly, and did not get the player.
        """
        try:
            raw = self._get(league_id, season, ["mTransactions2", "mTeam"])
        except ProviderError:
            # A missing transactions feed must never cost the paper its
            # matchups. This section is a bonus; the scores are the product.
            return []

        rows = [r for r in (raw.get("transactions") or [])
                if isinstance(r, dict)]
        if not rows:
            return []

        team_names = {t.get("id"): _team_name(t) for t in (raw.get("teams") or [])}

        def team(team_id) -> Optional[str]:
            # 0 is free agency, not a team. Returning a name for it would
            # print "Free Agency acquired…" as though it were a manager.
            if not team_id:
                return None
            return team_names.get(team_id) or f"Team {team_id}"

        kept = []
        wanted_ids: set[int] = set()
        for row in rows:
            if (row.get("type") or "").upper() not in self._MOVE_TYPES:
                continue

            # A claim that has not processed has not happened. Left in, the
            # first draft printed "Mike Vick Legal Team claimed Malik
            # Washington" — as a FAILURE, because PENDING is not EXECUTED —
            # for a claim that had not run yet and might still win. Both
            # halves of that are wrong, and it is worse than silence: the
            # claim will process on Tuesday and appear correctly in next
            # week's paper, having already been reported as lost in this one.
            #
            # Filtered on STATUS, not on isPending. The one completed trade in
            # the league reports isPending TRUE alongside status EXECUTED, so
            # the obvious check deletes the best story of the week.
            if (row.get("status") or "").upper() == "PENDING":
                continue
            try:
                if int(row.get("scoringPeriodId") or 0) != int(week):
                    continue
            except (TypeError, ValueError):
                continue

            items = [i for i in (row.get("items") or [])
                     if isinstance(i, dict)
                     and (i.get("type") or "").upper() in self._MOVE_ITEMS]
            if not items:
                continue

            kept.append((row, items))
            for item in items:
                if item.get("playerId") is not None:
                    wanted_ids.add(int(item["playerId"]))

        if not kept:
            return []

        players = self._player_names(league_id, season, sorted(wanted_ids))

        def player(player_id) -> TransactionPlayer:
            info = players.get(int(player_id)) if player_id is not None else None
            if not info:
                # A name we could not resolve prints as the id otherwise, in
                # the middle of a sentence, which reads as a bug to a reader
                # and is one.
                return TransactionPlayer(player_id=str(player_id),
                                         name="A player")
            return TransactionPlayer(
                player_id=str(player_id),
                name=info.get("name") or "A player",
                position=info.get("position"),
                nfl_team=info.get("nfl_team"),
            )

        out: list[Transaction] = []
        for row, items in kept:
            kind = self._transaction_kind(row)
            status = self._transaction_status(row)

            adds, drops, teams = [], [], []
            for item in items:
                item_type = (item.get("type") or "").upper()
                who = player(item.get("playerId"))
                gained, lost = team(item.get("toTeamId")), team(item.get("fromTeamId"))

                # A TRADE item is a drop and an add at once — one player
                # leaving one roster and arriving on another.
                if item_type in ("ADD", "TRADE") and gained:
                    adds.append((gained, who))
                if item_type in ("DROP", "TRADE") and lost:
                    drops.append((lost, who))
                teams.extend(t for t in (gained, lost) if t)

            if not adds and not drops:
                continue

            ordered_teams = list(dict.fromkeys(teams))
            out.append(Transaction(
                kind=kind,
                status=status,
                week=int(week),
                teams=ordered_teams,
                adds=adds,
                drops=drops,
                bid=self._transaction_bid(row),
                created=row.get("proposedDate"),
            ))

        # Oldest first reads as a wire: the week in the order it happened.
        out.sort(key=lambda t: t.created or 0)
        return out

    @staticmethod
    def _transaction_kind(row: dict) -> str:
        """ESPN's vocabulary mapped onto the paper's three kinds."""
        espn_type = (row.get("type") or "").upper()
        if espn_type.startswith("TRADE"):
            return "trade"
        if espn_type == "WAIVER":
            return "waiver"
        return "free_agent"

    @staticmethod
    def _transaction_status(row: dict) -> str:
        """"complete" or "failed", from a status that is a family of strings.

        EXECUTED is the only success. Everything beginning FAILED_ is a
        failure, and there is more than one — FAILED_INVALIDPLAYERSOURCE is
        the one this league produced. PENDING never reaches here — those rows
        are dropped in get_transactions, because a claim that has not
        processed has not happened, and calling it either a success or a
        failure is a statement the paper cannot support.
        """
        status = (row.get("status") or "").upper()
        return "complete" if status == "EXECUTED" else "failed"

    @staticmethod
    def _transaction_bid(row: dict) -> Optional[int]:
        """FAAB spent, or None in a waiver-priority league.

        ESPN reports bidAmount 0 for every claim in a league that runs on
        priority rather than a budget — which is most of them. Zero is the
        absence of bidding, not a bid of nothing, and "$0" in the paper tells
        a league that does not use FAAB that everybody bid nothing.
        """
        bid = row.get("bidAmount")
        if isinstance(bid, (int, float)) and bid > 0:
            return int(bid)
        return None

    def _player_names(self, league_id: str, season: int,
                      player_ids: list[int]) -> dict[int, dict]:
        """id -> {name, position, nfl_team}, for ids the feed only numbers.

        A transaction record names nobody; it carries player ids and nothing
        else. This is the lookup, and it is best-effort: a paper that says
        "A player" is worse than one with the name and far better than one
        that fails to render.

        Defences come back from this correctly — ESPN gives them negative ids
        and a fullName of "Buccaneers D/ST" — so unlike the Sleeper adapter
        there is no special case for them here.
        """
        if not player_ids:
            return {}

        try:
            raw = self._get(
                league_id, season, ["kona_player_info"],
                fantasy_filter={"players": {"filterIds": {"value": player_ids}}})
        except ProviderError:
            return {}

        found: dict[int, dict] = {}
        for row in (raw.get("players") or []):
            if not isinstance(row, dict):
                continue
            info = row.get("player") or {}
            try:
                key = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            found[key] = {
                "name": info.get("fullName"),
                "position": POSITION_MAP.get(info.get("defaultPositionId")),
                "nfl_team": PRO_TEAMS.get(info.get("proTeamId")),
            }
        return found


def _points(side: dict) -> float:
    for key in ("totalPoints", "totalPointsLive"):
        value = side.get(key)
        if value is not None:
            try:
                return round(float(value), 2)
            except (TypeError, ValueError):
                continue
    return 0.0


def _headshot(player_id, position_id) -> Optional[str]:
    """ESPN serves team logos for D/ST and headshots for everyone else."""
    if position_id == 16:
        return None
    return (f"https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/"
            f"full/{player_id}.png&w=350&h=254")


def _league_status(raw: dict, status: dict) -> str:
    """pre_draft / drafting / in_season / complete.

    ESPN reports this as a set of booleans and a numeric draft status rather
    than a single field, so it has to be assembled.
    """
    if status.get("isActive") is False and status.get("currentMatchupPeriod"):
        return "complete"
    draft = (raw.get("draftDetail") or {})
    if draft.get("inProgress"):
        return "drafting"
    if draft.get("drafted") is False:
        return "pre_draft"
    final = status.get("finalScoringPeriod")
    current = status.get("currentMatchupPeriod")
    try:
        if final and current and int(current) > int(final):
            return "complete"
    except (TypeError, ValueError):
        pass
    return "in_season"


def _default_season() -> int:
    from nfl_week import current_season
    return current_season()
