"""
How you stack up: one league's week against every league on the site.

Built once, when a paper is written, and frozen into the paper (like the
trends and the record book), so an edit in December shows the ranks as they
stood that Tuesday. Needs Around the Leagues (021) collected for the week
and the ranking function (024); without them, or with too few teams in the
pool to mean anything, it returns None and the paper prints no box.

Team names here are THIS league's own teams, printed in this league's own
paper. Nothing about any other league ever appears: they are only counted.
"""

from __future__ import annotations

import os
from typing import Any, Optional

#: Below this many teams nationally, a rank is noise ("4th of 40"). Early in
#: a week, before the Tuesday collection, most papers will skip the box.
MIN_TEAMS = int(os.getenv("NATIONAL_MIN_TEAMS", "1000"))


def _top_pct(above: int, total: int) -> int:
    """"Top N%": the share of teams at or above this one, never 0."""
    return max(1, round(100 * (above + 1) / max(total, 1)))


def build(db, league: dict, week_data) -> Optional[dict[str, Any]]:
    """The box's numbers, or None. Never raises."""
    try:
        from . import league_stats
        teams = [t for t in week_data.teams if (t.points or 0) > 0]
        if len(teams) < 2:
            return None
        season, week = int(week_data.season), int(week_data.week)
        if not league_stats.week_is_final(season, week):
            return None
        points = [round(float(t.points), 2) for t in teams]
        avg = sum(points) / len(points)
        # Ranked against everyone ELSE, then this league's own teams are
        # added back — so the answer is the same whether or not this week
        # of this league has been collected yet.
        ranks = db.national_ranks(season, week, points, avg, str(league["id"]))
        others = list(ranks.get("above") or [])
        if len(others) != len(teams):
            return None
        total = int(ranks.get("teams") or 0) + len(teams)
        n_leagues = int(ranks.get("leagues") or 0) + 1
        if total < MIN_TEAMS or n_leagues < 3:
            return None
        above = [o + sum(1 for q in points if q > p) for o, p in zip(others, points)]
    except Exception as exc:  # noqa: BLE001 — a sidebar is never worth a paper
        print(f"[national] skipped: {type(exc).__name__}: {exc}", flush=True)
        return None

    ranked = sorted(zip(teams, above), key=lambda ta: ta[1])
    best_team, best_above = ranked[0]
    worst_team, worst_above = ranked[-1]

    out: dict[str, Any] = {
        "week": week,
        "teams": total,
        "leagues": n_leagues,
        "league_avg": round(avg, 1),
        "league_top_pct": _top_pct(int(ranks.get("leagues_above") or 0), n_leagues),
        "best": {"team": best_team.team_name, "points": round(best_team.points, 1),
                 "rank": best_above + 1, "top_pct": _top_pct(best_above, total)},
        "worst": {"team": worst_team.team_name, "points": round(worst_team.points, 1),
                  # the share of the country that outscored them
                  "beaten_by_pct": round(100 * worst_above / total)},
        "honors": [],
    }

    # National honors: this league holds the week's record outright.
    try:
        boards = {b["key"]: (b["rows"] or [None])[0] for b in
                  league_stats.leaderboards(db, season, week, limit=1)["boards"]}
        mine = str(league["id"])
        top = boards.get("highest")
        if top and str(top.get("league_id")) == mine:
            out["honors"].append(f"Highest score in the country: {top['team_name']}, "
                                 f"{float(top['points']):.1f}")
        close = boards.get("closest")
        if close and str(close.get("league_id")) == mine:
            out["honors"].append(
                f"Closest game in the country: {close['team_name']} over "
                f"{close['opponent_name']} by {float(close['margin']):.2f}")
        low = boards.get("lowest")
        if low and str(low.get("league_id")) == mine:
            out["honors"].append(f"Lowest score in the country: {low['team_name']}, "
                                 f"{float(low['points']):.1f}")
    except Exception:  # noqa: BLE001
        pass
    return out


def writer_facts(national: Optional[dict]) -> str:
    """Two or three lines the lead writer may use one of."""
    if not national:
        return ""
    b, w = national["best"], national["worst"]
    lines = [
        f"This league averaged {national['league_avg']} this week, top "
        f"{national['league_top_pct']}% of {national['leagues']:,} leagues on the site.",
        f"{b['team']}'s {b['points']} was the #{b['rank']:,} score of "
        f"{national['teams']:,} teams nationally (top {b['top_pct']}%).",
        f"{w['team']}'s {w['points']} was outscored by {w['beaten_by_pct']}% of "
        f"teams nationally.",
    ]
    lines += national.get("honors") or []
    return "\n".join(f"- {line}" for line in lines)
