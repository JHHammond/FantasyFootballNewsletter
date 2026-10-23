"""One player's page in the trade market (John, 23 Oct 2026).

Everything the page shows about a player, gathered in one place so the
route stays a few lines and this can be tested without a server:

  * who he is, with his photo, and what he costs as a place in the market;
  * his price, day by day since the draft (the same line as the market chart);
  * his weekly usage, from Around the Leagues: how many teams started him,
    what he scored, and how often those teams won;
  * who he is traded for most;
  * every trade of him this season, newest first (migration 056);
  * for a paying viewer, the trades where one of HIS players was on the
    other side: "what my guys have fetched for him".

PRIVACY. Trades come from player_trades (056), which returns players, pick
labels and FAAB amounts, and a league only as its shape. The viewer's own
roster is read only for leagues on his account, and only its player keys
leave this module.

Never raises for a missing piece: a page with no usage, or no trades yet,
is still a page.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

#: Trades shown before "show more".
TRADES_FIRST = 25

PROVIDER = {"sleeper": "Sleeper", "espn": "ESPN", "yahoo": "Yahoo"}
SCORING = {"ppr": "PPR", "half_ppr": "half-PPR", "std": "standard"}


def league_shape(t: dict) -> str:
    """'12-team PPR · Sleeper', from what a trade knows about its league."""
    bits = []
    if t.get("team_count"):
        bits.append(f"{int(t['team_count'])}-team")
    if t.get("scoring_type") in SCORING:
        bits.append(SCORING[t["scoring_type"]])
    shape = " ".join(bits) or "A league"
    plat = PROVIDER.get((t.get("provider") or "").lower())
    return f"{shape} · {plat}" if plat else shape


def _side(players: list[dict], picks: list, faab) -> list[dict]:
    """One side of a deal as display items: players first, then picks, then
    FAAB. Each item is {kind, label, key?, position?}."""
    out = [{"kind": "player", "label": p.get("name") or p.get("player_key"),
            "key": p.get("player_key"), "position": p.get("position")}
           for p in players or []]
    out += [{"kind": "pick", "label": str(lbl)} for lbl in picks or [] if lbl]
    try:
        amt = float(faab or 0)
    except (TypeError, ValueError):
        amt = 0.0
    if amt > 0:
        out.append({"kind": "faab", "label": f"${amt:,.0f} FAAB"})
    return out


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def nice_day(day) -> Optional[str]:
    """'2026-10-22' -> 'Oct 22'. Anything else comes back as it was."""
    try:
        y, m, d = (int(x) for x in str(day)[:10].split("-"))
        return f"{MONTHS[m - 1]} {d}"
    except (TypeError, ValueError, IndexError):
        return day or None


def shape_trades(raw: list[dict]) -> list[dict]:
    """player_trades rows -> what the template draws."""
    out = []
    for t in raw or []:
        out.append({
            "key": t.get("key"),
            "day": nice_day(t.get("day")),
            "date": str(t.get("day") or "")[:10] or None,
            "week": t.get("week"),
            "league": league_shape(t),
            "three_way": int(t.get("teams") or 2) > 2,
            "got": _side(t.get("with"), t.get("got_picks"), t.get("got_faab")),
            "gave": _side(t.get("gave"), t.get("gave_picks"), t.get("gave_faab")),
            "gave_keys": [p.get("player_key") for p in t.get("gave") or []],
            "with_keys": [p.get("player_key") for p in t.get("with") or []],
            # Straight up: one player each way, nothing else in the deal.
            "straight": (len(t.get("gave") or []) == 1 and not t.get("with")
                         and not t.get("got_picks") and not t.get("gave_picks")
                         and not float(t.get("got_faab") or 0) and not float(t.get("gave_faab") or 0)),
        })
    return out


def recent_cut(trades: list[dict], days: int = 14) -> Optional[str]:
    """The date that splits "lately" from "earlier": two weeks before his
    latest trade."""
    from datetime import date, timedelta
    dates = [t["date"] for t in trades if t.get("date")]
    if not dates:
        return None
    last = date.fromisoformat(max(dates))
    return (last - timedelta(days=days)).isoformat()


def traded_for(trades: list[dict], prices: Optional[dict] = None,
               limit: int = 12) -> list[dict]:
    """The players he went for most often: everyone on the other side,
    counted once per deal, split into straight swaps and packages, and into
    the last two weeks against before. `prices` is the market's player list
    by key, for what each of them costs today."""
    prices = prices or {}
    cut = recent_cut(trades)
    tally: dict[str, dict] = {}
    for t in trades:
        lately = bool(cut and t.get("date") and t["date"] > cut)
        for item in t["gave"]:
            if item["kind"] != "player" or not item.get("key"):
                continue
            e = tally.setdefault(item["key"], {"key": item["key"], "name": item["label"],
                                               "position": item.get("position"), "n": 0,
                                               "straight": 0, "package": 0, "recent": 0})
            e["n"] += 1
            e["straight" if t.get("straight") else "package"] += 1
            e["recent"] += lately
    out = sorted(tally.values(), key=lambda e: (-e["n"], e["name"] or ""))[:limit]
    for e in out:
        p = prices.get(e["key"]) or {}
        e.update(costs=p.get("costs"), rank=p.get("rank"), photo=p.get("photo"))
    return out


def lately(trades: list[dict], limit: int = 3) -> list[dict]:
    """Who he has gone for in the last two weeks, most often first."""
    cut = recent_cut(trades)
    if not cut:
        return []
    recent = [t for t in trades if t.get("date") and t["date"] > cut]
    return traded_for(recent, limit=limit)


POS_WORD = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "kicker", "DEF": "defence"}


def summary_lines(trades: list[dict], key: str, prices: Optional[dict] = None) -> list[str]:
    """Two or three sentences about how he trades, the kind people repeat."""
    prices = prices or {}
    if len(trades) < 3:
        return []
    out = []
    # What he usually goes for: how many players, and which position.
    sizes: dict[int, int] = {}
    pos: dict[str, int] = {}
    for t in trades:
        players = [i for i in t["gave"] if i["kind"] == "player"]
        sizes[len(players)] = sizes.get(len(players), 0) + 1
        for i in players:
            if i.get("position"):
                pos[i["position"]] = pos.get(i["position"], 0) + 1
    n_size, _ = max(sizes.items(), key=lambda kv: (kv[1], -kv[0]))
    straight = sum(1 for t in trades if t.get("straight"))
    top_pos = max(pos.items(), key=lambda kv: kv[1])[0] if pos else None
    word = {0: "picks or FAAB alone", 1: "one player", 2: "two players"}.get(n_size, f"{n_size} players")
    line = f"Usually goes for {word}"
    if n_size == 1 and top_pos:
        line = f"Usually goes for one {POS_WORD.get(top_pos, top_pos)}"
    line += f", straight up in {round(100 * straight / len(trades))}% of deals."
    out.append(line)
    # Packaged: is he the best player in the deal?
    mine = prices.get(key) or {}
    if mine.get("rank"):
        packed = [t for t in trades if not t.get("straight")]
        judged = best = 0
        for t in packed:
            others = [prices.get(k) for k in t["gave_keys"] + t["with_keys"]]
            ranks = [o["rank"] for o in others if o and o.get("rank")]
            if ranks:
                judged += 1
                best += mine["rank"] < min(ranks)
        if judged >= 3:
            pct = round(100 * best / judged)
            if pct >= 50:
                out.append(f"When he\u2019s packaged, he\u2019s the most expensive player "
                           f"in the deal {pct}% of the time.")
            else:
                out.append(f"When he\u2019s packaged, there\u2019s usually someone pricier in "
                           f"the deal: {100 - pct}% of the time.")
    # The positions he's traded for.
    total = sum(pos.values())
    if total:
        mix = sorted(pos.items(), key=lambda kv: -kv[1])[:2]
        out.append("Traded for: " + ", ".join(
            f"{POS_WORD.get(p, p)}s {round(100 * n / total)}%" for p, n in mix) + ".")
    return out


def trend(chart_data: Optional[dict]) -> Optional[dict]:
    """Buy now or wait: how far his place has moved in the last seven days.
    Up (a smaller place) means he's getting more expensive."""
    if not chart_data:
        return None
    line = chart_data["line"]
    now = line[-1]
    then = None
    for pt in line:
        if pt[0] <= now[0] - 7:
            then = pt
    if then is None:
        return None
    moved = round(then[1] - now[1])
    if moved >= 2:
        say, tone = "Move now: he\u2019s getting more expensive.", "up"
    elif moved <= -2:
        say, tone = "Wait: he\u2019s getting cheaper.", "down"
    else:
        say, tone = "Holding steady: no rush either way.", "flat"
    return {"moved": moved, "say": say, "tone": tone}


