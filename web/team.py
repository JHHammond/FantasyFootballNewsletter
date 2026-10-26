"""My Team (/team) and its Portfolio tab (John, 25 Oct 2026).

    "a My Team tab where you can view your team, view the value of your team
    over time. It ranks your team positionally in your league ... and then
    a portfolio tab that basically just showed the net gain in value for
    players on your team, almost like a stock portfolio."

Everything here is arithmetic on things the site already has: the trade
market's daily prices (web/trade_rating.py), every league's weekly rosters
(lineup_players), its draft (draft_picks) and its trades (trade_players).
`assemble` is pure — plain data in, the page out — so it is tested without a
database; `build` is the thin part that fetches.

THE UNIT. The market prints a PLACE: #1 is the most expensive player. Places
cannot be added up (two #40s are not a #80, and certainly not a #20), and a
team is a sum. So every place is read back through the draft-value curve the
market is anchored on — `trade_rating.slot_value`, with the steepness the
trades themselves fitted — and scaled so the #1 player is worth 100. That is
"draft capital": the #12 player is worth about half the #1, the #50 about a
sixth. A team's value is the sum of its players' values, which is the one
thing the market's model does assume (a bundle is worth its parts).

Kickers and defences have no value. They are streamed, not held, and the
market deliberately keeps them off its board; they still get a production
rank, because points are points.

FAIR PACKAGES (the trade partners) use the market's own per-player `v` and
roster-spot charge, through player_page.offers — the same arithmetic as the
player page's "Get him / Sell him", so the two pages never disagree about
whether a deal is even.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from datetime import date
from typing import Any, Iterable, Optional

#: The hexagon, clockwise from the top. K and DEF sit together at the bottom
#: so they never split up the positions that matter most.
POSITIONS = ("QB", "RB", "WR", "TE", "DEF", "K")
#: Positions with a market value. K and DEF are ranked on points only.
VALUED = ("QB", "RB", "WR", "TE")
#: Which players each flex slot can hold.
ELIGIBLE = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"},
    "REC_FLEX": {"WR", "TE"}, "WRRB_FLEX": {"WR", "RB"},
    "FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}
#: Dedicated slots first, then the narrowest flex, so a flex never takes a
#: player a dedicated slot needed.
FILL_ORDER = ("QB", "RB", "WR", "TE", "REC_FLEX", "WRRB_FLEX", "FLEX", "SUPER_FLEX")
NOT_STARTERS = {"BN", "IR", "TAXI", "", None}
#: A league's lineup when we can't read one off its rosters.
DEFAULT_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}
#: How many leaguemates are suggested as trade partners, and packages each.
PARTNERS = 3
PACKAGES = 2
#: Points on the value chart. Days beyond this are thinned, never the last.
CHART_POINTS = 90
#: The ticker and the "this week" column look back this far.
WEEK_DAYS = 7


def _pos(p: Optional[str]) -> Optional[str]:
    p = (p or "").upper()
    return "DEF" if p in ("DST", "D/ST") else (p or None)


def rank_of(scores: dict[str, float], key: str) -> int:
    """1 = the most. Ties share the better place, the way a table prints."""
    mine = scores.get(key, 0.0)
    return 1 + sum(1 for k, v in scores.items() if k != key and v > mine + 1e-9)


def ordinal(n: Optional[int]) -> str:
    if not n:
        return "–"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

class Prices:
    """The market, re-read as values that add up.

    `rating` is the cached build from trade_rating (after apply_injuries).
    `adp` is the national ADP of every rated player, for turning a league's
    own draft pick into a place on the market's scale."""

    def __init__(self, rating: dict, adp: Optional[dict[str, float]] = None):
        from web import trade_rating
        self.decay = float(rating.get("decay") or trade_rating.DECAY)
        self.shift = float(rating.get("shift") or trade_rating.SHIFT)
        self._curve = trade_rating.slot_value
        self.rows = {p["k"]: p for p in rating.get("pickable") or []}
        self.injured = {p["k"]: p for p in rating.get("injured") or []}
        daily = rating.get("daily") or {}
        self.base = date.fromisoformat(daily["base"]).toordinal() if daily.get("base") else None
        self.last = int(daily.get("last") or 0)
        self.lines = daily.get("lines") or {}
        rated = set(self.rows) | set(self.injured)
        self._adps = sorted(v for k, v in (adp or {}).items() if k in rated and v)
        self._dense: dict[str, list] = {}

    def value(self, place: Optional[float]) -> float:
        if not place or place <= 0:
            return 0.0
        return round(100.0 * self._curve(float(place), self.decay, self.shift), 1)

    def now(self, key: str) -> float:
        """Today's value. A player on IR is off the board, and worth nothing
        to a team this season; a player the market can't price is 0 too and
        is flagged on the page rather than guessed at."""
        row = self.rows.get(key)
        return self.value(row["rank"]) if row else 0.0

    def priced(self, key: str) -> bool:
        return key in self.rows

    def dense(self, key: str) -> list[float]:
        """His value on every day from the draft (day 0) to the last close,
        carried forward across days with no close."""
        got = self._dense.get(key)
        if got is not None:
            return got
        line = self.lines.get(key) or []
        out, j, cur = [], 0, 0.0
        for t in range(self.last + 1):
            while j < len(line) and line[j][0] <= t:
                cur = self.value(line[j][1])
                j += 1
            out.append(cur)
        if key in self.rows and out:
            out[-1] = self.now(key)        # the line ends on today's place
        elif key in self.injured and out:
            out[-1] = 0.0
        self._dense[key] = out
        return out

    def on(self, key: str, t: int) -> float:
        d = self.dense(key)
        return d[max(0, min(t, len(d) - 1))] if d else 0.0

    def offset(self, ordinal_day: int) -> int:
        return 0 if self.base is None else max(0, min(self.last, ordinal_day - self.base))

    def draft_value(self, slot: float) -> float:
        """A pick in a twelve-team draft, as a value: the pick's ADP is read
        as the place it stands for among the players the market rates, the
        same transform the market's own lines start from."""
        if not slot:
            return 0.0
        if not self._adps:
            return self.value(slot)
        a, n = self._adps, len(self._adps)
        if slot > a[-1]:
            place = n + (slot - a[-1])
        else:
            # Between two rated players' ADPs, part way between their places:
            # a draft's picks are a continuum, the market's places are not.
            i = bisect_left(a, slot - 1e-9)
            if i == 0 or a[i] <= a[i - 1]:
                place = i + 1
            else:
                place = i + (slot - a[i - 1]) / (a[i] - a[i - 1])
        return self.value(max(1.0, place))


