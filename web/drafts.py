"""The drafts, kept, so there is an ADP of our own (22 Oct 2026).

A draft is the best season-start valuation anybody has, and we have 1,400
of them. Thousands of people, in rooms with no contact between them, each
deciding what a player is worth when taking him means not taking somebody
else. That is the same wisdom-of-crowds argument behind a prediction
market, and unlike a trade it covers every drafted player rather than only
the ones who happen to change hands.

The market needs it for a reason worth restating. Trades alone cannot
price a star: he is traded twice a season, and the additive model caps him
at the biggest bundle anyone offered, so value(star) = value(a) + value(b)
makes him worth two mid-tier players by construction. Draft position has
none of that problem — the first pick is visibly the first pick — and it
exists before a single game.

WHAT THIS DOES NOT DO. It does not touch dynasty leagues' startup drafts.
A dynasty startup is a different question (it prices a career, not a
season) and mixing the two would corrupt both. generate._draft_picks
already skips dynasty for the same reason.

Player ids are resolved through the same PPR matcher lineup_players and
trade_players use, so a drafted player joins to what he was traded for.
The platforms hand back bare ids — Sleeper's own, ESPN's numeric, Yahoo's
dotted key — so each is prefixed with its provider before resolving, which
is the form Context.resolve expects. A defence cannot be resolved this way
(that needs an NFL team, which a pick does not carry) and nor can a player
who only matches by name; both keep their platform id and are reported
unresolved rather than guessed at.
"""

from __future__ import annotations

from typing import Any, Optional


class _Pick:
    """The shape Context.resolve reads: an id, and whatever else we know.

    A draft pick is an id and a number. It carries no position and no NFL
    team, which is why defences do not resolve here — Context.resolve needs
    a team to find one. That is not worth fixing: the market leaves kickers
    and defences off the board anyway, because they are churned rather
    than valued.
    """

    __slots__ = ("player_id", "position", "nfl_team", "name")

    def __init__(self, player_id: str):
        self.player_id = player_id
        self.position = None
        self.nfl_team = None
        self.name = ""


def _prefixed(provider: str, raw: str) -> str:
    """A platform's own id in the form Context.resolve partitions."""
    provider = (provider or "").lower()
    raw = str(raw)
    return raw if provider == "sleeper" else f"{provider}:{raw}"


def rows(league: dict, picks: dict, season: int, ctx=None) -> list[dict[str, Any]]:
    """draft_picks rows for one league's draft.

    `picks` is what a provider's draft_picks() returns: {raw player id:
    {"round": r, "overall": n, "team_id": t}}.
    """
    size = league.get("team_count") or league.get("size")
    out = []
    for raw, pick in (picks or {}).items():
        overall = int((pick or {}).get("overall") or 0)
        if overall <= 0:
            # A pick the platform did not number cannot be an ADP sample.
            continue
        pid = _prefixed(league.get("provider", ""), raw)
        sid = ctx.resolve(_Pick(pid)) if ctx else None
        known = ctx.name(sid) if (ctx and sid) else None
        name, pos, nfl = known or ("", None, None)
        team_id = (pick or {}).get("team_id")
        out.append({
            "league_id": league["id"], "season": int(season),
            "player_key": sid or pid,
            "overall": overall,
            "round": int((pick or {}).get("round") or 0) or None,
            "team_id": str(team_id) if team_id is not None else None,
            "team_count": int(size) if size else None,
            "name": (name or "")[:80],
            "position": ((pos or "") or "")[:8] or None,
            "nfl_team": nfl or None,
        })
    return out


def collect_league(db, league: dict, season: int, ctx=None) -> int:
    """Fetch and store one league's draft. Returns how many picks.

    Records the scan either way, for the same reason the trade scan does:
    a league that never drafted and a league we never asked look identical
    in draft_picks, and telling them apart is what makes the skip logic and
    the coverage numbers mean anything.

    Dynasty leagues are skipped and recorded as scanned with no picks. A
    startup draft prices a career rather than a season, and averaging the
    two would make both meaningless.
    """
    from providers import get_provider

    picks: dict = {}
    fmt = str(league.get("format") or "").lower()
    if fmt != "dynasty":
        try:
            picks = get_provider(league["provider"]).draft_picks(
                league["platform_league_id"], int(season)) or {}
        except Exception:  # noqa: BLE001 — a draft is a bonus, never a blocker
            picks = {}

    made = rows(league, picks, season, ctx)
    if made:
        db.upsert_draft_picks(made)
    db.upsert_draft_scan({"league_id": league["id"], "season": int(season),
                          "picks": len(made)})
    return len(made)
