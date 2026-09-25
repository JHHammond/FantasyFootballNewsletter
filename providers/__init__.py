"""
Fantasy platform providers.

Everything upstream of this package talks to one normalized data model, so
adding a platform is a matter of writing one adapter and registering it here.

Typical use:

    from providers import get_provider, apply_lineup_gaps, week_to_legacy_games

    provider  = get_provider("sleeper")
    week_data = provider.get_week(league_id, season=2025, week=1)
    week_data = apply_lineup_gaps(week_data)

    games = week_to_legacy_games(week_data)   # feeds the existing pipeline
"""

from __future__ import annotations

from .base import (
    AuthRequired,
    FantasyProvider,
    LeagueNotFound,
    ProviderError,
    WeekNotAvailable,
)
from .cache import TTLCache
from .compat import legacy_league_info, team_to_legacy, week_to_legacy_games
from .espn import ESPNProvider
from .models import (
    BENCH_SLOTS,
    SLOT_ELIGIBILITY,
    League,
    Manager,
    Matchup,
    PlayerLine,
    Team,
    Transaction,
    TransactionPlayer,
    WeekData,
    can_fill_slot,
    can_fill_slot_any,
    is_starting_slot,
)
from .optimizer import apply_lineup_gaps, optimal_lineup
from .sleeper import SleeperProvider
from .yahoo import YahooProvider

#: Every platform we know about. Keys are what callers pass to get_provider()
#: and what gets stored in the leagues table's `provider` column.
PROVIDERS: dict[str, type[FantasyProvider]] = {
    SleeperProvider.name: SleeperProvider,
    ESPNProvider.name: ESPNProvider,
    YahooProvider.name: YahooProvider,
}


def get_provider(name: str, **kwargs) -> FantasyProvider:
    """Instantiate a provider by name.

    kwargs are passed through to the provider's constructor, which is where
    per-platform credentials go (ESPN's espn_s2/swid, Yahoo's OAuth token).
    """
    key = (name or "").strip().lower()
    try:
        provider_class = PROVIDERS[key]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown provider {name!r}. Known providers: {known}") from None
    return provider_class(**kwargs)


def available_providers() -> list[dict]:
    """Provider metadata, for populating a platform picker in the UI."""
    return [
        {
            "name": cls.name,
            "display_name": cls.display_name,
            "supports_public_leagues": cls.supports_public_leagues,
            "supports_projections": cls.supports_projections,
            "implemented": cls.implemented,
        }
        for cls in PROVIDERS.values()
    ]


#: How much of a week's roster movement belongs in a weekly paper.
#:
#: Seven days, and the reason is week 1. ESPN files EVERY move made before the
#: season under scoringPeriodId 1 — the whole offseason, every preseason cut,
#: months of it. The Hands Times week 1 printed NINETY transactions across
#: four pages, including people dropping players in July. Filtering by the
#: platform's own week number is not a filter at all in week 1.
TRANSACTION_WINDOW_DAYS = 7


def transaction_window(season: int, week: int) -> tuple[int, int]:
    """(start, end) in epoch milliseconds for the moves week N should carry.

    Anchored to the WEEK, not to now. Anchoring to now would be simpler and
    would break the archive: a paper re-renders every time it is edited, so a
    week 1 paper opened and corrected in December would fetch its transactions
    against a December window and come back empty. The same trap the
    classifieds page has already been through.
    """
    from datetime import datetime, timedelta, timezone

    from nfl_week import season_opener

    # Week N ends N weeks after the season's Thursday opener. Its window is
    # the seven days before that, which is Thursday to Wednesday — the span a
    # manager thinks of as "this week", waiver processing included.
    end = (datetime.combine(season_opener(int(season)),
                            datetime.min.time(), tzinfo=timezone.utc)
           + timedelta(days=7 * int(week)))
    start = end - timedelta(days=TRANSACTION_WINDOW_DAYS)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def load_transactions(
    provider_name: str,
    league_id: str,
    season: int,
    week: int,
    **provider_kwargs,
) -> list:
    """This week's roster moves, or an empty list.

    Never raises. The transactions section is a bonus on top of the paper, and
    a platform outage on a secondary feed must not cost a league its scores —
    so everything here degrades to "no section" rather than to an error page.

    The seven-day window is applied HERE rather than in each adapter, because
    it is a fact about what a weekly newspaper prints and not a fact about any
    platform's API.
    """
    try:
        provider = get_provider(provider_name, **provider_kwargs)
        moves = provider.get_transactions(league_id, season, week) or []
    except Exception:  # noqa: BLE001 — a bonus section, never a blocker
        return []

    start, end = transaction_window(season, week)
    recent = [m for m in moves
              if getattr(m, "created", None) is None
              or start <= m.created < end]

    # A move with no timestamp is kept rather than dropped: a platform that
    # does not date its transactions should lose the filter, not the section.
    return recent


def load_week(
    provider_name: str,
    league_id: str,
    season: int,
    week: int,
    *,
    with_optimizer: bool = True,
    **provider_kwargs,
) -> WeekData:
    """Fetch and fully prepare one week. The one call most callers want."""
    provider = get_provider(provider_name, **provider_kwargs)
    week_data = provider.get_week(league_id, season, week)
    if with_optimizer:
        week_data = apply_lineup_gaps(week_data)
    return week_data


__all__ = [
    "PROVIDERS",
    "get_provider",
    "available_providers",
    "load_week",
    "load_transactions",
    "FantasyProvider",
    "SleeperProvider",
    "ESPNProvider",
    "YahooProvider",
    "ProviderError",
    "LeagueNotFound",
    "AuthRequired",
    "WeekNotAvailable",
    "TTLCache",
    "League",
    "Manager",
    "Team",
    "Matchup",
    "PlayerLine",
    "WeekData",
    "Transaction",
    "TransactionPlayer",
    "SLOT_ELIGIBILITY",
    "BENCH_SLOTS",
    "is_starting_slot",
    "can_fill_slot",
    "can_fill_slot_any",
    "apply_lineup_gaps",
    "optimal_lineup",
    "week_to_legacy_games",
    "team_to_legacy",
    "legacy_league_info",
]
