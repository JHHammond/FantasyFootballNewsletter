"""What each player is actually being traded for, split dynasty / redraft.

    python -m scripts.trade_check                    # both boards
    python -m scripts.trade_check --player Bijan     # plus every Bijan trade
    python -m scripts.trade_check --refresh          # re-read the database
    python -m scripts.trade_check --no-model         # skip the side-by-side

John's idea, 22 Oct, written down as plainly as it can be: every player
has a number from where the country drafted him. A trade says the two
sides were worth about the same. So if Bijan (drafted 1.03) goes for
Parker Washington (3.05) and Christian Watson (4.04), the trade says
Bijan went for what those two were drafted at, which is worth the 1.12.
Do that for every trade a player was in, take the middle, and you have
what the field actually pays for him. The gap between that and his ADP
is who moved.

No tuning, no filter, nothing to trust but arithmetic — which is the
point. It is a CHECK on the model in web/trade_rating.py: if this puts
the players everybody knows are good at the top and the model does not,
the model is wrong.

HOW A TRADE IS READ (pro rata). Each player is credited with the other
side's draft value in proportion to his share of his own side:

    implied(p) = value(other side) * value(p) / value(his side)

One-for-one, that is just the other player. In a two-for-one it splits
the star's value across the two in proportion to their draft prices, and
credits the star with both of them. Never negative, and the two sides of
a trade always reconcile.

DYNASTY AND REDRAFT ARE DIFFERENT MARKETS. A rookie in dynasty is priced
on his career and in redraft on his next eight weeks. Superflex is folded
into whichever of the two its league is. A league is classified by the
first of these that says anything:

  1. Sleeper's own setting (league.settings.type: 0 redraft, 1 keeper,
     2 dynasty) — fetched from Sleeper's public API, cached.
  2. What the commissioner chose in setup, IF it is not the default
     (format = dynasty or keeper; "redraft" is the default and so says
     nothing on its own).
  3. The shape of this season's draft. A dynasty league's 2026 draft is
     a rookie draft: a handful of picks a team. Ten or more picks a team
     is a real draft.
  4. A traded draft pick for a future season, which redraft leagues do
     not have.

Keeper counts as redraft (it is priced on this season), and is reported
separately so that can be argued with. Anything still unclassified is
left out of both boards and counted.

THE YARDSTICK IS REDRAFT ADP, for both boards. Our stored ADP averages
every draft, including dynasty rookie drafts, where this year's rookies
go at picks 1 to 12 — so every rookie looks like a first-rounder. Here it
is rebuilt from redraft drafts only (keepers excluded too, since their
best players were never drafted). The dynasty board is read on the same
scale, so a dynasty "riser" means "dynasty pays more for him than redraft
drafted him", which is exactly the comparison worth seeing.

Reads, never writes. Needs .env with the Supabase service key.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.trade_rating import pick_label, slot_value, value_slot  # noqa: E402

CACHE = ROOT / ".trade_check_cache.json"

#: Picks a team in this season's draft. At or under ROOKIE, the draft is a
#: rookie draft and the league is dynasty; at or over FULL it is a real
#: draft. Between is left to the other signals.
ROOKIE_PICKS_PER_TEAM = 6
FULL_PICKS_PER_TEAM = 10

#: A player needs this many drafts in redraft leagues to have an ADP.
MIN_DRAFTS = 20
#: ...and this many trades to be on a board.
MIN_TRADES = 3
#: Risers and fallers need a little more, so one odd deal can't lead.
MIN_TRADES_MOVERS = 5

WATCH = ["Jahmyr Gibbs", "Bijan Robinson", "Kenneth Walker",
         "Puka Nacua", "Jaxon Smith-Njigba"]

SLEEPER_TYPE = {0: "redraft", 1: "keeper", 2: "dynasty"}


# ---------------------------------------------------------------- loading

def _page(table: str, cols: str, season: Optional[int], order: list[str],
          label: str) -> list[dict]:
    from web import db
    out: list[dict] = []
    start, size = 0, 1000
    while True:
        q = db.client().table(table).select(cols)
        if season is not None:
            q = q.eq("season", int(season))
        for col in order:
            q = q.order(col)
        rows = q.range(start, start + size - 1).execute().data or []
        out.extend(rows)
        print(f"\r  {label}: {len(out):,}", end="", flush=True)
        if len(rows) < size:
            print()
            return out
        start += size


def _sleeper_types(league_ids: list[str]) -> dict[str, dict]:
    """settings.type and whether it is superflex, per Sleeper league id."""
    def one(pid: str):
        url = f"https://api.sleeper.app/v1/league/{pid}"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=15) as r:
                    d = json.loads(r.read().decode() or "null") or {}
                st = (d.get("settings") or {}).get("type")
                sf = "SUPER_FLEX" in (d.get("roster_positions") or [])
                return pid, {"type": st, "superflex": sf}
            except Exception:  # noqa: BLE001
                time.sleep(1 + attempt)
        return pid, None

    out: dict[str, dict] = {}
    done = 0
    # Four at a time stays well inside Sleeper's published limit.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for pid, got in pool.map(one, league_ids):
            done += 1
            if got is not None:
                out[pid] = got
            if done % 50 == 0 or done == len(league_ids):
                print(f"\r  sleeper settings: {done:,}/{len(league_ids):,}", end="", flush=True)
    print()
    return out


def load(season: int, refresh: bool, sleeper: bool) -> dict[str, Any]:
    """Everything, from the cache when there is one."""
    if CACHE.exists() and not refresh:
        data = json.loads(CACHE.read_text())
        if data.get("season") == season:
            print(f"Using {CACHE.name} from {data.get('at')} (--refresh to re-read).")
            if sleeper and not data.get("sleeper"):
                data["sleeper"] = _fetch_sleeper(data["leagues"])
                CACHE.write_text(json.dumps(data))
            return data

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    print("Reading the database (a minute or two, then cached):")
    data = {
        "season": season,
        "at": time.strftime("%Y-%m-%d %H:%M"),
        "leagues": _page("leagues", "id,provider,platform_league_id,format", None,
                         ["id"], "leagues"),
        "trades": _page("trades", "league_id,trade_key,week,traded_at,picks,faab",
                        season, ["league_id", "trade_key"], "trades"),
        "trade_players": _page("trade_players",
                               "league_id,trade_key,player_key,name,position,to_team",
                               season, ["league_id", "trade_key", "player_key"],
                               "traded players"),
        "drafts": _page("draft_picks",
                        "league_id,player_key,overall,round,team_count,name,position",
                        season, ["league_id", "player_key"], "draft picks"),
    }
    data["sleeper"] = _fetch_sleeper(data["leagues"]) if sleeper else {}
    CACHE.write_text(json.dumps(data))
    return data


def _fetch_sleeper(leagues: list[dict]) -> dict[str, dict]:
    ids = sorted({str(l["platform_league_id"]) for l in leagues
                  if l.get("provider") == "sleeper" and l.get("platform_league_id")})
    if not ids:
        return {}
    print(f"Asking Sleeper what kind of league each of {len(ids):,} is:")
    return _sleeper_types(ids)


# ----------------------------------------------------------- classifying

_YEAR = re.compile(r"\b(20\d\d)\b")


def draft_shapes(drafts: Iterable[dict]) -> dict[str, float]:
    """Picks per team in each league's draft this season."""
    count: dict[str, int] = {}
    teams: dict[str, int] = {}
    for d in drafts:
        lid = str(d["league_id"])
        count[lid] = count.get(lid, 0) + 1
        if d.get("team_count"):
            teams[lid] = int(d["team_count"])
    return {lid: n / max(1, teams.get(lid) or 12) for lid, n in count.items()}


