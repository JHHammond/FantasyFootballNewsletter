"""
Yahoo adapter.

Yahoo is the one platform with a real, documented fantasy API — and the only
one that answers NOTHING without a signed-in user. There is no public league
on Yahoo in the sense ESPN and Sleeper have: every request carries an OAuth
access token belonging to somebody who is in the league.

    https://fantasysports.yahooapis.com/fantasy/v2/<resource>?format=json

WHERE THE TOKEN COMES FROM

This package knows nothing about users or databases, and a dozen call sites
across the app build providers with nothing but a platform name. So the
adapter takes an `access_token=` when the caller has one (the connect flow,
right after sign-in) and otherwise asks a TOKEN SOURCE — a function the web
layer registers with `set_token_source`, which maps a league key to the
owner's current access token, refreshing it when it has expired. Nothing
registered and no token passed means AuthRequired, with a sentence a person
can act on.

IDS

A Yahoo league key is "<game_key>.l.<league_id>", e.g. "461.l.12345". The game
key changes every season, so the key alone pins the season, and it is what is
stored as `platform_league_id`. Team keys are "<league_key>.t.<n>", player keys
"<game_key>.p.<player_id>". Players are namespaced as "yahoo:<player_id>" —
the bare id is stable across seasons, the game prefix is not.

THE JSON

Yahoo's JSON is XML wearing a disguise. Collections are objects keyed "0",
"1", ... with a "count"; a resource is a LIST whose first element is itself a
list of one-key dicts (with the occasional empty list thrown in), followed by
dicts for each sub-resource. `_merge` and `_items` flatten those two shapes,
and nothing else in this file should have to care.

WHAT HAS BEEN CHECKED

NOTHING, YET. This was written from Yahoo's documentation and the well-known
shape of its responses, and `implemented` stays False until a real league's
response has been parsed and reconciled — the same bar ESPN had to clear. The
check that matters is the same one: the players this adapter classifies as
starters must sum to Yahoo's own team_points for every team, to the cent.
/connect/yahoo/check runs exactly that against a connected league.

Known gaps, by design:
  * No player projections. Yahoo publishes projected points per TEAM on the
    scoreboard, not per player, so `supports_projections` is False and the
    writer skips boom/bust-vs-projection storylines, as it does for any
    platform without them.
  * Failed waiver claims. Yahoo's transaction feed lists what happened, not
    what was attempted.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any, Callable, Iterable, Optional

import requests

from .base import (
    AuthRequired,
    FantasyProvider,
    LeagueNotFound,
    ProviderError,
    WeekNotAvailable,
)
from .models import (League, Manager, Matchup, PlayerLine, StatLine, Team,
                     Transaction, TransactionPlayer, WeekData)

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"

REQUEST_TIMEOUT = 20

#: League settings barely move within a season; a finished week never does.
TTL_LEAGUE = 30 * 60
TTL_WEEK = 10 * 60

# ---------------------------------------------------------------------------
# The mappings. ASSUMPTION SURFACE — if this adapter is wrong, it is probably
# wrong here.
# ---------------------------------------------------------------------------

#: Yahoo roster position -> our canonical slot.
SLOT_MAP = {
    "QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "DEF": "DEF",
    "W/R/T": "FLEX",
    "W/R": "WR_RB_FLEX",
    "W/T": "REC_FLEX",
    "R/T": "FLEX",
    "Q/W/R/T": "SUPER_FLEX",
    "DL": "DL", "DE": "DL", "DT": "DL",
    "LB": "LB",
    "DB": "DB", "CB": "DB", "S": "DB",
    "D": "IDP_FLEX",
    "BN": "BN",
    "IR": "IR",
    "IR+": "IR",
    "NA": "IR",
}

BENCH_POSITIONS = frozenset({"BN", "IR", "IR+", "NA"})

#: Yahoo NFL stat ids for the counting stats the paper reads. Yahoo publishes
#: this table (game/nfl/stat_categories), and the league's own stat_modifiers
#: let /connect/yahoo/check reconcile it arithmetically, the way ESPN's was.
STAT_PASS_TD = "5"
STAT_INT = "6"
STAT_RUSH_TD = "10"
STAT_REC_TD = "13"
STAT_FUM_LOST = "18"
STAT_RECEPTION = "11"

#: Yahoo's player `status` codes. Absent means healthy.
_INJURY_LABELS = {
    "Q": "Questionable",
    "D": "Doubtful",
    "O": "Out",
    "IR": "IR",
    "IR-R": "IR",
    "PUP-P": "PUP",
    "PUP-R": "PUP",
    "NFI-R": "NFI",
    "SUSP": "Suspended",
    "NA": "Inactive",
    "COVID-19": "Out",
}

#: Yahoo team abbreviations are mostly what everyone else uses, lowercase.
_TEAM_FIX = {"JAC": "JAX", "WAS": "WSH", "LA": "LAR"}


# ---------------------------------------------------------------------------
# Token source
# ---------------------------------------------------------------------------

TokenSource = Callable[[str], Optional[str]]
_token_source: Optional[TokenSource] = None


def set_token_source(source: Optional[TokenSource]) -> None:
    """Register how a league key becomes an access token. The web layer does
    this at import; tests swap it out."""
    global _token_source
    _token_source = source


# ---------------------------------------------------------------------------
# Flattening Yahoo's JSON
# ---------------------------------------------------------------------------

def _merge(node: Any) -> dict:
    """A Yahoo resource, as one flat dict.

    A resource arrives as [ [{"a":1}, {"b":2}, [], ...], {"sub":...}, ... ]
    — one-key dicts nested in lists to any depth, with empty lists as padding.
    Every dict found is merged in; later keys do not overwrite earlier ones,
    because the metadata block comes first and is the authoritative one.
    """
    out: dict = {}

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                out.setdefault(k, v)
        elif isinstance(x, list):
            for item in x:
                walk(item)

    walk(node)
    return out


def _items(collection: Any, key: str) -> list:
    """The members of a Yahoo collection, in order.

    {"0": {"team": [...]}, "1": {"team": [...]}, "count": 2} -> [[...], [...]]
    Also tolerates a plain list of {"team": ...}, which Yahoo uses in places.
    """
    if isinstance(collection, list):
        return [c[key] for c in collection if isinstance(c, dict) and key in c]
    if not isinstance(collection, dict):
        return []
    numbered = []
    for k, v in collection.items():
        if k.isdigit() and isinstance(v, dict) and key in v:
            numbered.append((int(k), v[key]))
    return [v for _, v in sorted(numbered)]


def _num(value, default: float = 0.0) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return default


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _raw_id(key: str) -> str:
    """"461.p.30123" -> "30123", "461.l.12345.t.3" -> "3"."""
    return str(key or "").rsplit(".", 1)[-1]


def _points(block: Any) -> float:
    """team_points / player_points: {"coverage_type": "week", "total": "12.3"}."""
    if isinstance(block, dict):
        return _num(block.get("total"))
    return 0.0


def _team_abbr(value) -> Optional[str]:
    abbr = str(value or "").strip().upper()
    if not abbr:
        return None
    return _TEAM_FIX.get(abbr, abbr)


def _injury(status) -> Optional[str]:
    code = str(status or "").strip().upper()
    if not code:
        return None
    return _INJURY_LABELS.get(code, None)


def _ordinal(n) -> str:
    n = _int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _possessive(name: str) -> str:
    name = (name or "").strip()
    return f"{name}'" if name.lower().endswith("s") else f"{name}'s"


def _stat_values(block: Any) -> dict[str, float]:
    """player_stats -> {stat_id: value}. Values arrive as strings."""
    if not isinstance(block, dict):
        return {}
    out: dict[str, float] = {}
    for row in block.get("stats") or []:
        stat = (row or {}).get("stat") if isinstance(row, dict) else None
        if not isinstance(stat, dict):
            continue
        try:
            out[str(stat.get("stat_id"))] = float(stat.get("value"))
        except (TypeError, ValueError):
            continue
    return out


def _stat_line(values: dict[str, float]) -> Optional[StatLine]:
    if not values:
        return None

    def count(stat_id):
        v = values.get(stat_id)
        return int(round(v)) if v else None

    return StatLine(
        pass_td=count(STAT_PASS_TD),
        rush_td=count(STAT_RUSH_TD),
        rec_td=count(STAT_REC_TD),
        interceptions=count(STAT_INT),
        fumbles_lost=count(STAT_FUM_LOST),
    )


def _league_status(meta: dict) -> str:
    draft = str(meta.get("draft_status") or "").lower()
    if draft == "predraft":
        return "pre_draft"
    if draft == "draft":
        return "drafting"
    if str(meta.get("is_finished") or "") in ("1", "true", "True"):
        return "complete"
    return "in_season"


def _scoring_type(modifiers: dict[str, float]) -> str:
    rec = modifiers.get(STAT_RECEPTION, 0.0)
    if rec >= 1.0:
        return "ppr"
    if rec >= 0.5:
        return "half_ppr"
    return "std"


def _roster_slots(settings: dict) -> list[str]:
    slots: list[str] = []
    for row in settings.get("roster_positions") or []:
        pos = (row or {}).get("roster_position") if isinstance(row, dict) else None
        if not isinstance(pos, dict):
            continue
        slot = SLOT_MAP.get(str(pos.get("position") or ""), None)
        if slot:
            slots.extend([slot] * max(_int(pos.get("count"), 1), 0))
    return slots


def _modifiers(settings: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    block = settings.get("stat_modifiers") or {}
    for row in block.get("stats") or []:
        stat = (row or {}).get("stat") if isinstance(row, dict) else None
        if isinstance(stat, dict):
            try:
                out[str(stat.get("stat_id"))] = float(stat.get("value"))
            except (TypeError, ValueError):
                continue
    return out


def _previous_key(meta: dict) -> Optional[str]:
    """`renew` is "<game_key>_<league_id>" of last season's league, or empty."""
    renew = str(meta.get("renew") or "").strip()
    if "_" not in renew:
        return None
    game, league = renew.split("_", 1)
    if not (game.isdigit() and league.isdigit()):
        return None
    return f"{game}.l.{league}"


