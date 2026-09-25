"""
What week of the NFL season is it?

This module exists because the answer used to be hardcoded. The weekly cron
computed its week from a fixed anchor of 2 September 2025, which meant that
from the 2026 season onward every automated paper would have been a write-up of
week 18 — a week nobody had played. A date that only works for one season is a
bug with a timer on it.

Two sources, in order of preference:

    1. The platform. Sleeper publishes the live NFL state, which is
       authoritative and already accounts for flex scheduling and the extra
       regular-season week.
    2. This module's date arithmetic, used when the platform can't be reached.
       The NFL regular season opens on the Thursday after Labor Day (the first
       Monday in September), which is a rule rather than a coincidence, so it
       can be computed for any year instead of written down for one.

Nothing here is season-specific. If it still works in 2031 it will be because
nobody had to remember to update it.
"""

from __future__ import annotations

from datetime import date, timedelta

#: Weeks in the NFL regular season. 18 since 2021.
REGULAR_SEASON_WEEKS = 18


def labor_day(year: int) -> date:
    """First Monday in September."""
    d = date(year, 9, 1)
    # weekday(): Monday is 0.
    return d + timedelta(days=(7 - d.weekday()) % 7)


def season_opener(year: int) -> date:
    """Thursday of week 1 — the Thursday after Labor Day."""
    return labor_day(year) + timedelta(days=3)


def current_season(today: date | None = None) -> int:
    """The season a given date belongs to.

    A season is named for the calendar year it starts in, so January and
    February belong to the previous year's season. March through August is the
    offseason, which we treat as belonging to the season about to start —
    that's what somebody signing up in July is thinking about.
    """
    today = today or date.today()
    if today.month <= 2:
        return today.year - 1
    return today.year


def current_week(today: date | None = None, *, season: int | None = None) -> int:
    """Week number for a date, clamped to the regular season.

    Before the opener this returns 1 and after week 18 it returns 18, because
    every caller wants a week it can actually ask a platform about. Callers who
    need to know whether the season is live should ask `is_in_season`.
    """
    today = today or date.today()
    season = season if season is not None else current_season(today)
    delta = (today - season_opener(season)).days
    if delta < 0:
        return 1
    return max(1, min(REGULAR_SEASON_WEEKS, delta // 7 + 1))


def week_final(week: int, season: int):
    """When a week's games are all over: 08:00 UTC on the Tuesday after it
    (4am Eastern, clear of the latest Monday night finish). A paper written
    before this has part of the week's scores."""
    from datetime import datetime, time, timezone
    tuesday = season_opener(season) + timedelta(days=7 * (week - 1) + 5)
    return datetime.combine(tuesday, time(8, 0), tzinfo=timezone.utc)


def is_in_season(today: date | None = None) -> bool:
    """True between the opener and the end of week 18."""
    today = today or date.today()
    season = current_season(today)
    opener = season_opener(season)
    return opener <= today < opener + timedelta(weeks=REGULAR_SEASON_WEEKS)


def completed_week(today: date | None = None) -> int:
    """The most recent week that has actually finished.

    This is what the Tuesday cron wants. `current_week` on a Tuesday returns
    the week now in progress, whose games are still days away; the paper that
    should go out is the one about last weekend.
    """
    today = today or date.today()
    week = current_week(today)
    season = current_season(today)
    # A week runs Thursday to the following Wednesday. On Tuesday of week N the
    # games of week N-1 are done and week N hasn't started.
    days_in = (today - season_opener(season)).days % 7
    if days_in < 5 and week > 1:  # Thu..Mon of the new week
        week -= 1
    return max(1, min(REGULAR_SEASON_WEEKS, week))
