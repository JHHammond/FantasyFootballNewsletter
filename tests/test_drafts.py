"""
Our own ADP, and the draft-anchored market (050, 22 Oct 2026).

The market used to price only players somebody traded eight times, which
in a real season means the players who churn — kickers, defences, tight
ends — and never the best players in football, who are traded twice. This
covers the other half: a value for every drafted player before any trade
is read.

Run with: python -m pytest tests/test_drafts.py -q
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from web import demo_db, drafts, trade_rating  # noqa: E402

SEASON = 2026


@pytest.fixture(autouse=True)
def clean():
    for store in (demo_db._LEAGUES, demo_db._DRAFT_PICKS, demo_db._DRAFT_SCANS,
                  demo_db._NFL_PLAYERS, demo_db._TRADES, demo_db._TRADE_PLAYERS):
        store.clear()
    yield


def _league(pid="a", provider="sleeper", size=12, fmt="redraft"):
    lg = demo_db.create_league(provider=provider, platform_league_id=pid,
                               league_name="A League", paper_name="The A Times",
                               commissioner_name="", season=SEASON,
                               public_slug=f"slug-{pid}", admin_token="t" * 20)
    lg = dict(lg)
    lg["team_count"] = size
    lg["format"] = fmt
    return lg


# ----------------------------------------------------------------- the draft


def test_a_pick_is_keyed_like_every_other_player():
    """A drafted player has to join to what he was traded for and what he
    went on to score, which means resolving to a Sleeper id through the
    same matcher lineup_players uses. The platforms hand back bare ids, so
    each is prefixed with its provider first."""
    seen = {}

    class Ctx:
        def resolve(self, p):
            seen[p.player_id] = True
            return {"4984": "4984", "espn:3139477": "6786"}.get(p.player_id)

        def name(self, sid):
            return ["Josh Allen", "QB", "BUF"]

    rows = drafts.rows(_league(), {"4984": {"round": 1, "overall": 3,
                                            "team_id": "t2"}}, SEASON, Ctx())
    assert len(rows) == 1
    r = rows[0]
    assert r["player_key"] == "4984" and r["name"] == "Josh Allen"
    assert r["overall"] == 3 and r["round"] == 1 and r["team_count"] == 12

    # ESPN's bare id is prefixed before it is offered to the matcher.
    rows = drafts.rows(_league("b", provider="espn"),
                       {"3139477": {"round": 2, "overall": 15}}, SEASON, Ctx())
    assert "espn:3139477" in seen
    assert rows[0]["player_key"] == "6786"


def test_an_unmatched_pick_keeps_its_platform_id():
    """He cannot be joined to anything, and adp() reports that share —
    which is what caps the whole exercise. Guessing would be worse."""
    class Ctx:
        def resolve(self, p):
            return None

        def name(self, sid):
            return None

    rows = drafts.rows(_league("c", provider="yahoo"),
                       {"nfl.p.9999": {"round": 1, "overall": 1}}, SEASON, Ctx())
    assert rows[0]["player_key"] == "yahoo:nfl.p.9999"


def test_an_unnumbered_pick_is_not_an_adp_sample():
    """A platform that did not say where the pick fell cannot contribute
    to an average draft position."""
    rows = drafts.rows(_league(), {"4984": {"round": 1, "overall": 0}}, SEASON, None)
    assert rows == []


def test_dynasty_leagues_are_left_alone():
    """A startup draft prices a career, not a season. Averaging the two
    would make both meaningless, so a dynasty league is recorded as
    scanned and contributes nothing."""
    lg = _league("d", fmt="dynasty")
    n = drafts.collect_league(demo_db, lg, SEASON, None)
    assert n == 0
    assert demo_db.draft_scan_league_ids(SEASON) == {lg["id"]}


def test_a_small_league_is_not_a_big_one():
    """Pick 11 of ten teams is the last of round one; pick 11 of fourteen
    is the middle of it. Averaging them raw would blur the two, so every
    slot is normalised to its twelve-team equivalent."""
    for n, (pid, size) in enumerate(((("ten"), 10), ("fourteen", 14))):
        lg = _league(pid, size=size)
        demo_db.upsert_draft_picks([
            {"league_id": lg["id"], "season": SEASON, "player_key": "p",
             "overall": 11, "round": 1, "team_count": size, "name": "P",
             "position": "WR", "nfl_team": "CIN"}])
    got = demo_db.adp(SEASON, min_drafts=2)
    p = got["players"][0]
    # (11-1)*12/10+1 = 13.0 and (11-1)*12/14+1 = 9.57; the average is 11.3.
    assert p["adp"] == pytest.approx(11.29, abs=0.02), p


def test_the_disagreement_is_kept_not_just_the_average():
    """A player every room takes within four picks is a settled opinion;
    one who goes 20th in some and 60th in others is contested, and the
    market should be readier to move him. That spread is the uncertainty
    the rating model starts from, taken from the drafts rather than
    invented."""
    for i, slot in enumerate([20, 22, 21, 60, 58, 62]):
        lg = _league(f"l{i}")
        demo_db.upsert_draft_picks([
            {"league_id": lg["id"], "season": SEASON, "player_key": "split",
             "overall": slot, "round": 2, "team_count": 12, "name": "Split",
             "position": "RB", "nfl_team": "LAR"},
            {"league_id": lg["id"], "season": SEASON, "player_key": "agreed",
             "overall": 5, "round": 1, "team_count": 12, "name": "Agreed",
             "position": "WR", "nfl_team": "CIN"}])
    got = {p["player_key"]: p for p in demo_db.adp(SEASON, min_drafts=6)["players"]}
    assert got["agreed"]["spread"] == 0.0
    assert got["split"]["spread"] > 15
    assert got["split"]["early"] < got["split"]["late"]


# -------------------------------------------------------------- the pricing


def test_a_price_is_a_draft_pick():
    """John's call, and the right one: "costs the 1.04" means something to
    anybody who has drafted a team and "worth 103.9" does not."""
    assert trade_rating.pick_label(1) == "1.01"
    assert trade_rating.pick_label(12) == "1.12"
    assert trade_rating.pick_label(13) == "2.01"
    assert trade_rating.pick_label(27.4) == "3.03"


def test_the_slot_and_value_curves_are_inverses():
    """The page turns a value back into a pick, so the round trip has to
    land where it started or the number printed is not the number held."""
    for decay in (0.5, 1.0, 1.8):
        for shift in (0.0, 8.0, 24.0):
            for slot in (1, 5, 40, 120, 300):
                v = trade_rating.slot_value(slot, decay, shift)
                assert trade_rating.value_slot(v, decay, shift) == pytest.approx(
                    slot, rel=1e-6), (decay, shift, slot)


def test_a_player_nobody_traded_still_has_a_price():
    """The whole reason this model exists. The batch solver needs eight
    trades and the best players in football are traded twice a season, so
    it priced none of the top fifty. Here the draft speaks for them."""
    adp = {"star": 2.0, "mid": 60.0, "deep": 150.0}
    m = trade_rating.Market(adp, decay=1.0, shift=8.0)
    for key in adp:
        m._ensure(key, None)
    assert m.slot("star") < m.slot("mid") < m.slot("deep")
    assert m.row("star")["costs"] == "1.02"


def test_one_trade_moves_an_unknown_player_further_than_a_known_one():
    """Glicko's idea, and the reason the step size is not a tuned
    constant: how far a trade moves somebody is his own uncertainty
    divided by everyone's. A player we barely know should swing; one with
    forty trades behind him should not."""
    # Different slots, or the trade says the two are already equal, the
    # innovation is zero and nothing moves at all.
    adp = {"known": 12.0, "unknown": 60.0}
    m = trade_rating.Market(adp, decay=1.0, shift=8.0)
    for key in adp:
        m._ensure(key, 1)
    m.p["known"] = m.p["unknown"] / 25.0          # we are sure about one

    before = dict(m.v)
    m.observe(["known"], ["unknown"], 1)
    moved_known = abs(m.v["known"] - before["known"])
    moved_unknown = abs(m.v["unknown"] - before["unknown"])
    assert moved_unknown > moved_known * 5, (moved_known, moved_unknown)


def test_certainty_decays_when_nobody_trades_him():
    """No trades is no news, not confirmation. A player untouched since
    August has to become movable again, or the market freezes at a
    preseason opinion and calls it a price."""
    m = trade_rating.Market({"p": 20.0}, decay=1.0, shift=8.0)
    m._ensure("p", 1)
    m.p["p"] *= 0.1                                # pretend he was well traded
    tight = m.p["p"]
    m._drift("p", 1 + 40)
    assert m.p["p"] > tight * 2


def test_a_two_for_one_is_not_two_for_one():
    """The side taking back fewer bodies is buying a roster spot as well
    as a player, so a fair two-for-one gives up more talent than it gets.
    Ignore that and the model pushes every consolidation target up and
    every depth piece down, forever.

    The batch solver could not model this at all — a per-body charge and a
    constant shift were the same parameter there. Anchoring to the draft
    is what makes it measurable."""
    # The pair outweighs the single in raw talent, which is what a fair
    # consolidation trade looks like: the side getting the one best player
    # gives up more, and pockets a roster spot for the difference.
    adp = {"star": 10.0, "a": 20.0, "b": 22.0}
    deals = [{"a": ["star"], "b": ["a", "b"], "day": 1, "key": str(i)}
             for i in range(200)]
    charge = trade_rating.roster_charge(deals, adp, decay=1.0, shift=8.0)
    assert charge > 0, charge

    free = trade_rating.Market(adp, decay=1.0, shift=8.0, spot=0.0)
    paid = trade_rating.Market(adp, decay=1.0, shift=8.0, spot=charge)
    for mk in (free, paid):
        for deal in deals[:40]:
            mk.observe(deal["a"], deal["b"], deal["day"])
    # Unpriced, the spot shows up as talent and the model inflates the
    # consolidation target every time it sees one of these. Priced, the
    # trade balances and he stays where the draft put him.
    assert free.v["star"] > paid.v["star"]
    assert paid.v["star"] == pytest.approx(paid.start["star"], rel=0.05)


def test_a_uniform_sag_is_not_news():
    """Value here is relative and the scale is arbitrary, so a drift that
    moves every player the same way says nothing — but it printed as a
    sentence, and a false one: the first run had the whole top of the
    board down 24 to 45 picks since the draft, every player, no risers."""
    adp = {f"p{i}": float(i + 1) * 5 for i in range(30)}
    m = trade_rating.Market(adp, decay=1.0, shift=8.0)
    for key in adp:
        m._ensure(key, 1)
        m.n[key] = 3
        m.v[key] *= 0.6                          # the whole board sags
    factor = m.normalise()
    assert factor == pytest.approx(0.6, abs=0.01)
    for key in adp:
        assert m.v[key] == pytest.approx(m.start[key], rel=1e-6)


def test_a_band_never_runs_off_the_end_of_the_draft():
    """Slot is a power law in value, so once value minus two sigma nears
    zero the arithmetic runs away — a player with an ordinary 44%
    uncertainty came out with a band reaching pick 3,918 in a draft of
    about 200. Honest, and useless."""
    m = trade_rating.Market({"p": 20.0}, decay=1.0, shift=8.0)
    m._ensure("p", 1)
    m.p["p"] = (2.0 * m.v["p"]) ** 2             # enormous doubt
    dear, cheap = m.band("p")
    assert cheap <= m.undrafted_slot + trade_rating.TEAMS + 1
    assert dear <= cheap


def test_the_price_is_read_by_rank_so_the_dearest_costs_the_first_pick():
    """23 Oct, live: the order was right (Puka, Walker, Bijan, JSN...) but
    the most expensive player in the country "cost the 5.10" and every one
    of the top fifteen was "down" 38 to 64 picks. The bundle arithmetic
    squeezes the top of the value scale; rank cannot be squeezed."""
    adp = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0, "k": 5.0}
    m = trade_rating.Market(adp, decay=1.0, shift=8.0)
    # Everybody traded has sagged to a fraction of his draft value, the
    # way the live board did, but b has passed a.
    for key, share in (("a", 0.30), ("b", 0.40), ("c", 0.25), ("d", 0.20)):
        m._ensure(key, 0)
        m.v[key] = m.start[key] * share
        m.n[key] = 5
    r = m.ranked(frozenset({"k"}))
    assert "k" not in r                                   # kickers out of the ranking
    assert r["b"] == 1.0 and r["a"] == 2.0                # the dearest costs the 1st pick
    assert m.row("b", now=r["b"])["moved"] == 1.0         # up one: he passed a
    assert m.row("a", now=r["a"])["moved"] == -1.0
    assert m.slot("b") > 2.0                              # ...where the curve said "down"


def test_a_player_nobody_trades_is_not_ranked_above_the_market():
    """First version, live 23 Oct: untraded players held their draft spots,
    and Puka, the dearest player the market had priced, "cost the 3.02"
    with twenty-six invisible players ahead of him. A player with no
    trades has a draft price, not a market price: he is not ranked."""
    adp = {"star": 1.0, "b": 2.0, "c": 3.0}
    m = trade_rating.Market(adp, decay=1.0, shift=8.0)
    for key in ("b", "c"):
        m._ensure(key, 0)
        m.v[key] = m.start[key] * 0.3          # squeezed, as live
        m.n[key] = 3
    r = m.ranked()
    assert "star" not in r
    assert r["b"] == 2.0                       # the earliest a TRADED player went
