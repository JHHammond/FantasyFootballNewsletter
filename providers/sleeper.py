"""
Sleeper adapter.

Sleeper's read API is public and unauthenticated, which is why it's the easiest
platform to support and the right one to have built first.

Notable fixes this makes over the original fetch_data.py:

  * The ~5MB /players/nfl blob is cached for 24h instead of being re-downloaded
    on every generation. Sleeper explicitly asks for once-per-day, max.
  * Records are computed by replaying every prior week's matchups rather than
    trusting roster["settings"]["wins"], which on Sleeper already includes the
    week being reported on. That made every "upset" comparison wrong, and the
    old wins+losses > current_week guard silently zeroed real records.
  * Player points that come back as JSON null are coerced to 0.0 instead of
    flowing downstream as None and blowing up the first comparison that touches
    them.
  * Starters are read from `starters_points`, which is positionally aligned with
    `starters`, rather than re-looked-up out of the players_points dict.
"""

from __future__ import annotations

import requests

from .base import FantasyProvider, LeagueNotFound, ProviderError, WeekNotAvailable
from .cache import (
    TTL_FINAL_SCORES,
    TTL_LEAGUE_META,
    TTL_LIVE_SCORES,
    TTL_PLAYER_INDEX,
    TTL_PROJECTIONS,
)
from .models import (
    BENCH_SLOTS,
    League,
    Manager,
    Matchup,
    PlayerLine,
    Team,
    WeekData,
)

BASE_URL = "https://api.sleeper.app/v1"
PROJECTIONS_URL = "https://api.sleeper.app/projections/nfl"
AVATAR_URL = "https://sleepercdn.com/avatars"
HEADSHOT_URL = "https://sleepercdn.com/content/nfl/players"
TEAM_LOGO_URL = "https://sleepercdn.com/images/team_logos/nfl"

REQUEST_TIMEOUT = 20

#: Sleeper slot name -> our canonical slot name.
SLOT_MAP = {
    "QB": "QB",
    "RB": "RB",
    "WR": "WR",
    "TE": "TE",
    "K": "K",
    "DEF": "DEF",
    "DL": "DL",
    "LB": "LB",
    "DB": "DB",
    "FLEX": "FLEX",
    "WRRB_FLEX": "WR_RB_FLEX",
    "REC_FLEX": "REC_FLEX",
    "SUPER_FLEX": "SUPER_FLEX",
    "IDP_FLEX": "IDP_FLEX",
    "BN": "BN",
    "IR": "IR",
    "TAXI": "TAXI",
}

#: Sleeper marks an empty starting slot with either of these.
EMPTY_SLOT_MARKERS = {None, "", "0", 0}


