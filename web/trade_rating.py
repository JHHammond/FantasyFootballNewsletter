"""What a player costs, starting from where the country drafted him.

THE PROBLEM THIS REPLACES. web/trade_market.py solves every trade at once
as a system of equations. It is a good estimator and it has two faults
that no amount of tuning fixes, both of them at the top of the board,
which is the part anybody looks at:

  * COVERAGE. A player needs eight trades before he can be priced. A
    genuine star is traded twice a season, so the board fills with the
    players who churn. Live, that meant six kickers and four defences,
    and after they were removed, three tight ends.
  * COMPRESSION. "value(star) = value(a) + value(b)" caps a player at the
    biggest bundle anybody actually gave up for him. Nobody offers four
    good players for one, so nobody can come out worth four good players.
    On simulated trades the fit squeezed a planted 4.27x top-to-median
    spread to 3.64x; the live board managed 2.1x. A superstar is worth
    more than anything anyone will offer, so the trades cannot see him.

Both dissolve if every player has a value BEFORE any trade is read, and
trades only move him. That value is the draft: 1,400 leagues, thousands of
people deciding what a player is worth when taking him means not taking
somebody else, with a real spread at the top and complete coverage.

WHAT THIS IS, PRECISELY. A Kalman filter over trades.

John asked how prediction markets do this, and the honest answer is that
the famous one does not apply. Hanson's LMSR prices outcome i at
e^(q_i/b) / sum_j e^(q_j/b), and those prices sum to 1 — that constraint
is where a prediction market gets its scale, for free, from the fact that
exactly one outcome happens. We have no such constraint, which is exactly
why the batch solver was degenerate and why an anchor is mandatory rather
than ornamental. What does carry over is the liquidity parameter b: the
knob for how far one trade moves a price. Here that knob is not set by
hand, it is each player's own uncertainty, which is Glicko's idea — rating
plus rating deviation, where the deviation scales the update, shrinks as
evidence arrives and grows with inactivity.

Write that down properly and it is a Kalman filter, so the arithmetic is
not a heuristic:

    state       v, one value per player; P, the variance of each
    observation a trade says sum(what A got) = sum(what B got)
                H is +1 for A's side, -1 for B's, 0 for everybody else
    innovation  e = sum_a v - sum_b v, how wrong the trade says we are
    gain        S = sum_(a,b) P_i + sigma^2
                K_i = +/- P_i / S
    update      v_i -= K_i * e          P_i *= (1 - P_i / S)

Every property John asked for falls out rather than being bolted on:

  * A player we are unsure of moves a lot on one trade; a heavily traded
    one barely moves. That is P_i / S, not a tuned constant.
  * Certainty accumulates: P shrinks with every trade he appears in.
  * Between trades P grows (DRIFT below), so a player nobody has touched
    in a month becomes movable again. No trades means no news, not
    confirmation.
  * It is sequential, so the price line is simply the state after each
    day rather than forty separate re-solves.
  * v +/- 2*sqrt(P) is an honest band, so a thinly traded player can be
    shown as uncertain instead of printed to one decimal place.

THE SCALE IS DRAFT CAPITAL, which is John's call and the right one. A
number is reported as the draft slot whose starting value equals it:
"Chase costs the 1.02". That is a sentence anybody who has drafted a team
understands, and "he is worth 103.9" is not. The curve from slot to value
is NOT invented — it is fitted to the trades themselves (see `curve`), so
the drafts supply the order and the trades supply the steepness.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

#: Shape of the draft-value curve: value = (slot + SHIFT) ** -DECAY.
#:
#: The shift is not decoration, and leaving it out broke the fit. A plain
#: slot**-d has its steepest moment at pick 1 and flattens from there,
#: which is the wrong shape: in a real draft the first eight picks are
#: nearly interchangeable and the cliff comes later, when the position
#: runs dry. Asked to fit that with one parameter the search ran to the
#: flattest curve it was allowed and made every player worth the same,
#: because a flat board scores perfectly on the one-for-one trades that
#: are a third of the data. The shift lets the curve start flat and fall
#: away after, which is both what drafts look like and what lets the
#: steepness mean anything.
DECAY = 0.95
SHIFT = 8.0
#: Searched when fitting, two-dimensionally. Below 0.2 the board is flat
#: (the first pick worth barely more than the fiftieth); above 2.5 it is a
#: cliff with everyone outside round one worth nothing.
#:
#: The shift is capped well short of where the fit would like to go. Left
#: to a wider range the search pinned it to the ceiling every time, which
#: is the flat-board bias again wearing a second parameter: a very flat
#: curve makes the slot a near-vertical function of value, so a 2% change
#: in what a player is worth prints as "down 30 picks". A binding boundary
#: is a sign the objective, not the data, is choosing.
DECAY_RANGE = (0.20, 2.50)
SHIFT_RANGE = (0.0, 24.0)
#: How far apart two managers can be and still shake hands, as a share of
#: what is changing hands. This is the observation noise: trades are not
#: fair, and treating them as exact makes the filter chase every lopsided
#: deal.
#:
#: Deliberately much larger than the disagreement actually planted in the
#: simulator (18%), and that is not a fudge. A trade is only observed when
#: BOTH managers think they won, so the trades we see are a filtered
#: sample rather than a fair one, and the filtering looks exactly like
#: extra noise. Measured against planted values, rank correlation runs
#: 0.971 at 0.22, peaks around 0.986 at 0.6-0.9, and falls back to 0.970
#: by 2.0 as the filter stops listening to the trades at all.
TRADE_NOISE = 0.7
#: A player's starting uncertainty, as a share of his starting value. The
#: draft is informative but it is an August opinion, and by October the
#: country has watched six games.
START_RD = 0.45
#: Extra uncertainty for a player nobody drafted: he has no opinion behind
#: him at all, only the fact that somebody has now traded for him.
UNDRAFTED_RD = 1.20
#: Variance added per day without a trade, as a share of starting
#: variance. Glicko's c: certainty decays, so a player untouched for a
#: month is movable again rather than frozen at an August price.
DRIFT_PER_DAY = 0.004
#: The least uncertainty we will ever claim, as a share of a player's
#: current value. Without a floor the filter talks itself into certainty:
#: every observation multiplies the variance by (1 - gain), so a heavily
#: traded player ends up with a band of nearly nothing. That would be
#: right if trades were independent measurements of the truth. They are
#: not — the same selection bias leans on every one of them, so the
#: hundredth trade confirms the first rather than adding to it. Measured
#: against planted values, only 74% of players fell inside their own
#: two-sigma band before this, where honest bands put 95% in.
MIN_RD = 0.17
#: An update may not move a value by more than this share of itself in one
#: trade. One absurd deal — somebody dumping a star for a kicker in a dead
#: league — should not relocate the top of the board.
MAX_STEP = 0.35
#: A trade whose cheaper side is worth less than this share of the dearer
#: side, by today's prices, is a dump, a joke or collusion, not a price
#: (John, 23 Oct: Jahmyr Gibbs at #11). Gibbs + Kenneth Walker for
#: MarShawn Lloyd + Tim Tebow; Gibbs + Drake Maye + Nico Collins for Kenny
#: Gainwell. Each one, read as fair, dragged him down by the full step cap.
#: Only judged when every player on the dearer side already has a settled
#: price (LOPSIDED_KNOWN trades), so an early-season rookie whose real
#: value nobody knew yet can still teach the market.
LOPSIDED = 0.25
LOPSIDED_KNOWN = 3
#: Below this many drafts a player's ADP is one room's opinion, not a
#: consensus, and he starts as undrafted instead.
MIN_DRAFTS = 20
#: How many players the price chart draws before the reader picks any.
#: Five of the six colours, so there is always room to add one.
CHART_DEFAULT = 5

#: The movers boards: how many each way, and the trades a player needs
#: before his move counts.
MOVERS = 10
MOVER_MIN_TRADES = 10

#: Teams in the league a slot is quoted in. Everything is normalised to a
#: twelve-team draft, so "2.03" means the third pick of the second round.
TEAMS = 12


def slot_value(slot: float, decay: float = DECAY, shift: float = SHIFT) -> float:
    """What the pick at `slot` is worth, normalised so pick 1 is 1.0.

    A shifted power law rather than an exponential: an exponential makes
    the hundredth pick worth indistinguishable from nothing, and in a
    redraft league he is a real player somebody starts every week.
    """
    s = max(1e-6, float(slot)) + float(shift)
    return (s ** -float(decay)) / ((1.0 + float(shift)) ** -float(decay))


def value_slot(value: float, decay: float = DECAY, shift: float = SHIFT) -> float:
    """The inverse: the slot whose starting value is `value`. This is what
    turns a number back into "costs the 1.04"."""
    if value <= 0:
        return 9_999.0
    base = (1.0 + float(shift)) ** -float(decay)
    return max(0.1, (float(value) * base) ** (-1.0 / float(decay)) - float(shift))


def pick_label(slot: float, teams: int = TEAMS) -> str:
    """A place in the market as a player number (John, 23 Oct): whoever is
    the most expensive is #1, the next #2, and so on. It replaced the
    draft-pick reading ("2.03"), which made people do round arithmetic.
    `teams` is kept so callers needn't change."""
    return f"#{max(1, int(round(float(slot))))}"


