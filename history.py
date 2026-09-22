"""
What happened before this week, and what happens next.

Two features that need more than one week of data:

  "PREVIOUSLY ON"  Streaks, rematches, last week's result, last week's award
                   winners. Handed to the writer as context, so a recap can say
                   "third straight loss" or "revenge for week 2" when it's true.
                   Every paper used to start from nothing.

  NEXT WEEK'S LINES  A made-up spread for each upcoming game. No real odds
                   maths, on purpose (John's call): the favourite is whoever
                   is projected higher, and the spread is the gap. When the
                   platform has no projections for next week yet, points per
                   game stands in.

Pure functions over WeekData, plus two thin loaders. The loaders swallow
provider errors and return nothing, because both features are garnish: a
paper must never fail to print because last week or next week couldn't be
fetched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# The season so far
# ---------------------------------------------------------------------------

@dataclass
class Result:
    week: int
    team_id: str
    team: str
    manager: str
    points: float
    opp_id: str
    opp_team: str
    opp_manager: str
    opp_points: float

    @property
    def outcome(self) -> str:
        if self.points > self.opp_points:
            return "W"
        if self.points < self.opp_points:
            return "L"
        return "T"


def results_from_weeks(weeks: list) -> list[Result]:
    """Every team's result in every week given, from both sides."""
    out: list[Result] = []
    for wd in weeks:
        for m in getattr(wd, "matchups", []) or []:
            a, b = m.teams
            for me, them in ((a, b), (b, a)):
                out.append(Result(
                    week=wd.week, team_id=str(me.team_id), team=me.team_name,
                    manager=me.manager.display_name, points=float(me.points or 0),
                    opp_id=str(them.team_id), opp_team=them.team_name,
                    opp_manager=them.manager.display_name,
                    opp_points=float(them.points or 0)))
    return out


def streak(results: list[Result]) -> tuple[str, int]:
    """('W', 3) for three straight wins, from a team's results in order."""
    ordered = sorted(results, key=lambda r: r.week)
    if not ordered:
        return ("", 0)
    kind = ordered[-1].outcome
    n = 0
    for r in reversed(ordered):
        if r.outcome != kind:
            break
        n += 1
    return (kind, n)


@dataclass
class TeamHistory:
    team_id: str
    team: str
    manager: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0
    points_against: float = 0.0
    games: int = 0
    streak_kind: str = ""
    streak_len: int = 0
    last: Optional[Result] = None          # the week BEFORE this one

    @property
    def record(self) -> str:
        base = f"{self.wins}-{self.losses}"
        return f"{base}-{self.ties}" if self.ties else base

    @property
    def points_per_game(self) -> float:
        return self.points_for / self.games if self.games else 0.0


def team_histories(results: list[Result], through_week: int) -> dict[str, TeamHistory]:
    """Each team's season through `through_week`, inclusive."""
    by_team: dict[str, list[Result]] = {}
    for r in results:
        if r.week <= through_week:
            by_team.setdefault(r.team_id, []).append(r)

    out: dict[str, TeamHistory] = {}
    for team_id, rs in by_team.items():
        rs.sort(key=lambda r: r.week)
        latest = rs[-1]
        h = TeamHistory(team_id=team_id, team=latest.team, manager=latest.manager)
        for r in rs:
            h.games += 1
            h.points_for += r.points
            h.points_against += r.opp_points
            if r.outcome == "W":
                h.wins += 1
            elif r.outcome == "L":
                h.losses += 1
            else:
                h.ties += 1
        h.streak_kind, h.streak_len = streak(rs)
        before = [r for r in rs if r.week < through_week]
        h.last = before[-1] if before else None
        out[team_id] = h
    return out


def meetings(results: list[Result], a_id: str, b_id: str, before_week: int) -> list[Result]:
    """Earlier games between two teams this season, from a's side."""
    return [r for r in results
            if r.team_id == a_id and r.opp_id == b_id and r.week < before_week]


_STREAK_WORD = {"W": "won", "L": "lost", "T": "tied"}


