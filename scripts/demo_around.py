"""
Around the Leagues on localhost, with a season of made-up leagues.

    python -m scripts.demo_around            # then open http://localhost:8000/around
    python -m scripts.demo_around --leagues 120 --port 8001

Runs the app in DEMO_MODE with the in-memory database filled with a few
hundred invented leagues (real NFL player names, random weekly scores), so
the page can be looked at before a deploy. Nothing touches Supabase or
Anthropic, and nothing is written anywhere. The one call out is Sleeper's
public player index, which gives the players their real ids so their photos
show the way they will in production; without it the demo runs on made-up
ids and shows initials. Team names are invented either way.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# (name, position, team, weekly mean in PPR, spread)
POOL = [
    ("Josh Allen", "QB", "BUF", 24, 7), ("Lamar Jackson", "QB", "BAL", 23, 8),
    ("Jalen Hurts", "QB", "PHI", 21, 7), ("Joe Burrow", "QB", "CIN", 20, 7),
    ("Jayden Daniels", "QB", "WAS", 21, 8), ("Patrick Mahomes", "QB", "KC", 19, 6),
    ("Baker Mayfield", "QB", "TB", 18, 7), ("Bo Nix", "QB", "DEN", 17, 7),
    ("Kyler Murray", "QB", "ARI", 17, 6), ("Brock Purdy", "QB", "SF", 17, 6),
    ("Jared Goff", "QB", "DET", 17, 7), ("C.J. Stroud", "QB", "HOU", 15, 6),
    ("Caleb Williams", "QB", "CHI", 16, 7), ("Justin Herbert", "QB", "LAC", 16, 6),
    ("Drake Maye", "QB", "NE", 16, 7), ("Dak Prescott", "QB", "DAL", 16, 6),
    ("Bijan Robinson", "RB", "ATL", 20, 8), ("Saquon Barkley", "RB", "PHI", 19, 9),
    ("Jahmyr Gibbs", "RB", "DET", 19, 9), ("Christian McCaffrey", "RB", "SF", 18, 8),
    ("Derrick Henry", "RB", "BAL", 17, 9), ("De'Von Achane", "RB", "MIA", 17, 9),
    ("Ashton Jeanty", "RB", "LV", 15, 7), ("Jonathan Taylor", "RB", "IND", 16, 8),
    ("Josh Jacobs", "RB", "GB", 15, 7), ("Kyren Williams", "RB", "LAR", 15, 6),
    ("Bucky Irving", "RB", "TB", 14, 7), ("James Cook", "RB", "BUF", 14, 7),
    ("Chase Brown", "RB", "CIN", 13, 6), ("Breece Hall", "RB", "NYJ", 13, 7),
    ("Kenneth Walker III", "RB", "SEA", 12, 7), ("Alvin Kamara", "RB", "NO", 13, 6),
    ("Omarion Hampton", "RB", "LAC", 12, 6), ("Chuba Hubbard", "RB", "CAR", 11, 6),
    ("David Montgomery", "RB", "DET", 11, 6), ("D'Andre Swift", "RB", "CHI", 10, 6),
    ("Tony Pollard", "RB", "TEN", 10, 5), ("Aaron Jones", "RB", "MIN", 10, 6),
    ("TreVeyon Henderson", "RB", "NE", 10, 7), ("Rhamondre Stevenson", "RB", "NE", 8, 5),
    ("Javonte Williams", "RB", "DAL", 11, 6), ("Tyrone Tracy Jr.", "RB", "NYG", 8, 5),
    ("Zach Charbonnet", "RB", "SEA", 8, 6), ("Jaylen Warren", "RB", "PIT", 9, 6),
    ("Rachaad White", "RB", "TB", 7, 5), ("Tank Bigsby", "RB", "JAX", 7, 6),
    ("Ja'Marr Chase", "WR", "CIN", 21, 9), ("Justin Jefferson", "WR", "MIN", 18, 8),
    ("CeeDee Lamb", "WR", "DAL", 18, 8), ("Puka Nacua", "WR", "LAR", 18, 8),
    ("Amon-Ra St. Brown", "WR", "DET", 18, 7), ("Malik Nabers", "WR", "NYG", 17, 8),
    ("Nico Collins", "WR", "HOU", 16, 8), ("Brian Thomas Jr.", "WR", "JAX", 15, 8),
    ("A.J. Brown", "WR", "PHI", 15, 8), ("Drake London", "WR", "ATL", 15, 7),
    ("Ladd McConkey", "WR", "LAC", 14, 7), ("Tee Higgins", "WR", "CIN", 14, 8),
    ("Garrett Wilson", "WR", "NYJ", 14, 7), ("Tyreek Hill", "WR", "MIA", 13, 9),
    ("Mike Evans", "WR", "TB", 13, 8), ("Terry McLaurin", "WR", "WAS", 13, 7),
    ("DK Metcalf", "WR", "PIT", 12, 7), ("Davante Adams", "WR", "LAR", 13, 7),
    ("Jaxon Smith-Njigba", "WR", "SEA", 15, 8), ("Zay Flowers", "WR", "BAL", 12, 7),
    ("DJ Moore", "WR", "CHI", 11, 6), ("Courtland Sutton", "WR", "DEN", 12, 7),
    ("Marvin Harrison Jr.", "WR", "ARI", 12, 7), ("George Pickens", "WR", "DAL", 13, 8),
    ("Jameson Williams", "WR", "DET", 11, 8), ("Xavier Worthy", "WR", "KC", 10, 8),
    ("Jerry Jeudy", "WR", "CLE", 10, 6), ("Rome Odunze", "WR", "CHI", 11, 7),
    ("Jordan Addison", "WR", "MIN", 10, 7), ("Chris Olave", "WR", "NO", 11, 7),
    ("Jakobi Meyers", "WR", "LV", 10, 5), ("Khalil Shakir", "WR", "BUF", 10, 5),
    ("Travis Hunter", "WR", "JAX", 9, 7), ("Tetairoa McMillan", "WR", "CAR", 11, 7),
    ("Calvin Ridley", "WR", "TEN", 9, 6), ("Josh Downs", "WR", "IND", 9, 6),
    ("Wan'Dale Robinson", "WR", "NYG", 9, 5), ("Rashid Shaheed", "WR", "NO", 8, 7),
    ("Keon Coleman", "WR", "BUF", 7, 6), ("Christian Kirk", "WR", "HOU", 7, 5),
    ("Brock Bowers", "TE", "LV", 14, 6), ("Trey McBride", "TE", "ARI", 13, 6),
    ("George Kittle", "TE", "SF", 12, 7), ("Sam LaPorta", "TE", "DET", 10, 6),
    ("Travis Kelce", "TE", "KC", 10, 5), ("Mark Andrews", "TE", "BAL", 9, 6),
    ("T.J. Hockenson", "TE", "MIN", 9, 5), ("Tucker Kraft", "TE", "GB", 10, 6),
    ("David Njoku", "TE", "CLE", 8, 5), ("Dallas Goedert", "TE", "PHI", 8, 5),
    ("Tyler Warren", "TE", "IND", 9, 5), ("Jake Ferguson", "TE", "DAL", 8, 5),
    ("Dalton Kincaid", "TE", "BUF", 7, 5), ("Hunter Henry", "TE", "NE", 7, 4),
    ("Brandon Aubrey", "K", "DAL", 10, 4), ("Cameron Dicker", "K", "LAC", 9, 4),
    ("Jake Bates", "K", "DET", 9, 4), ("Chris Boswell", "K", "PIT", 9, 4),
    ("Ka'imi Fairbairn", "K", "HOU", 8, 4), ("Wil Lutz", "K", "DEN", 8, 4),
    ("Tyler Bass", "K", "BUF", 8, 4), ("Harrison Butker", "K", "KC", 8, 4),
    ("Steelers D/ST", "DEF", "PIT", 8, 5), ("Broncos D/ST", "DEF", "DEN", 9, 5),
    ("Ravens D/ST", "DEF", "BAL", 7, 5), ("Eagles D/ST", "DEF", "PHI", 7, 5),
    ("Texans D/ST", "DEF", "HOU", 7, 5), ("Vikings D/ST", "DEF", "MIN", 8, 6),
    ("Lions D/ST", "DEF", "DET", 7, 5), ("Bills D/ST", "DEF", "BUF", 7, 5),
    ("Chiefs D/ST", "DEF", "KC", 6, 5), ("Seahawks D/ST", "DEF", "SEA", 7, 5),
]

LINEUP = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
ROSTER = {"QB": 2, "RB": 5, "WR": 5, "TE": 2, "K": 1, "DEF": 1}
FLEX_OK = {"RB", "WR", "TE"}

ADJ = ["Fighting", "Mighty", "Gridiron", "Sunday", "Couch", "Fantasy", "Dynasty",
       "Waiver", "Bench", "Bye-Week", "Frozen", "Golden", "Rusty", "Lucky", "Salty"]
NOUN = ["Gladiators", "Goats", "Wizards", "Ballers", "Bandits", "Dawgs", "Sharks",
        "Prophets", "Dreamers", "Hogs", "Pirates", "Legends", "Outlaws", "Busts"]


def _made_up_key(name: str) -> str:
    return name.lower().replace(" ", "_").replace(".", "").replace("'", "")


def sleeper_keys() -> dict[str, str]:
    """Real Sleeper ids for the pool, looked up by name and position.

    The page draws a player's photo from his key (web.luck.photo_url), so
    with made-up keys the demo shows initials where production shows faces.
    Sleeper's player index is the same one the collector uses. If it can't
    be reached the demo still runs, just without the pictures.
    """
    try:
        from web import ppr
        index = ppr.fetch_index()
    except Exception as exc:                       # noqa: BLE001 — offline is fine
        print(f"  Sleeper's player index is out of reach ({exc}).")
        print("  The demo will show initials instead of photos.")
        return {}
    players, by_name = index.get("players") or {}, index.get("name") or {}
    out: dict[str, str] = {}
    for name, pos, nfl, *_ in POOL:
        if pos == "DEF":
            if nfl in players:                     # Sleeper keys defenses by team
                out[name] = nfl
            continue
        sids = by_name.get(f"{ppr.norm_name(name)}|{pos}") or []
        # Two players share a name often enough; prefer the one on this team.
        out[name] = next((s for s in sids if (players.get(s) or [None, None, None])[2] == nfl),
                         sids[0] if sids else None)
    found = {k: v for k, v in out.items() if v}
    print(f"  matched {len(found)} of {len(POOL)} players to Sleeper, for photos")
    return found


#: Filled by seed(): pool name -> Sleeper id, when Sleeper can be reached.
KEYS: dict[str, str] = {}


def _key(name: str) -> str:
    return KEYS.get(name) or _made_up_key(name)


def seed(n_leagues: int = 300, weeks: int = 7, season: int | None = None,
         rng: random.Random | None = None) -> None:
    import nfl_week
    from web import demo_db

    rng = rng or random.Random(2026)
    season = season or nfl_week.current_season()
    KEYS.clear()
    KEYS.update(sleeper_keys())
    demo_db.upsert_nfl_players([{"player_key": _key(n), "name": n, "position": p,
                                 "nfl_team": t} for n, p, t, *_ in POOL])
    by_pos: dict[str, list] = {}
    for p in POOL:
        by_pos.setdefault(p[1], []).append(p)

    # One real "week" of NFL results, shared by every league: the same player
    # scores the same everywhere, which is what makes the player boards work.
    scores = {w: {_key(p[0]): max(-2.0, round(rng.gauss(p[3], p[4]), 1)) for p in POOL}
              for w in range(1, weeks + 1)}
    # A couple of boom weeks for deep bench players, a dud for a star.
    for w in scores:
        for p in rng.sample(POOL[30:], 3):
            scores[w][_key(p[0])] = round(rng.uniform(26, 38), 1)
        star = rng.choice(POOL[:20])
        scores[w][_key(star[0])] = round(rng.uniform(0.5, 4.5), 1)

    providers = ["sleeper"] * 6 + ["espn"] * 3 + ["yahoo"] * 2
    fmts = ["ppr"] * 5 + ["half_ppr"] * 4 + ["std"] * 2
    sizes = [8, 10, 10, 12, 12, 12, 12, 14, 16]
    fmt_mult = {"ppr": 1.0, "half_ppr": 0.9, "std": 0.8}

    tw_rows, lu_rows = [], []
    rosters: dict[str, list] = {}
    for li in range(n_leagues):
        lid = f"demo-{li}"
        size, prov, fmt = rng.choice(sizes), rng.choice(providers), rng.choice(fmts)
        # Draft: everyone pulls from the same pool, roughly by value.
        teams = []
        pool = {pos: sorted(ps, key=lambda p: -(p[3] + rng.gauss(0, 2.5)))
                for pos, ps in by_pos.items()}
        taken: set = set()
        for t in range(size):
            roster = []
            for pos, n in ROSTER.items():
                cands = [p for p in pool[pos] if p[0] not in taken]
                window = cands[t * n // 2: t * n // 2 + n * 3] or cands
                for p in rng.sample(window, min(n, len(window))):
                    taken.add(p[0])
                    roster.append(p)
            if len(taken) > len(POOL) - 20:
                taken = set()
            name = f"{rng.choice(ADJ)} {rng.choice(NOUN)}"
            ghost = rng.random() < 0.04
            teams.append({"id": f"t{t}", "name": name, "roster": roster, "ghost": ghost,
                          "mgr": f"manager{li}_{t}"})
        rosters[lid] = teams

        for w in range(1, weeks + 1):
            sc = scores[w]
            order = teams[:]
            rng.shuffle(order)
            out = {}
            for t in teams:
                ros = t["roster"]
                pts = {p[0]: round(sc[_key(p[0])] * (fmt_mult[fmt] if p[1] in FLEX_OK else 1), 2)
                       for p in ros}

                def fill(rank):
                    chosen, used = [], set()
                    for slot in LINEUP:
                        ok = FLEX_OK if slot == "FLEX" else {slot}
                        c = [p for p in ros if p[1] in ok and p[0] not in used]
                        if not c:
                            chosen.append((slot, None))
                            continue
                        best = max(c, key=rank)
                        used.add(best[0])
                        chosen.append((slot, best))
                    return chosen
                # Managers start by projection plus a guess; the optimum knows.
                guess = fill(lambda p: p[3] + rng.gauss(0, 2.0))
                opt = fill(lambda p: pts[p[0]])
                if t["ghost"] and w > 2:
                    guess[rng.randrange(len(guess))] = ("FLEX", None)
                started = {p[0] for _, p in guess if p}
                total = round(sum(pts[p[0]] for _, p in guess if p), 2)
                optimal = round(sum(pts[p[0]] for _, p in opt if p), 2)
                ppr_total = round(sum(sc[_key(p[0])] for _, p in guess if p), 2)
                bench = [p for p in ros if p[0] not in started]
                top = max((p for _, p in guess if p), key=lambda p: pts[p[0]])
                bb = max(bench, key=lambda p: pts[p[0]]) if bench else None
                out[t["id"]] = dict(points=total, optimal=optimal, ppr=ppr_total,
                                    empty=sum(1 for _, p in guess if p is None),
                                    top=top[0], bb=bb[0] if bb else None)
                for p in ros:
                    lu_rows.append({"league_id": lid, "season": season, "week": w,
                                    "team_id": t["id"], "player_key": _key(p[0]),
                                    "slot": p[1] if p[0] in started else "BN",
                                    "started": p[0] in started, "points": pts[p[0]],
                                    "ppr_points": sc[_key(p[0])]})
            for i in range(0, len(order) - 1, 2):
                a, b = order[i], order[i + 1]
                for me, op in ((a, b), (b, a)):
                    m, o = out[me["id"]], out[op["id"]]
                    res = "W" if m["points"] > o["points"] else "L" if m["points"] < o["points"] else "T"
                    tw_rows.append({
                        "league_id": lid, "provider": prov, "platform_league_id": str(li),
                        "season": season, "week": w, "team_id": me["id"],
                        "team_name": me["name"], "manager": me["mgr"],
                        "points": m["points"], "opponent_name": op["name"],
                        "opponent_points": o["points"],
                        "margin": round(m["points"] - o["points"], 2), "result": res,
                        "optimal_points": m["optimal"],
                        "bench_left": round(max(0.0, m["optimal"] - m["points"]), 2),
                        "empty_slots": m["empty"], "top_player": m["top"],
                        "best_bench_player": m["bb"], "team_count": size,
                        "scoring_type": fmt, "ppr_points": m["ppr"], "ppr_coverage": 0.97,
                    })
    demo_db.upsert_team_weeks(tw_rows)
    demo_db.upsert_lineups(lu_rows)
    _trade(demo_db, rosters, season, weeks, rng)


def _trade(demo_db, rosters: dict, season: int, weeks: int,
           rng: random.Random) -> None:
    """Invent a season of trades, so the market panel can be looked at.

    The point is to produce trades the solver finds hard in the same ways
    real ones are, because an easy fake would make the panel look better
    than it is:

      * Both sides have to think they won. A manager values a player at his
        weekly mean plus a private error, held steady so he is consistently
        high on a player rather than re-rolling at every offer, and a trade
        only happens if both come out ahead by their own reckoning. That
        selection bias is the thing most likely to break the estimator, so
        the demo has it.
      * Bundles are lopsided — one-for-two and two-for-three, not just
        swaps — which is where a missing roster-spot charge would show.
      * About a sixth carry a draft pick or FAAB and a few are three-way.
        Those are exactly the trades the market throws away, so the panel's
        "usable trades" count and its note mean something here.
      * Every trade carries a real timestamp inside its week, because the
        daily line is drawn off those stamps and a flat one would hide a
        drift bug.

    What it does NOT do is move the players. The lineups above come from
    fixed rosters, and rewriting them after each trade would be a
    simulation of a season rather than a demo of a page, so a traded player
    keeps turning up on his old team's bench. That costs the market nothing
    — it reads trades, not rosters — but it does mean this demo cannot be
    used to check the collector.
    """
    import datetime as dt

    import nfl_week
    try:
        opener = nfl_week.season_opener(season)
    except Exception:  # noqa: BLE001
        opener = dt.date(season, 9, 4)

    def view(mgr: str, p) -> float:
        """One manager's standing private opinion of one player."""
        r = random.Random(hash((mgr, p[0])) & 0xFFFFFFFF)
        return max(1.0, p[3] * (1 + r.gauss(0, 0.22)))

    heads, moves, scans = [], [], []
    for lid, teams in rosters.items():
        scans.append({"league_id": lid, "season": season, "week": weeks,
                      "trades": 0, "scanned_at": dt.datetime.now(dt.UTC).isoformat()})
        # A target number of ACTUAL trades, not of attempts. Mutual gain
        # turns down about nine offers in ten, which is realistic and would
        # otherwise leave the whole country with too few trades for anybody
        # to clear MIN_TRADES.
        want, tries, n = rng.randint(3, 11), 0, 0
        while n < want and tries < want * 60:
            tries += 1
            a, b = rng.sample(teams, 2)
            na, nb = rng.choice([1, 1, 1, 2, 2, 3]), rng.choice([1, 1, 2, 2, 3])
            if len(a["roster"]) < na or len(b["roster"]) < nb:
                continue
            give = rng.sample(a["roster"], na)          # a sends these
            get = rng.sample(b["roster"], nb)           # a receives these
            if {p[0] for p in give} & {p[0] for p in get}:
                continue
            a_gain = (sum(view(a["mgr"], p) for p in get)
                      - sum(view(a["mgr"], p) for p in give))
            b_gain = (sum(view(b["mgr"], p) for p in give)
                      - sum(view(b["mgr"], p) for p in get))
            if a_gain <= 0 or b_gain <= 0:
                continue
            n += 1

            week = rng.randint(1, max(1, weeks))
            when = (dt.datetime.combine(opener + dt.timedelta(days=7 * (week - 1)),
                                        dt.time(0, 0), dt.UTC)
                    + dt.timedelta(days=rng.randint(0, 6), hours=rng.randint(0, 23),
                                   minutes=rng.randint(0, 59)))
            key = f"demo-tr-{lid}-{n}"
            picks = ([[a["id"], f"{season + 1} {rng.choice(['1st', '2nd', '3rd'])}"]]
                     if rng.random() < 0.13 else [])
            faab = [[b["id"], rng.choice([5, 10, 25])]] if rng.random() < 0.05 else []

            heads.append({"league_id": lid, "season": season, "week": week,
                          "trade_key": key, "provider": "sleeper",
                          "teams": [a["name"], b["name"]], "picks": picks,
                          "faab": faab, "traded_at": when.isoformat()})
            for side, (frm, to) in ((give, (a, b)), (get, (b, a))):
                for p in side:
                    moves.append({"league_id": lid, "season": season, "week": week,
                                  "trade_key": key, "player_key": _key(p[0]),
                                  "name": p[0], "position": p[1], "nfl_team": p[2],
                                  "from_team": frm["name"], "to_team": to["name"]})
            if rng.random() < 0.03 and len(teams) > 2:
                c = next((t for t in teams if t["id"] not in (a["id"], b["id"])), None)
                if c and c["roster"]:
                    p = rng.choice(c["roster"])
                    moves.append({"league_id": lid, "season": season, "week": week,
                                  "trade_key": key, "player_key": _key(p[0]),
                                  "name": p[0], "position": p[1], "nfl_team": p[2],
                                  "from_team": c["name"], "to_team": a["name"]})
    demo_db.upsert_trades(heads, moves)
    for s in scans:
        demo_db.upsert_trade_scan(s)
    pure = sum(1 for h in heads if not h["picks"] and not h["faab"])
    print(f"[demo] {len(heads):,} trades invented, {pure:,} player-for-player")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", type=int, default=300)
    ap.add_argument("--weeks", type=int, default=0, help="default: every finished week")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    # Here, not at import: importing this module must not reconfigure the app
    # (a test that imported it left /around open to everybody).
    os.environ["DEMO_MODE"] = "1"
    os.environ.setdefault("AROUND_PUBLIC", "1")
    import nfl_week
    weeks = args.weeks or max(nfl_week.completed_week(), 1)
    seed(args.leagues, weeks)
    import uvicorn
    from web.app import app
    print(f"Around the Leagues: http://localhost:{args.port}/around")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