# ---------------------------------------------------------------------------
# The league, week by week
# ---------------------------------------------------------------------------

def rosters_by_week(lineups: Iterable[dict]) -> dict[int, dict[str, set]]:
    out: dict[int, dict[str, set]] = {}
    for r in lineups:
        if r.get("player_key"):
            out.setdefault(int(r["week"]), {}).setdefault(str(r["team_id"]), set()).add(r["player_key"])
    return out


def lineup_slots(lineups: Iterable[dict], week: int) -> dict[str, int]:
    """The league's starting lineup, read off the most common set of
    started slots in a week. Falls back to a standard lineup."""
    per_team: dict[str, Counter] = {}
    for r in lineups:
        if int(r["week"]) == week and r.get("started") and r.get("slot") not in NOT_STARTERS:
            per_team.setdefault(str(r["team_id"]), Counter())[str(r["slot"]).upper()] += 1
    if not per_team:
        return dict(DEFAULT_SLOTS)
    common, _ = Counter(tuple(sorted(c.items())) for c in per_team.values()).most_common(1)[0]
    slots = {("DEF" if k in ("DST", "D/ST") else k): v for k, v in common}
    return slots or dict(DEFAULT_SLOTS)


def starter_values(roster: Iterable[str], pos: dict[str, str], prices: Prices,
                   slots: dict[str, int]) -> dict[str, Any]:
    """Fill the league's lineup with the roster's most valuable players and
    say what the starters at each position are worth. A flex filled with a
    third RB counts as RB value: that is what the team is actually fielding."""
    left = sorted((k for k in roster if pos.get(k) in VALUED and prices.now(k) > 0),
                  key=lambda k: -prices.now(k))
    by_pos = {p: 0.0 for p in VALUED}
    starters: list[str] = []
    for slot in FILL_ORDER:
        ok = ELIGIBLE[slot]
        for _ in range(int(slots.get(slot, 0))):
            k = next((k for k in left if pos.get(k) in ok), None)
            if k is None:
                break
            left.remove(k)
            starters.append(k)
            by_pos[pos[k]] += prices.now(k)
    return {"by_pos": {p: round(v, 1) for p, v in by_pos.items()}, "starters": starters}


