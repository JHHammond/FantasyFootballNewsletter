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

#: Every platform we know about. Keys are what callers pass to get_provider()
#: and what gets stored in the leagues table's `provider` column.
PROVIDERS: dict[str, type[FantasyProvider]] = {
    SleeperProvider.name: SleeperProvider,
    ESPNProvider.name: ESPNProvider,
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
    """
    try:
        provider = get_provider(provider_name, **provider_kwargs)
        return provider.get_transactions(league_id, season, week) or []
    except Exception:  # noqa: BLE001 — a bonus section, never a blocker
        return []


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