def curve(exchanges: Iterable[dict], adp: dict[str, float], *,
          steps: int = 26) -> dict[str, Any]:
    """Fit the steepness of the draft-value curve TO THE TRADES.

    The drafts say what order players go in. They do not say whether the
    first pick is worth twice the twenty-fourth or ten times it, and that
    is exactly what trades do say: every two-for-one is a statement about
    how fast value falls away. So the exponent is chosen to make the
    trades balance as well as they can, which is one number fitted to tens
    of thousands of equations.

    Scored on RELATIVE residual — |a - b| / (a + b) — because an absolute
    one would be minimised by making everybody worthless, which is the
    same degeneracy that sank the batch solver.

    ONLY LOPSIDED TRADES COUNT. A one-for-one says the two players are
    close, which every monotone curve already agrees with, so it carries
    no information about steepness — and there are enough of them
    (a third of the data) that including them drags the fit to a flat
    board, where every one-for-one scores perfectly and nothing else
    matters. Two-for-one is the trade that knows how fast value falls.
    """
    deals = [(list(e.get("a") or []), list(e.get("b") or [])) for e in exchanges]
    deals = [(a, b) for a, b in deals
             if a and b and set(a) != set(b) and len(set(a)) != len(set(b))
             and all(k in adp for k in a) and all(k in adp for k in b)]
    if len(deals) < 50:
        return {"decay": DECAY, "shift": SHIFT, "deals": len(deals),
                "fitted": False, "error": None}

    best = (DECAY, SHIFT)
    best_err = None
    for i in range(steps + 1):
        d = DECAY_RANGE[0] + (DECAY_RANGE[1] - DECAY_RANGE[0]) * i / steps
        for j in range(steps + 1):
            sh = SHIFT_RANGE[0] + (SHIFT_RANGE[1] - SHIFT_RANGE[0]) * j / steps
            total = 0.0
            for a, b in deals:
                va = sum(slot_value(adp[k], d, sh) for k in a)
                vb = sum(slot_value(adp[k], d, sh) for k in b)
                if va + vb > 0:
                    total += abs(va - vb) / (va + vb)
            err = total / len(deals)
            if best_err is None or err < best_err:
                best, best_err = (d, sh), err
    return {"decay": round(best[0], 3), "shift": round(best[1], 2),
            "deals": len(deals), "fitted": True, "error": round(best_err, 4)}


