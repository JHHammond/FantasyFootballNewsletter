"""Preview Draft Regrets priced on the trade market (26 Oct) WITHOUT
publishing anything.

    python -m scripts.preview_draft_market --slug <public slug> [--week N] [--theme T]
        One REAL league's paper, re-rendered with its steals and regrets
        priced on the real market. Reads Supabase (your .env), the league's
        platform and Sleeper. Writes NOTHING except a local file: no upload,
        no save, no Claude call. The prose is the paper's stored prose; the
        steals and regrets are worked out again from the season so far, the
        same way generation does it. -> output/draft_market_<slug>_w<N>.html

    python -m scripts.preview_draft_market
        Offline: the sample paper with invented steals and regrets priced on
        the Around the Leagues demo market. No database, no network.
        -> output/draft_market_demo.html

Open the file in a browser; narrow the window for the phone layout, print it
for the paper one. Links point at BASE_URL (default http://localhost:8000).

This turns nothing on for a real paper. That is PAPER_MARKET (see
web/paper_market.py).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "output"


def _report(regrets) -> None:
    if not regrets:
        print("No Draft Regrets for this paper (keeper/dynasty league, before Week 4, "
              "no draft data, or nobody moved 8+ places).")
        return
    block = regrets.get("market") or {}
    priced = block.get("players") or {}
    print(f"Through week {regrets.get('through_week')}, {block.get('market', 'no')} market:")
    for kind in ("steals", "regrets"):
        for e in regrets.get(kind) or []:
            p = priced.get(e.get("pid") or "")
            price = f"market {p['costs']:>5}  ({p['trades']} trades)" if p else "not priced on the market"
            print(f"  {kind[:-1]:<6} {e['player']:<24} {e['drafted_as']:>5} -> {e['now']:<5} {price}")


def demo(theme=None) -> Path:
    os.environ["DEMO_MODE"] = "1"
    import nfl_week
    from scripts import demo_around
    from scripts.render_sample_paper import sample_html
    from web import demo_db, paper_market, trade_rating

    season = nfl_week.current_season()
    latest = max(nfl_week.completed_week(), 1)
    demo_around.seed(300, latest)
    rating = trade_rating.build(demo_db, season, list(range(1, latest + 1)))
    lines = rating["daily"]["lines"]
    drafted = [p for p in rating["pickable"] if (lines.get(p["k"]) or [[1]])[0][0] == 0]

    def gain(p):
        line = lines[p["k"]]
        return line[0][1] - line[-1][1]

    teams = ["CeeDeezBallsOnYourChin", "THE Woke Agenda", "Mike Vick Legal Team"]

    def entry(p, i, rnd):
        pos = p.get("pos") or "WR"
        return {"pid": f"sleeper:{p['k']}", "player": p["name"], "pos": pos,
                "round": rnd, "overall": rnd * 10, "drafted_as": f"{pos}{20 + i * 7}",
                "now": f"{pos}{4 + i * 3}", "move": 12, "points": 88.4 - i * 9,
                "team": teams[i % 3], "where": "", "injured": False, "headshot": p.get("photo")}

    ups = sorted(drafted, key=gain, reverse=True)[:3]
    downs = sorted(drafted, key=gain)[:3]
    # The awkward cases a real league has: a player traded away, one hurt.
    notes = ["now on drake \"superchaser\" maye", "", ""]
    regrets = {"through_week": latest,
               "steals": [dict(entry(p, i, 8 + i), where=notes[i]) for i, p in enumerate(ups)],
               "regrets": [dict(entry(p, i, 1 + i), drafted_as=f"{p.get('pos')}{2 + i}",
                                now=f"{p.get('pos')}{24 + i * 5}", injured=(i == 1))
                           for i, p in enumerate(downs)]}
    block = paper_market.price(regrets, rating,
                               paper_market.keys_for(regrets, []))
    regrets["market"] = block
    _report(regrets)
    html = sample_html(theme, extra={"draft_regrets": regrets},
                       canonical_base=os.getenv("BASE_URL") or "http://localhost:8000")
    OUT.mkdir(exist_ok=True)
    out = OUT / f"draft_market_demo{'_' + theme if theme else ''}.html"
    out.write_text(html, encoding="utf-8")
    return out


def real(slug: str, week=None, theme=None) -> Path:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    os.environ.setdefault("BASE_URL", "http://localhost:8000")
    from newspaper import build_edition, build_power_rankings_from_matchups, render_html
    from providers import load_week, week_to_legacy_games
    from storylines import get_weekly_storylines
    from web import db, draft_regrets, generate, paper_market

    league = db.league_by_public_slug(slug)
    if not league:
        sys.exit(f"No league with the public slug {slug!r}.")
    season = int(league["season"])
    if week is None:
        papers = [p for p in db.list_papers(league["id"]) if int(p.get("season") or 0) == season]
        if not papers:
            sys.exit("That league has no paper this season to preview.")
        week = max(int(p["week"]) for p in papers)
    week = int(week)
    paper = db.get_paper(league["id"], season, week)
    if not paper or not paper.get("ai_cache"):
        sys.exit(f"No stored paper for week {week}.")
    ai = dict(paper["ai_cache"])

    print(f"{league.get('name') or slug}, week {week}: reading the season and pricing "
          "the market (a few seconds)...")
    weeks = [load_week(league["provider"], league["platform_league_id"], season, w,
                       with_optimizer=(w == week))
             for w in range(1, week + 1)]
    week_data = weeks[-1]
    regrets = draft_regrets.build(weeks, generate._draft_picks(league), week) \
        or ai.get("draft_regrets")
    if regrets:
        regrets = dict(regrets)
        regrets.pop(paper_market.KEY, None)
        paper_market.attach(db, league, weeks, regrets, force=True)   # ignores the switch
        ai["draft_regrets"] = regrets
    _report(regrets)

    games = week_to_legacy_games(week_data)
    edition = build_edition(
        generate.paper_name_for(league), week, get_weekly_storylines(games), games,
        build_power_rankings_from_matchups(games), ai,
        subscribe_slug=league["public_slug"],
        canonical_url=generate.paper_url_for(league, week),
        canonical_base=generate.public_base_url(),
        transactions=generate._transactions_for(league, season, week),
        publisher_ads=ai.get(generate.PUBLISHER_ADS_KEY),
        promo=generate.promo_settings(),
        photo_desk=generate._photo_desk(db, season, week),
        sponsor=generate.sponsor_settings(league))
    edition["paper_name"] = generate.paper_name_for(league)
    OUT.mkdir(exist_ok=True)
    out = OUT / f"draft_market_{slug}_w{week}{'_' + theme if theme else ''}.html"
    out.write_text(render_html(edition, theme=theme or league.get("theme")), encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--slug", help="a real league's public slug (read-only)")
    ap.add_argument("--week", type=int)
    ap.add_argument("--theme", choices=["tabloid", "broadsheet", "gameday"])
    args = ap.parse_args()
    path = real(args.slug, args.week, args.theme) if args.slug else demo(args.theme)
    print(f"\nWrote {path}\nOpen it with:  open \"{path}\"")
