"""READ-ONLY. The "is your buddy's team generationally bad?" funnel, through a week.

    python scripts/generationally_bad.py 4

All teams with a result in every week 1..N, then: winless; winless AND lost
every game by 10+; and all that AND under 100 every week. Own scoring, and
only leagues whose scoring looks standard (same rule as around_week).
"""
import json, pathlib, sys
from collections import defaultdict
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from web import db

N = int(sys.argv[1]) if len(sys.argv) > 1 else 4
SEASON = int(sys.argv[2]) if len(sys.argv) > 2 else 2026

rows = []
for wk in range(1, N + 1):
    start = 0
    while True:
        part = (db.client().table("team_weeks")
                .select("league_id,team_id,week,result,points,ppr_points,margin")
                .eq("season", SEASON).eq("week", wk)
                .order("league_id").order("team_id")
                .range(start, start + 999).execute().data or [])
        rows += part
        if len(part) < 1000:
            break
        start += 1000
    print(f"week {wk}: {sum(1 for r in rows if r['week']==wk)} rows", flush=True)

# leagues whose own scoring looks standard
lg = defaultdict(lambda: [0.0, 0, 0.0, 0])
for r in rows:
    if r["result"] in ("W", "L", "T") and (r["points"] or 0) > 0:
        a = lg[r["league_id"]]
        a[0] += float(r["points"]); a[1] += 1
        if r.get("ppr_points") is not None:
            a[2] += float(r["ppr_points"]); a[3] += 1
def normal(l):
    raw, n, ppr, m = lg[l]
    if not n:
        return False
    raw /= n
    if m:
        p = ppr / m
        return p > 0 and 0.7 <= raw / p <= 1.3
    return 40 <= raw <= 200

teams = defaultdict(dict)
for r in rows:
    if r["result"] in ("W", "L", "T"):
        teams[(r["league_id"], r["team_id"])][r["week"]] = r

full = {k: v for k, v in teams.items() if len(v) == N and normal(k[0])}
winless = {k: v for k, v in full.items() if all(g["result"] == "L" for g in v.values())}
blown = {k: v for k, v in winless.items()
         if all(g.get("margin") is not None and float(g["margin"]) <= -10 for g in v.values())}
under = {k: v for k, v in blown.items() if all(float(g["points"]) < 100 for g in v.values())}

out = {"week": N, "all_teams": len(full), "winless": len(winless),
       "blown_out": len(blown), "under_100": len(under),
       "leagues": len({k[0] for k in full})}
for k in ("winless", "blown_out", "under_100"):
    out[k + "_pct"] = round(100 * out[k] / out["all_teams"], 1) if out["all_teams"] else 0
out["one_in"] = round(out["all_teams"] / out["under_100"]) if out["under_100"] else None
print(json.dumps(out, indent=2))
(ROOT / "output" / f"generationally_bad_week{N}.json").write_text(json.dumps(out, indent=2))