def _to_float(value, default: float = 0.0) -> float:
    """Sleeper returns JSON null for players who didn't play. Treat as zero."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class SleeperProvider(FantasyProvider):
    name = "sleeper"
    display_name = "Sleeper"
    supports_public_leagues = True
    supports_projections = True

    # -- HTTP --------------------------------------------------------------

    def _get(self, url: str, params: dict | None = None):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"Sleeper request failed: {exc}") from exc

        if response.status_code == 404:
            raise LeagueNotFound(f"Sleeper returned 404 for {url}")
        if response.status_code == 429:
            raise ProviderError(
                "Sleeper rate limited us. The player index is cached for 24h; "
                "if you're seeing this often, something is bypassing the cache."
            )
        response.raise_for_status()
        return response.json()

    # -- reference data ----------------------------------------------------

    def _player_index(self) -> dict:
        """The full NFL player database, cached for a day."""
        return self.cache.get_or_fetch(
            "players:nfl",
            TTL_PLAYER_INDEX,
            lambda: self._get(f"{BASE_URL}/players/nfl"),
        )

    def _projections(self, season: int, week: int) -> dict:
        """Weekly projections keyed by raw Sleeper player ID."""

        def fetch():
            raw = self._get(
                f"{PROJECTIONS_URL}/{season}/{week}",
                params={
                    "season_type": "regular",
                    "position[]": ["QB", "RB", "WR", "TE", "K", "DEF"],
                },
            )
            # Sleeper has returned both a list and a dict from this endpoint
            # at different times. Normalize to dict-keyed-by-player-id.
            if isinstance(raw, list):
                out = {}
                for item in raw:
                    stats = item.get("stats") or {}
                    pid = item.get("player_id") or stats.get("player_id")
                    if pid:
                        merged = {**stats, **{k: v for k, v in item.items() if k != "stats"}}
                        out[str(pid)] = merged
                return out
            return raw or {}

        return self.cache.get_or_fetch(
            f"projections:{season}:{week}", TTL_PROJECTIONS, fetch
        )

    def _matchups_raw(self, league_id: str, week: int, is_past: bool) -> list:
        """Raw matchup rows. Completed weeks are cached hard; live ones briefly."""
        ttl = TTL_FINAL_SCORES if is_past else TTL_LIVE_SCORES
        return self.cache.get_or_fetch(
            f"matchups:{league_id}:{week}",
            ttl,
            lambda: self._get(f"{BASE_URL}/league/{league_id}/matchups/{week}"),
        ) or []

    def _projected_points(self, projections: dict, raw_pid: str, scoring_type: str):
        entry = projections.get(str(raw_pid))
        if not entry:
            return None
        key = {"ppr": "pts_ppr", "half_ppr": "pts_half_ppr", "std": "pts_std"}.get(
            scoring_type, "pts_ppr"
        )
        value = (
            entry.get(key)
            or entry.get("pts_ppr")
            or entry.get("pts_half_ppr")
            or entry.get("pts_std")
        )
        return round(float(value), 2) if value is not None else None

    def _build_player(
        self, raw_pid, slot: str, points, players_index: dict,
        projections: dict, scoring_type: str,
    ) -> PlayerLine:
        """Turn one Sleeper player ID into a normalized PlayerLine."""
        if raw_pid in EMPTY_SLOT_MARKERS:
            # An empty starting slot. Keep it in the lineup so empty_slots and
            # the optimizer can both see the hole.
            return PlayerLine(
                player_id="", name="(empty)", position="?", points=0.0, slot=slot
            )

        raw_pid = str(raw_pid)
        meta = players_index.get(raw_pid) or {}
        positions = tuple(meta.get("fantasy_positions") or [])
        position = positions[0] if positions else "?"

        first = meta.get("first_name") or ""
        last = meta.get("last_name") or ""
        name = f"{first} {last}".strip() or meta.get("last_name") or raw_pid

        # Team defenses are keyed by team abbreviation, and have no real name.
        if position == "DEF":
            name = f"{meta.get('team') or raw_pid} Defense"
            headshot = f"{TEAM_LOGO_URL}/{raw_pid.lower()}.png"
        else:
            headshot = f"{HEADSHOT_URL}/{raw_pid}.jpg"

        return PlayerLine(
            player_id=self.namespaced_id(raw_pid),
            name=name,
            position=position,
            positions=positions,
            points=round(_to_float(points), 2),
            slot=slot,
            nfl_team=meta.get("team"),
            projected=self._projected_points(projections, raw_pid, scoring_type),
            headshot_url=headshot,
            injury_status=meta.get("injury_status"),
        )

    # -- records -----------------------------------------------------------

    def _records_entering_week(self, league_id: str, week: int) -> dict[str, dict]:
        """Replay weeks 1..week-1 to get each roster's record at kickoff.

        Sleeper's roster["settings"]["wins"] already reflects the week you're
        reporting on, so using it means every team's "record" silently includes
        the result you're about to narrate. Replaying prior weeks is a few extra
        (cached, permanently) requests and is simply correct.
        """
        records: dict[str, dict] = {}

        for prior_week in range(1, week):
            rows = self._matchups_raw(league_id, prior_week, is_past=True)

            grouped: dict = {}
            for row in rows:
                mid = row.get("matchup_id")
                if mid is None:
                    continue  # bye or unscheduled
                grouped.setdefault(mid, []).append(row)

            for pair in grouped.values():
                if len(pair) != 2:
                    continue
                a, b = pair
                a_id, b_id = str(a["roster_id"]), str(b["roster_id"])
                a_pts = _to_float(a.get("custom_points") or a.get("points"))
                b_pts = _to_float(b.get("custom_points") or b.get("points"))

                for rid in (a_id, b_id):
                    records.setdefault(rid, {"wins": 0, "losses": 0, "ties": 0})

                if a_pts > b_pts:
                    records[a_id]["wins"] += 1
                    records[b_id]["losses"] += 1
                elif b_pts > a_pts:
                    records[b_id]["wins"] += 1
                    records[a_id]["losses"] += 1
                else:
                    records[a_id]["ties"] += 1
                    records[b_id]["ties"] += 1

        return records

    # -- public API --------------------------------------------------------

    def get_league(self, league_id: str, season: int) -> League:
        data = self.cache.get_or_fetch(
            f"league:{league_id}",
            TTL_LEAGUE_META,
            lambda: self._get(f"{BASE_URL}/league/{league_id}"),
        )
        if not data:
            raise LeagueNotFound(f"No Sleeper league with ID {league_id}")

        scoring = data.get("scoring_settings") or {}
        rec = _to_float(scoring.get("rec"), 0.0)
        scoring_type = "ppr" if rec >= 1.0 else "half_ppr" if rec >= 0.5 else "std"

        slots = [SLOT_MAP.get(s, s) for s in (data.get("roster_positions") or [])]

        users = self._get(f"{BASE_URL}/league/{league_id}/users") or []
        commissioners = [u["user_id"] for u in users if u.get("is_owner")]

        avatar = data.get("avatar")
        return League(
            provider=self.name,
            league_id=str(league_id),
            name=data.get("name") or "Fantasy League",
            season=int(data.get("season") or season),
            roster_slots=slots,
            team_count=int(data.get("total_rosters") or 0),
            scoring_type=scoring_type,
            avatar_url=f"{AVATAR_URL}/{avatar}" if avatar else None,
            previous_league_id=data.get("previous_league_id"),
            commissioner_ids=commissioners,
        )

    def get_week(self, league_id: str, season: int, week: int) -> WeekData:
        league = self.get_league(league_id, season)

        users = self._get(f"{BASE_URL}/league/{league_id}/users") or []
        rosters = self._get(f"{BASE_URL}/league/{league_id}/rosters") or []
        matchup_rows = self._matchups_raw(league_id, week, is_past=False)

        if not matchup_rows:
            raise WeekNotAvailable(
                f"Sleeper has no matchup data for league {league_id} week {week}. "
                "The week may not have started yet."
            )

        players_index = self._player_index()
        projections = self._projections(season, week) if self.supports_projections else {}
        records = self._records_entering_week(league_id, week)

        # user_id -> Manager
        managers: dict[str, Manager] = {}
        for u in users:
            avatar = u.get("avatar")
            managers[u["user_id"]] = Manager(
                manager_id=u["user_id"],
                display_name=u.get("display_name") or "Unknown Manager",
                avatar_url=f"{AVATAR_URL}/{avatar}" if avatar else None,
                is_commissioner=u["user_id"] in league.commissioner_ids,
            )

        # roster_id -> (team_name, Manager)
        roster_meta: dict[str, tuple[str, Manager]] = {}
        for r in rosters:
            rid = str(r["roster_id"])
            manager = managers.get(
                r.get("owner_id"),
                Manager(manager_id=r.get("owner_id") or rid, display_name="Unknown Manager"),
            )
            meta = r.get("metadata") or {}
            roster_meta[rid] = (meta.get("team_name") or manager.display_name, manager)

        starting_slots = [s for s in league.roster_slots if s not in BENCH_SLOTS]

        def build_team(row: dict) -> Team:
            rid = str(row["roster_id"])
            team_name, manager = roster_meta.get(rid, (f"Team {rid}", Manager(rid, "Unknown Manager")))
            record = records.get(rid, {"wins": 0, "losses": 0, "ties": 0})

            starters = row.get("starters") or []
            starters_points = row.get("starters_points") or []
            players_points = row.get("players_points") or {}

            lineup: list[PlayerLine] = []
            for i, raw_pid in enumerate(starters):
                # starters is positionally aligned with the non-bench slots.
                slot = starting_slots[i] if i < len(starting_slots) else "FLEX"
                if i < len(starters_points):
                    pts = starters_points[i]
                else:
                    pts = players_points.get(str(raw_pid))
                lineup.append(
                    self._build_player(
                        raw_pid, slot, pts, players_index, projections, league.scoring_type
                    )
                )

            starter_ids = {str(p) for p in starters if p not in EMPTY_SLOT_MARKERS}
            bench: list[PlayerLine] = []
            for raw_pid in (row.get("players") or []):
                if str(raw_pid) in starter_ids or raw_pid in EMPTY_SLOT_MARKERS:
                    continue
                bench.append(
                    self._build_player(
                        raw_pid, "BN", players_points.get(str(raw_pid)),
                        players_index, projections, league.scoring_type,
                    )
                )

            return Team(
                team_id=rid,
                team_name=team_name,
                manager=manager,
                points=round(_to_float(row.get("custom_points") or row.get("points")), 2),
                lineup=lineup,
                bench=bench,
                wins=record["wins"],
                losses=record["losses"],
                ties=record["ties"],
            )

        grouped: dict = {}
        byes: list[Team] = []
        for row in matchup_rows:
            mid = row.get("matchup_id")
            if mid is None:
                byes.append(build_team(row))
                continue
            grouped.setdefault(mid, []).append(row)

        matchups: list[Matchup] = []
        for mid, rows in sorted(grouped.items(), key=lambda kv: kv[0]):
            if len(rows) == 1:
                byes.append(build_team(rows[0]))
                continue
            if len(rows) != 2:
                # Shouldn't happen, but don't silently drop teams.
                for row in rows:
                    byes.append(build_team(row))
                continue
            matchups.append(
                Matchup(matchup_id=str(mid), teams=(build_team(rows[0]), build_team(rows[1])))
            )

        return WeekData(league=league, week=week, matchups=matchups, byes=byes)