def previously_on(results: list[Result], week: int,
                  this_week_pairs: list[tuple[str, str]],
                  last_paper: Optional[dict] = None) -> str:
    """The writer's briefing on everything before this week.

    Plain lines of fact, the same way the lineups are given. The prompt says
    to use them only when they make a story better; this function's job is to
    make sure what it hands over is true and short.
    """
    if week <= 1 or not results:
        return ""

    hist = team_histories(results, week)
    lines: list[str] = []

    for h in sorted(hist.values(), key=lambda h: (-h.wins, -h.points_for)):
        bits = [f"{h.team} ({h.manager}): {h.record} after this week"]
        if h.streak_len >= 2:
            bits.append(f"has {_STREAK_WORD[h.streak_kind]} {h.streak_len} straight")
        if h.last:
            verb = {"W": "beat", "L": "lost to", "T": "tied"}[h.last.outcome]
            bits.append(f"last week {verb} {h.last.opp_team} "
                        f"{h.last.points:.1f}-{h.last.opp_points:.1f}")
        lines.append("; ".join(bits) + ".")

    rematches = []
    for a_id, b_id in this_week_pairs:
        for r in meetings(results, a_id, b_id, week):
            winner = r.team if r.outcome == "W" else r.opp_team
            loser = r.opp_team if r.outcome == "W" else r.team
            hi, lo = max(r.points, r.opp_points), min(r.points, r.opp_points)
            rematches.append(f"This week was a rematch: in week {r.week} "
                             f"{winner} beat {loser} {hi:.1f}-{lo:.1f}.")
    lines.extend(rematches)

    if last_paper:
        headline = (last_paper.get("headline") or "").strip()
        if headline:
            lines.append(f"Last week's front page headline was: {headline}")
        for award in last_paper.get("awards") or []:
            title = (award.get("title") or "").strip()
            body = (award.get("body") or "").strip()
            if title and body:
                lines.append(f"Last week's {title}: {body[:220]}")

    return "\n".join(lines)


#: A streak only becomes a story at three.
NOTABLE_STREAK = 3


def matchup_memory(results: list[Result], week: int, a_id: str, b_id: str,
                   last_paper: Optional[dict] = None) -> str:
    """Only the NOTABLE history between and around these two teams.

    Deliberately sparse. The first version handed every recap both teams'
    records, last results, season highs and last week's headlines — so every
    recap had something to reach for, and the paper said "earlier this
    season" over and over. Now a game gets a note only when there is a real
    story: a streak of three or more, a rematch, a season high set this week,
    or last week's paper handing one of them an award. Most games get nothing,
    which is the point.
    """
    if week <= 1 or not results:
        return ""
    hist = team_histories(results, week)
    lines = []
    for tid in (a_id, b_id):
        h = hist.get(tid)
        if not h:
            continue
        if h.streak_len >= NOTABLE_STREAK:
            lines.append(f"{h.team} ({h.manager}) has now "
                         f"{_STREAK_WORD[h.streak_kind]} {h.streak_len} straight "
                         f"({h.record}).")
        prior = [r.points for r in results if r.team_id == tid and r.week < week]
        this = next((r.points for r in results
                     if r.team_id == tid and r.week == week), None)
        # Three earlier games at least, or "season high" means nothing.
        if len(prior) >= 3 and this is not None and this > max(prior):
            lines.append(f"{h.team}'s {this:.1f} is a new season high "
                         f"(previous best {max(prior):.1f}).")

    for r in meetings(results, a_id, b_id, week):
        winner = r.team if r.outcome == "W" else r.opp_team
        loser = r.opp_team if r.outcome == "W" else r.team
        hi, lo = max(r.points, r.opp_points), min(r.points, r.opp_points)
        lines.append(f"Rematch: in week {r.week} {winner} beat {loser} "
                     f"{hi:.1f}-{lo:.1f}.")

    if last_paper:
        names = {hist[t].team for t in (a_id, b_id) if t in hist}
        names |= {hist[t].manager for t in (a_id, b_id) if t in hist}
        for award in last_paper.get("awards") or []:
            body = award.get("body") or ""
            if any(n and n in body for n in names):
                lines.append(f"Last week the paper gave the "
                             f"{award.get('title', '').title()} to: {body[:160]}")
    return "\n".join(lines)


#: How many weeks a standings trend line shows. Enough to see a run, few
#: enough to fit in a table cell.
TREND_WEEKS = 8


def weekly_scores(results: list[Result], through_week: int,
                  last: int = TREND_WEEKS) -> dict:
    """Each team's recent weekly scores, for the trend lines in the standings.

    Keyed by the team's CURRENT name, because that is what the standings row
    prints. One scale for the whole league (`lo`/`hi`), so a flat line at the
    top means the same thing on every row and teams can be compared by eye.
    """
    by_team: dict[str, list[Result]] = {}
    for r in results:
        if r.week <= through_week:
            by_team.setdefault(r.team_id, []).append(r)
    teams = {}
    for rs in by_team.values():
        rs.sort(key=lambda r: r.week)
        recent = rs[-last:]
        teams[rs[-1].team] = [[r.week, round(r.points, 1)] for r in recent]
    values = [pts for series in teams.values() for _, pts in series]
    if not values:
        return {}
    return {"lo": min(values), "hi": max(values), "teams": teams}


def load_season(fetch_week: Callable[[int], Any], through_week: int) -> list[Result]:
    """Every result from week 1 to `through_week`. A week that can't be
    fetched is skipped rather than failing the paper."""
    weeks = []
    for w in range(1, through_week + 1):
        try:
            weeks.append(fetch_week(w))
        except Exception as exc:  # noqa: BLE001 — garnish, never fatal
            print(f"[history] week {w} unavailable: {type(exc).__name__}: {exc}",
                  flush=True)
    return results_from_weeks(weeks)