def usage(week_json: Callable[[int], dict], key: str, weeks: list[int]) -> list[dict]:
    """His line in Around the Leagues for each week: teams that had him, how
    many started him, his PPR score and those teams' win rate. A week he
    isn't in (a bye, or outside the 400 most rostered) is left out."""
    out = []
    for wk in weeks:
        try:
            players = ((week_json(wk) or {}).get("players") or {}).get("all") or []
        except Exception:  # noqa: BLE001
            continue
        row = next((p for p in players if p.get("player_key") == key), None)
        if not row:
            continue
        out.append({"week": wk, "rostered": row.get("rostered") or 0,
                    "started": row.get("started") or 0, "start_pct": row.get("start_pct"),
                    "ppr": row.get("ppr"), "win_pct": row.get("win_pct"),
                    "did_play": row.get("did_play")})
    return out


def your_trades(trades: list[dict], roster: set[str]) -> list[dict]:
    """Trades of him where one of the viewer's players went the other way,
    each marked with which of them."""
    out = []
    for t in trades:
        hit = [k for k in t["gave_keys"] if k in roster]
        if hit:
            out.append(dict(t, yours=set(hit)))
    return out


def summary(key: str, rating: Optional[dict], names: dict) -> dict:
    """Who he is and what he costs, from the market's cached build."""
    pick = next((p for p in (rating or {}).get("pickable") or [] if p["k"] == key), None)
    meta = names.get(key) or {}
    s = {"key": key, "name": (pick or {}).get("name") or meta.get("name") or key,
         "position": (pick or {}).get("pos") or meta.get("position"),
         "team": (pick or {}).get("team") or meta.get("nfl_team"),
         "photo": (pick or {}).get("photo"), "priced": pick is not None}
    if pick:
        s.update(costs=pick["costs"], rank=pick["rank"], drafted=pick.get("drafted"),
                 trades=pick.get("trades"), firm=pick.get("firm"),
                 moved=(pick["dp"] - pick["rank"]) if pick.get("dp") else None)
    return s


