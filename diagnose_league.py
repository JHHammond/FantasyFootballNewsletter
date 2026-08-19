"""
Ask Sleeper what it actually knows about a league.

    python diagnose_league.py 1389740002132377600

Prints the league's real season, its status, whether it has drafted, and
exactly which weeks have matchup data — which is the thing that determines
whether a paper can be generated at all.
"""

import sys

import requests

BASE = "https://api.sleeper.app/v1"

STATUS_MEANING = {
    "pre_draft": "Created, but the draft hasn't happened yet. No matchups exist.",
    "drafting": "Draft is in progress. No matchups exist yet.",
    "in_season": "Season is underway. Matchups exist for weeks that have started.",
    "complete": "Season is over. All weeks should have data.",
}


def main():
    if len(sys.argv) < 2:
        print("usage: python diagnose_league.py <league_id>")
        return 1

    league_id = sys.argv[1].strip()

    r = requests.get(f"{BASE}/league/{league_id}", timeout=20)
    if r.status_code == 404:
        print(f"Sleeper has no league with ID {league_id}.")
        print("Check the ID — it's the long number in your league's web URL.")
        return 1
    r.raise_for_status()
    league = r.json()

    status = league.get("status")
    print(f"Name:            {league.get('name')}")
    print(f"Season:          {league.get('season')}  <- this is the real season")
    print(f"Season type:     {league.get('season_type')}")
    print(f"Status:          {status}")
    print(f"                 {STATUS_MEANING.get(status, 'Unknown status.')}")
    print(f"Teams:           {league.get('total_rosters')}")
    print(f"Draft ID:        {league.get('draft_id')}")
    print(f"Previous league: {league.get('previous_league_id')}")

    rosters = requests.get(f"{BASE}/league/{league_id}/rosters", timeout=20).json() or []
    with_players = sum(1 for x in rosters if x.get("players"))
    print(f"Rosters:         {len(rosters)} ({with_players} have players)")

    print("\nChecking which weeks have matchup data...")
    playable = []
    for week in range(1, 19):
        rows = requests.get(f"{BASE}/league/{league_id}/matchups/{week}", timeout=20).json()
        if not rows:
            continue
        scored = [x for x in rows if (x.get("points") or 0) > 0]
        paired = [x for x in rows if x.get("matchup_id") is not None]
        if scored:
            playable.append(week)
            print(f"  week {week:>2}: {len(rows)} rosters, {len(paired)} paired, "
                  f"{len(scored)} with points  <- generatable")
        elif paired:
            print(f"  week {week:>2}: scheduled but nobody has scored yet")

    print()
    if playable:
        print(f"Generatable weeks: {', '.join(str(w) for w in playable)}")
        print(f"Use season {league.get('season')} and one of those weeks.")
    else:
        print("No week has any scoring yet.")
        if status in ("pre_draft", "drafting"):
            print("That's expected — this league hasn't drafted. Sleeper doesn't")
            print("create matchups until after the draft, so there's nothing to")
            print("write a paper about yet.")
        else:
            print("The league has drafted but no games have been scored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
