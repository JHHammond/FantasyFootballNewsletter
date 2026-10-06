"""Why did a paper's story photos come out the way they did? READ-ONLY.

    python scripts/photo_check.py SLUG WEEK

Prints the photo desk for the week, the stored story order, which desk
players started in each game, and the photo each story slot would get.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from newspaper import (_desk_candidates, _in_story_order, auto_hero_photo,  # noqa: E402
                       auto_photo_for_game, plain_player_name)
from providers import load_week, week_to_legacy_games  # noqa: E402
from web import db  # noqa: E402
from web.generate import _photo_desk  # noqa: E402


def main(slug: str, week: int) -> None:
    league = db.league_by_public_slug(slug)
    if not league:
        sys.exit(f"no league with slug {slug}")
    season = league["season"]
    desk = _photo_desk(db, season, week)
    names = sorted(k for k in desk if not k.startswith("__"))
    print(f"Photo desk, season {season} week {week}: {len(names)} players")
    print("  " + (", ".join(names) or "(EMPTY: no photos tagged for this week or any-week)"))

    paper = db.get_paper(league["id"], season, week) or {}
    ai = paper.get("ai_cache") or {}
    print(f"\nStored paper: updated {paper.get('updated_at') or paper.get('created_at')}, "
          f"generations {paper.get('generation_count')}")
    uploads = ai.get("images") or {}
    if uploads:
        print(f"  Commissioner-uploaded images (these beat everything): {list(uploads)}")

    games = week_to_legacy_games(load_week(league["provider"], league["platform_league_id"],
                                           season, week))
    ordered = _in_story_order(games, ai)
    hero = auto_hero_photo(games, desk)
    print(f"\nHero photo: {hero and hero.get('caption')}")
    used = {hero["url"]} if hero else set()
    print("\nStories in print order:")
    for i, g in enumerate(ordered):
        on = [p["name"] for side in ("team_1", "team_2")
              for p in _desk_candidates(g.get(side))
              if plain_player_name(p.get("name")) in desk]
        shot = auto_photo_for_game(g, desk, avoid=used)
        if shot:
            used.add(shot["url"])
        t1 = (g.get("team_1") or {}).get("team_name")
        t2 = (g.get("team_2") or {}).get("team_name")
        flag = "PHOTO SLOT" if i < 2 else "          "
        print(f"  {i}. {flag} {t1} vs {t2}")
        print(f"       desk starters: {on or 'none'}")
        print(f"       photo: {shot and shot.get('caption')}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], int(sys.argv[2]))
