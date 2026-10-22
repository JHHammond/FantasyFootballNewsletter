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

#: How many trades a player must appear in before a value is published.
#: Below this the number is one league's opinion wearing a decimal point.
MIN_TRADES = 8
#: Iterations of coordinate descent. It settles long before this on real
#: data; the cap is so a pathological week can't spin.
ROUNDS = 120
#: Stop when no value moves by more than this (in pre-scaling units).
TOLERANCE = 1e-3
#: Below this a value is "at the floor" and prints as "<1". Trades fix the
#: gaps BETWEEN players, not the bottom of the scale, so the cheapest come
#: out pinned together; a number under 1% of the median player is not a
#: price, it is the clamp.
FLOOR_AT = 1.0
#: A change smaller than this is not a move, it is the fit breathing.
MOVED_AT = 0.5
#: The published scale: the median player sits here.
MEDIAN_AT = 50.0
#: How much a value may move toward its new estimate each round. Under 1
#: this damps the oscillation two-player trades can set up.
RELAX = 0.7


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def solve(exchanges: Iterable[dict], *, min_trades: int = MIN_TRADES) -> dict[str, Any]:
    """Values from exchanges.

    Each exchange is {"a": [player_key, ...], "b": [player_key, ...]}: what
    one side received and what the other did. Returns

        {"values": {player_key: value}, "trades": {player_key: n},
         "rounds": int, "used": int, "players": int}

    `values` holds only players at or past `min_trades`; everyone else was
    still used to fit, they are just not published.
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

    # Start everyone level. The scale is arbitrary until the end, so the
    # starting value only has to be positive.
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
            new = max(0.0, was + RELAX * (wants / len(spots) - was))
            if new != was:
                delta = new - was
                for i, side in spots:
                    totals[i][side] += delta
                moved = max(moved, abs(delta))
                v[key] = new

        if moved < TOLERANCE:
            break

    published = {k: val for k, val in v.items() if seen[k] >= min_trades}
    scale = MEDIAN_AT / (_median(list(published.values())) or 1.0)
    return {
        "values": {k: round(val * scale, 1) for k, val in published.items()},
        "trades": {k: seen[k] for k in published},
        "rounds": rounds,
        "used": len(ex),
        "players": len(seen),
    }


def series(exchanges: Iterable[dict], weeks: Iterable[int], *,
           window: int = 3, min_trades: int = MIN_TRADES) -> dict[str, Any]:
    """A value per player per week: the chart.

    Each week is solved on its own trades plus the `window - 1` weeks
    before it. A single week of one league's trades is thin for a player
    who moved twice; a rolling window is what makes a line that moves for
    a reason rather than for a lack of data. The scale is re-fixed every
    week (median player = 50), so a line rising means rising against the
    field, not the field getting busier.
    """
    ex = list(exchanges)
    out: dict[str, list] = {}
    meta = []
    for wk in sorted(set(int(w) for w in weeks)):
        lo = wk - window + 1
        chunk = [e for e in ex if lo <= int(e.get("w") or 0) <= wk]
        got = solve(chunk, min_trades=min_trades)
        for key, val in got["values"].items():
            out.setdefault(key, []).append({"week": wk, "value": val,
                                            "trades": got["trades"][key]})
        meta.append({"week": wk, "used": got["used"],
                     "players": len(got["values"]), "rounds": got["rounds"]})
    return {"series": out, "weeks": meta, "window": window}


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
                     "weeks": f"{first['week']}–{last['week']}"})
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
          names: dict[str, dict], width: int = 720, height: int = 240) -> dict:
    """Geometry for the price lines, in viewBox units. Drawn as inline SVG
    from these numbers, like every other chart on the page."""
    lines = [(k, series_by_player.get(k) or []) for k in keys[:CHART_LINES]]
    lines = [(k, pts) for k, pts in lines if len(pts) > 1]
    if not lines:
        return {}
    weeks = sorted({p["week"] for _, pts in lines for p in pts})
    vals = [p["value"] for _, pts in lines for p in pts]
    lo = max(0.0, min(vals) - 10)
    hi = max(vals) + 10
    pad_l, pad_r, pad_t, pad_b = 44, 110, 14, 28
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    xs = {w: pad_l + (pw * (i / (len(weeks) - 1)) if len(weeks) > 1 else pw / 2)
          for i, w in enumerate(weeks)}
    def y(v):
        return pad_t + ph * (1 - (v - lo) / (hi - lo or 1))
    out = []
    for n, (key, pts) in enumerate(lines):
        meta = names.get(key) or {}
        dots = [{"x": round(xs[p["week"]], 1), "y": round(y(p["value"]), 1),
                 "week": p["week"], "v": p["value"], "trades": p["trades"]}
                for p in pts if p["week"] in xs]
        out.append({
            "key": key, "n": n,
            "name": (meta.get("name") or key).split(" ")[-1][:12],
            "full": meta.get("name") or key,
            "path": " ".join(f"{'M' if i == 0 else 'L'}{d['x']},{d['y']}"
                             for i, d in enumerate(dots)),
            "dots": dots, "last": dots[-1] if dots else None,
        })
    step = 25 if hi - lo > 80 else 10
    grid = [{"y": round(y(g), 1), "v": g}
            for g in range(int(lo // step * step), int(hi) + 1, step) if g >= lo]
    return {"lines": out, "grid": grid, "width": width, "height": height,
            "weeks": [{"w": w, "x": round(xs[w], 1)} for w in weeks],
            "base": pad_t + ph, "left": pad_l, "right": width - pad_r}


def build(db, season: int, weeks: list[int], *, limit: int = 40) -> dict[str, Any]:
    """Everything the market panel draws, or {} if there is nothing to
    draw. Never raises: a staff panel is not worth a 500."""
    try:
        ex = db.trade_exchanges(season, 1, max(weeks or [1]))
        names = db.trade_player_names(season)
    except Exception as exc:  # noqa: BLE001
        print(f"[market] no exchanges: {type(exc).__name__}: {exc}", flush=True)
        return {}
    if not ex:
        return {}
    whole = solve(ex)
    if not whole["values"]:
        return {"used": whole["used"], "players": whole["players"], "thin": True}
    ranked = board(whole["values"], whole["trades"], names, limit)
    ser = series(ex, weeks)
    top = [r["player_key"] for r in ranked]
    return {
        "board": ranked,
        "chart": chart(ser["series"], top, names),
        "movers": movers(ser["series"], names),
        "weeks": ser["weeks"],
        "window": ser["window"],
        "used": whole["used"],
        "players": len(whole["values"]),
        "seen": whole["players"],
        "min_trades": MIN_TRADES,
        "median_at": MEDIAN_AT,
    }