def chart(key: str, rating: Optional[dict]) -> Optional[dict]:
    """His daily line, on the market chart's scale."""
    daily = (rating or {}).get("daily") or {}
    line = (daily.get("lines") or {}).get(key)
    if not line or not daily.get("base"):
        return None
    market = [pt for pt in line if pt[0] > 0] or line
    best = min(market, key=lambda pt: pt[1])
    worst = max(market, key=lambda pt: pt[1])
    return {"base": daily["base"], "last": daily.get("last") or line[-1][0],
            "line": line, "best": best, "worst": worst}


# ---------------------------------------------------------------------------
# Get him / Sell him (paid). The viewer's own league, so team names are fine.
# ---------------------------------------------------------------------------

#: The starting lineup a team is judged on when we say what it is short at.
NEEDS = (("QB", 1), ("RB", 2), ("WR", 3), ("TE", 1))
#: How close a package has to be to his value to be called fair.
FAIR = 0.15
#: Offers shown.
OFFERS = 5


def team_needs(rosters: dict[str, set], prices: dict) -> dict[str, dict]:
    """Per team: its starting strength at each position (the sum of its best
    N players' values) against the league's median, and the position it's
    furthest behind at."""
    import statistics
    strength: dict[str, dict] = {}
    for tid, keys in rosters.items():
        by_pos: dict[str, list] = {}
        for k in keys:
            p = prices.get(k)
            if p and p.get("v") and p.get("pos") in dict(NEEDS):
                by_pos.setdefault(p["pos"], []).append(p["v"])
        strength[tid] = {pos: sum(sorted(by_pos.get(pos, []), reverse=True)[:n]) for pos, n in NEEDS}
    med = {pos: statistics.median([s[pos] for s in strength.values()]) or 1e-9
           for pos, _ in NEEDS} if strength else {}
    out = {}
    for tid, s in strength.items():
        gap = {pos: s[pos] / med[pos] for pos, _ in NEEDS}
        short = min(gap, key=gap.get)
        out[tid] = {"gap": gap, "short": short if gap[short] < 0.85 else None}
    return out


