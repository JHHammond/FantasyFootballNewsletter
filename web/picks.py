"""
The league votes on next week's lines (John, 12 Oct 2026).

Every paper's back page carries "Next Week's Lines". Readers tap a side on
each game; once they have, they see how the league split. Voting closes at
the next week's first kickoff, and the next paper's writer is told how the
league did — "nine of eleven took the Falcons, who lost by 31" — to use
where it fits. Tallies only: nobody's pick is ever shown with a name.

`week` everywhere here is the PAPER's week; its lines are for the week after.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

#: A voter id is made up by the reader's browser: 32 hex characters.
VOTER_RE = re.compile(r"^[0-9a-f]{32}$")
#: A game key: two team ids joined by "-" (history.line_key), or the
#: name-based fallback for lines saved before team ids were (n:<hex>).
GAME_RE = re.compile(r"^(?:[A-Za-z0-9_.]{1,40}-[A-Za-z0-9_.]{1,40}|n:[0-9a-f]{12})$")
SIDES = ("fav", "dog")

#: Votes per IP per hour. A league is a dozen people on one Wi-Fi at most,
#: each voting on six games and changing their minds; this is room for that.
VOTES_PER_IP_PER_HOUR = 300


def voting_closes(season: int, paper_week: int) -> datetime:
    import nfl_week
    return nfl_week.first_kickoff(int(paper_week) + 1, int(season))


def is_locked(season: int, paper_week: int, now: Optional[datetime] = None) -> bool:
    return (now or datetime.now(timezone.utc)) >= voting_closes(season, paper_week)


def tally(rows: list[dict]) -> dict[str, dict[str, Any]]:
    """{game: {"fav": n, "dog": n, "voters": {voter: side}}}."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows or []:
        side = r.get("side")
        if side not in SIDES:
            continue
        g = out.setdefault(r.get("game") or "", {"fav": 0, "dog": 0, "voters": {}})
        g[side] += 1
        g["voters"][r.get("voter")] = side
    return out


def public_view(rows: list[dict], voter: str, locked: bool) -> dict[str, dict]:
    """What one reader is allowed to see: the split on the games they have
    voted on (or every game, once voting has closed), and their own pick.
    A game they haven't voted on shows nothing — seeing the split first
    turns a vote into following the crowd."""
    out = {}
    for game, t in tally(rows).items():
        mine = t["voters"].get(voter)
        entry: dict[str, Any] = {"mine": mine}
        if mine or locked:
            entry.update(fav=t["fav"], dog=t["dog"])
        out[game] = entry
    return out


# ---------------------------------------------------------------------------
# Grading, for the next paper
# ---------------------------------------------------------------------------

def _find(week_data, line: dict):
    """The matchup and the two teams a line was about, by id, else by name."""
    fid, did = str(line.get("favorite_id") or ""), str(line.get("underdog_id") or "")
    for m in getattr(week_data, "matchups", None) or []:
        teams = list(m.teams)
        by_id = {str(t.team_id): t for t in teams}
        if fid and did and fid in by_id and did in by_id:
            return by_id[fid], by_id[did]
        by_name = {t.team_name: t for t in teams}
        if (line.get("favorite") in by_name and line.get("underdog") in by_name):
            return by_name[line["favorite"]], by_name[line["underdog"]]
    return None


def grade(line: dict, week_data) -> Optional[dict]:
    """Which side covered: "fav", "dog", or "push". None if the game can't be
    found or wasn't played."""
    found = _find(week_data, line)
    if not found:
        return None
    fav, dog = found
    fp, dp = float(fav.points or 0), float(dog.points or 0)
    if fp == 0 and dp == 0:
        return None
    margin = fp - dp
    spread = 0.0 if line.get("pickem") else float(line.get("spread") or 0)
    if margin > spread:
        covered = "fav"
    elif margin < spread:
        covered = "dog"
    else:
        covered = "push"
    return {"covered": covered, "fav_points": fp, "dog_points": dp,
            "fav": fav.team_name, "dog": dog.team_name}


def results(lines: list[dict], week_data, rows: list[dict]) -> dict:
    """How the league's picks did: per game, and the most-picked side's record."""
    votes = tally(rows)
    games, won, lost, pushed = [], 0, 0, 0
    for line in lines or []:
        key = line_key_for(line)
        t = votes.get(key)
        if not t or not (t["fav"] + t["dog"]):
            continue
        g = grade(line, week_data)
        if not g:
            continue
        crowd = ("fav" if t["fav"] > t["dog"] else
                 "dog" if t["dog"] > t["fav"] else None)
        if crowd and g["covered"] != "push":
            if crowd == g["covered"]:
                won += 1
            else:
                lost += 1
        elif crowd:
            pushed += 1
        games.append({**g, "line": line, "votes_fav": t["fav"],
                      "votes_dog": t["dog"], "crowd": crowd})
    return {"games": games, "won": won, "lost": lost, "pushed": pushed}


def line_key_for(line: dict) -> str:
    """The key a line's votes are stored under (newspaper.py uses the same)."""
    if line.get("key"):
        return str(line["key"])
    import hashlib
    names = "|".join(sorted((str(line.get("favorite") or ""),
                             str(line.get("underdog") or ""))))
    return "n:" + hashlib.sha1(names.encode()).hexdigest()[:12]


def writer_facts(res: dict) -> str:
    """The block the writer gets. Empty when nobody voted."""
    games = (res or {}).get("games") or []
    if not games:
        return ""
    out = [
        "HOW THE LEAGUE PICKED LAST WEEK'S LINES — readers voted on the lines "
        "in last week's paper. Bring this up once or twice, briefly, where it "
        "fits: the lead story, or the recap of a game the league got badly "
        "wrong or right. A confident league being wrong is the joke. Never "
        "list every game, never name who voted for what (the votes are "
        "anonymous), and say \"the league\" or \"the public\", not "
        "\"readers\"."
    ]
    for g in games:
        line = g["line"]
        spread = ("a pick'em" if line.get("pickem")
                  else f"{g['fav']} favored by {float(line.get('spread') or 0):g}")
        picked = (f"the league took {g['fav']} {g['votes_fav']}-{g['votes_dog']}"
                  if g["votes_fav"] >= g["votes_dog"] else
                  f"the league took {g['dog']} {g['votes_dog']}-{g['votes_fav']}")
        if g["votes_fav"] == g["votes_dog"]:
            picked = f"the league split {g['votes_fav']}-{g['votes_dog']}"
        winner, loser = ((g["fav"], g["dog"]) if g["fav_points"] >= g["dog_points"]
                         else (g["dog"], g["fav"]))
        hi, lo = max(g["fav_points"], g["dog_points"]), min(g["fav_points"], g["dog_points"])
        cover = {"fav": f"{g['fav']} covered", "dog": f"{g['dog']} covered",
                 "push": "a push"}[g["covered"]]
        verdict = ("" if not g["crowd"] or g["covered"] == "push" else
                   " — the league was RIGHT" if g["crowd"] == g["covered"] else
                   " — the league was WRONG")
        out.append(f"- {g['fav']} vs {g['dog']} ({spread}): {picked}. "
                   f"{winner} won {hi:.1f}-{lo:.1f}, {cover}{verdict}.")
    decided = res["won"] + res["lost"]
    if decided:
        out.append(f"The league's picks went {res['won']}-{res['lost']}"
                   + (f"-{res['pushed']}" if res["pushed"] else "")
                   + " against the spread.")
    return "\n".join(out)
