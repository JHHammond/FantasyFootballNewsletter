"""READ-ONLY (except one zip it uploads for you). Six weeks of record.

    python scripts/record_check.py 6

Through week N:
  * how many teams are N-0 and 0-N (teams with a W, L or T in every week 1..N)
  * the 10 players most often on undefeated rosters, and on winless rosters,
    with how common each is across ALL teams, so a share can be read as
    "on 61% of 6-0 teams, 34% of everyone"
  * headshots for those 20 players, zipped and uploaded to the storage
    bucket; the script prints one link to download them.

Rosters are each team's roster in week N (starters and bench). Nothing in the
database is changed. The zip lands at newspapers/staff-exports/.
"""
from __future__ import annotations

import io
import json
import pathlib
import re
import sys
import zipfile
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
import requests  # noqa: E402

from web import db  # noqa: E402
from web.luck import photo_url  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
SEASON = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
TOP = 10
MIN_TEAMS = 25          # a player must be on this many undefeated (or winless) rosters


def pages(table, cols, week, order):
    start = 0
    while True:
        q = db.client().table(table).select(cols).eq("season", SEASON).eq("week", week)
        for o in order:
            q = q.order(o)
        rows = q.range(start, start + 999).execute().data or []
        yield from rows
        if len(rows) < 1000:
            return
        start += 1000


# 1. Records
games = defaultdict(dict)
for wk in range(1, N + 1):
    n = 0
    for r in pages("team_weeks", "league_id,team_id,result", wk, ["league_id", "team_id"]):
        if r["result"] in ("W", "L", "T"):
            games[(r["league_id"], r["team_id"])][wk] = r["result"]
            n += 1
    print(f"week {wk}: {n} results", flush=True)
full = {k: v for k, v in games.items() if len(v) == N}
unbeaten = {k for k, v in full.items() if all(x == "W" for x in v.values())}
winless = {k for k, v in full.items() if all(x == "L" for x in v.values())}
total = len(full)

# 2. Week-N rosters
roster = defaultdict(set)
for r in pages("lineup_players", "league_id,team_id,player_key", N,
               ["league_id", "team_id", "player_key"]):
    k = (r["league_id"], r["team_id"])
    if k in full:
        roster[k].add(r["player_key"])
print(f"week {N} rosters: {len(roster)} teams", flush=True)

def share(group):
    teams = [t for t in group if t in roster]
    c = Counter(p for t in teams for p in roster[t])
    return c, len(teams)

all_c, all_n = share(full)
un_c, un_n = share(unbeaten)
wl_c, wl_n = share(winless)

keys = {p for p, _ in un_c.most_common(60)} | {p for p, _ in wl_c.most_common(60)}
info = {}
klist = list(keys)
for i in range(0, len(klist), 200):
    for r in (db.client().table("nfl_players").select("player_key,name,position,nfl_team")
              .in_("player_key", klist[i:i + 200]).execute().data or []):
        info[r["player_key"]] = r

def top(c, n_teams):
    out = []
    for p, cnt in c.most_common(80):
        if cnt < MIN_TEAMS:
            continue
        m = info.get(p, {})
        if (m.get("position") or "") in ("K", "DEF"):
            continue
        out.append({"key": p, "name": m.get("name") or p, "pos": m.get("position"),
                    "team": m.get("nfl_team"),
                    "share": round(100 * cnt / n_teams, 1), "teams": cnt,
                    "share_all": round(100 * all_c[p] / all_n, 1)})
        if len(out) == TOP:
            break
    return out

report = {
    "through_week": N, "teams": total,
    "undefeated": len(unbeaten), "undefeated_pct": round(100 * len(unbeaten) / total, 2),
    "winless": len(winless), "winless_pct": round(100 * len(winless) / total, 2),
    "rosters_checked": {"all": all_n, "undefeated": un_n, "winless": wl_n},
    "top_undefeated": top(un_c, un_n), "top_winless": top(wl_c, wl_n),
}
out_dir = ROOT / "output"
out_dir.mkdir(exist_ok=True)
(out_dir / f"record_week{N}.json").write_text(json.dumps(report, indent=2))

print(f"\nThrough Week {N}: {total:,} teams")
print(f"  {N}-0: {len(unbeaten):,} ({report['undefeated_pct']}%)")
print(f"  0-{N}: {len(winless):,} ({report['winless_pct']}%)")
for label, rows in (("UNDEFEATED", report["top_undefeated"]), ("WINLESS", report["top_winless"])):
    print(f"\nMost common on {label} rosters (share of them, share of all teams):")
    for i, r in enumerate(rows, 1):
        print(f"  {i:>2}. {r['name']} {r['pos']} {r['team']}: {r['share']}%  (all teams {r['share_all']}%)")

# 3. Headshots, zipped and uploaded
buf = io.BytesIO()
got = 0
with zipfile.ZipFile(buf, "w") as z:
    z.writestr(f"record_week{N}.json", json.dumps(report, indent=2))
    for label, rows in (("unbeaten", report["top_undefeated"]), ("winless", report["top_winless"])):
        for i, r in enumerate(rows, 1):
            url = photo_url(r["key"], r["pos"])
            if not url:
                continue
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                print(f"  no photo for {r['name']}: {exc}")
                continue
            ext = "png" if ".png" in url else "jpg"
            slug = re.sub(r"[^a-z0-9]+", "-", r["name"].lower()).strip("-")
            z.writestr(f"{label}-{i:02d}-{slug}.{ext}", resp.content)
            got += 1
path = f"staff-exports/record_week{N}.zip"
storage = db.client().storage.from_(db.BUCKET)
opts = {"content-type": "application/zip", "upsert": "true"}
try:
    storage.upload(path, buf.getvalue(), opts)
except Exception:  # noqa: BLE001
    storage.update(path, buf.getvalue(), opts)
print(f"\n{got} photos zipped. Download: {storage.get_public_url(path)}")
