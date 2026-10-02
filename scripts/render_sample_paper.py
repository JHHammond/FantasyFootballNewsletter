"""Render a full-length sample paper to HTML, with no network and no database.

Built from the Sleeper test fixture, with every optional section filled in and
the real league's long team names, so the page is about as long and as awkward
as a real one. Unlike scripts/sample_paper.py, it needs no .cache and makes no
Claude calls, so it runs anywhere, including in the test suite
(tests/test_layout.py uses it for the phone checks). Open the output in a
browser at phone width to look at a layout change before deploying:

    python scripts/render_sample_paper.py            # writes output/sample.html
    python scripts/render_sample_paper.py --theme gameday
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from newspaper import (build_edition, build_power_rankings_from_matchups,  # noqa: E402
                       render_html)
from providers import (SleeperProvider, TTLCache, apply_lineup_gaps,  # noqa: E402
                       week_to_legacy_games)
from storylines import get_weekly_storylines  # noqa: E402
from tests import fixtures  # noqa: E402

BODY = (
    "Jahmyr Gibbs went for 41 and three touchdowns, and that was the end of the "
    "conversation. The other side needed something from its receivers and got a "
    "shrug. A projection is a promise nobody signed, and this week it was "
    "broken in public. ") * 5


def sample_html(theme=None) -> str:
    p = SleeperProvider(cache=TTLCache(cache_dir=tempfile.mkdtemp(), namespace="s"))
    p._get = lambda u, params=None: fixtures.fake_get(u, params)
    games = week_to_legacy_games(apply_lineup_gaps(p.get_week("TESTLEAGUE", 2025, 3)))
    # Real team names are long and have no spaces in them, which is what
    # pushes a narrow table wider than a phone.
    long_names = iter(["CeeDeezBallsOnYourChin", "THE Woke Agenda",
                       "Mike Vick Legal Team", "Sell the Falcons"])
    renamed = {}
    for g in games:
        for side in ("team_1", "team_2"):
            t = g[side]
            renamed[t["team_name"]] = t["team_name"] = next(long_names, t["team_name"])
        if g.get("winner") in renamed:
            g["winner"] = renamed[g["winner"]]
    summary = get_weekly_storylines(games)
    rankings = build_power_rankings_from_matchups(games)
    names = [r["team"] for r in rankings]

    matchups = []
    for g in games:
        a, b = g["team_1"], g["team_2"]
        w, l = (a, b) if a["points"] >= b["points"] else (b, a)
        matchups.append({
            "winner": w["team_name"], "loser": l["team_name"],
            "headline": f"{w['team_name'].upper()} BURIES {l['team_name'].upper()}",
            "body": BODY, "teaser": "A three-touchdown afternoon",
            "winner_score": w["points"], "loser_score": l["points"],
            "winner_record": w.get("record_after") or "3-0",
            "loser_record": l.get("record_after") or "0-3",
            "winner_lineup_gap": 8.2, "loser_lineup_gap": 21.4,
            "margin": round(w["points"] - l["points"], 1)})

    ai = {
        "headline": "CHAMP'S GIBBS SCORES THREE TOUCHDOWNS TO BURY BIJAN ROBINSON'S 35",
        "lead_story": BODY * 2,
        "matchup_content": matchups,
        "awards": [{"title": t, "body": BODY[:420]} for t in (
            "TONY SNELL WINDSPRINT AWARD", "KYLE PITTS AWARD",
            "NICK FOLES AWARD", "JOE BURROW AWARD")],
        "fraud_watch": "A record this good has no business resting on this little.",
        "power_rankings_comments": {n: "Three wins and the schedule to thank for two of them."
                                    for n in names},
        "pull_quote": "We were never going to lose that one.",
        "obituaries": [{"player": f"Player {i}", "body": BODY[:260]} for i in range(4)],
        "lines": [{"favorite": names[i], "underdog": names[-1 - i], "spread": 6.5,
                   "total": 270.5, "pick": "Lay the points."}
                  for i in range(min(3, len(names) // 2))],
        "trends": {"teams": {n: [[1, 120 + i], [2, 140 - i], [3, 130 + 2 * i]]
                             for i, n in enumerate(names)},
                   "lo": 90, "hi": 190,
                   "streaks": {n: ("W" if i % 2 else "L", 1 + i % 3)
                               for i, n in enumerate(names)}},
        "national": {"week": 3, "teams": 23919, "leagues": 2149,
                     "league_top_pct": 33, "league_avg": 132.4,
                     "best": {"rank": 882, "team": names[0], "points": 188.4, "top_pct": 4},
                     "worst": {"team": names[-1], "points": 84.1, "beaten_by_pct": 94}},
        "classifieds": [],
    }
    edition = build_edition("The Hands Times", 3, summary, games, rankings, ai,
                            subscribe_slug="sample")
    edition["paper_name"] = "The Hands Times"
    return render_html(edition, theme=theme)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme")
    ap.add_argument("--out", default=str(ROOT / "output" / "sample.html"))
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sample_html(args.theme), encoding="utf-8")
    print(out)
