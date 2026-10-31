"""
The Undefeated Watch (29 Oct 2026).

John: "Is it possible to build something that tracks what 6-0 players do,
to try and identify interesting trends?" Then: analytical AND entertaining,
teach people what the best are doing, and late in the season show just how
few are left. Something people come back and check.

WHO COUNTS. Redraft leagues only (John's call; keeper counts as redraft, as
it does in the market). A team is unbeaten when it has lost and tied
nothing through the latest week the country has finished. That week,
`through`, is the latest one at least nine in ten leagues have been
collected for, so the page doesn't shrink every Tuesday while the collector
is still running. A league counts only with every week 1..through stored:
a team whose week 3 we never saw might have lost it.

THE FOIL is the winless (John's call): every comparison is unbeaten against
winless, with the whole country as the reference line. Abandoned rosters are
left out of the winless and the country (053's rule: under half the starters
took the field in some week), because a team nobody is managing teaches
nothing about managing. They still count in the survival numbers, because
they still lost.

TWICE, ON PURPOSE. `compute()` here is the Python twin of
`undefeated_live()` in migrations/068_undefeated.sql: the SQL is what
production runs (from a pg_cron saved copy, because it adds up the whole
country), this is what the demo and the tests run, and
`python -m scripts.check_undefeated_sql` proves they agree. Change one,
change both.

PRIVACY. Team names are user content. The payload carries them (staff see
them in dashed tags); `build()` strips them for everybody else, and the page
only ever describes a team: "a 12-team PPR league on Sleeper". The one
exception is the viewer's own team, named to its owner.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any, Callable, Iterable, Optional

#: Most leagues' playoffs start in week 15. After 14 the Watch closes the
#: book on the regular season.
MAX_WEEK = 14
#: A league smaller than this isn't a league anyone is trying to go unbeaten in.
MIN_TEAMS = 4
#: `through` is the latest week at least this share of leagues have (x/10).
COVER_TENTHS = 9
#: "Lost by less than a field goal."
CLOSE = 3.0
#: The player boards: a player must be on this share of a group's rosters,
#: on at least MIN_N of them, and on MIN_ALL rosters nationally, before his
#: over-representation means anything.
MIN_SHARE = 0.08
MIN_N = 3
MIN_ALL = 30
#: Every unbeaten team gets a card once this few are left.
CARDS = 100
#: All-play verdicts for an unbeaten team.
REAL = 0.75
PRETENDER = 0.5
SKILL = ("QB", "RB", "WR", "TE")
#: Rounds on the unbeaten draft board.
BOARD_ROUNDS = 15


def _board_pos(p) -> str:
    """A pick's position for the draft board's mix: the five that matter and
    defenses under one name (DEF, DST and D/ST all appear)."""
    p = (p or "").upper()
    if p in ("QB", "RB", "WR", "TE", "K"):
        return p
    if p in ("DEF", "DST", "D/ST"):
        return "DEF"
    return "OTHER"


#: The opening builds, in the order the page shows them.
BUILDS = ("rb_rb", "rb_wr", "wr_wr", "zero_rb", "early_qb", "late_qb", "early_te")


# ---------------------------------------------------------------------------
# Small arithmetic, matched to Postgres
# ---------------------------------------------------------------------------

def _r(x, k: int = 2):
    """Postgres round(): half away from zero."""
    if x is None:
        return None
    q = Decimal(1).scaleb(-k)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def _avg(vals: Iterable) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _div(a, b) -> Optional[float]:
    return (a / b) if (a is not None and b) else None


def _slot(overall: int, team_count) -> float:
    """A pick as its twelve-team equivalent (050's normalisation)."""
    tc = int(team_count or 12) or 12
    return (int(overall) - 1) * 12.0 / tc + 1


# ---------------------------------------------------------------------------
# The engine (the SQL twin)
# ---------------------------------------------------------------------------

def compute(*, team_rows: list[dict], lineup_rows: list[dict], draft_rows: list[dict],
            trade_heads: list[dict], trade_players: list[dict],
            waiver_rows: list[dict], waiver_scans: list[dict],
            played: dict, nfl_players: dict, dynasty_ids: Iterable,
            season: int) -> dict[str, Any]:
    """Everything the page draws, as one JSON-able dict. See the module
    docstring for who counts; migrations/068_undefeated.sql is the twin."""
    season = int(season)
    dyn = {str(x) for x in (dynasty_ids or [])}
    L = lambda r: str(r["league_id"])  # noqa: E731

    allrows = [r for r in team_rows
               if int(r["season"]) == season and 1 <= int(r["week"]) <= MAX_WEEK]
    dynasty_left = len({L(r) for r in allrows if L(r) in dyn})
    tw = [r for r in allrows if L(r) not in dyn]
    games = [r for r in tw if r["result"] in ("W", "L", "T")]

    per_week: dict[int, set] = {}
    for r in games:
        per_week.setdefault(int(r["week"]), set()).add(L(r))
    if not per_week:
        return {"season": season, "through": None, "summary": {"dynasty_left": dynasty_left}}
    maxn = max(len(v) for v in per_week.values())
    through = max(k for k, v in per_week.items() if len(v) * 10 >= maxn * COVER_TENTHS)

    # Leagues with every week 1..through stored, and enough teams.
    weeks_of: dict[str, set] = {}
    teams_now: dict[str, set] = {}
    for r in tw:
        if int(r["week"]) <= through:
            weeks_of.setdefault(L(r), set()).add(int(r["week"]))
        if int(r["week"]) == through:
            teams_now.setdefault(L(r), set()).add(str(r["team_id"]))
    pool = {lg for lg, ws in weeks_of.items()
            if len(ws) == through and len(teams_now.get(lg, ())) >= MIN_TEAMS}
    incomplete_left = len(set(weeks_of) - pool)

    rows = [r for r in tw if L(r) in pool and int(r["week"]) <= through]
    tk = lambda r: (L(r), str(r["team_id"]))  # noqa: E731
    now = {tk(r): r for r in rows if int(r["week"]) == through}
    hist: dict[tuple, list] = {}
    for r in rows:
        if tk(r) in now:
            hist.setdefault(tk(r), []).append(r)

    # League-week context: the average score, and every score for all-play.
    lw: dict[tuple, list] = {}
    for r in rows:
        if r["result"] in ("W", "L", "T"):
            lw.setdefault((L(r), int(r["week"])), []).append(_f(r["points"]) or 0.0)
    lw_avg = {k: sum(v) / len(v) for k, v in lw.items()}

    # Who turned up (053/055): starters, and starters who took the field.
    fielded: dict[tuple, list] = {}
    for l in lineup_rows:
        if (int(l["season"]) != season or str(l["league_id"]) not in pool
                or not (1 <= int(l["week"]) <= through) or not l.get("started")):
            continue
        k = (str(l["league_id"]), str(l["team_id"]), int(l["week"]))
        e = fielded.setdefault(k, [0, 0])
        e[0] += 1
        did = played.get((int(l["week"]), l["player_key"]))
        pts = _f(l.get("ppr_points")) if l.get("ppr_points") is not None else _f(l.get("points"))
        if did is True or (pts or 0) > 0:
            e[1] += 1
    gone = {(k[0], k[1]) for k, (st, pl) in fielded.items() if st > 0 and pl * 2 < st}

    T: dict[tuple, dict] = {}
    for key, rs in hist.items():
        rs.sort(key=lambda r: int(r["week"]))
        g = [r for r in rs if r["result"] in ("W", "L", "T")]
        w = sum(1 for r in g if r["result"] == "W")
        l_ = sum(1 for r in g if r["result"] == "L")
        t = sum(1 for r in g if r["result"] == "T")
        lost = [r for r in g if r["result"] in ("L", "T")]
        first = lost[0] if lost else None
        ap = []
        for r in g:
            sc = lw[(L(r), int(r["week"]))]
            n = len(sc)
            if n > 1:
                me = _f(r["points"]) or 0.0
                lower = sum(1 for o in sc if o < me)
                same = sum(1 for o in sc if o == me)
                ap.append((lower + 0.5 * (same - 1)) / (n - 1))
        opp = [r for r in g if r.get("opponent_points") is not None]
        pa_den = sum(lw_avg[(L(r), int(r["week"]))] for r in opp)
        wins = [r for r in g if r["result"] == "W" and r.get("margin") is not None]
        close = min(wins, key=lambda r: (_f(r["margin"]), int(r["week"]))) if wins else None
        big = max(wins, key=lambda r: (_f(r["margin"]), -int(r["week"]))) if wins else None
        bench = [_f(r["bench_left"]) for r in g if r.get("bench_left") is not None]
        last = now[key]
        T[key] = {
            "league_id": key[0], "team_id": key[1],
            "team_name": last.get("team_name"), "manager": last.get("manager"),
            "provider": last.get("provider"), "team_count": last.get("team_count"),
            "scoring_type": last.get("scoring_type"),
            "w": w, "l": l_, "t": t,
            "first_loss": int(first["week"]) if first else None,
            "_first": first,
            "ppg": _avg(_f(r["ppr_points"]) for r in g
                        if r.get("ppr_points") is not None and _f(r["ppr_points"]) > 0),
            "pf": sum(_f(r["points"]) or 0.0 for r in g),
            "pa_rel": (sum(_f(r["opponent_points"]) for r in opp) / pa_den - 1) if pa_den else None,
            "allplay": _avg(ap),
            "bench": _avg(bench),
            "perfect": _avg(1.0 if b <= 0.005 else 0.0 for b in bench),
            "closest": _f(close["margin"]) if close else None,
            "closest_week": int(close["week"]) if close else None,
            "biggest": _f(big["margin"]) if big else None,
            "abandoned": key in gone,
            "names": {str(r.get("team_name")) for r in rs if r.get("team_name") is not None},
        }
    # Points-for rank in the team's own league (1 = the most).
    by_lg: dict[str, list] = {}
    for t in T.values():
        by_lg.setdefault(t["league_id"], []).append(t["pf"])
    for t in T.values():
        t["pf_rank"] = 1 + sum(1 for x in by_lg[t["league_id"]] if x > t["pf"])

    U = {k for k, t in T.items() if t["first_loss"] is None and t["w"] >= 1}
    W = {k for k, t in T.items()
         if t["w"] == 0 and t["t"] == 0 and t["l"] >= 1 and not t["abandoned"]}
    A = {k for k, t in T.items() if not t["abandoned"] or k in U}
    groups = {"u": U, "w": W, "a": A}

    # -- the countdown ------------------------------------------------------
    survival = []
    for k in range(1, through + 1):
        fell = [t for t in T.values() if t["first_loss"] == k]
        survival.append({
            "week": k,
            "alive": sum(1 for t in T.values() if t["first_loss"] is None or t["first_loss"] > k),
            "fell": len(fell),
            "close": sum(1 for t in fell if t["_first"].get("margin") is not None
                         and _f(t["_first"]["margin"]) > -CLOSE),
        })

    # -- trades and the wire, joined by team name within a league ------------
    by_name: dict[tuple, list] = {}
    for k, t in T.items():
        for nm in t["names"]:
            by_name.setdefault((t["league_id"], nm), []).append(k)

    # Trades have been collected for every week since 045 (and backfilled by
    # 049), so a team with none really made none.
    n_trades: dict[tuple, set] = {}
    for h in trade_heads:
        if (int(h["season"]) != season or str(h["league_id"]) not in pool
                or int(h["week"]) > through):
            continue
        for nm in h.get("teams") or []:
            for k in by_name.get((str(h["league_id"]), str(nm)), []):
                n_trades.setdefault(k, set()).add(h["trade_key"])

    wire_weeks: dict[str, set] = {}
    for s in waiver_scans:
        if (int(s["season"]) == season and str(s["league_id"]) in pool
                and 1 <= int(s["week"]) <= through):
            wire_weeks.setdefault(str(s["league_id"]), set()).add(int(s["week"]))
    full_wire = {lg for lg, ws in wire_weeks.items() if len(ws) == through}
    adds: dict[tuple, int] = {}
    failed: dict[tuple, int] = {}
    faab: dict[tuple, int] = {}
    faab_leagues: set = set()
    wire_players: dict[str, set] = {}
    for r in waiver_rows:
        lg = str(r["league_id"])
        if (int(r["season"]) != season or lg not in full_wire
                or int(r["week"]) > through or r["action"] != "add"):
            continue
        ks = by_name.get((lg, str(r.get("team_name"))), [])
        for k in ks:
            if r["status"] == "complete":
                adds[k] = adds.get(k, 0) + 1
                faab[k] = faab.get(k, 0) + int(r.get("bid") or 0)
                wire_players.setdefault(r["player_key"], set()).add(k)
            else:
                failed[k] = failed.get(k, 0) + 1
        if r["status"] == "complete" and int(r.get("bid") or 0) > 0:
            faab_leagues.add(lg)

    # -- where the starting points came from ---------------------------------
    drafted_by: dict[tuple, str] = {}
    picks_of: dict[tuple, list] = {}
    draft_leagues: set = set()
    for d in draft_rows:
        lg = str(d["league_id"])
        if int(d["season"]) != season or lg not in pool:
            continue
        draft_leagues.add(lg)
        if d.get("team_id") is not None:
            drafted_by[(lg, d["player_key"])] = str(d["team_id"])
            if int(d.get("overall") or 0) > 0:
                picks_of.setdefault((lg, str(d["team_id"])), []).append(d)
    got_by_trade: dict[tuple, int] = {}       # (league, team name, player) -> first week
    for p in trade_players:
        if int(p["season"]) != season or str(p["league_id"]) not in pool:
            continue
        kk = (str(p["league_id"]), str(p.get("to_team")), p["player_key"])
        got_by_trade[kk] = min(got_by_trade.get(kk, 99), int(p["week"]))
    src: dict[tuple, list] = {}
    roster_now: dict[tuple, set] = {}
    for l in lineup_rows:
        if int(l["season"]) != season or str(l["league_id"]) not in pool:
            continue
        k = (str(l["league_id"]), str(l["team_id"]))
        if k not in T or not (1 <= int(l["week"]) <= through):
            continue
        if int(l["week"]) == through:
            roster_now.setdefault(k, set()).add(l["player_key"])
        if not l.get("started") or k[0] not in draft_leagues:
            continue
        pts = _f(l.get("points")) or 0.0
        e = src.setdefault(k, [0.0, 0.0, 0.0])
        if drafted_by.get((k[0], l["player_key"])) == k[1]:
            e[0] += pts
        elif any(got_by_trade.get((k[0], nm, l["player_key"]), 99) <= int(l["week"])
                 for nm in T[k]["names"]):
            e[1] += pts
        else:
            e[2] += pts

    for k, t in T.items():
        t["trades"] = len(n_trades.get(k, ()))
        if k[0] in full_wire:
            t["adds"] = adds.get(k, 0)
            t["failed"] = failed.get(k, 0)
            t["faab"] = faab.get(k, 0) if k[0] in faab_leagues else None
        else:
            t["adds"] = t["failed"] = t["faab"] = None
        e = src.get(k)
        tot = sum(e) if e else 0.0
        t["src"] = [x / tot for x in e] if e and tot > 0 else None

    def tape(keys: set) -> dict:
        ts = [T[k] for k in keys]
        if not ts:
            return {"n": 0}
        return {
            "n": len(ts),
            "ppg": _r(_avg(t["ppg"] for t in ts)),
            "pa_rel": _r(_avg(t["pa_rel"] for t in ts), 4),
            "allplay": _r(_avg(t["allplay"] for t in ts), 4),
            "bench": _r(_avg(t["bench"] for t in ts)),
            "perfect": _r(_avg(t["perfect"] for t in ts), 4),
            "trades": _r(_avg(t["trades"] for t in ts)),
            "adds": _r(_avg(t["adds"] for t in ts)),
            "failed": _r(_avg(t["failed"] for t in ts)),
            "faab": _r(_avg(t["faab"] for t in ts)),
            "drafted": _r(_avg(t["src"][0] for t in ts if t["src"]), 4),
            "traded": _r(_avg(t["src"][1] for t in ts if t["src"]), 4),
            "wire": _r(_avg(t["src"][2] for t in ts if t["src"]), 4),
            "with_wire": sum(1 for t in ts if t["adds"] is not None),
            "with_draft": sum(1 for t in ts if t["src"]),
        }

    # -- draft day ----------------------------------------------------------
    slot_of: dict[tuple, int] = {}
    build_of: dict[tuple, dict] = {}
    for k, ps in picks_of.items():
        if k not in T:
            continue
        ps = sorted(ps, key=lambda d: int(d["overall"]))
        first = ps[0]
        tc = int(first.get("team_count") or 12) or 12
        if int(first["overall"]) <= tc:
            slot_of[k] = int(min(12, max(1, _r(_slot(first["overall"], tc), 0))))
        if len(ps) >= 8:
            pos = [(d.get("position") or "").upper() for d in ps]
            qb = next((i + 1 for i, p in enumerate(pos) if p == "QB"), None)
            te = next((i + 1 for i, p in enumerate(pos) if p == "TE"), None)
            r1, r2 = pos[0], pos[1]
            build_of[k] = {
                "rb_rb": r1 == "RB" and r2 == "RB",
                "rb_wr": {r1, r2} == {"RB", "WR"},
                "wr_wr": r1 == "WR" and r2 == "WR",
                "zero_rb": "RB" not in pos[:4],
                "early_qb": qb is not None and qb <= 3,
                "late_qb": qb is None or qb >= 8,
                "early_te": te is not None and te <= 3,
                "r1": r1,
            }

    slots = []
    for s in range(1, 13):
        ks = {k for k, v in slot_of.items() if v == s and k in A}
        n, u, w = len(ks), len(ks & U), len(ks & W)
        slots.append({"slot": s, "n": n, "u": u, "w": w,
                      "rate": _r(_div(u, n), 4), "rate_w": _r(_div(w, n), 4)})

    have_build = {k for k in build_of if k in A}
    nb = {g: len(have_build & ks) for g, ks in groups.items()}

    def flag_row(key: str, test: Callable[[dict], bool]) -> dict:
        ks = {k for k in have_build if test(build_of[k])}
        n, u, w = len(ks), len(ks & U), len(ks & W)
        return {"key": key, "n": n, "u": u, "w": w,
                "share_u": _r(_div(u, nb["u"]), 4), "share_w": _r(_div(w, nb["w"]), 4),
                "share_a": _r(_div(n, nb["a"]), 4), "rate": _r(_div(u, n), 4)}

    builds = [flag_row(b, lambda x, b=b: x[b]) for b in BUILDS]
    first_pick = [flag_row(p, lambda x, p=p: x["r1"] == p) for p in SKILL]

    # -- players ------------------------------------------------------------
    def board(holders: dict[str, set], denom: dict[str, int], extra=None) -> dict:
        out = []
        for key, ks in holders.items():
            info = nfl_players.get(key) or {}
            pos = info.get("position")
            if pos not in SKILL:
                continue
            na, nu, nw = len(ks & A), len(ks & U), len(ks & W)
            if not na:
                continue
            su, sw, sa = _div(nu, denom["u"]), _div(nw, denom["w"]), _div(na, denom["a"])
            card = {"player_key": key, "name": info.get("name") or key,
                    "position": pos, "nfl_team": info.get("nfl_team"),
                    "n_a": na, "n_u": nu, "n_w": nw,
                    "share_u": _r(su, 4), "share_w": _r(sw, 4), "share_a": _r(sa, 4),
                    "lift": _r(_div(su, sa), 2), "lift_w": _r(_div(sw, sa), 2)}
            if extra:
                card.update(extra(key))
            # json_strip_nulls in the SQL: a share with no denominator, or a
            # rostered player's missing ADP, is left out rather than null.
            out.append({k: v for k, v in card.items() if v is not None})
        # Exact arithmetic for the filters and the order, so Python and
        # Postgres can't disagree about a tie: the lift's denominators are
        # the same for every player on a board, so it orders as n_u / n_a.
        F = Fraction
        bar = F(int(MIN_SHARE * 100), 100)
        C = lambda c: c["player_key"].encode()  # noqa: E731  (collate "C")
        thread = sorted((c for c in out if c["n_u"] >= MIN_N and c["n_a"] >= MIN_ALL
                         and denom["u"] and F(c["n_u"], denom["u"]) >= bar),
                        key=lambda c: (-F(c["n_u"], c["n_a"]), -c["n_u"], C(c)))
        most = sorted((c for c in out if c["n_u"] >= 1),
                      key=lambda c: (-c["n_u"], -F(c["n_u"], c["n_a"]), C(c)))
        kryp = sorted((c for c in out if c["n_w"] >= MIN_N and c["n_a"] >= MIN_ALL
                       and denom["w"] and F(c["n_w"], denom["w"]) >= bar),
                      key=lambda c: (-F(c["n_w"], c["n_a"]), -c["n_w"], C(c)))
        return {"thread": thread[:12], "most": most[:12], "kryptonite": kryp[:8]}

    rostered: dict[str, set] = {}
    for k, ps in roster_now.items():
        for p in ps:
            rostered.setdefault(p, set()).add(k)
    den_r = {g: len(ks & set(roster_now)) for g, ks in groups.items()}

    drafted: dict[str, set] = {}
    draft_slot: dict[str, list] = {}
    for d in draft_rows:
        lg = str(d["league_id"])
        if int(d["season"]) != season or lg not in pool or d.get("team_id") is None:
            continue
        k = (lg, str(d["team_id"]))
        if k not in T:
            continue
        drafted.setdefault(d["player_key"], set()).add(k)
        if k in A and int(d.get("overall") or 0) > 0:
            draft_slot.setdefault(d["player_key"], []).append(
                _slot(d["overall"], d.get("team_count")))
    teams_drafted = {k for ks in drafted.values() for k in ks}
    den_d = {g: len(ks & teams_drafted) for g, ks in groups.items()}

    full_teams = {k for k in T if k[0] in full_wire}
    den_w = {g: len(ks & full_teams) for g, ks in groups.items()}

    players = {
        "rostered": board(rostered, den_r),
        "drafted": board(drafted, den_d,
                         lambda key: {"adp": _r(_avg(draft_slot.get(key) or []), 1)}),
        "wire": board(wire_players, den_w) if full_wire else {"thread": [], "most": [], "kryptonite": []},
    }

    # The wire this week, nationally: every redraft league, not just the pool.
    wk_rows = [r for r in waiver_rows
               if int(r["season"]) == season and str(r["league_id"]) not in dyn
               and r["action"] == "add" and int(r["week"]) <= through + 1]
    wire_week = max((int(r["week"]) for r in wk_rows), default=None)
    hot = []
    if wire_week is not None:
        agg: dict[str, dict] = {}
        for r in wk_rows:
            if int(r["week"]) != wire_week:
                continue
            e = agg.setdefault(r["player_key"], {"adds": set(), "failed": 0, "bids": [],
                                                 "name": [], "position": [], "nfl_team": []})
            for f in ("name", "position", "nfl_team"):
                if r.get(f) is not None:
                    e[f].append(r[f])
            if r["status"] == "complete":
                e["adds"].add((str(r["league_id"]), r["move_key"]))
                if int(r.get("bid") or 0) > 0:
                    e["bids"].append(int(r["bid"]))
            else:
                e["failed"] += 1
        for key, e in agg.items():
            info = nfl_players.get(key) or {}
            mx = lambda xs: max(xs) if xs else None  # noqa: E731
            hot.append({"player_key": key, "name": info.get("name") or mx(e["name"]) or key,
                        "position": info.get("position") or mx(e["position"]),
                        "nfl_team": info.get("nfl_team") or mx(e["nfl_team"]),
                        "adds": len(e["adds"]), "failed": e["failed"],
                        "avg_bid": _r(_avg(e["bids"]), 1), "bids": len(e["bids"])})
        hot = sorted((h for h in hot if h["adds"] > 0),
                     key=lambda h: (-h["adds"], -h["failed"], h["player_key"].encode()))[:10]

    # -- the unbeaten draft, round by round (30 Oct, John: "one draft that is
    # like the average draft: who they took in round 1, 2, 3...") -----------
    # A team's k-th pick is its round k, whatever the platform numbered it,
    # so a traded pick or a keeper doesn't shift every round after it.
    board_rows: dict[int, dict] = {}
    for k, ps in picks_of.items():
        if k not in T or k not in A:
            continue
        ps = sorted(ps, key=lambda d: (int(d["overall"]), d["player_key"].encode()))
        for i, d in enumerate(ps[:BOARD_ROUNDS]):
            r = board_rows.setdefault(i + 1, {"n_u": 0, "n_a": 0, "mix_u": {}, "mix_a": {},
                                              "pl": {}})
            pos = _board_pos(d.get("position"))
            r["n_a"] += 1
            r["mix_a"][pos] = r["mix_a"].get(pos, 0) + 1
            e = r["pl"].setdefault(d["player_key"], {"u": 0, "a": 0, "names": [], "pos": []})
            e["a"] += 1
            if d.get("name"):
                e["names"].append(d["name"])
            if d.get("position"):
                e["pos"].append(d["position"])
            if k in U:
                r["n_u"] += 1
                r["mix_u"][pos] = r["mix_u"].get(pos, 0) + 1
                e["u"] += 1
    draft_board = []
    for rnd in sorted(board_rows):
        r = board_rows[rnd]
        top = []
        for key, e in r["pl"].items():
            if e["u"] < 1:
                continue
            info = nfl_players.get(key) or {}
            top.append({"player_key": key,
                        "name": info.get("name") or (max(e["names"]) if e["names"] else None) or key,
                        "position": info.get("position") or (max(e["pos"]) if e["pos"] else None),
                        "nfl_team": info.get("nfl_team"), "u": e["u"], "a": e["a"]})
        top.sort(key=lambda p: (-p["u"], -p["a"], p["player_key"].encode()))
        draft_board.append({"round": rnd, "n_u": r["n_u"], "n_a": r["n_a"],
                            "mix_u": r["mix_u"], "mix_a": r["mix_a"], "players": top[:5]})

    # -- the cards ------------------------------------------------------------
    def card(t: dict) -> dict:
        f = t["_first"]
        return {k: t[k] for k in ("league_id", "team_id", "team_name", "manager",
                                  "provider", "team_count", "scoring_type",
                                  "w", "l", "t", "pf_rank", "closest_week", "first_loss")} | {
            "ppg": _r(t["ppg"]), "pf": _r(t["pf"]), "allplay": _r(t["allplay"], 4),
            "closest": _r(t["closest"]), "biggest": _r(t["biggest"]),
            "fell_points": _r(_f(f["points"])) if f else None,
            "fell_ppr": _r(_f(f.get("ppr_points"))) if f else None,
            "fell_opp": _r(_f(f.get("opponent_points"))) if f else None,
            "fell_margin": _r(_f(f.get("margin"))) if f else None,
        }

    C2 = lambda t: (t["league_id"].encode(), t["team_id"].encode())  # noqa: E731
    # Sorted on rounded values so a float average and a numeric one can't
    # order two equal teams differently; the key settles real ties.
    best = lambda t: (-_r(t["allplay"] or 0, 6), -_r(t["ppg"] or 0, 4), C2(t))  # noqa: E731
    ranked_u = sorted((T[k] for k in U), key=best)
    if U:
        standing = ranked_u
        extinct = None
    else:
        lastw = max((t["first_loss"] for t in T.values() if t["first_loss"]), default=None)
        standing = sorted((t for t in T.values() if t["first_loss"] == lastw), key=best)
        extinct = lastw
    aps = [T[k]["allplay"] for k in U if T[k]["allplay"] is not None]
    verdicts = {
        "real": sum(1 for a in aps if a >= REAL),
        "middle": sum(1 for a in aps if PRETENDER <= a < REAL),
        "pretender": sum(1 for a in aps if a < PRETENDER),
        "led_league": sum(1 for k in U if T[k]["pf_rank"] == 1),
    }
    pretenders = sorted((T[k] for k in U if T[k]["allplay"] is not None),
                        key=lambda t: (_r(t["allplay"], 6), _r(t["ppg"] or 0, 4), C2(t)))[:5]

    fallen = [t for t in T.values() if t["first_loss"] == through]
    std = lambda t: (_f(t["_first"].get("ppr_points"))  # noqa: E731
                     if t["_first"].get("ppr_points") is not None
                     and _f(t["_first"]["ppr_points"]) > 0 else _f(t["_first"]["points"]) or 0.0)
    cruel = sorted(fallen, key=lambda t: (-_r(std(t), 2), C2(t)))[:3]
    nearest = sorted((t for t in fallen if t["_first"].get("margin") is not None),
                     key=lambda t: (-_r(_f(t["_first"]["margin"]), 2), C2(t)))[:3]

    rec: dict[tuple, int] = {}
    tied = 0
    for t in T.values():
        if t["t"]:
            tied += 1
        else:
            rec[(t["w"], t["l"])] = rec.get((t["w"], t["l"]), 0) + 1
    standings = [{"w": w, "l": l_, "n": n} for (w, l_), n in
                 sorted(rec.items(), key=lambda x: (-x[0][0], x[0][1]))]

    return {
        "season": season, "through": through,
        "summary": {
            "leagues": len(pool), "teams": len(T), "field": len(A),
            "unbeaten": len(U), "winless": len(W),
            "winless_all": sum(1 for t in T.values() if t["w"] == 0 and t["t"] == 0 and t["l"] >= 1),
            "abandoned": sum(1 for t in T.values() if t["abandoned"]),
            "dynasty_left": dynasty_left, "incomplete_left": incomplete_left,
            "wire_leagues": len(full_wire), "draft_leagues": len(draft_leagues),
            "extinct": extinct,
        },
        "survival": survival,
        "tape": {g: tape(ks) for g, ks in groups.items()},
        "slots": slots,
        "builds": builds,
        "first_pick": first_pick,
        "players": players,
        "draft_board": draft_board,
        "hot": {"week": wire_week, "players": hot},
        "verdicts": verdicts,
        "cards": [card(t) for t in standing[:CARDS]],
        "pretenders": [card(t) for t in pretenders],
        "fallen": {"n": len(fallen),
                   "close": sum(1 for t in fallen if t["_first"].get("margin") is not None
                                and _f(t["_first"]["margin"]) > -CLOSE),
                   "cruel": [card(t) for t in cruel],
                   "nearest": [card(t) for t in nearest]},
        "standings": standings, "tied": tied,
    }


# ---------------------------------------------------------------------------
# The page: words and geometry, worked out here so the template lays out
# ---------------------------------------------------------------------------

CACHE_SECONDS = 600
_cache: dict = {}


def clear_cache() -> None:
    _cache.clear()


def data(db, season: int) -> dict[str, Any]:
    """The saved country, through the National Desk's stale-while-revalidate
    cache: one refresh at a time, the last good copy kept on a failure."""
    from .around import swr
    return swr(_cache, ("uw", int(season)), lambda: db.undefeated(int(season)) or {},
               ttl=CACHE_SECONDS) or {}


NUM_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve "
             "thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def words(n: int) -> str:
    """41 -> 'forty-one', for headlines that count down. Past 100, digits."""
    n = int(n)
    if n < 20:
        return NUM_WORDS[n]
    if n < 100:
        t, o = divmod(n, 10)
        return TENS[t] + (f"-{NUM_WORDS[o]}" if o else "")
    if n == 100:
        return "one hundred"
    return f"{n:,}"


def title(s: str) -> str:
    """'forty-one' -> 'Forty-One'."""
    return "-".join(p[:1].upper() + p[1:] for p in s.split("-"))


def _pct(x: Optional[float], digits: int = 0) -> str:
    if x is None:
        return "–"
    v = x * 100
    if digits == 0 and 0 < abs(v) < 1:
        return f"{v:.1f}%"
    return f"{v:.{digits}f}%"


def _n(v: Optional[float], d: int = 1) -> str:
    return "–" if v is None else f"{float(v):,.{d}f}"


def describe(c: dict, cap: bool = False) -> str:
    from .around import describe as d
    return d(c, cap)


# ---------------------------------------------------------------------------
# Head to head (30 Oct, John: the tale of the tape was "a lot of information
# on one boring chart"; each measure gets its own card, swiped through).
# ---------------------------------------------------------------------------

def _bars(u, w, a, lo: float = 0.0, hi: Optional[float] = None) -> dict:
    """Bar widths in percent of one shared scale, from zero."""
    vals = [v for v in (u, w, a) if v is not None]
    top = hi if hi is not None else (max(vals) * 1.06 if vals else 1.0) or 1.0
    W = lambda v: None if v is None else round(100 * max(0.0, v - lo) / (top - lo), 1)  # noqa: E731
    return {"wu": W(u), "ww": W(w), "wa": W(a)}


def deck(raw: dict) -> list[dict]:
    """One card per measure, unbeaten against winless, each with its two
    numbers, two bars on one scale with every team as a tick, and one
    sentence that says which way it cuts. Only measures we hold."""
    tape = raw.get("tape") or {}
    U, Wl, A = tape.get("u") or {}, tape.get("w") or {}, tape.get("a") or {}
    cards = []

    def have(k):
        return U.get(k) is not None and Wl.get(k) is not None

    if have("ppg"):
        u, w, a = U["ppg"], Wl["ppg"], A.get("ppg")
        cards.append({"key": "ppg", "title": "Points a week", "unit": "in PPR",
                      "u": f"{u:.1f}", "w": f"{w:.1f}", "a": _n(a), **_bars(u, w, a),
                      "line": f"Unbeaten teams score {u - w:.1f} more a week than winless ones."})
    if have("allplay"):
        u, w, a = U["allplay"], Wl["allplay"], A.get("allplay")
        cards.append({"key": "allplay", "title": "Against everybody",
                      "unit": "of games won if they played their whole league every week",
                      "u": _pct(u), "w": _pct(w), "a": _pct(a), **_bars(u, w, a, hi=1.0),
                      "line": (f"Play the whole league every week and the unbeaten still win "
                               f"{_pct(u)}. The winless win {_pct(w)}.")})
    if have("pa_rel"):
        u, w = U["pa_rel"], Wl["pa_rel"]
        m = max(abs(u), abs(w), 0.01) * 1.25
        side = lambda v: ("under" if v < 0 else "over")  # noqa: E731
        cards.append({"key": "pa_rel", "title": "The schedule", "diverge": True,
                      "unit": "opponents\u2019 points against their league\u2019s average",
                      "u": ("even" if abs(u) < 0.005 else ("−" if u < 0 else "+") + _pct(abs(u))),
                      "w": ("even" if abs(w) < 0.005 else ("−" if w < 0 else "+") + _pct(abs(w))),
                      "a": None,
                      "du": round(50 * u / m, 1), "dw": round(50 * w / m, 1),
                      "line": (f"Unbeaten teams\u2019 opponents scored {_pct(abs(u))} {side(u)} their "
                               f"league\u2019s average. The winless drew opponents {_pct(abs(w))} "
                               f"{side(w)} it." if abs(u) >= 0.005 else
                               "Unbeaten teams faced an average schedule. They earned it.")})
    if have("bench"):
        u, w, a = U["bench"], Wl["bench"], A.get("bench")
        diff = w - u
        cards.append({"key": "bench", "title": "Left on the bench", "unit": "points a week",
                      "u": f"{u:.1f}", "w": f"{w:.1f}", "a": _n(a), **_bars(u, w, a),
                      "line": (f"Unbeaten teams start the right players: {diff:.1f} fewer points "
                               f"a week sitting on the bench." if diff > 1.0 else
                               "About the same. Lineups aren’t what separates them.")})
    if have("adds"):
        u, w, a = U["adds"], Wl["adds"], A.get("adds")
        cards.append({"key": "adds", "title": "Waiver pickups", "unit": "per team this season",
                      "u": f"{u:.1f}", "w": f"{w:.1f}", "a": _n(a), **_bars(u, w, a),
                      "line": ("The unbeaten work the wire harder." if u - w > 0.5 else
                               "The unbeaten don’t chase the wire. Winners don’t churn."
                               if w - u > 0.5 else
                               "Same number of pickups. It’s who, not how many.")})
    if have("faab"):
        u, w, a = U["faab"], Wl["faab"], A.get("faab")
        cards.append({"key": "faab", "title": "FAAB spent", "unit": "per team, in leagues that bid",
                      "u": f"${u:.0f}", "w": f"${w:.0f}", "a": (f"${a:.0f}" if a is not None else None),
                      **_bars(u, w, a),
                      "line": ("They spend it." if u - w > 5 else
                               "They save it." if w - u > 5 else
                               "Roughly the same budget, spent on different players.")})
    if have("trades"):
        u, w, a = U["trades"], Wl["trades"], A.get("trades")
        cards.append({"key": "trades", "title": "Trades made", "unit": "per team this season",
                      "u": f"{u:.1f}", "w": f"{w:.1f}", "a": _n(a), **_bars(u, w, a),
                      "line": ("They deal." if u - w > 0.3 else
                               "They don’t need to trade." if w - u > 0.3 else
                               "Trading isn’t the difference either way.")})
    v = raw.get("verdicts") or {}
    tot = sum(int(v.get(k) or 0) for k in ("real", "middle", "pretender"))
    if tot:
        segs = [{"key": k, "label": lab, "n": int(v.get(k) or 0),
                 "w": round(100 * int(v.get(k) or 0) / tot, 1)}
                for k, lab in (("real", "The real thing"), ("middle", "Doing enough"),
                               ("pretender", "Pretenders"))]
        cards.append({"key": "real", "title": "Real, or lucky?", "verdict": True, "segs": segs,
                      "unit": "every unbeaten team, by how it would do against its whole league",
                      "line": (f"{segs[0]['n']:,} would win three games in four against anybody in "
                               f"their league. "
                               + ("None of them would lose most weeks. No frauds yet."
                                  if segs[2]["n"] == 0 else
                                  "One would lose most of them, and is unbeaten on schedule."
                                  if segs[2]["n"] == 1 else
                                  f"{segs[2]['n']:,} would lose most of them, and are unbeaten "
                                  f"on schedule."))})
    return cards


# ---------------------------------------------------------------------------
# The unbeaten draft (30 Oct, John: "one draft that is like the average
# draft: who they took in round 1, 2, 3...").
# ---------------------------------------------------------------------------

POS_ORDER = ("QB", "RB", "WR", "TE", "K", "DEF", "OTHER")
POS_NAME = {"QB": "a quarterback", "RB": "a running back", "WR": "a receiver",
            "TE": "a tight end", "K": "a kicker", "DEF": "a defense", "OTHER": "someone else"}


def board(raw: dict, min_teams: int = 3) -> list[dict]:
    """Round by round: what the unbeaten took (the position mix, against
    every team's), and who (the players they took most in that round)."""
    from .around import photo
    out = []
    for r in raw.get("draft_board") or []:
        nu, na = int(r.get("n_u") or 0), int(r.get("n_a") or 0)
        if nu < min_teams or not na:
            continue
        mu, ma = r.get("mix_u") or {}, r.get("mix_a") or {}
        mix = [{"pos": p, "u": round(100 * mu.get(p, 0) / nu, 1),
                "a": round(100 * ma.get(p, 0) / na, 1)} for p in POS_ORDER
               if mu.get(p) or ma.get(p)]
        top = max(mix, key=lambda m: m["u"]) if mix else None
        players = [{**p, "photo": photo(p), "share": _pct(p["u"] / nu),
                    "share_a": _pct(p["a"] / na)} for p in (r.get("players") or [])[:3]]
        # The round's story: the position they took most, and how that
        # compares with everybody else in the same round.
        if top:
            gap = top["u"] - top["a"]
            line = (f"{top['u']:.0f}% took {POS_NAME[top['pos']]}"
                    + (f", against {top['a']:.0f}% of all teams." if abs(gap) >= 5 else
                       ", about the same as everybody."))
        else:
            line = ""
        out.append({"round": r["round"], "n_u": nu, "mix": mix, "top": top,
                    "players": players, "line": line,
                    "lead": bool(top and top["u"] - top["a"] >= 5)})
    return out


def _thread_card(p: dict, group: str = "u") -> dict:
    """A shared player in words anybody can read: 'X% of unbeaten teams have
    him. Y% of all teams do.' (30 Oct, John: "X percent of everyone" didn't
    make sense.) `group` is u (unbeaten) or w (winless)."""
    from .around import photo
    s = p.get("share_u" if group == "u" else "share_w")
    a = p.get("share_a")
    lift = p.get("lift" if group == "u" else "lift_w")
    return {**p, "photo": photo(p), "g": _pct(s), "all": _pct(a),
            "gw": round(100 * (s or 0), 1), "aw": round(100 * (a or 0), 1),
            "lift_txt": (f"{lift:.1f}x" if lift is not None else None)}


def _clean(c: dict, staff: bool) -> dict:
    """A team as the public sees it: described, never named."""
    if staff:
        return c
    return {k: v for k, v in c.items() if k not in ("team_name", "manager")}


def verdict(ap: Optional[float]) -> tuple[str, str]:
    """(word, class) for an unbeaten team's all-play rate."""
    if ap is None:
        return "Unrated", "mid"
    if ap >= REAL:
        return "The real thing", "real"
    if ap >= PRETENDER:
        return "Doing enough", "mid"
    return "Pretender", "fake"


def _ticket(c: dict, staff: bool) -> dict:
    c = _clean(c, staff)
    word, cls = verdict(c.get("allplay"))
    games = (c.get("w") or 0) + (c.get("l") or 0) + (c.get("t") or 0)
    ap = c.get("allplay")
    from .around import ordinal
    return {**c, "record": f"{c.get('w')}-{c.get('l')}" + (f"-{c['t']}" if c.get("t") else ""),
            "rank_txt": ordinal(int(c["pf_rank"])) if c.get("pf_rank") else None,
            "desc": describe(c, True), "verdict": word, "vcls": cls,
            "scores_like": (f"{round(ap * games)}-{games - round(ap * games)}"
                            if ap is not None and games else None),
            "close_txt": (f"Won by {c['closest']:.1f} in week {c['closest_week']}"
                          if c.get("closest") is not None else None)}


def build(raw: dict, *, staff: bool = False) -> dict[str, Any]:
    """Everything the template draws. `raw` is data(); `staff` keeps team
    names on the cards (for checking the data), otherwise they're stripped
    here so the template can't print one by accident."""
    raw = raw or {}
    through = raw.get("through")
    s = raw.get("summary") or {}
    teams = int(s.get("teams") or 0)
    if not through or not teams:
        return {"ready": False, "summary": s}
    surv = raw.get("survival") or []
    alive = int(surv[-1]["alive"]) if surv else int(s.get("unbeaten") or 0)
    unbeaten = int(s.get("unbeaten") or 0)
    fell = int(surv[-1]["fell"]) if surv else 0
    close = int(surv[-1]["close"]) if surv else 0
    coin = teams / 2 ** through
    over = (alive / coin) if coin else None

    if unbeaten == 0:
        state = "none"
    elif unbeaten == 1:
        state = "one"
    elif unbeaten <= 10:
        state = "final"
    elif unbeaten <= CARDS:
        state = "pack"
    else:
        state = "field"
    done = through >= MAX_WEEK

    w = words(unbeaten)
    if state == "none":
        head = "Nobody’s Perfect Anymore."
    elif state == "one":
        head = ("One Team Went 14-0." if done else "One Team Left in America.")
    elif done:
        head = f"{title(w) if unbeaten <= 100 else f'{unbeaten:,}'} Teams Went 14-0."
    elif state == "final":
        head = f"The Final {title(w)}."
    elif state == "pack":
        head = f"{title(w)} Left."
    else:
        head = f"{unbeaten:,} Teams Haven’t Lost Yet."

    if coin >= 10:
        coin_txt = f"{round(coin):,}"
    elif coin >= 1:
        coin_txt = f"about {round(coin)}"
    else:
        coin_txt = "none"
    deck_bits = []
    if state == "none":
        ext = s.get("extinct")
        n_last = len(raw.get("cards") or [])
        deck_bits.append(f"The last {words(n_last) if n_last > 1 else 'one'} "
                         f"fell in week {ext}. Somebody had to.")
    else:
        deck_bits.append(f"After week {through}, {unbeaten:,} of {teams:,} redraft teams "
                         f"are still {through}-0. A coin would have left {coin_txt}.")
    if fell and state != "none":
        deck_bits.append(f"Week {through} took out {fell:,}"
                         + (f", {close:,} of them by less than a field goal." if close else "."))
    deck_txt = " ".join(deck_bits)

    room = [{"week": 0, "n": 1000, "count": teams}] + [
        {"week": x["week"], "n": int(round(1000 * x["alive"] / teams)), "count": int(x["alive"])}
        for x in surv]
    in_room = room[-1]["n"]
    if unbeaten and in_room == 0:
        room_line = (f"In a room of 1,000 teams, not even one would still be perfect. "
                     f"There are {unbeaten:,} in the whole country.")
    elif unbeaten:
        room_line = (f"In a room of 1,000 teams, {in_room} "
                     f"{'is' if in_room == 1 else 'are'} still perfect.")
    else:
        room_line = "In a room of 1,000 teams, nobody is perfect. Nobody in the country is."

    P = raw.get("players") or {}
    R = P.get("rostered") or {}
    # The four players on the cards: the ones most over-represented among
    # the unbeaten, or, when too few are left for that to mean anything,
    # simply the ones most of them have.
    lead_rows = R.get("thread") if len(R.get("thread") or []) >= 2 else R.get("most")
    wire_ready = bool(s.get("wire_leagues"))
    cards = [_ticket(c, staff) for c in raw.get("cards") or []]

    return {
        "ready": True, "season": raw.get("season"), "through": through,
        "summary": s, "teams": teams, "alive": alive, "unbeaten": unbeaten,
        "state": state, "done": done, "headline": head, "deck": deck_txt,
        "coin": coin, "coin_txt": coin_txt, "over": over,
        "over_txt": (f"{over:.1f}x" if over and over >= 1.05 else None),
        "fell": fell, "close": close,
        "room": room, "room_line": room_line,
        "h2h": deck(raw),
        "board": board(raw),
        "thread": [_thread_card(p) for p in (lead_rows or [])[:4]],
        "thread_more": [_thread_card(p) for p in (lead_rows or [])[4:12]],
        "kryptonite": [_thread_card(p, "w") for p in R.get("kryptonite") or []],
        "wire_most": ([_thread_card(p) for p in ((P.get("wire") or {}).get("most") or [])[:8]]
                      if wire_ready else []),
        "wire_ready": wire_ready,
        "cards": cards, "show_all": 0 < unbeaten <= CARDS or state == "none",
    }


def my_team(db, user: Optional[dict], season: int, through: Optional[int]) -> Optional[dict]:
    """The signed-in reader's own team, if we KNOW which one it is
    (leagues.my_team_id, saved at connect time): still perfect, or when it
    fell and how. The one place this page names a team, to its owner.
    Never raises; a convenience is never worth the page."""
    if not user or not through:
        return None
    try:
        leagues = [l for l in db.market_leagues_for_user(user["id"])
                   if int(l.get("season") or 0) == int(season) and l.get("my_team_id")][:20]
        if not leagues:
            return None
        rows = db.team_weeks_season_for_leagues(season, [str(l["id"]) for l in leagues]) or []
    except Exception as exc:  # noqa: BLE001
        print(f"[undefeated] no team for the viewer: {exc}", flush=True)
        return None
    best = None
    for lg in leagues:
        mine = sorted((r for r in rows if str(r.get("league_id")) == str(lg["id"])
                       and str(r.get("team_id")) == str(lg.get("my_team_id"))
                       and 1 <= int(r.get("week") or 0) <= int(through)),
                      key=lambda r: int(r["week"]))
        g = [r for r in mine if r.get("result") in ("W", "L", "T")]
        if not g:
            continue
        w = sum(1 for r in g if r["result"] == "W")
        l_ = sum(1 for r in g if r["result"] == "L")
        t = sum(1 for r in g if r["result"] == "T")
        first = next((r for r in g if r["result"] in ("L", "T")), None)
        me = {"name": (mine[-1].get("team_name") or "").strip() or "Your team",
              "league": lg.get("paper_name") or lg.get("league_name") or "your league",
              "w": w, "l": l_, "t": t, "alive": first is None and w > 0,
              "record": f"{w}-{l_}" + (f"-{t}" if t else ""),
              "fell_week": int(first["week"]) if first else None,
              "fell_points": _f(first.get("points")) if first else None,
              "fell_opp": _f(first.get("opponent_points")) if first else None,
              "tie": bool(first and first["result"] == "T")}
        # An unbeaten team beats a beaten one; then the later fall.
        rank = (me["alive"], me["fell_week"] or 99, me["w"])
        if best is None or rank > best[0]:
            best = (rank, me)
    return best[1] if best else None
