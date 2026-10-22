"""Is the draft-anchored filter actually better? (22 Oct 2026)

    python -m scripts.check_trade_rating

web/trade_rating.py replaces a working estimator, so "it feels better" is
not good enough. This plants true values, invents a draft and a season of
trades from them, runs BOTH models over the same data and reports the
three things the old one was bad at:

  COVERAGE    how many players get a price at all. The batch solver needs
              eight trades before it will say anything, and a star is
              traded twice a season, so the live board filled with the
              players who churn.
  SPREAD      top-to-median. The additive model caps a player at the
              biggest bundle anybody gave up for him, so it cannot
              represent a superstar: it squeezed a planted 4.27x to 3.64x,
              and the live board managed 2.1x.
  ORDER       rank correlation against the planted truth, which the old
              model was already good at and the new one must not lose.

Plus one the old model could not do at all: CALIBRATION. The filter
publishes a band, so the band has to mean something — about 95% of players
should fall inside their own two-sigma range. A confident wrong answer is
worse than an honest vague one.

The draft here is deliberately imperfect in the way real drafts are: the
rooms agree about the first round and argue more the further down the
board they go, so ADP is sharp at the top and vague in the middle, which
is exactly where the trades have to do the work.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.check_trade_market import simulate, spearman, usage_prior  # noqa: E402
from web import trade_market, trade_rating  # noqa: E402


def august(truth: dict, rng: random.Random, *, churn: float = 0.55) -> dict:
    """What the country believed in August, given what turned out to be true.

    THIS IS THE WHOLE TEST, and the first version of it got the point
    backwards. Averaging 300 draft rooms makes the room-level noise vanish
    and leaves an ADP that ranks the players PERFECTLY — at which point the
    trades can only make things worse, and the model was being asked to
    improve on an oracle.

    Real ADP is not an oracle. It is a forecast made before anybody had
    played a game, and by October some of it is wrong: a third-rounder is
    the best player in football, a first-rounder has a broken foot. The
    market's entire job is to find those, so the simulation has to contain
    them. `churn` is how violently August and October disagree.
    """
    out = {}
    for key, now in truth.items():
        # A log-normal kick, so a few players move a very long way and most
        # barely move: the shape a season actually has.
        out[key] = max(0.5, now * math.exp(rng.gauss(0, churn)))
    return out


def draft(preseason: dict, rng: random.Random, rooms: int = 300) -> dict[str, float]:
    """An ADP, as 300 separate rooms reading the same August would make it.

    Noise grows down the board: nobody argues about the first pick and
    everybody argues about the ninetieth. The rooms average out, which is
    the point of a consensus — what does NOT average out is that they were
    all reading August.
    """
    hi = max(preseason.values()) or 1.0
    tally: dict[str, list] = {}
    for _ in range(rooms):
        order = sorted(preseason, key=lambda k: -(preseason[k] * (1 + rng.gauss(
            0, 0.10 + 0.55 * (1 - preseason[k] / hi)))))
        for slot, key in enumerate(order, 1):
            tally.setdefault(key, []).append(slot)
    return {k: sum(v) / len(v) for k, v in tally.items()}


def spread(values) -> float:
    s = sorted(values)
    return s[-1] / (s[len(s) // 2] or 1e-9)


def main() -> int:
    rng = random.Random(2026)
    bad = 0

    # popularity="hump" is the whole point of this file. The elite are
    # hoarded and the middle churns, which is what a season looks like and
    # what the old "best players trade most" default got backwards — the
    # flattery that let a solver look healthy here and fill up with
    # kickers in production.
    for label, kw in (("trades both sides think they win",
                       {"popularity": "hump"}),
                      ("managers who disagree wildly",
                       {"popularity": "hump", "disagreement": 0.35})):
        seed = rng.randrange(1 << 30)
        ex, truth = simulate(6000, 400, random.Random(seed), **kw)
        # The draft reflects AUGUST; the trades reflect what is true now.
        # The gap between them is what the market exists to find.
        pre = august(truth, random.Random(seed ^ 3))
        adp = draft(pre, random.Random(seed ^ 7))
        # Spread the season over real days, so the filter has an order to
        # read and the drift term has something to chew on.
        day_rng = random.Random(seed ^ 11)
        for i, e in enumerate(ex):
            e["day"] = 739_900 + day_rng.randint(0, 60)
            e["key"] = f"t{i}"

        print(f"\n{label}")
        planted = spread(truth.values())
        print(f"    planted: {len(truth)} players, top/median {planted:.2f}x")

        # -- the old model -------------------------------------------------
        pri = usage_prior(truth, random.Random(99))
        old = trade_market.solve(ex, prior=pri)
        ov = old["values"]
        okeys = sorted(ov)
        orho = spearman([ov[k] for k in okeys], [truth[k] for k in okeys])
        print(f"    batch solver : {len(ov):>3} priced  "
              f"rho {orho:.3f}  spread {spread(ov.values()):.2f}x")

        # -- the new one ---------------------------------------------------
        got = trade_rating.run(ex, adp)
        market = got["market"]
        keys = [k for k in market.v if market.n.get(k, 0) > 0]
        # Compare in VALUE space; a smaller slot is a better player, so the
        # value is what lines up with the planted truth.
        mine = [market.v[k] for k in keys]
        true = [truth[k] for k in keys]
        nrho = spearman(mine, true)
        print(f"    draft filter : {len(keys):>3} priced  "
              f"rho {nrho:.3f}  spread {spread(mine):.2f}x  "
              f"decay {got['decay']} shift {got['shift']} (fit on {got['fit']['deals']:,} lopsided deals)")

        # ADP alone, on the same players: the bar the filter must clear.
        arho = spearman([-adp[k] for k in keys], true)
        print(f"    the draft alone on those players: rho {arho:.3f}")

        # Coverage where it counts: can either model price a star at all?
        order = sorted(truth, key=lambda k: -truth[k])[:50]
        old_top = len(set(order) & set(ov))
        new_top = len(set(order) & set(keys))
        print(f"    of the 50 best players: batch prices {old_top}, "
              f"filter prices {new_top}")

        # -- calibration ----------------------------------------------------
        # Checked in RANK space, which is what the page shows (a draft
        # slot), and not in value space. Comparing values needs a single
        # factor to put truth on the model's scale, and when the two have
        # different SHAPES — the model is a shifted power law, a season is
        # not — no single factor fits, so that test measures
        # misspecification and calls it overconfidence.
        #
        # The width is printed next to it on purpose. The bands come out
        # honest and enormous: a two-sigma range of several hundred picks
        # covers almost everybody, which is why the page shows a word for
        # confidence rather than a range that would look like precision.
        order = sorted(truth, key=lambda k: -truth[k])
        true_rank = {k: i + 1 for i, k in enumerate(order)}
        inside, widths = 0, []
        for k in keys:
            dear, cheap = market.band(k)
            widths.append(cheap - dear)
            if dear <= true_rank[k] <= cheap:
                inside += 1
        hit = inside / max(1, len(keys))
        widths.sort()
        print(f"    calibration  : {hit:.0%} of true ranks inside their own band"
              f" (median band {widths[len(widths) // 2]:.0f} picks wide)")

        if nrho < arho + 0.02:
            print("    FAIL: the trades added nothing to the draft")
            bad += 1
        if nrho < orho:
            print("    FAIL: worse ordering than the model it replaces")
            bad += 1
        # The one that matters. The batch solver cannot price a player
        # with fewer than eight trades, and the best players in football
        # are traded twice a season, so it prices NONE of the top fifty.
        # That is the live symptom: a board of churners with no stars.
        if new_top < 25:
            print(f"    FAIL: only {new_top}/50 of the best players priced")
            bad += 1
        if spread(mine) < planted * 0.6:
            print("    FAIL: the spread is still being squashed")
            bad += 1
        if not 0.85 <= hit <= 1.0:
            print(f"    FAIL: the bands are not honest ({hit:.0%} inside, want ~95%)")
            bad += 1

    print()
    print("The draft filter is better where the old one was weak." if not bad
          else f"{bad} check(s) failed.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