def _manager(team: dict) -> Manager:
    managers = team.get("managers") or []
    first = {}
    for m in managers:
        if isinstance(m, dict) and isinstance(m.get("manager"), dict):
            first = m["manager"]
            break
    return Manager(
        manager_id=str(first.get("guid") or first.get("manager_id") or ""),
        display_name=str(first.get("nickname") or "").strip() or "Manager",
        avatar_url=first.get("image_url") or None,
        is_commissioner=str(first.get("is_commissioner") or "") in ("1", "true"),
    )


def _team_logo(team: dict) -> Optional[str]:
    for row in team.get("team_logos") or []:
        logo = (row or {}).get("team_logo") if isinstance(row, dict) else None
        if isinstance(logo, dict) and logo.get("url"):
            return logo["url"]
    return None


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------

class YahooProvider(FantasyProvider):
    name = "yahoo"
    display_name = "Yahoo"
    supports_public_leagues = False
    supports_projections = False

    #: False until a real league has been parsed and its starters reconcile to
    #: Yahoo's own team_points for every team. Until then the connect page
    #: shows Yahoo to staff accounts only. See the module docstring.
    implemented = False

    def __init__(self, cache=None, access_token: str | None = None, **_ignored):
        super().__init__(cache=cache)
        self._access_token = access_token
        self._memo: dict[str, Any] = {}
        self._memo_lock = threading.Lock()

    # -- transport ---------------------------------------------------------

    def _token_for(self, league_key: Optional[str]) -> str:
        if self._access_token:
            return self._access_token
        if _token_source and league_key:
            token = _token_source(league_key)
            if token:
                return token
        raise AuthRequired(
            "We've lost access to this Yahoo league. Sign in with Yahoo again "
            "from the Connect page and it will pick up where it left off.")

    def _get(self, path: str, league_key: Optional[str] = None) -> dict:
        """One request. `path` is everything after /fantasy/v2/."""
        token = self._token_for(league_key)
        url = f"{BASE_URL}/{path.lstrip('/')}"
        try:
            response = requests.get(
                url, params={"format": "json"},
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Yahoo request failed: {exc}") from exc

        if response.status_code == 401:
            raise AuthRequired(
                "Yahoo didn't accept our sign-in for this league. Sign in "
                "with Yahoo again from the Connect page.")
        if response.status_code == 403:
            raise AuthRequired(
                "That Yahoo account isn't in this league, so Yahoo won't "
                "share it. Sign in with an account that's in the league.")
        if response.status_code == 404:
            raise LeagueNotFound("Yahoo has no league by that key.")
        if response.status_code == 429 or response.status_code == 999:
            # 999 is Yahoo's own "request denied" throttle code.
            raise ProviderError("Yahoo is rate limiting us. Try again shortly.")
        if response.status_code >= 400:
            # Yahoo reports an unknown or unreadable league as a 400 with a
            # description, as often as a 404.
            raise LeagueNotFound(
                f"Yahoo couldn't find that ({response.status_code}).")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("Yahoo returned something that wasn't JSON.") from exc
        content = (payload or {}).get("fantasy_content")
        if not isinstance(content, dict):
            raise ProviderError("Yahoo's reply had no fantasy content in it.")
        return content

    def _cached(self, key: str, ttl: int, fetch: Callable[[], Any]) -> Any:
        """Per-instance memo in front of the disk cache.

        The disk cache is keyed by league, not by user. That is safe only
        because nothing reaches a league here without first being tied to an
        account Yahoo says is IN it: the connect route refuses any league key
        that isn't in the signed-in user's own list.
        """
        with self._memo_lock:
            if key in self._memo:
                return self._memo[key]
        value = self.cache.get_or_fetch(key, ttl, fetch)
        with self._memo_lock:
            self._memo[key] = value
        return value

    def raw(self, path: str, league_key: Optional[str] = None) -> dict:
        """Uncached passthrough, for the staff diagnostic page."""
        return self._get(path, league_key)

    # -- league ------------------------------------------------------------

    def _league_resource(self, league_key: str, sub: str) -> dict:
        content = self._cached(
            f"league:{league_key}:{sub}", TTL_LEAGUE,
            lambda: self._get(f"league/{league_key}/{sub}", league_key))
        return _merge((content or {}).get("league"))

    def get_league(self, league_id: str, season: Optional[int] = None) -> League:
        league_key = str(league_id).strip()
        if ".l." not in league_key:
            raise LeagueNotFound(
                "A Yahoo league key looks like 461.l.12345 — pick your "
                "league from the list instead of typing it.")
        merged = self._league_resource(league_key, "settings")
        if not merged.get("league_key"):
            raise LeagueNotFound("Yahoo has no league by that key.")

        settings = _merge(merged.get("settings"))
        modifiers = _modifiers(settings)

        return League(
            provider=self.name,
            league_id=league_key,
            name=str(merged.get("name") or "").strip() or "Yahoo league",
            season=_int(merged.get("season"), season or 0),
            roster_slots=_roster_slots(settings),
            team_count=_int(merged.get("num_teams")),
            scoring_type=_scoring_type(modifiers),
            avatar_url=merged.get("logo_url") or None,
            previous_league_id=_previous_key(merged),
            commissioner_ids=[],
            status=_league_status(merged),
        )

    def stat_modifiers(self, league_key: str) -> dict[str, float]:
        """The league's scoring, stat id -> points. For the diagnostic."""
        merged = self._league_resource(league_key, "settings")
        return _modifiers(_merge(merged.get("settings")))

    def available_weeks(self, league_id: str, season: int) -> list[int]:
        """Weeks with final results.

        `current_week` is the week in progress, so everything before it is
        done — unless the season is over, when every week up to end_week is.
        """
        try:
            merged = self._league_resource(str(league_id), "metadata")
        except ProviderError:
            return []
        if _league_status(merged) in ("pre_draft", "drafting"):
            return []
        start = max(_int(merged.get("start_week"), 1), 1)
        if _league_status(merged) == "complete":
            last = _int(merged.get("end_week"))
        else:
            last = _int(merged.get("current_week")) - 1
        return list(range(start, last + 1)) if last >= start else []

    def season_chain(self, league_id: str, max_hops: int = 10) -> list:
        chain: list[League] = []
        seen: set[str] = set()
        current: Optional[str] = str(league_id)
        while current and current not in seen and len(chain) < max_hops:
            seen.add(current)
            league = self.describe_league(current)
            if not league:
                break
            chain.append(league)
            current = league.previous_league_id
        return chain

    # -- the signed-in user's leagues --------------------------------------

    def user_leagues(self, user_id: str = "", season: int = 0) -> list:
        """Every NFL league the signed-in Yahoo account is in, this season.

        `user_id` is ignored: Yahoo answers for whoever owns the token
        (`use_login=1`). `game_keys=nfl` is Yahoo's alias for the current NFL
        season's game.
        """
        content = self._get("users;use_login=1/games;game_keys=nfl/leagues")
        leagues: list[League] = []
        for user in _items(content.get("users"), "user"):
            for game in _items(_merge(user).get("games"), "game"):
                game_merged = _merge(game)
                for league in _items(game_merged.get("leagues"), "league"):
                    meta = _merge(league)
                    key = meta.get("league_key")
                    if not key:
                        continue
                    leagues.append(League(
                        provider=self.name,
                        league_id=str(key),
                        name=str(meta.get("name") or "").strip() or "Yahoo league",
                        season=_int(meta.get("season") or game_merged.get("season")),
                        team_count=_int(meta.get("num_teams")),
                        avatar_url=meta.get("logo_url") or None,
                        previous_league_id=_previous_key(meta),
                        status=_league_status(meta),
                    ))
        leagues.sort(key=lambda l: l.season, reverse=True)
        return leagues

    # -- a week ------------------------------------------------------------

    def _scoreboard(self, league_key: str, week: int) -> list[dict]:
        """The week's matchups, each merged, with its two teams merged."""
        content = self._cached(
            f"scoreboard:{league_key}:{week}", TTL_WEEK,
            lambda: self._get(f"league/{league_key}/scoreboard;week={week}",
                              league_key))
        merged = _merge((content or {}).get("league"))
        board = merged.get("scoreboard") or {}
        if isinstance(board, list):
            board = _merge(board)
        listing = board.get("matchups") or (board.get("0") or {}).get("matchups")
        matchups = []
        for m in _items(listing, "matchup"):
            mm = _merge(m)
            teams = [_merge(t) for t in _items(mm.get("teams"), "team")]
            # The team list sometimes sits under a "0" key inside the matchup.
            if not teams and isinstance(m, dict):
                inner = m.get("0") or {}
                teams = [_merge(t) for t in _items(inner.get("teams"), "team")]
            mm["_teams"] = teams
            matchups.append(mm)
        return matchups

    def _roster(self, league_key: str, team_key: str, week: int) -> list[dict]:
        """One team's players for a week, each merged, with stats and points."""
        content = self._cached(
            f"roster:{team_key}:{week}", TTL_WEEK,
            lambda: self._get(
                f"team/{team_key}/roster;week={week}"
                f"/players/stats;type=week;week={week}", league_key))
        team = _merge((content or {}).get("team"))
        roster = team.get("roster") or {}
        if isinstance(roster, list):
            roster = _merge(roster)
        players = roster.get("players") or (roster.get("0") or {}).get("players")
        return [_merge(p) for p in _items(players, "player")]

    def _player_line(self, p: dict) -> Optional[PlayerLine]:
        player_id = p.get("player_id") or _raw_id(p.get("player_key"))
        if not player_id:
            return None

        selected = _merge(p.get("selected_position"))
        slot_raw = str(selected.get("position") or "BN")
        slot = SLOT_MAP.get(slot_raw, "BN" if slot_raw in BENCH_POSITIONS else slot_raw)

        name = p.get("name") or {}
        full = (name.get("full") if isinstance(name, dict) else str(name)) or ""

        positions = []
        for row in p.get("eligible_positions") or []:
            pos = (row or {}).get("position") if isinstance(row, dict) else None
            if pos and pos not in BENCH_POSITIONS and pos in SLOT_MAP:
                canon = SLOT_MAP[pos]
                if canon in ("QB", "RB", "WR", "TE", "K", "DEF",
                             "DL", "LB", "DB") and canon not in positions:
                    positions.append(canon)

        primary = str(p.get("primary_position") or p.get("display_position")
                      or "").split(",")[0].strip()
        position = SLOT_MAP.get(primary, primary) if primary else "FLEX"
        if position not in ("QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB"):
            position = positions[0] if positions else "FLEX"

        headshot = p.get("image_url") or (p.get("headshot") or {}).get("url")

        return PlayerLine(
            player_id=self.namespaced_id(player_id),
            name=full.strip() or str(player_id),
            position=position,
            points=_points(p.get("player_points")),
            slot=slot,
            positions=tuple(positions) or (position,),
            nfl_team=_team_abbr(p.get("editorial_team_abbr")),
            projected=None,
            headshot_url=headshot or None,
            injury_status=_injury(p.get("status")),
            stats=_stat_line(_stat_values(p.get("player_stats"))),
        )

    def _records_entering_week(self, league_key: str, week: int,
                               start_week: int) -> dict[str, dict]:
        """Records BEFORE the week being reported, replayed from scoreboards.

        Yahoo's standings include the week we are about to narrate, same as
        ESPN's — so earlier weeks are replayed instead. One request per past
        week, cached for ten minutes.
        """
        records: dict[str, dict] = {}
        for w in range(max(start_week, 1), week):
            try:
                board = self._scoreboard(league_key, w)
            except WeekNotAvailable:
                continue
            for m in board:
                teams = m.get("_teams") or []
                if len(teams) != 2:
                    continue
                if str(m.get("status") or "") not in ("postevent", ""):
                    continue
                a, b = teams
                pa, pb = _points(a.get("team_points")), _points(b.get("team_points"))
                for t in (a, b):
                    records.setdefault(str(t.get("team_key")),
                                       {"wins": 0, "losses": 0, "ties": 0})
                ka, kb = str(a.get("team_key")), str(b.get("team_key"))
                winner = str(m.get("winner_team_key") or "")
                tied = str(m.get("is_tied") or "0") in ("1", "true")
                if tied or (not winner and pa == pb):
                    records[ka]["ties"] += 1
                    records[kb]["ties"] += 1
                elif winner == ka or (not winner and pa > pb):
                    records[ka]["wins"] += 1
                    records[kb]["losses"] += 1
                else:
                    records[kb]["wins"] += 1
                    records[ka]["losses"] += 1
        return records

    def get_week(self, league_id: str, season: int, week: int) -> WeekData:
        league_key = str(league_id)
        league = self.get_league(league_key, season)
        week = int(week)

        if not league.has_drafted:
            raise WeekNotAvailable(
                "This league hasn't drafted yet, so there are no results to "
                "write up.")

        board = self._scoreboard(league_key, week)
        if not board:
            raise WeekNotAvailable(f"Yahoo has no week {week} for this league yet.")

        meta = self._league_resource(league_key, "metadata")
        records = self._records_entering_week(
            league_key, week, _int(meta.get("start_week"), 1))

        def build(team: dict) -> Team:
            team_key = str(team.get("team_key"))
            lineup: list[PlayerLine] = []
            bench: list[PlayerLine] = []
            for p in self._roster(league_key, team_key, week):
                line = self._player_line(p)
                if line is None:
                    continue
                (bench if line.slot in ("BN", "IR", "TAXI") else lineup).append(line)
            rec = records.get(team_key, {})
            return Team(
                team_id=team_key,
                team_name=str(team.get("name") or "").strip() or "Unnamed team",
                manager=_manager(team),
                points=_points(team.get("team_points")),
                lineup=lineup,
                bench=bench,
                wins=rec.get("wins", 0),
                losses=rec.get("losses", 0),
                ties=rec.get("ties", 0),
            )

        matchups: list[Matchup] = []
        byes: list[Team] = []
        for i, m in enumerate(board):
            teams = [build(t) for t in m.get("_teams") or []]
            if len(teams) == 2:
                matchups.append(Matchup(matchup_id=str(i + 1),
                                        teams=(teams[0], teams[1])))
            else:
                byes.extend(teams)

        if not matchups:
            raise WeekNotAvailable(f"Yahoo has no week {week} for this league yet.")
        return WeekData(league=league, week=week, matchups=matchups, byes=byes)

    # -- draft -------------------------------------------------------------

    def draft_picks(self, league_id: str, season: int) -> dict:
        try:
            merged = self._league_resource(str(league_id), "draftresults")
        except ProviderError:
            return {}
        out = {}
        for row in _items(merged.get("draft_results"), "draft_result"):
            pick = _merge(row)
            pid = _raw_id(pick.get("player_key"))
            if pid:
                out[pid] = {"round": _int(pick.get("round")),
                            "overall": _int(pick.get("pick"))}
        return out

    # -- transactions ------------------------------------------------------

    def get_transactions(self, league_id: str, season: int, week: int) -> list:
        """Adds, drops and trades. The seven-day window is applied upstream.

        Yahoo lists completed moves only (status "successful"); a vetoed or
        pending trade is skipped, and failed waiver claims are not published.
        """
        league_key = str(league_id)
        content = self._get(
            f"league/{league_key}/transactions;types=add,drop,trade", league_key)
        merged = _merge((content or {}).get("league"))

        team_names: dict[str, str] = {}
        try:
            for m in self._scoreboard(league_key, int(week)):
                for t in m.get("_teams") or []:
                    team_names[str(t.get("team_key"))] = str(t.get("name") or "")
        except ProviderError:
            pass

        def team(key, fallback=""):
            return team_names.get(str(key or "")) or fallback or "A team"

        out: list[Transaction] = []
        for row in _items(merged.get("transactions"), "transaction"):
            tx = _merge(row)
            status = str(tx.get("status") or "").lower()
            if status != "successful":
                continue
            kind_raw = str(tx.get("type") or "").lower()

            adds: list[tuple[str, TransactionPlayer]] = []
            drops: list[tuple[str, TransactionPlayer]] = []
            source_kinds: set[str] = set()
            involved: list[str] = []

            for pl in _items(tx.get("players"), "player"):
                p = _merge(pl)
                data = p.get("transaction_data")
                if isinstance(data, list):
                    data = _merge(data)
                data = data if isinstance(data, dict) else {}
                name = p.get("name") or {}
                person = TransactionPlayer(
                    player_id=self.namespaced_id(p.get("player_id")
                                                 or _raw_id(p.get("player_key"))),
                    name=str((name.get("full") if isinstance(name, dict) else name)
                             or "Unknown player"),
                    position=str(p.get("display_position") or "") or None,
                    nfl_team=_team_abbr(p.get("editorial_team_abbr")),
                )
                move = str(data.get("type") or "").lower()
                if move in ("add", "trade"):
                    dest = team(data.get("destination_team_key"),
                                data.get("destination_team_name"))
                    adds.append((dest, person))
                    if dest not in involved:
                        involved.append(dest)
                    source_kinds.add(str(data.get("source_type") or ""))
                    if move == "trade":
                        src = team(data.get("source_team_key"),
                                   data.get("source_team_name"))
                        drops.append((src, person))
                        if src not in involved:
                            involved.append(src)
                elif move == "drop":
                    src = team(data.get("source_team_key"),
                               data.get("source_team_name"))
                    drops.append((src, person))
                    if src not in involved:
                        involved.append(src)

            picks: list[tuple[str, str]] = []
            for pk in tx.get("picks") or []:
                pick = (pk or {}).get("pick") if isinstance(pk, dict) else None
                if not isinstance(pick, dict):
                    continue
                label = f"{_ordinal(pick.get('round'))}-round pick"
                original = pick.get("original_team_key")
                if original and original != pick.get("source_team_key"):
                    label += f" ({_possessive(team(original, pick.get('original_team_name')))})"
                picks.append((team(pick.get("destination_team_key"),
                                   pick.get("destination_team_name")), label))

            if kind_raw == "trade":
                kind = "trade"
                for key in (tx.get("trader_team_key"), tx.get("tradee_team_key")):
                    name = team(key, "")
                    if key and name not in involved:
                        involved.append(name)
            elif "waivers" in source_kinds:
                kind = "waiver"
            else:
                kind = "free_agent"

            if not (adds or drops or picks):
                continue

            bid = tx.get("faab_bid")
            ts = _int(tx.get("timestamp"))
            out.append(Transaction(
                kind=kind,
                status="complete",
                week=int(week),
                teams=involved,
                adds=adds,
                drops=drops,
                bid=_int(bid) if bid not in (None, "") and kind == "waiver" else None,
                created=ts * 1000 if ts else None,
                picks=picks,
            ))
        return out


def reconcile(week: WeekData) -> list[dict]:
    """Per team: do the starters add up to Yahoo's own score?

    The check that validated ESPN, and the one that flips `implemented`.
    Nothing else confirms the slot map, the bench rule and the points field
    all at once.
    """
    rows = []
    for t in week.teams:
        starters = round(sum(p.points for p in t.lineup), 2)
        rows.append({
            "team": t.team_name,
            "yahoo": t.points,
            "starters": starters,
            "ok": abs(starters - t.points) < 0.011,
            "n_starters": len(t.lineup),
            "n_bench": len(t.bench),
        })
    return rows


def token_fingerprint(token: str) -> str:
    """For logs: enough to tell two tokens apart, useless to anyone else."""
    return hashlib.sha256((token or "").encode()).hexdigest()[:8]