def future_pick_leagues(trades: Iterable[dict], season: int) -> set[str]:
    out = set()
    for t in trades:
        for p in t.get("picks") or []:
            label = p[1] if isinstance(p, (list, tuple)) and len(p) > 1 else p
            m = _YEAR.search(str(label or ""))
            if m and int(m.group(1)) > int(season):
                out.add(str(t["league_id"]))
                break
    return out


def classify(league: dict, sleeper: dict[str, dict], shapes: dict[str, float],
             future: set[str]) -> tuple[str, str, Optional[bool]]:
    """(kind, what decided it, superflex or None). kind is dynasty, keeper,
    redraft or unknown."""
    lid = str(league["id"])
    sf = None
    if league.get("provider") == "sleeper":
        got = sleeper.get(str(league.get("platform_league_id") or ""))
        if got:
            sf = got.get("superflex")
            kind = SLEEPER_TYPE.get(got.get("type"))
            if kind:
                return kind, "sleeper setting", sf
    fmt = (league.get("format") or "").strip().lower()
    if fmt in ("dynasty", "keeper"):
        return fmt, "commissioner said", sf
    per_team = shapes.get(lid)
    if per_team is not None:
        if per_team <= ROOKIE_PICKS_PER_TEAM:
            return "dynasty", "rookie-sized draft", sf
        if per_team >= FULL_PICKS_PER_TEAM:
            return "redraft", "full draft", sf
    if lid in future:
        return "dynasty", "traded a future pick", sf
    return "unknown", "nothing to go on", sf


