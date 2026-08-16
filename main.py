"""
CLI entry point -- generate one newspaper for one league/week.

Now platform-agnostic: change PROVIDER to "espn" (once that adapter is written)
and nothing else here has to change.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

print("RUNNING FILE:", os.path.abspath(__file__))

from newspaper import build_power_rankings_from_matchups, save_newspaper_html
from providers import ProviderError, load_week, week_to_legacy_games
from storylines import get_weekly_storylines
from writer import generate_full_newspaper_content

# --- Config ---
PROVIDER = "sleeper"
LEAGUE_ID = "1252396303246176256"
WEEK = 1
SEASON = 2025
USE_CACHE = True  # AI content cache -- avoids re-paying for identical prose

CACHE_FILE = f"cache_week_{WEEK}.json"

# --- Customize these each season ---
COMMISSIONER_NAME = "johnhenryhammond"
INSIDE_JOKES = """
- The chug counter: managers must chug a beer when one of their players scores 0 points
- ASS Watch is the official designation for the worst team in the league
- Champ Hammond (champayyy) is referred to as Satan or a devil worshipper who sold his soul
- Nick Arrowood's team is perpetually on ASS Watch and has been declared officially ASS
- The commissioner John Hammond is always described as glorious, handsome, and brilliant
- Gardner Minshew Award, Joe Burrow Award, and Kyle Pitts Award are weekly honors
- Locker Room Guy is a beloved useless player kept on the roster for morale
- Injuries are mourned like deaths with full in memoriam treatment
"""


def save_recap_to_file(recap_text, week):
    filename = f"week_{week}_recap.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(recap_text)
    return filename


def main():
    print(f"[data] Fetching week {WEEK} from {PROVIDER}...")
    try:
        # One call replaces the old six: league, users, rosters, matchups,
        # players, projections -- plus pairing, enrichment and the lineup
        # optimizer, all of which now live in the provider layer.
        week_data = load_week(PROVIDER, LEAGUE_ID, SEASON, WEEK)
    except ProviderError as exc:
        print(f"[data] Could not load the week: {exc}")
        return

    league_name = week_data.league.name
    games = week_to_legacy_games(week_data)

    print(f"[data] {league_name}: {len(games)} matchups, "
          f"{len(week_data.teams)} teams")

    summary = get_weekly_storylines(games)

    # --- AI content: use cache if available, otherwise call the API ---
    if USE_CACHE and Path(CACHE_FILE).exists():
        print(f"[writer] Loading from cache ({CACHE_FILE}) — no API call made")
        with open(CACHE_FILE, "r") as f:
            ai_content = json.load(f)
    else:
        print("[writer] Calling API for fresh content...")
        ai_content = generate_full_newspaper_content(
            league_name=league_name,
            week=WEEK,
            games=games,
            summary=summary,
            commissioner_name=COMMISSIONER_NAME,
            inside_jokes=INSIDE_JOKES,
        )
        with open(CACHE_FILE, "w") as f:
            json.dump(ai_content, f, indent=2)
        print(f"[writer] AI content saved to {CACHE_FILE}")

    # --- Save plain text recap from ai_content (no second API call) ---
    recap_parts = [ai_content["headline"], ""]
    recap_parts.append(ai_content["lead_story"])
    recap_parts.append("")
    for m in ai_content["matchup_content"]:
        recap_parts.append(m["headline"])
        recap_parts.append(m["body"])
        recap_parts.append("")
    for award in ai_content["awards"]:
        recap_parts.append(award["title"])
        recap_parts.append(award["body"])
        recap_parts.append("")
    recap_parts.append("FRAUD WATCH")
    recap_parts.append(ai_content["fraud_watch"])
    recap = "\n".join(recap_parts)
    filename = save_recap_to_file(recap, WEEK)
    print(f"Recap saved to {filename}")

    # --- Build power rankings and save newspaper ---
    power_rankings = build_power_rankings_from_matchups(games)

    newspaper_file = save_newspaper_html(
        league_name=league_name,
        week=WEEK,
        summary=summary,
        matchups=games,
        power_rankings=power_rankings,
        ai_content=ai_content,
    )
    print(f"Newspaper saved to {newspaper_file}")
    print("ABSOLUTE NEWSPAPER PATH:", newspaper_file.resolve())


if __name__ == "__main__":
    main()
