"""
A sample paper, built from a real league week, for showing a sponsor
(1 Oct: the PrizePicks mockups). Nothing is fetched and nothing is stored.

- The scores, lineups and projections are a real Sleeper league week, read
  from the local .cache (Kevlarville, 2025 week 13).
- Every team and manager name is replaced with an invented one, so no real
  person in that league appears in a paper sent to a company.
- The words are written by the real writer (one round of Claude calls).
- The sponsor's placements are switched on: the lines board, the Over of
  the Week award and the "by the numbers" box.

    python -m scripts.sample_paper            # -> output/sample_sponsor_paper.html
    python -m scripts.sample_paper --no-ai    # layout only, no Claude calls
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

LEAGUE_ID = "1252396303246176256"
SEASON, WEEK = 2025, 13

TEAMS = ["Hank's Heroes", "Mike Vick Legal Team", "The Mid Tier", "Sad Sacks",
         "Bijan Mustard", "Puka Shells", "Lamb Chops", "Kittle Me Elmo",
         "Gibbs Me a Break", "CeeDee Rom"]
HANDLES = ["hankthetank", "mvlt_esq", "midtierdave", "sacksonsacks",
           "mustardman", "pukashells22", "lambchop", "elmo_kittle",
           "gibbsfan", "ceedeerom"]

SPONSOR = {
    "brand": "PrizePicks", "code": "DESK", "link": "https://www.prizepicks.com",
    # Relative to the output file, so the mockup opens from disk with the logo.
    "logo": "../web/static/sponsors/prizepicks.png",
    "lines": True, "award": True, "numbers": True,
    "numbers_note": ("Sample: lines here are each player's projection to the "
                     "half point. Live, they'd be PrizePicks' own Fantasy "
                     "Score lines."),
}


def _cached():
    out = {}
    for f in glob.glob(str(ROOT / ".cache" / "sleeper" / "*.json")):
        try:
            d = json.load(open(f))
            out[d["key"]] = d["value"]
        except Exception:  # noqa: BLE001
            pass
    return out


def load_week():
    from providers.sleeper import SleeperProvider, BASE_URL, PROJECTIONS_URL
    cache = _cached()
    league = dict(cache[f"league:{LEAGUE_ID}"])
    rows = cache[f"matchups:{LEAGUE_ID}:{WEEK}"]
    roster_ids = sorted({int(r["roster_id"]) for r in rows})
    users = [{"user_id": f"u{rid}", "display_name": HANDLES[i % len(HANDLES)],
              "avatar": None, "metadata": {"team_name": TEAMS[i % len(TEAMS)]}}
             for i, rid in enumerate(roster_ids)]
    rosters = [{"roster_id": rid, "owner_id": f"u{rid}", "metadata": {},
                "settings": {}} for rid in roster_ids]

    def fake_get(url, params=None):
        if url.endswith("/users"):
            return users
        if url.endswith("/rosters"):
            return rosters
        if "/matchups/" in url:
            return cache.get(f"matchups:{LEAGUE_ID}:{url.rsplit('/', 1)[-1]}") or []
        if url.startswith(PROJECTIONS_URL):
            s, w = url.rsplit("/", 2)[-2:]
            return cache.get(f"projections:{s}:{w}") or {}
        if url == f"{BASE_URL}/players/nfl":
            return cache.get("players:nfl") or {}
        if url == f"{BASE_URL}/league/{LEAGUE_ID}":
            return league
        return []

    p = SleeperProvider()
    p._get = fake_get
    from providers import apply_lineup_gaps
    return apply_lineup_gaps(p.get_week(LEAGUE_ID, SEASON, WEEK)), p, cache


def lines_for_next_week(p, cache, week_data):
    """Next week's board from each team's season average — what the app
    falls back to when next week's projections aren't out."""
    from providers.sleeper import SleeperProvider  # noqa: F401
    totals: dict[str, list[float]] = {}
    for w in range(1, WEEK + 1):
        for r in cache.get(f"matchups:{LEAGUE_ID}:{w}") or []:
            totals.setdefault(str(r["roster_id"]), []).append(float(r.get("points") or 0))
    names = {}
    for m in week_data.matchups:
        for t in m.teams:
            names[str(t.team_id)] = t.team_name
    pairs: dict = {}
    for r in cache.get(f"matchups:{LEAGUE_ID}:{WEEK + 1}") or []:
        if r.get("matchup_id") is not None:
            pairs.setdefault(r["matchup_id"], []).append(str(r["roster_id"]))
    import history
    out = []
    for a, b in [v for v in pairs.values() if len(v) == 2]:
        pa = sum(totals[a]) / len(totals[a])
        pb = sum(totals[b]) / len(totals[b])
        fav, dog, fp, dp = (a, b, pa, pb) if pa >= pb else (b, a, pb, pa)
        spread = history.round_spread(fp - dp)
        out.append({"favorite": names.get(fav, fav), "underdog": names.get(dog, dog),
                    "favorite_points": round(fp, 1), "underdog_points": round(dp, 1),
                    "spread": spread, "pickem": spread == 0,
                    "total": round((fp + dp) * 2) / 2})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--theme", default="tabloid")
    ap.add_argument("--out", default="output/sample_sponsor_paper.html")
    ap.add_argument("--reuse", action="store_true", help="reuse output/sample_ai.json")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import history
    import newspaper
    from providers.compat import week_to_legacy_games
    from storylines import get_weekly_storylines

    week_data, p, cache = load_week()
    games = week_to_legacy_games(week_data)
    summary = get_weekly_storylines(games)
    lines = lines_for_next_week(p, cache, week_data)
    paper = "The Gridiron Gazette"
    ai_path = ROOT / "output" / "sample_ai.json"

    if args.reuse and ai_path.exists():
        ai = json.loads(ai_path.read_text())
    elif args.no_ai:
        ai = {"lines": lines, "obituaries": []}
    else:
        from writer import generate_full_newspaper_content
        ai = generate_full_newspaper_content(
            league_name=paper, week=WEEK, games=games, summary=summary,
            commissioner_name="", inside_jokes="", tone="standard",
            obituaries=history.lowest_starters(week_data), lines=lines)
        ai_path.parent.mkdir(exist_ok=True)
        ai_path.write_text(json.dumps(ai, default=str))

    edition = newspaper.build_edition(
        paper, WEEK, summary, games,
        newspaper.build_power_rankings_from_matchups(games), ai,
        sponsor=SPONSOR)
    edition["paper_name"] = paper
    html = newspaper.render_html(edition, theme=args.theme)
    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