def bucket_of(kind: str) -> Optional[str]:
    return {"dynasty": "dynasty", "keeper": "redraft", "redraft": "redraft"}.get(kind)


# ------------------------------------------------------------------- ADP

def build_adp(drafts: Iterable[dict], kinds: dict[str, str],
              allowed: set[str], min_drafts: int = MIN_DRAFTS) -> dict[str, dict]:
    """ADP from the drafts of leagues whose kind is in `allowed`, normalised
    to twelve teams the same way migration 050 does."""
    slots: dict[str, list[float]] = {}
    names: dict[str, tuple] = {}
    for d in drafts:
        if kinds.get(str(d["league_id"])) not in allowed:
            continue
        overall = int(d.get("overall") or 0)
        if overall <= 0:
            continue
        tc = int(d.get("team_count") or 12) or 12
        slots.setdefault(d["player_key"], []).append((overall - 1) * 12.0 / tc + 1)
        names.setdefault(d["player_key"], (d.get("name"), d.get("position")))
    return {k: {"adp": sum(v) / len(v), "drafts": len(v),
                "name": names[k][0], "position": names[k][1]}
            for k, v in slots.items() if len(v) >= min_drafts}


# ------------------------------------------------------------ the reading

def sides_of(trades: list[dict], players: list[dict]) -> list[dict]:
    """Two-sided trades as {league_id, week, day, a, b, picks}. a and b are
    lists of player keys; picks is True when picks or FAAB changed hands."""
    by: dict[tuple, dict[str, list]] = {}
    for p in players:
        if not p.get("to_team"):
            continue
        by.setdefault((str(p["league_id"]), p["trade_key"]), {}) \
          .setdefault(str(p["to_team"]), []).append(p["player_key"])
    out = []
    for t in trades:
        k = (str(t["league_id"]), t["trade_key"])
        teams = by.get(k) or {}
        # Picks first: players-for-a-pick has players going one way only,
        # and was being miscounted as a three-way.
        if t.get("picks") or t.get("faab"):
            out.append({"league_id": k[0], "week": t.get("week"), "skip": "with picks or FAAB"})
            continue
        if len(teams) != 2:
            why = "three-way or more" if len(teams) > 2 else "players going one way only"
            out.append({"league_id": k[0], "week": t.get("week"), "skip": why})
            continue
        a, b = (sorted(teams[x]) for x in sorted(teams))
        out.append({"league_id": k[0], "week": t.get("week"),
                    "day": str(t.get("traded_at") or "")[:10] or None,
                    "a": a, "b": b, "key": "|".join(k),
                    "picks": bool(t.get("picks") or t.get("faab"))})
    return out