def proof(trades: list[dict], offer: list[str]) -> dict:
    """How often this exact package went for him alone, and how often each
    piece of it went the other way in any deal."""
    want = set(offer)
    exact = sum(1 for t in trades if set(t["gave_keys"]) == want and not t["with_keys"])
    each = {k: sum(1 for t in trades if k in t["gave_keys"]) for k in offer}
    return {"exact": exact, "each": each}


def _fit(value: float, him: float) -> float:
    return abs(value / him - 1.0)


def offers(him_key: str, give_from: set, prices: dict, spot: float,
           trades: list[dict], prefer_pos: Optional[str] = None) -> list[dict]:
    """Fair packages for him from `give_from`: one player, or two whose
    combined value, less the roster spot the other side has to make, is
    about his. A player worth more than him on his own is never padded with
    a second. Best fit first, a nudge for the position the other side needs,
    and real deals ahead of hypothetical ones."""
    him = (prices.get(him_key) or {}).get("v")
    if not him:
        return []
    pool = [k for k in give_from if k != him_key and (prices.get(k) or {}).get("v")]
    found = []
    for k in pool:
        v = prices[k]["v"]
        if _fit(v, him) <= FAIR:
            found.append(([k], v))
    small = [k for k in pool if prices[k]["v"] < him * (1 - FAIR)]
    for i, a in enumerate(small):
        for b in small[i + 1:]:
            v = prices[a]["v"] + prices[b]["v"] - spot
            if _fit(v, him) <= FAIR:
                found.append(([a, b], v))
    out = []
    for keys, v in found:
        pr = proof(trades, keys)
        need = bool(prefer_pos and any(prices[k].get("pos") == prefer_pos for k in keys))
        # Lower is better. Closeness to his value first; then what makes a
        # deal likelier to happen: it has happened before (the proof), it is
        # one for one (no roster spot to argue about), it fills their need.
        score = (_fit(v, him)
                 - min(0.15, 0.03 * pr["exact"])
                 - min(0.05, 0.01 * sum(pr["each"].values()))
                 - (0.04 if len(keys) == 1 else 0)
                 - (0.05 if need else 0))
        keys = sorted(keys, key=lambda k: -prices[k]["v"])
        out.append({"keys": keys, "players": [prices[k] for k in keys],
                    "ratio": round(v / him, 3), "fills_need": need,
                    "exact": pr["exact"], "each": [pr["each"][k] for k in keys], "score": score})
    out.sort(key=lambda o: o["score"])
    # Variety: one package per lead player, and nobody in more than two
    # offers, so five offers aren't five ways of handing over the same man.
    lead, uses, kept = set(), {}, []
    for o in out:
        if o["keys"][0] in lead or any(uses.get(k, 0) >= 2 for k in o["keys"]):
            continue
        lead.add(o["keys"][0])
        for k in o["keys"]:
            uses[k] = uses.get(k, 0) + 1
        kept.append(o)
    return kept[:OFFERS]


def deal_desk(him_key: str, league: dict, rosters: dict[str, set], my_team: str,
              prices: dict, spot: float, trades: list[dict]) -> dict:
    """Everything "Get him" or "Sell him" shows, for one of the viewer's
    leagues. `league` is {id, league, teams: [{id, name, mine}]}."""
    names = {str(t["id"]): t["name"] for t in league["teams"]}
    owner = next((tid for tid, keys in rosters.items() if him_key in keys), None)
    needs = team_needs(rosters, prices)
    pos = (prices.get(him_key) or {}).get("pos")
    if owner and str(owner) == str(my_team):
        # SELL: what each leaguemate could give, teams that need him first.
        rows = []
        for tid, keys in rosters.items():
            if str(tid) == str(my_team):
                continue
            got = offers(him_key, keys, prices, spot, trades)
            if not got:
                continue
            short = (needs.get(tid) or {}).get("short")
            rows.append({"team": names.get(str(tid), "A team"), "needs": short,
                         "wants_him": short == pos, "offers": got[:2]})
        rows.sort(key=lambda r: (not r["wants_him"], r["offers"][0]["score"]))
        flat = [dict(o, team=r["team"], wants_him=r["wants_him"]) for r in rows for o in r["offers"]]
        return {"mode": "sell", "owner": names.get(str(owner)), "needs_him": [
            r["team"] for r in rows if r["wants_him"]], "offers": flat[:OFFERS]}
    owner_need = (needs.get(owner) or {}).get("short") if owner else None
    mine = rosters.get(my_team) or set()
    return {"mode": "get", "owner": names.get(str(owner)) if owner else None,
            "owner_needs": owner_need,
            "offers": offers(him_key, mine, prices, spot, trades, prefer_pos=owner_need)}