def production(lineups: Iterable[dict], pos: dict[str, str]) -> dict[str, dict[str, float]]:
    """Season points from each team's STARTERS, by the player's position
    (not the slot: a WR in the flex scored as a WR)."""
    out: dict[str, dict[str, float]] = {}
    for r in lineups:
        if not r.get("started") or r.get("slot") in NOT_STARTERS:
            continue
        p = pos.get(r["player_key"]) or _pos(r.get("slot"))
        if p not in POSITIONS:
            continue
        t = out.setdefault(str(r["team_id"]), {q: 0.0 for q in POSITIONS})
        t[p] += float(r.get("points") or 0)
    return out


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def assemble(*, my_team: str, lineups: list[dict], team_weeks: list[dict],
             trades: list[dict], draft: list[dict], positions: dict[str, str],
             prices: Prices, pickable: dict[str, dict], spot: float,
             opener: int, latest: int) -> dict[str, Any]:
    """Everything /team draws for one team in one league."""
    from web import player_page as pp

    my_team = str(my_team)
    weeks = rosters_by_week(lineups)
    if not weeks:
        return {}
    last_week = max([w for w in weeks if w <= latest] or list(weeks))
    now = weeks[last_week]
    if my_team not in now:
        return {}
    teams = sorted(now)
    n = len(teams)

    names: dict[str, str] = {}
    aliases: dict[str, set] = {}
    for r in sorted(team_weeks, key=lambda r: int(r.get("week") or 0)):
        nm = (r.get("team_name") or "").strip()
        if nm:
            names[str(r["team_id"])] = nm
            aliases.setdefault(str(r["team_id"]), set()).add(nm)
    for t in teams:
        names.setdefault(t, f"Team {t}")

    pos = {k: _pos(v) for k, v in positions.items() if v}
    for d in draft:
        if d.get("position") and d["player_key"] not in pos:
            pos[d["player_key"]] = _pos(d["position"])
    for r in lineups:                          # a slot that IS a position says so
        s = _pos(r.get("slot"))
        if s in POSITIONS and r["player_key"] not in pos:
            pos[r["player_key"]] = s

    # ---- value: each team today, its rank, and the line over the season
    slots = lineup_slots(lineups, last_week)
    team_value = {t: round(sum(prices.now(k) for k in now[t] if pos.get(k) in VALUED), 1) for t in teams}

    def roster_on(t: int) -> int:
        if prices.base is None:
            return last_week
        wk = (prices.base + t - opener) // 7 + 1
        have = [w for w in weeks if w <= wk]
        return max(have) if have else min(weeks)

    days = list(range(prices.last + 1)) if prices.base is not None else []
    if len(days) > CHART_POINTS:
        step = len(days) / (CHART_POINTS - 1)
        days = sorted({int(round(i * step)) for i in range(CHART_POINTS - 1)} | {days[-1]})
    lines: dict[str, list] = {t: [] for t in teams}
    for t in days:
        ros = weeks[roster_on(t)]
        for team in teams:
            lines[team].append(round(sum(prices.on(k, t) for k in ros.get(team, ())
                                         if pos.get(k) in VALUED), 1))
    mine_line = lines[my_team]
    week_ago = next((i for i in range(len(days) - 1, -1, -1) if days[i] <= days[-1] - WEEK_DAYS), 0) \
        if days else 0
    header = {
        "value": team_value[my_team],
        "rank": rank_of(team_value, my_team), "teams": n,
        "week": round(mine_line[-1] - mine_line[week_ago], 1) if mine_line else None,
        "draft": round(mine_line[-1] - mine_line[0], 1) if mine_line else None,
        "unpriced": sorted(k for k in now[my_team] if pos.get(k) in VALUED and not prices.priced(k)),
        "league_avg": round(sum(team_value.values()) / n, 1),
    }

    # ---- positional ranks: production (all six) and value (the four)
    prod = production(lineups, pos)
    sv = {t: starter_values(now[t], pos, prices, slots) for t in teams}
    ranks = []
    for p in POSITIONS:
        pts = {t: (prod.get(t) or {}).get(p, 0.0) for t in teams}
        row = {"pos": p, "prod_rank": rank_of(pts, my_team), "points": round(pts[my_team], 1),
               "best_points": round(max(pts.values()), 1), "value_rank": None, "value": None}
        if p in VALUED:
            vals = {t: sv[t]["by_pos"][p] for t in teams}
            row.update(value_rank=rank_of(vals, my_team), value=vals[my_team])
        ranks.append(row)
    team_ranks = {t: {p: rank_of({u: sv[u]["by_pos"][p] for u in teams}, t) for p in VALUED}
                  for t in teams}
    team_prod = {t: {p: rank_of({u: (prod.get(u) or {}).get(p, 0.0) for u in teams}, t)
                     for p in POSITIONS} for t in teams}

    # ---- trade partners: complementary leaguemates, and even packages.
    # First who fits (they're strong where you're weak, and the reverse),
    # then packages for the best fits in order, so nobody of yours is
    # offered to two different teams.
    mine_r = team_ranks[my_team]
    my_starters = set(sv[my_team]["starters"])
    fits = []
    for t in teams:
        if t == my_team:
            continue
        theirs = team_ranks[t]
        need = max(VALUED, key=lambda p: (mine_r[p] - theirs[p], -theirs[p]))
        give = max(VALUED, key=lambda p: (theirs[p] - mine_r[p], -mine_r[p]))
        gain_me, gain_them = mine_r[need] - theirs[need], theirs[give] - mine_r[give]
        if need == give or gain_me <= 0 or gain_them <= 0:
            continue
        fits.append((min(gain_me, gain_them) + 0.1 * (gain_me + gain_them), t, need, give))
    fits.sort(key=lambda f: (-f[0], f[1]))

    partners, offered = [], set()
    for score, t, need, give in fits:
        theirs = team_ranks[t]
        # Their spare piece at what you need (not their best there, if they
        # have depth), and only somebody who'd start for you.
        floor = min((prices.now(k) for k in my_starters if pos.get(k) == need), default=0.0)
        theirs_at = sorted((k for k in now[t] if pos.get(k) == need and k in pickable),
                           key=lambda k: -prices.now(k))
        targets = [k for k in theirs_at[1:] if prices.now(k) > floor] or theirs_at[:1]
        pool = {k for k in now[my_team] if pos.get(k) == give and k in pickable} - offered
        # Your best player at the position you're dealing from stays.
        best_give = max(pool, key=prices.now, default=None)
        if best_give:
            pool.discard(best_give)
        packs = []
        for k in targets[:3]:
            used = {x["k"] for pk in packs for x in pk["give"]}
            got = [o for o in pp.offers(k, pool, pickable, spot, []) if not used & set(o["keys"])]
            if got:
                packs.append({"take": pickable[k], "give": got[0]["players"], "ratio": got[0]["ratio"]})
            if len(packs) >= PACKAGES:
                break
        if not packs:
            continue
        offered |= {x["k"] for pk in packs for x in pk["give"]}
        partners.append({
            "team_id": t, "team": names[t], "need": need, "give": give,
            "my_need_rank": mine_r[need], "their_need_rank": theirs[need],
            "my_give_rank": mine_r[give], "their_give_rank": theirs[give],
            "packages": packs, "hex": geometry(hexagon(team_prod[t], theirs, n)),
        })
        if len(partners) >= PARTNERS:
            break

    folio = portfolio(my_team, aliases.get(my_team, {names[my_team]}), weeks, last_week,
                      trades, draft, pos, prices, pickable, opener)
    held = [h["k"] for h in folio["holdings"]]
    folio["line"] = [round(sum(prices.on(k, t) for k in held), 1) for t in days]

    return {
        "team_id": my_team, "team": names[my_team], "teams": [{"id": t, "name": names[t]} for t in teams],
        "week": last_week, "header": header, "slots": slots, "scale12": prices.value(12),
        "chart": {"start": date.fromordinal(prices.base).isoformat() if prices.base else None,
                  "days": days, "mine": mine_line,
                  "others": [lines[t] for t in teams if t != my_team],
                  "avg": [round(sum(lines[t][i] for t in teams) / n, 1) for i in range(len(days))]},
        "ranks": ranks, "story": story(ranks),
        "hex": geometry(hexagon(team_prod[my_team], mine_r, n)),
        "partners": partners,
        "portfolio": folio,
    }