# ---------------------------------------------------------------------------
# Next week's lines
# ---------------------------------------------------------------------------

def projected_total(team) -> Optional[float]:
    """The sum of a team's starters' projections, if it has any."""
    projections = [p.projected for p in team.lineup
                   if p.player_id and isinstance(p.projected, (int, float))]
    if len(projections) < max(1, len(team.lineup) // 2):
        return None
    return round(sum(projections), 1)


def round_spread(gap: float) -> float:
    """To the nearest half point, the way a book prints it."""
    return round(abs(gap) * 2) / 2


def betting_lines(next_week, histories: dict[str, TeamHistory]) -> list[dict]:
    """A line for every game next week.

    Projected starters first. If a team has no usable projection yet — some
    platforms don't publish next week's until the weekend — BOTH teams in that
    game fall back to points per game, so a line never compares one team's
    projection with the other's average.
    """
    lines = []
    for m in getattr(next_week, "matchups", []) or []:
        a, b = m.teams
        pa, pb = projected_total(a), projected_total(b)
        basis = "projection"
        if pa is None or pb is None:
            ha, hb = histories.get(str(a.team_id)), histories.get(str(b.team_id))
            pa = round(ha.points_per_game, 1) if ha and ha.games else None
            pb = round(hb.points_per_game, 1) if hb and hb.games else None
            basis = "average"
        if pa is None or pb is None:
            continue

        fav, dog, fav_pts, dog_pts = (a, b, pa, pb) if pa >= pb else (b, a, pb, pa)
        spread = round_spread(fav_pts - dog_pts)
        lines.append({
            "favorite": fav.team_name,
            "favorite_manager": fav.manager.display_name,
            "favorite_avatar": fav.manager.avatar_url,
            "underdog_avatar": dog.manager.avatar_url,
            "underdog": dog.team_name,
            "underdog_manager": dog.manager.display_name,
            "spread": spread,
            "pickem": spread < 1,
            "total": round_spread(fav_pts + dog_pts),
            "favorite_points": fav_pts,
            "underdog_points": dog_pts,
            "basis": basis,
        })
    lines.sort(key=lambda l: -l["spread"])
    return lines


def format_line(line: dict) -> str:
    if line["pickem"]:
        return f"{line['favorite']} vs {line['underdog']} — PICK'EM"
    return f"{line['favorite']} −{line['spread']:g} vs {line['underdog']}"


# ---------------------------------------------------------------------------
# The obituaries: the week's lowest-scoring starters
# ---------------------------------------------------------------------------

#: How many obituaries run. The back page's left column is laid out for four.
OBITUARY_COUNT = 4

_KICK_AND_DEF = {"K", "DEF", "DST", "D/ST"}


def lowest_starters(week_data, n: int = OBITUARY_COUNT) -> list[dict]:
    """The N lowest scores from players who were in a starting lineup.

    John's rule: the obituaries are the lowest scorers who actually started.
    At most ONE kicker or defence among them, because a defence at -3 is an
    ordinary Sunday and a column of four defences is not a joke anybody gets.
    """
    starters = []
    for m in getattr(week_data, "matchups", []) or []:
        for team in m.teams:
            for p in team.lineup:
                if p.player_id:
                    starters.append((p, team))
    starters.sort(key=lambda pt: (float(pt[0].points or 0), pt[0].name))

    picked, special = [], 0
    for p, team in starters:
        if (p.position or "").upper() in _KICK_AND_DEF:
            if special:
                continue
            special += 1
        picked.append({
            "name": p.name,
            "position": p.position,
            "points": round(float(p.points or 0), 1),
            "projected": p.projected,
            "manager": team.manager.display_name,
            "team": team.team_name,
            "nfl_team": p.nfl_team,
            "stat_note": p.stats.describe() if p.stats else "",
        })
        if len(picked) >= n:
            break
    return picked


def biggest_bust(week_data) -> Optional[dict]:
    """The starter who missed his projection by the most, league-wide.

    Needs a projection to have "missed" anything; with none anywhere, the
    lowest-scoring non-kicker, non-defence starter stands in.
    """
    best = None
    for m in getattr(week_data, "matchups", []) or []:
        for team in m.teams:
            for p in team.lineup:
                if not p.player_id or p.vs_projection is None:
                    continue
                if best is None or p.vs_projection < best[0].vs_projection:
                    best = (p, team)
    if best is None:
        for m in getattr(week_data, "matchups", []) or []:
            for team in m.teams:
                for p in team.lineup:
                    if p.player_id and p.position not in ("K", "DEF", "DST"):
                        if best is None or p.points < best[0].points:
                            best = (p, team)
    if best is None:
        return None
    p, team = best
    return {
        "name": p.name,
        "position": p.position,
        "points": round(float(p.points or 0), 1),
        "projected": p.projected,
        "manager": team.manager.display_name,
        "team": team.team_name,
        "stat_note": p.stats.describe() if p.stats else "",
    }
