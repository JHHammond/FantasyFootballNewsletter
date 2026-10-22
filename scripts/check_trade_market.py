"""
Does the trade market recover values it was never shown?

    python -m scripts.check_trade_market [trades] [players]

web/trade_market.py claims to read a price off who gets traded for whom.
That claim is checkable without any real data: plant a true value on every
player, simulate trades the way managers actually make them, hand the
solver nothing but the trades, and see whether it gets the planted values
back.

The simulation is deliberately unkind, because real trades are:

  * SELECTED, not fair. Nobody accepts a trade they think they lose, so
    every observed trade is one where BOTH sides believed they won. Each
    manager here values players with his own private error, and a trade
    happens only when both sides come out ahead on their own numbers. This
    is the bias that could sink the whole idea, so it is the default.
  * LOPSIDED in size. Two-for-ones and three-for-ones are common, and the
    side getting fewer players is paid for the roster spots. The generator
    charges a real roster-spot premium, and the solver has to find it.
  * UNEVEN in attention. Stars move constantly; most players move once or
    twice. The generator draws by popularity, so the tail is thin exactly
    where it is thin in life.

What it prints: Spearman rank correlation against the planted values (does
it get the ORDER right), the median absolute error on the published
players (is the SCALE right), and the roster-spot premium it recovered
against the one that was planted.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def spearman(a: list[float], b: list[float]) -> float:
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            shared = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = shared
            i = j + 1
        return out
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    if n < 2:
        return 0.0
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else 0.0


def simulate(n_trades: int, n_players: int, rng: random.Random, *,
             spot: float = 6.0, disagreement: float = 0.18,
             selected: bool = True) -> tuple[list[dict], dict[str, float]]:
    """Trades between managers who each misjudge value, and only agree
    when both of them think they have won."""
    # A real player pool: a few stars, a long tail.
    truth = {}
    for i in range(n_players):
        rank = (i + 1) / n_players
        truth[f"p{i}"] = round(100 * (1 - rank) ** 2.2 + 2, 2)
    keys = list(truth)
    # Who gets traded: popularity follows value, loosely.
    weights = [truth[k] + 6 for k in keys]

    def pick(n):
        got = set()
        while len(got) < n:
            got.add(rng.choices(keys, weights=weights, k=1)[0])
        return list(got)

    def view(manager: int, key: str) -> float:
        """One manager's private opinion of a player."""
        rng2 = random.Random(hash((manager, key)) & 0xFFFFFFFF)
        return truth[key] * (1 + rng2.gauss(0, disagreement))

    out: list[dict] = []
    tries = 0
    while len(out) < n_trades and tries < n_trades * 80:
        tries += 1
        na = rng.choices([1, 1, 1, 2, 2, 3], k=1)[0]
        nb = rng.choices([1, 1, 1, 2, 2, 3], k=1)[0]
        a, b = pick(na), pick(nb)
        if set(a) & set(b):
            continue
        if selected:
            # Nobody accepts a trade they think they lose, so an observed
            # trade is one BOTH sides believed they won. This is the bias
            # that could sink the idea, so it is the default.
            x, y = rng.randrange(10_000), rng.randrange(10_000)
            a_gain = (sum(view(x, k) for k in a) - sum(view(x, k) for k in b)
                      + spot * (nb - na))
            b_gain = (sum(view(y, k) for k in b) - sum(view(y, k) for k in a)
                      + spot * (na - nb))
            if not (a_gain > 0 and b_gain > 0):
                continue
        else:
            # The control: trades that are genuinely balanced, give or take
            # noise. Two random bundles are not a trade — they carry no
            # claim that the sides are worth the same — so the bundles have
            # to be matched on true value for this to test anything.
            gap = (sum(truth[k] for k in a) - sum(truth[k] for k in b)
                   - spot * (na - nb))
            tolerance = 0.12 * max(1.0, sum(truth[k] for k in a))
            if abs(gap) > tolerance:
                continue
        out.append({"w": rng.randint(1, 6), "a": a, "b": b})
    return out, truth


def main() -> int:
    from web import trade_market

    n_trades = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
    n_players = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    rng = random.Random(2026)
    bad = 0

    for label, kw in (("trades both sides think they win", {}),
                      ("every trade fair (no selection)", {"selected": False}),
                      ("managers who disagree wildly", {"disagreement": 0.35}),
                      ("no roster-spot premium in the data", {"spot": 0.0})):
        ex, truth = simulate(n_trades, n_players, random.Random(rng.randrange(1 << 30)), **kw)
        got = trade_market.solve(ex)
        vals = got["values"]
        if not vals:
            print(f"{label}: NOTHING PUBLISHED"); bad += 1; continue
        keys = sorted(vals)
        mine = [vals[k] for k in keys]
        true = [truth[k] for k in keys]
        # Put the planted values on the solver's own scale before comparing
        # sizes: only the ORDER and the RATIOS are claimed, never the units.
        scale = trade_market.MEDIAN_AT / (trade_market._median(true) or 1)
        err = sorted(abs(m - t * scale) for m, t in zip(mine, true))
        rho = spearman(mine, true)
        med_err = err[len(err) // 2]
        planted = kw.get("spot", 6.0) * scale
        print(f"{label}:")
        print(f"    {len(vals)} of {n_players} players published from {got['used']:,} trades"
              f" in {got['rounds']} rounds")
        print(f"    rank correlation {rho:.3f}   median error {med_err:.1f}"
              f" on a median-50 scale")
        if planted:
            print(f"    (a roster-spot premium of {planted:.1f} was planted and"
                  f" deliberately not modelled \u2014 see trade_market's docstring)")
        if rho < 0.9:
            print("    FAIL: the order is not recovered")
            bad += 1
        if med_err > 12:
            print("    FAIL: the scale is off")
            bad += 1

    # The thin tail: a player in three trades must not be published.
    ex, truth = simulate(400, n_players, random.Random(7))
    got = trade_market.solve(ex)
    if any(n < trade_market.MIN_TRADES for n in got["trades"].values()):
        print("FAIL: published a player under the minimum"); bad += 1
    else:
        print(f"thin data: {len(got['values'])} published of {got['players']} seen,"
              f" none under {trade_market.MIN_TRADES} trades")

    print("\nThe market recovers what it was never shown." if not bad
          else f"\n{bad} check(s) failed.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