def hexagon(prod_ranks: dict[str, int], value_ranks: dict[str, int], n: int) -> dict:
    """The radar's two shapes as 0..1 radii: 1st place on the rim, last on a
    small inner ring (not the centre, or one bad position would pinch the
    whole shape to a point)."""
    def r(rank):
        return 1.0 if n <= 1 else round(0.16 + 0.84 * (n - rank) / (n - 1), 3)
    return {"n": n,
            "prod": [{"pos": p, "rank": prod_ranks.get(p), "r": r(prod_ranks.get(p, n))} for p in POSITIONS],
            "value": [{"pos": p, "rank": value_ranks.get(p), "r": r(value_ranks.get(p, n))}
                      if p in VALUED else {"pos": p, "rank": None, "r": None} for p in POSITIONS]}


def geometry(hexa: dict, size: int = 320, pad: int = 46) -> dict:
    """The radar in SVG units, so the template draws it without any maths
    and it renders with scripts off. Vertices clockwise from QB at the top."""
    import math
    c = size / 2
    R = c - pad
    n = hexa["n"]

    def pt(i: int, r: float) -> tuple[float, float]:
        a = math.radians(-90 + 60 * i)
        return round(c + R * r * math.cos(a), 1), round(c + R * r * math.sin(a), 1)

    def path(rs: list) -> str:
        return " ".join(f"{x},{y}" for x, y in (pt(i, r) for i, r in rs))

    def radius(rank):
        return 1.0 if n <= 1 else 0.16 + 0.84 * (n - rank) / (n - 1)
    # Rings at 1st, the thirds and last, labelled with the place they mark.
    marks = sorted({1, max(1, round(n / 3)), max(1, round(2 * n / 3)), n})
    rings = [{"rank": m, "label": ordinal(m), "points": path([(i, radius(m)) for i in range(6)]),
              "lx": c + 4, "ly": round(c - R * radius(m) + 3, 1)} for m in marks]
    axes = [{"x": pt(i, 1.0)[0], "y": pt(i, 1.0)[1]} for i in range(6)]
    labels = []
    for i, (pr, vr) in enumerate(zip(hexa["prod"], hexa["value"])):
        x, y = pt(i, 1.0 + 30 / R)
        labels.append({"pos": pr["pos"], "x": x, "y": y,
                       "anchor": "middle" if i in (0, 3) else ("start" if i in (1, 2) else "end"),
                       "prod": ordinal(pr["rank"]), "value": ordinal(vr["rank"]) if vr["rank"] else None})
    prod = [(i, p["r"]) for i, p in enumerate(hexa["prod"])]
    val = [(i, v["r"]) for i, v in enumerate(hexa["value"]) if v["r"] is not None]
    return {"size": size, "c": c, "rings": rings, "axes": axes, "labels": labels,
            "prod": path(prod), "prod_dots": [dict(zip(("x", "y"), pt(i, r))) for i, r in prod],
            "value": path(val), "value_dots": [dict(zip(("x", "y"), pt(i, r))) for i, r in val]}