def read_trade(a: list[str], b: list[str], adp: dict[str, dict],
               undrafted_slot: float) -> list[dict]:
    """What each player in one trade was implicitly sold for.

    Undrafted players count at `undrafted_slot` (just past the last
    drafted player): worth very little, which is what the draft said."""
    def val(k):
        return slot_value(adp[k]["adp"] if k in adp else undrafted_slot)

    va = sum(val(k) for k in a)
    vb = sum(val(k) for k in b)
    out = []
    for side, other, vs, vo in ((a, b, va, vb), (b, a, vb, va)):
        for k in side:
            out.append({
                "player_key": k,
                "implied": vo * val(k) / vs if vs > 0 else 0.0,
                # How this deal was shaped from HIS side: "1-for-2" means he
                # went alone and came back as two.
                "shape": f"{len(side)}-for-{len(other)}",
                "alone": len(side) == 1,
                "got": list(other),
                "with": [x for x in side if x != k],
            })
    return out


def board(deals: list[dict], adp: dict[str, dict], undrafted_slot: float,
          min_trades: int = MIN_TRADES) -> dict[str, dict]:
    """Per player: his trades, read, and their middle."""
    per: dict[str, list[dict]] = {}
    for d in deals:
        for r in read_trade(d["a"], d["b"], adp, undrafted_slot):
            r["day"], r["week"] = d.get("day"), d.get("week")
            per.setdefault(r["player_key"], []).append(r)
    out = {}
    for k, rows in per.items():
        if k not in adp or len(rows) < min_trades:
            continue
        vals = [r["implied"] for r in rows]
        alone = [r["implied"] for r in rows if r["alone"] and r["shape"] != "1-for-1"]
        one = [r["implied"] for r in rows if r["shape"] == "1-for-1"]
        mid = statistics.median(vals)
        out[k] = {
            "player_key": k, "name": adp[k]["name"], "position": adp[k]["position"],
            "adp": adp[k]["adp"], "trades": len(rows),
            "implied_value": mid, "implied_slot": value_slot(mid),
            # log of (what he is traded for / what he was drafted at).
            # Positive is a riser. Logs, so a 2.01 who goes for a 1.01 and
            # a 10.01 who goes for a 5.01 count as the same size of move.
            "move": math.log(max(mid, 1e-9) / slot_value(adp[k]["adp"])),
            "one_for_one": (statistics.median(one), len(one)) if one else None,
            "consolidated": (statistics.median(alone), len(alone)) if alone else None,
            "rows": rows,
        }
    return out


# ------------------------------------------------------------- printing

def _name(adp, k):
    e = adp.get(k)
    return (e or {}).get("name") or k


def _pk(slot: float) -> str:
    # Past the end of any real draft: worth less than the last pick taken.
    return pick_label(slot) if slot < 300 else "past draft"


def _row(i, r, model=None):
    m = ""
    if model is not None:
        ms = model.get(r["player_key"])
        m = f"  model {_pk(ms):>9}" if ms is not None else "  model         —"
    return (f"  {i:>3}. {(r['name'] or '?')[:24]:<24} {(r['position'] or ''):<3} "
            f"drafted {_pk(r['adp']):>6}   traded as {_pk(r['implied_slot']):>9}"
            f"   {r['trades']:>4} trades{m}")


def run_model(deals: list[dict], adp: dict[str, dict], season: int) -> dict[str, float]:
    from web import trade_market, trade_rating
    ex = [{"a": d["a"], "b": d["b"], "d": d.get("day"), "w": d.get("week"),
           "key": d["key"]} for d in deals]
    ex = trade_market.days_for(ex, season)
    got = trade_rating.run(ex, {k: v["adp"] for k, v in adp.items()})
    m = got["market"]
    m.normalise()
    return {k: m.slot(k) for k in m.v if m.n.get(k, 0) > 0}


