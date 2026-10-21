"""
The league votes on who won each trade (John, 20 Oct 2026).

The twin of web/picks.py, which does the same for next week's lines: every
two-team trade in a paper gets "Who won the trade?" with a button for each
side. Once a reader has voted they see how the league split; voting closes
with the lines, at the next week's first kickoff. Tallies only: nobody's
vote is ever shown with a name.

`week` everywhere here is the PAPER's week.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

#: A trade key: "t" and 12 hex characters (trade_key below).
TRADE_RE = re.compile(r"^t[0-9a-f]{12}$")
#: The two sides of a trade, in the order the card shows them.
SIDES = ("a", "b")


def trade_sides(trade: dict) -> list[str]:
    """The teams in a trade, in card order: the platform's order first, then
    anyone who only appears receiving something."""
    teams = list(trade.get("teams") or [])
    for key in ("adds", "picks"):
        for team, _ in (trade.get(key) or []):
            if team not in teams:
                teams.append(team)
    return teams


def trade_key(trade: dict) -> str:
    """A stable short id for a trade, from who got what. The platforms don't
    give one we can rely on, and the same trade has to get the same key on
    every render of the paper."""
    adds = sorted((str(team), str((pl or {}).get("name") or ""))
                  for team, pl in (trade.get("adds") or []))
    picks = sorted((str(team), str(label)) for team, label in (trade.get("picks") or []))
    faab = sorted((str(a), str(b), str(n)) for a, b, n in (trade.get("faab") or []))
    blob = json.dumps([trade_sides(trade), adds, picks, faab, trade.get("week")],
                      sort_keys=True)
    return "t" + hashlib.sha1(blob.encode()).hexdigest()[:12]


def votable(trade: dict) -> bool:
    """Two teams only: "who won" has no simple answer in a three-way deal."""
    return len(trade_sides(trade)) == 2


def public_view(rows: list[dict], voter: str, locked: bool) -> dict[str, dict]:
    """What one reader may see: the split on trades they've voted on (or on
    every trade once voting has closed), and their own vote. Seeing the
    split first turns a vote into following the crowd."""
    out: dict[str, dict[str, Any]] = {}
    tallies: dict[str, dict[str, Any]] = {}
    for r in rows or []:
        side = r.get("side")
        if side not in SIDES:
            continue
        t = tallies.setdefault(r.get("trade") or "", {"a": 0, "b": 0, "voters": {}})
        t[side] += 1
        t["voters"][r.get("voter")] = side
    for trade, t in tallies.items():
        mine = t["voters"].get(voter)
        entry: dict[str, Any] = {"mine": mine}
        if mine or locked:
            entry.update(a=t["a"], b=t["b"])
        out[trade] = entry
    return out