def roster_charge(exchanges: Iterable[dict], adp: dict[str, float], *,
                  decay: float = DECAY, shift: float = SHIFT) -> float:
    """What a roster spot costs, in value units. Usually positive.

    Two-for-one trades do not balance, and everybody who has played knows
    why: the side giving up two bodies and getting one back is buying a
    roster spot as well as a player, so it expects to come out ahead on
    talent. Ignore that and the model quietly pushes every consolidation
    target up and every depth piece down, forever, because it keeps seeing
    the same unbalanced equation and keeps believing it.

    THE BATCH SOLVER COULD NOT DO THIS. There, a per-body charge was
    unidentifiable: adding a constant c to every player changes each
    side's total by c times the number of players on it, which is exactly
    what a body charge does, so the two were the same parameter and fitting
    them together made the charge run away. With the draft holding the
    scale still, that degeneracy is gone and the charge becomes an ordinary
    thing to measure. It is one of the real gains from having a prior.

    Estimated by the method of moments rather than another grid search:
    across lopsided trades, the average gap per extra body IS the charge.
    The median, not the mean, because one league trading a star for a
    kicker should not set the price of a bench spot.
    """
    gaps = []
    for e in exchanges:
        a = list(dict.fromkeys(e.get("a") or []))
        b = list(dict.fromkeys(e.get("b") or []))
        if not a or not b or len(a) == len(b):
            continue
        if not all(k in adp for k in a + b):
            continue
        va = sum(slot_value(adp[k], decay, shift) for k in a)
        vb = sum(slot_value(adp[k], decay, shift) for k in b)
        # The side receiving FEWER players should be getting more talent.
        gaps.append((vb - va) / (len(b) - len(a)))
    if len(gaps) < 50:
        return 0.0
    gaps.sort()
    return gaps[len(gaps) // 2]


class Market:
    """Values and their uncertainty, carried forward one trade at a time."""

    def __init__(self, adp: dict[str, float], *, decay: float = DECAY,
                 shift: float = SHIFT, spot: float = 0.0,
                 last_slot: Optional[float] = None):
        self.decay = float(decay)
        self.shift = float(shift)
        #: What a roster spot is worth, so a two-for-one is read as the
        #: trade it actually is. See roster_charge().
        self.spot = float(spot)
        self.adp = dict(adp)
        self.v: dict[str, float] = {}
        self.p: dict[str, float] = {}
        self.n: dict[str, int] = {}
        #: Trades ignored as lopsided (see LOPSIDED), for the page's footnote.
        self.ignored = 0
        self.seen_day: dict[str, int] = {}
        self.start: dict[str, float] = {}
        # Where an undrafted player comes in: just past the last pick
        # anybody made, with a lot of doubt attached.
        self.undrafted_slot = float(last_slot or (max(adp.values()) if adp else 180)) + 12

    def _ensure(self, key: str, day: Optional[int]) -> None:
        if key in self.v:
            return
        slot = self.adp.get(key)
        if slot is None:
            slot, rd = self.undrafted_slot, UNDRAFTED_RD
        else:
            rd = START_RD
        value = slot_value(slot, self.decay, self.shift)
        self.v[key] = value
        self.start[key] = value
        self.p[key] = (rd * value) ** 2
        self.n[key] = 0
        self.seen_day[key] = day if day is not None else 0

    def _drift(self, key: str, day: Optional[int]) -> None:
        """Glicko's inactivity term: certainty decays between trades.

        Capped at the uncertainty he started with. A player nobody has
        traded since August is as unknown as he was in August — not more
        so, and certainly not less.
        """
        if day is None:
            return
        gap = day - self.seen_day.get(key, day)
        if gap > 0:
            ceiling = (UNDRAFTED_RD * self.start[key]) ** 2
            self.p[key] = min(ceiling,
                              self.p[key] + DRIFT_PER_DAY * gap * self.start[key] ** 2)
        self.seen_day[key] = day

    def observe(self, a: list[str], b: list[str], day: Optional[int] = None) -> float:
        """One trade. Returns the innovation — how wrong it said we were."""
        a = list(dict.fromkeys(a))
        b = list(dict.fromkeys(b))
        if not a or not b or set(a) == set(b):
            return 0.0
        for key in a + b:
            self._ensure(key, day)
            self._drift(key, day)

        va = sum(self.v[k] for k in a)
        vb = sum(self.v[k] for k in b)
        # What the trade says, once the roster spots are priced in. The
        # side taking back FEWER bodies is buying a spot as well as a
        # player, so it rightly receives less talent than it gives: a fair
        # two-for-one has sum(a) - sum(b) = spot * (len(a) - len(b)).
        # Getting this sign backwards does not look like a sign error, it
        # looks like a broken model — it drove rank correlation to 0.57.
        innovation = (va - vb) - self.spot * (len(a) - len(b))
        if va + vb <= 0:
            return 0.0
        # A dump, not a price. The roster-spot charge is credited to the
        # side taking back fewer bodies before the two are compared.
        fa = va - self.spot * max(0, len(a) - len(b))
        fb = vb - self.spot * max(0, len(b) - len(a))
        hi_side = a if fa >= fb else b
        lo, hi = sorted((max(fa, 0.0), max(fb, 0.0)))
        if (hi > 0 and lo / hi < LOPSIDED
                and all(self.n.get(k, 0) >= LOPSIDED_KNOWN for k in hi_side)):
            self.ignored += 1
            return 0.0

        # Observation noise, relative to the size of the deal: two stars
        # swapping leaves more room for honest disagreement, in absolute
        # terms, than two bench players do.
        sigma2 = (TRADE_NOISE * (va + vb) / 2.0) ** 2
        s = sum(self.p[k] for k in a + b) + sigma2
        if s <= 0:
            return innovation

        for side, sign in ((a, 1.0), (b, -1.0)):
            for key in side:
                gain = self.p[key] / s
                step = -sign * gain * innovation
                # No single trade may relocate a player.
                cap = MAX_STEP * self.v[key]
                step = max(-cap, min(cap, step))
                self.v[key] = max(1e-9, self.v[key] + step)
                self.p[key] *= max(0.05, 1.0 - gain)
                # Never claim to know him better than MIN_RD. See the note
                # on that constant: trades are not independent evidence.
                self.p[key] = max(self.p[key], (MIN_RD * self.v[key]) ** 2)
                self.n[key] += 1
        return innovation

    def normalise(self, keys: Optional[list] = None) -> float:
        """Put the median player back where he started, and return by how
        much everything moved.

        VALUE HERE IS RELATIVE and the scale is arbitrary, so a drift that
        moves every player the same way says nothing — but it shows up on
        the page as a sentence, and a false one. The first run of this
        model had the whole top of the board "down" between 24 and 45
        picks since the draft, every player, with no risers at all. Nothing
        had happened; the level had simply sagged, mostly because the
        roster-spot charge leans on the dearest side of every lopsided
        trade.

        Dividing it out leaves exactly the thing worth printing: movement
        against the rest of the field. Somebody is up only if he passed
        somebody.
        """
        rated = [k for k in (keys or self.v) if self.n.get(k, 0) > 0]
        if len(rated) < 10:
            return 1.0
        ratios = sorted(self.v[k] / self.start[k] for k in rated
                        if self.start[k] > 0)
        factor = ratios[len(ratios) // 2] or 1.0
        if factor <= 0:
            return 1.0
        for key in self.v:
            self.v[key] /= factor
            self.p[key] /= factor ** 2
        return factor

    # -- reading it back ---------------------------------------------------

    def ranked(self, skip: frozenset = frozenset()) -> dict[str, float]:
        """Every TRADED player's price as a draft slot, by rank: the most
        expensive costs the earliest pick any of them was drafted at, the
        twelfth the twelfth, and so on down.

        WHY NOT value_slot(). The values are right about ORDER and wrong
        about LEVEL. Live on 23 Oct the order was exactly what anybody
        watching would say — Puka, Walker, Bijan, JSN, Lamb, Taylor, Gibbs
        — but the most expensive player in the country "cost the 5.10",
        every one of the top fifteen was "down" 38 to 64 picks, and the
        movers all converged on rounds nine to twelve from both ends. The
        additive bundle arithmetic squeezes the top of the scale, and no
        normalisation of the level undoes a squeeze. Rank cannot be
        squeezed: somebody is always first.

        ONLY TRADED PLAYERS TAKE A PLACE. The first version let an untraded
        player hold his draft position against the field, and Puka came out
        "costing the 3.02" with twenty-six invisible players ahead of him:
        everybody traded had been squeezed and nobody untraded had, so
        every untraded first-rounder outranked every star the market had
        actually priced. A player with no trades has no market price, only
        a draft price; he is listed apart (see build's "held"), not ranked.

        The slots are the traded players' own ADPs, in order, so a player
        who holds his draft rank among them reads as where he was drafted.
        Past the last drafted one it is one slot per place. `skip`
        (kickers, defences) is out of both the ranking and the slots.
        """
        pool = [k for k in self.v if self.n.get(k, 0) > 0 and k not in skip]
        slots = sorted(self.adp[k] for k in pool if k in self.adp)
        order = sorted(pool, key=lambda k: (-self.v[k], k))
        out: dict[str, float] = {}
        for i, key in enumerate(order):
            out[key] = (slots[i] if i < len(slots)
                        else (slots[-1] if slots else 0.0) + (i - len(slots) + 1))
        return out

    def slot(self, key: str) -> float:
        return value_slot(self.v[key], self.decay, self.shift)

    def band(self, key: str) -> tuple:
        """(dearest, cheapest) slot within two standard deviations.

        Dearest first because a lower slot number is a better player, which
        reads backwards until you remember that pick 1 is the good one.
        """
        sd = math.sqrt(max(0.0, self.p[key]))
        hi = value_slot(max(1e-9, self.v[key] + 2 * sd), self.decay, self.shift)
        # Clamped at the bottom. Slot is a power law in value, so once
        # v - 2sd approaches zero the arithmetic runs away: a player with
        # seven trades and a perfectly ordinary 44% uncertainty came out
        # with a band reaching pick 1,095, and another pick 3,918, in a
        # draft of about 200. Those are not wrong, they are useless, and
        # printing them would say "we have no idea" in a very long-winded
        # way. The floor is just past the last pick anybody made.
        floor = self.undrafted_slot + TEAMS
        lo = min(floor, value_slot(max(1e-9, self.v[key] - 2 * sd),
                                   self.decay, self.shift))
        return (hi, max(hi, lo))

    def row(self, key: str, names: Optional[dict] = None,
            now: Optional[float] = None,
            drafted: Optional[float] = None) -> dict[str, Any]:
        """`now` is the price to print and `drafted` where he went, both as
        places (see places()); without them, the value read straight off
        the curve and the raw ADP (what the validators compare)."""
        meta = (names or {}).get(key) or {}
        drafted = self.adp.get(key) if drafted is None else float(drafted)
        now = self.slot(key) if now is None else float(now)
        dear, cheap = self.band(key)
        # How much of the draft's original doubt we have talked ourselves
        # out of. This, not the band, is what the page shows: a band in
        # slot terms is honest and far too wide to read.
        sure = round(1.0 - min(1.0, math.sqrt(max(0.0, self.p[key]))
                               / max(1e-9, self.start[key])), 2)
        return {
            "player_key": key,
            "name": meta.get("name") or key,
            "position": meta.get("position"),
            "nfl_team": meta.get("nfl_team"),
            "slot": round(now, 1),
            "costs": pick_label(now),
            "drafted": round(drafted, 1) if drafted else None,
            "drafted_at": pick_label(drafted) if drafted else None,
            #: Positive means the country now pays MORE for him than it
            #: drafted him for, which is a smaller slot number — so the
            #: subtraction runs this way round on purpose.
            #:
            "moved": round(drafted - now, 1) if drafted else None,
            "trades": self.n.get(key, 0),
            "band": [round(dear, 1), round(cheap, 1)],
            "sure": sure,
            "firmness": ("settled" if sure >= 0.72 else
                         "firming" if sure >= 0.5 else "thin"),
            "undrafted": drafted is None,
        }


def run(exchanges: Iterable[dict], adp: dict[str, float], *,
        decay: Optional[float] = None, days: bool = True,
        skip: frozenset = frozenset()) -> dict[str, Any]:
    """Play every trade through in order and keep the daily state.

    Order is the whole point — a filter reads its observations once, in
    the order they happened — so a trade with no timestamp is placed at
    the end of its week rather than dropped, and undated trades sort after
    dated ones on the same day.
    """
    deals = [e for e in exchanges if e.get("a") and e.get("b")]
    deals.sort(key=lambda e: (e.get("day") if e.get("day") is not None else 0,
                              str(e.get("key") or "")))
    if decay is None:
        fit = curve(deals, adp)
        decay, shift = fit["decay"], fit.get("shift", SHIFT)
    else:
        shift = SHIFT
        fit = {"decay": decay, "shift": shift, "fitted": False,
               "deals": len(deals), "error": None}
    spot = roster_charge(deals, adp, decay=decay, shift=shift)
    fit["spot"] = round(spot, 5)

    market = Market(adp, decay=decay, shift=shift, spot=spot)
    series: dict[str, list] = {}
    seen_days: list[int] = []
    day_now = None
    for deal in deals:
        day = deal.get("day")
        if days and day is not None and day != day_now:
            if day_now is not None:
                _snapshot(market, series, day_now, skip)
                seen_days.append(day_now)
            day_now = day
        market.observe(list(deal["a"]), list(deal["b"]), day)
    if days and day_now is not None:
        _snapshot(market, series, day_now, skip)
        seen_days.append(day_now)

    return {"market": market, "series": series, "days": seen_days,
            "decay": decay, "shift": shift, "spot": spot,
            "fit": fit, "used": len(deals) - market.ignored, "ignored": market.ignored}


def build(db, season: int, weeks: list[int], *, limit: int = 40,
          lines: int = 6) -> dict[str, Any]:
    """The panel, priced from the draft. {} when there is nothing to draw.

    Returns {} rather than raising if the drafts have not been collected
    or migration 050 has not been run, so the caller can fall back to the
    older batch market instead of showing the page an error.
    """
    from web import trade_market, trade_prior

    try:
        raw = db.trade_exchanges(season, 1, max(weeks or [1]))
        names = db.trade_player_names(season)
        board = db.adp(season, MIN_DRAFTS) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[rating] no data: {type(exc).__name__}: {exc}", flush=True)
        return {}
    players = board.get("players") or []
    if not raw or len(players) < 50:
        return {}

    adp = {p["player_key"]: float(p["adp"]) for p in players if p.get("adp")}
    for p in players:
        meta = names.setdefault(p["player_key"], {})
        for field in ("name", "position", "nfl_team"):
            meta.setdefault(field, p.get(field))

    ex = trade_market.days_for(raw, season)
    for i, e in enumerate(ex):
        e.setdefault("key", str(i))
    # Kickers and defences are churned rather than valued — see the note
    # in trade_market.build. They stay in the fit and off the board, and
    # out of the ranking that prices everybody else.
    keys = set(adp) | {k for e in ex for k in list(e.get("a") or []) + list(e.get("b") or [])}
    skip = frozenset(k for k in keys
                     if ((names.get(k) or {}).get("position") or "") in trade_prior.THIN)
    got = run(ex, adp, skip=skip)
    market = got["market"]
    # The level sags over a season (see Market.normalise). Prices are read
    # by rank now and do not need it, but the drift is still reported.
    drift = market.normalise()
    ranked = market.ranked(skip)

    rated = [k for k in market.v if k not in skip and market.n.get(k, 0) > 0
             and k in ranked]
    rated.sort(key=lambda k: (ranked[k], k))
    # PLACES, NOT AVERAGES (John, 23 Oct: "it should work like 1.01, 1.02,
    # 1.03"). The price was the average draft slot at his place, so two
    # players could share the 1.05 and the list started wherever the
    # earliest traded ADP happened to be. Now the most expensive player is
    # the 1.01 and nobody shares a pick; "drafted" is his place in the
    # draft order among the same players, so "up 3" means he passed three.
    place, drafted_place = places(rated, adp)
    rows = [market.row(k, names, now=place[k], drafted=drafted_place.get(k))
            for k in rated[:limit]]

    # Ten trades before a move counts: one odd deal is not a market moving.
    movers = sorted((r for r in (market.row(k, names, now=place[k], drafted=drafted_place.get(k))
                                 for k in rated)
                     if r["moved"] is not None and r["trades"] >= MOVER_MIN_TRADES),
                    key=lambda r: -r["moved"])

    # WHICH PLAYERS GET A LINE, and why not simply the dearest six.
    #
    # Picking the top six by today's price guarantees they meet on the
    # right-hand edge: they are the six highest of a few hundred, so at the
    # moment they are chosen they are adjacent by construction, within a
    # percent or two of each other. Their histories are free to be
    # anywhere, so every such chart shows lines fanning in to a point, and
    # it looks like a finding. It is the selection.
    #
    # The ones worth drawing are the ones that MOVED. They were chosen for
    # travelling, not for where they finished, so there is no edge they are
    # obliged to meet at.
    travelled = sorted((r for r in movers if r["trades"] >= 6),
                       key=lambda r: -abs(r["moved"]))
    drawn = [r["player_key"] for r in travelled[:lines]]
    if len(drawn) < 2:
        drawn = rated[:lines]
    # Drafted early and never traded: nobody is selling. Listed rather
    # than ranked (see Market.ranked). Also the first place to look if a
    # name appears twice — a platform pick that never matched its Sleeper
    # id has an ADP under its own key, and that key never trades.
    held = [{"player_key": k, "name": (names.get(k) or {}).get("name") or k,
             "position": (names.get(k) or {}).get("position"),
             "drafted_at": pick_label(adp[k]), "drafted": round(adp[k], 1)}
            for k in sorted(adp, key=lambda k: adp[k])
            if k not in skip and market.n.get(k, 0) == 0][:12]
    # THE CHART: one line per priced player, one point per NFL week,
    # starting from where he was drafted. Every player, not a chosen six,
    # because the page lets the reader pick who to draw — see weekly().
    series = on_place_scale(got["series"], rated, adp, place)
    weekly_lines = weekly(series, drafted_place, season, rated)
    # By day as well (John, 23 Oct: "it should be by day, to get the Kalshi
    # type view"). Every player's line is kept here, in the cache; the page
    # carries only the default few and fetches the rest when added.
    day_lines = daily(series, drafted_place, rated)
    from web.luck import photo_url
    pickable = []
    for i, k in enumerate(rated, 1):
        meta = names.get(k) or {}
        pickable.append({
            "k": k, "name": meta.get("name") or k, "pos": meta.get("position"),
            "team": meta.get("nfl_team"), "rank": i,
            "costs": pick_label(place[k]),
            "drafted": pick_label(drafted_place[k]) if k in drafted_place else None,
            #: The numbers behind those labels, and how settled the price
            #: is, for the player page.
            "dp": drafted_place.get(k),
            "firm": market.row(k)["firmness"],
            #: His value on the market's own scale, so the player page can
            #: add a package up and compare it with him (see "spot").
            "v": round(market.v[k], 6),
            "trades": market.n.get(k, 0),
            "photo": photo_url(k, meta.get("position"))})
    return {
        "rows": rows,
        "held": held,
        "weekly": weekly_lines,
        "daily": day_lines,
        #: What the page embeds: the default lines only (the top few by
        #: price). Everybody else is fetched when the reader adds him.
        "chart": {"base": day_lines["base"], "last": day_lines["last"],
                  "default": [k for k in rated if k in day_lines["lines"]][:CHART_DEFAULT],
                  "lines": {k: day_lines["lines"][k]
                            for k in [k for k in rated if k in day_lines["lines"]][:CHART_DEFAULT]}},
        "pickable": pickable,
        "up": [r for r in movers if r["moved"] > 0][:MOVERS],
        "down": [r for r in reversed(movers) if r["moved"] < 0][:MOVERS],
        "mover_min_trades": MOVER_MIN_TRADES,
        "series": {k: got["series"][k] for k in drawn if k in got["series"]},
        "lines": drawn,
        "days": got["days"],
        "used": got["used"],
        "ignored": got.get("ignored", 0),
        "rated": len(rated),
        "streamers": len(skip),
        "drafts": board.get("drafts") or 0,
        "picks": board.get("picks") or 0,
        "adp_players": len(adp),
        "decay": got["decay"],
        "shift": got["shift"],
        "spot": round(got["spot"], 5),
        #: How far the whole board sagged before it was normalised away.
        "drift": round(drift, 4),
        "fit": got["fit"],
    }


def places(rated: list[str], adp: dict[str, float]) -> tuple[dict, dict]:
    """(price place, draft place) for every rated player, 1 = the 1.01.

    `rated` arrives most expensive first. The draft place is his position
    when the same players are ordered by ADP, ties broken the same way every
    time; a player nobody drafted has none."""
    place = {k: i + 1 for i, k in enumerate(rated)}
    drafted = [k for k in rated if k in adp]
    drafted.sort(key=lambda k: (adp[k], k))
    return place, {k: i + 1 for i, k in enumerate(drafted)}


def on_place_scale(series: dict, rated: list[str], adp: dict[str, float],
                   place: dict[str, int]) -> dict:
    """The daily closes re-read as places, so a line ends on the place in
    the table and starts at his place in the draft.

    Each close was taken as the ADP sitting at his place that day (see
    Market.ranked); that ADP's position among the final board's ADPs is the
    place it stands for. On the last day this is exactly his place."""
    from bisect import bisect_left
    vals = sorted(adp[k] for k in rated if k in adp)
    n = len(vals)

    def to_place(v: float) -> float:
        if n and v > vals[-1]:
            return float(n + (v - vals[-1]))
        return float(bisect_left(vals, v - 1e-9) + 1)

    out = {}
    for key, pts in series.items():
        if key not in place:
            continue
        line = [dict(pt, slot=to_place(pt["slot"])) for pt in pts]
        if line:
            line[-1]["slot"] = float(place[key])
        out[key] = line
    return out


def daily(series: dict, adp: dict[str, float],
          keys: Iterable[str]) -> dict[str, Any]:
    """Every player's close on every trading day, as offsets from a base
    date so a line is pairs of small numbers:

        {"base": "2026-08-27", "last": 57,
         "lines": {key: [[0, 9.8], [8, 9.1], [9, 8.7], ...]}}

    Day 0 is "the draft": the base is three days before the first trade, and
    every drafted player's line starts there at his ADP, so the first
    segment is still draft-to-market. A player first traded in October
    jumps from his draft price to his first close, which is what happened.
    A day with no trades anywhere has no close, and the line runs straight
    across it.
    """
    from datetime import date
    days = sorted({int(pt["day"]) for pts in series.values() for pt in pts})
    if not days:
        return {"base": None, "last": 0, "lines": {}}
    base = days[0] - 3
    lines: dict[str, list] = {}
    for key in keys:
        pts = series.get(key) or []
        if not pts:
            continue
        line = [[0, round(float(adp[key]), 1)]] if key in adp else []
        line += [[int(pt["day"]) - base, pt["slot"]] for pt in pts]
        lines[key] = line
    return {"base": date.fromordinal(base).isoformat(),
            "last": days[-1] - base, "lines": lines}


def weekly(series: dict, adp: dict[str, float], season: int,
           keys: Iterable[str]) -> dict[str, list]:
    """Daily closes folded to one point per NFL week: the price at the end
    of that week, for every player in `keys`.

    Each line starts at [0, his ADP] — "where the country drafted him" —
    so the first segment of every line is the draft-to-now story on its
    own. Trades before the opener fold into week 1: preseason is real
    trading, but there is no week 0 to plot it in apart from the draft.

    Weekly, not daily, because trades bunch on weekends and around waivers
    and a daily line is mostly that rhythm. The rest of the page counts in
    weeks too.
    """
    import nfl_week
    from datetime import date
    try:
        opener = nfl_week.season_opener(int(season)).toordinal()
    except Exception:  # noqa: BLE001
        opener = date(int(season), 9, 4).toordinal()
    out: dict[str, list] = {}
    for key in keys:
        points = series.get(key) or []
        if not points:
            continue
        by_week: dict[int, float] = {}
        for pt in points:                         # in day order: last wins
            wk = max(1, (int(pt["day"]) - opener) // 7 + 1)
            by_week[wk] = pt["slot"]
        line = []
        if key in adp:
            line.append([0, round(float(adp[key]), 1)])
        line += [[w, by_week[w]] for w in sorted(by_week)]
        out[key] = line
    return out


def _snapshot(market: Market, series: dict, day: int,
              skip: frozenset = frozenset()) -> None:
    """One day's close, for every player the market has an opinion on.

    By rank, like the board, so a line's last point is the price printed
    beside it. They were on different scales before (the series was taken
    before the level was normalised), and the lines ended a few picks away
    from their own table."""
    ranked = market.ranked(skip)
    for key in market.v:
        if market.n.get(key, 0) > 0 and key in ranked:
            series.setdefault(key, []).append({
                "day": day, "slot": round(ranked[key], 1),
                "trades": market.n[key]})