def report(title: str, deals: list[dict], skipped: dict[str, int], adp: dict,
           undrafted_slot: float, season: int, watch: list[str],
           show: list[str], with_model: bool,
           names: Optional[dict[str, str]] = None) -> None:
    print("\n" + "=" * 78)
    print(f"{title.upper()}: {len(deals):,} player-for-player trades read")
    for why, n in sorted(skipped.items(), key=lambda kv: -kv[1]):
        print(f"    left out: {n:,} {why}")
    if not deals:
        return
    if not adp:
        print("  No ADP to price against — the drafts haven't been collected.")
    b = board(deals, adp, undrafted_slot)
    model = None
    if with_model:
        print("  running the model on the same trades (a minute)…")
        try:
            model = run_model(deals, adp, season)
        except Exception as exc:  # noqa: BLE001
            print(f"  model failed: {type(exc).__name__}: {exc}")

    top = sorted(b.values(), key=lambda r: -r["implied_value"])[:25]
    print(f"\n  What the field pays, top 25 ({MIN_TRADES}+ trades):")
    for i, r in enumerate(top, 1):
        print(_row(i, r, model))

    movers = [r for r in b.values() if r["trades"] >= MIN_TRADES_MOVERS]
    print(f"\n  Biggest risers ({MIN_TRADES_MOVERS}+ trades) — traded for more than he was drafted at:")
    for i, r in enumerate(sorted(movers, key=lambda r: -r["move"])[:15], 1):
        print(_row(i, r, model))
    print(f"\n  Biggest fallers ({MIN_TRADES_MOVERS}+ trades):")
    for i, r in enumerate(sorted(movers, key=lambda r: r["move"])[:15], 1):
        print(_row(i, r, model))

    print("\n  Watch list — and does a two-for-one change the price?")
    # Counted from the trades themselves, before any ADP is needed, so the
    # two reasons a player can be missing are told apart. They were one
    # sentence, and with no drafts loaded every player hit it.
    counts: dict[str, int] = {}
    for d in deals:
        for k in d["a"] + d["b"]:
            counts[k] = counts.get(k, 0) + 1
    for name in watch:
        hits = [r for r in b.values() if name.lower() in (r["name"] or "").lower()]
        if not hits:
            keys = [k for k, n in (names or {}).items() if name.lower() in (n or "").lower()]
            n = sum(counts.get(k, 0) for k in keys)
            has_adp = any(k in adp for k in keys)
            why = ("no redraft ADP for him" if n >= MIN_TRADES and not has_adp
                   else f"needs {MIN_TRADES} trades" if has_adp
                   else "no redraft ADP for him either")
            print(f"    {name}: {n} player-for-player trades here — {why}")
            continue
        for r in hits:
            one = r["one_for_one"]
            con = r["consolidated"]
            print(f"    {r['name']}: drafted {_pk(r['adp'])}, traded as {_pk(r['implied_slot'])} "
                  f"over {r['trades']} trades")
            print(f"        one-for-one: "
                  + (f"{_pk(value_slot(one[0]))} ({one[1]})" if one else "none")
                  + "    went alone for two or more: "
                  + (f"{_pk(value_slot(con[0]))} ({con[1]})" if con else "none"))

    for name in show:
        hits = [r for r in b.values() if name.lower() in (r["name"] or "").lower()]
        for r in hits:
            print(f"\n  Every {r['name']} trade ({title}):")
            for t in sorted(r["rows"], key=lambda t: str(t.get("day") or t.get("week"))):
                got = " + ".join(f"{_name(adp, k)} ({_pk(adp[k]['adp']) if k in adp else 'undrafted'})"
                                 for k in t["got"]) or "—"
                w = (" with " + " + ".join(_name(adp, k) for k in t["with"])) if t["with"] else ""
                print(f"    {t.get('day') or 'week ' + str(t.get('week'))}: "
                      f"{t['shape']}{w}, for {got}  → {_pk(value_slot(t['implied']))}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", type=int, default=0)
    ap.add_argument("--refresh", action="store_true", help="re-read the database")
    ap.add_argument("--no-sleeper", action="store_true",
                    help="don't ask Sleeper for each league's type")
    ap.add_argument("--no-model", action="store_true", help="skip the model side-by-side")
    ap.add_argument("--player", action="append", default=[],
                    help="print every trade for players whose name contains this")
    args = ap.parse_args()

    season = args.season
    if not season:
        import nfl_week
        season = nfl_week.current_season()
    data = load(season, args.refresh, not args.no_sleeper)

    shapes = draft_shapes(data["drafts"])
    future = future_pick_leagues(data["trades"], season)
    kinds: dict[str, str] = {}
    decided: dict[tuple, int] = {}
    sf_count: dict[str, list[int]] = {}
    for lg in data["leagues"]:
        kind, why, sf = classify(lg, data.get("sleeper") or {}, shapes, future)
        kinds[str(lg["id"])] = kind
        decided[(kind, why)] = decided.get((kind, why), 0) + 1
        if sf is not None:
            e = sf_count.setdefault(kind, [0, 0])
            e[0] += int(bool(sf))
            e[1] += 1

    traded = {str(t["league_id"]) for t in data["trades"]}
    print(f"\nLeagues ({len(data['leagues']):,} connected, {len(traded):,} with trades):")
    for (kind, why), n in sorted(decided.items(), key=lambda kv: (kv[0][0], -kv[1])):
        print(f"    {kind:<8} {n:>6,}  ({why})")
    for kind, (sf, n) in sorted(sf_count.items()):
        print(f"    superflex among {kind} on Sleeper: {sf:,} of {n:,}")
    # Where the commissioner's answer and the evidence disagree. Usually
    # the default "redraft" left untouched in a league that isn't.
    fmt = {str(l["id"]): (l.get("format") or "redraft") for l in data["leagues"]}
    mismatch = sum(1 for lid, k in kinds.items()
                   if k == "dynasty" and fmt.get(lid) == "redraft")
    print(f"    dynasty leagues still marked 'redraft' in setup: {mismatch:,}")

    # The yardstick, and what dynasty rookie drafts were doing to it.
    adp = build_adp(data["drafts"], kinds, {"redraft"})
    adp_all = build_adp(data["drafts"], {k: "x" for k in kinds} |
                        {str(d["league_id"]): "x" for d in data["drafts"]}, {"x"})
    print(f"\nRedraft-only ADP: {len(adp):,} players with {MIN_DRAFTS}+ drafts "
          f"(all drafts mixed: {len(adp_all):,}).")
    diffs = sorted(((v["adp"] - adp[k]["adp"], k) for k, v in adp_all.items() if k in adp),
                   key=lambda t: t[0])[:10]
    if diffs:
        print("  Pulled earliest by mixing in dynasty drafts (the stored ADP does this today):")
        for d, k in diffs:
            print(f"    {(adp[k]['name'] or k)[:26]:<26} redraft {_pk(adp[k]['adp']):>6}"
                  f"   all drafts {_pk(adp_all[k]['adp']):>6}")
    undrafted_slot = (max(v["adp"] for v in adp.values()) if adp else 180) + 12

    deals = sides_of(data["trades"], data["trade_players"])
    names = {p["player_key"]: p.get("name") for p in data["trade_players"]}
    for bucket in ("redraft", "dynasty"):
        mine, skipped = [], {}
        for d in deals:
            if bucket_of(kinds.get(d["league_id"], "unknown")) != bucket:
                continue
            if d.get("skip"):
                skipped[d["skip"]] = skipped.get(d["skip"], 0) + 1
            else:
                mine.append(d)
        report(bucket, mine, skipped, adp, undrafted_slot, season,
               WATCH, args.player, not args.no_model and bool(adp), names)

    unk = sum(1 for d in deals if kinds.get(d["league_id"], "unknown") == "unknown")
    print(f"\n{unk:,} trades are in leagues we couldn't classify, and are in neither board.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