# How far apart the two sides can be, by today's prices, and still be "fair".
EVEN = 0.10


def dress_trades(trades: list[dict], prices: dict, him_key: str, spot: float,
                 photo: Optional[Callable] = None) -> list[dict]:
    """Add what the trade list draws beyond names: a photo and today's
    price on every player, and a verdict from the side that got him.

    The verdict values each side at today's prices, less a roster spot for
    every player past the first (the same sum the offers use). It's left
    out when a pick or FAAB is in the deal, or anybody in it is unpriced,
    because then half the deal can't be weighed."""
    him = prices.get(him_key)
    for t in trades:
        for item in t["got"] + t["gave"]:
            if item["kind"] != "player":
                continue
            p = prices.get(item.get("key")) or {}
            item["costs"] = p.get("costs")
            item["photo"] = p.get("photo") or (photo(item.get("key"), item.get("position"))
                                               if photo and item.get("key") else None)
        t["picks"] = any(i["kind"] != "player" for i in t["got"] + t["gave"])
        t["verdict"] = None
        if not him or t["picks"]:
            continue
        got = [him] + [prices.get(i.get("key")) for i in t["got"]]
        gave = [prices.get(i.get("key")) for i in t["gave"]]
        if not gave or any(p is None for p in got + gave):
            continue
        gv = sum(p["v"] for p in got) - spot * (len(got) - 1)
        pv = sum(p["v"] for p in gave) - spot * (len(gave) - 1)
        if gv <= 0 or pv <= 0:
            continue
        edge = gv / pv - 1.0
        word = "fair" if abs(edge) < EVEN else ("won" if edge > 0 else "paid")
        t["verdict"] = {"word": word, "edge": round(100 * edge),
                        "share": round(100 * gv / (gv + pv))}
    return trades


#: Players the chart can hold beside him.
COMPARE_MAX = 5


def compare_picks(him_key: str, prices: dict, traded_for_rows: list[dict],
                  limit: int = 5) -> list[dict]:
    """Who you'd most likely want on his chart: the players he is traded
    for most, then the next player at his position either side of him in
    the market."""
    out, seen = [], {him_key}

    def add(k, why):
        p = prices.get(k)
        if p and k not in seen and len(out) < limit:
            seen.add(k)
            out.append({"k": k, "name": p.get("name"), "pos": p.get("pos"),
                        "costs": p.get("costs"), "why": why})

    him = prices.get(him_key) or {}
    if him.get("pos") and him.get("rank"):
        same = sorted((p for p in prices.values() if p.get("pos") == him["pos"] and p.get("rank")),
                      key=lambda p: p["rank"])
        up = [p for p in same if p["rank"] < him["rank"]]
        down = [p for p in same if p["rank"] > him["rank"]]
        if up:
            add(up[-1]["k"], f"next {him['pos']} up")
        if down:
            add(down[0]["k"], f"next {him['pos']} down")
    for e in traded_for_rows:
        add(e["key"], f"traded for him {e['n']}\u00d7")
    # The traded-for ones read better first; the neighbours close the row.
    return sorted(out, key=lambda o: 0 if o["why"].startswith("traded") else 1)


def compare_players(prices: dict) -> list[dict]:
    """The search list for "add a player": everyone priced, most expensive
    first, with only what the search and the legend draw."""
    return [{"k": p["k"], "name": p.get("name"), "pos": p.get("pos"), "team": p.get("team"),
             "costs": p.get("costs"), "rank": p.get("rank"), "photo": p.get("photo")}
            for p in sorted(prices.values(), key=lambda p: p.get("rank") or 9999)]
