"""
What a player is worth, read off who gets traded for whom (21 Oct 2026).

THE IDEA. Every completed trade is two managers agreeing that one bundle of
players is worth about another. Write that down as an equation —

    sum(value of what A received) = sum(value of what B received)

— and a week of trades across every connected league is a few thousand
equations in a few hundred unknowns. Solve them and you have a price per
player that nobody set: a market, taken from what leagues actually did.

WHAT IT IS NOT. Not a projection, not a ranking of who is good. It is what
the field pays. A player can be excellent and cheap because nobody will
part with him, and a name can be dear because his owners are loud. The
number means "this is the going rate", and the page has to say so.

THE MODEL, and where it is wrong:

  * Additive, and with no charge for a roster spot. A bundle is worth the
    sum of its parts. That is false at the edges — two-for-ones favour the
    side getting the one, because a roster spot costs something — and the
    obvious fix is an extra unknown charged per body. It does not work,
    and the reason is worth writing down: adding a constant c to EVERY
    player's value changes each side's total by c times the number of
    players on it, which is exactly what a per-body charge does. The two
    are the same parameter. Fitted together they are unidentifiable, and
    in testing the body charge ran away and swallowed the values whole.
    So there is no such term, and the known consequence is that the depth
    pieces in consolidation trades come out a little rich. Planting a real
    premium in simulated trades and ignoring it costs about 0.00 of rank
    correlation, which is why this is the right trade.
  * Only player-for-player trades. A trade carrying draft picks or FAAB is
    not an equation we can write, because we do not know what the pick is
    worth; including it would quietly corrupt the players in it. Those
    trades are counted and set aside, and the share set aside is reported
    — in a dynasty-heavy population it will be large, and that is the
    first thing worth improving.
  * Trades are not fair. One side usually wins. That is fine in bulk: the
    disagreement is noise around the consensus, which is what we're after.
    It is NOT fine for a player who appears three times, so a value is
    only published past `MIN_TRADES` appearances.

THE SOLVE. Coordinate descent: hold everyone else fixed, set each player to
the value that best satisfies the trades he is in, repeat. That is plain
least squares, converges quickly on data this shape, needs no matrix
library, and every step is a sentence you can say out loud. Values are
pinned at or above zero, and the scale is fixed at the end by setting the
median published player to 50 — so 100 means "twice the typical traded
player", and a chart across weeks compares like with like even as the
population changes.

Checked against planted values in scripts/check_trade_market.py: trades
are simulated the way managers make them (each side accepts only what it
thinks it wins), and the solver has to recover the ranking it was never
shown.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from web import trade_prior

#: Days of trades behind each point on the line. Two weeks: long enough
#: that a quiet Tuesday is not a crash, short enough to still be a market.
WINDOW_DAYS = 14
#: How many trades a player must appear in before a value is published.
#: Below this the number is one league's opinion wearing a decimal point.
MIN_TRADES = 8
#: Pseudo-trades, each saying "this player is worth his prior", added to
#: every player's fit. The whole reason there is a unique answer: see the
#: update below. Three real trades' worth, measured at convergence against
#: planted values — enough to kill the degenerate fit and to shrink a
#: thinly-traded player toward what usage says, while still letting 3,000
#: trades move a player a long way from it.
#:
#: Sweeping it with a usage prior in place (rank correlation, realistic /
#: fair / high-disagreement / no-premium):
#:
#:      prior alone   0.805  0.819  0.821  0.810
#:      ridge   0.3   0.979  0.993  0.907  0.980
#:      ridge   1.0   0.979  0.993  0.913  0.981
#:      ridge   3.0   0.979  0.992  0.924  0.980   <-- here
#:      ridge   8.0   0.976  0.985  0.937  0.978
#:      ridge  20.0   0.966  0.973  0.942  0.968
#:
#: Note the shape: a heavier ridge helps the noisy case and costs the
#: clean ones, which is exactly right — the less the trades agree, the
#: more weight usage should carry. 3.0 is where that trade is cheapest.
RIDGE = 3.0
#: Iterations of coordinate descent. A real window settles in about 140,
#: so this is a backstop against a pathological day spinning, not the
#: thing that normally stops the loop — which matters, because a fit
#: stopped by the cap is a fit we don't know the shape of. `settled` in
#: the result says which happened.
ROUNDS = 400
#: Stop when no value moves by more than this (in pre-scaling units).
TOLERANCE = 1e-3
#: Below this a value is "at the floor" and prints as "<1". Trades fix the
#: gaps BETWEEN players, not the bottom of the scale, so the cheapest come
#: out pinned together; a number under 1% of the median player is not a
#: price, it is the clamp.
FLOOR_AT = 1.0
#: A change smaller than this is not a move, it is the fit breathing.
MOVED_AT = 0.5
#: The published scale: the median player IN THE FIT sits here. Not the
#: median published player — that set changes as people enter and leave,
#: and a line should move because the market moved.
MEDIAN_AT = 50.0
#: How much a value may move toward its new estimate each round. Under 1
#: this damps the oscillation two-player trades can set up.
RELAX = 0.7
#: A prior below this (in prior units, where 1 is an average traded player)
#: is too near the floor to divide by: a player at his position's
#: replacement level books at about nothing, and "x times nothing" is not a
#: premium. Those rows print a price and no comparison.
BOOK_AT = 0.25


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def solve(exchanges: Iterable[dict], *, min_trades: int = MIN_TRADES,
          prior: Optional[dict] = None, ridge: float = RIDGE) -> dict[str, Any]:
    """Values from exchanges.

    Each exchange is {"a": [player_key, ...], "b": [player_key, ...]}: what
    one side received and what the other did. Returns

        {"values": {player_key: value}, "trades": {player_key: n},
         "rounds": int, "used": int, "players": int}

    `values` holds only players at or past `min_trades`; everyone else was
    still used to fit, they are just not published.

    `prior` is what each player is believed to be worth before any trade is
    read, on a scale where 1 is an average player — in production, what the
    leagues' own usage says (see `web/trade_prior.py`). It is not a nicety.
    Trades alone do not determine values: see the note on the ridge below.
    Players missing from it are treated as average.
    """
    ex = [(list(e.get("a") or []), list(e.get("b") or []))
          for e in exchanges
          if (e.get("a") and e.get("b"))]
    # A player traded for himself (a correction, a re-entered trade) says
    # nothing and can only destabilise the fit.
    ex = [(a, b) for a, b in ex if set(a) != set(b)]

    seen: dict[str, int] = {}
    for a, b in ex:
        for key in set(a) | set(b):
            seen[key] = seen.get(key, 0) + 1
    if not seen:
        return {"values": {}, "trades": {}, "rounds": 0, "used": 0, "players": 0}

    # Everyone starts level, every time. An earlier version warm-started
    # each day from the day before, on the reasoning that consecutive
    # windows share nearly all their trades so the fit would barely have to
    # move. It is a trap, and an instructive one. The loop stops when
    # nothing has moved by TOLERANCE in a round — but the weak direction
    # here (scale the whole vector) moves slower than that while still
    # moving, so the day stops "converged" somewhere short of its own
    # minimum, hands that position to tomorrow, and the error compounds.
    # Over 47 simulated days the dearest player drifted from 168 to 1,811
    # and rank correlation fell from 0.97 to 0.69. A cold solve converges
    # in about 140 rounds and 130ms, so the whole season's line costs a few
    # seconds: the speed was never worth buying.
    v: dict[str, float] = {k: 1.0 for k in seen}

    # Which exchanges each player is in, and on which side.
    where: dict[str, list[tuple[int, int]]] = {}
    for i, (a, b) in enumerate(ex):
        for key in set(a):
            where.setdefault(key, []).append((i, 0))
        for key in set(b):
            where.setdefault(key, []).append((i, 1))

    # What each side of each exchange currently totals, kept up to date as
    # values move rather than re-added from scratch. With tens of thousands
    # of trades that is the difference between seconds and a minute.
    totals = [[float(len(set(a))), float(len(set(b)))] for a, b in ex]

    rounds = 0
    for rounds in range(1, ROUNDS + 1):
        moved = 0.0
        for key, spots in where.items():
            # Least squares for one unknown: the average of what each trade
            # says he is worth, given everyone else. What his side is owed,
            # less what else is on it.
            was = v[key]
            wants = 0.0
            for i, side in spots:
                t = totals[i]
                wants += t[1 - side] - (t[side] - was)
            # Plus `ridge` imaginary trades saying he is worth his prior.
            #
            # This term is load-bearing, and the reason is the hardest thing
            # about this module. "Every bundle is worth the other" is also
            # satisfied by giving a few players who trade evenly among
            # themselves a big number and EVERYBODY ELSE ZERO: then almost
            # every trade in the data reads 0 = 0, a perfect fit and utter
            # nonsense. It is a real minimum of a real objective and the
            # solver prefers it to the truth. For a while the market looked
            # fine only because the loop stopped early, and every attempt to
            # converge it properly made it worse (tightening the tolerance
            # on the noisiest case took rank correlation from 0.90 to 0.36).
            #
            # A pull toward *average* forbids the collapse but carries no
            # information, and measured at real convergence it caps out
            # around 0.90 on a realistic simulation and 0.28 when managers
            # disagree a lot — at every strength from 0.05 to 10, so there
            # is no constant that rescues it. A pull toward a real prior
            # forbids the same collapse AND tells the fit where to start, so
            # the trades only have to explain the departures from it. That
            # is the difference between a number and a guess.
            target = (wants + ridge * (1.0 if prior is None
                                       else float(prior.get(key, 1.0)))
                      ) / (len(spots) + ridge)
            new = max(0.0, was + RELAX * (target - was))
            if new != was:
                delta = new - was
                for i, side in spots:
                    totals[i][side] += delta
                moved = max(moved, abs(delta))
                v[key] = new

        # No renormalising here. The ridge above anchors the level — every
        # player is pulled toward 1 — so the vector cannot drift as a whole,
        # and a rescaling step would only fight the thing holding it still.
        if moved < TOLERANCE:
            break

    # The ridge pins the level near 1, but only near it, so the published
    # scale is set here: the median player in the fit sits at MEDIAN_AT.
    published = {k: val for k, val in v.items() if seen[k] >= min_trades}
    scale = MEDIAN_AT / (_median([val for val in v.values() if val > 0]) or 1.0)
    return {
        "values": {k: round(val * scale, 1) for k, val in published.items()},
        "trades": {k: seen[k] for k in published},
        #: The factor taken from the fit's own units (where the ridge pulls
        #: toward a prior of 1 for an average player) to the published
        #: median-50 scale. Anything compared against a published value —
        #: the prior above all — has to come through here first.
        "scale": scale,
        "rounds": rounds,
        "settled": rounds < ROUNDS,
        "used": len(ex),
        "players": len(seen),
    }


def series(exchanges: Iterable[dict], days: Iterable, *,
           window: int = WINDOW_DAYS, min_trades: int = MIN_TRADES,
           prior: Optional[dict] = None) -> dict[str, Any]:
    """A value per player per day: the chart.

    Each day is solved on the trades of the `window` days ending that day.
    A single day of trading is far too thin to price anybody; the window is
    what makes a line that moves for a reason rather than for a lack of
    data, and stepping it daily is what makes it a line at all.

    Every day is solved from scratch — see the note in `solve` on why it is
    not warm-started from the day before — and the scale is re-fixed each
    day (median player = 50), so a line rising means rising against the
    field rather than the field going quiet.
    """
    ex = [e for e in exchanges if e.get("day") is not None]
    byday: dict[int, list] = {}
    for e in ex:
        byday.setdefault(int(e["day"]), []).append(e)
    out: dict[str, list] = {}
    meta = []
    for day in sorted(set(int(d) for d in days)):
        chunk = [e for d, lot in byday.items() if day - window < d <= day for e in lot]
        if not chunk:
            continue
        got = solve(chunk, min_trades=min_trades, prior=prior)
        for key, val in got["values"].items():
            out.setdefault(key, []).append({"day": day, "value": val,
                                            "trades": got["trades"][key]})
        meta.append({"day": day, "used": got["used"],
                     "players": len(got["values"]), "rounds": got["rounds"],
                     "settled": got["settled"]})
    return {"series": out, "days": meta, "window": window}


def board(values: dict[str, float], trades: dict[str, int],
          names: dict[str, dict], limit: int = 50) -> list[dict]:
    """The ranking, richest first, named for printing."""
    rows = []
    for key, val in values.items():
        meta = names.get(key) or {}
        rows.append({"player_key": key, "name": meta.get("name") or key,
                     "position": meta.get("position"), "nfl_team": meta.get("nfl_team"),
                     "value": val, "trades": trades.get(key, 0),
                     # Pinned at the floor. Trades fix the GAPS between
                     # players, never the bottom of the scale — the clamp at
                     # zero is what pins it, so a player here is "cheaper
                     # than we can separate", not "worth nothing".
                     "floored": val < FLOOR_AT})
    rows.sort(key=lambda r: (-r["value"], r["player_key"]))
    return rows[:limit]


def _span(a: int, b: int) -> str:
    from datetime import date
    x, y = date.fromordinal(a), date.fromordinal(b)
    return f"{x:%b} {x.day}–{y:%b} {y.day}"


def movers(series_by_player: dict[str, list], names: dict[str, dict],
           limit: int = 10) -> dict[str, list]:
    """Who gained and lost the most since their first published week."""
    rows = []
    for key, points in series_by_player.items():
        if len(points) < 2:
            continue
        first, last = points[0], points[-1]
        change = round(last["value"] - first["value"], 1)
        if abs(change) < MOVED_AT:          # standing still is not a story
            continue
        meta = names.get(key) or {}
        rows.append({"player_key": key, "name": meta.get("name") or key,
                     "position": meta.get("position"),
                     "from": first["value"], "to": last["value"],
                     "change": change,
                     "span": _span(first["day"], last["day"])})
    rows.sort(key=lambda r: (-r["change"], r["player_key"]))
    return {"up": [r for r in rows if r["change"] > 0][:limit],
            "down": list(reversed([r for r in rows if r["change"] < 0]))[:limit]}


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

#: How many lines the chart draws. More than this and it is a plate of
#: spaghetti, not a market.
CHART_LINES = 6


def chart(series_by_player: dict[str, list], keys: list[str],
          names: dict[str, dict], width: int = 760, height: int = 260) -> dict:
    """Geometry for the price lines, in viewBox units, plus every point's
    value so the page can be dragged along without asking again.

    `keys` is who gets a line, in order. The page can swap them, so the
    series for EVERY published player rides along too — a few hundred
    players at a few dozen days each is small beside the boards.
    """
    lines = [(k, series_by_player.get(k) or []) for k in keys]
    lines = [(k, pts) for k, pts in lines if len(pts) > 1]
    if not lines:
        return {}
    days = sorted({p["day"] for _, pts in lines for p in pts})
    vals = [p["value"] for _, pts in lines for p in pts]
    lo = max(0.0, min(vals) - 8)
    hi = max(vals) + 8
    pad_l, pad_r, pad_t, pad_b = 40, 52, 16, 30
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    xs = {d: pad_l + (pw * (i / (len(days) - 1)) if len(days) > 1 else pw / 2)
          for i, d in enumerate(days)}

    def y(v):
        return pad_t + ph * (1 - (v - lo) / (hi - lo or 1))

    from .luck import photo_url
    out = []
    for n, (key, pts) in enumerate(lines):
        meta = names.get(key) or {}
        dots = [{"x": round(xs[p["day"]], 1), "y": round(y(p["value"]), 1),
                 "day": p["day"], "v": p["value"], "trades": p["trades"]}
                for p in pts if p["day"] in xs]
        out.append({
            "key": key, "n": n,
            "name": meta.get("name") or key,
            "short": (meta.get("name") or key).split(" ")[-1][:14],
            "position": meta.get("position"),
            "photo": photo_url(key, meta.get("position")),
            "path": " ".join(f"{'M' if i == 0 else 'L'}{d['x']},{d['y']}"
                             for i, d in enumerate(dots)),
            "dots": dots,
            "last": dots[-1] if dots else None,
            # day -> value, so a scrub reads straight off it
            "at": {str(d["day"]): d["v"] for d in dots},
        })
    step = 25 if hi - lo > 80 else 10
    grid = [{"y": round(y(g), 1), "v": g}
            for g in range(int(lo // step * step), int(hi) + 1, step) if g >= lo]
    from datetime import date
    every = max(1, len(days) // 5)
    axis = []
    for i, d in enumerate(days):
        when = date.fromordinal(d)
        axis.append({"d": d, "x": round(xs[d], 1),
                     "label": f"{when:%b} {when.day}",
                     "tick": i % every == 0 or i == len(days) - 1})
    return {"lines": out, "grid": grid, "width": width, "height": height,
            "days": axis,
            "lo": lo, "hi": hi,
            "base": pad_t + ph, "top": pad_t, "left": pad_l, "right": width - pad_r}


def days_for(exchanges: list[dict], season: int) -> list[dict]:
    """Put every exchange on a day. A trade whose platform gave no
    timestamp is placed in the middle of its own week rather than dropped:
    the week is real even when the hour isn't."""
    from datetime import date

    import nfl_week
    try:
        opener = nfl_week.season_opener(int(season))
    except Exception:  # noqa: BLE001
        opener = date(int(season), 9, 4)
    out = []
    for e in exchanges:
        when = e.get("d")
        if when:
            try:
                day = date.fromisoformat(str(when)[:10]).toordinal()
            except ValueError:
                day = None
        else:
            day = None
        if day is None:
            week = int(e.get("w") or 1)
            day = opener.toordinal() + 7 * (week - 1) + 3
        out.append(dict(e, day=day))
    return out


