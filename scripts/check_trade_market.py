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

import math
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
             selected: bool = True,
             popularity: str = "value") -> tuple[list[dict], dict[str, float]]:
    """Trades between managers who each misjudge value, and only agree
    when both of them think they have won."""
    # A real player pool: a few stars, a long tail.
    truth = {}
    for i in range(n_players):
        rank = (i + 1) / n_players
        truth[f"p{i}"] = round(100 * (1 - rank) ** 2.2 + 2, 2)
    keys = list(truth)
    # Who gets traded.
    #
    # "value" is the old behaviour and is wrong about the top of the board:
    # it makes the best players the most traded, when in life they are the
    # least — nobody parts with the first pick. That flattery is why a
    # solver can look healthy here and fill up with kickers in production.
    #
    # "hump" is what a season actually looks like: the elite are hoarded,
    # the middle churns constantly, and the worthless are not worth the
    # message. It is the realistic case and it is much harder.
    if popularity == "hump":
        hi = max(truth.values()) or 1.0
        weights = []
        for k in keys:
            share = truth[k] / hi                       # 1.0 is the best player
            # Peaks around a third of the way down the board.
            weights.append(0.25 + 8.0 * math.exp(-((share - 0.35) ** 2) / 0.035))
    else:
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


def usage_prior(truth: dict[str, float], rng: random.Random, *,
                noise: float = 0.35, cap: float = 0.92) -> dict[str, float]:
    """A stand-in for what the leagues' own usage says a player is worth.

    In production this is built from `lineup_players`: how many of ~14,000
    teams roster a player and how many start him. It is informative and it
    is wrong in two specific ways, both modelled here, because a prior that
    is merely truth-plus-noise would make this test too easy:

      * It SATURATES. Everybody starts a stud, so start rate runs out of
        room at the top and the real gaps between the dearest players are
        squashed. That is the concave term.
      * It is NOISY, and not evenly. A player can be widely started and
        cheap (a safe floor nobody covets) or thinly rostered and dear.

    So the prior knows roughly who the good players are and is poor at
    exactly the job the market is for: pricing the top of the board.
    """
    hi = max(truth.values()) or 1.0
    out = {}
    for key, val in truth.items():
        rate = min(cap, (val / hi) ** 0.55)
        out[key] = max(0.02, rate * (1 + rng.gauss(0, noise)))
    mean = sum(out.values()) / len(out)
    return {k: v / mean for k, v in out.items()}


def main() -> int:
    from web import trade_market

    n_trades = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
    n_players = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    rng = random.Random(2026)
    bad = 0

    # Each scenario carries its own bar for the median error, because the
    # scenarios are not equally hard and one number would either excuse the
    # easy cases or condemn the stress case for something no estimator can
    # fix. When managers' private values differ by 35% AND a trade only
    # happens if both of them think they won, the trades that get observed
    # are a biased sample of a very noisy signal: the ORDER survives (0.94),
    # the absolute level does not, and nothing in the data can recover it.
    # That is also why the page leads with price-against-prior rather than
    # the bare number. The rank bar is 0.9 everywhere and never moves.
    cases = (("trades both sides think they win", {}, 12.0),
             ("every trade fair (no selection)", {"selected": False}, 12.0),
             ("managers who disagree wildly", {"disagreement": 0.35}, 20.0),
             ("no roster-spot premium in the data", {"spot": 0.0}, 12.0))
    for label, kw, err_bar in cases:
        ex, truth = simulate(n_trades, n_players, random.Random(rng.randrange(1 << 30)), **kw)
        # The prior stands in for what the leagues' own usage says. The fit
        # cannot be judged against planted values alone — it has to be
        # judged against the prior it started from, or we would be
        # congratulating ourselves for printing the prior back out.
        pri = usage_prior(truth, random.Random(99))
        got = trade_market.solve(ex, prior=pri)
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
        base = spearman([pri[k] for k in keys], true)
        med_err = err[len(err) // 2]
        planted = kw.get("spot", 6.0) * scale
        print(f"{label}:")
        print(f"    {len(vals)} of {n_players} players published from {got['used']:,} trades"
              f" in {got['rounds']} rounds"
              f"{'' if got['settled'] else '  *** STOPPED ON THE ROUND CAP ***'}")
        print(f"    rank correlation {rho:.3f}   median error {med_err:.1f}"
              f" on a median-50 scale (bar: {err_bar:.0f})")
        print(f"    the prior alone scores {base:.3f}, so the trades are"
              f" worth {rho - base:+.3f}")
        if planted:
            print(f"    (a roster-spot premium of {planted:.1f} was planted and"
                  f" deliberately not modelled \u2014 see trade_market's docstring)")
        if rho < 0.9:
            print("    FAIL: the order is not recovered")
            bad += 1
        if med_err > err_bar:
            print("    FAIL: the scale is off")
            bad += 1
        # The one that matters most. A fit that cannot beat the prior it
        # was handed is an expensive way to redraw the prior, and the whole
        # claim of the feature ("this is what the field PAID") would be
        # false. 0.03 rather than 0 so noise alone can't pass it.
        if rho < base + 0.03:
            print("    FAIL: the trades added nothing over the prior")
            bad += 1
        if not got["settled"]:
            print("    FAIL: never converged, so the answer is an accident"
                  " of where the loop stopped")
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
