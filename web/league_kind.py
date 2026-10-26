"""Dynasty or redraft: one answer per league, for every page (25 Oct 2026).

John: "the whole site needs to understand it."

Two different questions, kept apart on purpose:

  * kind(league)    what the league IS, for the paper's writer: redraft,
                    keeper or dynasty. The platform's own setting
                    (leagues.league_type, migration 062) beats the setup
                    form, whose "redraft" is a default that hundreds of
                    dynasty leagues never changed.
  * MARKET          which trade market prices its players: "redraft" or
                    "dynasty". Keeper is priced on this season, so it is
                    redraft. A league with no stored type and a default
                    form is judged by SQL's dynasty_league_ids(), which
                    also reads the shape of its draft; Python asks that
                    same function (see app._dynasty_ids) rather than
                    re-implementing it.
"""

from __future__ import annotations

from typing import Optional

KINDS = ("redraft", "keeper", "dynasty")
MARKETS = ("redraft", "dynasty")
LABEL = {"redraft": "Redraft", "dynasty": "Dynasty"}


def kind(league: Optional[dict]) -> str:
    league = league or {}
    for v in (league.get("league_type"), league.get("format")):
        v = (v or "").strip().lower()
        if v in KINDS:
            return v
    return "redraft"


def market_of(kind_: str) -> str:
    return "dynasty" if kind_ == "dynasty" else "redraft"


def clean_market(value: Optional[str]) -> Optional[str]:
    """A ?m= from the address bar, or None if it isn't one."""
    v = (value or "").strip().lower()
    return v if v in MARKETS else None


def league_market(league: Optional[dict], dynasty_ids: Optional[set] = None) -> str:
    """The market a league's players are priced in. `dynasty_ids` is SQL's
    answer (dynasty_league_ids); without it, the stored type and form."""
    if league and dynasty_ids is not None:
        return "dynasty" if str(league.get("id")) in dynasty_ids else "redraft"
    return market_of(kind(league))