def build(db, season: int, weeks: list[int], *, limit: int = 40,
          lines: int = CHART_LINES) -> dict[str, Any]:
    """Everything the market panel draws, or {} if there is nothing to
    draw. Never raises: a staff panel is not worth a 500."""
    try:
        raw = db.trade_exchanges(season, 1, max(weeks or [1]))
        names = db.trade_player_names(season)
    except Exception as exc:  # noqa: BLE001
        print(f"[market] no exchanges: {type(exc).__name__}: {exc}", flush=True)
        return {}
    if not raw:
        return {}
    ex = days_for(raw, season)

    # The prior, built from the same leagues' own usage. Without it the fit
    # has a degenerate minimum it prefers to the truth (see `solve`), so a
    # missing prior is worth a loud line in the log — the market still
    # draws, because a flat prior only puts it back where it was, but the
    # numbers are much weaker and somebody should know.
    traded = {k for e in ex for k in list(e.get("a") or []) + list(e.get("b") or [])}
    prior: dict[str, float] = {}
    try:
        usage = db.trade_prior(season, 1, max(weeks or [1]))
        prior = trade_prior.build(usage or {}, only=traded)
    except Exception as exc:  # noqa: BLE001
        print(f"[market] NO USAGE PRIOR, values will be weak: "
              f"{type(exc).__name__}: {exc}", flush=True)
    whole = solve(ex, prior=prior or None)
    if not whole["values"]:
        return {"used": whole["used"], "players": whole["players"], "thin": True}

    ranked = board(whole["values"], whole["trades"], names, limit)
    span = sorted({e["day"] for e in ex})
    # Start the line once the window has something behind it, or the first
    # points are a fortnight of nothing pretending to be a price.
    first = span[0] + WINDOW_DAYS // 2
    ser = series(ex, [d for d in range(first, span[-1] + 1)], prior=prior or None)
    top = [r["player_key"] for r in ranked]

    # Price against the prior: the number that says something no ranking
    # site can. 1.0 is "the field pays exactly what usage says he's worth".
    # The prior is in the fit's own units and the board is in published
    # ones, so it has to be carried across by the fit's own scale — not by
    # re-deriving one from the published values, which are already scaled.
    scale = whole["scale"]
    for row in ranked:
        p = prior.get(row["player_key"])
        row["prior"] = round(p * scale, 1) if p else None
        # A player sitting at his position's replacement level books at
        # about nothing, and a ratio against about nothing is not a ratio —
        # it came out at 11.8x in testing, which would have printed as
        # "1081% over book". Below BOOK_AT there is no book to be over, so
        # the row says nothing rather than something silly.
        row["premium"] = (round(row["value"] / (p * scale), 2)
                          if p and p >= BOOK_AT and p * scale > 0 else None)

    return {
        "board": ranked,
        "priored": len(prior),
        "chart": chart(ser["series"], top[:lines], names),
        # Every published player's line, so the page can swap one in
        # without going back to the database.
        "all_series": {k: {str(p["day"]): p["value"] for p in pts}
                       for k, pts in ser["series"].items()},
        "movers": movers(ser["series"], names),
        "days": ser["days"],
        "window": ser["window"],
        "used": whole["used"],
        "players": len(whole["values"]),
        "seen": whole["players"],
        "min_trades": MIN_TRADES,
        "median_at": MEDIAN_AT,
    }
