"""
Draft regrets (John, 20 Oct 2026): the draft, re-graded with what has
happened since.

Every pick is compared on one scale: where the player went AT HIS POSITION
in this league's draft ("drafted as the WR28") against where he ranks at
his position now on season points in this league's scoring ("now the WR5").
The gap is the story. Points are counted only while a player sat on a roster
in this league — that's all the weekly data shows — so a player cut to
waivers stops scoring here. That is fair: the league stopped getting his
points too.

Two lists come out, frozen into the paper like the record book:
  * steals  — later picks now far above where they went,
  * regrets — early picks now far below it, or cut.
(A per-team re-grade with letter grades was tried and dropped, 20 Oct.)

Pure functions over WeekData and the provider's draft_picks(). No Claude
call, no extra requests: the weeks are the ones the season briefing
already loads.
"""

from __future__ import annotations

from typing import Any, Optional

#: Before this week there isn't enough season to judge anybody.
FIRST_WEEK = 4
#: Positions that are judged. Kickers and defenses go in the last two rounds
#: and swing week to week; "K8 to K1" is noise.
POSITIONS = ("QB", "RB", "WR", "TE")
#: A steal comes from here or later: a third-round pick finishing well is
#: just a good pick.
STEAL_FROM_ROUND = 4
#: A regret comes from here or earlier: that's where expectations live.
REGRET_THROUGH_ROUND = 4
#: How far a player has to move, in places at his position, to count.
MIN_MOVE = 8
#: How many of each to print.
SHOW = 3


def _raw(pid: str) -> str:
    return str(pid).split(":", 1)[-1] if pid else ""


def _season(weeks: list, through_week: int) -> tuple[dict, dict]:
    """(players, team_names): every player seen on a roster this season with
    his points, and the latest name of every team id."""
    players: dict[str, dict] = {}
    names: dict[str, str] = {}
    for wd in sorted((w for w in weeks if w is not None), key=lambda w: w.week):
        if wd.week > through_week:
            continue
        for team in wd.teams:
            tid = str(team.team_id)
            names[tid] = team.team_name
            for p in list(team.lineup) + list(team.bench):
                pid = _raw(p.player_id)
                if not pid:
                    continue
                row = players.setdefault(pid, {
                    "pid": str(p.player_id), "name": p.name, "pos": (p.position or "").upper(),
                    "points": 0.0, "weeks": 0, "holder": None,
                    "last_week": 0, "status": None, "headshot": None})
                row["points"] += float(p.points or 0)
                row["weeks"] += 1
                if wd.week >= row["last_week"]:
                    row["last_week"] = wd.week
                    row["holder"] = tid
                    row["status"] = p.injury_status
                    row["headshot"] = p.headshot_url or row["headshot"]
    return players, names


def _rank(ids_in_order: list[str], pos_of: dict[str, str]) -> dict[str, int]:
    """1-based rank of each id within its position, in the order given."""
    seen: dict[str, int] = {}
    out: dict[str, int] = {}
    for pid in ids_in_order:
        pos = pos_of[pid]
        seen[pos] = seen.get(pos, 0) + 1
        out[pid] = seen[pos]
    return out


def build(weeks: list, picks: dict, through_week: int) -> Optional[dict]:
    """The draft re-graded through `through_week`, or None when there's no
    draft, not enough season, or nothing worth printing."""
    if through_week < FIRST_WEEK or not picks:
        return None
    players, names = _season(weeks, through_week)
    if not players:
        return None
    latest = max((w.week for w in weeks if w is not None and w.week <= through_week),
                 default=0)

    # Where each drafted player went at his position.
    drafted = {pid: pk for pid, pk in picks.items()
               if pk.get("overall") and pid in players
               and players[pid]["pos"] in POSITIONS}
    if len(drafted) < 8:
        return None
    pos_of = {pid: players[pid]["pos"] for pid in players}
    by_pick = sorted(drafted, key=lambda pid: drafted[pid]["overall"])
    drafted_rank = _rank(by_pick, pos_of)

    # Where everyone ranks now, drafted or not: a waiver pickup who is the
    # RB4 pushes everybody below him down, as he should.
    judged = [pid for pid in players if pos_of[pid] in POSITIONS]
    by_points = sorted(judged, key=lambda pid: (-players[pid]["points"], pid))
    now_rank = _rank(by_points, pos_of)

    def where(pid: str) -> str:
        """Who has him now, from the drafting team's side."""
        p, pk = players[pid], drafted[pid]
        if p["last_week"] < latest:
            # Gone from the weekly rosters. Last seen hurt, he is most likely
            # parked on injured reserve, which some platforms leave out of a
            # week's roster; only a healthy player who vanished was cut.
            if str(p.get("status") or "").upper() in ("IR", "OUT", "PUP", "DOUBTFUL"):
                return "injured"
            return "cut"
        if pk.get("team_id") and p["holder"] != pk["team_id"]:
            return f"now on {names.get(p['holder'], 'another team')}"
        return ""

    def entry(pid: str) -> dict:
        p, pk = players[pid], drafted[pid]
        pos = p["pos"]
        injured = (str(p.get("status") or "").upper() in ("IR", "OUT", "PUP", "DOUBTFUL"))
        return {
            # The platform's player id, so the trade market can price him
            # (web/paper_market.py, 26 Oct).
            "pid": p.get("pid") or pid,
            "player": p["name"], "pos": pos,
            "round": pk.get("round"), "overall": pk.get("overall"),
            "drafted_as": f"{pos}{drafted_rank[pid]}",
            "now": f"{pos}{now_rank[pid]}",
            "move": drafted_rank[pid] - now_rank[pid],
            "points": round(p["points"], 1),
            "team": names.get(pk.get("team_id") or "", ""),
            "where": where(pid),
            "injured": injured,
            "headshot": p.get("headshot"),
        }

    rows = [entry(pid) for pid in by_pick]
    steals = sorted((r for r in rows if (r["round"] or 0) >= STEAL_FROM_ROUND
                     and r["move"] >= MIN_MOVE and r["team"]),
                    key=lambda r: (-r["move"], r["overall"]))[:SHOW]
    regrets = sorted((r for r in rows if 0 < (r["round"] or 0) <= REGRET_THROUGH_ROUND
                      and (r["move"] <= -MIN_MOVE or r["where"] == "cut") and r["team"]),
                     key=lambda r: (r["move"], r["overall"]))[:SHOW]

    if not (steals or regrets):
        return None
    return {"through_week": through_week, "steals": steals, "regrets": regrets}


def writer_facts(regrets: Optional[dict]) -> str:
    """A few lines for the writer's background, so a recap can say "his
    eleventh-round pick, now the WR5" when it fits. "" when there's nothing."""
    if not regrets:
        return ""
    lines = []
    for r in regrets.get("steals") or []:
        lines.append(f"Draft steal: {r['team']} took {r['player']} in round "
                     f"{r['round']} (drafted as the {r['drafted_as']}); he is "
                     f"now the {r['now']} in this league.")
    for r in regrets.get("regrets") or []:
        tail = {"cut": " and has been cut", "injured": " and is hurt", "": ""}.get(
            r["where"], f" and is {r['where']}")
        hurt = " He is injured, so blame luck more than the pick." if r["injured"] else ""
        lines.append(f"Draft regret: {r['team']} took {r['player']} in round "
                     f"{r['round']} (drafted as the {r['drafted_as']}); he is "
                     f"now the {r['now']}{tail}.{hurt}")
    if not lines:
        return ""
    return ("THE DRAFT, IN HINDSIGHT — background; use one only if it makes a "
            "story better, and never list them:\n" + "\n".join(lines))
