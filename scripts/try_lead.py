"""Write just the front page story for the sample week, to judge the prompt.

    python -m scripts.try_lead            # the possible stories, then the lead
    python -m scripts.try_lead --leads    # only the possible stories (free)

Uses the cached Sleeper week the sample paper uses (2025 week 13, invented
team names) and the same season briefing a real paper gets. One Claude call;
needs ANTHROPIC_API_KEY in .env.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import history
    import writer
    from providers.compat import week_to_legacy_games
    from scripts import sample_paper as sp
    from storylines import get_weekly_storylines

    week_data, _p, cache = sp.load_week()
    games = week_to_legacy_games(week_data)
    summary = get_weekly_storylines(games)
    names = {str(t.team_id): t.team_name for m in week_data.matchups for t in m.teams}
    results = []
    for w in range(1, sp.WEEK + 1):
        by_game: dict = {}
        for r in cache.get(f"matchups:{sp.LEAGUE_ID}:{w}") or []:
            if r.get("matchup_id") is not None:
                by_game.setdefault(r["matchup_id"], []).append(r)
        for pair in by_game.values():
            if len(pair) != 2:
                continue
            for me, them in ((pair[0], pair[1]), (pair[1], pair[0])):
                results.append(history.Result(
                    week=w, team_id=str(me["roster_id"]),
                    team=names.get(str(me["roster_id"]), ""), manager="",
                    points=float(me.get("points") or 0), opp_id=str(them["roster_id"]),
                    opp_team=names.get(str(them["roster_id"]), ""), opp_manager="",
                    opp_points=float(them.get("points") or 0)))
    pairs = [(str(m.teams[0].team_id), str(m.teams[1].team_id)) for m in week_data.matchups]
    leads = history.story_leads(results, sp.WEEK, pairs)
    print("POSSIBLE STORIES")
    for line in leads:
        print(" -", line)
    if "--leads" in sys.argv:
        return
    briefing = history.previously_on(results, sp.WEEK, pairs, None)
    context = ("BACKGROUND ON THE SEASON — for you, not to recite. Use a fact from "
               "here rarely, only when it makes a story better.\n" + briefing)
    system = writer.system_prompt("standard", games, league_context=context)
    print("\nTHE LEAD STORY\n")
    print(writer.generate_lead_story(
        summary, sp.WEEK, "The Gridiron Gazette", "", writer.LEAGUE_BACKGROUND_REF,
        system, writer.model_for("lead_story"), games=games, leads=leads))


if __name__ == "__main__":
    main()