#: How far apart the two ranks must be before the page calls it a story.
STORY_GAP = 3
PLURAL = {"QB": "quarterbacks", "RB": "running backs", "WR": "receivers", "TE": "tight ends"}


def story(ranks: list[dict]) -> Optional[str]:
    """The one line people screenshot: where points and price disagree most."""
    gaps = [r for r in ranks if r.get("value_rank") and abs(r["prod_rank"] - r["value_rank"]) >= STORY_GAP]
    if not gaps:
        return None
    r = max(gaps, key=lambda r: abs(r["prod_rank"] - r["value_rank"]))
    who = "Your " + PLURAL[r["pos"]]
    if r["prod_rank"] < r["value_rank"]:
        return (f"{who} have scored the {ordinal(r['prod_rank'])}-most points in your league, "
                f"but the market values them {ordinal(r['value_rank'])}.")
    return (f"{who} are valued {ordinal(r['value_rank'])} in your league, "
            f"but have scored only the {ordinal(r['prod_rank'])}-most points.")


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

def _trade_day(r: dict, opener: int) -> int:
    when = r.get("traded_at")
    if when:
        try:
            return date.fromisoformat(str(when)[:10]).toordinal()
        except ValueError:
            pass
    return opener + 7 * (int(r.get("week") or 1) - 1) + 3


