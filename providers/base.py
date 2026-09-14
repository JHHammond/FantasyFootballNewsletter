"""
The contract every fantasy platform adapter implements.

Adding a platform means writing one subclass of FantasyProvider and registering
it. Nothing else in the codebase should need to change -- if it does, something
platform-specific has leaked into the normalized model and belongs back here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .cache import TTLCache
from .models import League, WeekData


class ProviderError(Exception):
    """Base for anything a provider can fail at."""


class LeagueNotFound(ProviderError):
    """The league ID doesn't exist, or isn't visible to us."""


class AuthRequired(ProviderError):
    """The league is private and we have no valid credentials for it."""


class WeekNotAvailable(ProviderError):
    """That week hasn't been played, or the platform has no data for it yet."""


class FantasyProvider(ABC):
    """One fantasy platform, normalized."""

    #: Short lowercase identifier. Used as the key in the registry and as the
    #: namespace prefix on every player ID this provider emits.
    name: str = "abstract"

    #: Human-readable, for UI.
    display_name: str = "Abstract Provider"

    #: False for platforms where we can't read a league without user credentials.
    supports_public_leagues: bool = True

    #: False if the platform gives us no weekly projections -- the writer then
    #: has to skip every "beat/missed projection" storyline.
    supports_projections: bool = True

    def __init__(self, cache: Optional[TTLCache] = None, **credentials):
        self.cache = cache or TTLCache(namespace=self.name)
        self.credentials = credentials

    # -- required ----------------------------------------------------------

    @abstractmethod
    def get_league(self, league_id: str, season: Optional[int] = None) -> League:
        """Fetch league configuration: name, roster slots, scoring, team count.

        `season` is a fallback only. Every platform stores the season on the
        league itself, so the returned League.season is authoritative — asking
        a user to type it in is a way to generate wrong answers.

        Raises LeagueNotFound or AuthRequired.
        """

    @abstractmethod
    def get_week(self, league_id: str, season: int, week: int) -> WeekData:
        """Fetch one week of results, fully normalized.

        Every Team returned must carry its record ENTERING this week, its
        starting lineup with per-player points, and its bench. Implementations
        are responsible for pairing matchups and handling byes.

        Raises WeekNotAvailable if the week hasn't happened.
        """

    # -- optional ----------------------------------------------------------

    def verify_league(self, league_id: str, season: Optional[int] = None) -> Optional[str]:
        """Return the league's name if it exists and we can read it, else None."""
        league = self.describe_league(league_id, season)
        return league.name if league else None

    def describe_league(self, league_id: str, season: Optional[int] = None):
        """The full League, or None if it can't be read. Never raises."""
        try:
            return self.get_league(league_id, season)
        except ProviderError:
            return None

    def available_weeks(self, league_id: str, season: int) -> list[int]:
        """Weeks that actually have scored results. Empty if none do.

        The UI uses this so nobody can pick a week that cannot possibly work.
        Default implementation returns nothing; override where it's cheap.
        """
        return []

    def current_state(self) -> Optional[dict]:
        """The platform's own view of what week and season it is.

        Returns something like {"season": 2026, "week": 3} or None if the
        platform doesn't publish it. Callers must handle None — this is a
        convenience, and nfl_week.py answers the same question offline.
        """
        return None

    def find_user(self, username: str) -> Optional[dict]:
        """Resolve a display name to a stable account id, or None.

        Platforms that let a stranger look somebody up by name can implement
        this; it is what turns "paste the long number out of your league URL"
        into "type your username". Returns at least {"user_id", "username"}.

        Not every platform allows it. Yahoo needs OAuth before it will say
        anything at all, and ESPN has no public directory, so both leave this
        returning None rather than pretending.
        """
        return None

    def user_leagues(self, user_id: str, season: int) -> list:
        """Every league that account is in this season. Newest first.

        Returns League objects so the caller does not have to learn a second
        shape. Implementations should build these from whatever the listing
        endpoint already returns rather than fetching each league again — the
        point is one request, not one per league.
        """
        return []

    def season_chain(self, league_id: str, max_hops: int = 10) -> list:
        """This league and its predecessors, newest first.

        Platforms that roll a league over each year mint a NEW id every season
        and leave a pointer back to the old one. That matters enormously in the
        offseason: someone pastes the id they see today, it has no games in it,
        and their entire previous season is sitting one hop away.

        Returns a list of League objects. Default is just this league.
        """
        league = self.describe_league(league_id)
        return [league] if league else []

    def namespaced_id(self, raw_id: str | int) -> str:
        """Prefix a platform's player ID so IDs can never collide across platforms."""
        return f"{self.name}:{raw_id}"

    def clear_cache(self) -> int:
        return self.cache.clear()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
