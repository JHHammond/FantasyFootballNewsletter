"""What the leagues' own usage says a player is worth (21 Oct 2026).

The trade market needs a starting belief about every player before it reads
a single trade — see the long note on the ridge in `trade_market.solve` for
why it cannot work without one. This builds that belief out of what 14,000
teams a week actually did, and nothing else: no licensed value chart, no
expert consensus, no scraped rankings. It is the paper's own data saying
who the field trusts.

THE NUMBER. Per player, from `trade_prior()` in migration 048:

    started     team-weeks he was in somebody's starting lineup
    per_start   standardized PPR points he averaged in those lineups

Multiply them and you have points delivered to the field — demand and
production in one figure, which is most of what a player is worth.

BUT NOT POINTS. Raw points would systematically overprice quarterbacks,
because a QB outscores a running back every week and there is only one of
him in a lineup. What a manager pays for is points ABOVE what he could
have had for free at the same position, so the per-start average is
measured against a replacement level computed per position — and computed
from this season's own usage rather than from a table somebody wrote down.

REPLACEMENT LEVEL is the last player people actually start: sort a
position by total starts, walk down until the players counted cover
`COVERS` of all starts at that position, and take the per-start average
there. Everyone above him is a starter somebody chose; the tail below is
bye-week filler. One constant, and it is interpretable.

WHAT IT IS NOT. Backward-looking: it is what a player has delivered, not
what he will. Trade value is a forecast, and this is a record. That is
fine, and it is the division of labour the market rests on — usage says
where a player stands, the trades say where the field has moved him since.
A player the country has soured on is exactly the interesting case, and he
shows up as a price well under his prior.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

#: Share of a position's starts that sit above replacement level. The rest
#: is the tail of one-week fill-ins, and the player at the boundary is the
#: marginal starter — the worst man anybody deliberately plays.
COVERS = 0.90
#: A player needs this many starts somewhere in the country before his
#: per-start average means anything. Below it he keeps the replacement
#: value for his position rather than a number built on four games.
MIN_STARTS = 40
#: No prior may be lower than this share of an average player's. A prior of
#: zero would hand the solver back the degenerate fit it is there to
#: prevent (zeroing a player out has to cost something), and a player who
#: appears in real trades is by definition worth more than nothing.
FLOOR = 0.08
#: Positions that are priced but never really traded as value. They are
#: kept in the prior so a trade involving one still balances, but a kicker
#: is not a market.
THIN = ("K", "DEF", "DST")


def replacement(usage: dict[str, dict], *, covers: float = COVERS) -> dict[str, float]:
    """Per-start points at the margin of being started, by position.

    Players with no position land under None, which gets its own level
    rather than being lumped in with anybody — an unmatched player_key is
    usually an IDP slot or a platform oddity, not a wide receiver.
    """
    bypos: dict[Optional[str], list] = {}
    for row in usage.values():
        starts = int(row.get("started") or 0)
        rate = row.get("per_start")
        if starts > 0 and rate is not None:
            bypos.setdefault(row.get("position"), []).append((float(rate), starts))

    out: dict[Any, float] = {}
    for pos, players in bypos.items():
        # Best per-start first; walk down until we have covered `covers` of
        # the position's starts. Where that lands is replacement.
        players.sort(key=lambda p: (-p[0], -p[1]))
        total = sum(n for _, n in players)
        want = total * covers
        seen = 0
        level = players[-1][0]
        for rate, n in players:
            seen += n
            if seen >= want:
                level = rate
                break
        out[pos] = level
    return out


def build(usage: dict[str, dict], *, only: Optional[Iterable[str]] = None,
          covers: float = COVERS, min_starts: int = MIN_STARTS,
          floor: float = FLOOR) -> dict[str, float]:
    """A prior per player, on a scale where the average player is 1.0.

    `only` restricts the normalising population to the players the fit will
    actually see (the traded ones). That matters: normalising over every
    rostered player in the country would put the average at a deep bench
    piece and make every traded player look expensive.
    """
    levels = replacement(usage, covers=covers)
    keys = list(usage) if only is None else [k for k in only if k in usage]

    raw: dict[str, float] = {}
    for key in keys:
        row = usage[key]
        starts = int(row.get("started") or 0)
        rate = row.get("per_start")
        level = levels.get(row.get("position"), 0.0)
        if rate is None or starts < min_starts:
            # Not enough starts to price him on his own production. Hold him
            # at the position's margin and let the trades do the talking.
            over = 0.0
        else:
            over = max(0.0, float(rate) - level)
        raw[key] = over * starts

    if not raw:
        return {}
    mean = sum(raw.values()) / len(raw)
    if mean <= 0:
        # Nobody cleared replacement — a half-finished week, most likely.
        # A flat prior is honest here: it says "we don't know yet", and the
        # market degrades to the un-anchored fit rather than to nonsense.
        return {k: 1.0 for k in raw}
    return {k: max(floor, val / mean) for k, val in raw.items()}


def thin(usage: dict[str, dict]) -> set[str]:
    """Players whose price is not worth printing, whatever it comes out as."""
    return {k for k, row in usage.items() if (row.get("position") or "") in THIN}