def portfolio(me: str, my_names: set, weeks: dict[int, dict[str, set]], last_week: int,
              trades: list[dict], draft: list[dict], pos: dict[str, str],
              prices: Prices, pickable: dict[str, dict], opener: int) -> dict[str, Any]:
    """Holdings, cost basis, gains, and what was sold.

    Cost basis, in order of what we can prove:
      * TRADED FOR: his value on the day of the trade.
      * DRAFTED: the pick you spent, read on the market's scale.
      * ON THE ROSTER SINCE WEEK 1, not drafted by you (a keeper, or a
        league whose draft we never read): his value on draft day.
      * PICKED UP LATER: free. FAAB isn't counted yet.
    """
    first_week = min(weeks)
    drafted_by_me = {}
    for d in draft:
        if str(d.get("team_id")) == me and d.get("overall"):
            size = int(d.get("team_count") or 12) or 12
            drafted_by_me[d["player_key"]] = ((int(d["overall"]) - 1) * 12.0 / size + 1,
                                              int(d.get("round") or 0), int(d["overall"]))
    moves = sorted(({**r, "day": _trade_day(r, opener)} for r in trades),
                   key=lambda r: (r["day"], r.get("trade_key") or ""))
    in_ = [r for r in moves if r.get("to_team") in my_names]
    out = [r for r in moves if r.get("from_team") in my_names]

    def first_seen(k: str, before_day: Optional[int] = None) -> Optional[int]:
        for w in sorted(weeks):
            if before_day is not None and opener + 7 * (w - 1) > before_day:
                break
            if k in weeks[w].get(me, ()):
                return w
        return None

    def basis(k: str, before_day: Optional[int] = None) -> dict:
        got = [r for r in in_ if r["player_key"] == k and (before_day is None or r["day"] < before_day)]
        if got:
            d = got[-1]["day"]
            return {"how": "trade", "label": f"Traded for, {_md(d)}", "day": d,
                    "basis": prices.on(k, prices.offset(d))}
        seen = first_seen(k, before_day)
        if k in drafted_by_me and (seen is None or seen == first_week):
            slot, rnd, overall = drafted_by_me[k]
            return {"how": "draft", "label": f"Drafted, round {rnd}" if rnd else f"Drafted, pick {overall}",
                    "day": prices.base, "basis": prices.draft_value(slot)}
        if seen is None or seen == first_week:
            return {"how": "kept", "label": "On your roster since week 1", "day": prices.base,
                    "basis": prices.on(k, 0)}
        return {"how": "waiver", "label": f"Picked up, week {seen}",
                "day": opener + 7 * (seen - 1), "basis": 0.0}

    def spark(k: str, from_day: Optional[int]) -> list[float]:
        d = prices.dense(k)
        if not d:
            return []
        start = prices.offset(from_day) if from_day else 0
        seg = d[start:] or d[-1:]
        if len(seg) > 40:
            step = len(seg) / 39
            seg = [seg[int(round(i * step))] for i in range(39)] + [seg[-1]]
        return seg

    holdings = []
    for k in sorted(weeks[last_week].get(me, ())):
        if pos.get(k) not in VALUED:
            continue
        b = basis(k)
        cur = prices.now(k)
        row = pickable.get(k) or prices.injured.get(k) or {}
        gain = round(cur - b["basis"], 1)
        week_ago = prices.on(k, prices.last - WEEK_DAYS)
        holdings.append({
            "k": k, "name": row.get("name") or k, "pos": pos.get(k), "team": row.get("team"),
            "photo": row.get("photo"), "costs": row.get("costs"), "inj": row.get("inj"),
            "priced": prices.priced(k), "how": b["how"], "label": b["label"],
            "basis": round(b["basis"], 1), "value": cur, "gain": gain,
            "pct": round(100 * gain / b["basis"]) if b["basis"] >= 0.5 else None,
            "week": round(cur - week_ago, 1),
            "spark": spark(k, b["day"]),
        })
    holdings.sort(key=lambda h: -h["value"])

    sold = []
    for r in out:
        k = r["player_key"]
        if pos.get(k) not in VALUED or k in weeks[last_week].get(me, ()):
            continue
        b = basis(k, before_day=r["day"])
        at = prices.on(k, prices.offset(r["day"]))
        cur = prices.now(k)
        row = pickable.get(k) or prices.injured.get(k) or {}
        sold.append({"k": k, "name": row.get("name") or r.get("name") or k, "pos": pos.get(k),
                     "photo": row.get("photo"), "label": f"Traded away, {_md(r['day'])}",
                     "basis": round(b["basis"], 1), "sold": at, "gain": round(at - b["basis"], 1),
                     "now": cur, "since": round(cur - at, 1)})
    # A player traded away twice keeps his last sale.
    sold = list({s["k"]: s for s in sold}.values())
    sold.sort(key=lambda s: -abs(s["gain"]))

    value = round(sum(h["value"] for h in holdings), 1)
    cost = round(sum(h["basis"] for h in holdings), 1)
    ranked = [h for h in holdings if h["priced"] or h["inj"]]
    tape = sorted((h for h in holdings if h["priced"] and abs(h["week"]) >= 0.1),
                  key=lambda h: -abs(h["week"]))
    return {
        "holdings": holdings, "sold": sold,
        "value": value, "cost": cost, "unrealized": round(value - cost, 1),
        "realized": round(sum(s["gain"] for s in sold), 1),
        "pct": round(100 * (value - cost) / cost, 1) if cost >= 1 else None,
        "best": max(ranked, key=lambda h: h["gain"], default=None),
        "worst": min(ranked, key=lambda h: h["gain"], default=None),
        "tape": tape[:12],
        "sold_best": max(sold, key=lambda s: s["since"], default=None),
    }


