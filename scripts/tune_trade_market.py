"""Find a trade-market fit that is right because it CONVERGED (21 Oct 2026).

Not a tuning script in the usual sense. It exists because of a bug that
took most of a day to understand, and it is worth keeping so the next
person does not repeat it.

THE BUG. The solver minimises, over every trade, the gap between what the
two sides are worth. Left to run, it finds a wonderful fit: give a handful
of players who happen to trade evenly among themselves a big value, give
EVERYBODY ELSE ZERO, and now almost every trade in the data reads 0 = 0,
which is a perfect score and complete nonsense. It is a real minimum of a
real objective, and it is better — by the only measure the solver has —
than the truth. The honest answer is merely a place the optimiser passes
THROUGH on its way there.

So for a while the market looked good only because the loop stopped early,
and every attempt to make it converge properly made it worse: tightening
the tolerance on the noisiest case took rank correlation from 0.90 to 0.36.
Early stopping was doing all the regularising, invisibly, and a fit you
have to interrupt to keep is a fit you do not understand.

WHAT ACTUALLY FORBIDS IT. Something has to make zeroing a player out cost
something. Two candidates, measured here at real convergence:

  ridge  Each player is also pulled toward average by RIDGE imaginary
         trades. Dropping him to zero now costs RIDGE; the sparse answer
         stops being free.
  floor  Nobody in the fit may fall below a fraction of average, so the
         sparse answer is not merely dear, it is unreachable.

Run with no arguments. Prints rank correlation per scenario for each
setting, at a tolerance tight enough that the answer is the minimum and
not a waypoint — which is the whole point.
"""

from __future__ import annotations

import random
import sys

sys.path.insert(0, "/root/cd")

from scripts.check_trade_market import simulate, spearman  # noqa: E402
from web import trade_market as tm  # noqa: E402

#: Tight enough that we are reporting the minimum, not a waypoint.
TOL = 1e-7
ROUNDS = 4000


def fit(ex, *, ridge: float, floor: float, norm: bool, rounds: int = ROUNDS,
        relax: float = 0.7, tol: float = TOL):
    """Coordinate descent with a ridge, a floor, and optional renormalising."""
    pairs = [(list(e["a"]), list(e["b"])) for e in ex if e.get("a") and e.get("b")]
    pairs = [(a, b) for a, b in pairs if set(a) != set(b)]
    seen: dict[str, int] = {}
    for a, b in pairs:
        for k in set(a) | set(b):
            seen[k] = seen.get(k, 0) + 1
    v = {k: 1.0 for k in seen}
    where: dict[str, list] = {}
    for i, (a, b) in enumerate(pairs):
        for k in set(a):
            where.setdefault(k, []).append((i, 0))
        for k in set(b):
            where.setdefault(k, []).append((i, 1))
    totals = [[float(len(set(a))), float(len(set(b)))] for a, b in pairs]

    rounds_done = 0
    for rounds_done in range(1, rounds + 1):
        moved = 0.0
        for key, spots in where.items():
            was = v[key]
            wants = 0.0
            for i, side in spots:
                t = totals[i]
                wants += t[1 - side] - (t[side] - was)
            target = (wants + ridge) / (len(spots) + ridge)
            new = max(floor, was + relax * (target - was))
            if new != was:
                delta = new - was
                for i, side in spots:
                    totals[i][side] += delta
                moved = max(moved, abs(delta))
                v[key] = new
        if norm:
            mean = sum(v.values()) / len(v)
            if mean > 0 and abs(mean - 1.0) > 1e-12:
                for k in v:
                    v[k] /= mean
                for t in totals:
                    t[0] /= mean
                    t[1] /= mean
        if moved < tol:
            break
    return v, seen, rounds_done


def score(v, seen, truth):
    pub = {k: x for k, x in v.items() if seen[k] >= tm.MIN_TRADES}
    if not pub:
        return 0.0, 99.9, 0, 0
    keys = sorted(pub)
    true = [truth[k] for k in keys]
    mine = tm.MEDIAN_AT / (tm._median([x for x in v.values() if x > 0]) or 1)
    theirs = tm.MEDIAN_AT / (tm._median(true) or 1)
    err = sorted(abs(pub[k] * mine - truth[k] * theirs) for k in keys)
    floored = sum(1 for k in keys if pub[k] * mine < tm.FLOOR_AT)
    return spearman([pub[k] for k in keys], true), err[len(err) // 2], floored, len(pub)


def main() -> int:
    rng = random.Random(2026)
    cases = {}
    for label, kw in (("selection", {}), ("fair", {"selected": False}),
                      ("wild 0.35", {"disagreement": 0.35}), ("no spot", {"spot": 0.0})):
        cases[label] = simulate(6000, 400, random.Random(rng.randrange(1 << 30)), **kw)

    settings = [("ridge", {"ridge": r, "floor": 0.0, "norm": False}) for r in
                (0.3, 1.0, 3.0, 10.0)]
    settings += [("floor", {"ridge": 0.0, "floor": f, "norm": True}) for f in
                 (0.05, 0.15, 0.35)]
    settings += [("both", {"ridge": 1.0, "floor": 0.15, "norm": True})]

    print(f"converged fits (tol={TOL:g}, cap {ROUNDS} rounds)\n")
    print(f"{'setting':<26} " + "  ".join(f"{k:>16}" for k in cases))
    for kind, kw in settings:
        label = f"{kind} " + " ".join(f"{k}={x:g}" for k, x in kw.items()
                                     if not isinstance(x, bool) and x)
        cells = []
        for ex, truth in cases.values():
            v, seen, done = fit(ex, **kw)
            rho, err, floored, n = score(v, seen, truth)
            cells.append(f"{rho:.3f}/{err:4.1f}/{done:<4}")
        print(f"{label:<26} " + "  ".join(f"{c:>16}" for c in cells))
    print("\neach cell: rank correlation / median error / rounds to converge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