def _md(day: int) -> str:
    d = date.fromordinal(int(day))
    return f"{d:%b} {d.day}"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def build(db, *, league: dict, team_id: str, season: int, latest: int,
          rating: dict, names: dict, adp: dict[str, float]) -> dict[str, Any]:
    """Fetch one league and assemble the page. {} if there is nothing yet."""
    import nfl_week
    lid = str(league["id"])
    lineups = db.league_lineups(lid, season) or []
    if not lineups:
        return {}
    team_weeks = db.league_team_weeks(lid, season) or []
    try:
        trades = db.league_trades(lid, season) or []
    except Exception as exc:  # noqa: BLE001 — the page still has everything else
        print(f"[team] no trades for {lid}: {type(exc).__name__}: {exc}", flush=True)
        trades = []
    try:
        draft = db.league_draft(lid, season) or []
    except Exception as exc:  # noqa: BLE001
        print(f"[team] no draft for {lid}: {type(exc).__name__}: {exc}", flush=True)
        draft = []
    keys = {r["player_key"] for r in lineups if r.get("player_key")}
    pos = {k: (names.get(k) or {}).get("position") for k in keys if (names.get(k) or {}).get("position")}
    for p in rating.get("pickable") or []:
        if p["k"] in keys and p.get("pos"):
            pos.setdefault(p["k"], p["pos"])
    missing = [k for k in keys if k not in pos]
    if missing:
        try:
            pos.update(db.player_positions(missing) or {})
        except Exception as exc:  # noqa: BLE001
            print(f"[team] no positions: {type(exc).__name__}: {exc}", flush=True)
    try:
        opener = nfl_week.season_opener(int(season)).toordinal()
    except Exception:  # noqa: BLE001
        opener = date(int(season), 9, 4).toordinal()
    prices = Prices(rating, adp)
    pickable = {p["k"]: p for p in rating.get("pickable") or []}
    return assemble(my_team=str(team_id), lineups=lineups, team_weeks=team_weeks, trades=trades,
                    draft=draft, positions=pos, prices=prices, pickable=pickable,
                    spot=float(rating.get("spot") or 0), opener=opener, latest=latest)
